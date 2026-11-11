"""唯一写入者 ``GameEngine`` (第 21 章 21.2.1 的 M2 落地，契约 §4.4)。

``GameEngine`` 是唯一可以同时写事件表与当前状态行的组件：``create_game`` 写
``game_created`` 首事件并生成初始快照，``submit`` 做 CAS 版本校验 → EventLog 盖章 →
纯投影 → 单次 ``repo.commit`` 原子提交 → 按阈值追加派生快照。本类不调模型、不做规则
计算、不新增 IO：只依赖 ``domain`` 与 ``services.ports``（第 4 章 4.2、第 14 章 14.5）。

幂等键由编排层在调用前后读写幂等表（契约 §4.5）；本类签名保留 ``idempotency_key``
以便与第 21 章接口对齐，但不在引擎内二次缓存。
"""

from __future__ import annotations

import logging
from typing import Any, Literal
from uuid import uuid4

from textgame.domain.events import Event, EventLog, EventType
from textgame.domain.hashing import state_hash
from textgame.domain.models import GameState
from textgame.domain.results import ErrorCode, Result
from textgame.domain.scenario import (
    PLAYER_ID,
    ROOM_ENTRY,
    build_initial_state,
    build_m3_state,
    build_m4_state,
)
from textgame.domain.scenario_story import build_story_state
from textgame.services.idempotency_locks import snapshot_lock
from textgame.services.ports import CommitPayload, GameRepository, SnapshotRecord

Scenario = Literal["m2", "m3", "m4", "story", "custom"]

logger = logging.getLogger(__name__)

