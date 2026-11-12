"""R7-T4 裁判接线集成测试：``improvise``（首个 agent_assisted 工具）生产可达。

契约 ``.work/r7/contracts.md`` §7：无编排层改动（``improvise`` 声明 AGENT_ASSISTED 后，
``registry.agent_assisted_tools()={"improvise"}`` → 生产组合根的 ``RuleEngine`` 自动得到
``needs_judge`` 生产点，multi-actor 链路既有 ``_collect_judgements`` → Resolver needs_judge
分支即可用）。本文件只驱动「R7 生产同构」harness（``conftest.build_r7_prod_harness``），
零网络、零真实 LLM（``DeterministicJudgeAgent``/``DisabledLLMClient``/``FakeLLM``）。

覆盖：
1. 裁判缺失（``judge=None``）/ 离线（``LLMJudgeAgent(DisabledLLMClient())``）→ 确定性兜底
   拒绝、零事件、版本/哈希不变、审计 rejected/aborted；
2. allow（conf≥0.7、reason 非空、证据在感知切片）→ ``IMPROVISED_ACTION`` 提交、审计 applied；
3. deny / 低置信 → rejected 零写入；
4. 证据越界（LLM 裁判输出引用感知切片外实体）→ 视为无效证据 → 确定性兜底拒绝零写入；
5. 每回合裁判调用 ≤1（m3 同回合玩家 + 2 NPC 的 needs_judge 提案只调 1 次）；
6. 裁判请求载荷：``action_text`` 原样进入 proposal payload、无内部 ID、``judge@v1`` 不变。
"""

from __future__ import annotations

from typing import Any

import pytest

from conftest import build_r7_prod_harness
from fakes import FakeLLM
from textgame.agents.dto import ParseOutcome
from textgame.agents.judge_agent import DeterministicJudgeAgent, LLMJudgeAgent
from textgame.domain.judge import JUDGE_CONFIDENCE_FLOOR, JudgeOutput
from textgame.domain.models import ActionIntent, Proposal
from textgame.domain.scenario import M3_NPC_SCAVENGER_A, M3_NPC_SCAVENGER_B, PLAYER_ID
from textgame.llm.client import DisabledLLMClient
from textgame.services.dto import TurnResult

pytestmark = [pytest.mark.integration]

#: 玩家临场动作原文（1..200，无内部 ID）。
_ACTION_TEXT = "我捡起脚边的石子，在墙上刻下今天的日期"


# ---------------------------------------------------------------------------
# 测试装配：improvise 解析器 / NPC 提案 Agent / 裁判输出
# ---------------------------------------------------------------------------
class _ImproviseParser:
    """测试解析器：任意输入 → ``improvise(action_text=文本裁剪 200)``（模拟 LLM 解析产物）。"""

    def parse(
        self,
        text: str,
        *,
        perception: Any,
        ctx: Any = None,
    ) -> ParseOutcome:
        del perception, ctx
        args = {"action_text": text[:200]}
        return ParseOutcome(
            intent=ActionIntent(action="improvise", params=args, confidence=1.0),
            tool="improvise",
            args=args,
            confidence=1.0,
            source="keyword",
        )


class _NpcImproviseAgent:
    """测试 NPC Agent：每回合产出一条 ``improvise`` 提案（优先级低于玩家）。"""

    def __init__(self, action_text: str) -> None:
        self._action_text = action_text

    def propose(self, perception: Any, *, turn_id: int, save_seed: str) -> list[Proposal]:
        del save_seed
        return [
            Proposal(
                turn_id=turn_id,
                actor_id=perception.actor_id,
                tool="improvise",
                args={"action_text": self._action_text},
                reason="npc-test",
                priority=50,
            )
        ]


def _allow_output(**overrides: Any) -> JudgeOutput:
    """合法 allow 证据：证据引用 m2 入口房间可见的「火把」，置信度高于采纳门槛。"""
    payload: dict[str, Any] = {
        "verdict": "allow",
        "reason": "他手边就有火把，点亮灯芯合情合理。",
        "evidence_refs": ["火把"],
        "confidence": 0.9,
    }
    payload.update(overrides)
    return JudgeOutput.model_validate(payload)


