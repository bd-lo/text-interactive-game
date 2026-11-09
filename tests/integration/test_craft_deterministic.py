"""集成测试：M4-016 确定性制造全链路（第 13 章 §13.2-§13.6、第 14 章 §14.7）。

装配为 M2 同构单角色 + 内置配方/CraftService + scenario="m4"（conftest m4_game_harness）。
覆盖：建档、移动到工位、单事件提交、盖章 rng_seed 与服务派生一致、同 seed 同 state_hash、
材料不足/工位不符/缺工具/未知配方/自由制造关闭零写入、掷骰失败仅推进时间、模板叙事 80-150、
M2 初始状态与 golden 路径不变。固定种子：seed=0 铁剑成功（masterwork+铁渣），seed=19 掷骰失败。
"""

from __future__ import annotations

from typing import Any

from textgame.domain.crafting import CraftMode, CraftRequest
from textgame.domain.results import ErrorCode
from textgame.domain.scenario import (
    M4_ITEM_FORGE_HAMMER,
    M4_ITEM_IRON_INGOT,
    M4_ITEM_OAK_HANDLE,
    M4_ROOM_FORGE,
    PLAYER_ID,
    build_initial_state,
)
from textgame.orchestration.dto import TurnRequest
from textgame.orchestration.turn_orchestrator import CreateGameRequest
from textgame.services.tool_context import ToolContext

_SUCCESS_SEED = 0
_FAILURE_SEED = 19
_MOVE_TEXT = "向东走进铁匠铺"
_CRAFT_TEXT = "打造铁剑"


def _create(harness: Any, *, seed: int, key: str) -> str:
    view = harness.orchestrator.create_game(
        CreateGameRequest(seed=seed, idempotency_key=key, scenario="m4")
    )
    return view.game_id


def _turn(
    harness: Any,
    *,
    game_id: str,
    text: str,
    expected_version: int,
    key: str,
):
    return harness.orchestrator.run(
        TurnRequest(
            game_id=game_id,
            text=text,
            expected_version=expected_version,
            idempotency_key=key,
        )
    )


def _record_state(harness: Any, game_id: str):
    record = harness.repo.load(game_id)
    assert record is not None
    return record


def _ctx(state: Any, *, key: str = "craft-sec-key-0001") -> ToolContext:
    return ToolContext(
        actor_id=PLAYER_ID,
        actor_tags=frozenset({"player"}),
        turn_id=state.turn + 1,
        save_seed=state.rng_state.save_seed,
        idempotency_key=key,
        caller="player",
    )


def test_m4_game_creates_with_forge_and_materials(m4_game_harness: Any) -> None:
    game_id = _create(m4_game_harness, seed=_SUCCESS_SEED, key="m4-create-0001")
    record = _record_state(m4_game_harness, game_id)

    assert record.version == 1
    assert record.last_seq == 1
    assert M4_ROOM_FORGE in record.state.rooms
    assert record.state.characters[PLAYER_ID].inventory.count(M4_ITEM_IRON_INGOT) == 2


def test_http_create_game_accepts_scenario_m4(m4_game_harness: Any) -> None:
    response = m4_game_harness.client.post(
        "/api/v1/games",
        json={"seed": _SUCCESS_SEED, "scenario": "m4"},
        headers={"Idempotency-Key": "m4-http-create-0001"},
    )

    assert response.status_code == 201
    assert response.json()["game_id"]


def test_m4_deterministic_craft_commits_single_event(m4_game_harness: Any) -> None:
    game_id = _create(m4_game_harness, seed=_SUCCESS_SEED, key="m4-create-0002")

    moved = _turn(
        m4_game_harness,
        game_id=game_id,
        text=_MOVE_TEXT,
        expected_version=1,
        key="m4-turn-move-0002",
    )
    assert moved.ok is True
    assert moved.version == 2

    crafted = _turn(
        m4_game_harness,
        game_id=game_id,
        text=_CRAFT_TEXT,
        expected_version=2,
        key="m4-turn-craft-0002",
    )
    assert crafted.ok is True
    assert crafted.version == 3
    assert len(crafted.events) == 1
    event = crafted.events[0]
    assert event.event_type is not None
    assert event.event_type.value == "CRAFT_DETERMINISTIC"
    assert event.method == "craft_item"
    assert event.params["recipe_id"] == "recipe.forge_sword"
    craft_result = event.result["craft"]
    assert craft_result["success"] is True
    assert craft_result["quality"] == "masterwork"
    assert len(event.result["produced_items"]) == 2  # 铁剑 + 铁渣副产物（seed=0）
    assert event.rng_seed == craft_result["rng_seed"]

    record = _record_state(m4_game_harness, game_id)
    assert record.version == 3
    assert record.last_seq == 3
    assert record.state.turn == 3
    inventory = record.state.characters[PLAYER_ID].inventory
    assert M4_ITEM_FORGE_HAMMER in inventory
    assert M4_ITEM_IRON_INGOT not in inventory
    assert M4_ITEM_OAK_HANDLE not in inventory
    names = [record.state.items[item_id].name for item_id in inventory]
    assert "铁剑" in names
    assert "铁渣" in names