_INITIAL_SNAPSHOT_VERSION = 1
_GAME_ID_PREFIX = "game_"
_SCENARIOS: frozenset[str] = frozenset({"m2", "m3", "m4", "story", "custom"})


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
        # R-21：每个存档首次写入时做一次全量 replay 完整性校验；之后按尾部增量续写。
        self._verified_games: set[str] = set()

    def create_game(
        self,
        *,
        save_seed: str,
        idempotency_key: str | None = None,
        game_id: str | None = None,
        scenario: Scenario = "m2",
        initial_state: dict[str, Any] | None = None,
        scenario_id: str | None = None,
        card_id: str | None = None,
        world_book_id: str | None = None,
        world_book_name: str | None = None,
    ) -> Result:
        """创建存档：``game_created`` 首事件（seq=1）+ 当前状态 + 初始快照（契约 §3.2）。

        ``scenario``：``m2``=M2 单场景（默认，保证 M2 golden 不回归）；``m3``=多角色
        场景（``build_m3_state``）；``m4``=M4 制造场景（``build_m4_state``，D-M4-11）；
        ``story``=剧情场景「余烬哨站」（``build_story_state``，D1/T10；旧三场景行为与事件
        逐字不变）。

        D-M6-2 加法（默认 ``None`` 行为与事件/哈希完全不变）：``initial_state`` 为预先构造
        好的初始状态 JSON（人物卡建局时由 ``CardService`` 映射，玩家槽位仍是 ``PLAYER_ID``），
        提供时不再构造内置场景状态；``card_id`` 只写入 ``game_created.params`` 供回放/审计
        （加法字段，M2–M4 无卡路径恒为 ``None``）。

        H4 加法（D-H4）：``world_book_id``/``world_book_name`` **仅在非 None 时**追加进
        ``game_created.params``（世界书绑定留给编排层/查询层经 ``read_first_event`` 读取；
        旧请求的 params 逐字不变，事件哈希不受影响）。存在性校验在编排层/接入层完成
        （未知世界书 404），引擎不做 IO 查询。

        返回 ``Result.data = {"game_id", "turn", "version", "snapshot_version", "save_seed"}``；
        同一 ``game_id`` 已存在时返回 ``CONFLICT``；同一数据库可创建多个不同 ``game_id``
        的存档（D-008-3 复合主键落地后，``events.seq`` 每存档各自从 1 起）。
        """
        if not save_seed:
            return Result.fail(ErrorCode.INVALID_INPUT, "save_seed must be a non-empty string")
        if scenario not in _SCENARIOS:
            return Result.fail(ErrorCode.INVALID_INPUT, f"unknown scenario: {scenario!r}")
        if scenario == "custom" and initial_state is None:
            # 用户上传场景必须先由场景服务物化初始状态；引擎不做 IO/解析，避免分层污染。
            return Result.fail(
                ErrorCode.INVALID_INPUT,
                "scenario=custom 需要提供 initial_state（由场景服务物化）",
            )
        new_game_id = game_id or f"{_GAME_ID_PREFIX}{uuid4().hex}"
        if self._repo.load(new_game_id) is not None:
            return Result.fail(ErrorCode.CONFLICT, f"game already exists: {new_game_id}")

        if initial_state is not None:
            try:
                base_state = GameState.model_validate(initial_state)
            except ValueError as exc:
                return Result.fail(ErrorCode.SCHEMA_VIOLATION, f"invalid initial_state: {exc}")
        elif scenario == "m3":
            base_state = build_m3_state(save_seed=save_seed)
        elif scenario == "m4":
            base_state = build_m4_state(save_seed=save_seed)
        elif scenario == "story":
            base_state = build_story_state(save_seed=save_seed)
        else:
            base_state = build_initial_state(save_seed=save_seed)
        # R-06：初始状态的种子必须是本存档实际使用的种子，否则首事件/后续事件无法 replay。
        if base_state.rng_state.save_seed != save_seed:
            return Result.fail(
                ErrorCode.SCHEMA_VIOLATION,
                "initial_state.rng_state.save_seed 与 save_seed 不一致（回放不可复算）",
            )
        if PLAYER_ID not in base_state.characters:
            return Result.fail(
                ErrorCode.SCHEMA_VIOLATION,
                f"initial_state 缺少玩家槽位 {PLAYER_ID}",
            )
        # 用户上传场景（scenario="custom"）的出生房间来自物化状态，而不是内置常量；
        # 其它场景/人物卡路径保持 ROOM_ENTRY，旧事件 params 逐字不变（golden 零影响）。
        starting_room_id = ROOM_ENTRY
        if scenario == "custom":
            custom_player = base_state.characters.get(PLAYER_ID)
            if custom_player is not None:
                starting_room_id = custom_player.location
        created_params: dict[str, Any] = {
            "card_id": card_id,
            "starting_room_id": starting_room_id,
            "initial_character_id": PLAYER_ID,
            "save_seed": save_seed,
            "snapshot_version": _INITIAL_SNAPSHOT_VERSION,
        }
        # 仅用户场景写入 scenario_id（加法字段）；旧场景 params/事件哈希逐字不变。
        if scenario_id is not None:
            created_params["scenario_id"] = scenario_id
        # H4：仅建局显式绑定世界书时追加（旧请求 params 逐字不变；含 name 便于回放/审计）。
        if world_book_id is not None:
            created_params["world_book_id"] = world_book_id
        if world_book_name is not None:
            created_params["world_book_name"] = world_book_name
        game_created = Event(
            turn_id=1,
            actor_id="system",
            method="game_created",
            event_type=EventType.GAME_CREATED,
            params=created_params,
            result={"initial_state": base_state.model_dump(mode="json")},
        )
        try:
            stamped = EventLog(save_seed).stamp([game_created])
        except ValueError as exc:  # 序列化校验失败按 SCHEMA_VIOLATION 处理（第 5 章 5.8）
            return Result.fail(ErrorCode.SCHEMA_VIOLATION, str(exc))
        try:
            new_state = base_state.apply(stamped)
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
                "创建存档失败：同一 game_id 的存档已存在或并发写入冲突",
            )
        with snapshot_lock(new_game_id):
            self._save_snapshot(
                new_game_id,
                new_state,
                snapshot_version=_INITIAL_SNAPSHOT_VERSION,
                last_seq=1,
                version=1,
            )
        self._verified_games.add(new_game_id)
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
        if game_id not in self._verified_games:
            # 首次写入：全量读取并 replay 一次（R-06/R-21），之后只读尾部增量。
            verifier = EventLog(save_seed)
            try:
                verifier.append(self._repo.read_events(game_id))
                verifier.replay()
            except ValueError as exc:
                return Result.fail(ErrorCode.INTERNAL, f"event log is corrupted: {exc}")
            self._verified_games.add(game_id)
        log = EventLog.resume(save_seed, last_seq=record.last_seq)
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
        self._maybe_save_snapshot(game_id, new_state, last_seq=last_seq, version=new_version)
        return Result.success(
            data={"version": new_version, "turn_id": new_state.turn},
            events=stamped,
        )

    def _maybe_save_snapshot(
        self, game_id: str, state: GameState, *, last_seq: int, version: int
    ) -> None:
        """按阈值追加派生快照；快照只是缓存，**任何失败都不影响提交正确性**（R-03）。

        R-10：同一 ``game_id`` 的「读最新版本 → 计算下一版本 → 写入」在分段锁内串行，
        避免并发提交算出同一个 ``snapshot_version`` 并插入两条。
        """
        with snapshot_lock(game_id):
            self._maybe_save_snapshot_locked(game_id, state, last_seq=last_seq, version=version)

    def _maybe_save_snapshot_locked(
        self, game_id: str, state: GameState, *, last_seq: int, version: int
    ) -> None:
        try:
            latest = self._repo.latest_snapshot(game_id)
        except Exception:
            logger.warning("读取最新快照失败（跳过本次快照），game_id=%s", game_id, exc_info=True)
            return
        if latest is None:
            self._save_snapshot(
                game_id,
                state,
                snapshot_version=_INITIAL_SNAPSHOT_VERSION,
                last_seq=last_seq,
                version=version,
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
                version=version,
            )

    def _save_snapshot(
        self, game_id: str, state: GameState, *, snapshot_version: int, last_seq: int, version: int
    ) -> None:
        """写一条快照记录（``state_hash`` 由 ``domain.hashing`` 统一计算）。

        快照是派生缓存：SQLite locked / 磁盘满 / 状态不可 JSON 化等异常只记 WARNING，
        绝不把「已提交成功的回合」报告成失败（R-03）。
        """
        try:
            self._repo.save_snapshot(
                SnapshotRecord(
                    game_id=game_id,
                    snapshot_version=snapshot_version,
                    turn_id=state.turn,
                    last_seq=last_seq,
                    state=state,
                    state_hash=state_hash(state),
                    schema_version=1,
                    version=version,
                )
            )
        except Exception:
            logger.warning(
                "快照写入失败（已提交状态不受影响），game_id=%s snapshot_version=%s",
                game_id,
                snapshot_version,
                exc_info=True,
            )


__all__ = ["GameEngine"]
