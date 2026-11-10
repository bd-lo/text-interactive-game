"""集成测试：只读对局视图 ``GET /api/v1/games/{game_id}/view``（M6 状态面板/终端契约）。

覆盖：
- 开局后视图字段集合精确（顶层/room/inventory 逐键断言，无 ``last_text`` 等多余字段）；
- ``hp/max_hp/inventory/room`` 与 reducer 后状态及 ``QueryService.get_room_view``/
  ``get_inventory`` 一致（回合提交后同步：拿取 → 背包 +1、房间 -1、版本前进）；
- 只读语义：连续 GET 不产生事件、``version``/``snapshot_version`` 不变；
- 未知存档 → 404 统一 ``{ok:false, error_code:"NOT_FOUND", message}`` 信封。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from textgame.domain.models import Item
from textgame.domain.scenario import (
    ITEM_POTION,
    ITEM_POTION_NAME,
    ITEM_TORCH,
    ITEM_TORCH_NAME,
    NPC_SENTINEL,
    NPC_SENTINEL_NAME,
    PLAYER_ID,
    ROOM_CELLAR,
    ROOM_ENTRY,
    ROOM_ENTRY_NAME,
)

_GAME_KEY = "game-view-key-0001"

_VIEW_KEYS = {
    "game_id",
    "turn",
    "version",
    "snapshot_version",
    "status",
    "scenario",
    "hp",
    "max_hp",
    "inventory",
    "room",
    "goal",
    "ending_text",
    "suggested_actions",
}
_ROOM_KEYS = {"room_id", "title", "description", "exits", "exit_names", "items", "npcs"}
_ITEM_KEYS = {"item_id", "name"}
_NPC_KEYS = {"npc_id", "name"}


def _create_game(client: TestClient) -> dict:
    """``POST /api/v1/games``（201 断言），返回响应体。"""
    resp = client.post("/api/v1/games", headers={"Idempotency-Key": _GAME_KEY}, json={"seed": 42})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _get_view(client: TestClient, game_id: str) -> dict:
    """``GET /api/v1/games/{id}/view``（200 断言），返回响应体。"""
    resp = client.get(f"/api/v1/games/{game_id}/view")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _post_turn(client: TestClient, *, game_id: str, text: str, expected_version: int, key: str):
    """``POST /api/v1/turns``，返回 (status_code, body)。"""
    resp = client.post(
        "/api/v1/turns",
        headers={"Idempotency-Key": key},
        json={"game_id": game_id, "input": text, "expected_version": expected_version},
    )
    return resp.status_code, resp.json()


def test_view_after_create_has_exact_contract(game_harness) -> None:
    """开局视图：字段集合精确、版本链与 ``POST /games`` 一致、初始 hp/背包/房间正确。"""
    client = game_harness.client
    created = _create_game(client)

    view = _get_view(client, created["game_id"])

    assert set(view) == _VIEW_KEYS
    assert view["game_id"] == created["game_id"]
    assert view["turn"] == 1
    assert view["version"] == created["version"] == 1
    assert view["snapshot_version"] == created["snapshot_version"] == 1
    assert view["status"] == "active"
    assert view["scenario"] == "m2"
    assert view["goal"] is None
    assert view["ending_text"] is None
    assert view["hp"] == 20
    assert view["max_hp"] == 20
    assert view["inventory"] == []
    assert view["suggested_actions"], "成功/初始视图应给玩家可执行的下一步建议"

    room = view["room"]
    assert set(room) == _ROOM_KEYS
    assert room["room_id"] == ROOM_ENTRY
    assert room["title"] == ROOM_ENTRY_NAME
    assert isinstance(room["description"], str) and room["description"]
    assert room["exits"] == {"north": ROOM_CELLAR}
    assert room["exit_names"] == {"north": "哨站地窖"}
    assert [set(item) for item in room["items"]] == [_ITEM_KEYS, _ITEM_KEYS]
    assert room["items"] == [
        {"item_id": ITEM_TORCH, "name": ITEM_TORCH_NAME},
        {"item_id": ITEM_POTION, "name": ITEM_POTION_NAME},
    ]
    assert [set(npc) for npc in room["npcs"]] == [_NPC_KEYS]
    assert room["npcs"] == [{"npc_id": NPC_SENTINEL, "name": NPC_SENTINEL_NAME}]


def test_view_consistent_with_service_reads(game_harness) -> None:
    """视图与 ``QueryService.get_room_view``/``get_inventory`` 及 reducer 后状态一致。"""
    client = game_harness.client
    game_id = _create_game(client)["game_id"]

    view = _get_view(client, game_id)
    record = game_harness.repo.load(game_id)
    assert record is not None
    state = record.state
    character = state.characters[PLAYER_ID]

    assert view["hp"] == character.hp
    assert view["max_hp"] == character.attributes["max_hp"]
    assert view["inventory"] == game_harness.query.get_inventory(state, PLAYER_ID)["items"]
    assert view["room"] == game_harness.query.get_room_view(state, PLAYER_ID)
    assert view["version"] == record.version
    assert view["turn"] == state.turn


def test_view_reflects_committed_turn_state(game_harness) -> None:
    """提交「拿起火把」后：version/turn 前进、背包 +1、房间 -1、hp 不变。"""
    client = game_harness.client
    game_id = _create_game(client)["game_id"]

    status, turn = _post_turn(
        client,
        game_id=game_id,
        text="拿起火把",
        expected_version=1,
        key="turn-view-key-0001",
    )
    assert status == 200, turn
    assert turn["ok"] is True
    assert turn["version"] == 2

    view = _get_view(client, game_id)
    assert view["version"] == 2
    assert view["turn"] == 2
    assert view["hp"] == 20
    assert view["inventory"] == [{"item_id": ITEM_TORCH, "name": ITEM_TORCH_NAME, "qty": 1}]
    assert view["room"]["items"] == [{"item_id": ITEM_POTION, "name": ITEM_POTION_NAME}]

    record = game_harness.repo.load(game_id)
    assert record is not None
    assert set(record.state.characters[PLAYER_ID].inventory) == {ITEM_TORCH}


def test_view_is_read_only_zero_events_and_version_stable(game_harness) -> None:
    """连续 GET 视图：零事件、version/snapshot_version/state 均不变。"""
    client = game_harness.client
    game_id = _create_game(client)["game_id"]
    record_before = game_harness.repo.load(game_id)
    assert record_before is not None
    events_before = game_harness.repo.read_events(game_id)
    version_before = record_before.version

    first = _get_view(client, game_id)
    second = _get_view(client, game_id)
    third = _get_view(client, game_id)

    assert first == second == third
    assert first["version"] == version_before
    assert first["snapshot_version"] == 1
    record_after = game_harness.repo.load(game_id)
    assert record_after is not None
    assert record_after.version == version_before
    assert record_after.state == record_before.state
    events_after = game_harness.repo.read_events(game_id)
    assert [event.id for event in events_after] == [event.id for event in events_before]
    # 与既有刷新端点 GET /games/{id} 的版本链口径一致
    refresh = client.get(f"/api/v1/games/{game_id}").json()
    assert (refresh["turn"], refresh["version"], refresh["snapshot_version"]) == (
        first["turn"],
        first["version"],
        first["snapshot_version"],
    )


def test_inventory_qty_aggregates_same_template(game_harness) -> None:
    """背包 qty 按模板聚合：同 ``tags[0]`` 的两个实例合并为一项 qty=2（D-M4-1 口径）。"""
    client = game_harness.client
    game_id = _create_game(client)["game_id"]
    record = game_harness.repo.load(game_id)
    assert record is not None
    state = record.state
    first = Item(
        id="item_01J9Z3K4Q7X8Y9Z0ABCDEFGHK8",
        name="橡木柄",
        description="一根削好的橡木柄。",
        tags=["item.oak_handle"],
    )
    second = Item(
        id="item_01J9Z3K4Q7X8Y9Z0ABCDEFGHK9",
        name="橡木柄",
        description="一根削好的橡木柄。",
        tags=["item.oak_handle"],
    )
    player = state.characters[PLAYER_ID]
    crafted_state = state.model_copy(
        update={
            "items": {**state.items, first.id: first, second.id: second},
            "characters": {
                **state.characters,
                PLAYER_ID: player.model_copy(update={"inventory": [first.id, second.id]}),
            },
        }
    )

    inventory = game_harness.query.get_inventory(crafted_state, PLAYER_ID)["items"]

    assert inventory == [{"item_id": first.id, "name": "橡木柄", "qty": 2}]


def test_view_unknown_game_returns_404_envelope(game_harness) -> None:
    """未知存档 → 404 失败信封（``NOT_FOUND``，无 ``violations``）。"""
    resp = game_harness.client.get("/api/v1/games/game_unknown/view")

    assert resp.status_code == 404
    body = resp.json()
    assert set(body) == {"ok", "error_code", "message"}
    assert body["ok"] is False
    assert body["error_code"] == "NOT_FOUND"
    assert "game_unknown" in body["message"]
