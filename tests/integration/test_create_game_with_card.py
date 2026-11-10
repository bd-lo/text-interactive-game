"""集成测试：``POST /api/v1/games`` 的 ``card_id`` 加法语义（D-M6-2，task-24）。

覆盖：

- 提供 ``card_id`` → 按人物卡映射初始 ``Character``（name/attributes/hp 原样、玩家槽位仍是
  ``PLAYER_ID``）；``starting_items`` 实例化进背包与 ``GameState.items``（``tags[0]`` 用模板键）；
- ``game_created`` 载荷照旧含 ``initial_state``，``params.card_id`` 为加法字段；
- 响应保持现有 5 字段；未知卡 404、卡 ID 格式非法 422、``card_id`` + ``seed``/``scenario`` → 422；
- 不提供 ``card_id`` → 现行为完全不变（初始状态/事件载荷与 M2 基线一致、可继续提交回合）；
- 幂等：同键同参回放（零写入、同 game_id），同键异参 409；缺幂等键 422；
- ``hp < max_hp`` 合法受伤态、``starting_items`` 上限 8 件、删除后的卡不可建局。

装配：内存游戏仓储（``InMemoryRepository``，M2 单角色链路）+ 内存 SQLite 卡库，不联网。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient

from textgame.api.app import create_app
from textgame.domain.rules.engine import RuleEngine
from textgame.domain.scenario import (
    M3_NPC_SCAVENGER_A,
    M3_NPC_SCAVENGER_B,
    PLAYER_ID,
    ROOM_ENTRY,
    build_initial_state,
)
from textgame.orchestration.direct_gate import DirectGate
from textgame.orchestration.intent import DeterministicIntentParser
from textgame.orchestration.narrator import TemplateNarrator
from textgame.orchestration.turn_orchestrator import TurnOrchestrator
from textgame.persistence.card_repository import SqliteCardRepository
from textgame.persistence.database import init_schema, prepare_db
from textgame.persistence.memory import InMemoryRepository
from textgame.services.action_service import ActionService
from textgame.services.card_service import CardService
from textgame.services.game_engine import GameEngine
from textgame.services.query_service import QueryService

pytestmark = [pytest.mark.integration]

_ATTRIBUTES = {"str": 12, "dex": 14, "con": 11, "int": 15, "wis": 10, "cha": 8, "max_hp": 30}
_VALID_UNKNOWN_CARD = "card_01J9Z3K4Q7X8Y9Z0ABCDEFGHJK"
_CREATED_GAME_FIELDS = {"game_id", "turn", "version", "snapshot_version", "save_seed"}


@dataclass
class CardGameHarness:
    """游戏（内存仓储）+ 卡库（内存 SQLite）两轨装配，供 ``card_id`` 建局断言。"""

    client: TestClient
    repo: InMemoryRepository
    orchestrator: TurnOrchestrator
    query: QueryService
    card_service: CardService


def _card(**overrides: Any) -> dict[str, Any]:
    """一份合法人物卡（upload 入口语义）。"""
    card: dict[str, Any] = {
        "schema_version": "1.0",
        "name": "灰烬旅人",
        "attributes": dict(_ATTRIBUTES),
        "hp": 30,
        "starting_items": ["item.torch", "item.potion"],
        "background": "在旧矿镇长大的拾荒者，靠辨认废矿里的风声过活。",
        "appearance": "煤灰斗篷，左眉有一道旧疤。",
        "source": "upload",
        "generated_by": None,
    }
    card.update(overrides)
    return card


@pytest.fixture
def card_game_harness() -> Iterator[CardGameHarness]:
    """M2 单角色内存游戏 + 内存 SQLite 卡库 + TestClient（默认离线、确定性）。"""
    repo = InMemoryRepository()
    engine = GameEngine(repo)
    query = QueryService(repo)
    action = ActionService(query)
    orchestrator = TurnOrchestrator(
        repo=repo,
        engine=engine,
        query=query,
        action=action,
        parser=DeterministicIntentParser(),
        gate=DirectGate(action, query),
        narrator=TemplateNarrator(),
        rules=RuleEngine(),
        save_seed_default="save-42",
    )
    card_engine, _factory = prepare_db("sqlite:///:memory:")
    init_schema(card_engine)
    card_service = CardService(SqliteCardRepository(card_engine))
    with TestClient(
        create_app(orchestrator=orchestrator, query_service=query, card_service=card_service)
    ) as client:
        yield CardGameHarness(
            client=client,
            repo=repo,
            orchestrator=orchestrator,
            query=query,
            card_service=card_service,
        )
    card_engine.dispose()


def _import_card(harness: CardGameHarness, *, key: str, card: dict[str, Any] | None = None) -> str:
    """上传人物卡（201 断言）→ card_id。"""
    content = json.dumps(card or _card(), ensure_ascii=False).encode("utf-8")
    response = harness.client.post(
        "/api/v1/character-cards/import",
        headers={"Idempotency-Key": key},
        files={"file": ("card.json", content, "application/json")},
    )
    assert response.status_code == 201, response.text
    card_id: str = response.json()["card_id"]
    return card_id


def _create_game(harness: CardGameHarness, body: dict[str, Any], *, key: str) -> Any:
    """``POST /api/v1/games``（原始状态码/响应体，便于断言错误）。"""
    return harness.client.post("/api/v1/games", headers={"Idempotency-Key": key}, json=body)


def _create_game_ok(harness: CardGameHarness, body: dict[str, Any], *, key: str) -> dict[str, Any]:
    response = _create_game(harness, body, key=key)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def _assert_error(response: Any, status_code: int, error_code: str) -> dict[str, Any]:
    assert response.status_code == status_code, response.text
    body: dict[str, Any] = response.json()
    assert body.get("ok") is False
    assert body.get("error_code") == error_code
    return body


def _events(harness: CardGameHarness, game_id: str) -> list[Any]:
    return harness.repo.read_events(game_id)


def _view(harness: CardGameHarness, game_id: str) -> dict[str, Any]:
    response = harness.client.get(f"/api/v1/games/{game_id}/view")
    assert response.status_code == 200, response.text
    view: dict[str, Any] = response.json()
    return view


# ---------------------------------------------------------------------------
# 有效卡建局：字段映射 / 起始物品 / 事件载荷
# ---------------------------------------------------------------------------
def test_create_game_with_card_maps_character_and_returns_five_fields(
    card_game_harness: CardGameHarness,
) -> None:
    """带卡建局 → 201 5 字段；玩家 ``Character`` 映射卡片的 name/attributes/hp。"""
    card = _card()
    card_id = _import_card(card_game_harness, key="card-create-key-card-0001")

    created = _create_game_ok(
        card_game_harness, {"card_id": card_id}, key="card-create-key-game-0001"
    )

    assert set(created) == _CREATED_GAME_FIELDS
    assert (created["turn"], created["version"], created["snapshot_version"]) == (1, 1, 1)
    assert created["save_seed"] == "save-42"

    record = card_game_harness.repo.load(created["game_id"])
    assert record is not None
    player = record.state.characters[PLAYER_ID]
    assert player.name == card["name"]
    assert player.attributes == card["attributes"]
    assert player.hp == card["hp"]
    assert player.location == ROOM_ENTRY
    assert player.created_turn == 1


def test_game_created_payload_keeps_initial_state_and_adds_card_id(
    card_game_harness: CardGameHarness,
) -> None:
    """``game_created`` 载荷照旧含 ``initial_state``，``params.card_id`` 为加法字段。"""
    card = _card()
    card_id = _import_card(card_game_harness, key="card-create-key-card-0002")
    created = _create_game_ok(
        card_game_harness, {"card_id": card_id}, key="card-create-key-game-0002"
    )

    events = _events(card_game_harness, created["game_id"])
    assert len(events) == 1
    game_created = events[0]
    assert game_created.event_type is not None
    assert game_created.event_type.value == "game_created"
    assert game_created.params["card_id"] == card_id
    assert game_created.params["initial_character_id"] == PLAYER_ID
    assert game_created.params["starting_room_id"] == ROOM_ENTRY
    initial = game_created.result["initial_state"]
    assert initial["characters"][PLAYER_ID]["name"] == card["name"]


def test_starting_items_are_instantiated_into_inventory(
    card_game_harness: CardGameHarness,
) -> None:
    """``starting_items`` → 背包实例 + ``GameState.items``；``tags[0]`` 为模板键。"""
    card = _card(starting_items=["item.torch", "item.potion"])
    card_id = _import_card(card_game_harness, key="card-create-key-card-0003", card=card)
    created = _create_game_ok(
        card_game_harness, {"card_id": card_id}, key="card-create-key-game-0003"
    )

    record = card_game_harness.repo.load(created["game_id"])
    assert record is not None
    player = record.state.characters[PLAYER_ID]
    assert len(player.inventory) == 2
    assert len(set(player.inventory)) == 2  # 两个模板 → 两个独立实例
    for instance_id in player.inventory:
        assert instance_id.startswith("item_")
        assert len(instance_id) == len("item_") + 26
        item = record.state.items[instance_id]
        assert item.tags and item.tags[0].startswith("item.")
    items_by_template = {
        record.state.items[instance_id].tags[0]: record.state.items[instance_id]
        for instance_id in player.inventory
    }
    assert items_by_template["item.torch"].name == "火把"
    assert items_by_template["item.potion"].name == "治疗药水"

    view = _view(card_game_harness, created["game_id"])
    assert view["inventory"] == [
        {"item_id": player.inventory[0], "name": "火把", "qty": 1},
        {"item_id": player.inventory[1], "name": "治疗药水", "qty": 1},
    ]


def test_hp_below_max_hp_is_legal_injured_state(card_game_harness: CardGameHarness) -> None:
    """``hp < max_hp`` 是合法受伤态：不报错，``/view`` 的 hp/max_hp 来自卡片。"""
    card = _card(hp=10)
    card_id = _import_card(card_game_harness, key="card-create-key-card-0004", card=card)
    created = _create_game_ok(
        card_game_harness, {"card_id": card_id}, key="card-create-key-game-0004"
    )

    view = _view(card_game_harness, created["game_id"])
    assert (view["hp"], view["max_hp"]) == (10, 30)


def test_eight_starting_items_are_accepted(card_game_harness: CardGameHarness) -> None:
    """``starting_items`` 上限 8 件：8 个不同模板全部实例化进背包。"""
    card = _card(
        starting_items=[
            "item.torch",
            "item.potion",
            "item.sword",
            "item.relic",
            "item.rusty_knife",
            "item.chalk",
            "item.oak_cane",
            "item.iron_ingot",
        ]
    )
    card_id = _import_card(card_game_harness, key="card-create-key-card-0005", card=card)
    created = _create_game_ok(
        card_game_harness, {"card_id": card_id}, key="card-create-key-game-0005"
    )

    record = card_game_harness.repo.load(created["game_id"])
    assert record is not None
    assert len(record.state.characters[PLAYER_ID].inventory) == 8


def test_background_and_appearance_stay_out_of_character(
    card_game_harness: CardGameHarness,
) -> None:
    """``background``/``appearance`` 只留在卡库记录，不注入 ``Character``（无对应字段）。"""
    card = _card()
    card_id = _import_card(card_game_harness, key="card-create-key-card-0006")
    created = _create_game_ok(
        card_game_harness, {"card_id": card_id}, key="card-create-key-game-0006"
    )

    record = card_game_harness.repo.load(created["game_id"])
    assert record is not None
    dumped = record.state.characters[PLAYER_ID].model_dump()
    assert "background" not in dumped
    assert "appearance" not in dumped
    card_detail = card_game_harness.client.get(f"/api/v1/character-cards/{card_id}").json()
    assert card_detail["card"]["background"] == card["background"]
    assert card_detail["card"]["appearance"] == card["appearance"]


# ---------------------------------------------------------------------------
# 互斥 / 未知卡 / 非法卡 ID / 幂等键
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "body",
    [
        {"card_id": "PLACEHOLDER", "seed": 42},
        {"card_id": "PLACEHOLDER", "scenario": "m3"},
        {"card_id": "PLACEHOLDER", "scenario": "m2"},
    ],
)
def test_card_id_with_seed_or_scenario_returns_422(
    card_game_harness: CardGameHarness, body: dict[str, Any]
) -> None:
    """``card_id`` 与 ``seed``/``scenario`` 同给 → 422 ``INVALID_INPUT``（D-M6-2）。"""
    card_id = _import_card(
        card_game_harness, key=f"card-create-key-card-{abs(hash(str(body))) % 10**6:06d}"
    )
    request_body = {**body, "card_id": card_id}

    response = _create_game(card_game_harness, request_body, key="card-create-key-conflict-0001")

    _assert_error(response, 422, "INVALID_INPUT")


def test_unknown_card_returns_404_and_creates_nothing(
    card_game_harness: CardGameHarness,
) -> None:
    """未知卡 → 404 ``NOT_FOUND`` 统一信封，且不写幂等记录/存档。"""
    response = _create_game(
        card_game_harness, {"card_id": _VALID_UNKNOWN_CARD}, key="card-create-key-unknown-0001"
    )

    _assert_error(response, 404, "NOT_FOUND")
    assert card_game_harness.repo.get_idempotent("game:card-create-key-unknown-0001") is None


def test_malformed_card_id_returns_422(card_game_harness: CardGameHarness) -> None:
    """卡 ID 格式非法（非 ``card_<ULID>``）→ 422 ``INVALID_INPUT``。"""
    response = _create_game(
        card_game_harness, {"card_id": "not-a-card-id"}, key="card-create-key-bad-0001"
    )

    _assert_error(response, 422, "INVALID_INPUT")


def test_create_game_with_card_requires_idempotency_key(
    card_game_harness: CardGameHarness,
) -> None:
    """带卡建局同样必须带 ``Idempotency-Key``（缺 → 422）。"""
    card_id = _import_card(card_game_harness, key="card-create-key-card-0007")
    response = card_game_harness.client.post("/api/v1/games", json={"card_id": card_id})
    _assert_error(response, 422, "INVALID_INPUT")


def test_create_game_with_card_idempotency_replay(
    card_game_harness: CardGameHarness,
) -> None:
    """同键同参 → 回放首次结果（同 game_id、只写一次事件）；同键异参 → 409。"""
    first_card = _import_card(card_game_harness, key="card-create-key-card-0008")
    second_card = _import_card(
        card_game_harness, key="card-create-key-card-0009", card=_card(name="矿镇斥候")
    )

    first = _create_game_ok(
        card_game_harness, {"card_id": first_card}, key="card-create-key-idem-0001"
    )
    replay = _create_game_ok(
        card_game_harness, {"card_id": first_card}, key="card-create-key-idem-0001"
    )
    assert replay == first
    assert len(_events(card_game_harness, first["game_id"])) == 1

    conflict = _create_game(
        card_game_harness, {"card_id": second_card}, key="card-create-key-idem-0001"
    )
    _assert_error(conflict, 409, "CONFLICT")


def test_deleted_card_cannot_start_game(card_game_harness: CardGameHarness) -> None:
    """软删除后的卡不能建局（404 ``NOT_FOUND``）。"""
    card_id = _import_card(card_game_harness, key="card-create-key-card-0010")
    delete = card_game_harness.client.delete(
        f"/api/v1/character-cards/{card_id}",
        headers={"Idempotency-Key": "card-create-key-delete-0001"},
    )
    assert delete.status_code == 204

    response = _create_game(
        card_game_harness, {"card_id": card_id}, key="card-create-key-deleted-0001"
    )
    _assert_error(response, 404, "NOT_FOUND")


# ---------------------------------------------------------------------------
# 无卡路径零回归 / 建局后仍可游玩
# ---------------------------------------------------------------------------
def test_no_card_path_is_unchanged(card_game_harness: CardGameHarness) -> None:
    """不提供 ``card_id`` → 初始状态与 M2 基线一致、``params.card_id`` 为 None、可继续回合。"""
    created = _create_game_ok(card_game_harness, {"seed": 42}, key="card-create-key-nocard-0001")

    assert set(created) == _CREATED_GAME_FIELDS
    record = card_game_harness.repo.load(created["game_id"])
    assert record is not None
    baseline = build_initial_state(save_seed="save-42")
    player = record.state.characters[PLAYER_ID]
    assert player.name == baseline.characters[PLAYER_ID].name
    assert player.attributes == baseline.characters[PLAYER_ID].attributes
    assert player.hp == baseline.characters[PLAYER_ID].hp
    assert record.state.rooms == baseline.rooms
    assert record.state.items == baseline.items
    events = _events(card_game_harness, created["game_id"])
    assert events[0].params["card_id"] is None

    turn = card_game_harness.client.post(
        "/api/v1/turns",
        headers={"Idempotency-Key": "card-create-key-nocard-turn-0001"},
        json={"game_id": created["game_id"], "input": "拿起火把", "expected_version": 1},
    )
    assert turn.status_code == 200, turn.text
    assert turn.json()["ok"] is True
    assert turn.json()["version"] == 2


def test_m3_scenario_path_is_unchanged(card_game_harness: CardGameHarness) -> None:
    """不提供 ``card_id`` 的 ``scenario=m3`` 仍生成多角色场景（零回归代理断言）。"""
    created = _create_game_ok(card_game_harness, {"scenario": "m3"}, key="card-create-key-m3-0001")
    record = card_game_harness.repo.load(created["game_id"])
    assert record is not None
    assert set(record.state.characters) >= {
        PLAYER_ID,
        M3_NPC_SCAVENGER_A,
        M3_NPC_SCAVENGER_B,
    }


def test_create_game_with_card_is_playable(card_game_harness: CardGameHarness) -> None:
    """带卡建局后可提交回合（M2 单角色链路），版本链与背包正确推进。"""
    card_id = _import_card(
        card_game_harness,
        key="card-create-key-card-0011",
        card=_card(starting_items=["item.torch"]),
    )
    created = _create_game_ok(
        card_game_harness, {"card_id": card_id}, key="card-create-key-play-0001"
    )

    turn = card_game_harness.client.post(
        "/api/v1/turns",
        headers={"Idempotency-Key": "card-create-key-play-turn-0001"},
        json={"game_id": created["game_id"], "input": "拿起治疗药水", "expected_version": 1},
    )
    assert turn.status_code == 200, turn.text
    result = turn.json()
    assert result["ok"] is True
    assert result["version"] == 2

    view = _view(card_game_harness, created["game_id"])
    assert view["turn"] == 2
    assert view["version"] == 2
    names = [item["name"] for item in view["inventory"]]
    assert names == ["火把", "治疗药水"]


def test_openapi_create_game_body_exposes_card_id(card_game_harness: CardGameHarness) -> None:
    """OpenAPI：``CreateGameBody`` 暴露可选 ``card_id``（D-M6-2 前端类型源）。"""
    schema = card_game_harness.client.app.openapi()["components"]["schemas"]["CreateGameBody"]
    assert "card_id" in schema["properties"]
    assert "card_id" not in set(schema.get("required") or [])
    assert {"seed", "scenario", "card_id"} <= set(schema["properties"])
