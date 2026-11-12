"""R7-T3 编排层集成验收：分工具置信阈值 / 实体名→ID / 确认式澄清 / 多步原子执行。

契约：``.work/r7/contracts.md §6``（D-R7-3/D-R7-4/D-R7-5）。全部用例零网络：
解析器用测试 stub 直接注入 ``ParseOutcome``（含 ``steps``/``candidate_*``），叙事用
``TemplateNarrator``，裁判用 ``DeterministicJudgeAgent(None)``；不改 golden。

覆盖：
1. 多步 2 步成功（多角色链路）：两条事件按步骤顺序提交、version+1；
2. 多步第 2 步失败：整回合零提交、version/事件数/state_hash 不变、hint「第 2 步没能完成」；
3. 低置信确认式澄清：``text`` 逐字不变、补 ``effects.hint`` + 可点击 ``suggested_actions``、零写入；
4. 分工具阈值：``talk_to`` conf=0.55 通过（旧 0.6 单阈值会澄清）；
5. 实体名→ID：``item_id="火把"`` 经 ``normalize_parse_outcome`` 映射后成功取物；
6. 只读回合 H4-L3 口径保持（零事件、``narrative_source=None``、零写入）；
7. 单步旧行为回归（1 事件、文案 80–150、模板来源）；
8. 单角色链路多步成功（顺序 dispatch + 一次提交）；
9. 多步含只读步骤的防御性拒绝（整回合零提交）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest

from textgame.agents.dto import ParseOutcome, ParseStep, confidence_floor_for
from textgame.agents.judge_agent import DeterministicJudgeAgent
from textgame.agents.parser_agent import IntentParseAgent
from textgame.domain.events import EventType
from textgame.domain.hashing import state_hash
from textgame.domain.models import ActionIntent, PerceptionSlice
from textgame.domain.results import ErrorCode
from textgame.domain.rules.engine import RuleEngine
from textgame.domain.scenario import (
    ITEM_POTION,
    ITEM_SWORD,
    ITEM_TORCH,
    M3_NONPLAYER_TAGS,
    NPC_SENTINEL,
    PLAYER_ID,
)
from textgame.llm.client import DisabledLLMClient
from textgame.orchestration.dto import TurnRequest
from textgame.orchestration.intent import DeterministicIntentParser
from textgame.orchestration.narrator import TemplateNarrator
from textgame.orchestration.resolver import Resolver, ToolGateRouter
from textgame.orchestration.turn_orchestrator import (
    _UNKNOWN_ACTION_TEXT,
    CreateGameRequest,
    TurnOrchestrator,
)
from textgame.persistence.memory import InMemoryRepository
from textgame.services.action_service import ActionService
from textgame.services.actor_directory import ActorDirectory
from textgame.services.audit_service import AuditService
from textgame.services.dto import TurnResult
from textgame.services.game_engine import GameEngine
from textgame.services.query_service import QueryService
from textgame.services.tool_context import ToolContext
from textgame.tools.builtin.crafting import build_game_tool_registry
from textgame.tools.gate import ToolGate

pytestmark = pytest.mark.integration

_SAVE_SEED = "save-42"


class _StubParser:
    """测试解析器：忽略输入，固定返回预先构造的 ``ParseOutcome``（零 LLM）。"""

    def __init__(self, outcome: ParseOutcome) -> None:
        self.outcome = outcome
        self.calls: list[str] = []

    def parse(self, text: str, *, perception: PerceptionSlice, ctx: ToolContext) -> ParseOutcome:
        self.calls.append(text)
        return self.outcome


def _outcome(
    tool: str | None,
    args: dict[str, object] | None = None,
    *,
    confidence: float = 0.9,
    steps: tuple[ParseStep, ...] = (),
    candidate_tool: str | None = None,
    candidate_args: dict[str, object] | None = None,
    source: str = "llm",
) -> ParseOutcome:
    """构造解析结果（``candidate_*`` 用于低置信澄清用例）。"""
    payload = dict(args or {})
    return ParseOutcome(
        intent=ActionIntent(action=tool or "unknown", params=payload, confidence=confidence),
        tool=tool,
        args=payload,
        confidence=confidence,
        steps=steps,
        candidate_tool=candidate_tool,
        candidate_args=dict(candidate_args or {}),
        source=source,  # type: ignore[arg-type]
    )


def _step(tool: str, args: dict[str, object]) -> ParseStep:
    """构造一个多步子动作。"""
    return ParseStep(tool=tool, args=dict(args))


@dataclass
class _Harness:
    """与 ``main.py`` 同构的编排 harness（内存仓储 + 可断言零写入）。"""

    orchestrator: TurnOrchestrator
    repo: InMemoryRepository
    audit: AuditService | None
    game_id: str
    parser: object

    def snapshot(self) -> tuple[int, int, str, int]:
        """(version, 事件数, state_hash, last_seq) 四元组，供零写入比较。"""
        record = self.repo.load(self.game_id)
        assert record is not None
        events = self.repo.read_events(self.game_id)
        return record.version, len(events), state_hash(record.state), record.last_seq

    def run(self, text: str = "测试输入") -> TurnResult:
        """推进一回合（版本取当前记录，幂等键唯一）。"""
        record = self.repo.load(self.game_id)
        assert record is not None
        return self.orchestrator.run(
            TurnRequest(
                game_id=self.game_id,
                text=text,
                expected_version=record.version,
                idempotency_key=f"r7-t3-{uuid.uuid4().hex}",
            )
        )

    def audit_rows(self, turn_id: int = 2) -> list[object]:
        """本回合审计行（多角色链路才有）。"""
        assert self.audit is not None
        return self.audit.query(self.game_id, turn_id=turn_id)


def _build_harness(
    outcome: ParseOutcome | None, *, multi_actor: bool = True, parser: object | None = None
) -> _Harness:
    """装配 harness：``multi_actor=True`` 走 M3 提案→裁决链路，否则走 M2 单角色链路。

    ``parser`` 显式提供时使用（如真实 ``DeterministicIntentParser`` 多步端到端用例）；
    否则用 ``outcome`` 构造固定返回的 stub 解析器。
    """
    repo = InMemoryRepository()
    engine = GameEngine(repo)
    query = QueryService(repo)
    action = ActionService(query)
    registry = build_game_tool_registry()
    gate = ToolGate(registry=registry, query=query, action=action)
    rules = RuleEngine(agent_assisted_tools=registry.agent_assisted_tools())
    if parser is not None:
        active_parser: object = parser
    else:
        assert outcome is not None
        active_parser = _StubParser(outcome)
    kwargs: dict[str, object] = {
        "repo": repo,
        "engine": engine,
        "query": query,
        "action": action,
        "parser": active_parser,
        "gate": gate,
        "narrator": TemplateNarrator(),
        "rules": rules,
        "save_seed_default": _SAVE_SEED,
    }
    audit: AuditService | None = None
    if multi_actor:
        actors = ActorDirectory(nonplayer_tags=M3_NONPLAYER_TAGS)
        resolver = Resolver(
            rules=rules, services=ToolGateRouter(gate), actors=actors, save_seed=_SAVE_SEED
        )
        audit = AuditService(repo)
        kwargs.update(
            {
                "registry": registry,
                "resolver": resolver,
                "actors": actors,
                "character_agents": {},
                "audit": audit,
                "judge": DeterministicJudgeAgent(None),
            }
        )
    orchestrator = TurnOrchestrator(**kwargs)  # type: ignore[arg-type]
    created = orchestrator.create_game(
        CreateGameRequest(seed=42, idempotency_key=f"r7-t3-game-{uuid.uuid4().hex}", scenario="m2")
    )
    return _Harness(
        orchestrator=orchestrator,
        repo=repo,
        audit=audit,
        game_id=created.game_id,
        parser=active_parser,
    )


def _event_types(result: TurnResult) -> list[EventType]:
    """提交事件的 EventType 序列（按提交顺序）。"""
    return [event.event_type for event in result.events if event.event_type is not None]


def _hint_text(result: TurnResult) -> str:
    """失败回合 effects.hint 拼接（玩家可读提示）。"""
    hints = [str(effect.get("hint", "")) for effect in result.effects]
    return " ".join(hints)


def _assert_zero_write(harness: _Harness, before: tuple[int, int, str, int]) -> None:
    """零写入断言：version/事件数/state_hash/last_seq 四元组不变。"""
    assert harness.snapshot() == before


# ---------------------------------------------------------------------------
# 1) 多步 2 步成功（多角色链路）：事件按步骤顺序、一次提交
# ---------------------------------------------------------------------------
def test_multi_step_two_writes_commit_in_order() -> None:
    """「拿起火把，然后向北走」→ ITEM_TAKEN、CHARACTER_MOVED 按序提交，version+1。"""
    steps = (
        _step("take_item", {"item_id": ITEM_TORCH}),
        _step("move_to", {"direction": "north"}),
    )
    harness = _build_harness(
        _outcome("take_item", {"item_id": ITEM_TORCH}, steps=steps, confidence=0.95)
    )
    before = harness.snapshot()

    result = harness.run("拿起火把，然后向北走")

    assert result.ok is True, result
    assert result.version == before[0] + 1
    assert _event_types(result) == [EventType.ITEM_TAKEN, EventType.CHARACTER_MOVED]
    assert isinstance(result.text, str) and 80 <= len(result.text) <= 150
    assert result.narrative_source == "template"
    after = harness.snapshot()
    assert after[0] == before[0] + 1 and after[1] == before[1] + 2
    assert after[2] != before[2]


# ---------------------------------------------------------------------------
# 2) 多步第 2 步失败：整回合零提交 + 失败 hint 带步骤序号
# ---------------------------------------------------------------------------
def test_multi_step_second_step_failure_zero_write() -> None:
    """第 2 步拿不在房间里的锈剑（在地窖）→ 整回合失败、零事件、版本不变。"""
    steps = (
        _step("take_item", {"item_id": ITEM_TORCH}),
        _step("take_item", {"item_id": ITEM_SWORD}),
    )
    harness = _build_harness(
        _outcome("take_item", {"item_id": ITEM_TORCH}, steps=steps, confidence=0.95)
    )
    before = harness.snapshot()

    result = harness.run("先拿火把，再拿锈剑")

    assert result.ok is False
    assert result.error_code in {"INVALID_INPUT", "NOT_FOUND"}
    assert result.events == []
    assert "第 2 步没能完成。" in _hint_text(result)
    _assert_zero_write(harness, before)


# ---------------------------------------------------------------------------
# 3) 低置信确认式澄清：text 逐字不变、补 hint/建议动作、零写入
# ---------------------------------------------------------------------------
def test_low_confidence_clarification_adds_suggestions_without_text_change() -> None:
    """candidate=take_item(火把) conf=0.0 → 澄清 hint + 「拿起火把」建议，text 不变。"""
    harness = _build_harness(
        _outcome(
            None,
            {},
            confidence=0.0,
            candidate_tool="take_item",
            candidate_args={"item_id": "火把"},
        )
    )
    before = harness.snapshot()

    result = harness.run("那支火把我想收起来")

    assert result.ok is False
    assert result.text == _UNKNOWN_ACTION_TEXT  # 既有失败文案逐字不变
    assert result.error_code == ErrorCode.INVALID_INPUT
    hints = _hint_text(result)
    assert "拿起火把" in hints
    assert "拿起火把" in (result.suggested_actions or [])
    assert 1 <= len(result.suggested_actions) <= 2
    _assert_zero_write(harness, before)


# ---------------------------------------------------------------------------
# 4) 分工具阈值：talk_to 0.55 通过（旧 0.6 单阈值会澄清）
# ---------------------------------------------------------------------------
def test_tool_specific_floor_accepts_talk_to_at_0_55() -> None:
    """``confidence_floor_for("talk_to")==0.5``：conf=0.55 的社交正常提交。"""
    assert confidence_floor_for("talk_to") == 0.5
    harness = _build_harness(
        _outcome(
            "talk_to",
            {"npc_id": NPC_SENTINEL, "topic": "这里发生过什么"},
            confidence=0.55,
        )
    )
    before = harness.snapshot()

    result = harness.run("和老哨兵聊聊这里发生过什么")

    assert result.ok is True, result
    assert _event_types(result) == [EventType.DIALOGUE_EXCHANGED]
    assert harness.snapshot()[0] == before[0] + 1


# ---------------------------------------------------------------------------
# 5) 实体名→ID：展示名「火把」经 normalize 映射为真实 item_id
# ---------------------------------------------------------------------------
def test_entity_name_maps_to_real_item_id() -> None:
    """``args.item_id="火把"``（名称非 ID）→ normalize_parse_outcome 映射后成功取物。"""
    harness = _build_harness(_outcome("take_item", {"item_id": "火把"}, confidence=0.95))
    before = harness.snapshot()

    result = harness.run("我捡起火把")

    assert result.ok is True, result
    assert _event_types(result) == [EventType.ITEM_TAKEN]
    assert result.events[0].params["item_id"] == ITEM_TORCH
    assert harness.snapshot()[0] == before[0] + 1


# ---------------------------------------------------------------------------
# 6) 只读回合 H4-L3 口径保持：零事件 + narrative_source=None + 零写入
# ---------------------------------------------------------------------------
def test_read_only_turn_keeps_null_narrative_source_and_zero_write() -> None:
    """R7 不改 P0-4：只读回合同样零事件、零写入、narrative_source 仍为 None（H4-L3）。"""
    harness = _build_harness(_outcome("get_room_view", {}, confidence=0.95))
    before = harness.snapshot()

    result = harness.run("环顾四周")

    assert result.ok is True
    assert result.events == []
    assert result.narrative_source is None
    assert isinstance(result.text, str) and 80 <= len(result.text) <= 150
    _assert_zero_write(harness, before)


# ---------------------------------------------------------------------------
# 7) 单步旧行为回归（多角色链路）：1 事件、模板来源、文案长度
# ---------------------------------------------------------------------------
def test_single_step_behavior_unchanged() -> None:
    """单步 take_item 与旧行为一致：1 条 ITEM_TAKEN、version+1、text 80–150。"""
    harness = _build_harness(
        _outcome("take_item", {"item_id": ITEM_POTION}, confidence=1.0, source="keyword")
    )
    before = harness.snapshot()

    result = harness.run("拿起治疗药水")

    assert result.ok is True
    assert _event_types(result) == [EventType.ITEM_TAKEN]
    assert result.narrative_source == "template"
    assert isinstance(result.text, str) and 80 <= len(result.text) <= 150
    assert harness.snapshot()[0] == before[0] + 1


# ---------------------------------------------------------------------------
# 8) 单角色链路（M2）多步：顺序 dispatch + 一次提交
# ---------------------------------------------------------------------------
def test_single_actor_multi_step_commits_in_order() -> None:
    """未注入 M3 依赖时同样支持多步：2 步按序提交、一次 CAS。"""
    steps = (
        _step("take_item", {"item_id": ITEM_TORCH}),
        _step("move_to", {"direction": "north"}),
    )
    harness = _build_harness(
        _outcome("take_item", {"item_id": ITEM_TORCH}, steps=steps, confidence=0.95),
        multi_actor=False,
    )
    before = harness.snapshot()

    result = harness.run("拿起火把，然后向北走")

    assert result.ok is True, result
    assert _event_types(result) == [EventType.ITEM_TAKEN, EventType.CHARACTER_MOVED]
    assert harness.snapshot()[1] == before[1] + 2


# ---------------------------------------------------------------------------
# 9) 多步含只读步骤：编排层防御性拒绝（整回合零提交）
# ---------------------------------------------------------------------------
def test_multi_step_with_read_only_step_is_rejected() -> None:
    """防御：steps 含只读工具（解析层本应丢弃）→ 整回合失败、零写入。"""
    steps = (
        _step("get_room_view", {}),
        _step("take_item", {"item_id": ITEM_TORCH}),
    )
    harness = _build_harness(_outcome("get_room_view", {}, steps=steps, confidence=0.95))
    before = harness.snapshot()

    result = harness.run("先看一眼，再拿起火把")

    assert result.ok is False
    assert result.events == []
    _assert_zero_write(harness, before)


# ---------------------------------------------------------------------------
# 11) 多步含未知工具：编排层防御性拒绝（整回合零提交）
# ---------------------------------------------------------------------------
def test_multi_step_with_unknown_tool_is_rejected() -> None:
    """防御：steps 含白名单外工具（解析层本应只保留写工具）→ 整回合失败、零写入。"""
    steps = (
        _step("take_item", {"item_id": ITEM_TORCH}),
        _step("summon_dragon", {}),
    )
    harness = _build_harness(
        _outcome("take_item", {"item_id": ITEM_TORCH}, steps=steps, confidence=0.95)
    )
    before = harness.snapshot()

    result = harness.run("拿起火把，然后召唤一条龙")

    assert result.ok is False
    assert result.events == []
    _assert_zero_write(harness, before)


# ---------------------------------------------------------------------------
# 10) 真实确定性解析器多步端到端（T2 解析层 → T3 编排层，零 LLM）
# ---------------------------------------------------------------------------
def test_deterministic_parser_multi_step_end_to_end() -> None:
    """「拿起火把，然后向北走」经真实 ``IntentParseAgent`` + 关键词兜底产出 steps → 提交 2 事件。"""
    registry = build_game_tool_registry()
    real_parser = IntentParseAgent(
        DisabledLLMClient(),
        DeterministicIntentParser(),
        tool_specs=registry.visible_tools(PLAYER_ID, frozenset({"player"})),
    )
    harness = _build_harness(None, parser=real_parser)
    before = harness.snapshot()

    result = harness.run("拿起火把，然后向北走")

    assert result.ok is True, result
    assert _event_types(result) == [EventType.ITEM_TAKEN, EventType.CHARACTER_MOVED]
    assert harness.snapshot()[1] == before[1] + 2
