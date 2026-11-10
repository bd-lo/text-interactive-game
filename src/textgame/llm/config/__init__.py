"""LLM 客户端配置：单机单部署参数（Settings）+ 模型分级/缓存分层配置（JSON）。

M5-019 在 ``config.py`` 中只交付**单供应商单模型**的客户端配置；M5-020 按 ADR
D-M5-4 把它升级为包（``textgame.llm.config``），新增 **JSON 分层配置**：

- 包内默认：``llm/config/llm.default.json``（随包分发，``importlib.resources`` 读取）；
- 路径覆盖：``TEXTGAME_LLM_CONFIG``（``Settings.llm_config_path``）指向本地 JSON；
- 加载顺序：包内默认 → 路径覆盖；文件缺失/JSON 非法/字段越界一律**构造期 fail-fast**
  （不静默降级，D-M5-4/020 验收）；
- JSON 是**模型分级/缓存参数的唯一权威**（团队裁决 2-A）：``tiers`` 按
  ``model_tier`` 声明 ``provider/model[/api_base]``，``providers`` 提供各供应商
  ``api_base``；密钥/开关/超时/重试仍由 ``Settings`` 与 ``LLMClientConfig`` 承载。

``LLMClientConfig`` 保持 019 的字段与导入路径（``from textgame.llm.config import
LLMClientConfig``），供既有调用方与测试无感迁移。
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator

from textgame.config.settings import Settings
from textgame.llm.client import ModelTier

__all__ = [
    "CACHE_DEFAULT_MAXSIZE",
    "CACHE_DEFAULT_TTL_S",
    "CircuitSpec",
    "FallbackSpec",
    "LLMClientConfig",
    "LLMConfig",
    "ProviderSpec",
    "TierSpec",
    "load_llm_config",
]

#: 020 缓存默认上限（第 12 章 12.7：``TTLCache(maxsize=1024, ttl=600)``）。
CACHE_DEFAULT_MAXSIZE = 1024
CACHE_DEFAULT_TTL_S = 600

#: 包内默认配置文件（与 ``llm/config/__init__.py`` 同目录，随包分发）。
DEFAULT_CONFIG_FILENAME = "llm.default.json"


class ProviderSpec(BaseModel):
    """供应商端点配置（020 只消费 ``api_base``；024 扩展回退链时复用）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    api_base: str = Field(min_length=1, max_length=512)


class FallbackSpec(BaseModel):
    """回退链单项（M5-024/第 12 章 §12.4）：主供应商失败后按序尝试的 provider/model。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    api_base: str | None = Field(
        default=None,
        min_length=1,
        max_length=512,
        description="可选；缺省时取 providers[provider].api_base",
    )


class TierSpec(BaseModel):
    """单个模型分级的后端声明（``provider`` + ``model`` + 可选 ``api_base``/回退链）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    api_base: str | None = Field(
        default=None,
        min_length=1,
        max_length=512,
        description="可选；缺省时取 providers[provider].api_base",
    )
    #: M5-024：主供应商失败后按序回退（空元组 = 不回退，行为与 020 一致）。
    fallbacks: tuple[FallbackSpec, ...] = Field(default=(), max_length=8)


class CircuitSpec(BaseModel):
    """熔断参数（第 12 章 §12.8；进程内状态机，不跨进程共享）。

    触发条件满足任一即 open：滑动窗口 ``window_s`` 内错误率 ``> error_rate_threshold``
    （仅在窗口内 ≥4 次调用时评估，避免小样本误开）或连续失败 ``consecutive_failures`` 次。
    open 持续 ``open_s`` 后进入 half-open，最多放行 ``half_open_probes`` 次探测：
    探测成功 → 关闭并清零；探测失败 → 重新 open。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    window_s: float = Field(default=60.0, gt=0, le=3600)
    error_rate_threshold: float = Field(default=0.5, gt=0, le=1)
    consecutive_failures: int = Field(default=5, ge=1, le=100)
    open_s: float = Field(default=60.0, gt=0, le=3600)
    half_open_probes: int = Field(default=1, ge=1, le=10)


class CacheSpec(BaseModel):
    """进程内响应缓存参数（020；默认 ``allowed_tasks=()`` = 不缓存任何任务）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    maxsize: int = Field(default=CACHE_DEFAULT_MAXSIZE, ge=1, le=65536)
    ttl_s: int = Field(default=CACHE_DEFAULT_TTL_S, ge=1, le=86400)
    allowed_tasks: tuple[str, ...] = Field(default=(), max_length=64)

    def allows(self, task: str) -> bool:
        """该任务是否允许进入进程内缓存（默认空 allowlist = 一律不缓存）。"""
        return task in self.allowed_tasks


