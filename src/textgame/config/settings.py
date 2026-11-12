"""应用配置：从 ``TEXTGAME_*`` 环境变量与 ``.env`` 文件读取运行期配置。

默认值见开发文档第 16 章 16.5 环境变量表；密钥只允许经环境变量 / ``.env``（不入库）
注入，见第 12 章（API 调用治理）、第 15 章（防越权）与第 20 章 R-L5。
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
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
    # R-19（2026-11-12 已迁 pre-queue）：自由制造草稿在进入 Resolver 前生成一次，裁决/CAS
    # 重试纯执行不重调模型；本开关保留为无头/生产显式开启的历史兼容二次确认（M5 后评估简化）。
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

    # N2-038 流式叙事后端（ADR D-N2-8）：``True``（默认）= ``POST /api/v1/turns/stream``
    # 常开（旧客户端不调用 → 死代码无副作用；前端 039 只在 LLM 已配置时使用）；``False`` =
    # 端点返回 404 统一失败信封（前端自动降级普通 ``/turns``），用于出问题时一键回滚。
    # 默认离线路径（模板叙事/旧 ``/turns``/golden）不受该开关影响。
    stream_enabled: bool = True

    @field_validator(
        "free_craft_settings_file",
        "llm_credentials_file",
        "llm_config_path",
        mode="before",
    )
    @classmethod
    def _blank_path_is_none(cls, value: object) -> object:
        """空串/纯空白环境变量视为「未设置」（``TEXTGAME_FREE_CRAFT_SETTINGS_FILE=`` 等）。

        pydantic-settings 会把空串原样交给字段校验，而 ``Path("")`` 会静默变成当前目录
        ``.``（既不是 None 也不是可写文件），导致偏好/凭据被错误地读写到 CWD。对这类
        「可选路径」字段统一把空串归一为 ``None``，回到默认路径逻辑。
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value


@lru_cache
def get_settings() -> Settings:
    """返回进程内唯一的配置实例；测试中调用 ``get_settings.cache_clear()`` 重置。"""
    return Settings()