def _harness(
    *,
    judge: Any = None,
    scenario: str = "m2",
    character_agents: dict[str, Any] | None = None,
) -> Any:
    """R7 生产同构 harness（生产 registry + agent_assisted 派生 + improvise 解析器）。"""
    return build_r7_prod_harness(
        judge=judge,
        character_agents=character_agents,
        scenario=scenario,
        parser=_ImproviseParser(),
    )


def _hint_text(result: TurnResult) -> str:
    """失败回合 effects.hint 拼接（玩家可读提示）。"""
    return " ".join(str(effect.get("hint", "")) for effect in result.effects)


def _assert_rejected_zero_write(harness: Any, result: TurnResult, before: tuple[Any, ...]) -> None:
    """通用失败契约：无事件、版本/哈希/事件数不变、审计 rejected/aborted。"""
    assert result.ok is False
    assert result.error_code is not None and result.error_code.value == "INVALID_INPUT"
    assert result.events == []
    assert harness.write_snapshot() == before
    rows = harness.audit_rows()
    assert rows and rows[0].verdict == "rejected" and rows[0].status == "aborted"


# ---------------------------------------------------------------------------
# 1) 裁判缺失 / 离线：确定性兜底拒绝
# ---------------------------------------------------------------------------
def test_judge_none_rejects_improvise_zero_write() -> None:
    """judge=None：improvise 走确定性兜底（无裁判证据）→ rejected、零事件、零写入。"""
    harness = _harness(judge=None)
    before = harness.write_snapshot()

    result = harness.play(_ACTION_TEXT)

    _assert_rejected_zero_write(harness, result, before)
    reason = harness.audit_rows()[0].verdict_reason
    assert "临场动作" in reason, "improvise 的确定性兜底文案应说明临场动作未被世界确认"


def test_judge_offline_disabled_client_rejects_improvise_zero_write() -> None:
    """离线装配（DisabledLLMClient）：裁判 evaluate 返回 None → 兜底拒绝、零写入。"""
    harness = _harness(judge=LLMJudgeAgent(DisabledLLMClient()))
    before = harness.write_snapshot()

    result = harness.play(_ACTION_TEXT)

    _assert_rejected_zero_write(harness, result, before)
    assert "临场动作" in harness.audit_rows()[0].verdict_reason


# ---------------------------------------------------------------------------
# 2) allow：提交 IMPROVISED_ACTION（零数值、审计 applied）
# ---------------------------------------------------------------------------
def test_judge_allow_commits_improvised_action() -> None:
    """allow（conf 0.9 + 非空 reason + 合法证据）→ IMPROVISED_ACTION 提交、审计 applied。"""
    judge = DeterministicJudgeAgent(_allow_output())
    harness = _harness(judge=judge)
    before = harness.write_snapshot()

    result = harness.play(_ACTION_TEXT)

    assert result.ok is True
    assert result.version == before[0] + 1
    assert len(result.events) == 1
    event = result.events[0]
    assert event.event_type is not None and event.event_type.value == "IMPROVISED_ACTION"
    assert event.params["action_text"] == _ACTION_TEXT
    assert event.result["narrative_hint"] == _ACTION_TEXT[:120]
    assert len(judge.calls) == 1
    rows = harness.audit_rows()
    assert len(rows) == 1
    assert rows[0].verdict == "applied" and rows[0].status == "committed"
    assert "火把" in rows[0].verdict_reason


# ---------------------------------------------------------------------------
# 3) deny / 低置信：保守拒绝零写入
# ---------------------------------------------------------------------------
def test_judge_deny_rejects_improvise_zero_write() -> None:
    """deny（conf≥floor）→ RuleEngine 保守拒绝、零事件、零写入。"""
    harness = _harness(judge=DeterministicJudgeAgent(_allow_output(verdict="deny")))
    before = harness.write_snapshot()

    result = harness.play(_ACTION_TEXT)

    _assert_rejected_zero_write(harness, result, before)
    assert "保守" in harness.audit_rows()[0].verdict_reason


