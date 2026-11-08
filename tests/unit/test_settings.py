"""单元测试: 配置默认值与 ``TEXTGAME_*`` 环境变量覆盖 (开发文档第 16 章 16.5)。"""

import pytest

from textgame.config.settings import get_settings

_CONFIG_ENV_VARS = (
    "TEXTGAME_DB_URL",
    "TEXTGAME_SEED",
    "TEXTGAME_LLM_PROVIDER",
    "TEXTGAME_LLM_MODEL",
    "TEXTGAME_LOG_LEVEL",
)


def _clean_config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """清除可能影响默认值的 TEXTGAME_* 环境变量, 并重置配置缓存。"""
    for env_name in _CONFIG_ENV_VARS:
        monkeypatch.delenv(env_name, raising=False)
    get_settings.cache_clear()


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """未设置任何 TEXTGAME_* 环境变量时, 字段取 16.5 表的默认值。"""
    _clean_config_env(monkeypatch)

    settings = get_settings()

    assert settings.db_url == "sqlite:///data/textgame.db"
    assert settings.seed == 42
    assert settings.log_level == "INFO"
    assert settings.llm_provider is None
    assert settings.llm_model is None


def test_settings_env_override_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    """``TEXTGAME_SEED`` 环境变量覆盖默认种子。"""
    _clean_config_env(monkeypatch)
    monkeypatch.setenv("TEXTGAME_SEED", "7")

    settings = get_settings()

    assert settings.seed == 7
