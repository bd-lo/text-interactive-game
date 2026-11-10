"""M5E-2 RuleEngine 裁判接口单测（needs_judge 生产点 + verify/fallback 保守语义）。

红线：两方法纯计算、零事件、不读时钟/IO；默认空集时 ``evaluate_preconditions`` 的 data
逐字不变（旧测试/旧 golden 零回归）。
"""

from __future__ import annotations

import socket

import pytest

from textgame.domain.judge import JudgeEvidence, JudgeOutput
from textgame.domain.models import Proposal
from textgame.domain.results import ErrorCode
from textgame.domain.rules.engine import RuleEngine
from textgame.domain.scenario import ITEM_TORCH, PLAYER_ID, build_initial_state
from textgame.services.tool_context import ToolContext

_SAVE_SEED = "save-42"
_MOVE_ARGS = {"direction": "north"}
_TAKE_ARGS = {"item_id": ITEM_TORCH}


def _state():
    return build_initial_state(save_seed=_SAVE_SEED)


def _ctx() -> ToolContext:
    return ToolContext(
        actor_id=PLAYER_ID,
        turn_id=2,
        save_seed=_SAVE_SEED,
        idempotency_key="judge-rules-unit",
        caller="resolver",
    )


def _proposal(tool: str = "take_item", args: dict | None = None) -> Proposal:
    return Proposal(
        turn_id=2,
        actor_id=PLAYER_ID,
        tool=tool,
        args=dict(_TAKE_ARGS if args is None else args),
        reason="玩家明确动作",
        priority=100,
    )


def _judge(**overrides: object) -> JudgeOutput:
    payload: dict[str, object] = {
        "verdict": "allow",
        "reason": "火把就在脚边，取用没有阻碍。",
        "evidence_refs": ["火把"],
        "confidence": 0.9,
    }
    payload.update(overrides)
    return JudgeOutput.model_validate(payload)


def _preconditions(engine: RuleEngine, *, tool: str, args: dict | None = None):
    return engine.evaluate_preconditions(
        tool=tool,
        args=dict(_TAKE_ARGS if args is None else args),
        actor_id=PLAYER_ID,
        turn_id=2,
        save_seed=_SAVE_SEED,
        state=_state(),
    )


# ---------------------------------------------------------------------------
# needs_judge 生产点
# ---------------------------------------------------------------------------
def test_default_engine_preconditions_data_is_unchanged() -> None:
    """默认空集：data 逐字仍是 allowed/reason（既有测试与 golden 零回归）。"""
    result = _preconditions(RuleEngine(), tool="take_item")

    assert result.ok is True
    assert result.data == {"allowed": True, "reason": ""}


def test_agent_assisted_tool_emits_needs_judge() -> None:
    """集合内工具 L0 通过时返回 needs_judge=True；集合外工具不产生该键。"""
    engine = RuleEngine(agent_assisted_tools=frozenset({"take_item"}))

    take = _preconditions(engine, tool="take_item")
    move = _preconditions(engine, tool="move_to", args=_MOVE_ARGS)

    assert take.data == {"allowed": True, "needs_judge": True, "reason": ""}
    assert "needs_judge" not in (move.data or {})
    assert engine.agent_assisted_tools == frozenset({"take_item"})


# ---------------------------------------------------------------------------
# verify_agent_override：只采纳 allow + 高置信 + 非空理由
# ---------------------------------------------------------------------------
def test_verify_allows_high_confidence_allow_with_reason() -> None:
    """合法证据通过：data={"allowed": True, "reason": <裁判理由>}，零事件。"""
    engine = RuleEngine()

    result = engine.verify_agent_override(_ctx(), _proposal(), _judge())

    assert result.ok is True
    assert result.data == {"allowed": True, "reason": "火把就在脚边，取用没有阻碍。"}
    assert result.events == []


@pytest.mark.parametrize("verdict", ["deny", "modify"])
def test_verify_rejects_deny_and_modify(verdict: str) -> None:
    """deny/modify 均保守拒绝（modify 无法机械应用，不给有利效果）。"""
    engine = RuleEngine()

    result = engine.verify_agent_override(_ctx(), _proposal(), _judge(verdict=verdict))

    assert result.ok is False
    assert result.error_code is ErrorCode.NOT_ALLOWED
    assert "保守" in (result.message or "")
    assert result.events == []


@pytest.mark.parametrize("confidence", [0.0, 0.69])
def test_verify_rejects_low_confidence(confidence: float) -> None:
    """低于 0.7 的置信度一律拒绝。"""
    engine = RuleEngine()

    result = engine.verify_agent_override(_ctx(), _proposal(), _judge(confidence=confidence))

    assert result.ok is False
    assert result.error_code is ErrorCode.NOT_ALLOWED