def test_judge_low_confidence_rejects_improvise_zero_write() -> None:
    """置信度低于采纳门槛（0.5 < 0.7）→ 确定性兜底拒绝、（裁判理由进审计）。"""
    low = _allow_output(confidence=JUDGE_CONFIDENCE_FLOOR - 0.2, reason="大概可以。")
    harness = _harness(judge=DeterministicJudgeAgent(low))
    before = harness.write_snapshot()

    result = harness.play(_ACTION_TEXT)

    _assert_rejected_zero_write(harness, result, before)
    assert "临场动作" in harness.audit_rows()[0].verdict_reason


# ---------------------------------------------------------------------------
# 4) 证据越界：LLM 裁判引用感知切片外实体 → 无效证据 → 兜底拒绝
# ---------------------------------------------------------------------------
def test_judge_evidence_outside_slice_rejects_zero_write() -> None:
    """证据引用感知切片外实体（「门外的巨龙」）→ Agent 侧判定无效 → 兜底拒绝零写入。"""
    fake = FakeLLM(
        [
            {
                "verdict": "allow",
                "reason": "有巨龙帮忙，当然可以。",
                "evidence_refs": ["门外的巨龙"],
                "confidence": 0.9,
            }
        ]
    )
    harness = _harness(judge=LLMJudgeAgent(fake))
    before = harness.write_snapshot()

    result = harness.play(_ACTION_TEXT)

    _assert_rejected_zero_write(harness, result, before)
    assert len(fake.calls) == 1  # 只问一次裁判；无效证据后不重试、不重判
    assert "临场动作" in harness.audit_rows()[0].verdict_reason


# ---------------------------------------------------------------------------
# 5) 每回合裁判调用上限 1（m3：玩家 + 2 NPC 的 needs_judge 提案）
# ---------------------------------------------------------------------------
def test_judge_call_budget_is_one_per_turn() -> None:
    """m3 同回合 3 条 needs_judge 提案：裁判恰好调用 1 次，其余确定性兜底拒绝。"""
    judge = DeterministicJudgeAgent(_allow_output())
    npc_agents = {
        M3_NPC_SCAVENGER_A: _NpcImproviseAgent("往火堆里再添一把柴"),
        M3_NPC_SCAVENGER_B: _NpcImproviseAgent("用袖子擦掉墙上的灰"),
    }
    harness = _harness(judge=judge, scenario="m3", character_agents=npc_agents)

    result = harness.play(_ACTION_TEXT)

    assert len(judge.calls) == 1, "每回合最多 1 次裁判调用（第 8/12 章预算）"
    assert result.ok is True
    player_events = [event for event in result.events if event.actor_id == PLAYER_ID]
    npc_events = [
        event
        for event in result.events
        if event.actor_id in {M3_NPC_SCAVENGER_A, M3_NPC_SCAVENGER_B}
    ]
    assert len(npc_events) == 0, "未被裁判覆盖的 NPC 提案必须走确定性兜底拒绝"
    assert player_events and len(player_events) == 1
    rows = harness.audit_rows()
    assert len(rows) == 3  # 玩家 + 2 NPC 各一行审计
    assert sum(1 for row in rows if row.verdict == "applied") == 1
    assert sum(1 for row in rows if row.verdict == "rejected") == 2


# ---------------------------------------------------------------------------
# 6) 裁判请求载荷：action_text 原样 + 无内部 ID + judge@v1
# ---------------------------------------------------------------------------
def test_judge_request_payload_contains_action_text_without_internal_ids() -> None:
    """裁判请求含 ``action_text`` 原文（裁剪 200）、不含内部 ID，prompt_version 仍 judge@v1。"""
    fake = FakeLLM(
        [
            {
                "verdict": "allow",
                "reason": "刻字无碍。",
                "evidence_refs": ["火把"],
                "confidence": 0.9,
            }
        ]
    )
    harness = _harness(judge=LLMJudgeAgent(fake))

    result = harness.play(_ACTION_TEXT)

    assert result.ok is True
    assert len(fake.calls) == 1
    request = fake.calls[0]
    assert request.prompt_version == "judge@v1"
    body = request.messages[1].content
    assert "action_text" in body
    assert _ACTION_TEXT in body
    for marker in ("item_", "npc_", "char_", "room_", "game_"):
        assert marker not in body, f"裁判请求不得携带内部 ID 前缀 {marker!r}"
