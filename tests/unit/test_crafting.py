"""单元测试：M4-016 确定性制造纯函数（第 13 章 §13.2/§13.6、D-M4-1/D-M4-4/D-M4-8）。

覆盖：同 seed 确定性、成功率 0/1 边界、质量档命中、tool 不消耗/材料催化剂消耗、
副产物概率 0/1、产出模板与数量、actor 不匹配、derive_item_id 正则/确定性/上下文敏感、
instantiate_craft_result 实例定义与模板级结果不变、build_craft_state_slice 聚合/工位/
next_seq。所有断言不依赖真实时间/全局随机。
"""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter

from textgame.domain.crafting import (
    Byproduct,
    CraftInput,
    CraftStateSlice,
    Recipe,
    build_craft_state_slice,
    calculate_craft_result,
    derive_item_id,
    instantiate_craft_result,
)
from textgame.domain.rules.dice import DerivedRandom
from textgame.domain.scenario import (
    M4_ROOM_FORGE,
    M4_TEMPLATE_COPPER_WIRE,
    M4_TEMPLATE_FORGE_HAMMER,
    M4_TEMPLATE_IRON_INGOT,
    M4_TEMPLATE_OAK_HANDLE,
    PLAYER_ID,
    build_m4_state,
)
from textgame.domain.types import ItemId
from textgame.services.recipe_catalog import RecipeCatalog

_CATALOG = RecipeCatalog.load_builtin()
_FORGE_SWORD = _CATALOG.require("recipe.forge_sword")
_ITEM_ID = TypeAdapter(ItemId)


def _state_slice(
    *,
    station_id: str | None = None,
    next_seq: int = 2,
    inventory: dict[str, int] | None = None,
) -> CraftStateSlice:
    """构造制造视图：默认含铁剑配方所需材料（工具在背包但不消耗）。"""
    return CraftStateSlice(
        actor_id=PLAYER_ID,
        inventory=dict(
            inventory
            if inventory is not None
            else {
                M4_TEMPLATE_IRON_INGOT: 2,
                M4_TEMPLATE_OAK_HANDLE: 1,
                M4_TEMPLATE_FORGE_HAMMER: 1,
            }
        ),
        skill_levels={},
        station_id=station_id,
        next_seq=next_seq,
    )


def _recipe_with(rate: float, **overrides: object) -> Recipe:
    """复制内置铁剑配方并覆盖成功率/质量档/副产物（保持其余契约合法）。"""
    return _FORGE_SWORD.model_copy(update={"base_success_rate": rate, **overrides})


def test_calculate_is_deterministic_for_same_seed() -> None:
    state = _state_slice()
    first = calculate_craft_result(state, _FORGE_SWORD, PLAYER_ID, 12_345)

    for _ in range(100):
        assert calculate_craft_result(state, _FORGE_SWORD, PLAYER_ID, 12_345) == first


def test_success_rate_zero_always_fails() -> None:
    result = calculate_craft_result(_state_slice(), _recipe_with(0.0), PLAYER_ID, 7)

    assert result.ok is True
    assert result.success is False
    assert result.consumed == []
    assert result.produced == []
    assert result.byproducts == []
    assert result.quality is None
    assert result.rng_seed == 7


def test_success_rate_one_always_succeeds() -> None:
    result = calculate_craft_result(_state_slice(), _recipe_with(1.0), PLAYER_ID, 7)

    assert result.success is True
    assert result.consumed
    assert result.produced
    assert result.quality is not None


def test_quality_bands_pick_highest_matching() -> None:
    recipe = _recipe_with(1.0)
    bands = recipe.quality_bands

    for seed in range(20):
        rng = DerivedRandom(seed)
        rng.randint(0, 999_999)  # 成功判定消耗的第一次掷骰
        quality_roll = rng.randint(0, 999_999) / 1_000_000
        expected = bands[0].quality
        for band in bands:
            if band.min_roll <= quality_roll:
                expected = band.quality

        result = calculate_craft_result(_state_slice(), recipe, PLAYER_ID, seed)
        assert result.quality == expected


def test_tool_is_not_consumed_and_catalyst_is() -> None:
    catalyst = CraftInput(template_id="item.resin", qty=1, role="catalyst")
    recipe = _recipe_with(1.0, inputs=[*_FORGE_SWORD.inputs, catalyst])

    result = calculate_craft_result(_state_slice(), recipe, PLAYER_ID, 99)

    consumed_templates = [item.template_id for item in result.consumed]
    assert M4_TEMPLATE_FORGE_HAMMER not in consumed_templates
    assert M4_TEMPLATE_IRON_INGOT in consumed_templates
    assert M4_TEMPLATE_OAK_HANDLE in consumed_templates
    assert "item.resin" in consumed_templates
    assert [item.role for item in result.consumed] == ["material", "material", "catalyst"]


def test_byproduct_chance_zero_never_triggers() -> None:
    byproduct = Byproduct(template_id="item.iron_slag", qty=1, chance=0.0)
    recipe = _recipe_with(1.0, byproducts=[byproduct])

    result = calculate_craft_result(_state_slice(), recipe, PLAYER_ID, 5)

    assert result.byproducts == []


