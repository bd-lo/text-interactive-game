"""应用配置：从 ``TEXTGAME_*`` 环境变量与 ``.env`` 文件读取运行期配置。

默认值见开发文档第 16 章 16.5 环境变量表；密钥只允许经环境变量 / ``.env``（不入库）
注入，见第 12 章（API 调用治理）、第 15 章（防越权）与第 20 章 R-L5。
"""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """运行期配置模型。"""

    model_config = SettingsConfigDict(
        env_prefix="TEXTGAME_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    db_url: str = "sqlite:///data/textgame.db"
    seed: int = 42
    log_level: str = "INFO"

    # LLM 接入（第 12 章）：模型分级与回退链在 M5 落 config/llm.yaml；
    # 当前先配置单供应商单模型 + 凭据，凭据永不写入纳入 git 的文件。
    llm_provider: str = "deepseek"
    llm_model: str = "deepseek-flash"
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: SecretStr | None = None


@lru_cache
def get_settings() -> Settings:
    """返回进程内唯一的配置实例；测试中调用 ``get_settings.cache_clear()`` 重置。"""
    return Settings()
