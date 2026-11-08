"""单元测试：M3-013 ``Resolver`` 确定性裁决 + ``DeterministicCharacterAgent``。

契约依据：``.work/m3-contract.md`` §3.3/§3.4/§5；第 11 章 §11.1–§11.4（三道门/排序与冲突/
CAS/状态词）；第 21 章 §21.2.2（Resolver 伪代码）；第 9 章 §9.2 步骤 6–9；第 8 章 §8.1/§8.2
（Agent 只提案、私有感知切片）。

覆盖：
1) ``Resolver`` 成功路径：``applied`` 裁决、草稿事件、``params`` 追加 ``proposal_id``/
   ``resolution_id``（``r_{turn_id:06d}_{序号:03d}``）、逐条 apply 后的 ``working_state``
   重校验（资源竞争/同 actor 连续动作/目标状态变化）、输入顺序即裁决顺序（不重排）、
   ``ServiceRouter`` 失败映射（``CONFLICT`` → ``conflict``，其余 → ``rejected``）、空输入、
   纯函数确定性（同输入两次一致、不改 ``snapshot``/提案、mock 的 rules/services 收到服务端
   推导的上下文与独立 args 副本）、``ToolGateRouter`` 适配器透传；
2) ``DeterministicCharacterAgent``：按计划表产提案（≤2、priority=50、turn_id 当前回合、
   只消费 ``PerceptionSlice``）、无计划/不在场 → ``[]``、确定性/防御性拷贝、非法 turn_id、
   与 ``ProposalQueue``/``Resolver`` 串起来的双拾荒者抢药水端到端（一个 applied 一个 rejected）。

安全专项（伪造身份/越权/混合来源/注入）见 ``tests/security/test_resolver_security.py``。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import pytest

from textgame.agents.character_agent import (
    NPC_PROPOSAL_PRIORITY,
    DeterministicCharacterAgent,
)
from textgame.domain.events import Event, EventType
from textgame.domain.hashing import canonical_json, state_hash
from textgame.domain.models import GameState, PerceptionSlice, Proposal
from textgame.domain.results import ErrorCode, Result
from textgame.domain.rules.engine import RuleEngine
from textgame.domain.scenario import (
    ITEM_POTION,
    ITEM_SWORD,
    M3_NONPLAYER_TAGS,
    M3_NPC_PLANS,
    M3_NPC_SCAVENGER_A,
    M3_NPC_SCAVENGER_B,
    PLAYER_ID,
    ROOM_CELLAR,
    ROOM_ENTRY,
    PlanStep,
    build_m3_state,
)
from textgame.orchestration.proposal_queue import ProposalQueue, ProposalSource, QueueEntry
from textgame.orchestration.resolver import Resolver, ToolGateRouter
from textgame.persistence.memory import InMemoryRepository
from textgame.services.action_service import ActionService
from textgame.services.actor_directory import ActorDirectory
from textgame.services.query_service import QueryService
from textgame.services.tool_context import ToolContext
from textgame.tools.builtin.m2 import build_m2_tool_registry
from textgame.tools.gate import ToolGate
from textgame.tools.registry import ToolRegistry

_SAVE_SEED = "save-42"
_TURN_ID = 2


# ---------------------------------------------------------------------------
# 测试替身：受控 rules / service 端口（不联网、不写库）
# ---------------------------------------------------------------------------
class _RecordingRules:
    """记录调用参数的规则端口；``handler`` 决定每次返回。"""

    def __init__(self, handler: Callable[..., Result] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._handler = handler or (
            lambda **_: Result.success(data={"allowed": True, "reason": ""})
        )

    def evaluate_preconditions(
        self,
        *,
        tool: str,
        args: dict[str, Any],
        actor_id: str,
        turn_id: int,
        save_seed: str,
        state: GameState,
    ) -> Result:
        self.calls.append(
            {
                "tool": tool,
                "args": args,
                "actor_id": actor_id,
                "turn_id": turn_id,
                "save_seed": save_seed,
                "state": state,
            }
        )
        return self._handler(
            tool=tool,
            args=args,
            actor_id=actor_id,
            turn_id=turn_id,
            save_seed=save_seed,
            state=state,
        )


class _RecordingRouter:
    """记录 ``call`` 参数的 ServiceRouter 端口；可注入 delegate 或按脚本返回结果。"""

    def __init__(
        self,
        delegate: Any | None = None,
        results: Sequence[Result] | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._delegate = delegate
        self._results = list(results or [])

    def call(
        self,
        *,
        tool: str,
        ctx: ToolContext,
        args: dict[str, Any],
        state: GameState,
    ) -> Result:
        self.calls.append({"tool": tool, "ctx": ctx, "args": args, "state": state})
        if self._results:
            return self._results.pop(0)
        if self._delegate is not None:
            return self._delegate.call(tool=tool, ctx=ctx, args=args, state=state)
        return Result.success(events=[])


# ---------------------------------------------------------------------------
# fixtures 与构造辅助
# ---------------------------------------------------------------------------
@pytest.fixture
def state() -> GameState:
    """M3 多角色初始状态（M2 场景 + 两名拾荒者，turn=1）。"""
    return build_m3_state(save_seed=_SAVE_SEED)


@pytest.fixture
def query() -> QueryService:
    """``build_perception_slice`` 只读状态；仓储仅为满足构造注入。"""
    return QueryService(InMemoryRepository())


@pytest.fixture
def registry() -> ToolRegistry:
    """M2 六工具声明（M3 扩展的 ``take_item``/``move_to`` 可见性已在其中）。"""
    return build_m2_tool_registry()


@pytest.fixture
def gate(registry: ToolRegistry, query: QueryService) -> ToolGate:
    """真实工具门：白名单/actor_scope/Schema → ActionService 草稿事件。"""
    return ToolGate(registry=registry, query=query, action=ActionService(query))


@pytest.fixture
def actors() -> ActorDirectory:
    """服务端 actor 目录：两名拾荒者标签 ``{"npc", "npc:scavenger"}``。"""
    return ActorDirectory(nonplayer_tags=M3_NONPLAYER_TAGS)


@pytest.fixture
def resolver(actors: ActorDirectory, gate: ToolGate) -> Resolver:
    """生产同构 Resolver：真实 RuleEngine + ToolGateRouter 适配的 ToolGate。"""
    return Resolver(
        rules=RuleEngine(),
        services=ToolGateRouter(gate),
        actors=actors,
        save_seed=_SAVE_SEED,
    )


def _proposal(
    *,
    actor_id: str,
    tool: str,
    args: dict[str, Any],
    turn_id: int = _TURN_ID,
    priority: int = 50,
    reason: str = "plan_step",
) -> Proposal:
    """构造合法提案。"""
    return Proposal(
        turn_id=turn_id,
        actor_id=actor_id,
        tool=tool,
        args=args,
        reason=reason,
        priority=priority,
    )


def _entry(
    proposal: Proposal,
    *,
    enqueue_seq: int = 1,
    source: ProposalSource = ProposalSource.NPC,
) -> QueueEntry:
    """直接构造队列条目（绕过队列，用于精确控制输入顺序）。"""
    return QueueEntry(
        proposal_id=f"p_{proposal.turn_id:06d}_{enqueue_seq:03d}",
        enqueue_seq=enqueue_seq,
        source=source,
        proposal=proposal,
    )


def _drained(*items: tuple[Proposal, ProposalSource]) -> list[QueueEntry]:
    """按给定入队顺序构造队列并 drain（等价编排层步骤 5 的产出）。"""
    queue = ProposalQueue(turn_id=_TURN_ID)
    for proposal, source in items:
        queue.enqueue(proposal, source=source)
    return queue.drain()


def _perception(
    query: QueryService, state: GameState, actor_id: str, *, turn_id: int = _TURN_ID
) -> PerceptionSlice:
    """构造某角色的私有感知切片（第 8 章 §8.2，只含该角色视角数据）。"""
    return query.build_perception_slice(state, actor_id, turn_id=turn_id, save_seed=_SAVE_SEED)


# ---------------------------------------------------------------------------
# Resolver：applied 路径（草稿事件 + 审计键 + working_state）
# ---------------------------------------------------------------------------
def test_applied_resolution_returns_draft_event_with_audit_keys(
    state: GameState, resolver: Resolver
) -> None:
    """成功裁决 → ``applied``；事件为草稿且 ``params`` 追加 proposal_id/resolution_id。"""
    entry = _entry(
        _proposal(actor_id=M3_NPC_SCAVENGER_A, tool="take_item", args={"item_id": ITEM_POTION})
    )
    resolutions = resolver.resolve([entry], state)
    assert len(resolutions) == 1
    resolution = resolutions[0]
    assert resolution.proposal_id == entry.proposal_id
    assert resolution.verdict == "applied"
    assert len(resolution.applied_events) == 1
    event = resolution.applied_events[0]
    assert event.is_stamped is False
    assert (event.id, event.seq, event.rng_seed, event.ts) == (None, None, None, None)
    assert event.event_type is EventType.ITEM_TAKEN
    assert event.actor_id == M3_NPC_SCAVENGER_A
    assert event.turn_id == _TURN_ID
    assert event.params["item_id"] == ITEM_POTION
    assert event.params["from_room"] == ROOM_ENTRY
    assert event.params["proposal_id"] == entry.proposal_id
    assert event.params["resolution_id"] == "r_000002_001"


def test_resolution_ids_follow_position_in_input_batch(
    state: GameState, resolver: Resolver
) -> None:
    """``resolution_id = r_{turn_id:06d}_{batch 内序号:03d}``，序号为传入顺序 1..N。"""
    entries = _drained(
        (
            _proposal(actor_id=M3_NPC_SCAVENGER_A, tool="move_to", args={"direction": "north"}),
            ProposalSource.NPC,
        ),
        (
            _proposal(actor_id=M3_NPC_SCAVENGER_B, tool="move_to", args={"direction": "north"}),
            ProposalSource.NPC,
        ),
    )
    resolutions = resolver.resolve(entries, state)
    assert [resolution.verdict for resolution in resolutions] == ["applied", "applied"]
    assert [resolution.applied_events[0].params["resolution_id"] for resolution in resolutions] == [
        "r_000002_001",
        "r_000002_002",
    ]
    assert [resolution.applied_events[0].params["proposal_id"] for resolution in resolutions] == [
        entry.proposal_id for entry in entries
    ]


def test_working_state_resource_competition_second_proposal_rejected(
    state: GameState, resolver: Resolver
) -> None:
    """两名拾荒者同回合抢同一瓶药水：先到 applied，后到 precondition 失败 → rejected。"""
    entries = _drained(
        (
            _proposal(actor_id=M3_NPC_SCAVENGER_A, tool="take_item", args={"item_id": ITEM_POTION}),
            ProposalSource.NPC,
        ),
        (
            _proposal(actor_id=M3_NPC_SCAVENGER_B, tool="take_item", args={"item_id": ITEM_POTION}),
            ProposalSource.NPC,
        ),
    )
    resolutions = resolver.resolve(entries, state)
    assert [resolution.verdict for resolution in resolutions] == ["applied", "rejected"]
    assert resolutions[1].applied_events == []
    # 理由来自 precondition（第 11 章 §11.3 规则 2：含 item ... is not in ...）
    assert ITEM_POTION in resolutions[1].reason
    assert "is not in" in resolutions[1].reason


def test_working_state_same_actor_sequential_actions_revalidate(
    state: GameState, resolver: Resolver
) -> None:
    """同 actor 连续裁决：先 ``move_to`` 到地窖，``take_item`` 锈剑基于新状态重新校验成功。"""
    entries = _drained(
        (
            _proposal(actor_id=M3_NPC_SCAVENGER_A, tool="move_to", args={"direction": "north"}),
            ProposalSource.NPC,
        ),
        (
            _proposal(actor_id=M3_NPC_SCAVENGER_A, tool="take_item", args={"item_id": ITEM_SWORD}),
            ProposalSource.NPC,
        ),
    )
    resolutions = resolver.resolve(entries, state)
    assert [resolution.verdict for resolution in resolutions] == ["applied", "applied"]
    assert resolutions[1].applied_events[0].event_type is EventType.ITEM_TAKEN
    assert resolutions[1].applied_events[0].params["item_id"] == ITEM_SWORD


def test_without_prior_move_take_sword_is_rejected(state: GameState, resolver: Resolver) -> None:
    """反证：没有前序 move 时，入口的拾荒者拿地窖锈剑 → precondition 失败 rejected。"""
    entries = _drained(
        (
            _proposal(actor_id=M3_NPC_SCAVENGER_A, tool="take_item", args={"item_id": ITEM_SWORD}),
            ProposalSource.NPC,
        )
    )
    resolutions = resolver.resolve(entries, state)
    assert resolutions[0].verdict == "rejected"
    assert "is not in" in resolutions[0].reason


def test_rejected_then_later_entry_still_sees_unchanged_state(
    state: GameState, resolver: Resolver
) -> None:
    """被拒提案不改变 ``working_state``：随后同物品提案仍按原状态判定（仍 rejected）。"""
    entries = _drained(
        (
            _proposal(actor_id=M3_NPC_SCAVENGER_A, tool="move_to", args={"direction": "east"}),
            ProposalSource.NPC,
        ),
        (
            _proposal(actor_id=M3_NPC_SCAVENGER_B, tool="move_to", args={"direction": "east"}),
            ProposalSource.NPC,
        ),
    )
    resolutions = resolver.resolve(entries, state)
    assert [resolution.verdict for resolution in resolutions] == ["rejected", "rejected"]
    assert all(resolution.applied_events == [] for resolution in resolutions)


def test_input_order_is_adjudication_order_even_with_priority_mismatch(
    state: GameState, resolver: Resolver
) -> None:
    """Resolver 不重排：按传入顺序裁决（排序是 ProposalQueue 的职责，第 21 章 §21.2.2）。"""
    low_priority = _entry(
        _proposal(
            actor_id=M3_NPC_SCAVENGER_B,
            tool="take_item",
            args={"item_id": ITEM_POTION},
            priority=10,
        ),
        enqueue_seq=1,
    )
    high_priority = _entry(
        _proposal(
            actor_id=M3_NPC_SCAVENGER_A,
            tool="take_item",
            args={"item_id": ITEM_POTION},
            priority=100,
        ),
        enqueue_seq=2,
    )
    resolutions = resolver.resolve([low_priority, high_priority], state)
    # 传入顺序即裁决顺序：低优先级（先传入）抢到，高优先级后到被拒。
    assert [resolution.proposal_id for resolution in resolutions] == [
        low_priority.proposal_id,
        high_priority.proposal_id,
    ]
    assert [resolution.verdict for resolution in resolutions] == ["applied", "rejected"]
    assert resolutions[0].applied_events[0].actor_id == M3_NPC_SCAVENGER_B


def test_resolution_list_is_parallel_to_entries_on_rejections(
    state: GameState, resolver: Resolver
) -> None:
    """每条 entry 恰有一条同序 ``Resolution``（审计逐提案一行，第 11 章 §11.4）。"""
    entries = _drained(
        (
            _proposal(actor_id=M3_NPC_SCAVENGER_A, tool="move_to", args={"direction": "east"}),
            ProposalSource.PLAYER,
        ),
        (
            _proposal(actor_id=M3_NPC_SCAVENGER_B, tool="take_item", args={"item_id": ITEM_POTION}),
            ProposalSource.NPC,
        ),
        (
            _proposal(actor_id=M3_NPC_SCAVENGER_A, tool="take_item", args={"item_id": ITEM_SWORD}),
            ProposalSource.NPC,
        ),
    )
    resolutions = resolver.resolve(entries, state)
    assert [resolution.proposal_id for resolution in resolutions] == [
        entry.proposal_id for entry in entries
    ]
    assert [resolution.verdict for resolution in resolutions] == ["rejected", "applied", "rejected"]


# ---------------------------------------------------------------------------
# Resolver：ServiceRouter 失败映射与状态词
# ---------------------------------------------------------------------------
def test_service_conflict_maps_to_conflict_verdict(
    state: GameState, actors: ActorDirectory
) -> None:
    """服务返回 ``CONFLICT`` → ``verdict="conflict"``（第 21 章 §21.2.2 步骤 3）。"""
    router = _RecordingRouter(results=[Result.fail(ErrorCode.CONFLICT, "version mismatch")])
    resolver = Resolver(rules=RuleEngine(), services=router, actors=actors, save_seed=_SAVE_SEED)
    entry = _entry(
        _proposal(actor_id=M3_NPC_SCAVENGER_A, tool="move_to", args={"direction": "north"})
    )
    resolutions = resolver.resolve([entry], state)
    assert resolutions[0].verdict == "conflict"
    assert resolutions[0].reason == "version mismatch"
    assert resolutions[0].applied_events == []


@pytest.mark.parametrize(
    "error_code",
    [ErrorCode.NOT_ALLOWED, ErrorCode.INVALID_INPUT, ErrorCode.NOT_FOUND, ErrorCode.INTERNAL],
)
def test_service_non_conflict_failures_map_to_rejected(
    state: GameState, actors: ActorDirectory, error_code: ErrorCode
) -> None:
    """服务返回非 ``CONFLICT`` 错误码 → ``verdict="rejected"`` 且零事件。"""
    router = _RecordingRouter(results=[Result.fail(error_code, "tool failed")])
    resolver = Resolver(rules=RuleEngine(), services=router, actors=actors, save_seed=_SAVE_SEED)
    entry = _entry(
        _proposal(actor_id=M3_NPC_SCAVENGER_A, tool="move_to", args={"direction": "north"})
    )
    resolutions = resolver.resolve([entry], state)
    assert resolutions[0].verdict == "rejected"
    assert resolutions[0].reason == "tool failed"
    assert resolutions[0].applied_events == []


def test_service_failure_without_message_gets_empty_reason(
    state: GameState, actors: ActorDirectory
) -> None:
    """``Result.fail`` 未带 message 时 reason 为空字符串（``Resolution.reason`` 允许空）。"""
    router = _RecordingRouter(results=[Result.fail(ErrorCode.INTERNAL)])
    resolver = Resolver(rules=RuleEngine(), services=router, actors=actors, save_seed=_SAVE_SEED)
    entry = _entry(
        _proposal(actor_id=M3_NPC_SCAVENGER_A, tool="move_to", args={"direction": "north"})
    )
    assert resolver.resolve([entry], state)[0].reason == ""


def test_precondition_failure_skips_service_call(state: GameState, actors: ActorDirectory) -> None:
    """前置条件失败 → 不调用服务端口（零副作用，第 11 章 §11.2“裁决门”）。"""
    router = _RecordingRouter()
    resolver = Resolver(rules=RuleEngine(), services=router, actors=actors, save_seed=_SAVE_SEED)
    entry = _entry(
        _proposal(actor_id=M3_NPC_SCAVENGER_A, tool="move_to", args={"direction": "east"})
    )
    resolutions = resolver.resolve([entry], state)
    assert resolutions[0].verdict == "rejected"
    assert router.calls == []


def test_rules_data_allowed_false_rejected_with_custom_reason(
    state: GameState, actors: ActorDirectory
) -> None:
    """规则端口返回 ``ok=True`` 但 ``data.allowed=False`` → rejected（第 21 章 §21.2.2）。"""
    rules = _RecordingRules(
        handler=lambda **_: Result.success(data={"allowed": False, "reason": "custom deny"})
    )
    resolver = Resolver(
        rules=rules, services=_RecordingRouter(), actors=actors, save_seed=_SAVE_SEED
    )
    entry = _entry(
        _proposal(actor_id=M3_NPC_SCAVENGER_A, tool="take_item", args={"item_id": ITEM_POTION})
    )
    resolutions = resolver.resolve([entry], state)
    assert resolutions[0].verdict == "rejected"
    assert resolutions[0].reason == "custom deny"


def test_rules_failure_without_message_uses_fallback_reason(
    state: GameState, actors: ActorDirectory
) -> None:
    """规则 ``Result.fail`` 未带 message → 使用固定兜底理由，不产生空 reason 崩溃。"""
    rules = _RecordingRules(handler=lambda **_: Result.fail(ErrorCode.NOT_ALLOWED))
    resolver = Resolver(
        rules=rules, services=_RecordingRouter(), actors=actors, save_seed=_SAVE_SEED
    )
    entry = _entry(
        _proposal(actor_id=M3_NPC_SCAVENGER_A, tool="take_item", args={"item_id": ITEM_POTION})
    )
    assert resolver.resolve([entry], state)[0].reason == "precondition rejected"


def test_applied_reason_comes_from_precondition_data(
    state: GameState, actors: ActorDirectory, gate: ToolGate
) -> None:
    """applied 的 ``reason`` 取前置条件 ``data.reason``（第 21 章 §21.2.2 步骤 4）。"""
    rules = _RecordingRules(
        handler=lambda **_: Result.success(data={"allowed": True, "reason": "rule passed"})
    )
    resolver = Resolver(
        rules=rules,
        services=ToolGateRouter(gate),
        actors=actors,
        save_seed=_SAVE_SEED,
    )
    entry = _entry(
        _proposal(actor_id=M3_NPC_SCAVENGER_A, tool="take_item", args={"item_id": ITEM_POTION})
    )
    assert resolver.resolve([entry], state)[0].reason == "rule passed"


def test_event_projection_failure_rejects_without_partial_state(
    state: GameState, actors: ActorDirectory
) -> None:
    """服务返回无法投影的事件 → rejected 且 ``working_state`` 不回退（后序提案仍可成功）。"""
    bad_event = Event(
        turn_id=_TURN_ID,
        actor_id=M3_NPC_SCAVENGER_A,
        method="take_item",
        event_type=None,
        params={"item_id": ITEM_POTION, "from_room": ROOM_ENTRY},
        result={"inventory_size": 1},
    )
    good_event = Event(
        turn_id=_TURN_ID,
        actor_id=M3_NPC_SCAVENGER_B,
        method="take_item",
        event_type=EventType.ITEM_TAKEN,
        params={"item_id": ITEM_POTION, "from_room": ROOM_ENTRY},
        result={"inventory_size": 1},
    )
    router = _RecordingRouter(
        results=[Result.success(events=[bad_event]), Result.success(events=[good_event])]
    )
    rules = _RecordingRules()
    resolver = Resolver(rules=rules, services=router, actors=actors, save_seed=_SAVE_SEED)
    entries = [
        _entry(
            _proposal(actor_id=M3_NPC_SCAVENGER_A, tool="take_item", args={"item_id": ITEM_POTION})
        ),
        _entry(
            _proposal(actor_id=M3_NPC_SCAVENGER_B, tool="take_item", args={"item_id": ITEM_POTION}),
            enqueue_seq=2,
        ),
    ]
    resolutions = resolver.resolve(entries, state)
    assert [resolution.verdict for resolution in resolutions] == ["rejected", "applied"]
    assert "event projection failed" in resolutions[0].reason
    assert resolutions[0].applied_events == []


def test_empty_entries_returns_empty_list(state: GameState, resolver: Resolver) -> None:
    """空批次 → ``[]``（不抛异常、不调用任何端口）。"""
    assert resolver.resolve([], state) == []
    assert resolver.resolve((), state, judgements=None) == []
    assert resolver.resolve((), state, judgements=()) == []


# ---------------------------------------------------------------------------
# Resolver：上下文、纯函数与确定性
# ---------------------------------------------------------------------------
def test_context_is_server_derived_and_passed_to_ports(
    state: GameState, actors: ActorDirectory
) -> None:
    """``ToolContext`` 六字段由服务端推导：actor_tags 来自 ``ActorDirectory.tags_of``。"""
    rules = _RecordingRules()
    router = _RecordingRouter()
    resolver = Resolver(rules=rules, services=router, actors=actors, save_seed=_SAVE_SEED)
    entry = _entry(
        _proposal(actor_id=M3_NPC_SCAVENGER_A, tool="take_item", args={"item_id": ITEM_POTION}),
        source=ProposalSource.NPC,
    )
    resolver.resolve([entry], state)
    assert len(rules.calls) == 1
    rules_call = rules.calls[0]
    assert rules_call["tool"] == "take_item"
    assert rules_call["actor_id"] == M3_NPC_SCAVENGER_A
    assert rules_call["turn_id"] == _TURN_ID
    assert rules_call["save_seed"] == _SAVE_SEED
    assert rules_call["state"] is state
    assert len(router.calls) == 1
    ctx: ToolContext = router.calls[0]["ctx"]
    assert ctx.actor_id == M3_NPC_SCAVENGER_A
    assert ctx.actor_tags == frozenset({"npc", "npc:scavenger"})
    assert ctx.turn_id == _TURN_ID
    assert ctx.save_seed == _SAVE_SEED
    assert ctx.idempotency_key == f"res:{_TURN_ID}:{entry.proposal_id}"
    assert ctx.caller == "resolver"
    assert router.calls[0]["state"] is state


def test_player_context_tags_come_from_actor_directory(
    state: GameState, actors: ActorDirectory
) -> None:
    """玩家提案的 actor_tags 为 ``{"player"}``（同一套服务端推导，不读取提案字段）。"""
    rules = _RecordingRules()
    router = _RecordingRouter()
    resolver = Resolver(rules=rules, services=router, actors=actors, save_seed=_SAVE_SEED)
    entry = _entry(
        _proposal(actor_id=PLAYER_ID, tool="move_to", args={"direction": "north"}, priority=100),
        source=ProposalSource.PLAYER,
    )
    resolver.resolve([entry], state)
    ctx: ToolContext = router.calls[0]["ctx"]
    assert ctx.actor_tags == frozenset({"player"})


def test_ports_receive_independent_args_copies(state: GameState, actors: ActorDirectory) -> None:
    """rules/services 收到 args 的独立副本：端口内改动不影响提案与另一端口。"""

    def _mutating_handler(**kwargs: Any) -> Result:
        kwargs["args"].pop("item_id", None)
        return Result.success(data={"allowed": True, "reason": ""})

    rules = _RecordingRules(handler=_mutating_handler)
    router = _RecordingRouter()
    resolver = Resolver(rules=rules, services=router, actors=actors, save_seed=_SAVE_SEED)
    entry = _entry(
        _proposal(actor_id=M3_NPC_SCAVENGER_A, tool="take_item", args={"item_id": ITEM_POTION})
    )
    resolver.resolve([entry], state)
    assert entry.proposal.args == {"item_id": ITEM_POTION}
    assert router.calls[0]["args"] == {"item_id": ITEM_POTION}


def test_resolver_does_not_mutate_snapshot_or_proposals(
    state: GameState, resolver: Resolver
) -> None:
    """纯函数：``snapshot`` 的 ``state_hash`` 不变、提案参数不变（第 11 章 §11.2）。"""
    before_hash = state_hash(state)
    entries = _drained(
        (
            _proposal(actor_id=M3_NPC_SCAVENGER_A, tool="move_to", args={"direction": "north"}),
            ProposalSource.NPC,
        ),
        (
            _proposal(actor_id=M3_NPC_SCAVENGER_B, tool="take_item", args={"item_id": ITEM_POTION}),
            ProposalSource.NPC,
        ),
    )
    snapshot_args = [dict(entry.proposal.args) for entry in entries]
    resolver.resolve(entries, state)
    assert state == build_m3_state(save_seed=_SAVE_SEED)
    assert state_hash(state) == before_hash
    assert [entry.proposal.args for entry in entries] == snapshot_args


def test_resolver_is_deterministic_for_same_input(state: GameState, resolver: Resolver) -> None:
    """同一输入两次裁决 → 逐字节相同的 ``Resolution`` 列表（可回放）。"""
    entries = _drained(
        (
            _proposal(actor_id=M3_NPC_SCAVENGER_A, tool="take_item", args={"item_id": ITEM_POTION}),
            ProposalSource.NPC,
        ),
        (
            _proposal(actor_id=M3_NPC_SCAVENGER_B, tool="take_item", args={"item_id": ITEM_POTION}),
            ProposalSource.NPC,
        ),
    )
    first = resolver.resolve(entries, state)
    second = resolver.resolve(entries, state)
    assert [resolution.model_dump(mode="json") for resolution in first] == [
        resolution.model_dump(mode="json") for resolution in second
    ]


def test_resolver_performs_no_io(
    state: GameState, resolver: Resolver, monkeypatch: pytest.MonkeyPatch
) -> None:
    """纯函数：文件/套接字访问被禁止时裁决仍成功（不写库、不读时钟、不联网）。"""

    def _forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("resolver must not perform IO")

    monkeypatch.setattr("builtins.open", _forbidden)
    monkeypatch.setattr("socket.socket", _forbidden)
    entry = _entry(
        _proposal(actor_id=M3_NPC_SCAVENGER_A, tool="take_item", args={"item_id": ITEM_POTION})
    )
    assert resolver.resolve([entry], state)[0].verdict == "applied"


def test_judgements_parameter_is_ignored(state: GameState, resolver: Resolver) -> None:
    """``judgements`` 仅为接口保留：M3 无 needs_judge，注入任何对象都不改变结果/不被访问。"""
    entries = _drained(
        (
            _proposal(actor_id=M3_NPC_SCAVENGER_A, tool="take_item", args={"item_id": ITEM_POTION}),
            ProposalSource.NPC,
        )
    )
    baseline = resolver.resolve(entries, state)

    class _ExplodingJudge:
        """任何属性访问都失败，证明确实没有被 Resolver 读取。"""

        def __getattr__(self, name: str) -> Any:
            raise AssertionError(f"judgements must be ignored in M3, accessed {name!r}")

    injected = resolver.resolve(entries, state, judgements=[_ExplodingJudge()])
    empty = resolver.resolve(entries, state, judgements=[])
    assert [resolution.model_dump(mode="json") for resolution in baseline] == [
        resolution.model_dump(mode="json") for resolution in injected
    ]
    assert [resolution.model_dump(mode="json") for resolution in baseline] == [
        resolution.model_dump(mode="json") for resolution in empty
    ]


def test_applied_resolution_never_carries_stamped_events(
    state: GameState, resolver: Resolver
) -> None:
    """所有 applied 裁决的事件都是草稿（盖章是 GameEngine 的职责，第 21 章 §21.2.1）。"""
    entries = _drained(
        (
            _proposal(actor_id=M3_NPC_SCAVENGER_A, tool="move_to", args={"direction": "north"}),
            ProposalSource.NPC,
        ),
        (
            _proposal(actor_id=M3_NPC_SCAVENGER_A, tool="take_item", args={"item_id": ITEM_SWORD}),
            ProposalSource.NPC,
        ),
    )
    resolutions = resolver.resolve(entries, state)
    applied_events = [event for resolution in resolutions for event in resolution.applied_events]
    assert applied_events
    assert all(not event.is_stamped for event in applied_events)


# ---------------------------------------------------------------------------
# Resolver：ToolGateRouter 适配器
# ---------------------------------------------------------------------------
def test_tool_gate_router_forwards_to_dispatch() -> None:
    """``ToolGateRouter.call`` 逐字透传到 ``dispatch``（契约 §3.3 的 ServiceRouter 适配）。"""

    class _SpyDispatcher:
        def __init__(self) -> None:
            self.kwargs: dict[str, Any] | None = None

        def dispatch(
            self,
            *,
            tool: str,
            args: dict[str, Any],
            ctx: ToolContext,
            state: GameState,
        ) -> Result:
            self.kwargs = {"tool": tool, "args": args, "ctx": ctx, "state": state}
            return Result.success(data={"ok": True})

    spy = _SpyDispatcher()
    ctx = ToolContext(
        actor_id=M3_NPC_SCAVENGER_A,
        actor_tags=frozenset({"npc"}),
        turn_id=_TURN_ID,
        save_seed=_SAVE_SEED,
        idempotency_key="res:2:p_000002_001",
        caller="resolver",
    )
    state = build_m3_state(save_seed=_SAVE_SEED)
    result = ToolGateRouter(spy).call(
        tool="take_item", ctx=ctx, args={"item_id": ITEM_POTION}, state=state
    )
    assert result.ok
    assert spy.kwargs == {
        "tool": "take_item",
        "args": {"item_id": ITEM_POTION},
        "ctx": ctx,
        "state": state,
    }


def test_tool_gate_router_works_with_real_gate(state: GameState, gate: ToolGate) -> None:
    """真实 ``ToolGate`` 经适配器可完成裁决（组合根接线路径，main.py 由 Lead 落地）。"""
    resolver = Resolver(
        rules=RuleEngine(),
        services=ToolGateRouter(gate),
        actors=ActorDirectory(nonplayer_tags=M3_NONPLAYER_TAGS),
        save_seed=_SAVE_SEED,
    )
    entry = _entry(
        _proposal(actor_id=M3_NPC_SCAVENGER_A, tool="move_to", args={"direction": "north"})
    )
    assert resolver.resolve([entry], state)[0].verdict == "applied"


# ---------------------------------------------------------------------------
# DeterministicCharacterAgent
# ---------------------------------------------------------------------------
def _agent(plans: dict[str, tuple[PlanStep, ...]]) -> DeterministicCharacterAgent:
    """构造确定性角色 Agent（计划表来自场景或测试自定义）。"""
    return DeterministicCharacterAgent(plans)


def test_character_agent_returns_plan_proposal_for_current_turn(
    state: GameState, query: QueryService
) -> None:
    """拾荒者甲在 turn 2 按 ``M3_NPC_PLANS`` 产出 take_item 药水提案（priority=50）。"""
    agent = _agent(dict(M3_NPC_PLANS))
    perception = _perception(query, state, M3_NPC_SCAVENGER_A)
    proposals = agent.propose(perception, turn_id=_TURN_ID, save_seed=_SAVE_SEED)
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.actor_id == M3_NPC_SCAVENGER_A
    assert proposal.turn_id == _TURN_ID
    assert proposal.tool == "take_item"
    assert proposal.args == {"item_id": ITEM_POTION}
    assert proposal.priority == NPC_PROPOSAL_PRIORITY == 50
    assert proposal.reason == M3_NPC_PLANS[M3_NPC_SCAVENGER_A][0].reason


def test_character_agent_without_plan_for_turn_returns_empty(
    state: GameState, query: QueryService
) -> None:
    """该回合无计划步骤 → ``[]``（不行动，第 9 章 §9.2 步骤 8 的确定性等价）。"""
    agent = _agent(dict(M3_NPC_PLANS))
    perception = _perception(query, state, M3_NPC_SCAVENGER_A, turn_id=3)
    assert agent.propose(perception, turn_id=3, save_seed=_SAVE_SEED) == []


def test_character_agent_unknown_actor_returns_empty(state: GameState, query: QueryService) -> None:
    """切片 actor 不在计划表（不在场/无计划）→ ``[]``。"""
    agent = _agent(dict(M3_NPC_PLANS))
    perception = _perception(query, state, PLAYER_ID)
    assert agent.propose(perception, turn_id=_TURN_ID, save_seed=_SAVE_SEED) == []


def test_character_agent_caps_proposals_at_two(state: GameState, query: QueryService) -> None:
    """单回合最多 2 条提案（第 8 章 §8.1 ``CharacterOutput.proposals`` ≤ 2）。"""
    plans = {
        M3_NPC_SCAVENGER_A: (
            PlanStep(2, "move_to", {"direction": "north"}, "一"),
            PlanStep(2, "take_item", {"item_id": ITEM_SWORD}, "二"),
            PlanStep(2, "move_to", {"direction": "south"}, "三"),
        )
    }
    agent = _agent(plans)
    perception = _perception(query, state, M3_NPC_SCAVENGER_A)
    proposals = agent.propose(perception, turn_id=_TURN_ID, save_seed=_SAVE_SEED)
    assert len(proposals) == 2
    assert [proposal.reason for proposal in proposals] == ["一", "二"]


def test_character_agent_keeps_plan_order_across_multiple_turns(
    state: GameState, query: QueryService
) -> None:
    """跨回合过滤：只取当前 ``turn_id`` 的步骤，保持计划表原始顺序。"""
    plans = {
        M3_NPC_SCAVENGER_B: (
            PlanStep(2, "take_item", {"item_id": ITEM_POTION}, "甲"),
            PlanStep(2, "move_to", {"direction": "north"}, "乙"),
            PlanStep(3, "move_to", {"direction": "south"}, "丙"),
            PlanStep(2, "talk_to", {"npc_id": "npc_x", "topic": "嗨"}, "丙二"),
        )
    }
    agent = _agent(plans)
    perception = _perception(query, state, M3_NPC_SCAVENGER_B)
    proposals = agent.propose(perception, turn_id=_TURN_ID, save_seed=_SAVE_SEED)
    assert [proposal.reason for proposal in proposals] == ["甲", "乙"]


def test_character_agent_returns_defensive_args_copy(state: GameState, query: QueryService) -> None:
    """提案 ``args`` 是计划表 dict 的拷贝：改提案参数不污染静态计划（回放稳定）。"""
    plan_args = {"item_id": ITEM_POTION}
    plans = {M3_NPC_SCAVENGER_A: (PlanStep(2, "take_item", plan_args, "拿药水"),)}
    agent = _agent(plans)
    perception = _perception(query, state, M3_NPC_SCAVENGER_A)
    proposals = agent.propose(perception, turn_id=_TURN_ID, save_seed=_SAVE_SEED)
    proposals[0].args["item_id"] = "tampered"
    assert plan_args == {"item_id": ITEM_POTION}
    assert agent.propose(perception, turn_id=_TURN_ID, save_seed=_SAVE_SEED)[0].args == {
        "item_id": ITEM_POTION
    }


def test_character_agent_is_deterministic(state: GameState, query: QueryService) -> None:
    """同一 ``PerceptionSlice``/``turn_id`` 两次 ``propose`` 结果一致（无随机/无时钟）。"""
    agent = _agent(dict(M3_NPC_PLANS))
    perception = _perception(query, state, M3_NPC_SCAVENGER_A)
    first = agent.propose(perception, turn_id=_TURN_ID, save_seed=_SAVE_SEED)
    second = agent.propose(perception, turn_id=_TURN_ID, save_seed=_SAVE_SEED)
    assert canonical_json(
        [proposal.model_dump(mode="json") for proposal in first]
    ) == canonical_json([proposal.model_dump(mode="json") for proposal in second])


def test_character_agent_plan_mapping_is_copied_defensively(
    state: GameState, query: QueryService
) -> None:
    """构造时拷贝计划表：外部后续修改 mapping 不影响 Agent 行为。"""
    plans: dict[str, tuple[PlanStep, ...]] = {
        M3_NPC_SCAVENGER_A: (PlanStep(2, "take_item", {"item_id": ITEM_POTION}, "原计划"),)
    }
    agent = _agent(plans)
    plans.clear()
    perception = _perception(query, state, M3_NPC_SCAVENGER_A)
    proposals = agent.propose(perception, turn_id=_TURN_ID, save_seed=_SAVE_SEED)
    assert [proposal.reason for proposal in proposals] == ["原计划"]


@pytest.mark.parametrize("turn_id", [0, -1])
def test_character_agent_rejects_non_positive_turn_id(
    state: GameState, query: QueryService, turn_id: int
) -> None:
    """非法 ``turn_id`` → ``ValueError``（避免产出非法 Proposal）。"""
    agent = _agent(dict(M3_NPC_PLANS))
    perception = _perception(query, state, M3_NPC_SCAVENGER_A)
    with pytest.raises(ValueError, match="turn_id"):
        agent.propose(perception, turn_id=turn_id, save_seed=_SAVE_SEED)


def test_character_agent_output_can_be_replayed_through_queue_and_resolver(
    state: GameState, query: QueryService, resolver: Resolver
) -> None:
    """M3 冲突演示端到端：两名拾荒者 turn 2 的提案入队 → 裁决 → 仅一个 applied。"""
    agents = {actor_id: _agent({actor_id: steps}) for actor_id, steps in M3_NPC_PLANS.items()}
    queue = ProposalQueue(turn_id=_TURN_ID)
    for actor_id in sorted(agents):
        perception = _perception(query, state, actor_id)
        for proposal in agents[actor_id].propose(
            perception, turn_id=_TURN_ID, save_seed=_SAVE_SEED
        ):
            queue.enqueue(proposal, source=ProposalSource.NPC)
    entries = queue.drain()
    assert len(entries) == 2
    resolutions = resolver.resolve(entries, state)
    assert [resolution.verdict for resolution in resolutions] == ["applied", "rejected"]
    applied = resolutions[0].applied_events[0]
    assert applied.actor_id == M3_NPC_SCAVENGER_A
    assert applied.params["item_id"] == ITEM_POTION
    assert ITEM_POTION in resolutions[1].reason
    # 冲突后药水只被拿走一次；地窖等其余状态不在本回合事件内。
    assert applied.params["from_room"] == ROOM_ENTRY
    assert ROOM_CELLAR in state.rooms
