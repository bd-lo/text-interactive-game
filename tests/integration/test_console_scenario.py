"""OSS-2 终端安全包验收（R1 玩家实测 P0-2/P1-4）：场景切换、同库续玩、首屏引导。

覆盖：
1. ``--scenario m2|m3|m4`` 建局场景透传（m3 首屏可见多角色短句、m4 初始铸造材料）；
2. 默认/``--continue`` 同库恢复最近存档（版本链续写），``--new`` 强制新开；
3. 首屏「继续/新开」提示 + 真实数据引导（不编造剧情、不泄漏协议字段）。

装配口径与 ``test_console_walkthrough.py`` 一致：临时库 + 进程内 ``TestClient``
（不占端口、不联网）；不触碰叙事文案与 golden。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from textgame import console
from textgame.config.settings import get_settings

pytestmark = [pytest.mark.integration]


def _dispose(app: FastAPI) -> None:
    """释放组合根内 SQLite Engine 的文件句柄（Windows 临时文件收尾）。"""
    console.dispose_app_engine(app)


def _build_app_with_db(monkeypatch: pytest.MonkeyPatch, db_path: Path) -> tuple[FastAPI, str]:
    """把 ``TEXTGAME_DB_URL`` 指向临时库后重建生产组合根，并返回 DSN。"""
    db_url = f"sqlite:///{db_path.as_posix()}"
    monkeypatch.setenv("TEXTGAME_DB_URL", db_url)
    get_settings.cache_clear()
    import textgame.main as main_module

    _dispose(main_module.app)
    return main_module._build_app(), db_url


def _inventory_names(view: dict[str, Any]) -> list[str]:
    """只读视图的背包物品名列表。"""
    return [item["name"] for item in view["inventory"]]


def _quit_reader() -> Callable[[str], str]:
    """交互循环用 read_line：第一次调用即退出（不阻塞、不需要真实键盘）。"""
    return lambda _prompt: ":quit"


def test_parser_supports_scenario_and_save_flags() -> None:
    """CLI：默认 m2；--scenario/--continue/--resume/--new 全部可解析。"""
    parser = console._build_parser()
    defaults = parser.parse_args([])
    assert defaults.scenario == "m2"
    assert defaults.continue_save is False
    assert defaults.new_game is False
    assert defaults.check is False
    assert parser.parse_args(["--scenario", "m3"]).scenario == "m3"
    assert parser.parse_args(["--scenario", "m4"]).scenario == "m4"
    assert parser.parse_args(["--continue"]).continue_save is True
    assert parser.parse_args(["--resume"]).continue_save is True
    assert parser.parse_args(["--new"]).new_game is True
    # --check 固定走 m2 自检，场景参数不影响自检路径
    assert parser.parse_args(["--check", "--scenario", "m4"]).scenario == "m4"


def test_scenario_m4_session_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``scenario="m4"`` 建局：初始背包为制造材料、入口多一个东向铁匠铺。"""
    app, _ = _build_app_with_db(monkeypatch, tmp_path / "m4.db")
    try:
        with TestClient(app) as client:
            session = console.GameSession(client, scenario="m4")
            session.create()
            view = session.view()
    finally:
        _dispose(app)
    assert view is not None
    names = _inventory_names(view)
    assert "铁锭" in names and "铜线" in names
    assert "east" in view["room"]["exits"]
    assert console.status_line(view).startswith("【状态】HP 20/20")


