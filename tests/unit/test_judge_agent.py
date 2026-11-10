"""M5E-2 裁判 Agent 单测（JudgeOutput 契约 / Deterministic 替身 / LLMJudgeAgent 治理链）。

覆盖：JudgeOutput Schema（extra 拒绝/verdict 枚举/置信度边界/reason 长度/证据上限）、
DeterministicJudgeAgent 固定输出、LLMJudgeAgent 成功/一次修复/两次失败/离线/异常/证据越界
→ 全部失败路径返回 None（由 Resolver 确定性兜底）、Prompt 只含脱敏感知切片与提案。
零网络：FakeLLM/DisabledLLMClient。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from fakes import FakeLLM
from textgame.agents.judge_agent import (
    PROMPT_VERSION,
    DeterministicJudgeAgent,
    LLMJudgeAgent,
)
from textgame.domain.judge import JUDGE_CONFIDENCE_FLOOR, MAX_EVIDENCE_REFS, JudgeOutput
from textgame.domain.models import EntityRef, PerceptionSlice, Proposal
from textgame.domain.results import Result
from textgame.domain.scenario import ITEM_TORCH, PLAYER_ID
from textgame.llm.client import DisabledLLMClient, LLMRequest, ModelTier

_SAVE_SEED = "save-42"


def _perception() -> PerceptionSlice:
    """模拟 m2 入口的玩家私有感知切片（可见火把 + 北出口）。"""
    return PerceptionSlice(
        actor_id=PLAYER_ID,
        visible_entities=[
            EntityRef(kind="item", id=ITEM_TORCH, name="火把"),
            EntityRef(kind="npc", id="npc_sentinel", name="老哨兵"),
        ],
        exit_refs=[EntityRef(kind="room", id="north", name="哨站地窖")],
    )


def _proposal(*, tool: str = "take_item", args: dict | None = None) -> Proposal:
    """构造一个 needs_judge 提案（默认拿火把）。"""
    return Proposal(
        turn_id=2,
        actor_id=PLAYER_ID,
        tool=tool,
        args={"item_id": ITEM_TORCH} if args is None else args,
        reason="玩家明确动作",
        priority=100,
    )


def _valid_payload(**overrides: object) -> dict[str, object]:
    """合法 JudgeOutput JSON 载荷（可按字段覆盖）。"""
    payload: dict[str, object] = {
        "verdict": "allow",
        "reason": "火把就在脚边，取用没有阻碍。",
        "evidence_refs": ["火把"],
        "confidence": 0.9,
    }
    payload.update(overrides)
    return payload


def _llm_agent(responses: list[object]) -> tuple[LLMJudgeAgent, FakeLLM]:
    """FakeLLM 驱动 LLMJudgeAgent（客户端层替身，零网络）。"""
    fake = FakeLLM(responses)
    return LLMJudgeAgent(fake), fake


def _evaluate(agent: LLMJudgeAgent) -> JudgeOutput | None:
    return agent.evaluate(_perception(), _proposal(), turn_id=2, save_seed=_SAVE_SEED)


# ---------------------------------------------------------------------------
# JudgeOutput Schema
# ---------------------------------------------------------------------------
def test_judge_output_construct_and_allowed_property() -> None:
    """合法输出可构造；``allowed`` 只读属性 = verdict=="allow"；dump 字段集合固定。"""
    judge = JudgeOutput.model_validate(_valid_payload())

    assert judge.verdict == "allow" and judge.allowed is True
    assert set(judge.model_dump(mode="json")) == {
        "verdict",
        "reason",
        "evidence_refs",
        "confidence",
    }
    assert JudgeOutput.model_validate(_valid_payload(verdict="deny")).allowed is False
    assert JudgeOutput.model_validate(_valid_payload(verdict="modify")).allowed is False


def test_judge_output_rejects_extra_fields() -> None:
    """extra=forbid：模型多给字段（如伪造 allowed/数值）直接拒绝。"""
    with pytest.raises(ValidationError):
        JudgeOutput.model_validate(_valid_payload(allowed=True))


def test_judge_output_rejects_unknown_verdict() -> None:
    """verdict 只允许 allow/deny/modify。"""
    with pytest.raises(ValidationError):
        JudgeOutput.model_validate(_valid_payload(verdict="maybe"))


@pytest.mark.parametrize("confidence", [-0.1, 1.5])
def test_judge_output_confidence_bounds(confidence: float) -> None:
    """confidence 必须落在 0..1（越界/恶意值拒绝）。"""
    with pytest.raises(ValidationError):
        JudgeOutput.model_validate(_valid_payload(confidence=confidence))


def test_judge_output_reason_length_bounds() -> None:
    """reason 1–300 字：空串与超长均拒绝。"""
    for reason in ("", "长" * 301):
        with pytest.raises(ValidationError):
            JudgeOutput.model_validate(_valid_payload(reason=reason))


def test_judge_output_evidence_refs_limit() -> None:
    """evidence_refs 最多 5 条。"""
    with pytest.raises(ValidationError):
        JudgeOutput.model_validate(
            _valid_payload(evidence_refs=[f"实体{i}" for i in range(MAX_EVIDENCE_REFS + 1)])
        )


# ---------------------------------------------------------------------------
# DeterministicJudgeAgent（测试替身）
# ---------------------------------------------------------------------------
def test_deterministic_judge_returns_configured_output_and_records_calls() -> None:
    """替身：固定返回构造时的证据，并记录每次调用的感知/提案（零 IO）。"""
    output = JudgeOutput.model_validate(_valid_payload())
    agent = DeterministicJudgeAgent(output)

    result = agent.evaluate(_perception(), _proposal(), turn_id=2, save_seed=_SAVE_SEED)

    assert result == output
    assert len(agent.calls) == 1
    assert agent.calls[0][0].actor_id == PLAYER_ID and agent.calls[0][2] == 2
    assert (
        DeterministicJudgeAgent(None).evaluate(
            _perception(), _proposal(), turn_id=2, save_seed=_SAVE_SEED
        )
        is None
    )


# ---------------------------------------------------------------------------
# LLMJudgeAgent：成功 / 修复 / 失败兜底
# ---------------------------------------------------------------------------
def test_llm_judge_success_returns_output_and_request_contract() -> None:
    """成功：返回 JudgeOutput；请求 task/prompt_version/STRONG/max_repair/output_schema 正确。"""
    agent, fake = _llm_agent([_valid_payload()])

    result = _evaluate(agent)

    assert isinstance(result, JudgeOutput) and result.allowed is True
    request: LLMRequest = fake.calls[0]
    assert request.task == "judge"
    assert request.prompt_version == PROMPT_VERSION == "judge@v1"
    assert request.model_tier is ModelTier.STRONG
    assert request.max_repair == 1
    assert request.output_schema is JudgeOutput
    assert request.idempotency_key.startswith("judge:000002:")


def test_llm_judge_repairs_once_then_succeeds() -> None:
    """首次 Schema 违规 → 恰好一次修复 → 第二次合法输出被采纳（共 2 次请求）。"""
    agent, fake = _llm_agent(["not-json", _valid_payload(verdict="allow")])

    result = _evaluate(agent)

    assert result is not None and result.allowed is True
    assert len(fake.calls) == 2


def test_llm_judge_invalid_twice_returns_none() -> None:
    """两次都不合格 → None（Resolver 走确定性保守兜底），恰好 2 次请求。"""
    agent, fake = _llm_agent(["not-json", '{"verdict": "maybe"}'])

    result = _evaluate(agent)

    assert result is None
    assert len(fake.calls) == 2


def test_llm_judge_offline_or_exception_returns_none() -> None:
    """默认离线客户端/客户端异常 → None（裁判绝不影响回合）。"""

    class _RaisingClient:
        def complete_json(self, req: LLMRequest) -> Result:
            raise RuntimeError("boom")

        def complete_text(self, req: LLMRequest) -> Result:  # pragma: no cover - 未使用
            return self.complete_json(req)

    assert _evaluate(LLMJudgeAgent(DisabledLLMClient())) is None
    assert _evaluate(LLMJudgeAgent(_RaisingClient())) is None


def test_llm_judge_rejects_evidence_outside_perception_slice() -> None:
    """证据引用不在私有感知切片内（幻觉实体）→ 视为无效证据返回 None。"""
    agent, _fake = _llm_agent([_valid_payload(evidence_refs=["门外的巨龙"])])

    assert _evaluate(agent) is None


def test_llm_judge_prompt_contains_only_projected_slice_and_proposal() -> None:
    """Prompt 只含脱敏感知切片/提案（名称+方向），不含 GameState 与内部实体 ID。"""
    agent, fake = _llm_agent([_valid_payload()])

    _evaluate(agent)

    request = fake.calls[0]
    combined = request.messages[0].content + request.messages[1].content
    assert "火把" in combined and "老哨兵" in combined and "take_item" in combined
    assert "哨站地窖" in combined
    assert "GameState" not in combined
    assert ITEM_TORCH not in combined  # 内部 ULID 不进入模型输入
    assert "npc_sentinel" not in combined
    # 规则空白说明与 JSON Schema 在提示中
    assert "agent_assisted" in combined
    assert '"verdict"' in combined


def test_judge_confidence_floor_constant() -> None:
    """置信度门槛与第 10/11 章口径一致（0.7）。"""
    assert JUDGE_CONFIDENCE_FLOOR == 0.7
