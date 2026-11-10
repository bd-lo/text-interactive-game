"""R3 P2-4（HTTP 级）：story 缺材料失败的建议优先指向可执行一步 + 点名材料位置。

真实生产装配（临时 SQLite + 进程内 TestClient，不占端口/不联网）；复现 player-rep R3
的 AC-09c 路径：入口 → 铁匠铺拿两卷铜线（无火把）→ 打造信号火把失败，验证：
- hint 点名「缺少材料/火把」+ 火把所在房间「废弃哨站·入口」；
- ``suggested_actions[0]`` 是可提交的导航指令（提交后真的移动到入口），而不是房间通用建议；
- 失败回合零写入（events/version/turn 不变）、无内部 ID 泄漏；
- 非 craft 失败（无出口）建议保持修复前行为（suggested[0] 向西）。
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from textgame import console
from textgame.config.settings import get_settings

pytestmark = [pytest.mark.integration]

_ULID_RE = __import__("re").compile(r"(?:room|item|npc|char|game)_[0-9A-HJKMNP-TV-Z]{26}")
_FORBIDDEN_INTERNAL = ("item_", "npc_", "room_", "json", "game_id", "version", "idempotency")


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


def _create_story(client: TestClient) -> tuple[str, int]:
    """创建 story 存档并返回 ``(game_id, version)``。"""
    response = client.post(
        "/api/v1/games",
        headers={"Idempotency-Key": _new_key("craft-focus-create")},
        json={"seed": 42, "scenario": "story"},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    return str(payload["game_id"]), int(payload["version"])


def _view(client: TestClient, game_id: str) -> dict[str, Any]:
    """只读 ``/view`` 响应（200 断言）。"""
    response = client.get(f"/api/v1/games/{game_id}/view")
    assert response.status_code == 200, response.text
    return response.json()


def _turn(client: TestClient, game_id: str, version: int, text: str) -> tuple[dict[str, Any], int]:
    """提交一个回合，返回 ``(响应, 下一版本)``。"""
    response = client.post(
        "/api/v1/turns",
        headers={"Idempotency-Key": _new_key("craft-focus-turn")},
        json={"game_id": game_id, "input": text, "expected_version": version},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    return payload, int(payload["version"])


def _hint_join(result: dict[str, Any]) -> str:
    """失败回合全部 effects.hint 拼串。"""
    return " ".join(str(effect.get("hint", "")) for effect in result.get("effects") or [])


def test_forge_missing_torch_suggestion_names_item_and_is_executable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC-09c 后续：铁匠铺缺火把 → hint 点名火把+入口，suggested[0] 提交后真的移动过去。"""
    app = _build_app_with_db(monkeypatch, tmp_path / "craft-focus-forge.db")
    try:
        with TestClient(app) as client:
            game_id, version = _create_story(client)
            for text in ("向东走进入口", "向东走进铁匠铺", "拿起铜线", "拿起铜线"):
                result, version = _turn(client, game_id, version, text)
                assert result["ok"] is True, (text, result)
            rejected, rejected_version = _turn(client, game_id, version, "打造信号火把")
            hint = _hint_join(rejected)
            suggestions = list(rejected.get("suggested_actions") or [])
            view_after_fail = _view(client, game_id)

            # 建议[0] 提交（同一失败回合之后，版本未推进）
            moved, _moved_version = _turn(client, game_id, rejected_version, suggestions[0])
            view_after_move = _view(client, game_id)
    finally:
        _dispose(app)

    assert rejected["ok"] is False
    assert rejected["events"] == []
    assert rejected_version == version
    assert view_after_fail["version"] == version
    assert "缺少材料" in hint and "火把" in hint
    assert "废弃哨站·入口" in hint
    assert suggestions and suggestions[0] == "去废弃哨站·入口拿火把"
    assert len(suggestions) <= 4 and len(set(suggestions)) == len(suggestions)
    # 建议真的可执行：提交后移动到入口（断桥/铁匠铺之外的房间）
    assert moved["ok"] is True, moved
    assert view_after_move["room"]["title"] == "废弃哨站·入口"
    assert _ULID_RE.search(hint + " " + " ".join(suggestions)) is None
    for term in _FORBIDDEN_INTERNAL:
        assert term not in (hint + " " + " ".join(suggestions)).lower(), term


def test_missing_material_in_current_room_suggests_pick_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """入口打造（火把就在入口地面未拾取）：hint 提示就在这间屋，suggested[0]「拿起火把」。"""
    app = _build_app_with_db(monkeypatch, tmp_path / "craft-focus-hub.db")
    try:
        with TestClient(app) as client:
            game_id, version = _create_story(client)
            result, version = _turn(client, game_id, version, "向东走进入口")
            assert result["ok"] is True
            rejected, rejected_version = _turn(client, game_id, version, "打造信号火把")
            hint = _hint_join(rejected)
            suggestions = list(rejected.get("suggested_actions") or [])
            took, _ = _turn(client, game_id, rejected_version, suggestions[0])
            view = _view(client, game_id)
    finally:
        _dispose(app)

    assert rejected["ok"] is False and rejected_version == version
    assert "火把" in hint and "就在这间屋里" in hint
    assert suggestions and suggestions[0] == "拿起火把"
    assert took["ok"] is True
    assert "火把" in [item["name"] for item in view["inventory"]]


def test_non_craft_failure_suggestions_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """非 craft 失败（铁匠铺无北出口）：建议仍为修复前口径（suggested[0] 向西）。"""
    app = _build_app_with_db(monkeypatch, tmp_path / "craft-focus-noncraft.db")
    try:
        with TestClient(app) as client:
            game_id, version = _create_story(client)
            for text in ("向东走进入口", "向东走进铁匠铺"):
                result, version = _turn(client, game_id, version, text)
                assert result["ok"] is True
            rejected, rejected_version = _turn(client, game_id, version, "向北走")
            hint = _hint_join(rejected)
            suggestions = list(rejected.get("suggested_actions") or [])
    finally:
        _dispose(app)

    assert rejected["ok"] is False and rejected_version == version
    assert "没有往北的路" in hint
    assert suggestions and suggestions[0].startswith("向西")
    assert "火把在" not in hint
