"""M6 后端前置·回放端点契约测试（D-M6-3：只读 ``GET /api/v1/games/{id}/replay``）。

覆盖：响应字段完整性/``ts`` 序列化、``seq`` 升序、``from_seq`` 过滤与越界、``limit``
截断与 >500 校验、未知存档 404、空窗口边界（``from_seq == last_seq + 1``）、只读零写入
（版本与事件数不变）、跨存档隔离、``params``/``result.narrative`` 原样保留。
"""

from __future__ import annotations

from datetime import datetime

from fastapi.testclient import TestClient

_GAME_KEY = "game-key-replay-0001"
_EVENT_KEYS = {
    "id",
    "seq",
    "turn_id",
    "actor_id",
    "method",
    "event_type",
    "params",
    "result",
    "rng_seed",
    "ts",
}


def _create_game(client: TestClient, *, key: str = _GAME_KEY) -> dict:
    """POST /api/v1/games（201 断言），返回响应体。"""
    resp = client.post("/api/v1/games", headers={"Idempotency-Key": key}, json={"seed": 42})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _post_turn(
    client: TestClient, *, game_id: str, text: str, expected_version: int, key: str
) -> dict:
    """POST /api/v1/turns（200 + ok 断言），返回响应体。"""
    resp = client.post(
        "/api/v1/turns",
        headers={"Idempotency-Key": key},
        json={"game_id": game_id, "input": text, "expected_version": expected_version},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True, body
    return body


def _replay(client: TestClient, game_id: str, **params: object) -> dict:
    """GET replay 端点（200 断言），返回响应体。"""
    resp = client.get(f"/api/v1/games/{game_id}/replay", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_replay_returns_contract_fields_in_seq_order(game_harness) -> None:
    """开局 + 一个写回合：字段集合、seq 升序、from/to_seq、narrative 与 ts 均符合契约。"""
    client = game_harness.client
    game_id = _create_game(client)["game_id"]
    _post_turn(client, game_id=game_id, text="拿起火把", expected_version=1, key="turn-replay-0001")

    body = _replay(client, game_id)

    assert set(body) == {"events", "from_seq", "to_seq"}
    assert body["from_seq"] == 1
    assert body["to_seq"] == 2
    events = body["events"]
    assert [event["seq"] for event in events] == [1, 2]
    assert [event["id"] for event in events] == ["evt_000001", "evt_000002"]
    assert [event["turn_id"] for event in events] == [1, 2]
    assert [event["event_type"] for event in events] == ["game_created", "ITEM_TAKEN"]
    for event in events:
        assert set(event) == _EVENT_KEYS
        assert event["actor_id"]
        assert event["method"]
        assert isinstance(event["params"], dict)
        assert isinstance(event["result"], dict)
        assert isinstance(event["rng_seed"], int)
        parsed_ts = datetime.fromisoformat(str(event["ts"]).replace("Z", "+00:00"))
        assert parsed_ts.tzinfo is not None
    narrative = events[1]["result"]["narrative"]
    assert isinstance(narrative, str) and narrative
    assert isinstance(events[1]["params"]["item_id"], str) and events[1]["params"]["item_id"]


def test_replay_from_seq_filters_earlier_events(game_harness) -> None:
    """``from_seq=2``：只返回 seq>=2 的事件，``from_seq`` 回显、``to_seq`` 为末条。"""
    client = game_harness.client
    game_id = _create_game(client)["game_id"]
    _post_turn(client, game_id=game_id, text="拿起火把", expected_version=1, key="turn-replay-0002")

    body = _replay(client, game_id, from_seq=2)

    assert body["from_seq"] == 2
    assert body["to_seq"] == 2
    assert [event["seq"] for event in body["events"]] == [2]


def test_replay_limit_truncates_window(game_harness) -> None:
    """``limit=2``：默认从 seq=1 起只返回 2 条，``to_seq`` 为截断后末条。"""
    client = game_harness.client
    game_id = _create_game(client)["game_id"]
    _post_turn(client, game_id=game_id, text="拿起火把", expected_version=1, key="turn-replay-0003")
    _post_turn(client, game_id=game_id, text="环顾四周", expected_version=2, key="turn-replay-0004")

    body = _replay(client, game_id, limit=2)

    assert [event["seq"] for event in body["events"]] == [1, 2]
    assert body["to_seq"] == 2


def test_replay_limit_over_max_returns_422_envelope(game_harness) -> None:
    """``limit=501`` 超过契约上限 500 → 422 统一失败信封 ``INVALID_INPUT``。"""
    game_id = _create_game(game_harness.client)["game_id"]

    resp = game_harness.client.get(f"/api/v1/games/{game_id}/replay", params={"limit": 501})

    assert resp.status_code == 422
    body = resp.json()
    assert body["ok"] is False
    assert body["error_code"] == "INVALID_INPUT"
    assert isinstance(body["violations"], list)


def test_replay_invalid_or_out_of_range_from_seq_returns_422(game_harness) -> None:
    """``from_seq`` 非正整数 → 422；越过末事件后一位（``last_seq + 2``）→ 422。"""
    game_id = _create_game(game_harness.client)["game_id"]

    zero = game_harness.client.get(f"/api/v1/games/{game_id}/replay", params={"from_seq": 0})
    beyond = game_harness.client.get(f"/api/v1/games/{game_id}/replay", params={"from_seq": 3})

    assert zero.status_code == 422
    assert zero.json()["error_code"] == "INVALID_INPUT"
    assert beyond.status_code == 422
    assert beyond.json()["ok"] is False
    assert beyond.json()["error_code"] == "INVALID_INPUT"


def test_replay_from_seq_at_last_plus_one_returns_empty_window(game_harness) -> None:
    """``from_seq == last_seq + 1`` 合法：返回空窗口，``to_seq == from_seq - 1``。"""
    game_id = _create_game(game_harness.client)["game_id"]

    body = _replay(game_harness.client, game_id, from_seq=2)

    assert body["events"] == []
    assert body["from_seq"] == 2
    assert body["to_seq"] == 1


def test_replay_unknown_game_returns_404_envelope(game_harness) -> None:
    """未知 ``game_id`` → 404 统一失败信封 ``NOT_FOUND``。"""
    resp = game_harness.client.get("/api/v1/games/game_unknown/replay")

    assert resp.status_code == 404
    body = resp.json()
    assert body["ok"] is False
    assert body["error_code"] == "NOT_FOUND"


def test_replay_is_read_only_with_zero_state_writes(game_harness) -> None:
    """回放只读：调用前后版本、事件数、快照与审计均不变，重复调用结果一致。"""
    client = game_harness.client
    repo = game_harness.repo
    game_id = _create_game(client)["game_id"]
    _post_turn(client, game_id=game_id, text="拿起火把", expected_version=1, key="turn-replay-0005")
    record_before = repo.load(game_id)
    assert record_before is not None
    events_before = repo.read_events(game_id)
    audit_before = repo.read_audit(game_id)

    first = _replay(client, game_id)
    second = _replay(client, game_id)

    record_after = repo.load(game_id)
    assert record_after is not None
    assert (record_after.version, record_after.state.turn) == (
        record_before.version,
        record_before.state.turn,
    )
    assert repo.read_events(game_id) == events_before
    assert repo.read_audit(game_id) == audit_before
    assert first == second


def test_replay_is_isolated_between_saves(game_harness) -> None:
    """跨存档隔离：各存档 replay 只返回自己的事件（同库内存仓储下的第二个存档）。"""
    client = game_harness.client
    first = _create_game(client, key="game-key-replay-0002")["game_id"]
    second = _create_game(client, key="game-key-replay-0003")["game_id"]
    _post_turn(client, game_id=first, text="拿起火把", expected_version=1, key="turn-replay-0006")
    _post_turn(client, game_id=second, text="拿起火把", expected_version=1, key="turn-replay-0007")

    first_body = _replay(client, first)
    second_body = _replay(client, second)

    assert [event["id"] for event in first_body["events"]] == ["evt_000001", "evt_000002"]
    assert [event["id"] for event in second_body["events"]] == ["evt_000001", "evt_000002"]
    assert first_body["events"][1]["params"]["item_id"]
    assert second_body["events"][1]["result"]["narrative"]