def test_scenario_m3_first_screen_shows_multi_actor_clause(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``scenario="m3"`` 首屏：拾荒者抢药水的多角色短句对玩家可见。"""
    app, _ = _build_app_with_db(monkeypatch, tmp_path / "m3.db")
    try:
        with TestClient(app) as client:
            session = console.GameSession(client, scenario="m3")
            session.create()
            result = session.act("环顾四周")
            view = session.view()
    finally:
        _dispose(app)
    assert result["ok"] is True
    assert "拾荒者" in result["text"]
    assert view is not None
    assert "治疗药水" not in [item["name"] for item in view["room"]["items"]]


def test_continue_resumes_latest_save(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """同库第三次启动（默认）自动继续：位置/背包/版本链保持，之后仍可正常续写。"""
    db_path = tmp_path / "continue.db"
    app, db_url = _build_app_with_db(monkeypatch, db_path)
    try:
        with TestClient(app) as client:
            session = console.GameSession(client)
            session.create()
            assert session.act("拿起火把")["ok"] is True
            assert session.act("向北走进地窖")["ok"] is True
            first_view = session.view()
    finally:
        _dispose(app)
    assert first_view is not None
    assert first_view["room"]["title"] == "哨站地窖"
    assert "火把" in _inventory_names(first_view)

    app, db_url = _build_app_with_db(monkeypatch, db_path)
    try:
        with TestClient(app) as client:
            session = console.GameSession(client)
            notice = console.prepare_session(session, db_url=db_url)
            resumed_view = session.view()
            result = session.act("向南走出地窖")
            final_view = session.view()
    finally:
        _dispose(app)
    assert "已继续上次存档" in notice
    assert resumed_view is not None
    assert resumed_view["room"]["title"] == "哨站地窖"
    assert "火把" in _inventory_names(resumed_view)
    assert result["ok"] is True
    assert final_view is not None
    assert final_view["room"]["title"] == "废弃哨站·入口"


def test_continue_without_save_creates_new(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``--continue`` 但同库没有存档：明确提示后新开一局，入口仍可用。"""
    app, db_url = _build_app_with_db(monkeypatch, tmp_path / "empty.db")
    try:
        with TestClient(app) as client:
            session = console.GameSession(client)
            notice = console.prepare_session(session, db_url=db_url, continue_save=True)
            view = session.view()
    finally:
        _dispose(app)
    assert "没有找到可继续的存档" in notice
    assert view is not None
    assert view["room"]["title"] == "废弃哨站·入口"


def test_new_forces_fresh_game(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """同库已有存档时 ``--new`` 显式新开：不继承旧背包/位置。"""
    db_path = tmp_path / "new.db"
    app, db_url = _build_app_with_db(monkeypatch, db_path)
    try:
        with TestClient(app) as client:
            session = console.GameSession(client)
            session.create()
            assert session.act("拿起火把")["ok"] is True
    finally:
        _dispose(app)
    assert console.latest_saved_game_id(db_url) is not None

    app, db_url = _build_app_with_db(monkeypatch, db_path)
    try:
        with TestClient(app) as client:
            session = console.GameSession(client)
            notice = console.prepare_session(session, db_url=db_url, new_game=True)
            view = session.view()
    finally:
        _dispose(app)
    assert "--new" in notice
    assert view is not None
    assert view["room"]["title"] == "废弃哨站·入口"
    assert _inventory_names(view) == []


def test_latest_saved_game_id_degrades_without_sqlite_db() -> None:
    """非 SQLite/内存库不抛错：返回 ``None``（降级为新开）。"""
    assert console.latest_saved_game_id("memory://") is None
    assert console.latest_saved_game_id("sqlite:///:memory:") is None
    assert console.latest_saved_game_id("sqlite:///no/such/dir/x.db") is None


def test_run_interactive_first_screen_notice_and_guidance(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """交互首屏：存档提示 + 按真实视图生成的「可以试试」引导，输出无协议字段。"""
    app, db_url = _build_app_with_db(monkeypatch, tmp_path / "interactive.db")
    lines: list[str] = []
    try:
        with TestClient(app) as client:
            session = console.GameSession(client)
            notice = console.prepare_session(session, db_url=db_url)
            console.run_interactive(
                session, read_line=_quit_reader(), out=lines.append, start_notice=notice
            )
    finally:
        _dispose(app)
    rendered = "\n".join(lines)
    assert "【存档】已新开一局。" in rendered
    assert "【引导】可以试试：" in rendered
    assert "拿起火把" in rendered
    assert "对老哨兵问好" in rendered
    assert "往北走" in rendered
    assert "再见。" in rendered
    console.assert_clean_output(rendered)


def test_run_interactive_m3_guidance_matches_new_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """m3 首屏引导随真实状态变化：药水被拾荒者拿走，引导里只剩火把可拿。"""
    app, db_url = _build_app_with_db(monkeypatch, tmp_path / "interactive-m3.db")
    lines: list[str] = []
    try:
        with TestClient(app) as client:
            session = console.GameSession(client, scenario="m3")
            notice = console.prepare_session(session, db_url=db_url)
            console.run_interactive(
                session, read_line=_quit_reader(), out=lines.append, start_notice=notice
            )
    finally:
        _dispose(app)
    rendered = "\n".join(lines)
    assert "拾荒者" in rendered
    guidance = [line for line in lines if line.startswith("【引导】")]
    assert len(guidance) == 1
    assert "拿起火把" in guidance[0]
    assert "治疗药水" not in guidance[0]
    console.assert_clean_output(rendered)


def test_failure_turn_renders_backend_hints_end_to_end(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """联测（C1×A4）：失败回合的 suggested_actions/effects 原样进入终端提示层。

    16 字失败文案保持原文；A4 字段提供「可以试试 / 提示」两行；若 A4 缺失两字段，
    本测试失败（提示契约缺失）。
    """
    app, _ = _build_app_with_db(monkeypatch, tmp_path / "hints.db")
    try:
        with TestClient(app) as client:
            session = console.GameSession(client)
            session.create()
            result = session.act("使用火把")  # 背包为空 → 世界拒绝（16 字 + 零写入）
            rendered = console.render_turn(result, session.view())
    finally:
        _dispose(app)
    assert result["ok"] is False
    assert result["text"] == "这个动作没有生效，你停下了脚步。"
    assert result["suggested_actions"], "A4 应填充失败回合的可行动作建议"
    assert result["effects"] and any("hint" in effect for effect in result["effects"])
    assert "可以试试：" in rendered
    assert "提示：" in rendered
    console.assert_clean_output(rendered)


def test_m4_status_line_uses_view_qty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """联测（C1×A4）：m4 初始重复材料在状态行按「铁锭×2」聚合展示。"""
    app, _ = _build_app_with_db(monkeypatch, tmp_path / "qty.db")
    try:
        with TestClient(app) as client:
            session = console.GameSession(client, scenario="m4")
            session.create()
            view = session.view()
    finally:
        _dispose(app)
    assert view is not None
    line = console.status_line(view)
    assert "铁锭×2" in line
    assert "铜线×2" in line
    console.assert_clean_output(line)
