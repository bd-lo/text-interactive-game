"""H4 世界书 HTTP API 端到端测试（临时 SQLite + 进程内 TestClient，不占端口、不联网）。

覆盖 H4-CONTRACT §2/§6：multipart 导入（201/413/422/409 幂等）、validate 只读、列表分页/搜索、
详情、软删与 404、``POST /games`` 世界书绑定（未知 404、``game_created.params`` 记录）、
``/view.world_book``/``/view.lore`` 加法字段、旧场景 null 兼容；以及 L1 环境伤害：
枯井反复受伤 → 倒下结局、药水可救回、旧场景不产生 ENVIRONMENT_DAMAGE 事件。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from textgame import console
from textgame.config.settings import get_settings
from textgame.domain.scenario_story import STORY_WELL_ROOM_ID

pytestmark = [pytest.mark.integration]

_TAVERN: dict[str, Any] = {
    "name": "余烬哨站设定",
    "description": "北境哨站的旧事。",
    "entries": {
        "0": {
            "uid": 0,
            "key": "哨站,大桥",
            "content": "哨站建在封冻河谷的断桥尽头，风雪一起就看不见对面的路。",
            "constant": True,
            "order": 10,
        },
        "1": {
            "uid": 1,
            "key": "信号灯",
            "content": "信号灯由守夜人点燃。",
            "order": 20,
        },
        "2": {
            "uid": 2,
            "key": "枯井",
            "content": "枯井里的寒气会伤人。",
            "order": 30,
            "enabled": False,
        },
    },
}


def _new_key(prefix: str) -> str:
    """唯一幂等键（每次请求新键，避免回放旧结果）。"""
    return f"{prefix}-{uuid.uuid4().hex}"


def _dispose(app: FastAPI) -> None:
    """释放组合根内 SQLite Engine 的文件句柄（Windows 临时文件收尾）。"""
    console.dispose_app_engine(app)


def _build_app(monkeypatch: pytest.MonkeyPatch, db_path: Path) -> FastAPI:
    """把 ``TEXTGAME_DB_URL`` 指向临时库后重建生产组合根（含世界书装配）。"""
    monkeypatch.setenv("TEXTGAME_DB_URL", f"sqlite:///{db_path.as_posix()}")
    get_settings.cache_clear()
    import textgame.main as main_module

    _dispose(main_module.app)
    return main_module._build_app()


def _import_book(
    client: TestClient, payload: dict[str, Any], *, name: str | None = None
) -> dict[str, Any]:
    """multipart 导入世界书（201 断言），返回响应体。"""
    content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    data = {"name": name} if name is not None else None
    response = client.post(
        "/api/v1/world-books/import",
        headers={"Idempotency-Key": _new_key("wb-import")},
        files={"file": ("lore.json", content, "application/json")},
        data=data,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_game(client: TestClient, **body: Any) -> dict[str, Any]:
    """``POST /games``（201 断言）。"""
    response = client.post(
        "/api/v1/games",
        headers={"Idempotency-Key": _new_key("game-create")},
        json=body,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _turn(client: TestClient, game_id: str, version: int, text: str) -> dict[str, Any]:
    """提交一个回合（200 断言）。"""
    response = client.post(
        "/api/v1/turns",
        headers={"Idempotency-Key": _new_key("turn")},
        json={"game_id": game_id, "input": text, "expected_version": version},
    )
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# 导入 / 校验 / 列表 / 详情 / 软删
# ---------------------------------------------------------------------------
def test_import_list_detail_delete_roundtrip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """导入 → 列表 → 详情 → 软删 → 404/幂等，全链路口径与契约一致。"""
    app = _build_app(monkeypatch, tmp_path / "wb-api.db")
    try:
        with TestClient(app) as client:
            imported = _import_book(client, _TAVERN)
            world_book_id = imported["world_book_id"]
            assert imported["entry_count"] == 3
            assert imported["source"] == "tavern"
            assert imported["warnings"] == []

            listing = client.get("/api/v1/world-books", params={"limit": 20, "offset": 0})
            assert listing.status_code == 200
            assert listing.json()["total"] == 1
            item = listing.json()["world_books"][0]
            assert set(item) == {
                "world_book_id",
                "name",
                "description",
                "source",
                "entry_count",
                "updated_at",
            }
            assert item["world_book_id"] == world_book_id

            search = client.get("/api/v1/world-books", params={"query": "余烬"})
            assert search.json()["total"] == 1

            detail = client.get(f"/api/v1/world-books/{world_book_id}")
            assert detail.status_code == 200
            payload = detail.json()
            assert set(payload) == {"world_book", "entries"}
            assert payload["world_book"]["world_book_id"] == world_book_id
            entry = payload["entries"][0]
            assert set(entry) == {
                "entry_uid",
                "keys",
                "secondary_keys",
                "content",
                "enabled",
                "constant",
                "order",
                "position",
                "depth",
                "selective",
                "selective_logic",
            }
            assert entry["keys"] == ["哨站", "大桥"]
            assert payload["entries"][2]["enabled"] is False

            deleted = client.delete(
                f"/api/v1/world-books/{world_book_id}",
                headers={"Idempotency-Key": _new_key("wb-del")},
            )
            assert deleted.status_code == 200
            assert deleted.json() == {"world_book_id": world_book_id, "deleted": True}
            again = client.delete(
                f"/api/v1/world-books/{world_book_id}",
                headers={"Idempotency-Key": _new_key("wb-del")},
            )
            assert again.status_code == 200 and again.json()["deleted"] is True
            assert client.get(f"/api/v1/world-books/{world_book_id}").status_code == 404
            assert client.get("/api/v1/world-books").json()["total"] == 0
    finally:
        _dispose(app)


def test_validate_endpoint_is_read_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``/validate``：合法→valid True+preview；非法→valid False+errors；只读零落库。"""
    app = _build_app(monkeypatch, tmp_path / "wb-validate.db")
    try:
        with TestClient(app) as client:
            valid = client.post("/api/v1/world-books/validate", json={"payload": _TAVERN})
            assert valid.status_code == 200
            body = valid.json()
            assert body["valid"] is True
            assert body["preview"] == {
                "name": "余烬哨站设定",
                "entry_count": 3,
                "detected_format": "tavern",
            }

            invalid = client.post("/api/v1/world-books/validate", json={"payload": {"bad": 1}})
            assert invalid.status_code == 200
            assert invalid.json()["valid"] is False
            assert invalid.json()["errors"]
            assert client.get("/api/v1/world-books").json()["total"] == 0

            missing = client.post("/api/v1/world-books/validate", json={})
            assert missing.status_code == 422
            assert missing.json()["ok"] is False
    finally:
        _dispose(app)


