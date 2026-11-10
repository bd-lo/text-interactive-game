"""内存仓储实现（M2 005 的默认 ``GameRepository``；008 的 SQLite 实现之前的过渡）。

实现契约 §2.2/§6 与 M3 §3.5 的语义：
- ``commit``：CAS 版本 + 事件 ``seq`` 连续 + 事件 ``id`` 与序号一致，全部校验通过后
  一次性更新内存结构，失败**不部分写入**；
- ``load``：当前状态行缺失时用「最新快照 + 之后事件」重建，无快照则从事件流（首条必须
  是 ``game_created``）重建；两者都没有返回 ``None``。重建出的 ``version`` 与 SQLite
  实现同一规则（M3 D-M3-6，见 ``persistence.versioning``）：快照 ``version`` +
  快照后 distinct ``turn_id`` 数，旧快照（version=0）以 ``last_seq`` 为 base 兜底；
- 幂等表、快照表、M3 审计表独立存储，不参与事务正确性（快照可随时删除重建）；
  审计只增（``save_audit`` 不覆盖、不删除，同 ``audit_uid`` 重投幂等）。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

from textgame.domain.events import Event
from textgame.domain.reducers import apply_events
from textgame.services.ports import (
    AuditRecord,
    CommitPayload,
    GameRecord,
    IdempotencyRecord,
    SnapshotRecord,
)

from .versioning import rebuild_version


class InMemoryRepository:
    """进程内 ``GameRepository``；单进程单写者使用，不做跨进程并发控制。"""

    def __init__(self) -> None:
        """创建空仓储（无存档、无事件、无快照、无幂等/审计记录）。"""
        self._current: dict[str, GameRecord] = {}
        self._events: dict[str, list[Event]] = {}
        self._snapshots: dict[str, list[SnapshotRecord]] = {}
        self._idempotency: dict[str, IdempotencyRecord] = {}
        self._audit: dict[str, list[AuditRecord]] = {}
        self._audit_keys: set[tuple[str, int, int, str]] = set()
        # 最近提交时间（只读列表排序用；不参与事件溯源/state_hash）。
        self._updated_at: dict[str, datetime] = {}
        # current 行被删除后重建时仍需 last_seq（契约 §6 的“空档重建”）。
        self._last_seqs: dict[str, int] = {}

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------
    def load(self, game_id: str) -> GameRecord | None:
        """返回当前状态；current 缺失时用快照/事件重建，空存档返回 ``None``。"""
        current = self._current.get(game_id)
        if current is not None:
            return current
        events = self._events.get(game_id, [])
        if not events:
            return None
        version = rebuild_version(events)
        last_seq = self._last_seqs.get(game_id, events[-1].seq or 0)
        snapshot = self.latest_snapshot(game_id)
        if snapshot is not None:
            remaining = [event for event in events if (event.seq or 0) > snapshot.last_seq]
            state = apply_events(snapshot.state, remaining)
            version = rebuild_version(events, snapshot=snapshot)
        else:
            state = apply_events(None, events)
        return GameRecord(
            game_id=game_id,
            state=state,
            version=version,
            last_seq=last_seq,
            updated_at=self._updated_at.get(game_id),
        )

    def list_games(self, *, limit: int, offset: int) -> list[GameRecord]:
        """按最近更新时间倒序返回存档摘要（同一时间按 game_id 稳定排序）。"""
        game_ids = sorted(
            self._current,
            key=lambda game_id: (
                self._updated_at.get(game_id) or datetime.min.replace(tzinfo=timezone.utc),
                game_id,
            ),
            reverse=True,
        )
        records: list[GameRecord] = []
        for game_id in game_ids[offset : offset + limit]:
            record = self.load(game_id)
            if record is not None:
                records.append(record)
        return records

    def count_games(self) -> int:
        """返回当前存档数（只读）。"""
        return len(self._current)

    def read_events(self, game_id: str, *, after_seq: int = 0) -> list[Event]:
        """按 ``seq`` 升序返回 ``seq > after_seq`` 的事件。"""
        return [event for event in self._events.get(game_id, []) if (event.seq or 0) > after_seq]

    def read_first_event(self, game_id: str) -> Event | None:
        """返回该存档第一条事件（``seq`` 最小）；无存档返回 ``None``（H4 加法，只读）。"""
        events = self._events.get(game_id, [])
        return events[0] if events else None

    def latest_snapshot(self, game_id: str) -> SnapshotRecord | None:
        """返回 ``snapshot_version`` 最大的快照；无快照返回 ``None``。"""
        snapshots = self._snapshots.get(game_id, [])
        if not snapshots:
            return None
        return max(snapshots, key=lambda snapshot: snapshot.snapshot_version)

    def get_idempotent(self, key: str) -> IdempotencyRecord | None:
        """按存储键取幂等记录。"""
        return self._idempotency.get(key)

    # ------------------------------------------------------------------
    # 写
    # ------------------------------------------------------------------
    def commit(self, payload: CommitPayload) -> bool:
        """原子提交；校验失败返回 ``False`` 且内存状态完全不变（契约 §2.2）。"""
        existing = self._current.get(payload.game_id)
        current_version = existing.version if existing is not None else 0
        if payload.expected_version != current_version:
            return False
        if payload.new_version != payload.expected_version + 1:
            return False

        history = self._events.get(payload.game_id, [])
        previous_last_seq = self._last_seqs.get(payload.game_id, 0)
        incoming = list(payload.stamped_events)
        if payload.last_seq != previous_last_seq + len(incoming):
            return False
        for offset, event in enumerate(incoming, start=1):
            expected_seq = previous_last_seq + offset
            if not event.is_stamped or event.seq != expected_seq:
                return False
            if event.id != f"evt_{expected_seq:06d}":
                return False

        stored = [*history, *incoming]
        updated_at = datetime.now(timezone.utc)
        # 全部校验通过后才修改内存，保证失败不部分写入。
        self._current[payload.game_id] = GameRecord(
            game_id=payload.game_id,
            state=payload.new_state,
            version=payload.new_version,
            last_seq=payload.last_seq,
            updated_at=updated_at,
        )
        self._updated_at[payload.game_id] = updated_at
        self._events[payload.game_id] = stored
        self._last_seqs[payload.game_id] = payload.last_seq
        return True

    def save_snapshot(self, snapshot: SnapshotRecord) -> None:
        """追加派生快照；同一 ``snapshot_version`` 覆盖旧值（幂等重写）。"""
        snapshots = self._snapshots.setdefault(snapshot.game_id, [])
        for index, existing in enumerate(snapshots):
            if existing.snapshot_version == snapshot.snapshot_version:
                snapshots[index] = snapshot
                return
        snapshots.append(snapshot)

    def save_idempotent(self, record: IdempotencyRecord) -> None:
        """保存幂等记录；同键覆盖（同键异参判定由编排层负责）。"""
        self._idempotency[record.key] = record

    # ------------------------------------------------------------------
    # 审计（M3 D-M3-7：只增）
    # ------------------------------------------------------------------
    def save_audit(self, records: Sequence[AuditRecord]) -> None:
        """只追加审计行；同 ``audit_uid``（game/turn/attempt/proposal）已存在时跳过。"""
        for record in records:
            key = self._audit_key(record)
            if key in self._audit_keys:
                continue
            self._audit_keys.add(key)
            self._audit.setdefault(record.game_id, []).append(record)

    def read_audit(self, game_id: str, *, turn_id: int | None = None) -> list[AuditRecord]:
        """按 ``(turn_id, proposal_id, attempt)`` 升序返回；无存档返回空列表。"""
        records = self._audit.get(game_id, [])
        if turn_id is not None:
            records = [record for record in records if record.turn_id == turn_id]
        return sorted(
            records, key=lambda record: (record.turn_id, record.proposal_id, record.attempt)
        )

    @staticmethod
    def _audit_key(record: AuditRecord) -> tuple[str, int, int, str]:
        """与 SQLite 审计主键 ``audit_uid`` 等价的去重键（保证只增语义一致）。"""
        return (record.game_id, record.turn_id, record.attempt, record.proposal_id)

    # ------------------------------------------------------------------
    # 测试辅助（不属于协议）
    # ------------------------------------------------------------------
    def drop_current_state(self, game_id: str) -> None:
        """模拟 current 行丢失，用于验证 ``load`` 的快照/事件重建路径。"""
        self._current.pop(game_id, None)


__all__ = ["InMemoryRepository"]
