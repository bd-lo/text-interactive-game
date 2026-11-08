"""应用配置: 从 ``TEXTGAME_*`` 环境变量与 ``.env`` 文件读取运行期配置。

默认值见开发文档第 16 章 16.5 环境变量表; 不读取任何真实密钥。
"""

from functools import lru_cache

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
    llm_provider: str | None = None
    llm_model: str | None = None
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """返回进程内唯一的配置实例; 测试中调用 ``get_settings.cache_clear()`` 重置。"""
    return Settings()
