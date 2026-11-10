"""集成测试：M2-005 回合 API（``/api/v1/games`` + ``/api/v1/turns``，契约 §5）。

覆盖任务验收清单：开局 201 与幂等、连续回合脚本（版本链 / 80–150 字 / 事件盖章）、
``Idempotency-Key`` 缺失或超短 422 信封、同键同参零写入、同键异参 ``CONFLICT``、
expected_version 过期、未知存档、无法理解输入零写入、事件 seq/id/rng_seed/event_type、
``GET /games/{id}`` 视图与 404 信封、CAS 并发冲突与请求体校验 422 信封。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from textgame.domain.events import EventType
from textgame.domain.scenario import ITEM_SWORD, ITEM_TORCH, NPC_SENTINEL, PLAYER_ID, ROOM_CELLAR

_RNG_SEED_MAX = 0x7FFF_FFFF_FFFF_FFFF
_GAME_KEY = "game-key-000001"


def _create_game(client: TestClient, *, key: str = _GAME_KEY, seed: int | None = 42) -> dict:
    """POST /api/v1/games（201 断言），返回响应体。"""
    body = {} if seed is None else {"seed": seed}
    resp = client.post("/api/v1/games", headers={"Idempotency-Key": key}, json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _post_turn(
    client: TestClient,
    *,
    game_id: str,
    text: str,
    expected_version: int,
    key: str,
) -> tuple[int, dict]:
    """POST /api/v1/turns，返回 (status_code, body)。"""
    resp = client.post(
        "/api/v1/turns",
        headers={"Idempotency-Key": key},
        json={"game_id": game_id, "input": text, "expected_version": expected_version},
    )
    return resp.status_code, resp.json()


# ---------------------------------------------------------------------------
# POST /api/v1/games
# ---------------------------------------------------------------------------
def test_create_game_returns_201_initial_view(game_harness) -> None:
    """开局 201：{game_id, turn:1, version:1, snapshot_version:1, save_seed}。"""
    client = game_harness.client

    body = _create_game(client)

    assert set(body) == {"game_id", "turn", "version", "snapshot_version", "save_seed"}
    assert body["turn"] == 1
    assert body["version"] == 1
    assert body["snapshot_version"] == 1
    assert body["save_seed"] == "save-42"
    events = game_harness.repo.read_events(body["game_id"])
    assert len(events) == 1
    assert events[0].event_type is EventType.GAME_CREATED


def test_create_game_without_body_uses_default_seed(game_harness) -> None:
    """body 可省略：缺省用服务端默认种子 save-42。"""
    body = _create_game(game_harness.client, seed=None)

    assert body["save_seed"] == "save-42"


def test_create_game_idempotent_replay_makes_no_new_save(game_harness) -> None:
    """同 Idempotency-Key 同参重放：返回首次响应且不再建新存档（零写入）。"""
    client = game_harness.client
    first = _create_game(client)

    second = _create_game(client)

    assert second == first
    assert len(game_harness.repo.read_events(first["game_id"])) == 1


def test_create_game_same_key_different_seed_conflicts(game_harness) -> None:
    """同 Idempotency-Key 异参 → 409 失败信封。"""
    client = game_harness.client
    _create_game(client)

    resp = client.post("/api/v1/games", headers={"Idempotency-Key": _GAME_KEY}, json={"seed": 7})

    assert resp.status_code == 409
    body = resp.json()
    assert body["ok"] is False
    assert body["error_code"] == "CONFLICT"
    assert "message" in body


def test_create_game_rejects_unknown_body_fields(game_harness) -> None:
    """body 出现未知字段 → 422 失败信封。

    注：M6 D-M6-2 起 body 合法字段扩为 ``{card_id?, seed?, scenario?}``，``card_id`` 不再
    属于未知字段（卡建局用例见 ``test_create_game_with_card.py``）；本用例改用真正未知字段。
    """
    resp = game_harness.client.post(
        "/api/v1/games",
        headers={"Idempotency-Key": _GAME_KEY},
        json={"unknown_field": 1},
    )

    assert resp.status_code == 422
    body = resp.json()
    assert body["ok"] is False
    assert body["error_code"] == "INVALID_INPUT"
    assert isinstance(body["violations"], list)


def test_create_game_requires_idempotency_key(game_harness) -> None:
    """缺 Idempotency-Key → 422 失败信封。"""
    resp = game_harness.client.post("/api/v1/games")

    assert resp.status_code == 422
    body = resp.json()
    assert body["ok"] is False
    assert body["error_code"] == "INVALID_INPUT"


# ---------------------------------------------------------------------------
# 连续回合脚本
# ---------------------------------------------------------------------------
def test_turn_script_versions_texts_and_event_stamps(game_harness) -> None:
    """连续回合：版本链、read-only 不涨版本、80–150 字、事件盖章全部正确。"""
    client = game_harness.client
    repo = game_harness.repo
    game_id = _create_game(client)["game_id"]

    # (输入, 期望的 expected_version, 期望的新 version, 期望事件类型)
    script = [
        ("环顾四周", 1, 1, None),
        ("对老哨兵问好", 1, 2, EventType.DIALOGUE_EXCHANGED),
        ("拿起火把", 2, 3, EventType.ITEM_TAKEN),
        ("拿起治疗药水", 3, 4, EventType.ITEM_TAKEN),
        ("使用治疗药水", 4, 5, EventType.ITEM_CONSUMED),
        ("向北走进地窖", 5, 6, EventType.CHARACTER_MOVED),
        ("拿起锈剑", 6, 7, EventType.ITEM_TAKEN),
    ]
    last_text = ""
    for index, (text, expected_version, new_version, event_type) in enumerate(script):
        status, body = _post_turn(
            client,
            game_id=game_id,
            text=text,
            expected_version=expected_version,
            key=f"turn-key-{index:06d}",
        )
        assert status == 200, body
        assert body["ok"] is True, body
        assert body["error_code"] is None
        assert body["version"] == new_version
        assert isinstance(body["text"], str)
        assert 80 <= len(body["text"]) <= 150
        assert body["turn_id"] == new_version
        if event_type is None:
            assert body["events"] == []
        else:
            assert len(body["events"]) == 1
            assert body["events"][0]["event_type"] == event_type.value
        last_text = body["text"]

    events = repo.read_events(game_id)
    assert [event.seq for event in events] == list(range(1, 8))
    assert [event.id for event in events] == [f"evt_{seq:06d}" for seq in range(1, 8)]
    assert [event.event_type for event in events] == [
        EventType.GAME_CREATED,
        EventType.DIALOGUE_EXCHANGED,
        EventType.ITEM_TAKEN,
        EventType.ITEM_TAKEN,
        EventType.ITEM_CONSUMED,
        EventType.CHARACTER_MOVED,
        EventType.ITEM_TAKEN,
    ]
    for event in events:
        assert event.rng_seed is not None
        assert 0 <= event.rng_seed <= _RNG_SEED_MAX
        assert event.turn_id >= 1

    record = repo.load(game_id)
    assert record is not None
    assert record.state.characters[PLAYER_ID].location == ROOM_CELLAR
    assert record.state.npcs[NPC_SENTINEL].dialogue_state == "greeted"
    assert set(record.state.characters[PLAYER_ID].inventory) == {ITEM_TORCH, ITEM_SWORD}

    view_resp = client.get(f"/api/v1/games/{game_id}")
    assert view_resp.status_code == 200
    view = view_resp.json()
    assert set(view) == {
        "game_id",
        "turn",
        "version",
        "snapshot_version",
        "status",
        "last_text",
    }
    assert view["turn"] == 7
    assert view["version"] == 7
    assert view["snapshot_version"] == 1
    assert view["status"] == "active"
    assert view["last_text"] == last_text


# ---------------------------------------------------------------------------
# Idempotency-Key 协议错误
# ---------------------------------------------------------------------------
def test_turn_requires_idempotency_key(game_harness) -> None:
    """缺 Idempotency-Key → 422 失败信封（协议错误，非业务失败）。"""
    client = game_harness.client
    game_id = _create_game(client)["game_id"]

    resp = client.post(
        "/api/v1/turns",
        json={"game_id": game_id, "input": "环顾四周", "expected_version": 1},
    )

    assert resp.status_code == 422
    body = resp.json()
    assert body["ok"] is False
    assert body["error_code"] == "INVALID_INPUT"


def test_turn_rejects_short_idempotency_key(game_harness) -> None:
    """头长度 < 8 → 422 失败信封。"""
    client = game_harness.client
    game_id = _create_game(client)["game_id"]

    resp = client.post(
        "/api/v1/turns",
        headers={"Idempotency-Key": "short"},
        json={"game_id": game_id, "input": "环顾四周", "expected_version": 1},
    )

    assert resp.status_code == 422
    assert resp.json()["error_code"] == "INVALID_INPUT"


def test_turn_rejects_invalid_body(game_harness) -> None:
    """body 缺字段/多字段 → 422 失败信封（violations 列出位置）。"""
    client = game_harness.client
    game_id = _create_game(client)["game_id"]
    headers = {"Idempotency-Key": "turn-key-000999"}

    missing = client.post(
        "/api/v1/turns",
        headers=headers,
        json={"game_id": game_id, "expected_version": 1},
    )
    extra = client.post(
        "/api/v1/turns",
        headers=headers,
        json={
            "game_id": game_id,
            "input": "环顾四周",
            "expected_version": 1,
            "actor_id": "char_x",
        },
    )

    for resp in (missing, extra):
        assert resp.status_code == 422
        body = resp.json()
        assert body["ok"] is False
        assert body["error_code"] == "INVALID_INPUT"
        assert isinstance(body["violations"], list)


# ---------------------------------------------------------------------------
# 幂等重放与同键异参
# ---------------------------------------------------------------------------
def test_turn_idempotent_replay_returns_first_result_zero_writes(game_harness) -> None:
    """同键同参：原样返回首次 TurnResult，事件数不增。"""
    client = game_harness.client
    repo = game_harness.repo
    game_id = _create_game(client)["game_id"]
    key = "turn-key-replay-01"

    status, first = _post_turn(
        client, game_id=game_id, text="拿起火把", expected_version=1, key=key
    )
    assert status == 200 and first["ok"] is True
    events_after_first = len(repo.read_events(game_id))

    status, second = _post_turn(
        client, game_id=game_id, text="拿起火把", expected_version=1, key=key
    )

    assert status == 200
    assert second == first
    assert len(repo.read_events(game_id)) == events_after_first


def test_turn_same_key_different_params_conflicts(game_harness) -> None:
    """同键异参 → 200 TurnResult(ok=false, CONFLICT)，零写入。"""
    client = game_harness.client
    repo = game_harness.repo
    game_id = _create_game(client)["game_id"]
    key = "turn-key-conflict-01"
    status, first = _post_turn(
        client, game_id=game_id, text="拿起火把", expected_version=1, key=key
    )
    assert status == 200 and first["ok"] is True
    events_after_first = len(repo.read_events(game_id))

    status, second = _post_turn(
        client, game_id=game_id, text="拿起治疗药水", expected_version=2, key=key
    )

    assert status == 200
    assert second["ok"] is False
    assert second["error_code"] == "CONFLICT"
    assert second["version"] == 2
    assert second["events"] == []
    assert len(repo.read_events(game_id)) == events_after_first


# ---------------------------------------------------------------------------
# 业务失败分支（HTTP 200 + TurnResult(ok=false)）
# ---------------------------------------------------------------------------
def test_turn_stale_expected_version_conflicts(game_harness) -> None:
    """expected_version 过期 → CONFLICT，version=当前版本，零写入。"""
    client = game_harness.client
    repo = game_harness.repo
    game_id = _create_game(client)["game_id"]

    status, body = _post_turn(
        client,
        game_id=game_id,
        text="环顾四周",
        expected_version=0,
        key="turn-key-stale-001",
    )

    assert status == 200
    assert body["ok"] is False
    assert body["error_code"] == "CONFLICT"
    assert body["version"] == 1
    assert body["events"] == []
    assert len(repo.read_events(game_id)) == 1


def test_turn_unknown_game_returns_not_found(game_harness) -> None:
    """未知 game_id → NOT_FOUND、version=0。"""
    status, body = _post_turn(
        game_harness.client,
        game_id="game_unknown",
        text="环顾四周",
        expected_version=0,
        key="turn-key-unknown-01",
    )

    assert status == 200
    assert body["ok"] is False
    assert body["error_code"] == "NOT_FOUND"
    assert body["version"] == 0


def test_turn_unrecognized_input_zero_writes(game_harness) -> None:
    """无法理解输入（“跳舞”）→ INVALID_INPUT，事件零新增、版本不变。"""
    client = game_harness.client
    repo = game_harness.repo
    game_id = _create_game(client)["game_id"]

    status, body = _post_turn(
        client,
        game_id=game_id,
        text="跳舞",
        expected_version=1,
        key="turn-key-dance-001",
    )

    assert status == 200
    assert body["ok"] is False
    assert body["error_code"] == "INVALID_INPUT"
    assert body["version"] == 1
    assert body["events"] == []
    assert len(repo.read_events(game_id)) == 1

    view = client.get(f"/api/v1/games/{game_id}").json()
    assert view["turn"] == 1
    assert view["version"] == 1


def test_turn_cas_race_conflicts(game_harness) -> None:
    """两个写回合用同一 expected_version：后到者 CONFLICT（CAS），版本只前进一次。"""
    client = game_harness.client
    repo = game_harness.repo
    game_id = _create_game(client)["game_id"]

    first_status, first = _post_turn(
        client, game_id=game_id, text="拿起火把", expected_version=1, key="turn-key-cas-0001"
    )
    second_status, second = _post_turn(
        client,
        game_id=game_id,
        text="拿起治疗药水",
        expected_version=1,
        key="turn-key-cas-0002",
    )

    assert first_status == 200 and first["ok"] is True and first["version"] == 2
    assert second_status == 200
    assert second["ok"] is False
    assert second["error_code"] == "CONFLICT"
    assert second["version"] == 2
    record = repo.load(game_id)
    assert record is not None
    assert record.version == 2


# ---------------------------------------------------------------------------
# GET /api/v1/games/{game_id}
# ---------------------------------------------------------------------------
def test_get_unknown_game_returns_404_envelope(game_harness) -> None:
    """未知存档 → 404 失败信封。"""
    resp = game_harness.client.get("/api/v1/games/game_unknown")

    assert resp.status_code == 404
    body = resp.json()
    assert body["ok"] is False
    assert body["error_code"] == "NOT_FOUND"
