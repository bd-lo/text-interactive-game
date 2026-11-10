"""H4 QA：世界书 API 契约黑盒（真实组合根 + 临时 SQLite + TestClient，禁网）。\r\n\r\n依据 `H4-CONTRACT.md` §2 独立验收：导入（SillyTavern entries map/array、Character Card V2\r\n`data.character_book`、简化 schema）、413/422/404/409、写幂等、软删、列表分页/搜索、\r\n导入→`POST /games` 绑定→`/view.world_book`+`/view.lore`→回合；旧场景无世界书时字段为 null\r\n且 demo 7 屏长度逐字不变。等价类/边界值为主，不重复 backend 自己的测试实现。\r\n"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

SIMPLE_BOOK: dict[str, Any] = {
    "name": "QA 世界书",
    "entries": [
        {"uid": 1, "keys": ["哨站", "信号灯"], "content": "哨站的信号灯灭了两夜。", "order": 10},
        {"uid": 2, "keys": ["守夜人", "老兵"], "content": "老兵守着废弃哨站。", "order": 20},
    ],
}

TAVERN_MAP: dict[str, Any] = {
    "name": "酒馆书",
    "entries": {
        "1": {"uid": 1, "key": "哨站, 信号灯", "content": "灯", "order": 10},
        "2": {"uid": 2, "key": "老兵", "content": "人", "order": 20},
    },
}

TAVERN_ARRAY: dict[str, Any] = {
    "entries": [
        {"uid": 5, "key": ["枯井", "寒气"], "content": "井底很冷", "order": 30},
        {"uid": 6, "key": "火石", "content": "火石能点灯", "order": 40},
    ]
}

CHARACTER_CARD: dict[str, Any] = {
    "spec": "chara_card_v2",
    "data": {
        "name": "灰烬旅人",
        "character_book": {
            "name": "旅人手记",
            "entries": [
                {"id": 1, "keys": ["旧提灯"], "content": "提灯是旧的", "order": 10},
                {"id": 2, "keys": ["矿镇"], "content": "矿镇在河谷尽头", "order": 20},
            ],
        },
    },
}


@pytest.fixture
def wb_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    """临时 SQLite + 生产组合根（含 WorldBookService 装配）的 TestClient。"""
    from textgame import console
    from textgame.config.settings import get_settings

    db_path = tmp_path / "wb-api.db"
    monkeypatch.setenv("TEXTGAME_DB_URL", f"sqlite:///{db_path.as_posix()}")
    get_settings.cache_clear()
    import textgame.main as main_module

    console.dispose_app_engine(main_module.app)
    app = main_module._build_app()
    try:
        with TestClient(app) as client:
            yield client
    finally:
        console.dispose_app_engine(app)


def _import(
    client: TestClient,
    payload: dict[str, Any],
    *,
    key: str = "wb-import-key-0001",
    name: str | None = None,
    raw: bytes | None = None,
) -> Any:
    """multipart 导入；返回响应。"""
    content = raw if raw is not None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    data = {"name": name} if name is not None else None
    return client.post(
        "/api/v1/world-books/import",
        headers={"Idempotency-Key": key},
        files={"file": ("book.json", content, "application/json")},
        data=data,
    )


def _imported_id(client: TestClient, payload: dict[str, Any], *, key: str) -> str:
    """导入并断言 201，返回 world_book_id。"""
    resp = _import(client, payload, key=key)
    assert resp.status_code == 201, resp.text
    return str(resp.json()["world_book_id"])


def test_validate_formats(wb_client: TestClient) -> None:
    """validate：四种格式 200 + preview(detected_format/entry_count)，非法返回 valid=false。"""
    for payload, expected in (
        (SIMPLE_BOOK, "manual"),
        (TAVERN_MAP, "tavern"),
        (TAVERN_ARRAY, "tavern"),
        (CHARACTER_CARD, "character_card"),
    ):
        resp = wb_client.post("/api/v1/world-books/validate", json={"payload": payload})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["valid"] is True
        assert body["preview"]["detected_format"] == expected
        assert body["preview"]["entry_count"] == 2

    bad = wb_client.post("/api/v1/world-books/validate", json={"payload": {"foo": 1}})
    assert bad.status_code == 200
    assert bad.json()["valid"] is False and bad.json()["errors"]


def test_import_idempotent_and_conflict(wb_client: TestClient) -> None:
    """导入同键同参幂等回放；同键异参 409 CONFLICT。"""
    first = _import(wb_client, SIMPLE_BOOK, key="wb-import-idem-0001")
    assert first.status_code == 201, first.text
    replay = _import(wb_client, SIMPLE_BOOK, key="wb-import-idem-0001")
    assert replay.status_code == 201 and replay.json() == first.json()

    conflict = _import(wb_client, TAVERN_MAP, key="wb-import-idem-0001")
    assert conflict.status_code == 409
    assert conflict.json()["ok"] is False and conflict.json()["error_code"] == "CONFLICT"


def test_import_413_422_and_404(wb_client: TestClient) -> None:
    """413 超 256KB；422 非法 JSON / 不支持格式；未知 id 404。"""
    huge = b'{"entries": [' + b" " * (300 * 1024) + b"]}"
    too_big = _import(wb_client, {}, key="wb-import-big-0001", raw=huge)
    assert too_big.status_code == 413

    invalid_json = _import(wb_client, {}, key="wb-import-bad-0001", raw=b"{not json")
    assert invalid_json.status_code == 422

    unsupported = _import(wb_client, {"foo": "bar"}, key="wb-import-bad-0002")
    assert unsupported.status_code == 422

    assert wb_client.get("/api/v1/world-books/wb_missing").status_code == 404
    # 实现要求写操作带幂等键（H4-CONTRACT 未列，QA 已报 P2）；未知 id 在带键时 404\n    missing_delete = wb_client.delete(\n        "/api/v1/world-books/wb_missing", headers={"Idempotency-Key": "wb-del-missing-0001"}\n    )\n    assert missing_delete.status_code == 404


def test_list_pagination_and_search(wb_client: TestClient) -> None:
    """列表分页 limit/offset 与 query 搜索；limit>100 → 422。"""
    for index in range(3):
        payload = json.loads(json.dumps(SIMPLE_BOOK))
        payload["name"] = f"分页书{index}"
        _imported_id(wb_client, payload, key=f"wb-list-{index}-0001")

    first_page = wb_client.get("/api/v1/world-books", params={"limit": 2}).json()
    assert len(first_page["world_books"]) == 2 and first_page["total"] == 3
    second_page = wb_client.get("/api/v1/world-books", params={"limit": 2, "offset": 2}).json()
    assert len(second_page["world_books"]) == 1

    search = wb_client.get("/api/v1/world-books", params={"query": "分页书1"}).json()
    assert search["total"] == 1 and search["world_books"][0]["name"] == "分页书1"

    assert wb_client.get("/api/v1/world-books", params={"limit": 101}).status_code == 422


def test_detail_entries_and_soft_delete(wb_client: TestClient) -> None:
    """详情返回 entries 全字段；删除软删幂等，删后 404 且列表排除。"""
    wb_id = _imported_id(wb_client, SIMPLE_BOOK, key="wb-detail-key-0001")
    detail = wb_client.get(f"/api/v1/world-books/{wb_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["world_book"]["entry_count"] == 2
    entries = body["entries"]
    assert len(entries) == 2
    assert {entry["entry_uid"] for entry in entries} == {1, 2}
    for field in ("keys", "secondary_keys", "content", "enabled", "constant", "order"):
        assert field in entries[0]

    deleted = wb_client.delete(
        f"/api/v1/world-books/{wb_id}", headers={"Idempotency-Key": "wb-del-0001"}
    )
    assert deleted.status_code == 200 and deleted.json()["deleted"] is True
    again = wb_client.delete(
        f"/api/v1/world-books/{wb_id}", headers={"Idempotency-Key": "wb-del-0002"}
    )
    assert again.status_code == 200 and again.json()["deleted"] is True
    assert wb_client.get(f"/api/v1/world-books/{wb_id}").status_code == 404
    listing = wb_client.get("/api/v1/world-books").json()
    assert all(item["world_book_id"] != wb_id for item in listing["world_books"])


def test_game_binding_view_lore_and_turn(wb_client: TestClient) -> None:
    """导入→建局绑定→/view world_book+lore→回合；lore 只读确定性；create params 记账。"""
    wb_id = _imported_id(wb_client, SIMPLE_BOOK, key="wb-bind-key-0001")
    created = wb_client.post(
        "/api/v1/games",
        headers={"Idempotency-Key": "wb-bind-game-0001"},
        json={"seed": 42, "scenario": "story", "world_book_id": wb_id},
    )
    assert created.status_code == 201, created.text
    game_id = created.json()["game_id"]

    # game_created.params 记录 world_book_id/name（仅传入时）
    replay = wb_client.get(
        f"/api/v1/games/{game_id}/replay", params={"from_seq": 1, "limit": 10}
    ).json()
    params = replay["events"][0]["params"]
    assert params["world_book_id"] == wb_id
    assert params["world_book_name"] == "QA 世界书"

    view = wb_client.get(f"/api/v1/games/{game_id}/view").json()
    assert view["world_book"]["world_book_id"] == wb_id
    assert view["world_book"]["entry_count"] == 2
    assert isinstance(view["lore"]["active_entries"], list)
    # 只读：重复 /view 不推进版本、lore 结果一致
    before = wb_client.get(f"/api/v1/games/{game_id}").json() if False else None
    view2 = wb_client.get(f"/api/v1/games/{game_id}/view").json()
    assert view2["version"] == view["version"]
    assert view2["lore"] == view["lore"]

    turn = wb_client.post(
        "/api/v1/turns",
        headers={"Idempotency-Key": "wb-bind-turn-0001"},
        json={"game_id": game_id, "input": "环顾四周", "expected_version": view["version"]},
    )
    assert turn.status_code == 200, turn.text
    body = turn.json()
    assert body["ok"] is True
    assert 80 <= len(body["text"]) <= 150
    del before


def test_unknown_world_book_binding_404(wb_client: TestClient) -> None:
    """POST /games 带未知 world_book_id → 404 NOT_FOUND，且不产生存档。"""
    resp = wb_client.post(
        "/api/v1/games",
        headers={"Idempotency-Key": "wb-bind-missing-0001"},
        json={"seed": 42, "world_book_id": "wb_missing"},
    )
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "NOT_FOUND"


def test_legacy_game_view_null_fields_and_demo_unchanged(wb_client: TestClient) -> None:
    """旧场景（m2）不绑世界书：/view world_book/lore 为 null，demo 7 屏长度逐字不变。"""
    from textgame import demo

    created = wb_client.post(
        "/api/v1/games",
        headers={"Idempotency-Key": "wb-legacy-game-0001"},
        json={"seed": 42},
    ).json()
    view = wb_client.get(f"/api/v1/games/{created['game_id']}/view").json()
    assert view["world_book"] is None
    assert view["lore"] is None

    records = demo.run_walkthrough(wb_client)
    demo.verify_walkthrough(records)
    assert [record["text_len"] for record in records] == list(demo.EXPECTED_TEXT_LEN)


def test_game_view_has_world_book_fields_for_legacy_and_bound(wb_client: TestClient) -> None:
    """P0 回归：GET /games/{id} 对 legacy 存档 200 且新字段 null；绑世界书存档 200 且非空。

    背景：`QueryService.get_game_view` 曾返回 world_book/world_book_id/lore，而
    `GameViewResponse(extra="forbid")` 未声明这些字段 → 所有存档 500。本用例钉住修复后契约。
    """
    # legacy m2：新字段为 null，不得再 500
    legacy = wb_client.post(
        "/api/v1/games",
        headers={"Idempotency-Key": "wb-gv-legacy-0001"},
        json={"seed": 42},
    ).json()
    legacy_view = wb_client.get(f"/api/v1/games/{legacy['game_id']}")
    assert legacy_view.status_code == 200, legacy_view.text
    legacy_body = legacy_view.json()
    assert legacy_body["scenario"] == "m2"
    assert legacy_body["world_book"] is None
    assert legacy_body["world_book_id"] is None
    assert legacy_body["lore"] is None

    # 世界书绑定存档：world_book/world_book_id/lore 非空且与导入一致
    wb_id = _imported_id(wb_client, SIMPLE_BOOK, key="wb-gv-bound-0001")
    bound = wb_client.post(
        "/api/v1/games",
        headers={"Idempotency-Key": "wb-gv-bound-game-0001"},
        json={"seed": 42, "scenario": "story", "world_book_id": wb_id},
    ).json()
    bound_view = wb_client.get(f"/api/v1/games/{bound['game_id']}")
    assert bound_view.status_code == 200, bound_view.text
    bound_body = bound_view.json()
    assert bound_body["scenario"] == "story"
    assert bound_body["world_book_id"] == wb_id
    assert bound_body["world_book"]["world_book_id"] == wb_id
    assert bound_body["world_book"]["entry_count"] == 2
    assert isinstance(bound_body["lore"], dict)
    assert isinstance(bound_body["lore"]["active_entries"], list)
