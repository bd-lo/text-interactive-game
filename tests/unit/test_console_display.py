"""终端展示层单测（QA P2-8）：``--list-scenarios`` 空库提示与 ``FORBIDDEN_OUTPUT_TERMS`` 口径一致。

旧文案含字面 "JSON"（``FORBIDDEN_OUTPUT_TERMS`` 之一），与终端「不显示协议内部字段」的
口径冲突；这里同时锁定常量文案与真实入口的 stdout，防止回归。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from textgame import console
from textgame.config.settings import get_settings


def test_empty_scenario_library_tips_are_player_facing() -> None:
    """空库提示不含 "JSON" 等协议内部字面（``assert_clean_output`` 同口径）。"""
    joined = "\n".join(console.EMPTY_SCENARIO_LIBRARY_TIPS)

    console.assert_clean_output(joined)  # 不抛异常 = 不含 FORBIDDEN_OUTPUT_TERMS
    assert "json" not in joined.lower()
    assert "场景" in joined
    assert "--scenario" in joined


def _run_console(argv: list[str]) -> int:
    """执行 ``console.main(argv)`` 并恢复 ``console.build_app`` 写入的进程级环境变量。"""
    previous = os.environ.get("TEXTGAME_DB_URL")
    try:
        return console.main(argv)
    finally:
        if previous is None:
            os.environ.pop("TEXTGAME_DB_URL", None)
        else:
            os.environ["TEXTGAME_DB_URL"] = previous
        get_settings.cache_clear()


def test_list_scenarios_empty_library_message_is_clean(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """真实入口 ``--list-scenarios`` 空库：exit 0 + 干净中文提示（无 "JSON"）。"""
    db_path = tmp_path / "empty-scenarios.db"

    code = _run_console(["--db", str(db_path), "--list-scenarios"])

    out = capsys.readouterr().out
    assert code == 0
    assert "还没有上传的场景" in out
    assert "内置场景" in out
    assert "JSON" not in out
    console.assert_clean_output(out)  # 旧文案含 "JSON" 会在这一行失败