def test_byproduct_chance_one_always_triggers() -> None:
    byproduct = Byproduct(template_id="item.iron_slag", qty=1, chance=1.0)
    recipe = _recipe_with(1.0, byproducts=[byproduct])

    result = calculate_craft_result(_state_slice(), recipe, PLAYER_ID, 5)

    assert [item.template_id for item in result.byproducts] == ["item.iron_slag"]


def test_produced_uses_output_template_and_qty() -> None:
    recipe = _recipe_with(1.0, output_qty=2)

    result = calculate_craft_result(_state_slice(), recipe, PLAYER_ID, 3)

    assert len(result.produced) == 1
    assert result.produced[0].template_id == _FORGE_SWORD.output_template_id
    assert result.produced[0].qty == 2
    assert result.produced[0].role == "material"


def test_actor_mismatch_raises() -> None:
    other_actor = "char_01J9Z3K4Q7X8Y9Z0ABCDEFGHK2"

    with pytest.raises(ValueError, match="does not match"):
        calculate_craft_result(_state_slice(), _FORGE_SWORD, other_actor, 1)


def test_derive_item_id_is_deterministic_and_matches_item_id_pattern() -> None:
    kwargs = {
        "save_seed": "save-42",
        "turn_id": 3,
        "actor_id": PLAYER_ID,
        "craft_kind": "CRAFT_DETERMINISTIC",
        "event_seq": 3,
        "ordinal": 0,
    }
    item_id = derive_item_id(**kwargs)

    assert _ITEM_ID.validate_python(item_id) == item_id
    assert derive_item_id(**kwargs) == item_id


def test_derive_item_id_changes_with_context() -> None:
    base = {
        "save_seed": "save-42",
        "turn_id": 3,
        "actor_id": PLAYER_ID,
        "craft_kind": "CRAFT_DETERMINISTIC",
        "event_seq": 3,
        "ordinal": 0,
    }
    ids = {
        derive_item_id(**base),
        derive_item_id(**{**base, "ordinal": 1}),
        derive_item_id(**{**base, "turn_id": 4}),
        derive_item_id(**{**base, "craft_kind": "CRAFT_GENERATED"}),
    }

    assert len(ids) == 4


def test_instantiate_craft_result_assigns_concrete_ids_and_definitions() -> None:
    recipe = _recipe_with(1.0)
    result = calculate_craft_result(_state_slice(), recipe, PLAYER_ID, 21)

    same_result, items = instantiate_craft_result(
        result,
        recipe,
        save_seed="save-42",
        turn_id=3,
        actor_id=PLAYER_ID,
        craft_kind="CRAFT_DETERMINISTIC",
        event_seq=3,
    )

    assert same_result is result
    assert len(items) == 1
    item = items[0]
    assert _ITEM_ID.validate_python(item.id) == item.id
    template = recipe.item_templates[recipe.output_template_id]
    assert item.tags[0] == recipe.output_template_id
    assert item.name == template.name
    assert item.stats == template.stats
    assert item.rarity == template.rarity
    assert item.stackable == template.stackable


def test_instantiate_includes_triggered_byproducts() -> None:
    byproduct = Byproduct(template_id="item.iron_slag", qty=1, chance=1.0)
    recipe = _recipe_with(1.0, byproducts=[byproduct])
    result = calculate_craft_result(_state_slice(), recipe, PLAYER_ID, 8)

    _, items = instantiate_craft_result(
        result,
        recipe,
        save_seed="save-42",
        turn_id=3,
        actor_id=PLAYER_ID,
        craft_kind="CRAFT_DETERMINISTIC",
        event_seq=3,
    )

    assert len(items) == 2
    assert [item.tags[0] for item in items] == ["item.iron_sword", "item.iron_slag"]
    assert len({item.id for item in items}) == 2


def test_build_craft_state_slice_aggregates_inventory_and_station() -> None:
    state = build_m4_state(save_seed="save-42")
    at_entry = build_craft_state_slice(state, PLAYER_ID)

    assert at_entry.station_id is None
    assert at_entry.next_seq == state.rng_state.last_seq + 1
    assert at_entry.inventory == {
        M4_TEMPLATE_IRON_INGOT: 2,
        M4_TEMPLATE_OAK_HANDLE: 1,
        M4_TEMPLATE_FORGE_HAMMER: 1,
        M4_TEMPLATE_COPPER_WIRE: 2,
    }
    assert at_entry.skill_levels == {}

    character = state.characters[PLAYER_ID]
    moved = state.model_copy(
        update={
            "characters": {
                **state.characters,
                PLAYER_ID: character.model_copy(update={"location": M4_ROOM_FORGE}),
            }
        }
    )
    at_forge = build_craft_state_slice(moved, PLAYER_ID)
    assert at_forge.station_id == "station.forge"


def test_build_craft_state_slice_requires_known_actor() -> None:
    state = build_m4_state(save_seed="save-42")

    with pytest.raises(ValueError, match="not present"):
        build_craft_state_slice(state, "char_01J9Z3K4Q7X8Y9Z0ABCDEFGHK2")


def test_same_seed_same_outcome_100_runs() -> None:
    state = _state_slice()
    recipe = _recipe_with(1.0)
    first = calculate_craft_result(state, recipe, PLAYER_ID, 2026)

    for _ in range(100):
        assert calculate_craft_result(state, recipe, PLAYER_ID, 2026) == first