def test_verify_rejects_blank_reason_and_non_evidence_object() -> None:
    """reason 纯空白、任意非法对象（无属性/dict）都拒绝且不抛异常。"""
    engine = RuleEngine()

    assert engine.verify_agent_override(_ctx(), _proposal(), _judge(reason="   ")).ok is False
    for bogus in (object(), {"verdict": "allow", "reason": "x", "confidence": 0.9}):
        result = engine.verify_agent_override(_ctx(), _proposal(), bogus)  # type: ignore[arg-type]
        assert result.ok is False
        assert result.error_code is ErrorCode.NOT_ALLOWED
        assert result.events == []


class _DuckJudge:
    """任意 duck 对象（非 ``JudgeEvidence`` 契约）：验证不会被 ``str()/float()`` 洗白。"""

    def __init__(self, **attrs: object) -> None:
        self.__dict__.update(attrs)


@pytest.mark.parametrize(
    "bad",
    [
        _DuckJudge(verdict="allow", reason="理由", confidence="0.95"),
        _DuckJudge(verdict="allow", reason="理由", confidence=None),
        _DuckJudge(verdict="allow", reason=None, confidence=0.9),
        _DuckJudge(verdict="allow", reason=123, confidence=0.9),
        _DuckJudge(verdict=None, reason="理由", confidence=0.9),
        _DuckJudge(verdict=object(), reason="理由", confidence=0.9),
        _DuckJudge(verdict="deny", reason="理由", confidence=0.9),
        _DuckJudge(verdict="allow", reason="   ", confidence=0.9),
        _DuckJudge(verdict="allow", reason="理由", confidence=float("nan")),
    ],
    ids=[
        "confidence-str",
        "confidence-none",
        "reason-none",
        "reason-int",
        "verdict-none",
        "verdict-object",
        "deny",
        "reason-blank",
        "confidence-nan",
    ],
)
def test_verify_rejects_malicious_duck_objects(bad: object) -> None:
    """QA 红队 4 例 + 扩展：任何非契约对象都返回否定 Result、不抛异常（不会被强转洗白）。"""
    result = RuleEngine().verify_agent_override(None, _proposal(), bad)  # type: ignore[arg-type]

    assert result.ok is False
    assert result.error_code is ErrorCode.NOT_ALLOWED
    assert result.message
    assert result.events == []


@pytest.mark.parametrize(
    "bad",
    [
        JudgeEvidence.model_construct(
            verdict="allow", reason=None, evidence_refs=[], confidence=0.9
        ),
        JudgeEvidence.model_construct(
            verdict="allow", reason=123, evidence_refs=[], confidence=0.9
        ),
        JudgeEvidence.model_construct(
            verdict="allow", reason="理由", evidence_refs=[], confidence="0.95"
        ),
        JudgeEvidence.model_construct(
            verdict="allow", reason="理由", evidence_refs=[], confidence=float("nan")
        ),
        JudgeEvidence.model_construct(
            verdict=None, reason="理由", evidence_refs=[], confidence=0.9
        ),
        JudgeEvidence.model_construct(
            verdict="allow", reason="理由", evidence_refs=[], confidence=True
        ),
    ],
    ids=[
        "construct-reason-none",
        "construct-reason-int",
        "construct-confidence-str",
        "construct-confidence-nan",
        "construct-verdict-none",
        "construct-confidence-bool",
    ],
)
def test_verify_rejects_model_construct_invalid_fields(bad: JudgeEvidence) -> None:
    """``model_construct`` 绕过 pydantic 校验时，字段级严格校验仍拒绝（防洗白第二道门）。"""
    result = RuleEngine().verify_agent_override(None, _proposal(), bad)

    assert result.ok is False
    assert result.error_code is ErrorCode.NOT_ALLOWED
    assert result.events == []


# ---------------------------------------------------------------------------
# deterministic_fallback：保守否决、纯函数
# ---------------------------------------------------------------------------
def test_deterministic_fallback_is_conservative_and_deterministic() -> None:
    """兜底恒否决（NOT_ALLOWED + 中文保守提示、零事件），同输入两次逐字相同。"""
    engine = RuleEngine()
    state = _state()

    first = engine.deterministic_fallback(_ctx(), _proposal(), state)
    second = engine.deterministic_fallback(_ctx(), _proposal(), state)

    assert first.ok is False
    assert first.error_code is ErrorCode.NOT_ALLOWED
    assert "保守" in (first.message or "") and "裁判不可用" in (first.message or "")
    assert first.events == []
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_judge_methods_perform_no_io(monkeypatch: pytest.MonkeyPatch) -> None:
    """纯函数：禁用文件/套接字访问后两方法仍正常返回。"""

    def _forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("RuleEngine judge methods must not perform IO")

    monkeypatch.setattr("builtins.open", _forbidden)
    monkeypatch.setattr(socket.socket, "connect", _forbidden)
    engine = RuleEngine(agent_assisted_tools=frozenset({"take_item"}))

    assert engine.verify_agent_override(_ctx(), _proposal(), _judge()).ok is True
    assert engine.deterministic_fallback(_ctx(), _proposal(), _state()).ok is False
