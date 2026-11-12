"""单元测试：N2-038 流式 LLM 客户端治理（``LiteLLMClient.complete_json_stream``，ADR D-N2-4）。

全程零网络、零真实 sleep：流式运输由本地 ``FakeStreamTransport`` 注入、时钟由 ``FakeClock``
注入（与 ``tests/unit/test_llm_client.py`` 同口径）。覆盖：首 chunk 前可重试/耗尽换供应商、
首 chunk 后不重试不换供应商、空闲超时、usage 取末 chunk、缓存/幂等命中一次下发、
``LLMCallRecord.streamed``、RPM 只按次前置、回调异常不影响结果；另含 providers 流式适配
与 ``RuntimeLLMClient`` 委托（不支持流式时退回一次下发）。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, SecretStr

from textgame.config.settings import Settings
from textgame.domain.results import ErrorCode
from textgame.llm.client import (
    ChatMessage,
    DeltaCallback,
    LLMRequest,
    ModelTier,
    compose_idempotency_key,
    supports_streaming,
)
from textgame.llm.config import (
    CacheSpec,
    CircuitSpec,
    FallbackSpec,
    LLMClientConfig,
    LLMConfig,
    ProviderSpec,
    TierSpec,
    load_llm_config,
)
from textgame.llm.errors import ProviderCallError
from textgame.llm.litellm_client import LiteLLMClient
from textgame.llm.providers import ChatCompletionChunk
from textgame.llm.records import InMemoryLLMCallLog
from textgame.llm.runtime import RuntimeLLMClient

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


class NarrativeOutput(BaseModel):
    """结构化输出示例 Schema（``complete_json_stream`` 必填 output_schema）。"""

    narrative: str


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


#: 单个 chunk 规格：str=文本；dict=文本+usage/model；Exception=迭代中抛出；callable=副作用。
ChunkSpec = str | dict[str, Any] | Exception | Callable[[], None]
#: 单次尝试脚本：chunk 规格序列，或一个异常（首 chunk 前失败）。
AttemptScript = Sequence[ChunkSpec] | Exception
FakeStreamResponse = Any


class FakeStreamTransport:
    """可编排的供应商流式 transport 替身（零网络，记录每次尝试的 kwargs）。"""

    def __init__(
        self,
        scripts: Sequence[FakeStreamResponse] | None = None,
        *,
        model: str = "fake-stream-model",
    ) -> None:
        """``scripts`` 按尝试顺序消费：chunk 序列 → 逐 chunk 产出；异常 → 首 chunk 前失败。"""
        self._scripts: list[FakeStreamResponse] = list(scripts or [])
        self._model = model
        self._cursor = 0
        self.calls: list[dict[str, Any]] = []

    @property
    def remaining(self) -> int:
        """剩余未消费的脚本尝试数。"""
        return len(self._scripts) - self._cursor

    def __call__(
        self,
        *,
        provider: str,
        model: str,
        api_key: str,
        api_base: str,
        messages: list[dict[str, str]],
        timeout_s: float,
    ) -> Iterator[ChatCompletionChunk]:
        """记录调用参数并返回一个按脚本产出的 chunk 迭代器（异常在迭代时抛出）。"""
        self.calls.append(
            {
                "provider": provider,
                "model": model,
                "api_key": api_key,
                "api_base": api_base,
                "messages": messages,
                "timeout_s": timeout_s,
            }
        )
        if self._cursor >= len(self._scripts):
            raise ProviderCallError("FakeStreamTransport: 脚本响应已耗尽", retryable=False)
        script = self._scripts[self._cursor]
        self._cursor += 1

        def chunks() -> Iterator[ChatCompletionChunk]:
            """按脚本产出；``Exception`` 项在对应位置抛出。"""
            if isinstance(script, Exception):
                raise script
            for item in script:  # type: ignore[union-attr]
                if isinstance(item, Exception):
                    raise item
                if callable(item):
                    item()
                    continue
                if isinstance(item, dict):
                    yield ChatCompletionChunk(
                        text=str(item.get("text", "")),
                        model=str(item.get("model", self._model)),
                        prompt_tokens=int(item.get("prompt_tokens", 0) or 0),
                        completion_tokens=int(item.get("completion_tokens", 0) or 0),
                    )
                    continue
                yield ChatCompletionChunk(
                    text=str(item),
                    model=self._model,
                    prompt_tokens=0,
                    completion_tokens=0,
                )

        return chunks()


def _config(**overrides: Any) -> LLMClientConfig:
    """构造测试用客户端配置（默认 60 RPM、单次超时 3s、最多 3 次尝试）。"""
    values = dict(_BASE_CONFIG)
    values.update(overrides)
    return LLMClientConfig(**values)


def _request(
    *,
    task: str = "narrate",
    turn_id: int = 2,
    idempotency_key: str = "stream-turn-2",
    timeout_s: float = 3.0,
    max_repair: int = 1,
    output_schema: type[BaseModel] | None = NarrativeOutput,
    cache_ttl_s: int = 0,
) -> LLMRequest:
    """构造测试请求（叙事形态，与 ``LLMNarratorAgent`` 生产请求同构）。"""
    return LLMRequest(
        task=task,
        output_schema=output_schema,
        agent_id="narrator",
        turn_id=turn_id,
        idempotency_key=idempotency_key,
        model_tier=ModelTier.ECONOMY,
        messages=[ChatMessage(role="user", content="叙述这一回合")],
        timeout_s=timeout_s,
        cache_ttl_s=cache_ttl_s,
        max_repair=max_repair,
        prompt_version="narrate@v3",
    )


def _client(
    stream_transport: FakeStreamTransport,
    *,
    clock: FakeClock | None = None,
    calls: InMemoryLLMCallLog | None = None,
    config: LLMClientConfig | None = None,
    llm_config: LLMConfig | None = None,
) -> LiteLLMClient:
    """构造可断言的客户端：注入流式 transport/伪时钟/内存调用日志。"""
    fake_clock = clock or FakeClock()
    return LiteLLMClient(
        config or _config(),
        transport=lambda **kwargs: pytest.fail(f"非流式 transport 不应被调用: {kwargs}"),
        stream_transport=stream_transport,
        calls=calls or InMemoryLLMCallLog(),
        clock=fake_clock,
        sleep=fake_clock.sleep,
        llm_config=llm_config,
    )


def _fallback_llm_config() -> LLMConfig:
    """主供应商 + 一个回退供应商的分级配置（用于断言「首 chunk 后不换供应商」）。"""
    tiers = {
        tier: TierSpec(
            provider="primary",
            model="primary-model",
            api_base="https://primary.invalid",
            fallbacks=(FallbackSpec(provider="secondary", model="secondary-model"),),
        )
        for tier in ModelTier
    }
    return LLMConfig(
        providers={
            "primary": ProviderSpec(api_base="https://primary.invalid"),
            "secondary": ProviderSpec(api_base="https://secondary.invalid"),
        },
        tiers=tiers,
        cache=CacheSpec(),
        circuit=CircuitSpec(),
    )


def _deltas(collector: list[str]) -> DeltaCallback:
    """构造收集增量的 ``on_delta`` 回调。"""

    def collect(text: str) -> None:
        collector.append(text)

    return collect


# ---------------------------------------------------------------------------
# 成功路径 / 契约
# ---------------------------------------------------------------------------
def test_stream_success_delivers_deltas_and_full_content() -> None:
    """成功：逐 chunk 下发原始文本，结果含完整 content；记录 ``streamed=True``、attempt=1。"""
    transport = FakeStreamTransport(
        [['{"narrative": "', "你沿着北边的通道动身", '，脚下的石板缝里积着陈年的灰。"}']]
    )
    client = _client(transport)
    deltas: list[str] = []

    result = client.complete_json_stream(_request(), _deltas(deltas))

    assert result.ok is True
    assert result.data is not None
    content = result.data["content"]
    assert isinstance(content, str)
    assert "".join(deltas) == content
    assert json.loads(content)["narrative"] == "你沿着北边的通道动身，脚下的石板缝里积着陈年的灰。"
    call = result.data["call"]
    assert call.streamed is True
    assert call.attempt == 1
    assert call.error_code is None
    assert len(transport.calls) == 1


def test_stream_requires_output_schema() -> None:
    """缺 ``output_schema`` → INVALID_INPUT，且 0 次 transport。"""
    transport = FakeStreamTransport([["unused"]])
    client = _client(transport)

    result = client.complete_json_stream(_request(output_schema=None), _deltas([]))

    assert result.ok is False
    assert result.error_code is ErrorCode.INVALID_INPUT
    assert transport.calls == []


def test_stream_usage_from_last_chunk() -> None:
    """usage 取末 chunk（``stream_options.include_usage``），记录 prompt/completion tokens。"""
    transport = FakeStreamTransport(
        [["正文", {"text": "", "prompt_tokens": 88, "completion_tokens": 40}]]
    )
    client = _client(transport)

    result = client.complete_json_stream(_request(), _deltas([]))

    assert result.ok is True
    assert result.data is not None
    call = result.data["call"]
    assert call.prompt_tokens == 88
    assert call.completion_tokens == 40
    assert call.streamed is True


def test_stream_callback_exception_does_not_fail_call() -> None:
    """``on_delta`` 抛异常只记 WARNING：显示层故障不影响 LLM 调用结果/计量。"""
    transport = FakeStreamTransport([["正文"]])

    def broken(text: str) -> None:
        raise RuntimeError("client disconnected")

    result = _client(transport).complete_json_stream(_request(), broken)

    assert result.ok is True
    assert result.data is not None
    assert result.data["content"] == "正文"


# ---------------------------------------------------------------------------
# 首 chunk 前重试 / 首 chunk 后不重试
# ---------------------------------------------------------------------------
def test_pre_chunk_retryable_error_retries_and_succeeds() -> None:
    """首 chunk 前可重试错误 → tenacity 重试一次成功；stream transport 恰 2 次。"""
    clock = FakeClock()
    transport = FakeStreamTransport(
        [ProviderCallError("429", retryable=True, status_code=429), ["正文"]]
    )
    client = _client(transport, clock=clock)

    result = client.complete_json_stream(_request(), _deltas([]))

    assert result.ok is True
    assert result.data is not None
    assert result.data["call"].attempt == 2
    assert len(transport.calls) == 2
    assert len(clock.sleeps) == 1


def test_pre_chunk_non_retryable_error_returns_unavailable() -> None:
    """首 chunk 前不可重试错误（鉴权失败）→ 只调用 1 次、LLM_UNAVAILABLE。"""
    transport = FakeStreamTransport([ProviderCallError("auth", retryable=False, status_code=401)])
    client = _client(transport)

    result = client.complete_json_stream(_request(), _deltas([]))

    assert result.ok is False
    assert result.error_code is ErrorCode.LLM_UNAVAILABLE
    assert len(transport.calls) == 1


def test_pre_chunk_status_422_maps_invalid_input() -> None:
    """供应商 422（请求非法）→ INVALID_INPUT，且不重试。"""
    transport = FakeStreamTransport(
        [ProviderCallError("bad request", retryable=False, status_code=422)]
    )
    client = _client(transport)

    result = client.complete_json_stream(_request(), _deltas([]))

    assert result.ok is False
    assert result.error_code is ErrorCode.INVALID_INPUT
    assert len(transport.calls) == 1


def test_mid_stream_failure_not_retried_and_no_provider_switch() -> None:
    """首 chunk 后失败 → 不重试、不换供应商、已下发增量保留；返回 LLM_UNAVAILABLE。"""
    transport = FakeStreamTransport(
        [["你沿着北边", ProviderCallError("connection reset", retryable=True)]]
    )
    calls = InMemoryLLMCallLog()
    client = _client(transport, calls=calls, llm_config=_fallback_llm_config())
    deltas: list[str] = []

    result = client.complete_json_stream(_request(), _deltas(deltas))

    assert result.ok is False
    assert result.error_code is ErrorCode.LLM_UNAVAILABLE
    assert deltas == ["你沿着北边"]
    assert len(transport.calls) == 1  # 不重试、不换供应商
    assert transport.calls[0]["provider"] == "primary"
    assert calls.failures and calls.failures[-1].streamed is True


def test_idle_timeout_after_first_chunk_is_mid_stream_failure() -> None:
    """chunk 间空闲 > ``timeout_s``（伪时钟推进）→ 首 chunk 后失败，不重试。"""
    clock = FakeClock()

    def advance_clock() -> None:
        """脚本副作用：两次 chunk 之间推进伪时钟（模拟长时间无数据）。"""
        clock.advance(10.0)

    transport = FakeStreamTransport([["前半", advance_clock, "后半"]])
    client = _client(transport, clock=clock)
    deltas: list[str] = []

    result = client.complete_json_stream(_request(), _deltas(deltas))

    assert result.ok is False
    assert result.error_code is ErrorCode.LLM_UNAVAILABLE
    assert deltas == ["前半"]
    assert len(transport.calls) == 1


def test_deadline_exhaustion_stops_retries() -> None:
    """整链 deadline 耗尽后不再发起剩余重试（伪时钟随等待推进）。"""
    clock = FakeClock()
    transport = FakeStreamTransport(
        [ProviderCallError("timeout", retryable=True) for _ in range(3)]
    )
    client = _client(transport, clock=clock)

    result = client.complete_json_stream(_request(timeout_s=2.0), _deltas([]))

    assert result.ok is False
    assert result.error_code is ErrorCode.LLM_UNAVAILABLE
    assert len(transport.calls) < 3


# ---------------------------------------------------------------------------
# 缓存 / 幂等 / 限流
# ---------------------------------------------------------------------------
def test_same_idempotency_key_reuses_and_emits_once() -> None:
    """同幂等键第二次调用：不调 transport，完整内容一次 ``on_delta`` 下发（D-N2-4 ⑤）。"""
    transport = FakeStreamTransport([["正文"]])
    client = _client(transport)
    request = _request()

    first = client.complete_json_stream(request, _deltas([]))
    second_deltas: list[str] = []
    second = client.complete_json_stream(request, _deltas(second_deltas))

    assert first.ok is True and second.ok is True
    assert len(transport.calls) == 1
    assert second_deltas == ["正文"]


def test_cache_hit_emits_full_content_once() -> None:
    """进程内缓存命中：不调流式 transport，完整内容一次下发，记录 ``cache_hit/streamed``。"""
    from fakes import FakeTransport

    llm_config = load_llm_config().model_copy(
        update={"cache": CacheSpec(allowed_tasks=("narrate",))}
    )
    clock = FakeClock()
    non_stream = FakeTransport(['{"narrative": "缓存正文"}'])
    stream = FakeStreamTransport([["不应被调用"]])
    client = LiteLLMClient(
        _config(),
        transport=non_stream,
        stream_transport=stream,
        calls=InMemoryLLMCallLog(),
        clock=clock,
        sleep=clock.sleep,
        llm_config=llm_config,
    )
    request = _request(cache_ttl_s=60)
    assert client.complete_json(request).ok is True
    deltas: list[str] = []

    result = client.complete_json_stream(request, _deltas(deltas))

    assert result.ok is True
    assert stream.calls == []
    assert deltas == ['{"narrative": "缓存正文"}']
    assert result.data is not None
    assert result.data["call"].cache_hit is True
    assert result.data["call"].streamed is True


def test_in_flight_key_returns_unavailable_without_transport() -> None:
    """同键已在飞 → LLM_UNAVAILABLE，且不发起流式 transport（不重复扣费）。"""
    calls = InMemoryLLMCallLog()
    stream = FakeStreamTransport([["正文"]])
    client = _client(stream, calls=calls)
    request = _request()
    assert calls.try_begin(compose_idempotency_key(request)) is True

    result = client.complete_json_stream(request, _deltas([]))

    assert result.ok is False
    assert result.error_code is ErrorCode.LLM_UNAVAILABLE
    assert stream.calls == []
    calls.end(compose_idempotency_key(request))


def test_rpm_limit_blocks_stream_attempt_without_transport() -> None:
    """RPM=1：第二次（不同键）在令牌桶处被拒绝，transport 仍只有 1 次（不按 chunk 计费）。"""
    clock = FakeClock()
    stream = FakeStreamTransport([["一"], ["二"]])
    client = _client(stream, clock=clock, config=_config(rpm_limit=1))

    first = client.complete_json_stream(_request(idempotency_key="stream-key-1"), _deltas([]))
    second = client.complete_json_stream(_request(idempotency_key="stream-key-2"), _deltas([]))

    assert first.ok is True
    assert second.ok is False
    assert second.error_code is ErrorCode.LLM_UNAVAILABLE
    assert len(stream.calls) == 1


# ---------------------------------------------------------------------------
# providers 流式适配（monkeypatch litellm.completion，零网络）
# ---------------------------------------------------------------------------
class _FakeLiteLLMDelta:
    """模拟 litellm chunk 的 ``choices[0].delta.content``。"""

    def __init__(self, content: str | None) -> None:
        self.content = content


class _FakeLiteLLMChoice:
    """模拟 litellm chunk 的 choice。"""

    def __init__(self, content: str | None) -> None:
        self.delta = _FakeLiteLLMDelta(content)


class _FakeLiteLLMUsage:
    """模拟 litellm usage（仅末 chunk 出现）。"""

    def __init__(self) -> None:
        self.prompt_tokens = 11
        self.completion_tokens = 7


class _FakeLiteLLMChunk:
    """模拟 litellm 流式 chunk。"""

    def __init__(self, content: str | None, *, model: str = "fake-model", usage: bool = False):
        self.choices = [_FakeLiteLLMChoice(content)] if content is not None else []
        self.model = model
        self.usage = _FakeLiteLLMUsage() if usage else None


def test_chat_completion_stream_maps_chunks_and_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """providers 流式适配：``stream=True``/``include_usage``/``num_retries=0`` 且字段映射正确。"""
    import litellm

    from textgame.llm.providers import chat_completion_stream

    captured: dict[str, Any] = {}

    def fake_completion(**kwargs: Any) -> list[_FakeLiteLLMChunk]:
        captured.update(kwargs)
        return [
            _FakeLiteLLMChunk("你"),
            _FakeLiteLLMChunk("好"),
            _FakeLiteLLMChunk(None, usage=True),
        ]

    monkeypatch.setattr(litellm, "completion", fake_completion)

    chunks = list(
        chat_completion_stream(
            provider="deepseek",
            model="deepseek-flash",
            api_key="sk-test",
            api_base="https://api.deepseek.com",
            messages=[{"role": "user", "content": "hi"}],
            timeout_s=5.0,
        )
    )

    assert captured["stream"] is True
    assert captured["stream_options"] == {"include_usage": True}
    assert captured["num_retries"] == 0
    assert [chunk["text"] for chunk in chunks] == ["你", "好", ""]
    assert chunks[-1]["prompt_tokens"] == 11
    assert chunks[-1]["completion_tokens"] == 7


def test_chat_completion_stream_classifies_iteration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """迭代中供应商异常翻译为 ``ProviderCallError``（不向上抛裸 SDK 异常）。"""
    import litellm

    from textgame.llm.providers import chat_completion_stream

    class _FakeRateLimitError(Exception):
        status_code = 429

    def broken_stream(**kwargs: Any) -> Iterator[_FakeLiteLLMChunk]:
        del kwargs
        yield _FakeLiteLLMChunk("开头")
        raise _FakeRateLimitError("rate limited")

    monkeypatch.setattr(litellm, "completion", broken_stream)

    iterator = chat_completion_stream(
        provider="deepseek",
        model="deepseek-flash",
        api_key="sk-test",
        api_base="https://api.deepseek.com",
        messages=[{"role": "user", "content": "hi"}],
        timeout_s=5.0,
    )
    assert next(iterator)["text"] == "开头"
    with pytest.raises(ProviderCallError) as excinfo:
        next(iterator)
    assert excinfo.value.retryable is True


# ---------------------------------------------------------------------------
# RuntimeLLMClient 委托（热切换）
# ---------------------------------------------------------------------------
def _runtime(tmp_path: Path, **kwargs: Any) -> RuntimeLLMClient:
    """构造不读仓库 ``.env`` 的运行期客户端（凭据文件落在 tmp）。"""
    settings = Settings(
        _env_file=None,
        llm_credentials_file=tmp_path / "llm.credentials.json",
        **kwargs,
    )
    return RuntimeLLMClient(settings)


def test_runtime_offline_client_returns_unavailable_without_delta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """默认离线内部客户端不支持流式 → 退回 complete_json（失败）且不下发增量。"""
    monkeypatch.delenv("TEXTGAME_LLM_ENABLED", raising=False)
    client = _runtime(tmp_path)
    deltas: list[str] = []

    result = client.complete_json_stream(_request(), _deltas(deltas))

    assert result.ok is False
    assert result.error_code is ErrorCode.LLM_UNAVAILABLE
    assert deltas == []
    assert supports_streaming(client) is True


def test_runtime_delegates_to_inner_streaming_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """内部客户端支持流式（LiteLLMClient）时直接委托，增量原样透传。"""
    monkeypatch.delenv("TEXTGAME_LLM_ENABLED", raising=False)
    clock = FakeClock()
    stream = FakeStreamTransport([['{"narrative": "', "正文", '" }']])
    client = RuntimeLLMClient(
        Settings(
            _env_file=None,
            llm_credentials_file=tmp_path / "llm.credentials.json",
            llm_api_key=SecretStr("sk-dev-key-123456"),
        ),
        stream_transport=stream,
        clock=clock,
        sleep=clock.sleep,
    )
    deltas: list[str] = []

    result = client.complete_json_stream(_request(), _deltas(deltas))

    assert result.ok is True
    assert "".join(deltas) == '{"narrative": "正文" }'
    assert len(stream.calls) == 1
    assert stream.calls[0]["api_key"] == "sk-dev-key-123456"
