"""M5-023 批量合并 + 休眠唤醒 + 调用审计的集成验收（第 12 章 §12.7、D-M5-2..；离线可回放）。

覆盖：未唤醒零调用（``SKIPPED`` 审计）；三种唤醒（玩家触发/进入同场景/计划触发点）与
ACTIVE 窗口（本回合 + 2 回合，含端点）到期回 DORMANT；批量仅针对同场景公共群演、每批 ≤4、
输入只含公共感知（``private_memory == []``）；私有记忆角色与批量互斥、走串行私有切片；
单 NPC 失败只丢该 NPC、整批异常降级逐 NPC 串行；不跨房间合并；审计字段与结构化日志；
公共事件口径拒绝未盖章事件；不新增数据库表/迁移（``InvocationLog`` 进程内端口）。
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Sequence
from datetime import datetime, timezone

import pytest

from textgame.agents.character_agent import DeterministicCharacterAgent
from textgame.agents.dto import ParseOutcome
from textgame.agents.parser_agent import IntentParseAgent
from textgame.domain.events import Event, EventType
from textgame.domain.models import Character, GameState, PerceptionSlice, Proposal
from textgame.domain.rules.engine import RuleEngine
from textgame.domain.scenario import (
    ITEM_TORCH,
    M3_NONPLAYER_TAGS,
    M3_NPC_PLANS,
    M3_NPC_SCAVENGER_A,
    M3_NPC_SCAVENGER_B,
    PLAYER_ID,
    ROOM_CELLAR,
    ROOM_ENTRY,
    build_initial_state,
)
from textgame.llm.client import DisabledLLMClient
from textgame.orchestration.batch_sleep import (
    BatchAgentLike,
    BatchCandidate,
    BatchReply,
    BatchSleepScheduler,
    DormancyScheduler,
    DormancyState,
    InMemoryInvocationLog,
    InvocationMode,
    WakeCause,
    build_public_perception,
    filter_public_events,
    plan_batches,
    plan_serial,
)
from textgame.orchestration.dto import TurnRequest
from textgame.orchestration.intent import DeterministicIntentParser
from textgame.orchestration.narrator import TemplateNarrator
from textgame.orchestration.resolver import Resolver, ToolGateRouter
from textgame.orchestration.turn_orchestrator import CreateGameRequest, TurnOrchestrator
from textgame.persistence.memory import InMemoryRepository
from textgame.services.action_service import ActionService
from textgame.services.actor_directory import ActorDirectory
from textgame.services.audit_service import AuditService
from textgame.services.dto import TurnResult
from textgame.services.game_engine import GameEngine
from textgame.services.query_service import QueryService
from textgame.tools.builtin.m2 import build_m2_tool_registry
from textgame.tools.gate import ToolGate

pytestmark = pytest.mark.integration

_CROWD_BASE = "char_01J9Z3K4Q7X8Y9Z0ABCDEFGH"
#: 5 个公共群演（入口房间可批量）+ 1 个带私有记忆角色（只能串行）。
_CROWD_IDS: tuple[str, ...] = tuple(f"{_CROWD_BASE}K{index}" for index in range(1, 6))
_PRIVATE_ID = f"{_CROWD_BASE}K9"
_ALL_IDS: tuple[str, ...] = (*_CROWD_IDS, _PRIVATE_ID)


def _character(actor_id: str, *, room: str, private_memory: str | None = None) -> Character:
    """构造合法最小 ``Character``（ULID 形状 id、必备属性与位置）。"""
    return Character(
        id=actor_id,
        name=actor_id[-6:],
        attributes={"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10, "max_hp": 20},
        inventory=[],
        location=room,
        hp=20,
        created_turn=1,
        private_memory_ref=private_memory,
    )


def _state(*, cellar_actors: Collection[str] = ()) -> GameState:
    """初始状态 + 5 个群演（可指定部分在地窖）+ 1 个私有记忆角色（入口房间）。"""
    base = build_initial_state(save_seed="save-42")
    characters = dict(base.characters)
    for actor_id in _CROWD_IDS:
        room = ROOM_CELLAR if actor_id in cellar_actors else ROOM_ENTRY
        characters[actor_id] = _character(actor_id, room=room)
    characters[_PRIVATE_ID] = _character(
        _PRIVATE_ID, room=ROOM_ENTRY, private_memory="npc_private_mem"
    )
    return base.model_copy(update={"characters": characters})


def _action(*, npc_id: str | None = None) -> ParseOutcome:
    """玩家本回合动作（``talk_to`` + 可选 ``npc_id``，用于「玩家触发」唤醒）。"""
    args = {} if npc_id is None else {"npc_id": npc_id}
    return ParseOutcome(tool="talk_to", args=args, confidence=0.9)


def _proposal(actor_id: str, turn_id: int) -> Proposal:
    """测试用合法 NPC 提案（take_item 工具名合法、确定性）。"""
    return Proposal(
        turn_id=turn_id,
        actor_id=actor_id,
        tool="take_item",
        args={"item_id": ITEM_TORCH},
        reason="fake_agent_proposal",
        priority=50,
    )


class _CountingAgent:
    """计数型角色 Agent 替身：每次 ``propose`` 记录调用与收到的感知切片。"""

    def __init__(self) -> None:
        """空计数。"""
        self.calls: list[tuple[str, int]] = []
        self.perceptions: list[PerceptionSlice] = []

    def propose(
        self, perception: PerceptionSlice, *, turn_id: int, save_seed: str
    ) -> list[Proposal]:
        """记录并返回一条确定性提案。"""
        _ = save_seed
        self.calls.append((perception.actor_id, turn_id))
        self.perceptions.append(perception)
        return [_proposal(perception.actor_id, turn_id)]


class _FakeBatchAgent:
    """批量 Agent 替身：可配置单 NPC 失败或整批抛异常，并记录收到的公共感知。"""

    def __init__(
        self,
        *,
        error_actors: Collection[str] = (),
        raises: bool = False,
    ) -> None:
        """``error_actors`` 中的 NPC 返回 ``BatchReply(error=...)``；``raises`` 模拟整批失败。"""
        self._error_actors = frozenset(error_actors)
        self._raises = raises
        self.calls: list[dict[str, object]] = []

    def propose_batch(
        self,
        public_perception: PerceptionSlice,
        actor_ids: Sequence[str],
        *,
        turn_id: int,
        save_seed: str,
    ) -> list[BatchReply]:
        """记录调用并返回逐 NPC 回复（或抛异常模拟整批失败）。"""
        self.calls.append(
            {
                "perception": public_perception,
                "actor_ids": tuple(actor_ids),
                "turn_id": turn_id,
                "save_seed": save_seed,
            }
        )
        if self._raises:
            raise RuntimeError("batch boom")
        replies: list[BatchReply] = []
        for actor_id in actor_ids:
            if actor_id in self._error_actors:
                replies.append(BatchReply(actor_id=actor_id, error="bad json for actor"))
            else:
                replies.append(
                    BatchReply(actor_id=actor_id, proposals=(_proposal(actor_id, turn_id),))
                )
        return replies


def _make_scheduler(
    *,
    batch_agent: BatchAgentLike | None = None,
    batchable: Collection[str] = (),
    plan_triggers: dict[str, Collection[int]] | None = None,
) -> tuple[BatchSleepScheduler, dict[str, _CountingAgent], InMemoryInvocationLog]:
    """装配调度器 + 每个 actor 一个计数 Agent + 进程内审计日志。"""
    log = InMemoryInvocationLog()
    scheduler = BatchSleepScheduler(
        query=QueryService(InMemoryRepository()),
        actors=ActorDirectory(),
        dormancy=DormancyScheduler(),
        invocation_log=log,
        batch_agent=batch_agent,
        batchable_actors=batchable,
        plan_trigger_turns=plan_triggers,
    )
    agents = {actor_id: _CountingAgent() for actor_id in _ALL_IDS}
    return scheduler, agents, log


def _collect(
    scheduler: BatchSleepScheduler,
    agents: dict[str, _CountingAgent],
    *,
    turn_id: int,
    player_action: ParseOutcome | None = None,
    events: Sequence[Event] = (),
) -> list[Proposal]:
    """调用调度器收集本回合 NPC 提案（固定 save_seed 与状态）。"""
    state = _state()
    return scheduler.collect(
        state,
        list(events),
        turn_id=turn_id,
        save_seed=state.rng_state.save_seed,
        player_action=player_action,
        character_agents=agents,
    )


def _moved_event(*, to_room: str, seq: int = 1) -> Event:
    """构造已盖章的玩家移动事件（``result.to`` 为目标房间，供「进入同场景」唤醒）。"""
    return Event(
        id=f"evt_{seq:06d}",
        seq=seq,
        turn_id=1,
        actor_id=PLAYER_ID,
        method="move_to",
        event_type=EventType.CHARACTER_MOVED,
        params={"direction": "north"},
        result={"to": to_room},
        rng_seed=7,
        ts=datetime(2026, 11, 10, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# 休眠：无唤醒零调用 / 三种唤醒 / 窗口
# ---------------------------------------------------------------------------
def test_dormant_actors_are_not_invoked_and_audited_skipped() -> None:
    """没有任何唤醒：零 Agent 调用，只写 SKIPPED 审计（「无唤醒即无调用」）。"""
    scheduler, agents, log = _make_scheduler()

    proposals = _collect(scheduler, agents, turn_id=2)

    assert proposals == []
    assert all(agent.calls == [] for agent in agents.values())
    assert log.count(mode=InvocationMode.SKIPPED) == len(_ALL_IDS)
    assert log.count(mode=InvocationMode.SERIAL) == 0
    assert all(entry.cause is WakeCause.NONE for entry in log.entries)


def test_player_trigger_wakes_for_three_turn_window() -> None:
    """玩家触发 → ACTIVE；本回合 + 2 回合内调用，第 3 个后续回合回 DORMANT 零调用。"""
    scheduler, agents, _log = _make_scheduler()

    # 仅唤醒回合有玩家指向动作；随后靠 ACTIVE 窗口延续（不重复触发）
    proposals = _collect(scheduler, agents, turn_id=2, player_action=_action(npc_id=_CROWD_IDS[0]))
    assert {proposal.actor_id for proposal in proposals} == {_CROWD_IDS[0]}
    for turn_id in (3, 4):
        proposals = _collect(scheduler, agents, turn_id=turn_id)
        assert {proposal.actor_id for proposal in proposals} == {_CROWD_IDS[0]}

    assert len(agents[_CROWD_IDS[0]].calls) == 3
    assert scheduler.dormancy.state_of(_CROWD_IDS[0]) is DormancyState.ACTIVE

    proposals = _collect(scheduler, agents, turn_id=5)
    assert proposals == []
    assert len(agents[_CROWD_IDS[0]].calls) == 3  # 窗口外零调用
    assert scheduler.dormancy.state_of(_CROWD_IDS[0]) is DormancyState.DORMANT


def test_scene_entry_wakes_actor_in_entered_room_only() -> None:
    """玩家进入某房间 → 只有该房间的 actor 被唤醒（其他房间保持 DORMANT）。"""
    scheduler, agents, log = _make_scheduler()

    proposals = _collect(scheduler, agents, turn_id=2, events=[_moved_event(to_room=ROOM_ENTRY)])

    woken = {proposal.actor_id for proposal in proposals}
    assert woken == set(_ALL_IDS)  # 入口房间 5 群演 + 私有角色
    assert scheduler.dormancy.last_wake_cause(_CROWD_IDS[0]) is WakeCause.SCENE_ENTRY
    assert log.count(mode=InvocationMode.DROPPED) == 0


def test_plan_trigger_wakes_and_window_continues() -> None:
    """计划触发点到达 → 唤醒；窗口延续回合内继续调用，窗口外回休眠。"""
    scheduler, agents, log = _make_scheduler(plan_triggers={_CROWD_IDS[0]: (3,)})

    assert _collect(scheduler, agents, turn_id=2) == []  # 触发点未到
    woken = _collect(scheduler, agents, turn_id=3)
    assert {proposal.actor_id for proposal in woken} == {_CROWD_IDS[0]}
    assert scheduler.dormancy.last_wake_cause(_CROWD_IDS[0]) is WakeCause.PLAN_TRIGGER

    assert _collect(scheduler, agents, turn_id=4)  # 窗口延续
    assert _collect(scheduler, agents, turn_id=5)  # 窗口延续
    assert _collect(scheduler, agents, turn_id=6) == []  # 窗口结束
    assert len(agents[_CROWD_IDS[0]].calls) == 3
    assert log.count(mode=InvocationMode.SKIPPED) > 0


def test_armed_state_requires_real_trigger() -> None:
    """ARMED 只是待唤醒：没有真实唤醒原因时仍零调用，触发点到达才 ACTIVE。"""
    scheduler, agents, log = _make_scheduler()
    scheduler.dormancy.arm(_CROWD_IDS[1])

    assert _collect(scheduler, agents, turn_id=2) == []
    assert scheduler.dormancy.state_of(_CROWD_IDS[1]) is DormancyState.ARMED
    assert agents[_CROWD_IDS[1]].calls == []
    assert any(
        entry.actor_id == _CROWD_IDS[1] and entry.mode is InvocationMode.SKIPPED
        for entry in log.entries
    )


def test_dormancy_window_is_inclusive_and_expires() -> None:
    """状态机口径：wake(t) 时 t..t+2 允许调用，t+3 返回 False 并回 DORMANT。"""
    dormancy = DormancyScheduler()
    dormancy.wake("actor_a", turn_id=2, cause=WakeCause.PLAYER_TRIGGER)

    assert [dormancy.should_invoke("actor_a", turn_id=t) for t in (2, 3, 4)] == [
        True,
        True,
        True,
    ]
    assert dormancy.should_invoke("actor_a", turn_id=5) is False
    assert dormancy.state_of("actor_a") is DormancyState.DORMANT


def test_dormancy_rejects_invalid_wake_arguments() -> None:
    """非法唤醒原因/回合号 → ``ValueError``（防止把「活跃延续」当作再次唤醒）。"""
    dormancy = DormancyScheduler()
    with pytest.raises(ValueError):
        dormancy.wake("actor_a", turn_id=2, cause=WakeCause.NONE)
    with pytest.raises(ValueError):
        dormancy.wake("actor_a", turn_id=2, cause=WakeCause.ACTIVE_WINDOW)
    with pytest.raises(ValueError):
        dormancy.wake("actor_a", turn_id=0, cause=WakeCause.PLAN_TRIGGER)


# ---------------------------------------------------------------------------
# 批量：分组 / 公共感知 / 私有互斥 / 降级
# ---------------------------------------------------------------------------
def _wake_all(scheduler: BatchSleepScheduler, *, turn_id: int = 2) -> None:
    """测试辅助：直接唤醒全部 actor（模拟计划触发点集中到达）。"""
    for actor_id in _ALL_IDS:
        scheduler.dormancy.wake(actor_id, turn_id=turn_id, cause=WakeCause.PLAN_TRIGGER)


def test_batches_group_public_crowd_by_room_with_max_four() -> None:
    """5 个同房间公共群演 → 2 批（4+1），批量 Agent 调用 2 次，串行 Agent 零调用。"""
    batch_agent = _FakeBatchAgent()
    scheduler, agents, log = _make_scheduler(batch_agent=batch_agent, batchable=_CROWD_IDS)
    _wake_all(scheduler)

    proposals = _collect(scheduler, agents, turn_id=2)

    assert {proposal.actor_id for proposal in proposals} == {*_CROWD_IDS, _PRIVATE_ID}
    assert len(batch_agent.calls) == 2
    assert sorted(len(call["actor_ids"]) for call in batch_agent.calls) == [1, 4]  # type: ignore[arg-type]
    assert agents[_PRIVATE_ID].calls == [(_PRIVATE_ID, 2)]  # 私有角色只走串行
    assert all(agents[actor_id].calls == [] for actor_id in _CROWD_IDS)  # 批量时不逐 NPC 调用
    assert log.count(mode=InvocationMode.BATCH) == len(_CROWD_IDS)


def test_batch_input_contains_public_perception_only() -> None:
    """批量输入 = 公共感知：``actor_id=="scene"``、``private_memory==[]``、无私有记忆引用。"""
    batch_agent = _FakeBatchAgent()
    scheduler, agents, _log = _make_scheduler(batch_agent=batch_agent, batchable=_CROWD_IDS)
    _wake_all(scheduler)

    _collect(scheduler, agents, turn_id=2)

    perception = batch_agent.calls[0]["perception"]
    assert isinstance(perception, PerceptionSlice)
    assert perception.actor_id == "scene"
    assert perception.private_memory == []
    assert perception.plan_hint is None
    visible_ids = {entity.id for entity in perception.visible_entities}
    assert ROOM_ENTRY in visible_ids
    assert "npc_private_mem" not in str(perception.model_dump())


def test_private_memory_actor_is_serial_and_never_batched() -> None:
    """私有记忆角色与批量互斥：批量 Agent 零调用，该角色收到自己的私有切片。"""
    batch_agent = _FakeBatchAgent()
    scheduler, agents, log = _make_scheduler(batch_agent=batch_agent, batchable=_CROWD_IDS)
    _wake_all(scheduler)

    proposals = _collect(scheduler, agents, turn_id=2)

    assert batch_agent.calls  # 群演走批量
    private_calls = agents[_PRIVATE_ID].calls
    assert private_calls == [(_PRIVATE_ID, 2)]
    assert agents[_PRIVATE_ID].perceptions[0].private_memory == ["npc_private_mem"]
    assert any(
        entry.actor_id == _PRIVATE_ID and entry.mode is InvocationMode.SERIAL
        for entry in log.entries
    )
    # 私有记忆角色绝不出现在任何批量调用里
    assert all(_PRIVATE_ID not in call["actor_ids"] for call in batch_agent.calls)  # type: ignore[operator]
    assert {proposal.actor_id for proposal in proposals} == set(_ALL_IDS)


def test_single_npc_batch_error_only_drops_that_npc() -> None:
    """批量中单个 NPC 回复解析失败：其余 NPC 提案保留，失败者审计为 DROPPED。"""
    batch_agent = _FakeBatchAgent(error_actors={_CROWD_IDS[2]})
    scheduler, agents, log = _make_scheduler(batch_agent=batch_agent, batchable=_CROWD_IDS)
    _wake_all(scheduler)

    proposals = _collect(scheduler, agents, turn_id=2)

    produced = {proposal.actor_id for proposal in proposals}
    assert _CROWD_IDS[2] not in produced
    assert produced == {*_CROWD_IDS[:2], *_CROWD_IDS[3:], _PRIVATE_ID}
    assert len(batch_agent.calls) == 2  # 5 个群演按 4+1 两批，各只调用一次
    assert log.count(mode=InvocationMode.DROPPED) == 1


def test_whole_batch_failure_degrades_to_individual_serial_calls() -> None:
    """整批调用异常 → 降级逐 NPC 串行；每个群演仍各得一条提案且无 DROPPED。"""
    batch_agent = _FakeBatchAgent(raises=True)
    scheduler, agents, log = _make_scheduler(batch_agent=batch_agent, batchable=_CROWD_IDS)
    _wake_all(scheduler)

    proposals = _collect(scheduler, agents, turn_id=2)

    assert len(batch_agent.calls) == 2  # 两次批量尝试（4+1）都失败后降级
    assert {proposal.actor_id for proposal in proposals} == set(_ALL_IDS)
    for actor_id in _CROWD_IDS:
        assert agents[actor_id].calls == [(actor_id, 2)]
    assert log.count(mode=InvocationMode.DROPPED) == 0
    assert any("batch_error" in entry.detail for entry in log.entries)


def test_batches_do_not_cross_room_boundaries() -> None:
    """不跨房间合并：入口 3 群演 + 地窖 2 群演 → 两次批量，各自只含本房间 actor。"""
    batch_agent = _FakeBatchAgent()
    scheduler, agents, _log = _make_scheduler(batch_agent=batch_agent, batchable=_CROWD_IDS)
    _wake_all(scheduler)
    state = _state(cellar_actors={_CROWD_IDS[3], _CROWD_IDS[4]})

    scheduler.collect(
        state,
        [],
        turn_id=2,
        save_seed=state.rng_state.save_seed,
        player_action=None,
        character_agents=agents,
    )

    actor_sets = [set(call["actor_ids"]) for call in batch_agent.calls]  # type: ignore[arg-type]
    assert actor_sets == [
        {_CROWD_IDS[0], _CROWD_IDS[1], _CROWD_IDS[2]},
        {_CROWD_IDS[3], _CROWD_IDS[4]},
    ]
    rooms = [
        {entity.id for entity in call["perception"].visible_entities}  # type: ignore[attr-defined]
        for call in batch_agent.calls
    ]
    assert ROOM_ENTRY in rooms[0] and ROOM_CELLAR in rooms[1]


# ---------------------------------------------------------------------------
# 审计 / 规划工具 / 纯函数护栏
# ---------------------------------------------------------------------------
def test_invocation_log_records_structured_fields(caplog: pytest.LogCaptureFixture) -> None:
    """审计条目字段完整，且输出不含 Prompt/私有记忆的结构化日志 ``agent_invocation``。"""
    caplog.set_level(logging.INFO, logger="textgame.agents")
    scheduler, agents, log = _make_scheduler()

    _collect(scheduler, agents, turn_id=2, player_action=_action(npc_id=_CROWD_IDS[0]))

    entry = next(item for item in log.entries if item.mode is InvocationMode.SERIAL)
    assert entry.turn_id == 2
    assert entry.actor_id == _CROWD_IDS[0]
    assert entry.cause is WakeCause.PLAYER_TRIGGER
    assert entry.batch_size == 1
    assert any("agent_invocation" in record.message for record in caplog.records)


def test_plan_batches_and_serial_split_private_actors() -> None:
    """规划纯函数：可批量按房间切 ≤4、私有角色进 serial 列表；非法 batch size 报错。"""
    candidates = [
        BatchCandidate(actor_id="a", room_id="room_1", batchable=True),
        BatchCandidate(actor_id="b", room_id="room_1", batchable=True),
        BatchCandidate(actor_id="c", room_id="room_1", batchable=True),
        BatchCandidate(actor_id="d", room_id="room_2", batchable=True),
        BatchCandidate(actor_id="e", room_id="room_2", batchable=False),
    ]

    batches = plan_batches(candidates, max_batch_size=2)

    assert batches == (("a", "b"), ("c",), ("d",))
    assert plan_serial(candidates) == ("e",)
    with pytest.raises(ValueError):
        plan_batches(candidates, max_batch_size=0)


def test_filter_public_events_requires_stamped_events() -> None:
    """公共事件口径只接受已盖章事件：草稿事件 → ``ValueError``（防未提交事实外泄）。"""
    draft = Event(
        turn_id=2,
        actor_id=PLAYER_ID,
        method="move_to",
        event_type=EventType.CHARACTER_MOVED,
        params={"direction": "north"},
    )

    with pytest.raises(ValueError):
        filter_public_events([draft])


def test_build_public_perception_unknown_room_raises() -> None:
    """未知房间 → ``ValueError``（批量输入必须来自真实公共场景）。"""
    state = _state()
    with pytest.raises(ValueError):
        build_public_perception(state, "room_unknown", [])


def test_default_offline_agents_still_accept_private_slices() -> None:
    """存量确定性 Agent 可继续按原签名注入调度器（私有切片路径兼容）。"""
    scheduler, agents, _log = _make_scheduler()
    _wake_all(scheduler)

    proposals = _collect(scheduler, agents, turn_id=2)

    assert proposals
    for proposal in proposals:
        assert proposal.actor_id in _ALL_IDS  # type: ignore[operator]
    for agent in agents.values():
        assert hasattr(agent, "propose")


# ---------------------------------------------------------------------------
# 编排层接线（M5-023 授权接线：默认 None = 原逐 Agent 循环不变）
# ---------------------------------------------------------------------------
def _build_m3_orchestrator(
    *,
    plan_triggers: dict[str, Collection[int]] | None = None,
) -> tuple[TurnOrchestrator, InMemoryRepository, InMemoryInvocationLog]:
    """按 ``main.py`` 口径装配 M3 多角色编排器；``plan_triggers=None`` 时不注入 023 调度器。"""
    repo = InMemoryRepository()
    engine = GameEngine(repo)
    query = QueryService(repo)
    action = ActionService(query)
    registry = build_m2_tool_registry()
    gate = ToolGate(registry=registry, query=query, action=action)
    rules = RuleEngine()
    actors = ActorDirectory(nonplayer_tags=M3_NONPLAYER_TAGS)
    resolver = Resolver(
        rules=rules, services=ToolGateRouter(gate), actors=actors, save_seed="save-42"
    )
    deterministic = DeterministicCharacterAgent(M3_NPC_PLANS)
    character_agents = dict.fromkeys(M3_NPC_PLANS, deterministic)
    audit = AuditService(repo)
    parser = IntentParseAgent(
        DisabledLLMClient(),
        DeterministicIntentParser(),
        tool_specs=registry.visible_tools(PLAYER_ID, frozenset({"player"})),
    )
    log = InMemoryInvocationLog()
    scheduler = None
    if plan_triggers is not None:
        scheduler = BatchSleepScheduler(
            query=query,
            actors=actors,
            dormancy=DormancyScheduler(),
            invocation_log=log,
            batchable_actors=(),
            plan_trigger_turns=plan_triggers,
        )
    orchestrator = TurnOrchestrator(
        repo=repo,
        engine=engine,
        query=query,
        action=action,
        parser=parser,
        gate=gate,
        narrator=TemplateNarrator(),
        rules=rules,
        save_seed_default="save-42",
        registry=registry,
        resolver=resolver,
        actors=actors,
        character_agents=character_agents,
        audit=audit,
        agent_scheduler=scheduler,
    )
    return orchestrator, repo, log


def _play_m3_turn(orchestrator: TurnOrchestrator) -> TurnResult:
    """创建 scenario=m3 存档并执行第 2 回合（M3 计划表：两拾荒者都抢同一瓶药水）。"""
    created = orchestrator.create_game(
        CreateGameRequest(seed=42, idempotency_key="game-batch-sleep-wire-0001", scenario="m3")
    )
    return orchestrator.run(
        TurnRequest(
            game_id=created.game_id,
            text="环顾四周",
            expected_version=1,
            idempotency_key="turn-batch-sleep-wire-0001",
        )
    )


def test_orchestrator_wires_optional_agent_scheduler() -> None:
    """接线：注入调度器后由 ``collect`` 产出 NPC 提案并进入队列/裁决（计划触发唤醒）。"""
    orchestrator, _repo, log = _build_m3_orchestrator(
        plan_triggers={M3_NPC_SCAVENGER_A: (2,), M3_NPC_SCAVENGER_B: (2,)}
    )

    result = _play_m3_turn(orchestrator)

    assert result.ok is True
    assert result.version == 2
    scavenger_events = [
        event
        for event in result.events
        if event.actor_id in {M3_NPC_SCAVENGER_A, M3_NPC_SCAVENGER_B}
    ]
    assert len(scavenger_events) == 1  # 同物品冲突只应用一个
    assert scavenger_events[0].event_type is EventType.ITEM_TAKEN
    serial_entries = [entry for entry in log.entries if entry.mode is InvocationMode.SERIAL]
    assert len(serial_entries) == 2  # 两拾荒者都被计划触发点唤醒并串行调用
    assert all(entry.cause is WakeCause.PLAN_TRIGGER for entry in serial_entries)


def test_orchestrator_default_path_unchanged_without_scheduler() -> None:
    """默认 ``agent_scheduler=None``：原逐 Agent 循环不变，且无 023 审计副作用。"""
    orchestrator, _repo, log = _build_m3_orchestrator(plan_triggers=None)

    result = _play_m3_turn(orchestrator)

    assert result.ok is True
    assert result.version == 2
    scavenger_events = [
        event
        for event in result.events
        if event.actor_id in {M3_NPC_SCAVENGER_A, M3_NPC_SCAVENGER_B}
    ]
    assert len(scavenger_events) == 1
    assert scavenger_events[0].event_type is EventType.ITEM_TAKEN
    assert log.entries == []  # 未接线 → 零审计记录（默认路径零副作用）
