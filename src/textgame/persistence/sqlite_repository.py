"""``SqliteRepository``：``GameRepository`` 的 SQLite 实现（M2 契约 §2.2/§6、第 14 章 14.3/14.4/14.5）。

职责边界：只做事件行/快照/当前状态缓存的读写与 CAS，不承载业务规则（不 import 任何
服务实现；重建时复用 ``GameState.apply`` 纯投影，哈希复用 ``domain.hashing``）。

- ``commit``：单事务。先校验事件已盖章、``seq`` 与 ``id`` 自洽且紧接**该存档**已有序号
  （``events`` 主键为 ``(game_id, seq)``，同库不同存档的 ``seq`` 各自从 1 起），再 CAS
  ``UPDATE game_state_current SET ... WHERE game_id=? AND version=?``；影响行数 0 → 回滚
  返回 ``False``。重复 ``(game_id, seq)`` / ``(game_id, event_uid)`` 由数据库唯一约束兜底
  （``IntegrityError`` → 回滚返回 ``False``），任何异常先回滚再抛出/返回，绝不部分写入。
- ``load``：current 行优先；缺失时用「最新快照 + ``after_seq`` 之后事件」经
  ``GameState.apply`` 重建，并用 ``state_hash`` 校验快照；无快照时要求事件流首条为
  ``game_created``（否则视为损坏抛 ``ValueError``）。current 行丢失时重建出的
  ``version`` 按 M3 D-M3-6：快照 ``version`` + 快照后事件里 distinct ``turn_id`` 数；
  快照缺 ``version``（旧 M2 数据/0）时以 ``last_seq`` 为 base 兜底（M2 一事件一版本），
  无快照时取事件流 distinct ``turn_id`` 数（见 ``persistence.versioning``）。
- 审计（M3 D-M3-7）：``save_audit`` 单事务批量 **只 INSERT**（``INSERT OR IGNORE``：
  同 ``audit_uid`` 已存在时不覆盖、不删除）；``read_audit`` 按
  ``(turn_id, proposal_id, attempt)`` 升序，``turn_id=None`` 返回全部，无存档返回空。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Any, cast

from sqlalchemy import CursorResult, Engine, func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from textgame.domain.events import Event, EventType
from textgame.domain.hashing import canonical_json, state_hash
from textgame.domain.models import GameState
from textgame.domain.reducers import apply_events
from textgame.services.ports import (
    AuditRecord,
    CommitPayload,
    GameRecord,
    IdempotencyRecord,
    SnapshotRecord,
)

from .tables import (
    AuditEventRow,
    EventRow,
    GameStateCurrentRow,
    IdempotencyRow,
    SnapshotRow,
)
from .versioning import rebuild_version


class SqliteRepository:
    """``GameRepository`` 的 SQLite 实现；每次调用使用独立 Session + 独立事务。"""

    def __init__(self, engine: Engine) -> None:
        """绑定 Engine；新库建表由 ``init_schema(engine)`` 完成（M3 另提供 Alembic baseline）。"""
        self._engine = engine
        self._session_factory: sessionmaker[Session] = sessionmaker(
            bind=engine, expire_on_commit=False
        )

    # ------------------------------------------------------------------ load
    def load(self, game_id: str) -> GameRecord | None:
        """读当前状态；current 行缺失时按第 14 章 14.4 从快照 + 事件重建。"""
        with self._session_factory() as session:
            current = session.get(GameStateCurrentRow, game_id)
            if current is not None:
                return GameRecord(
                    game_id=game_id,
                    state=GameState.model_validate_json(current.state_json),
                    version=current.version,
                    last_seq=current.last_seq,
                )
            snapshot = self._latest_snapshot(session, game_id)
            if snapshot is not None:
                return self._rebuild_from_snapshot(session, snapshot)
            events = self._read_events(session, game_id, after_seq=0)
            if not events:
                return None
            return self._rebuild_from_events(game_id, events)

    def _rebuild_from_snapshot(self, session: Session, snapshot: SnapshotRecord) -> GameRecord:
        """用「快照状态 + 快照之后事件」重建；快照哈希不符按损坏处理。"""
        actual_hash = state_hash(snapshot.state)
        if actual_hash != snapshot.state_hash:
            raise ValueError(
                f"snapshot state_hash mismatch for game {snapshot.game_id} "
                f"v{snapshot.snapshot_version}: expected {snapshot.state_hash}, got {actual_hash}"
            )
        events = self._read_events(session, snapshot.game_id, after_seq=snapshot.last_seq)
        self._ensure_contiguous(events, start_seq=snapshot.last_seq + 1)
        state = snapshot.state.apply(events) if events else snapshot.state
        last_seq = events[-1].seq if events else snapshot.last_seq
        if last_seq is None:  # pragma: no cover - _ensure_contiguous 已保证盖章态
            raise ValueError("stamped event must carry seq")
        # M3 D-M3-6：version = 快照 version + 快照后 distinct turn 数（旧快照兜底见 versioning）。
        version = rebuild_version(events, snapshot=snapshot)
        return GameRecord(game_id=snapshot.game_id, state=state, version=version, last_seq=last_seq)

    def _rebuild_from_events(self, game_id: str, events: list[Event]) -> GameRecord:
        """无快照时从空档重放；首条必须是 ``game_created``，否则视为损坏。"""
        first = events[0]
        if first.event_type is not EventType.GAME_CREATED:
            raise ValueError(
                f"save {game_id} has events but no leading game_created event; save is corrupted"
            )
        self._ensure_contiguous(events, start_seq=1)
        # 空存档重建走 reducer 的唯一投影实现（``apply_events(None, ...)`` 要求
        # 首条为 GAME_CREATED，与第 14 章 14.4 的回放算法一致）。
        state = apply_events(None, events)
        last_seq = events[-1].seq
        if last_seq is None:  # pragma: no cover - _ensure_contiguous 已保证盖章态
            raise ValueError("stamped event must carry seq")
        # 无快照兜底：每个成功提交推进一个回合 → version = 事件流 distinct turn_id 数。
        return GameRecord(
            game_id=game_id, state=state, version=rebuild_version(events), last_seq=last_seq
        )

    # ------------------------------------------------------------ read_events
    def read_events(self, game_id: str, *, after_seq: int = 0) -> list[Event]:
        """按 ``seq`` 升序读取该存档 ``seq > after_seq`` 的事件。"""
        with self._session_factory() as session:
            return self._read_events(session, game_id, after_seq=after_seq)

    def _read_events(self, session: Session, game_id: str, *, after_seq: int) -> list[Event]:
        rows = session.scalars(
            select(EventRow)
            .where(EventRow.game_id == game_id, EventRow.seq > after_seq)
            .order_by(EventRow.seq.asc())
        ).all()
        return [self._event_from_row(row) for row in rows]

    # ------------------------------------------------------------- snapshots
    def latest_snapshot(self, game_id: str) -> SnapshotRecord | None:
        """按 ``snapshot_version`` 取最新快照；无快照返回 ``None``。"""
        with self._session_factory() as session:
            return self._latest_snapshot(session, game_id)

    def _latest_snapshot(self, session: Session, game_id: str) -> SnapshotRecord | None:
        row = session.scalars(
            select(SnapshotRow)
            .where(SnapshotRow.game_id == game_id)
            .order_by(SnapshotRow.snapshot_version.desc(), SnapshotRow.id.desc())
            .limit(1)
        ).first()
        if row is None:
            return None
        return SnapshotRecord(
            game_id=row.game_id,
            snapshot_version=row.snapshot_version,
            turn_id=row.turn_id,
            last_seq=row.last_seq,
            state=GameState.model_validate_json(row.state_json),
            state_hash=row.state_hash,
            schema_version=row.schema_version,
            version=row.version,
        )

    def save_snapshot(self, snapshot: SnapshotRecord) -> None:
        """追加派生快照；同一 ``(game_id, snapshot_version)`` 覆盖重写（幂等）。"""
        values = {
            "turn_id": snapshot.turn_id,
            "last_seq": snapshot.last_seq,
            "state_json": canonical_json(snapshot.state.model_dump(mode="json")),
            "state_hash": snapshot.state_hash,
            "schema_version": snapshot.schema_version,
            "version": snapshot.version,
            "created_at": datetime.now(timezone.utc),
        }
        with self._session_factory() as session, session.begin():
            row = session.scalars(
                select(SnapshotRow).where(
                    SnapshotRow.game_id == snapshot.game_id,
                    SnapshotRow.snapshot_version == snapshot.snapshot_version,
                )
            ).first()
            if row is None:
                session.add(
                    SnapshotRow(
                        game_id=snapshot.game_id,
                        snapshot_version=snapshot.snapshot_version,
                        **values,
                    )
                )
            else:
                for field, value in values.items():
                    setattr(row, field, value)

    # ----------------------------------------------------------- idempotency
    def get_idempotent(self, key: str) -> IdempotencyRecord | None:
        """按存储键取幂等记录；``result_json`` 为空时返回 ``None``。"""
        with self._session_factory() as session:
            row = session.get(IdempotencyRow, key)
            if row is None:
                return None
            payload = json.loads(row.result_json)
            events = tuple(Event.model_validate(item) for item in payload.get("events", []))
            response = payload.get("response")
            if not isinstance(response, dict):
                return None
            return IdempotencyRecord(
                key=row.store_key,
                params_hash=row.params_hash,
                payload=response,
                events=events,
            )

    def save_idempotent(self, record: IdempotencyRecord) -> None:
        """保存幂等记录；同键已存在时覆盖为最新（同键异参判定由编排层完成）。"""
        payload = {
            "response": record.payload,
            "events": [event.model_dump(mode="json") for event in record.events],
        }
        result_json = canonical_json(payload)
        now = datetime.now(timezone.utc)
        with self._session_factory() as session, session.begin():
            row = session.get(IdempotencyRow, record.key)
            if row is None:
                session.add(
                    IdempotencyRow(
                        store_key=record.key,
                        params_hash=record.params_hash,
                        result_json=result_json,
                        created_at=now,
                    )
                )
            else:
                row.params_hash = record.params_hash
                row.result_json = result_json
                row.created_at = now

    # ------------------------------------------------------------------- audit
    def save_audit(self, records: Sequence[AuditRecord]) -> None:
        """单事务批量追加审计行（只 INSERT，不 UPDATE/DELETE；重复 ``audit_uid`` 忽略）。"""
        items = list(records)
        if not items:
            return
        now = datetime.now(timezone.utc)
        statement = insert(AuditEventRow).prefix_with("OR IGNORE")
        with self._session_factory() as session, session.begin():
            session.execute(statement, [self._audit_values(record, now) for record in items])

    def read_audit(self, game_id: str, *, turn_id: int | None = None) -> list[AuditRecord]:
        """按 ``(turn_id, proposal_id, attempt)`` 升序读审计行；无存档返回空列表。"""
        statement = select(AuditEventRow).where(AuditEventRow.game_id == game_id)
        if turn_id is not None:
            statement = statement.where(AuditEventRow.turn_id == turn_id)
        statement = statement.order_by(
            AuditEventRow.turn_id.asc(),
            AuditEventRow.proposal_id.asc(),
            AuditEventRow.attempt.asc(),
        )
        with self._session_factory() as session:
            rows = session.scalars(statement).all()
        return [self._audit_from_row(row) for row in rows]

    @staticmethod
    def _audit_uid(record: AuditRecord) -> str:
        """确定性主键：同 ``(game_id, turn_id, attempt, proposal_id)`` 重投时保持只增。"""
        return (
            f"aud_{record.game_id}:{record.turn_id:06d}:{record.attempt:03d}:{record.proposal_id}"
        )

    @classmethod
    def _audit_values(cls, record: AuditRecord, created_at: datetime) -> dict[str, Any]:
        """``AuditRecord`` → 行字典（``args`` 存规范 JSON 文本；created_at 仅落库）。"""
        return {
            "audit_uid": cls._audit_uid(record),
            "game_id": record.game_id,
            "turn_id": record.turn_id,
            "attempt": record.attempt,
            "proposal_id": record.proposal_id,
            "resolution_id": record.resolution_id,
            "actor_id": record.actor_id,
            "tool": record.tool,
            "args_json": canonical_json(record.args),
            "reason": record.reason,
            "priority": record.priority,
            "source": record.source,
            "verdict": record.verdict,
            "verdict_reason": record.verdict_reason,
            "status": record.status,
            "created_at": created_at,
        }

    @staticmethod
    def _audit_from_row(row: AuditEventRow) -> AuditRecord:
        """ORM 行 → ``AuditRecord``（``args_json`` 解析回 dict）。"""
        return AuditRecord(
            game_id=row.game_id,
            turn_id=row.turn_id,
            attempt=row.attempt,
            proposal_id=row.proposal_id,
            resolution_id=row.resolution_id,
            actor_id=row.actor_id,
            tool=row.tool,
            args=json.loads(row.args_json),
            reason=row.reason,
            priority=row.priority,
            source=row.source,
            verdict=row.verdict,
            verdict_reason=row.verdict_reason,
            status=row.status,
        )

    # ------------------------------------------------------------------ commit
    def commit(self, payload: CommitPayload) -> bool:
        """单事务原子提交；版本冲突/序号不连续/唯一键重复返回 ``False`` 且零写入。

        同库多存档（D-008-3）：事件唯一性按 ``(game_id, seq)`` 主键与
        ``(game_id, event_uid)`` 唯一约束判定，其它存档的同序号/同 ``evt_*`` 不冲突。
        """
        events = list(payload.stamped_events)
        if not self._events_well_formed(events):
            return False
        if payload.new_version != payload.expected_version + 1:
            # CAS 语义：每次提交只前进一个版本（与 InMemoryRepository 一致）
            return False
        if payload.expected_version == 0 and not self._is_game_created_first(events):
            return False
        session = self._session_factory()
        try:
            max_seq = self._max_seq(session, payload.game_id)
            if not self._events_follow(max_seq, events, payload.last_seq):
                session.rollback()
                return False
            if not self._write_current_row(session, payload):
                session.rollback()
                return False
            session.add_all([self._row_from_event(event, payload.game_id) for event in events])
            session.commit()
            return True
        except IntegrityError:
            session.rollback()
            return False
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _write_current_row(self, session: Session, payload: CommitPayload) -> bool:
        """CAS 更新 current 行；行缺失时按「重建后补齐缓存」语义 INSERT。"""
        state_json = canonical_json(payload.new_state.model_dump(mode="json"))
        now = datetime.now(timezone.utc)
        values = {
            "state_json": state_json,
            "version": payload.new_version,
            "last_seq": payload.last_seq,
            "updated_at": now,
        }
        if payload.expected_version == 0:
            if session.get(GameStateCurrentRow, payload.game_id) is not None:
                return False
            session.add(GameStateCurrentRow(game_id=payload.game_id, **values))
            session.flush()
            return True
        result = cast(
            CursorResult[Any],
            session.execute(
                update(GameStateCurrentRow)
                .where(
                    GameStateCurrentRow.game_id == payload.game_id,
                    GameStateCurrentRow.version == payload.expected_version,
                )
                .values(**values)
            ),
        )
        if result.rowcount > 0:
            return True
        if session.get(GameStateCurrentRow, payload.game_id) is not None:
            return False
        # current 行被删除（快照重建后缓存丢失）：版本无从校验，按补齐缓存写入
        session.add(GameStateCurrentRow(game_id=payload.game_id, **values))
        session.flush()
        return True

    @staticmethod
    def _max_seq(session: Session, game_id: str) -> int:
        value = session.scalar(select(func.max(EventRow.seq)).where(EventRow.game_id == game_id))
        return 0 if value is None else int(value)

    @staticmethod
    def _events_well_formed(events: list[Event]) -> bool:
        """校验盖章态自洽（``id = evt_{seq:06d}``、UTC ts、rng_seed 非空）。"""
        for event in events:
            if not event.is_stamped or event.seq is None or event.id is None:
                return False
            if event.rng_seed is None or event.ts is None or event.ts.tzinfo is None:
                return False
            if event.ts.utcoffset() != timedelta(0):
                return False
            if event.id != f"evt_{event.seq:06d}":
                return False
        return True

    @staticmethod
    def _is_game_created_first(events: list[Event]) -> bool:
        """新存档提交必须自带 ``seq=1`` 的 ``game_created`` 首事件（第 14 章 14.7）。"""
        if not events:
            return False
        first = events[0]
        return first.seq == 1 and first.event_type is EventType.GAME_CREATED

    @staticmethod
    def _events_follow(max_seq: int, events: list[Event], last_seq: int) -> bool:
        """事件 ``seq`` 必须紧接该存档已有序号，且末条等于 ``payload.last_seq``。"""
        if not events:
            return last_seq == max_seq
        for offset, event in enumerate(events):
            if event.seq != max_seq + offset + 1:
                return False
        return events[-1].seq == last_seq

    @staticmethod
    def _row_from_event(event: Event, game_id: str) -> EventRow:
        """领域盖章事件 → ORM 行（调用前已由 ``_events_well_formed`` 校验非空字段）。"""
        return EventRow(
            seq=event.seq,
            event_uid=event.id,
            game_id=game_id,
            turn_id=event.turn_id,
            event_type=event.event_type.value if event.event_type is not None else None,
            actor_id=event.actor_id,
            method=event.method,
            params=canonical_json(event.params),
            result=canonical_json(event.result),
            rng_seed=event.rng_seed,
            ts=event.ts,
        )

    @staticmethod
    def _event_from_row(row: EventRow) -> Event:
        """ORM 行 → 领域盖章事件；未知 ``event_type`` 按损坏抛 ``ValueError``。"""
        event_type: EventType | None = None
        if row.event_type is not None:
            try:
                event_type = EventType(row.event_type)
            except ValueError as exc:
                raise ValueError(
                    f"event seq={row.seq} has unknown event_type {row.event_type!r}"
                ) from exc
        return Event(
            id=row.event_uid,
            turn_id=row.turn_id,
            seq=row.seq,
            actor_id=row.actor_id,
            method=row.method,
            event_type=event_type,
            params=json.loads(row.params),
            result=json.loads(row.result),
            rng_seed=row.rng_seed,
            ts=SqliteRepository._as_utc(row.ts),
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        """SQLite 的 ``DATETIME`` 不保留时区；落库均为 UTC，读出时补 UTC 时区。"""
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _ensure_contiguous(events: list[Event], start_seq: int) -> None:
        """重建前校验事件流：盖章态、``id`` 与 ``seq`` 连续自洽，否则视为损坏。"""
        for offset, event in enumerate(events):
            expected_seq = start_seq + offset
            if event.seq != expected_seq:
                raise ValueError(f"event stream gap: expected seq={expected_seq}, got {event.seq}")
            if event.id != f"evt_{expected_seq:06d}":
                raise ValueError(
                    f"event seq={expected_seq} has id={event.id!r}, expected "
                    f"'evt_{expected_seq:06d}'"
                )