def test_craft_event_rng_seed_matches_service_derivation(
    m4_game_harness: Any,
) -> None:
    game_id = _create(m4_game_harness, seed=_SUCCESS_SEED, key="m4-create-0003")
    _turn(
        m4_game_harness,
        game_id=game_id,
        text=_MOVE_TEXT,
        expected_version=1,
        key="m4-turn-move-0003",
    )
    crafted = _turn(
        m4_game_harness,
        game_id=game_id,
        text=_CRAFT_TEXT,
        expected_version=2,
        key="m4-turn-craft-0003",
    )

    event = crafted.events[0]
    assert event.rng_seed == event.result["craft"]["rng_seed"]


def test_same_seed_same_state_hash_across_games(m4_game_harness: Any) -> None:
    game_a = _create(m4_game_harness, seed=_SUCCESS_SEED, key="m4-create-a-0004")
    game_b = _create(m4_game_harness, seed=_SUCCESS_SEED, key="m4-create-b-0004")
    for index, game_id in enumerate((game_a, game_b)):
        _turn(
            m4_game_harness,
            game_id=game_id,
            text=_MOVE_TEXT,
            expected_version=1,
            key=f"m4-turn-move-{index}-0004",
        )
        _turn(
            m4_game_harness,
            game_id=game_id,
            text=_CRAFT_TEXT,
            expected_version=2,
            key=f"m4-turn-craft-{index}-0004",
        )

    record_a = _record_state(m4_game_harness, game_a)
    record_b = _record_state(m4_game_harness, game_b)
    assert record_a.state.model_dump(mode="json") == record_b.state.model_dump(mode="json")
    events_a = m4_game_harness.repo.read_events(game_a)
    events_b = m4_game_harness.repo.read_events(game_b)
    assert [event.id for event in events_a] == [event.id for event in events_b]
    assert [event.rng_seed for event in events_a] == [event.rng_seed for event in events_b]


def test_craft_narration_is_80_to_150_chars(m4_game_harness: Any) -> None:
    game_id = _create(m4_game_harness, seed=_SUCCESS_SEED, key="m4-create-0005")
    _turn(
        m4_game_harness,
        game_id=game_id,
        text=_MOVE_TEXT,
        expected_version=1,
        key="m4-turn-move-0005",
    )
    crafted = _turn(
        m4_game_harness,
        game_id=game_id,
        text=_CRAFT_TEXT,
        expected_version=2,
        key="m4-turn-craft-0005",
    )

    assert 80 <= len(crafted.text) <= 150
    assert "铁剑" in crafted.text


def test_craft_failed_roll_only_advances_turn(m4_game_harness: Any) -> None:
    game_id = _create(m4_game_harness, seed=_FAILURE_SEED, key="m4-create-0006")
    _turn(
        m4_game_harness,
        game_id=game_id,
        text=_MOVE_TEXT,
        expected_version=1,
        key="m4-turn-move-0006",
    )
    before = _record_state(m4_game_harness, game_id)
    crafted = _turn(
        m4_game_harness,
        game_id=game_id,
        text=_CRAFT_TEXT,
        expected_version=2,
        key="m4-turn-craft-0006",
    )

    assert crafted.ok is True
    event = crafted.events[0]
    assert event.result["craft"]["success"] is False
    assert event.result["produced_items"] == []
    after = _record_state(m4_game_harness, game_id)
    assert (
        after.state.characters[PLAYER_ID].inventory == before.state.characters[PLAYER_ID].inventory
    )
    assert after.state.items == before.state.items
    assert after.version == before.version + 1
    assert after.last_seq == before.last_seq + 1


