"""LLM 运行期配置解析与可热切换客户端（本地开发免输入 / 干净克隆网页输入）。

**优先级**（本模块即口径；与 ``llm/credentials.py`` 配套）：

1. ``TEXTGAME_LLM_ENABLED=false`` 显式关闭 → 永远离线（保持 D-M5-6 的「一处开关」语义）；
2. 环境变量 / ``.env`` 的 ``TEXTGAME_LLM_API_KEY``（**开发 key**）→ 直接启用，玩家无需输入；
3. 本机 ``data/llm.credentials.json``（网页/终端里玩家输入并保存）→ 启用；
4. 都没有 → 离线（``DisabledLLMClient``），网页设置页提示玩家填入 DeepSeek API Key。

这样：本地开发机（仓库根有 ``.env``）开箱即用开发 key；``git clone`` 后没有 ``.env`` 的干净环境
会走「未配置 → 去设置页填 key」，填完无需重启即可生效（``RuntimeLLMClient`` 热切换内部客户端）。

实现要点：

- 组合根把**同一个** ``RuntimeLLMClient`` 实例注入所有 Agent（解析/叙事/角色/裁判/制造/卡生成），
  热切换只换内部委托对象，Agent 不需要重建；
- 未配置时行为与原默认完全一致（``DisabledLLMClient`` → 各 Agent 确定性回退），旧 golden/demo/
  console 逐字不变；
- 运行时自定义的 provider/model/api_base 会覆盖 JSON 分级配置的**主后端**（保留缓存/熔断参数），
  否则玩家在网页换模型不会生效；
- 密钥只在本机文件与内存中流转：状态接口只返回打码 key，日志不含明文。
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import SecretStr

from textgame.config.settings import Settings
from textgame.llm.client import (
    ChatMessage,
    DisabledLLMClient,
    LLMClient,
    LLMRequest,
    ModelTier,
)
from textgame.llm.config import (
    LLMClientConfig,
    LLMConfig,
    ProviderSpec,
    TierSpec,
    load_llm_config,
)
from textgame.llm.credentials import (
    CredentialStoreError,
    LocalLLMCredentialStore,
    StoredLLMCredentials,
    mask_api_key,
)
from textgame.llm.litellm_client import LiteLLMClient, LLMTransport
from textgame.llm.records import LLMCallLog

__all__ = [
    "LLMRuntimeStatus",
    "LLMSource",
    "RuntimeLLMClient",
]

_LOGGER = logging.getLogger(__name__)

#: 连接测试用的最小请求（只验证网络/鉴权/协议，不写入任何游戏状态）。
_TEST_PROMPT = "请只回复两个字母：OK"
_TEST_TIMEOUT_S = 15.0
_TEST_TASK = "settings.test"
_TEST_AGENT = "settings"


class LLMSource(str, Enum):
    """当前生效的配置来源。"""

    ENV = "env"
    USER = "user"
    NONE = "none"


@dataclass(frozen=True)
class LLMRuntimeStatus:
    """``GET /api/v1/settings/llm`` 的权威状态（不含任何明文密钥）。"""

    configured: bool
    enabled: bool
    source: str
    provider: str
    model: str
    api_base: str
    masked_key: str | None
    has_saved_user_key: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        """转成可直接返回给前端的 JSON 形状（键固定）。"""
        return {
            "configured": self.configured,
            "enabled": self.enabled,
            "source": self.source,
            "provider": self.provider,
            "model": self.model,
            "api_base": self.api_base,
            "masked_key": self.masked_key,
            "has_saved_user_key": self.has_saved_user_key,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class _ResolvedConfig:
    """一次解析得到的可用配置（仅在内存中短暂存在，含明文 key）。"""

    provider: str
    model: str
    api_base: str
    api_key: str


class RuntimeLLMClient:
    """实现 ``LLMClient`` 协议的可热切换客户端（默认离线；配置后立即生效）。"""

    def __init__(
        self,
        settings: Settings,
        *,
        store: LocalLLMCredentialStore | None = None,
        transport: LLMTransport | None = None,
        calls: LLMCallLog | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """解析一次配置并构造内部客户端；``transport``/``clock``/``sleep`` 供测试注入。"""
        self._settings = settings
        self._store = store or LocalLLMCredentialStore(settings.llm_credentials_file)
        self._transport = transport
        self._calls = calls
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.RLock()
        self._inner: LLMClient = DisabledLLMClient()
        self._status = LLMRuntimeStatus(
            configured=False,
            enabled=False,
            source=LLMSource.NONE.value,
            provider=settings.llm_provider,
            model=settings.llm_model,
            api_base=settings.llm_base_url,
            masked_key=None,
            has_saved_user_key=False,
            detail="正在读取 LLM 配置",
        )
        self.refresh()

    # ------------------------------------------------------------------
    # 状态与配置解析
    # ------------------------------------------------------------------
    @property
    def store(self) -> LocalLLMCredentialStore:
        """本机凭据存储（接入层保存/清除用）。"""
        return self._store

    def status(self) -> LLMRuntimeStatus:
        """返回当前状态快照（不含明文密钥）。"""
        with self._lock:
            return self._status

    def _explicit_enabled(self) -> bool | None:
        """``TEXTGAME_LLM_ENABLED`` 是否被显式配置（``None`` = 未配置，走 key 自动判定）。"""
        if "llm_enabled" in self._settings.model_fields_set:
            return self._settings.llm_enabled
        return None

    def _env_api_key(self) -> str | None:
        """``.env``/环境变量里的开发 key（去空白；无则 ``None``）。"""
        secret = self._settings.llm_api_key
        if secret is None:
            return None
        value = secret.get_secret_value().strip()
        return value or None

    def _saved_credentials(self) -> StoredLLMCredentials | None:
        """本机保存的凭据（损坏/不存在 → ``None``）。"""
        try:
            return self._store.load()
        except CredentialStoreError:
            return None

    def _resolve(self) -> tuple[LLMRuntimeStatus, _ResolvedConfig | None]:
        """按优先级解析「状态 + 可用配置」；``resolved=None`` 表示离线。"""
        explicit = self._explicit_enabled()
        env_key = self._env_api_key()
        saved = self._saved_credentials()
        has_saved = saved is not None
        env_config = (
            _ResolvedConfig(
                provider=self._settings.llm_provider,
                model=self._settings.llm_model,
                api_base=self._settings.llm_base_url,
                api_key=env_key,
            )
            if env_key is not None
            else None
        )
        user_config = (
            _ResolvedConfig(
                provider=saved.provider,
                model=saved.model,
                api_base=saved.api_base,
                api_key=saved.api_key,
            )
            if saved is not None
            else None
        )
        chosen: _ResolvedConfig | None = env_config or user_config
        if explicit is False:
            display = chosen
            return (
                LLMRuntimeStatus(
                    configured=chosen is not None,
                    enabled=False,
                    source=(chosen and (LLMSource.ENV if env_config else LLMSource.USER))
                    or LLMSource.NONE,
                    provider=(display.provider if display else self._settings.llm_provider),
                    model=(display.model if display else self._settings.llm_model),
                    api_base=(display.api_base if display else self._settings.llm_base_url),
                    masked_key=mask_api_key(display.api_key if display else None),
                    has_saved_user_key=has_saved,
                    detail="已显式关闭大模型（TEXTGAME_LLM_ENABLED=false）：当前为离线模板叙事",
                ),
                None,
            )
        if env_config is not None:
            return (
                LLMRuntimeStatus(
                    configured=True,
                    enabled=True,
                    source=LLMSource.ENV.value,
                    provider=env_config.provider,
                    model=env_config.model,
                    api_base=env_config.api_base,
                    masked_key=mask_api_key(env_config.api_key),
                    has_saved_user_key=has_saved,
                    detail="使用开发环境配置（.env 的 TEXTGAME_LLM_API_KEY）：本地开发免输入",
                ),
                env_config,
            )
        if user_config is not None:
            return (
                LLMRuntimeStatus(
                    configured=True,
                    enabled=True,
                    source=LLMSource.USER.value,
                    provider=user_config.provider,
                    model=user_config.model,
                    api_base=user_config.api_base,
                    masked_key=mask_api_key(user_config.api_key),
                    has_saved_user_key=has_saved,
                    detail="使用本机保存的 API Key（网页设置里输入）",
                ),
                user_config,
            )
        return (
            LLMRuntimeStatus(
                configured=False,
                enabled=False,
                source=LLMSource.NONE.value,
                provider=self._settings.llm_provider,
                model=self._settings.llm_model,
                api_base=self._settings.llm_base_url,
                masked_key=None,
                has_saved_user_key=False,
                detail=(
                    "未配置大模型：当前为离线模板叙事（可玩）。"
                    "在网页设置页填入 DeepSeek API Key 后立即启用 AI 叙事"
                ),
            ),
            None,
        )

    # ------------------------------------------------------------------
    # 内部客户端构建（受治理参数保留）
    # ------------------------------------------------------------------
    def _runtime_llm_config(self, resolved: _ResolvedConfig) -> LLMConfig:
        """按运行期配置覆盖 JSON 分级的主后端（保留缓存/熔断参数）。"""
        base = load_llm_config(self._settings.llm_config_path)
        providers = {**base.providers}
        providers[resolved.provider] = ProviderSpec(api_base=resolved.api_base)
        tiers = {
            tier: TierSpec(
                provider=resolved.provider,
                model=resolved.model,
                api_base=resolved.api_base,
                fallbacks=(),
            )
            for tier in ModelTier
        }
        return base.model_copy(update={"providers": providers, "tiers": tiers})

    def _build_inner(self, resolved: _ResolvedConfig) -> LLMClient:
        """按解析结果构造真实客户端（缺省离线；配置非法在构造期抛出）。"""
        config = LLMClientConfig(
            provider=resolved.provider,
            model=resolved.model,
            api_base=resolved.api_base,
            api_key=SecretStr(resolved.api_key),
        )
        return LiteLLMClient(
            config,
            transport=self._transport,
            calls=self._calls,
            clock=self._clock,
            sleep=self._sleep,
            llm_config=self._runtime_llm_config(resolved),
        )

    def refresh(self) -> LLMRuntimeStatus:
        """重新解析环境变量 + 本机凭据并切换内部客户端（保存/清除后调用）。"""
        status, resolved = self._resolve()
        inner: LLMClient
        if resolved is None:
            inner = DisabledLLMClient()
        else:
            try:
                inner = self._build_inner(resolved)
            except Exception as exc:  # 配置非法：降级离线并如实报告（不让服务起不来）
                _LOGGER.warning("LLM 运行时配置非法，已降级离线（%s）", exc.__class__.__name__)
                status = LLMRuntimeStatus(
                    configured=False,
                    enabled=False,
                    source=LLMSource.NONE.value,
                    provider=status.provider,
                    model=status.model,
                    api_base=status.api_base,
                    masked_key=status.masked_key,
                    has_saved_user_key=status.has_saved_user_key,
                    detail=f"LLM 配置非法，已降级离线模板叙事（{exc.__class__.__name__}）",
                )
                resolved = None
                inner = DisabledLLMClient()
        with self._lock:
            self._inner = inner
            self._status = status
        if resolved is not None:
            _LOGGER.info(
                "LLM 已启用：source=%s provider=%s model=%s",
                status.source,
                status.provider,
                status.model,
            )
        return status

    # ------------------------------------------------------------------
    # 玩家凭据写入/清除
    # ------------------------------------------------------------------
    def save_user_credentials(
        self,
        *,
        provider: str,
        model: str,
        api_base: str,
        api_key: str,
    ) -> LLMRuntimeStatus:
        """保存玩家输入的 key 并热切换；字段非法抛 ``CredentialStoreError``（422）。"""
        self._store.save(
            provider=provider,
            model=model,
            api_base=api_base,
            api_key=api_key.strip(),
        )
        return self.refresh()

    def clear_user_credentials(self) -> LLMRuntimeStatus:
        """清除本机保存的 key 并热切换（若存在开发 key，仍会继续用开发 key）。"""
        self._store.clear()
        return self.refresh()

    # ------------------------------------------------------------------
    # LLMClient 协议（委托内部当前客户端）
    # ------------------------------------------------------------------
    def _current(self) -> LLMClient:
        """取当前内部客户端（持锁读引用；调用期间热切换不影响已取出的实例）。"""
        with self._lock:
            return self._inner

    def complete_json(self, req: LLMRequest) -> Any:
        """委托结构化补全；离线时返回 ``LLM_UNAVAILABLE``（与默认装配一致）。"""
        return self._current().complete_json(req)

    def complete_text(self, req: LLMRequest) -> Any:
        """委托纯文本补全；离线时返回 ``LLM_UNAVAILABLE``。"""
        return self._current().complete_text(req)

    # ------------------------------------------------------------------
    # 连接测试（网页「测试连接」按钮）
    # ------------------------------------------------------------------
    def test_connection(self, *, timeout_s: float = _TEST_TIMEOUT_S) -> dict[str, Any]:
        """用当前配置发一次最小请求，返回 ``{ok, latency_ms, provider, model, error}``。

        - 未启用时不发请求，直接返回失败原因（零网络）；
        - 只验证网络/鉴权/协议，不写任何状态、不含业务数据；
        - 失败原因只回传 ``Result`` 的短消息（不含密钥）。
        """
        status = self.status()
        if not status.enabled:
            return {
                "ok": False,
                "latency_ms": None,
                "provider": status.provider,
                "model": status.model,
                "error": status.detail,
            }
        request = LLMRequest(
            task=_TEST_TASK,
            agent_id=_TEST_AGENT,
            turn_id=1,
            idempotency_key=f"llm-settings-test-{uuid4().hex}",
            model_tier=ModelTier.ECONOMY,
            messages=[ChatMessage(role="user", content=_TEST_PROMPT)],
            timeout_s=timeout_s,
            max_repair=0,
            prompt_version="settings@v1",
        )
        started = time.monotonic()
        result = self.complete_text(request)
        latency_ms = int((time.monotonic() - started) * 1000)
        ok = bool(result.ok)
        return {
            "ok": ok,
            "latency_ms": latency_ms,
            "provider": status.provider,
            "model": status.model,
            "error": None if ok else str(result.message or "连接失败"),
        }
