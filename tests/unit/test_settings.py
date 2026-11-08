"""单元测试：配置默认值、环境变量覆盖与密钥脱敏（第 16 章 16.5、第 12 章）。"""

from pathlib import Path

import pytest
from pydantic import SecretStr

from textgame.config.settings import get_settings

_CONFIG_ENV_VARS = (
    "TEXTGAME_DB_URL",
    "TEXTGAME_SEED",
    "TEXTGAME_LOG_LEVEL",
    "TEXTGAME_LLM_PROVIDER",
    "TEXTGAME_LLM_MODEL",
    "TEXTGAME_LLM_BASE_URL",
    "TEXTGAME_LLM_API_KEY",
)


def _isolate_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """清除可能影响默认值的 TEXTGAME_* 环境变量、切到无 .env 的目录并重置缓存。"""
    for env_name in _CONFIG_ENV_VARS:
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """未设置 TEXTGAME_* 且工作目录无 .env 时，字段取 16.5 表默认值。"""
    _isolate_config(monkeypatch, tmp_path)

    settings = get_settings()

    assert settings.db_url == "sqlite:///data/textgame.db"
    assert settings.seed == 42
    assert settings.log_level == "INFO"
    assert settings.llm_provider == "deepseek"
    assert settings.llm_model == "deepseek-flash"
    assert settings.llm_base_url == "https://api.deepseek.com"
    assert settings.llm_api_key is None


def test_settings_env_override_seed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``TEXTGAME_SEED`` 环境变量覆盖默认种子。"""
    _isolate_config(monkeypatch, tmp_path)
    monkeypatch.setenv("TEXTGAME_SEED", "7")

    settings = get_settings()

    assert settings.seed == 7


def test_llm_api_key_env_is_secret(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """API Key 从环境注入后是 SecretStr：可取值，但 repr/str/model_dump 必须脱敏。"""
    _isolate_config(monkeypatch, tmp_path)
    secret = "sk-test-not-a-real-key-0000"
    monkeypatch.setenv("TEXTGAME_LLM_API_KEY", secret)

    settings = get_settings()

    assert isinstance(settings.llm_api_key, SecretStr)
    assert settings.llm_api_key.get_secret_value() == secret
    assert str(settings.llm_api_key) == "**********"
    assert secret not in repr(settings)
    assert secret not in str(settings)
    assert secret not in str(settings.model_dump())