def test_craft_insufficient_materials_rejected_zero_writes(
    m4_game_harness: Any,
) -> None:
    game_id = _create(m4_game_harness, seed=_SUCCESS_SEED, key="m4-create-0007")
    _turn(
        m4_game_harness,
        game_id=game_id,
        text=_MOVE_TEXT,
        expected_version=1,
        key="m4-turn-move-0007",
    )
    first = _turn(
        m4_game_harness,
        game_id=game_id,
        text=_CRAFT_TEXT,
        expected_version=2,
        key="m4-turn-craft-0007a",
    )
    before = _record_state(m4_game_harness, game_id)

    second = _turn(
        m4_game_harness,
        game_id=game_id,
        text=_CRAFT_TEXT,
        expected_version=first.version,
        key="m4-turn-craft-0007b",
    )

    assert second.ok is False
    assert second.error_code is not None
    after = _record_state(m4_game_harness, game_id)
    assert after.version == before.version
    assert after.last_seq == before.last_seq
    assert after.state.model_dump(mode="json") == before.state.model_dump(mode="json")


def test_craft_wrong_station_rejected_zero_writes(m4_game_harness: Any) -> None:
    game_id = _create(m4_game_harness, seed=_SUCCESS_SEED, key="m4-create-0008")
    before = _record_state(m4_game_harness, game_id)

    crafted = _turn(
        m4_game_harness,
        game_id=game_id,
        text=_CRAFT_TEXT,
        expected_version=1,
        key="m4-turn-craft-0008",
    )

    assert crafted.ok is False
    assert crafted.error_code is ErrorCode.NOT_ALLOWED
    after = _record_state(m4_game_harness, game_id)
    assert after.version == before.version
    assert after.last_seq == before.last_seq


def test_craft_missing_tool_rejected_zero_writes(m4_game_harness: Any) -> None:
    game_id = _create(m4_game_harness, seed=_SUCCESS_SEED, key="m4-create-0009")
    record = _record_state(m4_game_harness, game_id)
    character = record.state.characters[PLAYER_ID]
    at_forge_without_tool = record.state.model_copy(
        update={
            "characters": {
                **record.state.characters,
                PLAYER_ID: character.model_copy(
                    update={
                        "location": M4_ROOM_FORGE,
                        "inventory": [
                            item_id
                            for item_id in character.inventory
                            if item_id != M4_ITEM_FORGE_HAMMER
                        ],
                    }
                ),
            }
        }
    )
    request = CraftRequest(mode=CraftMode.RECIPE, recipe_id="recipe.forge_sword")

    result = m4_game_harness.craft.craft(
        _ctx(at_forge_without_tool),
        at_forge_without_tool,
        request,
        idempotency_key="m4-craft-tool-0009",
    )

    assert result.ok is False
    assert result.error_code is ErrorCode.NOT_ALLOWED
    assert result.events == []


def test_craft_unknown_recipe_rejected_zero_events(m4_game_harness: Any) -> None:
    game_id = _create(m4_game_harness, seed=_SUCCESS_SEED, key="m4-create-0010")
    record = _record_state(m4_game_harness, game_id)

    result = m4_game_harness.craft.craft(
        _ctx(record.state),
        record.state,
        CraftRequest(mode=CraftMode.RECIPE, recipe_id="recipe.no_such_recipe"),
        idempotency_key="m4-craft-recipe-0010",
    )

    assert result.ok is False
    assert result.error_code is ErrorCode.NOT_FOUND
    assert result.events == []


def test_craft_free_mode_disabled_rejected_zero_events(
    m4_game_harness: Any,
) -> None:
    game_id = _create(m4_game_harness, seed=_SUCCESS_SEED, key="m4-create-0011")
    record = _record_state(m4_game_harness, game_id)

    result = m4_game_harness.craft.craft(
        _ctx(record.state),
        record.state,
        CraftRequest(mode=CraftMode.FREE, free_description="把火把和铜线绑在一起"),
        idempotency_key="m4-craft-free-0011",
    )

    assert result.ok is False
    assert result.error_code is ErrorCode.NOT_ALLOWED
    assert result.events == []


def test_m4_scenario_does_not_change_m2_initial_state(m4_game_harness: Any) -> None:
    before = build_initial_state(save_seed="save-0").model_dump(mode="json")
    _create(m4_game_harness, seed=_SUCCESS_SEED, key="m4-create-0012")
    m2_game = m4_game_harness.orchestrator.create_game(
        CreateGameRequest(seed=_SUCCESS_SEED, idempotency_key="m2-create-0012")
    )

    m2_record = _record_state(m4_game_harness, m2_game.game_id)
    initial = build_initial_state(save_seed="save-0")
    assert m2_record.state.turn == 1
    assert m2_record.state.characters == initial.characters
    assert m2_record.state.rooms == initial.rooms
    assert m2_record.state.items == initial.items
    assert m2_record.state.rng_state.last_seq == 1
    assert m2_record.version == 1
    assert build_initial_state(save_seed="save-0").model_dump(mode="json") == before
