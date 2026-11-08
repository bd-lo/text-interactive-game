"""M3 多角色场景与确定性计划表单元测试（契约 .work/m3-contract.md §3.6）。"""

from __future__ import annotations

import pytest

from textgame.domain.hashing import state_hash
from textgame.domain.models import GameState
from textgame.domain.scenario import (
    ITEM_POTION,
    ITEM_TORCH,
    M3_NONPLAYER_TAGS,
    M3_NPC_PLANS,
    M3_NPC_SCAVENGER_A,
    M3_NPC_SCAVENGER_B,
    PLAYER_ID,
    ROOM_CELLAR,
    ROOM_ENTRY,
    PlanStep,
    build_initial_state,
    build_m3_state,
)
from textgame.persistence.memory import InMemoryRepository
from textgame.services.game_engine import GameEngine


def test_build_initial_state_unchanged_for_m2_golden() -> None:
    """M2 单场景必须保持单角色（M2 golden 的 state_hash 不回归）。"""
    state = build_initial_state(save_seed="save-42")
    assert set(state.characters) == {PLAYER_ID}
    assert state.turn == 1
    assert state.rng_state.save_seed == "save-42"


def test_build_m3_state_adds_two_scavengers_without_touching_world() -> None:
    m2 = build_initial_state(save_seed="save-42")
    m3 = build_m3_state(save_seed="save-42")

    assert set(m3.characters) == {PLAYER_ID, M3_NPC_SCAVENGER_A, M3_NPC_SCAVENGER_B}
    for actor_id in (M3_NPC_SCAVENGER_A, M3_NPC_SCAVENGER_B):
        character = m3.characters[actor_id]
        assert character.location == ROOM_ENTRY
        assert character.inventory == []
        assert character.hp == 12
        assert character.attributes["max_hp"] == 12
    # 房间/物品/NPC 与 M2 完全一致（只追加角色）。
    assert m3.rooms == m2.rooms
    assert m3.items == m2.items
    assert m3.npcs == m2.npcs
    assert m3.turn == m2.turn
    assert m3.rng_state == m2.rng_state


def test_build_m3_state_is_deterministic_and_seed_sensitive() -> None:
    first = build_m3_state(save_seed="save-42")
    second = build_m3_state(save_seed="save-42")
    assert state_hash(first) == state_hash(second)
    other = build_m3_state(save_seed="save-43")
    assert state_hash(other) != state_hash(first)


def test_m3_nonplayer_tags_keys_exist_in_state() -> None:
    state = build_m3_state(save_seed="save-42")
    assert set(M3_NONPLAYER_TAGS) == {M3_NPC_SCAVENGER_A, M3_NPC_SCAVENGER_B}
    for actor_id, tags in M3_NONPLAYER_TAGS.items():
        assert actor_id in state.characters
        assert "npc" in tags
        assert "npc:scavenger" in tags


def test_scavenger_can_carry_items_in_state_model() -> None:
    """D-M3-4：拾荒者用 Character 建模，具备背包字段（冲突演示的 reducer 前提）。"""
    state = build_m3_state(save_seed="save-42")
    assert state.items[ITEM_POTION].id == ITEM_POTION
    assert state.rooms[ROOM_ENTRY].item_ids == [ITEM_TORCH, ITEM_POTION]
    assert state.rooms[ROOM_CELLAR].item_ids  # 地窖仍有可拿物品
    assert state.characters[M3_NPC_SCAVENGER_A].inventory == []


def test_conflict_plan_on_turn_two_targets_same_item() -> None:
    """M3 冲突演示：turn 2 两名拾荒者同回合抢同一瓶药水。"""
    state = build_m3_state(save_seed="save-42")
    plan_a = [step for step in M3_NPC_PLANS[M3_NPC_SCAVENGER_A] if step.turn_id == 2]
    plan_b = [step for step in M3_NPC_PLANS[M3_NPC_SCAVENGER_B] if step.turn_id == 2]
    assert len(plan_a) == 1
    assert len(plan_b) == 1
    assert plan_a[0].tool == "take_item"
    assert plan_b[0].tool == "take_item"
    assert plan_a[0].args["item_id"] == plan_b[0].args["item_id"] == ITEM_POTION
    assert ITEM_POTION in state.rooms[ROOM_ENTRY].item_ids


def test_plans_are_deterministic_and_cover_ten_turns() -> None:
    turns: set[int] = set()
    for plans in M3_NPC_PLANS.values():
        for step in plans:
            assert isinstance(step, PlanStep)
            assert step.tool in {"take_item", "move_to"}
            assert 2 <= step.turn_id <= 11
            turns.add(step.turn_id)
    assert len(turns) >= 5  # 多角色回放的十回合脚本覆盖多个回合
    # 冻结 dataclass：不可原地修改（回放确定性保障）。
    step = M3_NPC_PLANS[M3_NPC_SCAVENGER_A][0]
    with pytest.raises(AttributeError):
        step.turn_id = 99  # type: ignore[misc]


def test_game_engine_create_game_scenario_m3() -> None:
    repo = InMemoryRepository()
    engine = GameEngine(repo)
    result = engine.create_game(save_seed="save-42", scenario="m3")
    assert result.ok is True
    record = repo.load(result.data["game_id"])
    assert record is not None
    assert set(record.state.characters) == {
        PLAYER_ID,
        M3_NPC_SCAVENGER_A,
        M3_NPC_SCAVENGER_B,
    }
    assert record.version == 1
    assert record.last_seq == 1


def test_game_engine_create_game_default_scenario_is_m2() -> None:
    repo = InMemoryRepository()
    engine = GameEngine(repo)
    result = engine.create_game(save_seed="save-42")
    assert result.ok is True
    record = repo.load(result.data["game_id"])
    assert record is not None
    assert set(record.state.characters) == {PLAYER_ID}


def test_game_engine_rejects_unknown_scenario() -> None:
    repo = InMemoryRepository()
    engine = GameEngine(repo)
    result = engine.create_game(save_seed="save-42", scenario="m9")  # type: ignore[arg-type]
    assert result.ok is False
    assert result.error_code is not None
    assert result.error_code.value == "INVALID_INPUT"
    assert all(record is None for record in [repo.load("game_missing")])


def test_game_engine_m3_state_round_trips_through_snapshot() -> None:
    """version 持久化前置：create_game 初始快照带 version=1 且可重建同一状态。"""
    repo = InMemoryRepository()
    engine = GameEngine(repo)
    created = engine.create_game(save_seed="save-42", scenario="m3")
    game_id = created.data["game_id"]
    snapshot = repo.latest_snapshot(game_id)
    assert snapshot is not None
    assert snapshot.version == 1
    assert isinstance(snapshot.state, GameState)
    assert state_hash(snapshot.state) == snapshot.state_hash
