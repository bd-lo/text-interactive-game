"""单元测试：M5-020 ``model-tier-cache``（第 12 章 12.4/12.7；ADR D-M5-4）。

覆盖：
- JSON 分层配置：包内默认可加载、``TEXTGAME_LLM_CONFIG`` 路径覆盖、非法配置 fail-fast
  （缺文件/坏 JSON/未知字段/缺分级/未知 provider）；
- ``LiteLLMClient`` 按 ``model_tier`` 选择 provider/model/api_base（strong/economy）；
- 进程内缓存：默认 allowlist 为空不生效、``cache_ttl_s=0`` 不生效、命中不调 transport 且
  ``cache_hit=true``、失败不缓存、按注入时钟过期、线程安全；
- 工厂：``llm_enabled=false`` 不加载 JSON（坏路径也返回 DisabledLLMClient）、启用时
  坏配置 fail-fast、默认 JSON 正常构造。

全程零网络、零真实 sleep：transport 用 ``tests.fakes.FakeTransport``，时钟注入伪时钟；
``tests/conftest.py`` 的 socket 禁网 guard 仍为 autouse。
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from importlib import resources
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, SecretStr

from fakes import FakeTransport
from textgame.config.settings import Settings
from textgame.llm.cache import ResponseCache, cache_key
from textgame.llm.client import ChatMessage, DisabledLLMClient, LLMRequest, ModelTier
from textgame.llm.config import CacheSpec, LLMClientConfig, LLMConfig, load_llm_config
from textgame.llm.errors import ProviderCallError
from textgame.llm.factory import build_llm_client
from textgame.llm.litellm_client import LiteLLMClient
from textgame.llm.records import LLMCallEntry, LLMCallRecord

_API_KEY = "sk-test-secret-abcdefghijklmnop"


class SampleOutput(BaseModel):
    """结构化输出示例 Schema（``complete_json`` 必填 ``output_schema``）。"""

    answer: str


class FakeClock:
    """可推进的伪时钟（不真实等待）。"""

    def __init__(self, start: float = 1000.0) -> None:
        """从 ``start`` 秒开始计时。"""
        self.now = start

    def __call__(self) -> float:
        """返回当前伪时间（秒）。"""
        return self.now

    def advance(self, seconds: float) -> None:
        """推进伪时间。"""
        self.now += seconds


def _client_config(**overrides: Any) -> LLMClientConfig:
    """构造 019 兼容的客户端配置（超时 3s、3 次尝试、60 RPM）。"""
    values: dict[str, Any] = {
        "provider": "deepseek",
        "model": "default-model",
        "api_base": "https://default.example",
        "api_key": SecretStr(_API_KEY),
        "max_attempts": 3,
        "timeout_s": 3.0,
        "rpm_limit": 60,
    }
    values.update(overrides)
    return LLMClientConfig(**values)


def _llm_config(
    *,
    allowed_tasks: tuple[str, ...] = ("intent_parse",),
    strong_model: str = "strong-model",
    economy_model: str = "economy-model",
    cache_maxsize: int = 8,
    cache_ttl_s: int = 60,
) -> LLMConfig:
    """构造分层配置：strong 带 api_base 覆盖，economy 继承 providers 的 api_base。"""
    return LLMConfig.model_validate(
        {
            "providers": {"deepseek": {"api_base": "https://providers.example"}},
            "tiers": {
                "strong": {
                    "provider": "deepseek",
                    "model": strong_model,
                    "api_base": "https://strong.example",
                },
                "economy": {"provider": "deepseek", "model": economy_model},
            },
            "cache": {
                "maxsize": cache_maxsize,
                "ttl_s": cache_ttl_s,
                "allowed_tasks": list(allowed_tasks),
            },
        }
    )


def _request(
    *,
    tier: ModelTier = ModelTier.ECONOMY,
    task: str = "intent_parse",
    cache_ttl_s: int = 0,
    turn_id: int = 2,
    idempotency_key: str = "idem-2",
    messages: list[ChatMessage] | None = None,
    output_schema: type[BaseModel] | None = SampleOutput,
) -> LLMRequest:
    """构造测试请求；默认与经济档意图解析请求同构。"""
    return LLMRequest(
        task=task,
        output_schema=output_schema,
        agent_id="parse",
        turn_id=turn_id,
        idempotency_key=idempotency_key,
        model_tier=tier,
        messages=messages or [ChatMessage(role="user", content="向北走")],
        timeout_s=3.0,
        cache_ttl_s=cache_ttl_s,
        prompt_version="parse@v1",
    )


def _make_entry(index: int = 0) -> LLMCallEntry:
    """构造缓存条目（直接测 ``ResponseCache`` 线程安全时使用）。"""
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
    return LLMCallEntry(idempotency_key=f"key-{index}", content=f"content-{index}", record=record)


def _write_config(path: Path, payload: dict[str, Any]) -> None:
    """把配置字典写为 UTF-8 JSON 文件。"""
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _custom_payload(economy_model: str = "custom-economy") -> dict[str, Any]:
    """返回可被 ``LLMConfig`` 接受的自定义配置载荷。"""
    return {
        "providers": {"deepseek": {"api_base": "https://custom.example"}},
        "tiers": {
            "strong": {"provider": "deepseek", "model": "custom-strong"},
            "economy": {"provider": "deepseek", "model": economy_model},
        },
        "cache": {"maxsize": 4, "ttl_s": 30, "allowed_tasks": ["intent_parse"]},
    }


# ---------------------------------------------------------------------------
# 1) JSON 分层配置加载
# ---------------------------------------------------------------------------
def test_default_config_loads_from_package_resource() -> None:
    """包内默认 JSON 可加载：两个分级齐全、providers/api_base 正确、缓存默认关闭。"""
    config = load_llm_config()

    assert set(config.tiers) == {ModelTier.STRONG, ModelTier.ECONOMY}
    assert config.providers["deepseek"].api_base == "https://api.deepseek.com"
    assert config.cache.allowed_tasks == ()
    assert config.backend_for(ModelTier.STRONG) == (
        "deepseek",
        "deepseek-flash",
        "https://api.deepseek.com",
    )


def test_default_json_file_is_shipped_in_package() -> None:
    """``llm.default.json`` 作为包内资源随包分发（importlib.resources 可读）。"""
    resource = resources.files("textgame.llm.config").joinpath("llm.default.json")

    assert resource.is_file()
    assert json.loads(resource.read_text(encoding="utf-8"))["schema_version"] == 1


def test_config_path_override_is_used(tmp_path: Path) -> None:
    """``TEXTGAME_LLM_CONFIG`` 路径覆盖：economy 模型与 api_base 按覆盖文件解析。"""
    path = tmp_path / "llm.json"
    _write_config(path, _custom_payload())

    config = load_llm_config(path)

    assert config.backend_for(ModelTier.ECONOMY) == (
        "deepseek",
        "custom-economy",
        "https://custom.example",
    )
    assert config.cache.allowed_tasks == ("intent_parse",)


def test_missing_config_file_fails_fast(tmp_path: Path) -> None:
    """配置文件不存在 → ``RuntimeError``（构造期 fail-fast，不静默回退）。"""
    with pytest.raises(RuntimeError, match="读取失败"):
        load_llm_config(tmp_path / "missing.json")


def test_invalid_json_fails_fast(tmp_path: Path) -> None:
    """JSON 语法错误 → ``RuntimeError``。"""
    path = tmp_path / "bad.json"
    path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(RuntimeError, match="不是合法 JSON"):
        load_llm_config(path)


def test_unknown_field_fails_fast(tmp_path: Path) -> None:
    """未知字段（extra=forbid）→ ``RuntimeError``，避免配置漂移被静默忽略。"""
    payload = _custom_payload()
    payload["unexpected"] = True
    path = tmp_path / "extra.json"
    _write_config(path, payload)

    with pytest.raises(RuntimeError, match="配置非法"):
        load_llm_config(path)


def test_missing_tier_fails_fast(tmp_path: Path) -> None:
    """只声明 economy 而缺 strong → ``RuntimeError``。"""
    payload = _custom_payload()
    del payload["tiers"]["strong"]
    path = tmp_path / "missing-tier.json"
    _write_config(path, payload)

    with pytest.raises(RuntimeError, match="配置非法"):
        load_llm_config(path)


def test_unknown_provider_fails_fast(tmp_path: Path) -> None:
    """tier 引用未声明的 provider → ``RuntimeError``。"""
    payload = _custom_payload()
    payload["tiers"]["strong"]["provider"] = "ghost"
    path = tmp_path / "unknown-provider.json"
    _write_config(path, payload)

    with pytest.raises(RuntimeError, match="配置非法"):
        load_llm_config(path)


# ---------------------------------------------------------------------------
# 2) 模型分级选择
# ---------------------------------------------------------------------------
def test_client_selects_provider_model_and_api_base_by_tier() -> None:
    """strong/economy 各自使用 JSON 配置的 model 与 api_base（strong 覆盖 providers）。"""
    clock = FakeClock()
    transport = FakeTransport(["ok-strong", "ok-economy"])
    client = LiteLLMClient(
        _client_config(),
        transport=transport,
        clock=clock,
        sleep=lambda _seconds: None,
        llm_config=_llm_config(),
    )

    strong = client.complete_json(_request(tier=ModelTier.STRONG, idempotency_key="idem-strong"))
    economy = client.complete_json(_request(tier=ModelTier.ECONOMY, idempotency_key="idem-economy"))

    assert strong.ok is True and economy.ok is True
    assert transport.calls[0]["model"] == "strong-model"
    assert transport.calls[0]["api_base"] == "https://strong.example"
    assert strong.data is not None
    assert strong.data["call"].provider == "deepseek"
    assert strong.data["call"].model_tier is ModelTier.STRONG
    assert transport.calls[1]["model"] == "economy-model"
    assert transport.calls[1]["api_base"] == "https://providers.example"
    assert economy.data is not None
    assert economy.data["call"].model_tier is ModelTier.ECONOMY


def test_client_without_llm_config_keeps_single_model() -> None:
    """未注入 ``LLMConfig`` 时保持 019 行为：所有分级用 ``LLMClientConfig`` 单模型。"""
    transport = FakeTransport(["ok"])
    client = LiteLLMClient(_client_config(), transport=transport)

    result = client.complete_json(_request(tier=ModelTier.STRONG))

    assert result.ok is True
    assert transport.calls[0]["model"] == "default-model"
    assert transport.calls[0]["provider"] == "deepseek"
    assert transport.calls[0]["api_base"] == "https://default.example"


# ---------------------------------------------------------------------------
# 3) 进程内缓存
# ---------------------------------------------------------------------------
def test_cache_disabled_when_allowlist_empty() -> None:
    """allowlist 为空（默认）→ ``cache_ttl_s>0`` 也不缓存，两次都调 transport。"""
    clock = FakeClock()
    transport = FakeTransport(["first", "second"])
    client = _cached_client(transport, clock, allowed_tasks=())

    first = client.complete_json(_request(cache_ttl_s=60, turn_id=2, idempotency_key="idem-a"))
    second = client.complete_json(_request(cache_ttl_s=60, turn_id=3, idempotency_key="idem-b"))

    assert first.ok is True and second.ok is True
    assert len(transport.calls) == 2
    assert second.data is not None and second.data["call"].cache_hit is False


def test_cache_disabled_when_ttl_is_zero() -> None:
    """任务在 allowlist 但 ``cache_ttl_s=0`` → 不缓存，两次都调 transport。"""
    clock = FakeClock()
    transport = FakeTransport(["first", "second"])
    client = _cached_client(transport, clock, allowed_tasks=("intent_parse",))

    client.complete_json(_request(cache_ttl_s=0, turn_id=2, idempotency_key="idem-a"))
    client.complete_json(_request(cache_ttl_s=0, turn_id=3, idempotency_key="idem-b"))

    assert len(transport.calls) == 2


def test_cache_hit_skips_transport_and_sets_cache_hit() -> None:
    """同内容第二次请求命中缓存：transport 仍 1 次、``cache_hit=true``、内容复用。"""
    clock = FakeClock()
    transport = FakeTransport(["cached-content"])
    client = _cached_client(transport, clock, allowed_tasks=("intent_parse",))

    first = client.complete_json(_request(cache_ttl_s=60, turn_id=2, idempotency_key="idem-a"))
    second = client.complete_json(_request(cache_ttl_s=60, turn_id=3, idempotency_key="idem-b"))

    assert first.ok is True and second.ok is True
    assert len(transport.calls) == 1
    assert second.data is not None
    assert second.data["content"] == "cached-content"
    assert second.data["call"].cache_hit is True
    assert second.data["call"].latency_ms == 0
    assert first.data is not None and first.data["call"].cache_hit is False


def test_cache_hit_checked_before_idempotency_reuse() -> None:
    """同请求第二次调用返回缓存命中记录（``cache_hit=true``），而非仅幂等复用。"""
    clock = FakeClock()
    transport = FakeTransport(["once"])
    client = _cached_client(transport, clock, allowed_tasks=("intent_parse",))
    request = _request(cache_ttl_s=60)

    first = client.complete_json(request)
    second = client.complete_json(request)

    assert first.ok is True and second.ok is True
    assert len(transport.calls) == 1
    assert second.data is not None and second.data["call"].cache_hit is True


def test_cache_does_not_store_failures() -> None:
    """失败响应不缓存：第二次同内容请求会重新调 transport。"""
    clock = FakeClock()
    transport = FakeTransport([ProviderCallError("boom", retryable=False), "ok"])
    client = _cached_client(transport, clock, allowed_tasks=("intent_parse",))

    failed = client.complete_json(_request(cache_ttl_s=60, turn_id=2, idempotency_key="idem-a"))
    recovered = client.complete_json(_request(cache_ttl_s=60, turn_id=3, idempotency_key="idem-b"))

    assert failed.ok is False
    assert recovered.ok is True
    assert len(transport.calls) == 2


def test_cache_entry_expires_after_request_ttl() -> None:
    """条目在 ``cache_ttl_s`` 到期后失效：推进伪时钟超过 TTL 再次调 transport。"""
    clock = FakeClock()
    transport = FakeTransport(["first", "second"])
    client = _cached_client(transport, clock, allowed_tasks=("intent_parse",))

    client.complete_json(_request(cache_ttl_s=5, turn_id=2, idempotency_key="idem-a"))
    clock.advance(6)
    second = client.complete_json(_request(cache_ttl_s=5, turn_id=3, idempotency_key="idem-b"))

    assert len(transport.calls) == 2
    assert second.ok is True
    assert second.data is not None and second.data["call"].cache_hit is False


def test_cache_key_ignores_turn_and_idempotency_but_not_messages() -> None:
    """缓存键只由内容字段决定：turn/幂等键/ttl 不同仍同键；消息不同则异键。"""
    base = _request(turn_id=2, idempotency_key="idem-a", cache_ttl_s=60)
    same_content = _request(turn_id=9, idempotency_key="idem-b", cache_ttl_s=1)
    other_message = _request(
        turn_id=2,
        idempotency_key="idem-a",
        messages=[ChatMessage(role="user", content="向南走")],
    )

    assert cache_key(base) == cache_key(same_content)
    assert cache_key(base) != cache_key(other_message)


def test_response_cache_is_thread_safe() -> None:
    """并发 put/get 不抛异常、条目数不超过 maxsize（TTLCache 访问已加锁）。"""
    spec = CacheSpec(maxsize=32, ttl_s=60, allowed_tasks=("intent_parse",))
    cache = ResponseCache(spec)

    def worker(worker_index: int) -> None:
        for index in range(40):
            key = f"key-{worker_index}-{index}"
            cache.put(key, _make_entry(index), ttl_s=60)
            cache.get(key)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(worker, range(8)))

    assert 0 < len(cache) <= 32


# ---------------------------------------------------------------------------
# 4) 工厂与 Settings 开关
# ---------------------------------------------------------------------------
def test_factory_disabled_does_not_load_json_config(tmp_path: Path) -> None:
    """``llm_enabled=false`` 时即使配置路径非法也不加载 JSON，返回 DisabledLLMClient。"""
    settings = Settings(
        _env_file=None,
        llm_enabled=False,
        llm_config_path=tmp_path / "broken.json",
    )

    client = build_llm_client(settings)

    assert isinstance(client, DisabledLLMClient)


def test_factory_enabled_with_invalid_config_fails_fast(tmp_path: Path) -> None:
    """``llm_enabled=true`` 且配置 JSON 非法 → 构造期 ``RuntimeError``（不做静默降级）。"""
    settings = Settings(
        _env_file=None,
        llm_enabled=True,
        llm_api_key=SecretStr(_API_KEY),
        llm_config_path=tmp_path / "missing.json",
    )

    with pytest.raises(RuntimeError, match="LLM 配置"):
        build_llm_client(settings)


def test_factory_enabled_default_json_builds_tiered_client() -> None:
    """启用 + 密钥 + 默认 JSON：工厂按 tiers 选择 economy 模型（FakeTransport 可离线验证）。"""
    transport = FakeTransport(["ok"])
    settings = Settings(
        _env_file=None,
        llm_enabled=True,
        llm_api_key=SecretStr(_API_KEY),
    )

    client = build_llm_client(settings, transport=transport)
    result = client.complete_json(_request(tier=ModelTier.ECONOMY))

    assert isinstance(client, LiteLLMClient)
    assert result.ok is True
    assert transport.calls[0]["model"] == "deepseek-flash"
    assert transport.calls[0]["provider"] == "deepseek"


def test_factory_override_config_path_selects_custom_models(tmp_path: Path) -> None:
    """工厂读取 ``Settings.llm_config_path`` 覆盖文件：strong 使用自定义模型名。"""
    path = tmp_path / "llm.json"
    _write_config(path, _custom_payload())
    transport = FakeTransport(["ok"])
    settings = Settings(
        _env_file=None,
        llm_enabled=True,
        llm_api_key=SecretStr(_API_KEY),
        llm_config_path=path,
    )

    client = build_llm_client(settings, transport=transport)
    client.complete_json(_request(tier=ModelTier.STRONG))

    assert transport.calls[0]["model"] == "custom-strong"
    assert transport.calls[0]["api_base"] == "https://custom.example"


def test_settings_reads_textgame_llm_config_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``TEXTGAME_LLM_CONFIG`` 环境变量映射到 ``Settings.llm_config_path``。"""
    monkeypatch.setenv("TEXTGAME_LLM_CONFIG", str(tmp_path / "env.json"))

    settings = Settings(_env_file=None)

    assert settings.llm_config_path == tmp_path / "env.json"


def _cached_client(
    transport: FakeTransport,
    clock: FakeClock,
    *,
    allowed_tasks: tuple[str, ...],
) -> LiteLLMClient:
    """构造启用缓存的客户端（缓存 TTL 60s、allowlist 由用例指定）。"""
    return LiteLLMClient(
        _client_config(),
        transport=transport,
        clock=clock,
        sleep=lambda _seconds: None,
        llm_config=_llm_config(allowed_tasks=allowed_tasks),
    )
