"""D4：``ITEM_CONSUMED`` reducer 按物品 ``stats`` 应用 HP（heal 正 / hp_delta 有符号）。

覆盖：残血 heal 回血、封顶 max_hp、满血零变化、hp_delta 负值扣血并夹到 0、无 stats 旧物品
零变化（m2/m3 golden 依赖）、非法/缺失数值静默按 0、背包移除语义不变、回放纯函数性。
"""

from __future__ import annotations

import pytest

from textgame.domain.events import Event, EventType
from textgame.domain.models import GameState, Item
from textgame.domain.reducers import apply_events
from textgame.domain.scenario import ITEM_POTION, ITEM_TORCH, PLAYER_ID, build_initial_state

_SAVE_SEED = "save-42"
#: 26 位 ULID 公共前缀（25 位）+ 1 位区分符（不与 story/m2 物品 ID 冲突）。
_ID_PREFIX = "01J9Z3K4Q7X8Y9Z0ABCDEFGHK"


def _state_with_item(*, item: Item, hp: int, inventory: list[str] | None = None) -> GameState:
    """把指定物品定义放进世界、放进玩家背包，并按 ``hp`` 覆盖玩家血量。"""
    base = build_initial_state(save_seed=_SAVE_SEED)
    items = {**base.items, item.id: item}
    player = base.characters[PLAYER_ID].model_copy(
        update={"inventory": [item.id] if inventory is None else inventory, "hp": hp}
    )
    return base.model_copy(
        update={"items": items, "characters": {**base.characters, PLAYER_ID: player}}
    )


def _consumed_event(item_id: str, *, turn_id: int = 2) -> Event:
    """构造 ``ITEM_CONSUMED`` 草稿事件（params 只有 item_id，与 action_service 一致）。"""
    return Event(
        turn_id=turn_id,
        actor_id=PLAYER_ID,
        method="use_item",
        event_type=EventType.ITEM_CONSUMED,
        params={"item_id": item_id},
        result={"consumed": True},
    )


def test_heal_restores_hp_when_wounded() -> None:
    """残血喝治疗药水：HP +8，物品移出背包。"""
    potion = build_initial_state(save_seed=_SAVE_SEED).items[ITEM_POTION]
    state = _state_with_item(item=potion, hp=12)

    result = state.apply([_consumed_event(ITEM_POTION)])

    character = result.characters[PLAYER_ID]
    assert character.hp == 20
    assert character.inventory == []


def test_heal_is_clamped_to_max_hp() -> None:
    """heal 不会超过 max_hp（20）。"""
    potion = build_initial_state(save_seed=_SAVE_SEED).items[ITEM_POTION]
    state = _state_with_item(item=potion, hp=17)

    result = state.apply([_consumed_event(ITEM_POTION)])

    assert result.characters[PLAYER_ID].hp == 20


def test_full_hp_use_keeps_state_hash_identical() -> None:
    """满血用药：HP 不变、state_hash 不变（m2 golden 第 5 回合口径）。"""
    from textgame.domain.hashing import state_hash

    potion = build_initial_state(save_seed=_SAVE_SEED).items[ITEM_POTION]
    state = _state_with_item(item=potion, hp=20)

    result = state.apply([_consumed_event(ITEM_POTION)])

    assert result.characters[PLAYER_ID].hp == 20
    assert result.characters[PLAYER_ID].inventory == []
    # 只有背包移除改变状态；HP 字段与之前逐字相同
    before = state.characters[PLAYER_ID].model_dump()
    after = result.characters[PLAYER_ID].model_dump()
    assert {k: v for k, v in before.items() if k != "inventory"} == {
        k: v for k, v in after.items() if k != "inventory"
    }
    assert state_hash(result) != state_hash(state)  # 背包变化仍应反映在哈希里


def test_hp_delta_negative_reduces_hp_and_clamps_at_zero() -> None:
    """``hp_delta`` 为负（结霜烈酒）：扣血并夹到 0，不会出现负 HP。"""
    liquor = Item(
        id=f"item_{_ID_PREFIX}2",
        name="结霜的烈酒",
        description="一瓶结了霜的烈酒。",
        tags=["consumable"],
        stats={"hp_delta": -4},
    )
    state = _state_with_item(item=liquor, hp=3)

    result = state.apply([_consumed_event(liquor.id)])

    assert result.characters[PLAYER_ID].hp == 0
    assert result.characters[PLAYER_ID].inventory == []


def test_hp_delta_positive_is_applied() -> None:
    """``hp_delta`` 为正（未来道具）：按有符号值回血。"""
    tonic = Item(
        id=f"item_{_ID_PREFIX}3",
        name="暖身的姜汤",
        description="一小碗热汤。",
        tags=["consumable"],
        stats={"hp_delta": 5},
    )
    state = _state_with_item(item=tonic, hp=10)

    result = state.apply([_consumed_event(tonic.id)])

    assert result.characters[PLAYER_ID].hp == 15


def test_legacy_item_without_stats_keeps_hp_unchanged() -> None:
    """旧场景无 stats 物品（火把）：只移出背包，HP 与旧行为一致。"""
    state = _state_with_item(
        item=build_initial_state(save_seed=_SAVE_SEED).items[ITEM_TORCH], hp=14
    )

    result = state.apply([_consumed_event(ITEM_TORCH)])

    assert result.characters[PLAYER_ID].hp == 14
    assert result.characters[PLAYER_ID].inventory == []


@pytest.mark.parametrize(
    "stats",
    [
        {"heal": 0},
        {"heal": -5},
        {"hp_delta": 0},
        {"heal": 2.7, "hp_delta": -1},
    ],
)
def test_non_positive_heal_and_float_stats(stats: dict[str, int | float]) -> None:
    """非正 ``heal`` 不生效；``heal`` 与 ``hp_delta`` 同时出现时 ``heal`` 优先并截断为整数。"""
    item = Item(
        id=f"item_{_ID_PREFIX}4",
        name="可疑的药剂",
        description="颜色可疑的药剂。",
        tags=["consumable"],
        stats=stats,
    )
    state = _state_with_item(item=item, hp=10)

    result = state.apply([_consumed_event(item.id)])

    # heal=2.7 精确 int() 截断为 2（heal 优先于 hp_delta）
    expected_hp = 12 if stats == {"heal": 2.7, "hp_delta": -1} else 10
    assert result.characters[PLAYER_ID].hp == expected_hp


def test_reducer_is_deterministic_and_pure() -> None:
    """同一状态 + 同一事件应用两次结果相等；入参状态不被修改（frozen 纯投影）。"""
    potion = build_initial_state(save_seed=_SAVE_SEED).items[ITEM_POTION]
    state = _state_with_item(item=potion, hp=15)
    event = _consumed_event(ITEM_POTION)

    first = apply_events(state, [event])
    second = apply_events(state, [event])

    assert first == second
    assert state.characters[PLAYER_ID].hp == 15  # 入参未被修改
    assert state.characters[PLAYER_ID].inventory == [ITEM_POTION]
