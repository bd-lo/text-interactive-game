"""M5-023 批量合并 + 休眠唤醒 + 调用审计（第 12 章 §12.7、第 11 章 §11.2、第 17 章 023）。

设计口径（离线可回放、默认装配零行为变化）：

- **批量合并**：仅对组合根显式声明的「同场景公共群演」``batchable_actors``。批量输入由
  :func:`build_public_perception` 构造（``actor_id="scene"``、``private_memory=[]``、只含
  房间公共实体与公共事件），**私有记忆与批量互斥**：未声明为可批量的角色（含私有记忆
  角色）一律走串行路径，用各自的 ``PerceptionSlice``（含私有记忆）；每批 ≤4 个 NPC，只发
  一次 ``BatchAgentLike.propose_batch``；单个 NPC 回复解析失败只丢该 NPC（审计 ``DROPPED``），
  整批调用异常时降级为逐 NPC 串行 Agent（单点失败同样只丢该 NPC）。
- **休眠状态机**：``DORMANT → ARMED → ACTIVE → DORMANT``；唤醒仅三种（玩家触发 / 进入
  同场景 / 计划触发点到达）；ACTIVE 窗口 = 本回合 + 2 回合（含）；``should_invoke`` 为
  False 时只写 ``SKIPPED`` 审计、不调用任何 Agent（「无唤醒即无调用」）。
- **调用审计**：本切片**不新增数据库表/迁移**（Alembic 0003 留给并行卡库轨，登记为文档
  偏离）：``InvocationLog`` 是进程内可注入端口，默认 :class:`InMemoryInvocationLog` 同时
  输出结构化日志 ``agent_invocation {...}``；后续需要持久化时再补表。
- 组件全部由组合根注入；未注入时编排层保持原路径（M2–M4 golden 61 零回归）。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Collection, Mapping, Sequence
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Protocol

from textgame.agents.character_agent import CharacterAgentLike
from textgame.agents.dto import ParseOutcome
from textgame.domain.events import Event, EventType
from textgame.domain.models import EntityRef, GameState, PerceptionSlice, Proposal
from textgame.domain.perception import LOCAL_EVENTS_LIMIT
from textgame.domain.scenario import PLAYER_ID
from textgame.services.actor_directory import ActorDirectory
from textgame.services.query_service import QueryService

__all__ = [
    "DEFAULT_ACTIVE_WINDOW_TURNS",
    "MAX_BATCH_SIZE",
    "PUBLIC_SCENE_ACTOR_ID",
    "BatchAgentLike",
    "BatchCandidate",
    "BatchReply",
    "BatchSleepScheduler",
    "DormancyScheduler",
    "DormancyState",
    "InMemoryInvocationLog",
    "InvocationLog",
    "InvocationMode",
    "InvocationRecord",
    "WakeCause",
    "build_public_perception",
    "filter_public_events",
    "plan_batches",
    "plan_serial",
]

#: 每批最多合并的公共群演 NPC 数（第 12 章 §12.7：每批 ≤4 个）。
MAX_BATCH_SIZE = 4
#: ACTIVE 窗口 = 唤醒回合 + 之后 2 个回合（第 12 章 §12.7「本回合 + 2 回合」）。
DEFAULT_ACTIVE_WINDOW_TURNS = 2
#: 公共感知切片的 actor_id 占位（不指向任何真实角色，避免批量输入伪装成私有切片）。
PUBLIC_SCENE_ACTOR_ID = "scene"

_LOGGER = logging.getLogger("textgame.agents")


# ---------------------------------------------------------------------------
# 调用审计端口（不新增数据库表：进程内实现 + 结构化日志）
# ---------------------------------------------------------------------------
class InvocationMode(str, Enum):
    """一次角色调度审计的模式：串行 / 批量 / 跳过（休眠）/ 丢弃（降级）。"""

    SERIAL = "serial"
    BATCH = "batch"
    SKIPPED = "skipped"
    DROPPED = "dropped"


class WakeCause(str, Enum):
    """唤醒原因（仅三种合法唤醒 + 活跃窗口延续 + 未唤醒）。"""

    PLAYER_TRIGGER = "player_trigger"
    SCENE_ENTRY = "scene_entry"
    PLAN_TRIGGER = "plan_trigger"
    ACTIVE_WINDOW = "active_window"
    NONE = "no_wake"


@dataclass(frozen=True)
class InvocationRecord:
    """一条可审计的调度记录（第 12 章 §12.7 ``agent_invocation_log`` 的进程内投影）。"""

    turn_id: int
    actor_id: str
    mode: InvocationMode
    cause: WakeCause
    batch_size: int = 1
    detail: str = ""


class InvocationLog(Protocol):
    """调用审计端口：进程内实现可注入；后续持久化实现同一接口（不新增表属本切片口径）。"""

    def record(self, entry: InvocationRecord) -> None: ...


class InMemoryInvocationLog:
    """进程内审计实现：保存只读快照 + 输出结构化日志（不含 Prompt/私有记忆内容）。"""

    def __init__(self, *, logger: logging.Logger | None = None) -> None:
        """初始化空日志；``logger`` 可注入（测试断言日志格式用）。"""
        self._entries: list[InvocationRecord] = []
        self._logger = logger or _LOGGER

    def record(self, entry: InvocationRecord) -> None:
        """记录一条调度审计并输出结构化日志（字段均可序列化、无私有数据）。"""
        self._entries.append(entry)
        self._logger.info("agent_invocation %s", json.dumps(asdict(entry), ensure_ascii=False))

    @property
    def entries(self) -> list[InvocationRecord]:
        """已记录条目的只读快照。"""
        return list(self._entries)

    def count(self, *, mode: InvocationMode | None = None) -> int:
        """按模式统计条目数（测试断言「无唤醒即无调用」用）。"""
        if mode is None:
            return len(self._entries)
        return sum(1 for entry in self._entries if entry.mode is mode)


# ---------------------------------------------------------------------------
# 休眠状态机
# ---------------------------------------------------------------------------
class DormancyState(str, Enum):
    """休眠状态：休眠 / 待唤醒（已布防）/ 活跃。"""

    DORMANT = "dormant"
    ARMED = "armed"
    ACTIVE = "active"


class DormancyScheduler:
    """每 actor 的休眠状态机（回合驱动、纯内存、可注入时钟无关）。

    - ``DORMANT``：零调用；``arm`` 后转 ``ARMED``（等待三种唤醒之一）；
    - ``ARMED``：仍零调用，直到显式 ``wake``（计划触发点到达等）；
    - ``ACTIVE``：唤醒回合起 ``active_window_turns`` 个后续回合内调用；窗口结束后回到
      ``DORMANT``。
    """

    def __init__(self, *, active_window_turns: int = DEFAULT_ACTIVE_WINDOW_TURNS) -> None:
        """``active_window_turns`` 必须 ≥0（默认 2 = 本回合 + 2 回合）。"""
        if active_window_turns < 0:
            raise ValueError(f"active_window_turns must be >= 0, got {active_window_turns}")
        self._window = active_window_turns
        self._state: dict[str, DormancyState] = {}
        self._active_until: dict[str, int] = {}
        self._last_cause: dict[str, WakeCause] = {}

    def state_of(self, actor_id: str) -> DormancyState:
        """返回 actor 当前状态（未登记视为 DORMANT）。"""
        return self._state.get(actor_id, DormancyState.DORMANT)

    def active_until(self, actor_id: str) -> int | None:
        """返回 ACTIVE 窗口的最后一个回合；非 ACTIVE 返回 None。"""
        if self.state_of(actor_id) is not DormancyState.ACTIVE:
            return None
        return self._active_until.get(actor_id)

    def last_wake_cause(self, actor_id: str) -> WakeCause | None:
        """返回最近一次唤醒原因（审计/日志用）。"""
        return self._last_cause.get(actor_id)

    def arm(self, actor_id: str) -> None:
        """布防：DORMANT → ARMED（ACTIVE 保持不变；唤醒仍需显式 ``wake``）。"""
        if self.state_of(actor_id) is DormancyState.DORMANT:
            self._state[actor_id] = DormancyState.ARMED

    def wake(self, actor_id: str, *, turn_id: int, cause: WakeCause) -> None:
        """唤醒：任意状态 → ACTIVE，窗口 = ``turn_id + active_window_turns``（含端点）。"""
        if cause is WakeCause.NONE or cause is WakeCause.ACTIVE_WINDOW:
            raise ValueError(f"wake requires a real wake cause, got {cause}")
        if isinstance(turn_id, bool) or turn_id < 1:
            raise ValueError(f"turn_id must be a positive integer, got {turn_id!r}")
        self._state[actor_id] = DormancyState.ACTIVE
        self._active_until[actor_id] = turn_id + self._window
        self._last_cause[actor_id] = cause

    def should_invoke(self, actor_id: str, *, turn_id: int) -> bool:
        """本回合是否允许调用该 actor；超出活跃窗口时自动回到 DORMANT。"""
        if self.state_of(actor_id) is not DormancyState.ACTIVE:
            return False
        if turn_id <= self._active_until.get(actor_id, 0):
            return True
        self._state[actor_id] = DormancyState.DORMANT
        self._active_until.pop(actor_id, None)
        return False


# ---------------------------------------------------------------------------
# 批量合并
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class BatchCandidate:
    """批量规划候选：``batchable=False`` 表示私有记忆角色（只能串行）。"""

    actor_id: str
    room_id: str
    batchable: bool = True


@dataclass(frozen=True)
class BatchReply:
    """批量调用中单个 NPC 的回复：``error`` 非空表示该 NPC 解析失败（只丢它）。"""

    actor_id: str
    proposals: tuple[Proposal, ...] = ()
    error: str | None = None


class BatchAgentLike(Protocol):
    """批量角色 Agent 端口：一次调用接收公共感知 + 同批 actor，返回逐 NPC 回复。"""

    def propose_batch(
        self,
        public_perception: PerceptionSlice,
        actor_ids: Sequence[str],
        *,
        turn_id: int,
        save_seed: str,
    ) -> Sequence[BatchReply]: ...


def filter_public_events(
    events: Sequence[Event], *, limit: int = LOCAL_EVENTS_LIMIT
) -> list[Event]:
    """公共事件口径：已盖章事件按 ``seq`` 升序取最近 ``limit`` 条（不含私有记忆/切片）。

    批量输入只允许公共感知，故不按 actor 过滤（调用方只传入该场景可见事件）；
    未盖章事件属编程错误 → ``ValueError``（与 ``domain.perception`` 口径一致）。
    """
    if limit < 1:
        raise ValueError(f"limit must be positive, got {limit}")
    stamped: list[tuple[int, Event]] = []
    for event in events:
        seq = event.seq
        if seq is None:
            raise ValueError(
                f"public events require stamped events (seq is None): method={event.method}"
            )
        stamped.append((seq, event))
    return [event for _, event in sorted(stamped, key=lambda pair: pair[0])][-limit:]


def build_public_perception(
    state: GameState, room_id: str, events: Sequence[Event], *, limit: int = LOCAL_EVENTS_LIMIT
) -> PerceptionSlice:
    """构造「同场景公共感知」切片：房间公共实体 + 公共事件，**私有记忆恒为空**。

    结构性约束：``actor_id=PUBLIC_SCENE_ACTOR_ID``、``private_memory=[]``、``plan_hint=None``，
    因此批量 Agent 拿不到任何角色的私有记忆（第 8 章 §8.2、第 15 章 §15.4）。
    """
    room = state.rooms.get(room_id)
    if room is None:
        raise ValueError(f"unknown room: {room_id!r}")
    entities: list[EntityRef] = [EntityRef(kind="room", id=room.id, name=room.name)]
    for item_id in room.item_ids:
        item = state.items.get(item_id)
        if item is not None:
            entities.append(EntityRef(kind="item", id=item.id, name=item.name))
    for npc_id in room.npc_ids:
        npc = state.npcs.get(npc_id)
        if npc is not None:
            entities.append(EntityRef(kind="npc", id=npc.id, name=npc.name))
    for character in state.characters.values():
        if character.location == room.id:
            entities.append(EntityRef(kind="character", id=character.id, name=character.name))
    return PerceptionSlice(
        actor_id=PUBLIC_SCENE_ACTOR_ID,
        visible_entities=entities,
        local_events=filter_public_events(events, limit=limit),
        private_memory=[],
        plan_hint=None,
    )


def plan_batches(
    candidates: Sequence[BatchCandidate], *, max_batch_size: int = MAX_BATCH_SIZE
) -> tuple[tuple[str, ...], ...]:
    """按房间分组并把可批量 actor 切成 ≤``max_batch_size`` 的批次（确定性顺序）。

    只处理 ``batchable=True`` 的候选；同一房间的 actor 按 id 升序分组，房间按 id 升序；
    不跨房间合并（同场景边界），返回 ``(actor_id, ...)`` 批次元组。
    """
    if max_batch_size < 1:
        raise ValueError(f"max_batch_size must be positive, got {max_batch_size}")
    by_room: dict[str, list[str]] = {}
    for candidate in candidates:
        if not candidate.batchable:
            continue
        by_room.setdefault(candidate.room_id, []).append(candidate.actor_id)
    batches: list[tuple[str, ...]] = []
    for room_id in sorted(by_room):
        actors = sorted(by_room[room_id])
        for start in range(0, len(actors), max_batch_size):
            batches.append(tuple(actors[start : start + max_batch_size]))
    return tuple(batches)


def plan_serial(candidates: Sequence[BatchCandidate]) -> tuple[str, ...]:
    """需要串行调用的 actor（私有记忆角色：``batchable=False``），按 id 升序。"""
    return tuple(sorted(candidate.actor_id for candidate in candidates if not candidate.batchable))


# ---------------------------------------------------------------------------
# 调度器：休眠唤醒 + 批量/串行执行 + 审计
# ---------------------------------------------------------------------------
class BatchSleepScheduler:
    """把休眠状态机与批量合并组合成可注入的 NPC 调度器（编排层可选接线）。

    构造后调用 :meth:`collect` 返回本回合所有 NPC 提案；未唤醒的 actor 零调用、只写
    ``SKIPPED`` 审计。未注入到编排层时（默认路径）M2–M4 行为完全不变。
    """

    def __init__(
        self,
        *,
        query: QueryService,
        actors: ActorDirectory,
        dormancy: DormancyScheduler | None = None,
        invocation_log: InvocationLog | None = None,
        batch_agent: BatchAgentLike | None = None,
        batchable_actors: Collection[str] = (),
        plan_trigger_turns: Mapping[str, Collection[int]] | None = None,
        max_batch_size: int = MAX_BATCH_SIZE,
    ) -> None:
        """装配调度依赖；``batchable_actors`` 为空 = 全部角色走串行（批量关闭）。"""
        if max_batch_size < 1:
            raise ValueError(f"max_batch_size must be positive, got {max_batch_size}")
        self._query = query
        self._actors = actors
        self._dormancy = dormancy or DormancyScheduler()
        self._log: InvocationLog = invocation_log or InMemoryInvocationLog()
        self._batch_agent = batch_agent
        self._batchable = frozenset(batchable_actors)
        self._plan_trigger_turns: dict[str, frozenset[int]] = {
            actor_id: frozenset(turns) for actor_id, turns in (plan_trigger_turns or {}).items()
        }
        self._max_batch_size = max_batch_size

    @property
    def dormancy(self) -> DormancyScheduler:
        """休眠状态机（供组合根/测试按回合布防或查询状态）。"""
        return self._dormancy

    @property
    def invocation_log(self) -> InvocationLog:
        """调用审计端口（测试断言用）。"""
        return self._log

    def collect(
        self,
        state: GameState,
        events: Sequence[Event],
        *,
        turn_id: int,
        save_seed: str,
        player_action: ParseOutcome | None,
        character_agents: Mapping[str, CharacterAgentLike],
    ) -> list[Proposal]:
        """返回本回合所有 NPC 提案（批量/串行混合，顺序确定性：批次按房间/actor 排序）。"""
        present = tuple(
            actor_id
            for actor_id in sorted(character_agents)
            if self._actors.exists(state, actor_id)
        )
        targets = self._player_targets(player_action, present)
        entered_room = self._player_entered_room(events, turn_id=turn_id)
        active: dict[str, WakeCause] = {}
        for actor_id in present:
            npc_room = self._room_of(state, actor_id)
            cause = self._detect_wake(
                actor_id,
                npc_room=npc_room,
                entered_room=entered_room,
                targets=targets,
                turn_id=turn_id,
            )
            if cause is not None:
                self._dormancy.wake(actor_id, turn_id=turn_id, cause=cause)
                active[actor_id] = cause
            elif self._dormancy.should_invoke(actor_id, turn_id=turn_id):
                active[actor_id] = WakeCause.ACTIVE_WINDOW

        proposals: list[Proposal] = []
        # 1) 批量：仅 active + 声明为公共群演 + 有房间；按房间分组、每批 ≤4
        batch_candidates = [
            BatchCandidate(actor_id=actor_id, room_id=room)
            for actor_id in active
            if actor_id in self._batchable and (room := self._room_of(state, actor_id)) is not None
        ]
        for batch in plan_batches(batch_candidates, max_batch_size=self._max_batch_size):
            proposals.extend(
                self._collect_batch(
                    state=state,
                    events=events,
                    actor_ids=batch,
                    turn_id=turn_id,
                    save_seed=save_seed,
                    active=active,
                    character_agents=character_agents,
                )
            )
        # 2) 串行：未声明为可批量的私有角色（私有记忆与批量互斥）
        serial_actors = tuple(
            actor_id
            for actor_id in sorted(active)
            if actor_id not in self._batchable or self._room_of(state, actor_id) is None
        )
        if serial_actors:
            proposals.extend(
                self._collect_serial(
                    state=state,
                    events=events,
                    actor_ids=serial_actors,
                    turn_id=turn_id,
                    save_seed=save_seed,
                    active=active,
                    character_agents=character_agents,
                )
            )
        # 3) 未唤醒：零调用 + 只写 SKIPPED 审计（「无唤醒即无调用」）
        for actor_id in present:
            if actor_id not in active:
                self._record(
                    actor_id,
                    turn_id=turn_id,
                    mode=InvocationMode.SKIPPED,
                    cause=WakeCause.NONE,
                    detail="dormant",
                )
        return proposals

    # ------------------------------------------------------------------
    # 唤醒判定
    # ------------------------------------------------------------------
    @staticmethod
    def _room_of(state: GameState, actor_id: str) -> str | None:
        """actor 所在房间：Character 取 location，NPC 取 room_id；不存在返回 None。"""
        character = state.characters.get(actor_id)
        if character is not None:
            return character.location
        npc = state.npcs.get(actor_id)
        return npc.room_id if npc is not None else None

    @staticmethod
    def _player_targets(
        player_action: ParseOutcome | None, present: Sequence[str]
    ) -> frozenset[str]:
        """玩家本回合动作直接指向的 NPC（``npc_id``/``actor_id``/``target`` 参数）。"""
        if player_action is None:
            return frozenset()
        args = player_action.args
        targeted = {
            str(args[key])
            for key in ("npc_id", "actor_id", "target")
            if isinstance(args.get(key), str)
        }
        return frozenset(targeted & set(present))

    @staticmethod
    def _player_entered_room(events: Sequence[Event], *, turn_id: int) -> str | None:
        """玩家**本回合**最近一次 ``CHARACTER_MOVED`` 的目标房间（R-08）。

        只扫描 ``event.turn_id == turn_id`` 的事件：旧实现扫全部历史，玩家曾经进过一次
        某房间后该房间 NPC 会每回合被 ``SCENE_ENTRY`` 永久唤醒，ACTIVE 窗口永不结束。
        """
        for event in reversed(events):
            if event.turn_id != turn_id:
                continue
            if event.event_type is not EventType.CHARACTER_MOVED:
                continue
            if event.actor_id != PLAYER_ID:
                continue
            to_room = event.result.get("to") or event.params.get("to_room")
            return to_room if isinstance(to_room, str) else None
        return None

    def _detect_wake(
        self,
        actor_id: str,
        *,
        npc_room: str | None,
        entered_room: str | None,
        targets: frozenset[str],
        turn_id: int,
    ) -> WakeCause | None:
        """三种唤醒（玩家触发 / 进入同场景 / 计划触发点到达）；同房间但不互动不唤醒。"""
        if actor_id in targets:
            return WakeCause.PLAYER_TRIGGER
        if entered_room is not None and npc_room is not None and entered_room == npc_room:
            return WakeCause.SCENE_ENTRY
        if turn_id in self._plan_trigger_turns.get(actor_id, ()):
            return WakeCause.PLAN_TRIGGER
        return None

    # ------------------------------------------------------------------
    # 执行：批量 / 串行 / 降级
    # ------------------------------------------------------------------
    def _collect_batch(
        self,
        *,
        state: GameState,
        events: Sequence[Event],
        actor_ids: Sequence[str],
        turn_id: int,
        save_seed: str,
        active: Mapping[str, WakeCause],
        character_agents: Mapping[str, CharacterAgentLike],
    ) -> list[Proposal]:
        """一次批量调用；整批异常 → 逐 NPC 串行降级，单 NPC 失败只丢该 NPC。"""
        room_id = self._room_of(state, actor_ids[0])
        assert room_id is not None  # 调用方已保证批次 actor 均有房间
        if self._batch_agent is None:
            return self._collect_serial(
                state=state,
                events=events,
                actor_ids=actor_ids,
                turn_id=turn_id,
                save_seed=save_seed,
                active=active,
                character_agents=character_agents,
                fallback_detail="batch_agent_unavailable",
            )
        public = build_public_perception(state, room_id, events)
        batch_size = len(actor_ids)
        try:
            replies = list(
                self._batch_agent.propose_batch(
                    public,
                    actor_ids,
                    turn_id=turn_id,
                    save_seed=save_seed,
                )
            )
        except Exception as exc:  # 整批失败：降级逐 NPC 串行，单点失败仍只丢该 NPC
            return self._collect_serial(
                state=state,
                events=events,
                actor_ids=actor_ids,
                turn_id=turn_id,
                save_seed=save_seed,
                active=active,
                character_agents=character_agents,
                fallback_detail=f"batch_error:{type(exc).__name__}",
            )
        by_actor = {reply.actor_id: reply for reply in replies}
        proposals: list[Proposal] = []
        for actor_id in actor_ids:
            reply = by_actor.get(actor_id)
            if reply is None or reply.error is not None:
                detail = reply.error if reply is not None else "missing_batch_reply"
                self._record(
                    actor_id,
                    turn_id=turn_id,
                    mode=InvocationMode.DROPPED,
                    cause=active.get(actor_id, WakeCause.ACTIVE_WINDOW),
                    batch_size=batch_size,
                    detail=str(detail)[:200],
                )
                continue
            accepted = [
                proposal
                for proposal in reply.proposals
                if proposal.actor_id == actor_id and proposal.turn_id == turn_id
            ]
            proposals.extend(accepted)
            self._record(
                actor_id,
                turn_id=turn_id,
                mode=InvocationMode.BATCH,
                cause=active.get(actor_id, WakeCause.ACTIVE_WINDOW),
                batch_size=batch_size,
                detail=f"replies={len(accepted)}",
            )
        return proposals

    def _collect_serial(
        self,
        *,
        state: GameState,
        events: Sequence[Event],
        actor_ids: Sequence[str],
        turn_id: int,
        save_seed: str,
        active: Mapping[str, WakeCause],
        character_agents: Mapping[str, CharacterAgentLike],
        fallback_detail: str = "",
    ) -> list[Proposal]:
        """逐 NPC 调用各角色 Agent（私有感知切片）；单 NPC 失败只丢该 NPC。"""
        proposals: list[Proposal] = []
        for actor_id in actor_ids:
            agent = character_agents.get(actor_id)
            if agent is None:
                self._record(
                    actor_id,
                    turn_id=turn_id,
                    mode=InvocationMode.DROPPED,
                    cause=active.get(actor_id, WakeCause.ACTIVE_WINDOW),
                    detail="no_character_agent",
                )
                continue
            try:
                perception = self._query.build_perception_slice(
                    state,
                    actor_id,
                    turn_id=turn_id,
                    save_seed=save_seed,
                    events=events,
                )
                produced = [
                    proposal
                    for proposal in agent.propose(perception, turn_id=turn_id, save_seed=save_seed)
                    if proposal.turn_id == turn_id
                ]
            except Exception as exc:
                self._record(
                    actor_id,
                    turn_id=turn_id,
                    mode=InvocationMode.DROPPED,
                    cause=active.get(actor_id, WakeCause.ACTIVE_WINDOW),
                    detail=f"serial_error:{type(exc).__name__}",
                )
                continue
            proposals.extend(produced)
            self._record(
                actor_id,
                turn_id=turn_id,
                mode=InvocationMode.SERIAL,
                cause=active.get(actor_id, WakeCause.ACTIVE_WINDOW),
                detail=fallback_detail or f"proposals={len(produced)}",
            )
        return proposals

    def _record(
        self,
        actor_id: str,
        *,
        turn_id: int,
        mode: InvocationMode,
        cause: WakeCause,
        batch_size: int = 1,
        detail: str = "",
    ) -> None:
        """写一条调度审计（端口实现负责日志；本层不落库）。"""
        try:
            self._log.record(
                InvocationRecord(
                    turn_id=turn_id,
                    actor_id=actor_id,
                    mode=mode,
                    cause=cause,
                    batch_size=batch_size,
                    detail=detail,
                )
            )
        except Exception:  # 审计失败不得阻塞回合（第 11 章 §11.4 同口径）
            return
