"""M5-024：供应商回退链 + 熔断的离线验收（第 12 章 §12.4/§12.8；FakeTransport + 伪时钟）。

覆盖：主供应商重试耗尽 → 按配置顺序回退成功；全链失败 → ``LLM_UNAVAILABLE``（不抛裸异常）；
400/422 不回退、401 不回退；429/超时回退；连续失败达阈值 → open 后跳过该供应商；
open 到期 → half-open 单次探测成功恢复 / 失败重新 open；错误率阈值（窗口 + 最小样本）与
窗口过期剪枝；默认配置（无回退）行为与 019/020 一致；配置非法 fail-fast。
所有用例零网络（FakeTransport）、零真实 sleep（``sleep`` 注入为空操作 + 伪时钟）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from fakes import FakeTransport
from textgame.domain.results import ErrorCode
from textgame.llm.client import ChatMessage, LLMRequest, ModelTier
from textgame.llm.config import CircuitSpec, LLMClientConfig, LLMConfig, load_llm_config
from textgame.llm.errors import ProviderCallError
from textgame.llm.litellm_client import LiteLLMClient
from textgame.llm.records import InMemoryLLMCallLog

pytestmark = pytest.mark.integration

_PROVIDERS: dict[str, dict[str, str]] = {
    "p1": {"api_base": "https://p1.test"},
    "p2": {"api_base": "https://p2.test"},
    "p3": {"api_base": "https://p3.test"},
}


class _Clock:
    """可推进的伪单调时钟（秒）；配合 ``sleep=noop`` 完全脱离真实时间。"""

    def __init__(self) -> None:
        """从 0 秒开始。"""
        self.now = 0.0

    def __call__(self) -> float:
        """返回当前假时间。"""
        return self.now

    def advance(self, seconds: float) -> None:
        """推进假时间（模拟窗口/open 时长流逝）。"""
        self.now += seconds


def _config(**overrides: Any) -> LLMClientConfig:
    """客户端基础配置（主供应商由 ``LLMConfig`` 决定，此处仅超时/重试/RPM/密钥）。"""
    values: dict[str, Any] = {
        "provider": "p1",
        "model": "model-p1",
        "api_base": "https://p1.test",
        "api_key": SecretStr("sk-test-offline"),
        "max_attempts": 3,
        "timeout_s": 20.0,
        "rpm_limit": 600,
    }
    values.update(overrides)
    return LLMClientConfig(**values)


def _llm_config(
    *,
    fallbacks: tuple[dict[str, str], ...] = (),
    circuit: dict[str, Any] | None = None,
) -> LLMConfig:
    """构造带尝试链的 ``LLMConfig``（两个 tier 用同一主供应商，便于断言回退顺序）。"""
    payload: dict[str, Any] = {
        "providers": _PROVIDERS,
        "tiers": {
            ModelTier.STRONG.value: {
                "provider": "p1",
                "model": "strong-p1",
                "fallbacks": list(fallbacks),
            },
            ModelTier.ECONOMY.value: {
                "provider": "p1",
                "model": "economy-p1",
                "fallbacks": list(fallbacks),
            },
        },
    }
    if circuit is not None:
        payload["circuit"] = circuit
    return LLMConfig.model_validate(payload)


def _request(*, key: str = "idem-fallback-0001", tier: ModelTier = ModelTier.ECONOMY) -> LLMRequest:
    """纯文本请求（回退/熔断与结构化路径共用同一 ``_execute``）。"""
    return LLMRequest(
        task="narrate",
        output_schema=None,
        agent_id="narrator",
        turn_id=2,
        idempotency_key=key,
        model_tier=tier,
        messages=[ChatMessage(role="user", content="描述四周的环境")],
    )


def _client(
    transport: FakeTransport,
    *,
    llm_config: LLMConfig | None = None,
    clock: _Clock | None = None,
    calls: InMemoryLLMCallLog | None = None,
) -> LiteLLMClient:
    """构造零网络、零真实 sleep 的客户端（``sleep`` 为空操作，时间由 ``clock`` 控制）。"""
    return LiteLLMClient(
        _config(),
        transport=transport,
        calls=calls or InMemoryLLMCallLog(),
        clock=clock or _Clock(),
        sleep=lambda _seconds: None,
        llm_config=llm_config,
    )


def _timeout() -> ProviderCallError:
    """可重试超时错误（第 12 章 12.3：tenacity 重试 ≤3 次）。"""
    return ProviderCallError("timeout", retryable=True, status_code=504)


def _providers(transport: FakeTransport) -> list[str]:
    """本次脚本真实调用过的 provider 顺序。"""
    return [str(call["provider"]) for call in transport.calls]


# ---------------------------------------------------------------------------
# 回退链：顺序 / 全失败 / 错误分类
# ---------------------------------------------------------------------------
def test_primary_exhausted_falls_back_in_config_order() -> None:
    """主供应商重试 3 次耗尽 → 回退到 p2 成功；记录/日志使用实际后端 p2。"""
    transport = FakeTransport([_timeout(), _timeout(), _timeout(), "ok-from-p2"])
    client = _client(
        transport, llm_config=_llm_config(fallbacks=({"provider": "p2", "model": "m2"},))
    )

    result = client.complete_text(_request())

    assert result.ok is True
    assert result.data is not None and result.data["content"] == "ok-from-p2"
    assert _providers(transport) == ["p1", "p1", "p1", "p2"]
    call = result.data["call"]
    assert call.provider == "p2" and call.model == "fake-model"
    assert call.error_code is None


def test_all_backends_failed_returns_llm_unavailable_without_raising() -> None:
    """全链（主 + 回退）都失败 → LLM_UNAVAILABLE + 失败记录，不抛裸供应商异常。"""
    transport = FakeTransport([_timeout() for _ in range(6)])
    calls = InMemoryLLMCallLog()
    client = _client(
        transport,
        llm_config=_llm_config(fallbacks=({"provider": "p2", "model": "m2"},)),
        calls=calls,
    )

    result = client.complete_text(_request())

    assert result.ok is False
    assert result.error_code is ErrorCode.LLM_UNAVAILABLE
    assert _providers(transport) == ["p1", "p1", "p1", "p2", "p2", "p2"]
    assert len(calls.failures) == 1
    assert calls.failures[0].error_code is ErrorCode.LLM_UNAVAILABLE


def test_retryable_429_also_falls_back() -> None:
    """429（可重试）同样触发回退；不做三层重试叠加。"""
    transport = FakeTransport(
        [
            ProviderCallError("429", retryable=True, status_code=429),
            ProviderCallError("429", retryable=True, status_code=429),
            ProviderCallError("429", retryable=True, status_code=429),
            "ok",
        ]
    )
    client = _client(
        transport, llm_config=_llm_config(fallbacks=({"provider": "p2", "model": "m2"},))
    )

    result = client.complete_text(_request())

    assert result.ok is True
    assert _providers(transport) == ["p1", "p1", "p1", "p2"]


def test_non_retryable_401_does_not_fall_back() -> None:
    """鉴权/配额类不可重试错误不换供应商（换一家同样无效），直接 LLM_UNAVAILABLE。"""
    transport = FakeTransport([ProviderCallError("auth", retryable=False, status_code=401)])
    client = _client(
        transport, llm_config=_llm_config(fallbacks=({"provider": "p2", "model": "m2"},))
    )

    result = client.complete_text(_request())

    assert result.ok is False
    assert result.error_code is ErrorCode.LLM_UNAVAILABLE
    assert _providers(transport) == ["p1"]


def test_non_retryable_400_maps_to_invalid_input_without_fallback() -> None:
    """400/422 属调用方输入问题 → INVALID_INPUT，不重试也不回退（第 12 章 12.9）。"""
    transport = FakeTransport([ProviderCallError("bad request", retryable=False, status_code=400)])
    client = _client(
        transport, llm_config=_llm_config(fallbacks=({"provider": "p2", "model": "m2"},))
    )

    result = client.complete_text(_request())

    assert result.error_code is ErrorCode.INVALID_INPUT
    assert _providers(transport) == ["p1"]


def test_fallback_chain_visits_multiple_fallbacks_in_order() -> None:
    """两家回退：p2 也失败 → 继续 p3；provider 顺序 = p1×3 → p2×3 → p3。"""
    transport = FakeTransport([_timeout() for _ in range(6)] + ["ok-from-p3"])
    client = _client(
        transport,
        llm_config=_llm_config(
            fallbacks=({"provider": "p2", "model": "m2"}, {"provider": "p3", "model": "m3"})
        ),
    )

    result = client.complete_text(_request())

    assert result.ok is True
    assert _providers(transport) == ["p1", "p1", "p1", "p2", "p2", "p2", "p3"]


# ---------------------------------------------------------------------------
# 连续失败 → 熔断 open → 跳过 → half-open
# ---------------------------------------------------------------------------
def _open_primary_circuit(
    transport: FakeTransport, clock: _Clock, *, failures: int = 1
) -> LiteLLMClient:
    """把 p1 的连续失败计数推到阈值（默认 circuit 连续 5 次太慢，测试用 1–2 次）。"""
    client = _client(
        transport,
        llm_config=_llm_config(
            fallbacks=({"provider": "p2", "model": "m2"},),
            circuit={"consecutive_failures": failures, "open_s": 60, "window_s": 60},
        ),
        clock=clock,
    )
    for index in range(failures):
        transport.reset([_timeout(), _timeout(), _timeout(), "ok-from-p2"])
        assert client.complete_text(_request(key=f"idem-circuit-fill-{index:04d}")).ok
    return client


def test_open_circuit_skips_provider_and_uses_fallback() -> None:
    """p1 连续失败达到阈值 → open；后续请求跳过 p1，直接打 p2。"""
    clock = _Clock()
    transport = FakeTransport()
    client = _open_primary_circuit(transport, clock, failures=2)

    transport.reset(["ok-from-p2"])
    result = client.complete_text(_request(key="idem-circuit-skip-0001"))

    assert result.ok is True
    assert _providers(transport) == ["p2"]  # open 期跳过 p1，不发起任何 p1 调用


def test_half_open_probe_success_closes_circuit() -> None:
    """open 到期 → half-open 放行 1 次探测；探测成功 → 关闭，后续恢复走 p1。"""
    clock = _Clock()
    transport = FakeTransport()
    client = _open_primary_circuit(transport, clock, failures=2)

    clock.advance(61.0)  # open_s 到期
    transport.reset(["ok-from-p1-probe"])
    probe = client.complete_text(_request(key="idem-circuit-probe-ok-0001"))
    assert probe.ok is True
    assert _providers(transport) == ["p1"]

    transport.reset(["ok-from-p1-normal"])
    normal = client.complete_text(_request(key="idem-circuit-normal-0001"))
    assert normal.ok is True
    assert _providers(transport) == ["p1"]  # 已恢复 closed，重新使用主供应商


def test_half_open_probe_failure_reopens_circuit() -> None:
    """half-open 探测失败 → 重新 open；紧接着的请求继续跳过 p1 走 p2。"""
    clock = _Clock()
    transport = FakeTransport()
    client = _open_primary_circuit(transport, clock, failures=1)

    clock.advance(61.0)
    transport.reset([_timeout(), _timeout(), _timeout(), "ok-from-p2"])
    probe = client.complete_text(_request(key="idem-circuit-probe-fail-0001"))
    assert probe.ok is True
    assert _providers(transport) == ["p1", "p1", "p1", "p2"]  # 探测失败后回退 p2

    transport.reset(["ok-from-p2"])
    after = client.complete_text(_request(key="idem-circuit-after-fail-0001"))
    assert after.ok is True
    assert _providers(transport) == ["p2"]  # 重新 open，跳过 p1


def test_error_rate_threshold_opens_circuit() -> None:
    """连续失败阈值放宽到 99 后，错误率规则生效：窗口内 4 次全失败 → open。

    只统计「逻辑调用」结果（每次调用含 3 次供应商尝试），窗口内 ≥4 样本才评估错误率，
    避免单次失败即熔断；这里第 4 次失败后触发 open。
    """
    clock = _Clock()
    transport = FakeTransport()
    client = _client(
        transport,
        llm_config=_llm_config(
            fallbacks=({"provider": "p2", "model": "m2"},),
            circuit={
                "consecutive_failures": 99,
                "error_rate_threshold": 0.5,
                "window_s": 60,
                "open_s": 60,
            },
        ),
        clock=clock,
    )
    for index in range(4):
        transport.reset([_timeout(), _timeout(), _timeout(), "ok-from-p2"])
        assert client.complete_text(_request(key=f"idem-rate-fill-{index:04d}")).ok
        assert _providers(transport)[0] == "p1"  # 前 4 次 p1 仍被尝试

    transport.reset(["ok-from-p2"])
    result = client.complete_text(_request(key="idem-rate-open-0001"))

    assert result.ok is True
    assert _providers(transport) == ["p2"]  # 错误率熔断 → 跳过 p1


def test_window_expiry_prunes_old_failures() -> None:
    """窗口过期后旧失败被剪枝：3 次失败（<4 样本）不 open，时间流逝后再失败也不触发。"""
    clock = _Clock()
    transport = FakeTransport()
    client = _client(
        transport,
        llm_config=_llm_config(
            fallbacks=({"provider": "p2", "model": "m2"},),
            circuit={
                "consecutive_failures": 99,
                "error_rate_threshold": 0.5,
                "window_s": 60,
                "open_s": 60,
            },
        ),
        clock=clock,
    )
    for index in range(3):
        transport.reset([_timeout(), _timeout(), _timeout(), "ok-from-p2"])
        assert client.complete_text(_request(key=f"idem-window-a-{index:04d}")).ok

    clock.advance(61.0)  # 窗口过期：旧 3 条失败不再参与错误率统计

    transport.reset([_timeout(), _timeout(), _timeout(), "ok-from-p2"])
    assert client.complete_text(_request(key="idem-window-b-0001")).ok
    assert _providers(transport)[0] == "p1"  # 未熔断，仍先尝试 p1


# ---------------------------------------------------------------------------
# 默认配置 / 配置校验
# ---------------------------------------------------------------------------
def test_default_config_has_no_fallback_and_legacy_behavior() -> None:
    """包内默认配置：两 tier 均无回退、熔断参数为 12.8 口径；无 llm_config 时保持 019 行为。"""
    config = load_llm_config()
    assert config.circuit == CircuitSpec()
    assert config.backends_for(ModelTier.STRONG) == (config.backend_for(ModelTier.STRONG),)
    assert config.backends_for(ModelTier.ECONOMY) == (config.backend_for(ModelTier.ECONOMY),)
    transport = FakeTransport([_timeout(), _timeout(), _timeout(), "ok"])
    client = _client(transport)  # 不注入 llm_config = 019 兼容路径

    failed = client.complete_text(_request(key="idem-legacy-fail-0001"))
    assert failed.error_code is ErrorCode.LLM_UNAVAILABLE
    assert _providers(transport) == ["p1", "p1", "p1"]

    transport.reset(["ok-legacy"])
    ok = client.complete_text(_request(key="idem-legacy-ok-0001"))
    assert ok.ok is True and ok.data is not None and ok.data["content"] == "ok-legacy"


def test_invalid_fallback_provider_fails_fast(tmp_path: Path) -> None:
    """配置非法（回退 provider 未声明）→ ``LLMConfig``/``load_llm_config`` fail-fast。"""
    with pytest.raises(ValidationError):
        LLMConfig.model_validate(
            {
                "providers": {"p1": {"api_base": "https://p1.test"}},
                "tiers": {
                    "strong": {
                        "provider": "p1",
                        "model": "m1",
                        "fallbacks": [{"provider": "unknown", "model": "m"}],
                    },
                    "economy": {"provider": "p1", "model": "m1"},
                },
            }
        )

    bad_path = tmp_path / "llm.json"
    bad_path.write_text(
        json.dumps(
            {
                "providers": {"p1": {"api_base": "https://p1.test"}},
                "tiers": {
                    "strong": {
                        "provider": "p1",
                        "model": "m1",
                        "fallbacks": [{"provider": "unknown", "model": "m"}],
                    },
                    "economy": {"provider": "p1", "model": "m1"},
                },
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="LLM 配置非法"):
        load_llm_config(bad_path)


def test_invalid_circuit_threshold_fails_fast() -> None:
    """熔断参数越界（错误率 >1 / 探测次数为 0）→ 配置期 ValidationError。"""
    for circuit in (
        {"error_rate_threshold": 1.5},
        {"half_open_probes": 0},
        {"consecutive_failures": 0},
    ):
        with pytest.raises(ValidationError):
            LLMConfig.model_validate(
                {
                    "providers": {"p1": {"api_base": "https://p1.test"}},
                    "tiers": {
                        "strong": {"provider": "p1", "model": "m1"},
                        "economy": {"provider": "p1", "model": "m1"},
                    },
                    "circuit": circuit,
                }
            )
