"""集成测试：``GET /api/v1/games`` 存档列表（M6 续玩 UI 契约，D9；只读零写入）。

覆盖：
- 空库 → ``{games: [], total: 0}``；
- 创建多个存档后按最近更新时间倒序返回摘要字段集合精确、``total`` 正确；
- 分页 ``limit``/``offset`` 生效，``limit`` 越界 → 422；
- 只读语义：列表请求前后所有存档的 version/events 完全不变（零写入）；
- 未完成 story 存档 ``status=active``、字段 ``scenario=story``（场景识别走房间事实）。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

_LIST_KEYS = {"games", "total"}
_ITEM_KEYS = {
    "game_id",
    "turn",
    "version",
    "snapshot_version",
    "status",
    "scenario",
    "last_text",
    "updated_at",
}


def _create(client: TestClient, key: str, body: dict | None = None) -> dict:
    """创建存档并断言 201，返回响应体。"""
    resp = client.post(
        "/api/v1/games", headers={"Idempotency-Key": key}, json=body if body is not None else {}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_game_list_empty_returns_zero(game_harness) -> None:
    """空库：200 + 空列表 + total=0。"""
    resp = game_harness.client.get("/api/v1/games")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == _LIST_KEYS
    assert body == {"games": [], "total": 0}


def test_game_list_returns_summaries_newest_first(game_harness) -> None:
    """两个存档：摘要字段集合精确、按最近更新时间倒序、total 正确。"""
    client = game_harness.client
    first = _create(client, "list-key-0001", {"seed": 42, "scenario": "m2"})
    second = _create(client, "list-key-0002", {"seed": 7, "scenario": "story"})

    body = client.get("/api/v1/games").json()
    assert body["total"] == 2
    assert [item["game_id"] for item in body["games"]] == [second["game_id"], first["game_id"]]
    for item in body["games"]:
        assert set(item) == _ITEM_KEYS
    by_id = {item["game_id"]: item for item in body["games"]}
    assert by_id[first["game_id"]]["scenario"] == "m2"
    assert by_id[second["game_id"]]["scenario"] == "story"
    assert by_id[second["game_id"]]["status"] == "active"


def test_game_list_pagination(game_harness) -> None:
    """``limit``/``offset`` 分页：total 恒为总数、页大小与偏移正确。"""
    client = game_harness.client
    for index in range(3):
        _create(client, f"list-page-key-{index:04d}", {"seed": index})

    page = client.get("/api/v1/games", params={"limit": 2, "offset": 0}).json()
    assert page["total"] == 3 and len(page["games"]) == 2
    page2 = client.get("/api/v1/games", params={"limit": 2, "offset": 2}).json()
    assert page2["total"] == 3 and len(page2["games"]) == 1
    assert page["games"][0]["game_id"] != page2["games"][0]["game_id"]

    bad = client.get("/api/v1/games", params={"limit": 51})
    assert bad.status_code == 422
    assert bad.json()["ok"] is False
    assert bad.json()["error_code"] == "INVALID_INPUT"

    bad_offset = client.get("/api/v1/games", params={"offset": -1})
    assert bad_offset.status_code == 422


def test_game_list_is_read_only(game_harness) -> None:
    """列表请求零写入：前后所有存档 version/事件序列完全一致。"""
    client = game_harness.client
    created = _create(client, "list-ro-key-0001", {"seed": 42})
    game_id = created["game_id"]
    before = game_harness.repo.load(game_id)
    assert before is not None
    events_before = [event.id for event in game_harness.repo.read_events(game_id)]

    assert client.get("/api/v1/games").status_code == 200

    after = game_harness.repo.load(game_id)
    assert after is not None
    assert (after.version, after.state) == (before.version, before.state)
    assert [event.id for event in game_harness.repo.read_events(game_id)] == events_before


def test_game_list_last_text_uses_latest_narrative(game_harness) -> None:
    """列表项 ``last_text`` 取最近一条带 narrative 的事件文本（与 GET /games/{id} 同口径）。"""
    client = game_harness.client
    game_id = _create(client, "list-text-key-0001", {"seed": 42})["game_id"]
    turn = client.post(
        "/api/v1/turns",
        headers={"Idempotency-Key": "list-text-turn-0001"},
        json={"game_id": game_id, "input": "拿起火把", "expected_version": 1},
    )
    assert turn.status_code == 200 and turn.json()["ok"] is True

    item = client.get("/api/v1/games").json()["games"][0]
    refresh = client.get(f"/api/v1/games/{game_id}").json()
    assert item["last_text"] is not None
    assert item["last_text"] == refresh["last_text"]
    assert item["status"] == refresh["status"] == "active"


def test_read_recent_events_returns_tail_in_seq_order(game_harness) -> None:
    """R-21：列表页只读尾部事件；``read_recent_events`` 返回升序的最后 N 条。"""
    client = game_harness.client
    game_id = _create(client, "list-recent-key-0001", {"seed": 42})["game_id"]
    turn = client.post(
        "/api/v1/turns",
        headers={"Idempotency-Key": "list-recent-turn-0001"},
        json={"game_id": game_id, "input": "拿起火把", "expected_version": 1},
    )
    assert turn.status_code == 200 and turn.json()["ok"] is True

    all_events = game_harness.repo.read_events(game_id)
    recent = game_harness.repo.read_recent_events(game_id, limit=2)

    assert [event.seq for event in recent] == [event.seq for event in all_events[-2:]]
    assert len(recent) == 2 and recent[0].seq < recent[1].seq
