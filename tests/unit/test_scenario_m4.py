"""单元测试：M4 制造场景（第 13 章 §13.2、D-M4-11；M2/M3 golden 不回归）。

覆盖：build_m4_state 确定性、铁匠铺/材料/初始背包、build_initial_state 不被修改、
GameEngine scenario="m4" 开局、缺省 m2 不变、未知 scenario 仍拒绝。
"""

from __future__ import annotations

from textgame.domain.hashing import state_hash
from textgame.domain.scenario import (
    ITEM_TORCH,
    M4_ITEM_COPPER_WIRE,
    M4_ITEM_FORGE_HAMMER,
    M4_ITEM_IRON_INGOT,
    M4_ITEM_OAK_HANDLE,
    M4_ROOM_FORGE,
    M4_STARTING_INVENTORY,
    M4_TEMPLATE_COPPER_WIRE,
    M4_TEMPLATE_FORGE_HAMMER,
    M4_TEMPLATE_IRON_INGOT,
    M4_TEMPLATE_OAK_HANDLE,
    PLAYER_ID,
    ROOM_ENTRY,
    build_initial_state,
    build_m4_state,
)
from textgame.persistence.memory import InMemoryRepository
from textgame.services.game_engine import GameEngine

_SAVE_SEED = "save-42"


def test_build_m4_state_is_deterministic() -> None:
    first = build_m4_state(save_seed=_SAVE_SEED)
    second = build_m4_state(save_seed=_SAVE_SEED)

    assert first == second
    assert state_hash(first) == state_hash(second)


def test_build_m4_state_does_not_mutate_m2_initial_state() -> None:
    before = build_initial_state(save_seed=_SAVE_SEED).model_dump(mode="json")

    build_m4_state(save_seed=_SAVE_SEED)

    after = build_initial_state(save_seed=_SAVE_SEED).model_dump(mode="json")
    assert after == before
    assert state_hash(build_initial_state(save_seed=_SAVE_SEED)) == state_hash(
        build_initial_state(save_seed=_SAVE_SEED)
    )


def test_build_m4_state_has_forge_room_and_materials() -> None:
    state = build_m4_state(save_seed=_SAVE_SEED)

    forge = state.rooms[M4_ROOM_FORGE]
    assert "station.forge" in forge.ambient_tags
    assert forge.exits == {"west": ROOM_ENTRY}
    assert state.rooms[ROOM_ENTRY].exits["east"] == M4_ROOM_FORGE

    player = state.characters[PLAYER_ID]
    assert tuple(player.inventory) == M4_STARTING_INVENTORY
    for item_id, template_key in (
        (M4_ITEM_IRON_INGOT, M4_TEMPLATE_IRON_INGOT),
        (M4_ITEM_OAK_HANDLE, M4_TEMPLATE_OAK_HANDLE),
        (M4_ITEM_FORGE_HAMMER, M4_TEMPLATE_FORGE_HAMMER),
        (M4_ITEM_COPPER_WIRE, M4_TEMPLATE_COPPER_WIRE),
    ):
        assert state.items[item_id].tags[0] == template_key

    # M4 场景内所有物品实例的 tags 首项均为模板键，制造材料视图可聚合。
    for item in state.items.values():
        assert item.tags
        assert item.tags[0].startswith("item.")
    assert state.items[ITEM_TORCH].tags[0] == "item.torch"


def test_game_engine_create_game_scenario_m4() -> None:
    repo = InMemoryRepository()
    engine = GameEngine(repo)

    result = engine.create_game(save_seed=_SAVE_SEED, scenario="m4")

    assert result.ok is True
    assert result.data is not None
    record = repo.load(str(result.data["game_id"]))
    assert record is not None
    assert M4_ROOM_FORGE in record.state.rooms
    assert record.state.characters[PLAYER_ID].inventory == list(M4_STARTING_INVENTORY)


def test_default_scenario_remains_m2() -> None:
    repo = InMemoryRepository()
    engine = GameEngine(repo)

    result = engine.create_game(save_seed=_SAVE_SEED)

    assert result.ok is True
    assert result.data is not None
    record = repo.load(str(result.data["game_id"]))
    assert record is not None
    initial = build_initial_state(save_seed=_SAVE_SEED)
    assert record.state.turn == 1
    assert record.state.characters == initial.characters
    assert record.state.rooms == initial.rooms
    assert record.state.items == initial.items
    assert record.state.rng_state.last_seq == 1  # game_created 盖章后 last_seq=1


def test_unknown_scenario_still_rejected() -> None:
    repo = InMemoryRepository()
    engine = GameEngine(repo)

    result = engine.create_game(save_seed=_SAVE_SEED, scenario="m9")  # type: ignore[arg-type]

    assert result.ok is False
