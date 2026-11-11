"""应用配置：从 ``TEXTGAME_*`` 环境变量与 ``.env`` 文件读取运行期配置。

默认值见开发文档第 16 章 16.5 环境变量表；密钥只允许经环境变量 / ``.env``（不入库）
注入，见第 12 章（API 调用治理）、第 15 章（防越权）与第 20 章 R-L5。
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """运行期配置模型。"""

    model_config = SettingsConfigDict(
        env_prefix="TEXTGAME_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # 允许按字段名构造（``Settings(llm_config_path=...)``），与默认 env 前缀行为并存
        populate_by_name=True,
    )

    db_url: str = "sqlite:///data/textgame.db"
    seed: int = 42
    log_level: str = "INFO"

    # M4 制造（第 13 章 §13.4、D-M4-6）：自由制造开关默认关闭；M5 预算/真实 LLM 前不开启。
    free_craft_enabled: bool = False
    # 网页设置页持久化的自由制造偏好（enabled + risk_accepted）；缺省 data/free_craft.json。
    free_craft_settings_file: Path | None = None
    # R-19：自由制造在 Resolver 内调 LLM；无头/生产开启必须显式承认该架构风险（M5 前迁移）。
    # 网页设置页的二次确认是运行期等价 opt-in，会写入 free_craft_settings_file.risk_accepted。
    allow_resolver_llm: bool = False
    # R-25：允许的 Host 头（逗号分隔）；默认只允许本机/测试客户端，防止 DNS rebinding。
    allowed_hosts: str = "127.0.0.1,localhost,[::1],testserver"

    # 启动器控制面（普通玩家双击启动/网页内退出）：控制面是否可用由进程入口注入的
    # ``LauncherController`` 决定（``python -m textgame.launcher`` 才注入；普通
    # ``uvicorn textgame.main:app`` 不注入），不通过 env 开关。这里只配置空闲自动退出阈值
    # （秒，0=关闭；env 名 TEXTGAME_IDLE_EXIT_SECONDS，默认 300 = 5 分钟；launcher CLI 可覆盖）。
    idle_exit_seconds: int = Field(default=300, ge=0)

    # LLM 接入（第 12 章）：模型分级与回退链在 M5 落 config/llm.json；
    # 当前先配置单供应商单模型 + 凭据，凭据永不写入纳入 git 的文件。
    # D-M5-6：默认离线（DisabledLLMClient）；true 时构造真实客户端且缺 key 立即失败。
    llm_enabled: bool = False
    llm_provider: str = "deepseek"
    llm_model: str = "deepseek-flash"
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: SecretStr | None = None
    #: 本机 LLM 凭据文件（网页/终端输入的 API Key 保存位置）：默认 ``data/llm.credentials.json``
    #: （``data/`` 已 gitignore），env 名 ``TEXTGAME_LLM_CREDENTIALS_FILE``；只在本机单机场景使用。
    llm_credentials_file: Path | None = None
    # M5-020（D-M5-4）：包内默认 llm/config/llm.default.json 的路径覆盖；
    # env 名为 TEXTGAME_LLM_CONFIG（不是 TEXTGAME_LLM_CONFIG_PATH）。
    llm_config_path: Path | None = Field(
        default=None,
        validation_alias="TEXTGAME_LLM_CONFIG",
        description="020：模型分级/缓存 JSON 配置路径覆盖（缺省用包内默认）",
    )


@lru_cache
def get_settings() -> Settings:
    """返回进程内唯一的配置实例；测试中调用 ``get_settings.cache_clear()`` 重置。"""
    return Settings()
