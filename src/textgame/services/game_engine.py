"""唯一写入者 ``GameEngine`` (第 21 章 21.2.1 的 M2 落地，契约 §4.4)。

``GameEngine`` 是唯一可以同时写事件表与当前状态行的组件：``create_game`` 写
``game_created`` 首事件并生成初始快照，``submit`` 做 CAS 版本校验 → EventLog 盖章 →
纯投影 → 单次 ``repo.commit`` 原子提交 → 按阈值追加派生快照。本类不调模型、不做规则
计算、不新增 IO：只依赖 ``domain`` 与 ``services.ports``（第 4 章 4.2、第 14 章 14.5）。

幂等键由编排层在调用前后读写幂等表（契约 §4.5）；本类签名保留 ``idempotency_key``
以便与第 21 章接口对齐，但不在引擎内二次缓存。
"""

from __future__ import annotations

from uuid import uuid4

from textgame.domain.events import Event, EventLog, EventType
from textgame.domain.hashing import state_hash
from textgame.domain.models import GameState
from textgame.domain.results import ErrorCode, Result
from textgame.domain.scenario import PLAYER_ID, ROOM_ENTRY, build_initial_state
from textgame.services.ports import CommitPayload, GameRepository, SnapshotRecord

_INITIAL_SNAPSHOT_VERSION = 1
_GAME_ID_PREFIX = "game_"