def test_import_rejects_oversized_invalid_and_missing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """413 超 256KB、422 非法 JSON/格式不支持、422 缺 file 部件。"""
    app = _build_app(monkeypatch, tmp_path / "wb-errors.db")
    try:
        with TestClient(app) as client:
            oversized = client.post(
                "/api/v1/world-books/import",
                headers={"Idempotency-Key": _new_key("wb-import")},
                files={"file": ("lore.json", b" " * (256 * 1024 + 64), "application/json")},
            )
            assert oversized.status_code == 413
            assert oversized.json()["ok"] is False

            bad_json = client.post(
                "/api/v1/world-books/import",
                headers={"Idempotency-Key": _new_key("wb-import")},
                files={"file": ("lore.json", b"{not json", "application/json")},
            )
            assert bad_json.status_code == 422

            unsupported = client.post(
                "/api/v1/world-books/import",
                headers={"Idempotency-Key": _new_key("wb-import")},
                files={"file": ("lore.json", b'{"no": 1}', "application/json")},
            )
            assert unsupported.status_code == 422
            assert unsupported.json()["ok"] is False

            missing_file = client.post(
                "/api/v1/world-books/import",
                headers={"Idempotency-Key": _new_key("wb-import")},
                files={"other": ("lore.json", b"{}", "application/json")},
            )
            assert missing_file.status_code == 422

            no_key = client.post(
                "/api/v1/world-books/import",
                files={"file": ("lore.json", b"{}", "application/json")},
            )
            assert no_key.status_code == 422
            assert no_key.json()["ok"] is False
    finally:
        _dispose(app)


