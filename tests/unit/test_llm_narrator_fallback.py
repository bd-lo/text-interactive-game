"""单元测试：``LLMNarratorAgent`` 在**真实客户端治理链**下的回退可感知性（R2 任务 2）。

与 ``test_llm_wiring.py`` 的 schema 失败回退互补：这里用 ``LiteLLMClient`` + 注入 transport
覆盖「供应商不可达 / 不可重试错误」两条 LLM_UNAVAILABLE 路径，断言：
- 叙事不抛异常、返回模板文本且 ``source="template"``（前端据此显示“本地叙事”）；
- 可重试错误恰好用满 ``max_attempts`` 次尝试，不可重试错误只尝试 1 次；
- 全过程零真实网络、零真实 sleep（transport/sleep 均注入）。
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr

from fakes import FakeTransport
from textgame.agents.dto import ParseOutcome
from textgame.agents.narrator_agent import LLMNarratorAgent
from textgame.domain.models import ActionIntent
from textgame.domain.scenario_story import build_story_state
from textgame.llm.config import LLMClientConfig
from textgame.llm.errors import ProviderCallError
from textgame.llm.litellm_client import LiteLLMClient
from textgame.orchestration.narrator import TemplateNarrator

_SAVE_SEED = "save-42"
_API_KEY = "cassette-no-secret"  # 桩密钥，非真实凭据


def _config() -> LLMClientConfig:
    """测试用客户端配置（假端点/桩密钥；不会发起真实请求）。"""
    return LLMClientConfig(
        provider="deepseek",
        model="fake-model",
        api_base="https://cassette.invalid",
        api_key=SecretStr(_API_KEY),
        max_attempts=3,
        timeout_s=5.0,
    )


def _narrator(transport: FakeTransport) -> LLMNarratorAgent:
    """按测试 transport 组装真实治理链客户端 + 模板兜底叙事器。"""
    client = LiteLLMClient(_config(), transport=transport, sleep=lambda _s: None)
    return LLMNarratorAgent(client, fallback=TemplateNarrator())


def _outcome(tool: str = "get_room_view") -> ParseOutcome:
    """构造与编排层同构的解析结果。"""
    return ParseOutcome(
        intent=ActionIntent(action=tool, target=None, params={}, confidence=1.0),
        tool=tool,
        args={},
        confidence=1.0,
        source="keyword",
    )


def _render(narrator: LLMNarratorAgent) -> tuple[str, str]:
    """用剧情场景初始状态渲染一次只读回合叙事。"""
    return narrator.render_with_meta(
        action=_outcome(), events=[], state=build_story_state(save_seed=_SAVE_SEED), result_data={}
    )


def test_retryable_transport_failure_uses_all_attempts_then_template() -> None:
    """可重试供应商错误：尝试 3 次（max_attempts）→ LLM_UNAVAILABLE → 模板兜底。"""
    transport = FakeTransport([ProviderCallError("connection refused", retryable=True)] * 3)

    text, source = _render(_narrator(transport))

    assert source == "template"
    assert 80 <= len(text) <= 150
    assert len(transport.calls) == 3
    # deadline 收缩：每次尝试的 timeout 不超过整链 timeout_s。
    assert all(0 < call["timeout_s"] <= 5.0 for call in transport.calls)


def test_permanent_transport_failure_tries_once_then_template() -> None:
    """不可重试供应商错误（如鉴权失败）：只尝试 1 次即回退模板，不重复计费。"""
    transport = FakeTransport([ProviderCallError("authentication failed", retryable=False)])

    text, source = _render(_narrator(transport))

    assert source == "template"
    assert 80 <= len(text) <= 150
    assert len(transport.calls) == 1


def test_transport_exhaustion_then_template() -> None:
    """transport 脚本耗尽（非重试错误）→ 模板兜底仍返回合法一屏。"""
    transport = FakeTransport([])

    text, source = _render(_narrator(transport))

    assert source == "template"
    assert 80 <= len(text) <= 150
    assert len(transport.calls) == 1


@pytest.mark.parametrize("narrative_factory", [lambda: {"narrative": "短"}])
def test_repair_then_still_violating_falls_back(narrative_factory: Any) -> None:
    """供应商成功但输出两次都不合法 → SCHEMA_VIOLATION → 模板兜底（不写不稳定输出）。"""
    transport = FakeTransport([narrative_factory(), narrative_factory()])

    text, source = _render(_narrator(transport))

    assert source == "template"
    assert 80 <= len(text) <= 150
    assert len(transport.calls) == 2  # 首次 + 恰好一次修复
