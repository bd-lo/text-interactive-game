"""LLM 客户端配置（第 12 章 12.2/12.4；D-M5-4 配置形态定案）。

M5-019 只交付**单供应商单模型**的客户端配置：provider/model/api_base/密钥/重试上限/
超时/RPM 限额。模型分级、供应商回退链、价格表等 ``config/llm.json`` 的分层配置归 020/022
（不新增 PyYAML 依赖，JSON-only，与 M4 数据文件同一纪律）。

密钥只从环境变量 / ``.env`` 经 ``Settings.llm_api_key``（SecretStr）进入，配置对象不落盘、
不进日志、不进 ``model_dump`` 输出。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from textgame.config.settings import Settings

__all__ = ["LLMClientConfig"]


class LLMClientConfig(BaseModel):
    """``LiteLLMClient`` 的不可变配置快照（D-M5-4：020 起的 JSON 分层配置逐字段映射）。"""

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
