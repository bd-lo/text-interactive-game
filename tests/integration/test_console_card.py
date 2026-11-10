"""R3 console 按卡开局：``--card-id`` / ``--list-cards`` + 旧 CLI 行为不变（HTTP/CLI 混合）。

装配口径与 ``test_console_story.py`` 一致：临时 SQLite + 进程内组合根（``console.build_app``，
不占端口、不联网）。覆盖：
1. 存卡（``POST /api/v1/character-cards``）→ ``console --card-id``：真实 CLI 入口建局，
   ``game_created.params.card_id`` 落库、首屏 view 的 HP/行囊来自人物卡；
2. ``--list-cards``：列出卡名+卡 ID（空库/无端点友好提示）；
3. 未知卡：exit 1、中文提示、无 traceback、库里不产生新对局；
4. ``--card-id`` 与 ``--scenario``/``--continue`` 互斥：exit 1 中文提示；
5. 旧 CLI 不变：``--new --scenario m2`` 仍走原开局提示（card 分支不触发）。
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from textgame import console
from textgame.config.settings import get_settings

pytestmark = [pytest.mark.integration]

_UNKNOWN_CARD_ID = "card_01J9Z3K4Q7X8Y9Z0ABCDEFGHJZ"
_CARD: dict[str, Any] = {
    "schema_version": "1.0",
    "name": "灰烬旅人",
    "attributes": {"str": 10, "dex": 12, "con": 11, "int": 9, "wis": 10, "cha": 8, "max_hp": 18},
    "hp": 7,
    "starting_items": ["item.potion"],
    "background": "从雪线那头走来，旧伤未愈，想在天亮前找条活路。",
    "appearance": None,
    "source": "manual",
    "generated_by": None,
}


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


def _new_key(prefix: str) -> str:
    """唯一幂等键（每次请求新键，避免回放旧结果）。"""
    return f"{prefix}-{uuid.uuid4().hex}"


def _save_card(client: TestClient) -> str:
    """保存一张合法人物卡 → 返回 ``card_id``。"""
    response = client.post(
        "/api/v1/character-cards",
        headers={"Idempotency-Key": _new_key("console-card-save")},
        json=_CARD,
    )
    assert response.status_code == 201, response.text
    return str(response.json()["card_id"])


def _run_cli(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> tuple[int, dict[str, Any]]:
    """执行 ``console.main(argv)``；拦截交互循环并记录 session/notice（不改真实 stdin）。

    同时负责恢复 ``TEXTGAME_DB_URL``（``console.build_app`` 会写进程级环境变量）。
    """
    captured: dict[str, Any] = {}

    def fake_run_interactive(
        session: console.GameSession,
        *,
        read_line: Callable[[str], str] = input,
        out: Callable[[str], None] = print,
        start_notice: str | None = None,
    ) -> None:
        del read_line, out
        captured["session"] = session
        captured["start_notice"] = start_notice
        captured["view"] = session.view()

    monkeypatch.setattr(console, "run_interactive", fake_run_interactive)
    previous = os.environ.get("TEXTGAME_DB_URL")
    try:
        code = console.main(argv)
    finally:
        if previous is None:
            os.environ.pop("TEXTGAME_DB_URL", None)
        else:
            os.environ["TEXTGAME_DB_URL"] = previous
        get_settings.cache_clear()
    return code, captured


@pytest.fixture
def card_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[tuple[Path, str]]:
    """临时库 + 一张已保存的人物卡；用例结束释放 Engine。"""
    db_path = tmp_path / "console-card.db"
    app = _build_app_with_db(monkeypatch, db_path)
    try:
        with TestClient(app) as client:
            card_id = _save_card(client)
    finally:
        _dispose(app)
    yield db_path, card_id


def test_card_id_cli_starts_game_from_card(
    monkeypatch: pytest.MonkeyPatch, card_db: tuple[Path, str]
) -> None:
    """``--card-id``：真实 CLI 入口建局；开局事件带 card_id、首屏 HP/行囊来自人物卡。"""
    db_path, card_id = card_db

    code, captured = _run_cli(monkeypatch, ["--db", str(db_path), "--card-id", card_id])

    assert code == 0
    assert captured["start_notice"] == "【存档】已按人物卡开局。"
    view = captured["view"]
    assert view is not None
    assert view["room"]["title"] == "废弃哨站·入口"
    assert (view["hp"], view["max_hp"]) == (7, 18)
    assert "治疗药水" in [item["name"] for item in view["inventory"]]
    game_id = str(view["game_id"])

    # 重新装配只读核对：开局事件 params.card_id 落库、存档场景按 m2 推断
    app = _build_app_with_db(monkeypatch, db_path)
    try:
        with TestClient(app) as client:
            replay = client.get(
                f"/api/v1/games/{game_id}/replay", params={"from_seq": 1, "limit": 10}
            )
            assert replay.status_code == 200, replay.text
            events = replay.json()["events"]
            created = next(event for event in events if event["method"] == "game_created")
            games = client.get("/api/v1/games").json()["games"]
    finally:
        _dispose(app)

    assert created["params"]["card_id"] == card_id
    assert len(games) == 1 and games[0]["game_id"] == game_id
    assert games[0]["scenario"] == "m2"


def test_list_cards_prints_saved_card(
    monkeypatch: pytest.MonkeyPatch, card_db: tuple[Path, str], capsys: pytest.CaptureFixture[str]
) -> None:
    """``--list-cards``：输出卡名与完整卡 ID、给出按卡开局命令、exit 0。"""
    db_path, card_id = card_db

    code, _captured = _run_cli(monkeypatch, ["--db", str(db_path), "--list-cards"])

    out = capsys.readouterr().out
    assert code == 0
    assert "最近人物卡" in out
    assert "灰烬旅人" in out
    assert card_id in out
    assert "--card-id" in out


def test_list_cards_empty_library_is_friendly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """空卡库：友好提示"还没有人物卡"，exit 0（不建局、不写库）。"""
    db_path = tmp_path / "console-card-empty.db"

    code, captured = _run_cli(monkeypatch, ["--db", str(db_path), "--list-cards"])

    out = capsys.readouterr().out
    assert code == 0
    assert "还没有人物卡" in out
    assert captured == {}


def test_unknown_card_id_is_friendly_and_creates_no_game(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """未知/已删除卡：exit 1、中文提示、无 traceback、库里不产生新对局。"""
    db_path = tmp_path / "console-card-unknown.db"

    code, captured = _run_cli(monkeypatch, ["--db", str(db_path), "--card-id", _UNKNOWN_CARD_ID])

    out = capsys.readouterr().out
    assert code == 1
    assert "找不到这张人物卡" in out
    assert "Traceback" not in out
    assert captured == {}

    app = _build_app_with_db(monkeypatch, db_path)
    try:
        with TestClient(app) as client:
            assert client.get("/api/v1/games").json()["games"] == []
    finally:
        _dispose(app)


@pytest.mark.parametrize(
    "extra_args",
    [["--scenario", "story"], ["--continue"]],
    ids=["scenario-conflict", "continue-conflict"],
)
def test_card_id_conflicts_are_friendly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    extra_args: list[str],
) -> None:
    """``--card-id`` 与显式场景/强制继续互斥：exit 1 中文提示（不建局）。"""
    db_path = tmp_path / "console-card-conflict.db"

    code, captured = _run_cli(
        monkeypatch, ["--db", str(db_path), "--card-id", _UNKNOWN_CARD_ID, *extra_args]
    )

    out = capsys.readouterr().out
    assert code == 1
    assert "不能与" in out
    assert captured == {}


def test_legacy_new_scenario_cli_unchanged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """旧 CLI（``--new --scenario m2``）行为不变：仍走原开局提示，不触发卡分支。"""
    db_path = tmp_path / "console-card-legacy.db"

    code, captured = _run_cli(
        monkeypatch, ["--db", str(db_path), "--new", "--scenario", "m2", "--seed", "42"]
    )

    assert code == 0
    assert captured["start_notice"] == "【存档】已按 --new 新开一局。"
    assert captured["view"] is not None
    assert captured["view"]["room"]["title"] == "废弃哨站·入口"
