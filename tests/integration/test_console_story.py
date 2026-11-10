"""R2 QA-2：console（终端）story 主线验收：目标行 / 进度 / 结局屏 / 输出洁净 / 零写入。

为什么单独建文件：HTTP 级 story 旅程在 ``test_player_journey.py`` 与
``test_story_ending.py`` 已覆盖；本文件验的是**玩家在终端里看到什么**——
``python -m textgame.console --scenario story --new`` 的同一套渲染函数：

1. 首屏：``【目标】让信号灯亮起来 · 0/3`` + 三条子目标 ``[ ]`` + ``【引导】`` 建议；
2. 中途 ``:status``：目标进度与勾选态随剧情推进（``1/3`` + ``[x]``）；
3. 结局屏：服务端 ``ending_text`` + ``故事结束，输入 :quit 退出。``；
4. 结局后再输入行动：只重复结局提示，不产生新回合/新事件（零写入）；
5. 全部输出过 ``console.assert_clean_output``：无 json/game_id/version/幂等等协议字段，
   也无内部实体 ULID。

装配口径与 ``test_console_scenario.py`` 一致：临时库 + 进程内 ``TestClient``（不占端口、
不联网）；CLI 用例覆盖 argparse → ``--db`` → ``prepare_session(--new)`` 的真实入口链路。
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from textgame import console
from textgame.config.settings import get_settings

pytestmark = [pytest.mark.integration]

#: 与 HTTP 旅程同口径的 story 主线（14 个写回合 + 1 个失败回合 = 15 条事件，含开局）。
_STORY_FLOW: tuple[str, ...] = (
    "向东走",
    "细看火把",
    "对守夜人老兵说信号灯的事",
    "拿起火把",
    "向东走进铁匠铺",
    "拿起铜线",
    "拿起铜线",
    "打造信号火把",
    "向北走",  # 失败：铁匠铺没有北出口（只给提示，不中断主线）
    "向西走",
    "向北走进地窖",
    "拿起火石",
    "拿起治疗药水",
    "使用治疗药水",
    "向南走",
    "向上走",  # 登塔 → 结局
)
_ENDING_HINT = "故事结束，输入 :quit 退出。"
_ULID_RE = re.compile(r"(?:room|item|npc|char|game)_[0-9A-HJKMNP-TV-Z]{26}")


def _dispose(app: FastAPI) -> None:
    """释放组合根内 SQLite Engine 的文件句柄（Windows 临时文件收尾）。"""
    console.dispose_app_engine(app)


def _build_app_with_db(monkeypatch: pytest.MonkeyPatch, db_path: Path) -> FastAPI:
    """把 ``TEXTGAME_DB_URL`` 指向临时库后重建生产组合根。"""
    monkeypatch.setenv("TEXTGAME_DB_URL", f"sqlite:///{db_path.as_posix()}")
    get_settings.cache_clear()
    import textgame.main as main_module

    _dispose(main_module.app)
    return main_module._build_app()


@pytest.fixture
def console_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[tuple[FastAPI, Path]]:
    """每个用例一套临时库 + 生产组合根；结束释放 Engine。"""
    db_path = tmp_path / "console-story.db"
    app = _build_app_with_db(monkeypatch, db_path)
    try:
        yield app, db_path
    finally:
        _dispose(app)


def _scripted_reader(lines: list[str]) -> Callable[[str], str]:
    """把脚本行喂给 ``run_interactive(read_line=...)``（空行/命令也按原文返回）。"""
    script = iter(lines)

    def read_line(_prompt: str) -> str:
        return next(script)

    return read_line


def _replay_count(client: TestClient, game_id: str) -> int:
    """``/replay`` 事件总数（只读）。"""
    response = client.get(f"/api/v1/games/{game_id}/replay", params={"from_seq": 1, "limit": 500})
    assert response.status_code == 200, response.text
    events: list[dict[str, Any]] = response.json()["events"]
    return len(events)


# ---------------------------------------------------------------------------
# 1. 交互循环（``run_interactive``）：首屏目标行 → 进度 → 结局屏 → 零写入
# ---------------------------------------------------------------------------
def test_console_story_interactive_shows_goal_ending_and_stays_clean(
    console_app: tuple[FastAPI, Path],
) -> None:
    """终端 story 主线：首屏含【目标】0/3、:status 显示 1/3、结局屏含「故事结束」且洁净。"""
    app, _db_path = console_app
    with TestClient(app) as client:
        session = console.GameSession(client, scenario="story")
        session.create()
        # 在「问完老兵」之后插入 :status，验证目标进度随剧情推进（1/3 已勾选）
        lines = [*_STORY_FLOW[:3], ":status", *_STORY_FLOW[3:], "环顾四周", ":quit"]
        rendered: list[str] = []
        console.run_interactive(session, read_line=_scripted_reader(lines), out=rendered.append)
        joined = "\n".join(rendered)
        final_view = session.view()
        assert final_view is not None
        events = _replay_count(client, str(final_view["game_id"]))

    # 首屏：目标标题 / 初始进度 / 未勾选子目标 / 可做动作引导
    assert "【目标】让信号灯亮起来 · 0/3" in joined
    assert "[ ] 问问守夜人老兵：信号灯怎么点" in joined
    assert "【引导】可以试试：" in joined
    # 中途 :status：目标 1 已完成（勾选态玩家可见）
    assert "【目标】让信号灯亮起来 · 1/3" in joined
    assert "[x] 问问守夜人老兵：信号灯怎么点" in joined
    # 结局屏：服务端结局正文 + 固定退出提示（结局后再输入行动只重复提示，不产生回合）
    assert "整支火把" in joined and "活人" in joined
    assert joined.count(_ENDING_HINT) == 2, "结局屏 + 结局后输入各一次提示"
    assert "再见。" in joined
    # 零写入：开局 + 14 个写回合 = 15 条事件；结局后的「环顾四周」没有新增
    assert events == 15
    # 状态行显示回血后的满血与登塔位置
    assert "【状态】HP 20/20 ｜ 位置 哨站瞭望塔 ｜ 行囊：信号火把、火石" in joined
    assert final_view is not None and final_view["status"] == "ended"

    # 输出洁净：无协议字段（json/game_id/version/idempotency…）与内部 ULID
    console.assert_clean_output(joined)
    assert _ULID_RE.search(joined) is None


# ---------------------------------------------------------------------------
# 2. CLI 入口链路：``console.main([--db … --scenario story --new])``
# ---------------------------------------------------------------------------
def test_console_cli_story_new_flag_reaches_ending(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """CLI：``--db/--scenario story/--new`` 解析 → prepare_session → 交互循环直达结局。"""
    db_path = tmp_path / "cli-story.db"
    previous = os.environ.get("TEXTGAME_DB_URL")
    rendered: list[str] = []
    real_run_interactive = console.run_interactive
    lines = [*_STORY_FLOW, ":quit"]

    def run_with_script(
        session: console.GameSession,
        *,
        read_line: Callable[[str], str] = input,
        out: Callable[[str], None] = print,
        start_notice: str | None = None,
    ) -> None:
        """注入脚本化 stdin 并收集输出（保持真实交互循环逻辑不变）。"""
        del read_line, out  # 用脚本化 reader 与收集器替换
        real_run_interactive(
            session,
            read_line=_scripted_reader(lines),
            out=rendered.append,
            start_notice=start_notice,
        )

    monkeypatch.setattr(console, "run_interactive", run_with_script)
    try:
        code = console.main(["--db", str(db_path), "--scenario", "story", "--new", "--seed", "42"])
    finally:
        # console.build_app 会写 TEXTGAME_DB_URL（进程级）：恢复环境，避免污染其它用例
        if previous is None:
            os.environ.pop("TEXTGAME_DB_URL", None)
        else:
            os.environ["TEXTGAME_DB_URL"] = previous
        get_settings.cache_clear()

    joined = "\n".join(rendered)
    assert code == 0, joined
    assert "【存档】已按 --new 新开一局。" in joined
    assert "【目标】让信号灯亮起来 · 0/3" in joined
    assert _ENDING_HINT == console.ENDING_HINT
    assert console.ENDING_HINT in joined
    console.assert_clean_output(joined)
    assert _ULID_RE.search(joined) is None
    # 存档确实落在 --db 指定文件（而不是默认 data/textgame.db）
    assert db_path.exists() and db_path.stat().st_size > 0
