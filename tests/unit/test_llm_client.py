"""单元测试：M5-019 ``LiteLLMClient`` 治理语义（第 12 章 12.2/12.3/12.7/12.9）。

全程不联网、不真实 sleep：transport 由 ``tests.fakes.FakeTransport`` 注入，时钟由
``FakeClock`` 注入；socket 禁网 guard 仍由 ``tests/conftest.py`` autouse 生效。
覆盖：deadline、tenacity 单一重试（≤3 次，仅可重试错误）、RPM 令牌桶、幂等复用/在飞、
token 计量、结构化日志脱敏、错误码映射、工厂默认离线与 fail-fast。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import BaseModel, SecretStr

from fakes import FakeTransport
from textgame.config.settings import Settings
from textgame.domain.results import ErrorCode
from textgame.llm.client import (
    ChatMessage,
    DisabledLLMClient,
    LLMRequest,
    ModelTier,
    compose_idempotency_key,
)
from textgame.llm.config import LLMClientConfig
from textgame.llm.errors import ProviderCallError
from textgame.llm.factory import build_llm_client
from textgame.llm.litellm_client import LiteLLMClient
from textgame.llm.records import InMemoryLLMCallLog, LLMCallEntry, LLMCallRecord

_API_KEY = "sk-test-secret-abcdefghijklmnop"
_BASE_CONFIG: dict[str, Any] = {
    "provider": "deepseek",
    "model": "deepseek-flash",
    "api_base": "https://api.deepseek.com",
    "api_key": SecretStr(_API_KEY),
    "max_attempts": 3,
    "timeout_s": 3.0,
    "rpm_limit": 60,
}


class SampleOutput(BaseModel):
    """结构化输出示例 Schema（complete_json 必填 output_schema）。"""

    answer: str


class FakeClock:
    """可推进的伪时钟；``sleep`` 推进时钟但不真实等待。"""

    def __init__(self, start: float = 1000.0) -> None:
        """从 ``start`` 秒开始计时。"""
        self.now = start
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        """返回当前伪时间（秒）。"""
        return self.now

    def advance(self, seconds: float) -> None:
        """手动推进伪时间。"""
        self.now += seconds

    def sleep(self, seconds: float) -> None:
        """记录并推进等待时长（注入 tenacity，测试零真实等待）。"""
        self.sleeps.append(seconds)
        self.now += seconds


def _config(**overrides: Any) -> LLMClientConfig:
    """构造测试用客户端配置（默认限流 60 RPM、单次超时 3s、最多 3 次尝试）。"""
    values = dict(_BASE_CONFIG)
    values.update(overrides)
    return LLMClientConfig(**values)


def _request(
    *,
    output_schema: type[BaseModel] | None = SampleOutput,
    task: str = "intent_parse",
    agent_id: str = "parse",
    turn_id: int = 2,
    idempotency_key: str = "idem-turn-2",
    messages: list[ChatMessage] | None = None,
    timeout_s: float = 3.0,
    prompt_version: str = "parse@v1",
) -> LLMRequest:
    """构造测试请求（默认意图解析形态，与 Agent 生产请求同构）。"""
    return LLMRequest(
        task=task,
        output_schema=output_schema,
        agent_id=agent_id,
        turn_id=turn_id,
        idempotency_key=idempotency_key,
        model_tier=ModelTier.ECONOMY,
        messages=messages or [ChatMessage(role="user", content="向北走")],
        timeout_s=timeout_s,
        prompt_version=prompt_version,
    )


def _client(
    transport: Callable[..., Any],
    *,
    clock: FakeClock | None = None,
    calls: InMemoryLLMCallLog | None = None,
    config: LLMClientConfig | None = None,
) -> LiteLLMClient:
    """构造可断言的客户端：注入 transport/伪时钟/内存调用日志。"""
    fake_clock = clock or FakeClock()
    return LiteLLMClient(
        config or _config(),
        transport=transport,
        calls=calls or InMemoryLLMCallLog(),
        clock=fake_clock,
        sleep=fake_clock.sleep,
    )


# ---------------------------------------------------------------------------
# 成功路径与契约
# ---------------------------------------------------------------------------
def test_complete_json_success_returns_content_usage_and_call() -> None:
    """成功路径：1 次 transport、data 含 content/usage/call、attempt=1、计量正确。"""
    transport = FakeTransport(['{"answer": "ok"}'])
    client = _client(transport)

    result = client.complete_json(_request())

    assert result.ok is True
    assert result.events == []
    assert result.data is not None
    assert json.loads(result.data["content"]) == {"answer": "ok"}
    assert result.data["repaired"] is False
    assert result.data["usage"] == {"prompt_tokens": 48, "completion_tokens": 16}
    call = result.data["call"]
    assert call.attempt == 1
    assert call.prompt_tokens == 48
    assert call.completion_tokens == 16
    assert call.provider == "deepseek"
    assert call.model == "fake-model"
    assert call.prompt_version == "parse@v1"
    assert call.error_code is None
    assert len(transport.calls) == 1


def test_complete_text_success_without_schema() -> None:
    """纯文本任务：无 output_schema 成功返回 content/usage（不出现 parsed 键）。"""
    transport = FakeTransport(["可用"])
    client = _client(transport)

    result = client.complete_text(
        _request(output_schema=None, task="narration", agent_id="narrate")
    )

    assert result.ok is True
    assert result.data is not None
    assert result.data["content"] == "可用"
    assert "parsed" not in result.data


def test_complete_json_requires_output_schema() -> None:
    """complete_json 缺 output_schema → INVALID_INPUT，且 0 次 transport。"""
    transport = FakeTransport(["unused"])
    client = _client(transport)

    result = client.complete_json(_request(output_schema=None))

    assert result.ok is False
    assert result.error_code is ErrorCode.INVALID_INPUT
    assert result.events == []
    assert transport.calls == []


def test_complete_text_rejects_output_schema() -> None:
    """complete_text 携带 output_schema → INVALID_INPUT（防误用），0 次 transport。"""
    transport = FakeTransport(["unused"])
    client = _client(transport)

    result = client.complete_text(_request())

    assert result.ok is False
    assert result.error_code is ErrorCode.INVALID_INPUT
    assert transport.calls == []


# ---------------------------------------------------------------------------
# 重试 / deadline / 错误码
# ---------------------------------------------------------------------------
def test_retryable_error_then_success_retries_once() -> None:
    """可重试错误 → tenacity 重试一次成功；transport 恰 2 次、attempt=2。"""
    clock = FakeClock()
    transport = FakeTransport([ProviderCallError("429", retryable=True, status_code=429), "ok"])
    client = _client(transport, clock=clock)

    result = client.complete_json(_request())

    assert result.ok is True
    assert result.data is not None
    assert result.data["call"].attempt == 2
    assert len(transport.calls) == 2
    assert len(clock.sleeps) == 1


def test_retry_exhausted_returns_llm_unavailable() -> None:
    """连续 3 次可重试错误 → LLM_UNAVAILABLE；调用恰 3 次、失败记录 1 条。"""
    clock = FakeClock()
    calls = InMemoryLLMCallLog()
    transport = FakeTransport([ProviderCallError("timeout", retryable=True) for _ in range(3)])
    client = _client(transport, clock=clock, calls=calls)

    result = client.complete_json(_request(timeout_s=60.0))

    assert result.ok is False
    assert result.error_code is ErrorCode.LLM_UNAVAILABLE
    assert len(transport.calls) == 3
    assert len(calls.failures) == 1
    assert calls.failures[0].error_code is ErrorCode.LLM_UNAVAILABLE


def test_non_retryable_error_not_retried() -> None:
    """不可重试错误（如鉴权失败）只调用 1 次 → LLM_UNAVAILABLE。"""
    transport = FakeTransport([ProviderCallError("auth", retryable=False, status_code=401)])
    client = _client(transport)

    result = client.complete_json(_request())

    assert result.ok is False
    assert result.error_code is ErrorCode.LLM_UNAVAILABLE
    assert len(transport.calls) == 1


def test_status_422_maps_to_invalid_input() -> None:
    """供应商 422（请求非法）→ INVALID_INPUT，且不重试。"""
    transport = FakeTransport([ProviderCallError("bad request", retryable=False, status_code=422)])
    client = _client(transport)

    result = client.complete_json(_request())

    assert result.ok is False
    assert result.error_code is ErrorCode.INVALID_INPUT
    assert len(transport.calls) == 1


def test_per_attempt_timeout_is_capped_by_remaining_deadline() -> None:
    """单次尝试 timeout ≤ 整链剩余时间（req.timeout_s=1.0 < config.timeout_s=3.0）。"""
    transport = FakeTransport(["ok"])
    client = _client(transport)

    result = client.complete_json(_request(timeout_s=1.0))

    assert result.ok is True
    assert 0.0 < transport.calls[0]["timeout_s"] <= 1.0


def test_deadline_exhaustion_stops_before_three_attempts() -> None:
    """整链 deadline 耗尽后不再发起剩余尝试（伪时钟随等待推进）。"""
    clock = FakeClock()
    transport = FakeTransport([ProviderCallError("timeout", retryable=True) for _ in range(3)])
    client = _client(transport, clock=clock)

    result = client.complete_json(_request(timeout_s=2.0))

    assert result.ok is False
    assert result.error_code is ErrorCode.LLM_UNAVAILABLE
    assert len(transport.calls) < 3


def test_retry_wait_aborts_when_deadline_consumed_by_attempt() -> None:
    """R-13：重试等待前剩余预算不足 → 不 sleep、不发起下一次 attempt，直接 LLM_UNAVAILABLE。

    与 ``test_deadline_exhaustion_stops_before_three_attempts`` 互补：该用例覆盖 attempt
    入口的 deadline 检查，本用例覆盖 tenacity ``wait`` 回调内的 deadline 检查（供应商调用
    本身耗时耗尽整链预算时，等待不能越过 deadline）。
    """
    clock = FakeClock()
    calls = 0

    def deadline_consuming_transport(**kwargs: Any) -> Any:
        """供应商调用本身耗尽整链 deadline 后可重试失败。"""
        nonlocal calls
        del kwargs
        calls += 1
        clock.advance(5.0)
        raise ProviderCallError("timeout", retryable=True)

    client = _client(deadline_consuming_transport, clock=clock)

    result = client.complete_json(_request(timeout_s=2.0))

    assert result.ok is False
    assert result.error_code is ErrorCode.LLM_UNAVAILABLE
    assert calls == 1
    assert clock.sleeps == []


def test_malformed_transport_outcome_maps_internal() -> None:
    """transport 返回残缺结构（缺 content 键）→ INTERNAL（不泄漏堆栈）。"""

    def broken_transport(**kwargs: Any) -> Any:
        del kwargs
        return {}

    client = _client(broken_transport)

    result = client.complete_json(_request())

    assert result.ok is False
    assert result.error_code is ErrorCode.INTERNAL


# ---------------------------------------------------------------------------
# 限流 / 幂等
# ---------------------------------------------------------------------------
def test_rpm_limit_blocks_call_without_touching_transport() -> None:
    """RPM=1：第二次调用在令牌桶处被拒绝，transport 仍只有 1 次。"""
    clock = FakeClock()
    transport = FakeTransport(["ok", "ok"])
    client = _client(transport, clock=clock, config=_config(rpm_limit=1))

    first = client.complete_json(_request(idempotency_key="k1"))
    second = client.complete_json(
        _request(idempotency_key="k2", messages=[ChatMessage(role="user", content="向南走")])
    )

    assert first.ok is True
    assert second.ok is False
    assert second.error_code is ErrorCode.LLM_UNAVAILABLE
    assert len(transport.calls) == 1


def test_rpm_limit_refills_after_clock_advance() -> None:
    """伪时钟推进 60s 后令牌恢复，第二次调用成功。"""
    clock = FakeClock()
    transport = FakeTransport(["ok", "ok"])
    client = _client(transport, clock=clock, config=_config(rpm_limit=1))

    assert client.complete_json(_request(idempotency_key="k1")).ok is True
    clock.advance(60.0)
    assert (
        client.complete_json(
            _request(idempotency_key="k2", messages=[ChatMessage(role="user", content="向南走")])
        ).ok
        is True
    )
    assert len(transport.calls) == 2


def test_same_idempotency_key_reuses_success_without_transport() -> None:
    """同幂等键第二次调用直接复用成功结果：transport 仍 1 次、记录不新增。"""
    calls = InMemoryLLMCallLog()
    transport = FakeTransport(["ok"])
    client = _client(transport, calls=calls)
    request = _request()

    first = client.complete_json(request)
    second = client.complete_json(request)

    assert first.ok is True and second.ok is True
    assert len(transport.calls) == 1
    assert calls.success_count == 1
    assert first.data is not None and second.data is not None
    assert first.data["content"] == second.data["content"]
    assert first.data["call"].request_id == second.data["call"].request_id


def test_in_flight_idempotency_key_returns_unavailable() -> None:
    """同键已在飞 → LLM_UNAVAILABLE，且不发起 transport（不重复扣费）。"""
    calls = InMemoryLLMCallLog()
    transport = FakeTransport(["ok"])
    client = _client(transport, calls=calls)
    request = _request()
    assert calls.try_begin(compose_idempotency_key(request)) is True

    result = client.complete_json(request)

    assert result.ok is False
    assert result.error_code is ErrorCode.LLM_UNAVAILABLE
    assert transport.calls == []
    calls.end(compose_idempotency_key(request))


def test_compose_idempotency_key_distinguishes_agents_and_messages() -> None:
    """组合幂等键：同回合不同 Agent/task/消息 → 不同键；同输入 → 同键。"""
    base = _request()
    parse = _request()
    craft = _request(task="craft_generate", agent_id="craft")
    other_message = _request(messages=[ChatMessage(role="user", content="向南走")])

    assert compose_idempotency_key(base) == compose_idempotency_key(parse)
    assert compose_idempotency_key(base) != compose_idempotency_key(craft)
    assert compose_idempotency_key(base) != compose_idempotency_key(other_message)


# ---------------------------------------------------------------------------
# 计量 / 日志 / 工厂 / 离线
# ---------------------------------------------------------------------------
def test_call_record_populated_on_success() -> None:
    """LLMCallRecord 字段齐全：task/agent/tier/provider/model/prompt_version/latency。"""
    clock = FakeClock()
    transport = FakeTransport(["ok"])
    client = _client(transport, clock=clock)

    result = client.complete_json(_request())

    assert result.data is not None
    call = result.data["call"]
    assert call.task == "intent_parse"
    assert call.agent_id == "parse"
    assert call.model_tier is ModelTier.ECONOMY
    assert call.cost_usd == 0.0
    assert call.latency_ms >= 0
    assert call.request_id


def test_failure_record_written_with_error_code() -> None:
    """失败也写 LLMCallRecord（含 error_code，token 为 0），供成本归集。"""
    calls = InMemoryLLMCallLog()
    transport = FakeTransport([ProviderCallError("boom", retryable=False)])
    client = _client(transport, calls=calls)

    result = client.complete_json(_request())

    assert result.ok is False
    assert calls.failures[0].error_code is ErrorCode.LLM_UNAVAILABLE
    assert calls.failures[0].prompt_tokens == 0
    assert calls.failures[0].completion_tokens == 0


def test_save_failure_is_best_effort() -> None:
    """幂等记录写入抛异常时不影响成功返回（只 warning）。"""

    class ExplodingCalls(InMemoryLLMCallLog):
        def save_success(self, entry: Any) -> None:
            raise RuntimeError("db down")

    transport = FakeTransport(["ok"])
    client = _client(transport, calls=ExplodingCalls())

    result = client.complete_json(_request())

    assert result.ok is True


def test_structured_log_redacts_prompt_and_api_key(caplog: pytest.LogCaptureFixture) -> None:
    """结构化日志只含 prompt 哈希，不含 Prompt 原文与 API Key。"""
    caplog.set_level(logging.INFO, logger="textgame.llm")
    transport = FakeTransport(["ok"])
    client = _client(transport)

    client.complete_json(_request(messages=[ChatMessage(role="user", content="秘密口令：向北走")]))

    log_text = caplog.text
    assert "llm_call" in log_text
    assert "prompt_sha256" in log_text
    assert "向北走" not in log_text
    assert _API_KEY not in log_text


def test_output_schema_safe_dump_is_json_serializable() -> None:
    """请求摘要剔除不可序列化的 output_schema 类对象，并给出 schema 哈希。"""
    request = _request()

    dumped = request.safe_dump()

    json.dumps(dumped)
    assert "output_schema" not in dumped
    assert dumped["output_schema_sha256"]


def test_disabled_client_implements_both_methods() -> None:
    """默认离线客户端两个方法都返回 LLM_UNAVAILABLE、零 events。"""
    client = DisabledLLMClient()

    for result in (client.complete_json(_request()), client.complete_text(_request())):
        assert result.ok is False
        assert result.error_code is ErrorCode.LLM_UNAVAILABLE
        assert result.events == []


def test_factory_defaults_to_disabled_client() -> None:
    """工厂默认（llm_enabled=false）返回 DisabledLLMClient，零网络。"""
    settings = Settings(_env_file=None, llm_enabled=False)

    client = build_llm_client(settings)

    assert isinstance(client, DisabledLLMClient)


def test_factory_enabled_without_api_key_fails_fast() -> None:
    """llm_enabled=true 但缺密钥 → 构造期 RuntimeError（不做静默降级）。"""
    settings = Settings(_env_file=None, llm_enabled=True, llm_api_key=None)

    with pytest.raises(RuntimeError, match="TEXTGAME_LLM_API_KEY"):
        build_llm_client(settings)


def test_factory_enabled_builds_real_client_with_injected_transport() -> None:
    """llm_enabled=true + 密钥 → LiteLLMClient；注入 FakeTransport 可离线验证成功路径。"""
    transport = FakeTransport(["ok"])
    settings = Settings(
        _env_file=None,
        llm_enabled=True,
        llm_api_key=SecretStr(_API_KEY),
        llm_provider="deepseek",
        llm_model="deepseek-flash",
        llm_base_url="https://api.deepseek.com",
    )

    client = build_llm_client(settings, transport=transport)
    result = client.complete_json(_request())

    assert isinstance(client, LiteLLMClient)
    assert result.ok is True
    assert len(transport.calls) == 1
    assert transport.calls[0]["provider"] == "deepseek"
    assert transport.calls[0]["api_key"] == _API_KEY


def test_in_memory_call_log_success_table_is_bounded() -> None:
    """R-21：成功复用表有界（LRU），长期运行不随调用次数无限增长。"""
    log = InMemoryLLMCallLog()
    total = log._MAX_SUCCESS_ENTRIES + 5
    for index in range(total):
        record = LLMCallRecord(
            request_id=f"req-{index}",
            task="intent_parse",
            agent_id="parse",
            model_tier=ModelTier.ECONOMY,
            provider="deepseek",
            model="m",
            attempt=1,
            latency_ms=0,
        )
        log.save_success(LLMCallEntry(idempotency_key=f"key-{index}", content="x", record=record))

    assert log.success_count == log._MAX_SUCCESS_ENTRIES
    assert log.find_success("key-0") is None
    assert log.find_success(f"key-{total - 1}") is not None
