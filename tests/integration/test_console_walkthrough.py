"""OSS-1「一条命令开玩」终端入口验收（进程内装配、临时库、禁网）。

覆盖团队裁决 B″ 的三条判据：
1. ``python -m textgame.console --check`` 的脚本化 4 回合（含 1 个失败样本）全通过且输出无协议内部字段；
2. ``python -m textgame.demo --check`` 的 7 屏断言：成功、字数口径、golden 屏字数、版本链、两次运行确定性；
3. 只读对局视图缺失时状态行降级为提示而不是伪造数据。

装配口径与 ``tests/integration/test_error_envelope.py`` 一致：``TEXTGAME_DB_URL`` 指向 ``tmp_path``
→ ``get_settings.cache_clear()`` → ``main._build_app()`` → ``TestClient``（不占端口、不联网）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from textgame import console, demo
from textgame.config.settings import get_settings

pytestmark = [pytest.mark.integration]


def _dispose(app: FastAPI) -> None:
    """释放组合根内 SQLite Engine 的文件句柄（Windows 临时文件收尾）。"""
    console.dispose_app_engine(app)


def _build_app_with_db(monkeypatch: pytest.MonkeyPatch, db_path: Path) -> FastAPI:
    """把 ``TEXTGAME_DB_URL`` 指向临时库后重建生产组合根。"""
    db_url = f"sqlite:///{db_path.as_posix()}"
    monkeypatch.setenv("TEXTGAME_DB_URL", db_url)
    get_settings.cache_clear()
    import textgame.main as main_module

    _dispose(main_module.app)
    return main_module._build_app()


def _walkthrough(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str
) -> list[dict[str, Any]]:
    """在独立临时库上跑一次 7 回合演示并释放资源。"""
    app = _build_app_with_db(monkeypatch, tmp_path / name)
    try:
        with TestClient(app) as client:
            return demo.run_walkthrough(client)
    finally:
        _dispose(app)


def test_console_scripted_check(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """4 回合自检：3 成功 + 1 失败样本，输出无 JSON/游戏标识/版本号/幂等键字样。"""
    app = _build_app_with_db(monkeypatch, tmp_path / "console-check.db")
    lines: list[str] = []
    try:
        with TestClient(app) as client:
            code = console.run_check(client, out=lines.append)
    finally:
        _dispose(app)
    rendered = "\n".join(lines)
    assert code == 0
    assert console.FAILED_HINT in rendered
    assert "第 4 回合 输入：向北走进地窖" in rendered
    console.assert_clean_output(rendered)


def test_demo_walkthrough_lengths_and_determinism(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """7/7 成功、屏字数与 golden 一致、版本链 1..7、两次运行逐字一致。"""
    first = _walkthrough(monkeypatch, tmp_path, "first.db")
    second = _walkthrough(monkeypatch, tmp_path, "second.db")
    demo.verify_walkthrough(first)
    demo.verify_walkthrough(second)
    assert [record["text_len"] for record in first] == list(demo.EXPECTED_TEXT_LEN)
    assert [record["version"] for record in first] == list(range(1, len(demo.WALKTHROUGH) + 1))
    assert [record["text"] for record in first] == [record["text"] for record in second]
    transcript = demo.format_transcript(first)
    assert transcript.count("回合（输入：") == len(demo.WALKTHROUGH)
    console.assert_clean_output(transcript)


def test_demo_markdown_is_generated_from_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """示例文档由实跑记录生成：7 段屏幕输出 + 复现命令，且无协议内部字段。"""
    records = _walkthrough(monkeypatch, tmp_path, "markdown.db")
    document = demo.render_markdown(records)
    assert document.count("```text") == len(demo.WALKTHROUGH)
    assert "uv run python -m textgame.demo --check" in document
    assert "| 回合 | 输入 | 屏字数 |" in document
    console.assert_clean_output(document)


def test_status_line_degrades_without_view() -> None:
    """只读视图缺失时显示降级提示（不伪造 HP/背包/位置）。"""
    line = console.status_line(None)
    assert line == console.STATE_UNAVAILABLE
    assert "HP " not in line and "行囊：" not in line
    console.assert_clean_output(line)


def test_status_line_renders_view() -> None:
    """只读视图存在时渲染 HP/位置/行囊，且不含协议内部字段。"""
    view: dict[str, Any] = {
        "hp": 20,
        "max_hp": 20,
        "inventory": [{"item_id": "item_a", "name": "火把"}],
        "room": {"room_id": "room_a", "title": "废弃哨站·入口"},
    }
    line = console.status_line(view)
    assert "HP 20/20" in line
    assert "废弃哨站·入口" in line
    assert "行囊：火把" in line
    console.assert_clean_output(line)


def test_render_turn_renders_failure_hints() -> None:
    """失败回合在 16 字文案后追加 suggested_actions/effects 提示（不改原文案）。"""
    view: dict[str, Any] = {
        "hp": 20,
        "max_hp": 20,
        "inventory": [],
        "room": {"room_id": "room_a", "title": "废弃哨站·入口"},
    }
    result: dict[str, Any] = {
        "ok": False,
        "text": "这个动作没有生效，你停下了脚步。",
        "suggested_actions": ["环顾四周", "查看背包"],
        "effects": [{"hint": "背包里没有这件物品"}, {"reason": "internal english only"}],
    }
    rendered = console.render_turn(result, view)
    assert rendered.startswith("这个动作没有生效，你停下了脚步。")
    assert console.FAILED_HINT in rendered
    assert "可以试试：环顾四周 / 查看背包" in rendered
    assert "提示：背包里没有这件物品" in rendered
    assert "internal english only" not in rendered
    console.assert_clean_output(rendered)


def test_render_turn_without_hints_keeps_oss1_output() -> None:
    """A4 提示字段为空时，失败回合输出与 OSS-1 逐字一致（不含多余行）。"""
    view: dict[str, Any] = {
        "hp": 20,
        "max_hp": 20,
        "inventory": [],
        "room": {"room_id": "room_a", "title": "废弃哨站·入口"},
    }
    result: dict[str, Any] = {
        "ok": False,
        "text": "这个动作没有生效，你停下了脚步。",
        "suggested_actions": [],
        "effects": [],
    }
    expected = "\n".join(
        [
            "这个动作没有生效，你停下了脚步。",
            console.FAILED_HINT,
            console.status_line(view),
        ]
    )
    assert console.render_turn(result, view) == expected


def test_failure_detail_lines_tolerates_object_without_fields() -> None:
    """编排层结果若为对象且字段缺失，失败提示容错为空（不抛错、不伪造）。"""
    from types import SimpleNamespace

    assert console._failure_detail_lines(SimpleNamespace()) == []
    details = console._failure_detail_lines(
        SimpleNamespace(suggested_actions=["拿起火把"], effects=[{"hint": "先翻翻行囊"}])
    )
    assert details == ["可以试试：拿起火把", "提示：先翻翻行囊"]
    # 含协议内部字段的提示一律不显示（玩家视角不泄漏 json/version 等词）
    filtered = console._failure_detail_lines(
        SimpleNamespace(suggested_actions=["查看 version 字段"], effects=[])
    )
    assert filtered == []


def test_scene_guidance_uses_real_view_data() -> None:
    """首屏引导只列只读视图里真实存在的动作（不编造剧情/目标）。"""
    view: dict[str, Any] = {
        "room": {
            "exits": {"north": "room_b"},
            "items": [{"item_id": "item_a", "name": "火把"}],
            "npcs": [{"npc_id": "npc_a", "name": "老哨兵"}],
        }
    }
    lines = console.scene_guidance(view)
    joined = "\n".join(lines)
    assert "往北走" in joined
    assert "拿起火把" in joined
    assert "对老哨兵问好" in joined
    assert "查看背包" in joined
    assert "剧情" not in joined
    console.assert_clean_output(joined)


def test_status_line_aggregates_view_qty() -> None:
    """A4 加法字段 qty：同模板多件按「名字×N」展示，qty=1/缺省保持原样。"""
    view: dict[str, Any] = {
        "hp": 20,
        "max_hp": 20,
        "inventory": [
            {"item_id": "item_a", "name": "铁锭", "qty": 2},
            {"item_id": "item_b", "name": "橡木柄", "qty": 1},
            {"item_id": "item_c", "name": "铜线", "qty": 2},
        ],
        "room": {"room_id": "room_a", "title": "废弃铁匠铺"},
    }
    line = console.status_line(view)
    assert "行囊：铁锭×2、橡木柄、铜线×2" in line
    console.assert_clean_output(line)