def test_import_idempotency_and_conflict(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """同键同参回放同 id（不新增）；同键异参 409。"""
    app = _build_app(monkeypatch, tmp_path / "wb-idem.db")
    try:
        with TestClient(app) as client:
            content = json.dumps(_TAVERN, ensure_ascii=False).encode("utf-8")
            key = _new_key("wb-import")
            first = client.post(
                "/api/v1/world-books/import",
                headers={"Idempotency-Key": key},
                files={"file": ("lore.json", content, "application/json")},
            ).json()
            replay = client.post(
                "/api/v1/world-books/import",
                headers={"Idempotency-Key": key},
                files={"file": ("lore.json", content, "application/json")},
            ).json()
            assert replay == first
            assert client.get("/api/v1/world-books").json()["total"] == 1

            conflict = client.post(
                "/api/v1/world-books/import",
                headers={"Idempotency-Key": key},
                files={"file": ("other.json", b'{"entries": []}', "application/json")},
            )
            assert conflict.status_code == 409
            assert conflict.json()["error_code"] == "CONFLICT"
    finally:
        _dispose(app)


# ---------------------------------------------------------------------------
# 建局绑定 → /view world_book + lore → 回合
# ---------------------------------------------------------------------------
def test_game_binding_exposes_world_book_and_lore(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """建局绑定世界书：``game_created.params`` 记录、``/view`` 两加法字段、回合可注入。"""
    app = _build_app(monkeypatch, tmp_path / "wb-game.db")
    try:
        with TestClient(app) as client:
            imported = _import_book(client, _TAVERN)
            world_book_id = imported["world_book_id"]
            created = _create_game(client, seed=42, scenario="story", world_book_id=world_book_id)
            game_id, version = created["game_id"], created["version"]

            # game_created.params 记录（仅传入时）
            events = client.get(f"/api/v1/games/{game_id}/replay").json()["events"]
            assert events[0]["event_type"] == "game_created"
            assert events[0]["params"]["world_book_id"] == world_book_id
            assert events[0]["params"]["world_book_name"] == "余烬哨站设定"

            view = client.get(f"/api/v1/games/{game_id}/view").json()
            assert view["world_book"] == {
                "world_book_id": world_book_id,
                "name": "余烬哨站设定",
                "entry_count": 3,
            }
            assert view["lore"] is not None
            active = view["lore"]["active_entries"]
            assert any(entry["content"].startswith("哨站建在封冻河谷") for entry in active)

            turn = _turn(client, game_id, version, "环顾四周")
            assert turn["ok"] is True
            assert 80 <= len(turn["text"]) <= 150
    finally:
        _dispose(app)


def test_unknown_world_book_on_create_is_404(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """建局绑定未知/已删除世界书 → 404 ``NOT_FOUND``，不建存档。"""
    app = _build_app(monkeypatch, tmp_path / "wb-game-404.db")
    try:
        with TestClient(app) as client:
            unknown = client.post(
                "/api/v1/games",
                headers={"Idempotency-Key": _new_key("game-bad")},
                json={"seed": 42, "scenario": "story", "world_book_id": "wb_" + "0" * 30},
            )
            assert unknown.status_code == 404
            assert unknown.json()["error_code"] == "NOT_FOUND"

            imported = _import_book(client, _TAVERN)
            client.delete(
                f"/api/v1/world-books/{imported['world_book_id']}",
                headers={"Idempotency-Key": _new_key("wb-del")},
            )
            deleted_bind = client.post(
                "/api/v1/games",
                headers={"Idempotency-Key": _new_key("game-deleted")},
                json={"scenario": "story", "world_book_id": imported["world_book_id"]},
            )
            assert deleted_bind.status_code == 404
            assert client.get("/api/v1/games").json()["total"] == 0
    finally:
        _dispose(app)


def test_legacy_game_without_world_book_keeps_null_fields(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """旧路径（不传 world_book_id）：``/view`` 两字段为 null、params 不含世界书键。"""
    app = _build_app(monkeypatch, tmp_path / "wb-legacy.db")
    try:
        with TestClient(app) as client:
            created = _create_game(client, seed=42)
            game_id = created["game_id"]
            events = client.get(f"/api/v1/games/{game_id}/replay").json()["events"]
            assert "world_book_id" not in events[0]["params"]
            assert "world_book_name" not in events[0]["params"]

            view = client.get(f"/api/v1/games/{game_id}/view").json()
            assert view["world_book"] is None
            assert view["lore"] is None
    finally:
        _dispose(app)


# ---------------------------------------------------------------------------
# L1 环境伤害：枯井阳气 → 倒下结局；药水可救回；旧场景无该事件
# ---------------------------------------------------------------------------
def test_story_well_environment_damage_reaches_fallen_ending(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """连续进入枯井（-6/次）使 HP 12→0，触发「倒下」结局且后续回合被拒。"""
    app = _build_app(monkeypatch, tmp_path / "wb-hazard.db")
    try:
        with TestClient(app) as client:
            created = _create_game(client, seed=42, scenario="story")
            game_id, version = created["game_id"], created["version"]
            hp_trace: list[int] = []
            for text in ("向东走", "向南走", "向北走", "向南走"):
                result = _turn(client, game_id, version, text)
                assert result["ok"] is True, (text, result)
                version = result["version"]
                hp_trace.append(client.get(f"/api/v1/games/{game_id}/view").json()["hp"])
            final = client.get(f"/api/v1/games/{game_id}/view").json()
            replay_events = client.get(f"/api/v1/games/{game_id}/replay").json()["events"]
    finally:
        _dispose(app)

    event_types = [event["event_type"] for event in replay_events]
    move_targets = [
        event["params"].get("to_room")
        for event in replay_events
        if event["event_type"] == "CHARACTER_MOVED"
    ]
    # 枯井两次：12 → 6 → 0（其余移动不扣血）
    assert hp_trace == [12, 6, 6, 0]
    assert final["hp"] == 0
    assert final["status"] == "ended"
    assert "你倒下了" in (final["goal"]["title"] if final["goal"] else "") or (
        final["ending_text"] or ""
    ).startswith("寒意从脚底")
    assert event_types.count("ENVIRONMENT_DAMAGE") == 2
    assert STORY_WELL_ROOM_ID in move_targets


def test_potion_can_rescue_after_well_damage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """受伤后仍可回地窖拿药水治疗（heal=8），HP 回升且未触发结局。"""
    app = _build_app(monkeypatch, tmp_path / "wb-rescue.db")
    try:
        with TestClient(app) as client:
            created = _create_game(client, seed=42, scenario="story")
            game_id, version = created["game_id"], created["version"]
            for text in ("向东走", "向南走进枯井"):
                result = _turn(client, game_id, version, text)
                assert result["ok"] is True, (text, result)
                version = result["version"]
            wounded = client.get(f"/api/v1/games/{game_id}/view").json()
            assert wounded["hp"] == 6

            for text in ("向北走", "向北走进地窖", "拿起治疗药水", "使用治疗药水"):
                result = _turn(client, game_id, version, text)
                assert result["ok"] is True, (text, result)
                version = result["version"]
            healed = client.get(f"/api/v1/games/{game_id}/view").json()
    finally:
        _dispose(app)

    assert wounded["status"] == "active"
    assert healed["hp"] == 14  # 6 + heal 8 = 14（未超 max 20）
    assert healed["status"] == "active"


def test_legacy_scenario_never_emits_environment_damage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """旧场景（m2）多次移动/进入房间不产生 ENVIRONMENT_DAMAGE 事件（golden 零影响）。"""
    app = _build_app(monkeypatch, tmp_path / "wb-hazard-legacy.db")
    try:
        with TestClient(app) as client:
            created = _create_game(client, seed=42)
            game_id, version = created["game_id"], created["version"]
            for text in ("向北走进地窖", "向南走", "向北走进地窖", "向南走"):
                result = _turn(client, game_id, version, text)
                assert result["ok"] is True, (text, result)
                version = result["version"]
            event_types = [
                event["event_type"]
                for event in client.get(f"/api/v1/games/{game_id}/replay").json()["events"]
            ]
    finally:
        _dispose(app)

    assert "ENVIRONMENT_DAMAGE" not in event_types