class LLMConfig(BaseModel):
    """``llm.default.json`` 的强类型快照（``extra=forbid``，未知字段直接拒绝）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = Field(default=1, ge=1, le=1)
    providers: dict[str, ProviderSpec] = Field(min_length=1)
    tiers: dict[ModelTier, TierSpec]
    cache: CacheSpec = Field(default_factory=CacheSpec)
    #: M5-024：熔断参数（默认值即第 12 章 §12.8 口径；不配置时行为与 019/020 一致）。
    circuit: CircuitSpec = Field(default_factory=CircuitSpec)

    @model_validator(mode="after")
    def _validate_tiers(self) -> LLMConfig:
        """两个分级都必须声明，且主/回退 provider 都必须在 providers 中存在（fail-fast）。"""
        missing = [tier.value for tier in ModelTier if tier not in self.tiers]
        if missing:
            raise ValueError(f"tiers 缺少模型分级: {', '.join(missing)}")
        referenced = {spec.provider for spec in self.tiers.values()}
        for spec in self.tiers.values():
            referenced.update(fallback.provider for fallback in spec.fallbacks)
        unknown = sorted(referenced - set(self.providers))
        if unknown:
            raise ValueError(f"tiers/fallbacks 引用了未声明 provider: {', '.join(unknown)}")
        return self

    def _resolve(self, provider: str, model: str, api_base: str | None) -> tuple[str, str, str]:
        """``(provider, model, api_base)`` 三元组；api_base 缺省时取 providers 声明。"""
        return provider, model, api_base or self.providers[provider].api_base

    def backend_for(self, tier: ModelTier) -> tuple[str, str, str]:
        """返回该分级的**主** ``(provider, model, api_base)``；缺分级属配置错误。"""
        spec = self.tiers.get(tier)
        if spec is None:
            raise ValueError(f"缺少模型分级配置: {tier.value}")
        return self._resolve(spec.provider, spec.model, spec.api_base)

    def backends_for(self, tier: ModelTier) -> tuple[tuple[str, str, str], ...]:
        """返回该分级的完整尝试链：主供应商在前、``fallbacks`` 按声明顺序在后（M5-024）。"""
        spec = self.tiers.get(tier)
        if spec is None:
            raise ValueError(f"缺少模型分级配置: {tier.value}")
        primary = self._resolve(spec.provider, spec.model, spec.api_base)
        fallbacks = tuple(
            self._resolve(fallback.provider, fallback.model, fallback.api_base)
            for fallback in spec.fallbacks
        )
        return (primary, *fallbacks)


class LLMClientConfig(BaseModel):
    """``LiteLLMClient`` 的不可变配置快照（019 契约，020 起作为 tiers 缺省的兼容回退）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    api_base: str = Field(min_length=1, max_length=512)
    api_key: SecretStr
    max_attempts: int = Field(default=3, ge=1, le=3)
    timeout_s: float = Field(default=20.0, gt=0)
    rpm_limit: int = Field(default=60, ge=1)

    @classmethod
    def from_settings(cls, settings: Settings) -> LLMClientConfig:
        """从 ``Settings`` 构造配置；启用真实 LLM 时缺少密钥立即失败（fail-fast）。"""
        api_key = settings.llm_api_key
        if api_key is None or not api_key.get_secret_value().strip():
            raise RuntimeError(
                "TEXTGAME_LLM_ENABLED=true 但未配置 TEXTGAME_LLM_API_KEY；"
                "请复制 .env.example 为 .env 并填入密钥（.env 不入库）"
            )
        return cls(
            provider=settings.llm_provider,
            model=settings.llm_model,
            api_base=settings.llm_base_url,
            api_key=api_key,
        )


def _read_config_text(path: str | Path | None) -> str:
    """读取 JSON 文本：``None`` = 包内默认，否则读指定文件（UTF-8）。"""
    if path is None:
        resource = resources.files(__package__).joinpath(DEFAULT_CONFIG_FILENAME)
        return resource.read_text(encoding="utf-8")
    return Path(path).read_text(encoding="utf-8")


def load_llm_config(path: str | Path | None = None) -> LLMConfig:
    """加载并校验 LLM 分层配置；任何非法配置在构造期抛 ``RuntimeError``（fail-fast）。

    ``path=None`` 时读包内默认 ``llm.default.json``；否则读 ``TEXTGAME_LLM_CONFIG``
    指向的文件。文件不存在、非 UTF-8、JSON 语法错误、字段越界/未知字段/缺分级均失败。
    """
    source = "包内默认 llm.default.json" if path is None else str(path)
    try:
        raw = _read_config_text(path)
    except OSError as exc:
        raise RuntimeError(f"LLM 配置读取失败（{source}）: {exc}") from exc
    try:
        payload: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"LLM 配置不是合法 JSON（{source}）: {exc.msg}") from exc
    try:
        return LLMConfig.model_validate(payload)
    except ValidationError as exc:
        raise RuntimeError(f"LLM 配置非法（{source}）: {exc}") from exc