class GameEngine:
    """唯一写入口；所有状态变更都必须经 ``repo.commit`` 单次原子提交。"""

    def __init__(
        self,
        repo: GameRepository,
        *,
        snapshot_every_turns: int = 20,
        snapshot_every_events: int = 500,
    ) -> None:
        """装配仓储与快照阈值（第 14 章 14.4：20 回合 / 500 事件，先到先算）。"""
        if snapshot_every_turns < 1 or snapshot_every_events < 1:
            raise ValueError("snapshot thresholds must be positive integers")
        self._repo = repo
        self._snapshot_every_turns = snapshot_every_turns
        self._snapshot_every_events = snapshot_every_events

    def create_game(
        self,
        *,
        save_seed: str,
        idempotency_key: str | None = None,
        game_id: str | None = None,
    ) -> Result:
        """创建存档：``game_created`` 首事件（seq=1）+ 当前状态 + 初始快照（契约 §3.2）。

        返回 ``Result.data = {"game_id", "turn", "version", "snapshot_version", "save_seed"}``；
        同一 ``game_id`` 已存在时返回 ``CONFLICT``。
        """
        if not save_seed:
            return Result.fail(ErrorCode.INVALID_INPUT, "save_seed must be a non-empty string")
        new_game_id = game_id or f"{_GAME_ID_PREFIX}{uuid4().hex}"
        if self._repo.load(new_game_id) is not None:
            return Result.fail(ErrorCode.CONFLICT, f"game already exists: {new_game_id}")

        initial_state = build_initial_state(save_seed=save_seed)
        game_created = Event(
            turn_id=1,
            actor_id="system",
            method="game_created",
            event_type=EventType.GAME_CREATED,
            params={
                "card_id": None,
                "starting_room_id": ROOM_ENTRY,
                "initial_character_id": PLAYER_ID,
                "save_seed": save_seed,
                "snapshot_version": _INITIAL_SNAPSHOT_VERSION,
            },
            result={"initial_state": initial_state.model_dump(mode="json")},
        )
        try:
            stamped = EventLog(save_seed).stamp([game_created])
        except ValueError as exc:  # 序列化校验失败按 SCHEMA_VIOLATION 处理（第 5 章 5.8）
            return Result.fail(ErrorCode.SCHEMA_VIOLATION, str(exc))
        try:
            new_state = initial_state.apply(stamped)
        except ValueError as exc:
            return Result.fail(ErrorCode.INTERNAL, f"game_created projection failed: {exc}")

        payload = CommitPayload(
            game_id=new_game_id,
            expected_version=0,
            new_version=1,
            last_seq=1,
            new_state=new_state,
            stamped_events=tuple(stamped),
        )
        if not self._repo.commit(payload):
            return Result.fail(
                ErrorCode.CONFLICT,
                "创建存档失败：当前数据库已存在存档（M2 每库单存档，见 PROGRESS §5 D-008-3）",
            )
        self._save_snapshot(
            new_game_id,
            new_state,
            snapshot_version=_INITIAL_SNAPSHOT_VERSION,
            last_seq=1,
        )
        return Result.success(
            data={
                "game_id": new_game_id,
                "turn": new_state.turn,
                "version": 1,
                "snapshot_version": _INITIAL_SNAPSHOT_VERSION,
                "save_seed": save_seed,
            }
        )

    def submit(
        self,
        *,
        game_id: str,
        expected_version: int,
        drafts: list[Event],
        idempotency_key: str | None = None,
    ) -> Result:
        """提交一批草稿事件：CAS + 盖章 + 投影 + 原子提交（契约 §4.4）。

        返回 ``Result.data = {"version": new_version, "turn_id": turn}`` 与盖章态事件；
        版本冲突返回 ``CONFLICT``，事件不合法返回 ``SCHEMA_VIOLATION``，投影失败返回
        ``INTERNAL``。任何失败都不写库（``repo.commit`` 未调用或返回 False）。
        """
        record = self._repo.load(game_id)
        if record is None:
            return Result.fail(ErrorCode.NOT_FOUND, f"game not found: {game_id}")
        if record.version != expected_version:
            return Result.fail(
                ErrorCode.CONFLICT,
                f"version mismatch: expected {expected_version}, current {record.version}",
            )
        if not drafts:
            return Result.fail(ErrorCode.INVALID_INPUT, "no draft events to submit")
        for draft in drafts:
            if draft.is_stamped:
                return Result.fail(ErrorCode.SCHEMA_VIOLATION, "submit only accepts draft events")
            if draft.event_type is None:
                return Result.fail(
                    ErrorCode.SCHEMA_VIOLATION,
                    f"event {draft.method} is missing event_type",
                )

        save_seed = record.state.rng_state.save_seed
        log = EventLog(save_seed)
        try:
            log.append(self._repo.read_events(game_id))
        except ValueError as exc:
            return Result.fail(ErrorCode.INTERNAL, f"event log is corrupted: {exc}")
        try:
            stamped = log.stamp(drafts)
        except ValueError as exc:
            return Result.fail(ErrorCode.SCHEMA_VIOLATION, str(exc))
        try:
            new_state = record.state.apply(stamped)
        except ValueError as exc:
            return Result.fail(ErrorCode.INTERNAL, f"event projection failed: {exc}")

        new_version = expected_version + 1
        last_seq = record.last_seq + len(stamped)
        payload = CommitPayload(
            game_id=game_id,
            expected_version=expected_version,
            new_version=new_version,
            last_seq=last_seq,
            new_state=new_state,
            stamped_events=tuple(stamped),
        )
        if not self._repo.commit(payload):
            return Result.fail(ErrorCode.CONFLICT, "concurrent write detected")
        self._maybe_save_snapshot(game_id, new_state, last_seq=last_seq)
        return Result.success(
            data={"version": new_version, "turn_id": new_state.turn},
            events=stamped,
        )

    def _maybe_save_snapshot(self, game_id: str, state: GameState, *, last_seq: int) -> None:
        """按阈值追加派生快照；快照只是缓存，写失败不影响提交正确性。"""
        latest = self._repo.latest_snapshot(game_id)
        if latest is None:
            self._save_snapshot(
                game_id,
                state,
                snapshot_version=_INITIAL_SNAPSHOT_VERSION,
                last_seq=last_seq,
            )
            return
        events_since = last_seq - latest.last_seq
        turns_since = state.turn - latest.turn_id
        if events_since >= self._snapshot_every_events or turns_since >= self._snapshot_every_turns:
            self._save_snapshot(
                game_id,
                state,
                snapshot_version=latest.snapshot_version + 1,
                last_seq=last_seq,
            )

    def _save_snapshot(
        self, game_id: str, state: GameState, *, snapshot_version: int, last_seq: int
    ) -> None:
        """写一条快照记录（``state_hash`` 由 ``domain.hashing`` 统一计算）。"""
        self._repo.save_snapshot(
            SnapshotRecord(
                game_id=game_id,
                snapshot_version=snapshot_version,
                turn_id=state.turn,
                last_seq=last_seq,
                state=state,
                state_hash=state_hash(state),
                schema_version=1,
            )
        )


__all__ = ["GameEngine"]
