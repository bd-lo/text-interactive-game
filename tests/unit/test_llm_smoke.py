"""单元测试：LLM API 连通性自检 CLI 的无密钥分支（不联网，M1 环境追加项）。"""

from pathlib import Path

import pytest

from textgame.config.settings import get_settings
from textgame.llm import smoke


def test_smoke_returns_2_without_api_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """未配置密钥时返回退出码 2，提示复制 .env.example，且输出不含密钥形态。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TEXTGAME_LLM_API_KEY", raising=False)
    get_settings.cache_clear()

    code = smoke.main()

    captured = capsys.readouterr()
    assert code == 2
    assert "TEXTGAME_LLM_API_KEY" in captured.out
    assert ".env" in captured.out
    assert "sk-" not in captured.out
