"""单元测试：N2-038 流式开关配置（``TEXTGAME_STREAM_ENABLED``，ADR D-N2-8）。

默认 ``True``（后端端点常开、旧客户端不调用 = 死代码无副作用）；``False`` 时端点返回 404
统一失败信封（前端自动降级普通 ``/turns``），用于一键回滚。开关不影响默认离线路径。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from textgame.config.settings import get_settings

_ENV_NAME = "TEXTGAME_STREAM_ENABLED"


def test_stream_enabled_default_true(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """未显式配置时默认 ``True``（阶段 1 后端端点常开，D-N2-8）。"""
    monkeypatch.delenv(_ENV_NAME, raising=False)
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.stream_enabled is True


def test_stream_enabled_env_false_disables(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``TEXTGAME_STREAM_ENABLED=false`` → ``False``（端点 404 回滚路径）。"""
    monkeypatch.setenv(_ENV_NAME, "false")
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()

    try:
        assert get_settings().stream_enabled is False
    finally:
        get_settings.cache_clear()


def test_stream_enabled_env_true_explicit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``TEXTGAME_STREAM_ENABLED=true`` 显式开启与默认值一致。"""
    monkeypatch.setenv(_ENV_NAME, "true")
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()

    try:
        assert get_settings().stream_enabled is True
    finally:
        get_settings.cache_clear()


def test_env_example_documents_stream_switch() -> None:
    """``.env.example`` 必须文档化开关（含默认值说明，D-N2-8 回写要求）。"""
    root = Path(__file__).resolve().parents[2]

    text = (root / ".env.example").read_text(encoding="utf-8")

    assert "TEXTGAME_STREAM_ENABLED" in text
    assert "TEXTGAME_STREAM_ENABLED=true" in text
    assert "404" in text
