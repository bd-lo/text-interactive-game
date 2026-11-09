"""单元测试：``complete_json_with_repair`` 的合法 / 修复 / 失败分支 (第 12 章 12.6)。"""

from __future__ import annotations

from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field

from fakes import FakeLLM
from textgame.domain.results import ErrorCode
from textgame.llm.client import ChatMessage, LLMRequest, ModelTier
from textgame.llm.validation import complete_json_with_repair


class SampleOutput(BaseModel):
    """测试用结构化输出契约：与 ``ParseOutput`` 同构的最小 Schema。"""

    model_config = ConfigDict(extra="forbid")

    action: Literal["move_to", "take_item"]
    confidence: float = Field(ge=0.0, le=1.0)


def _request(*, max_repair: int = 1) -> LLMRequest:
    """构造与解析 Agent 实际调用形态一致的请求 (ECONOMY / intent_parse)。"""
    return LLMRequest(
        task="intent_parse",
        output_schema=SampleOutput,
        agent_id="parse",
        turn_id=2,
        idempotency_key="idem-parse-0001",
        model_tier=ModelTier.ECONOMY,
        messages=[ChatMessage(role="user", content="向北走")],
        max_repair=max_repair,
    )


def test_valid_first_response_needs_no_repair() -> None:
    """合法输出一次通过：repaired=False、只调用 1 次、返回已校验的规范化数据。"""
    llm = FakeLLM([{"action": "move_to", "confidence": 0.9}])

    result = complete_json_with_repair(llm, _request(), schema_model=SampleOutput)

    assert result.ok
    assert result.error_code is None
    assert result.data is not None
    assert result.data["parsed"] == {"action": "move_to", "confidence": 0.9}
    assert result.data["repaired"] is False
    assert result.data["usage"] == {"prompt_tokens": 48, "completion_tokens": 16}
    assert len(llm.calls) == 1
    assert llm.calls[0].max_repair == 1


def test_invalid_then_valid_is_repaired_once() -> None:
    """首次输出多字段 → 回传错误修复一次 → 第二次合法：repaired=True、calls=2。"""
    llm = FakeLLM(
        [
            {"action": "move_to", "confidence": 0.9, "extra": 1},  # 额外字段 → Schema 违规
            {"action": "move_to", "confidence": 0.9},
        ]
    )

    result = complete_json_with_repair(llm, _request(), schema_model=SampleOutput)

    assert result.ok
    assert result.data is not None
    assert result.data["repaired"] is True
    assert result.data["parsed"] == {"action": "move_to", "confidence": 0.9}
    assert len(llm.calls) == 2
    # 第一次请求允许修复；修复请求不再允许第二次修复 (max_repair=0)
    assert llm.calls[0].max_repair == 1
    assert llm.calls[1].max_repair == 0
    assert llm.remaining == 0


def test_repair_request_carries_validation_error() -> None:
    """修复请求确实携带精简后的字段错误说明，而不是重复原始用户输入。"""
    llm = FakeLLM(
        [
            {"action": "move_to"},  # 缺 confidence → required 错误
            {"action": "move_to", "confidence": 0.5},
        ]
    )

    result = complete_json_with_repair(llm, _request(), schema_model=SampleOutput)

    assert result.ok
    repair_request = llm.calls[1]
    repair_text = repair_request.messages[-1].content
    assert "JSON Schema" in repair_text
    assert "confidence" in repair_text
    assert "missing" in repair_text
    assert repair_request.messages[:-1] == _request().messages


def test_repair_still_invalid_returns_schema_violation_without_data() -> None:
    """两次都不合法 → SCHEMA_VIOLATION，不返回任何未校验数据，且只修复一次。"""
    llm = FakeLLM(
        [
            '{"action": "move_to", "confidence": 2.0}',  # ge/le 越界
            "not-json",  # 修复后仍不是 JSON
            {"action": "take_item", "confidence": 0.9},  # 不应被消费
        ]
    )

    result = complete_json_with_repair(llm, _request(), schema_model=SampleOutput)

    assert not result.ok
    assert result.error_code is ErrorCode.SCHEMA_VIOLATION
    assert result.data is None
    assert result.events == []
    assert "修复后仍未通过 Schema 校验" in (result.message or "")
    assert len(llm.calls) == 2
    assert llm.remaining == 1


def test_invalid_json_first_response_can_be_repaired() -> None:
    """首次输出不是 JSON 也走一次修复 (JSON 解析失败同样属于 Schema 违规)。"""
    llm = FakeLLM(
        [
            "抱歉，我无法理解。",
            '{"action": "take_item", "confidence": 0.8}',
        ]
    )

    result = complete_json_with_repair(llm, _request(), schema_model=SampleOutput)

    assert result.ok
    assert result.data is not None
    assert result.data["repaired"] is True
    assert "JSON 解析失败" in llm.calls[1].messages[-1].content


@pytest.mark.parametrize("failure", [RuntimeError("timeout"), None])
def test_llm_unavailable_is_passed_through(failure: Exception | None) -> None:
    """客户端失败 (异常/无响应) 原样透传 LLM_UNAVAILABLE，不进入修复重试。"""
    llm = FakeLLM([failure] if failure is not None else [])

    result = complete_json_with_repair(llm, _request(), schema_model=SampleOutput)

    assert not result.ok
    assert result.error_code is ErrorCode.LLM_UNAVAILABLE
    assert result.data is None
    assert len(llm.calls) == 1


def test_max_repair_zero_skips_repair() -> None:
    """``max_repair=0`` 时首次失败直接 SCHEMA_VIOLATION，不发第二次请求。"""
    llm = FakeLLM([{"action": "move_to", "confidence": 0.9, "extra": 1}])

    result = complete_json_with_repair(llm, _request(max_repair=0), schema_model=SampleOutput)

    assert not result.ok
    assert result.error_code is ErrorCode.SCHEMA_VIOLATION
    assert len(llm.calls) == 1
