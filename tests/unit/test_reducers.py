"""单元测试：``domain.reducers`` 事件投影（第 14 章 14.4/14.7、契约 §3.3）。

覆盖：``game_created`` 从空存档重建、四类写事件对实体的副作用、公共规则
（turn / world_time / rng_state.last_seq）、未知/缺失 ``event_type`` 抛 ``ValueError``、
``GameState.apply`` 委托语义，以及「快照 + 后续事件」与「全量事件」重建等价。
"""

from __future__ import annotations

import pytest

from textgame.domain.events import Event, EventLog, EventType
from textgame.domain.models import GameState
from textgame.domain.reducers import DEFAULT_REGISTRY, ReducerRegistry, apply_events
from textgame.domain.scenario import (
    ITEM_SWORD,
    ITEM_TORCH,
    NPC_SENTINEL,
    PLAYER_ID,
    ROOM_CELLAR,
    ROOM_ENTRY,
    build_initial_state,
)

_SAVE_SEED = "save-42"
_UNKNOWN_ROOM = "room_01J9Z3K4Q7X8Y9Z0ABCDEFGHJV"
_UNKNOWN_NPC = "npc_01J9Z3K4Q7X8Y9Z0ABCDEFGHJV"


def _initial() -> GameState:
    """场景初始状态（world_time=0、turn=1、last_seq=0）。"""
    return build_initial_state(save_seed=_SAVE_SEED)


def _game_created_draft() -> Event:
    """构造 ``game_created`` 草稿：result 内嵌整状态快照（契约 §3.2）。"""
    initial = _initial()
    return Event(
        turn_id=1,
        actor_id="system",
        method="game_created",
        event_type=EventType.GAME_CREATED,
        params={
            "card_id": None,
            "starting_room_id": ROOM_ENTRY,
            "initial_character_id": PLAYER_ID,
            "save_seed": _SAVE_SEED,
            "snapshot_version": 1,
        },
        result={"initial_state": initial.model_dump(mode="json")},
    )


def _move_draft(*, turn_id: int = 2, to_room: str = ROOM_CELLAR) -> Event:
    """构造 ``CHARACTER_MOVED`` 草稿（东侧入口 -> 北侧地窖）。"""
    return Event(
        turn_id=turn_id,
        actor_id=PLAYER_ID,
        method="move_to",
        event_type=EventType.CHARACTER_MOVED,
        params={"direction": "north", "from_room": ROOM_ENTRY, "to_room": to_room},
        result={"from": ROOM_ENTRY, "to": to_room},
    )


def _take_draft(
    *, turn_id: int = 2, item_id: str = ITEM_TORCH, from_room: str = ROOM_ENTRY
) -> Event:
    """构造 ``ITEM_TAKEN`` 草稿。"""
    return Event(
        turn_id=turn_id,
        actor_id=PLAYER_ID,
        method="take_item",
        event_type=EventType.ITEM_TAKEN,
        params={"item_id": item_id, "from_room": from_room},
        result={"inventory_size": 1},
    )


def _use_draft(*, turn_id: int = 3, item_id: str = ITEM_TORCH) -> Event:
    """构造 ``ITEM_CONSUMED`` 草稿。"""
    return Event(
        turn_id=turn_id,
        actor_id=PLAYER_ID,
        method="use_item",
        event_type=EventType.ITEM_CONSUMED,
        params={"item_id": item_id},
        result={"consumed": True},
    )


def _talk_draft(*, turn_id: int = 2) -> Event:
    """构造 ``DIALOGUE_EXCHANGED`` 草稿。"""
    return Event(
        turn_id=turn_id,
        actor_id=PLAYER_ID,
        method="talk_to",
        event_type=EventType.DIALOGUE_EXCHANGED,
        params={"npc_id": NPC_SENTINEL, "topic": "对老哨兵问好"},
        result={"dialogue_state": "greeted"},
    )


# ---------------------------------------------------------------------------
# game_created：空存档重建 / 首事件约束
# ---------------------------------------------------------------------------
def test_apply_game_created_from_empty_restores_initial_state() -> None:
    """空存档 + 盖章 game_created → 场景初始状态（world_time=0、last_seq=1）。"""
    log = EventLog(_SAVE_SEED)
    [stamped] = log.stamp([_game_created_draft()])

    state = apply_events(None, [stamped])

    assert state.turn == 1
    assert state.world_time == 0
    assert state.rng_state.save_seed == _SAVE_SEED
    assert state.rng_state.last_seq == 1
    assert state.characters[PLAYER_ID].location == ROOM_ENTRY
    assert state.model_dump(exclude={"rng_state"}) == _initial().model_dump(exclude={"rng_state"})


def test_apply_events_empty_returns_same_state() -> None:
    """空事件列表返回入参状态本身（不构造新状态）。"""
    state = _initial()

    assert apply_events(state, []) == state


def test_apply_events_empty_from_empty_story_raises() -> None:
    """空存档配空事件列表无法投影（回放不允许静默返回空）。"""
    with pytest.raises(ValueError, match="empty"):
        apply_events(None, [])


def test_first_event_must_be_game_created() -> None:
    """空存档的首条事件不是 game_created → ValueError。"""
    with pytest.raises(ValueError, match="GAME_CREATED"):
        apply_events(None, [_move_draft()])


def test_missing_event_type_raises() -> None:
    """缺失 event_type 的事件投影必须失败（回放映射 INTERNAL，禁止跳过）。"""
    draft = Event(turn_id=2, actor_id=PLAYER_ID, method="move_to")

    with pytest.raises(ValueError, match="event_type"):
        apply_events(_initial(), [draft])


def test_unregistered_event_type_raises() -> None:
    """空注册表对合法 EventType 也抛 ValueError（未注册禁止静默跳过）。"""
    registry = ReducerRegistry()

    with pytest.raises(ValueError, match="no reducer registered"):
        registry.reduce(_initial(), _move_draft())


def test_duplicate_reducer_registration_raises() -> None:
    """同一 event_type 重复注册是编程错误。"""
    registry = ReducerRegistry()
    registry.register(EventType.GAME_CREATED, lambda state, event: _initial())

    with pytest.raises(ValueError, match="already registered"):
        registry.register(EventType.GAME_CREATED, lambda state, event: _initial())


def test_default_registry_covers_m2_event_types() -> None:
    """默认注册表覆盖 M2 全部 reducer 路由键。"""
    for event_type in (
        EventType.GAME_CREATED,
        EventType.CHARACTER_MOVED,
        EventType.ITEM_TAKEN,
        EventType.ITEM_CONSUMED,
        EventType.DIALOGUE_EXCHANGED,
    ):
        assert DEFAULT_REGISTRY.get(event_type) is not None


# ---------------------------------------------------------------------------
# 四类写事件
# ---------------------------------------------------------------------------
def test_character_moved_updates_location_only() -> None:
    """CHARACTER_MOVED：角色 location 改变，原状态不被修改。"""
    state = _initial()

    projected = state.apply([_move_draft()])

    assert projected.characters[PLAYER_ID].location == ROOM_CELLAR
    assert state.characters[PLAYER_ID].location == ROOM_ENTRY
    assert projected.turn == 2
    assert projected.world_time == 1


def test_character_moved_unknown_room_raises() -> None:
    """CHARACTER_MOVED 目标房间不存在 → ValueError。"""
    with pytest.raises(ValueError, match="target room"):
        _initial().apply([_move_draft(to_room=_UNKNOWN_ROOM)])


def test_item_taken_moves_item_from_room_to_inventory() -> None:
    """ITEM_TAKEN：房间移除物品、背包追加，物品定义仍保留在 state.items。"""
    projected = _initial().apply([_take_draft()])

    torch = projected.characters[PLAYER_ID]
    assert torch.inventory == [ITEM_TORCH]
    assert ITEM_TORCH not in projected.rooms[ROOM_ENTRY].item_ids
    assert ITEM_TORCH in projected.items
    assert projected.world_time == 1


def test_item_taken_not_in_room_raises() -> None:
    """ITEM_TAKEN 的物品不在当前房间（锈剑在地窖） → ValueError。"""
    with pytest.raises(ValueError, match="not in room"):
        _initial().apply([_take_draft(item_id=ITEM_SWORD)])


def test_item_consumed_removes_from_inventory() -> None:
    """ITEM_CONSUMED：背包移除该物品。"""
    with_torch = _initial().apply([_take_draft()])

    projected = with_torch.apply([_use_draft()])

    assert projected.characters[PLAYER_ID].inventory == []
    assert with_torch.characters[PLAYER_ID].inventory == [ITEM_TORCH]


def test_item_consumed_not_in_inventory_raises() -> None:
    """ITEM_CONSUMED 物品不在背包 → ValueError。"""
    with pytest.raises(ValueError, match="not in inventory"):
        _initial().apply([_use_draft()])


def test_dialogue_exchanged_updates_npc_state() -> None:
    """DIALOGUE_EXCHANGED：NPC dialogue_state 更新为该事件结果。"""
    projected = _initial().apply([_talk_draft()])

    assert projected.npcs[NPC_SENTINEL].dialogue_state == "greeted"
    assert _initial().npcs[NPC_SENTINEL].dialogue_state == "idle"


def test_dialogue_unknown_npc_raises() -> None:
    """DIALOGUE_EXCHANGED 目标 NPC 不存在 → ValueError。"""
    draft = _talk_draft()
    bad = draft.model_copy(update={"params": {"npc_id": _UNKNOWN_NPC, "topic": "你好"}})

    with pytest.raises(ValueError, match="unknown npc"):
        _initial().apply([bad])


# ---------------------------------------------------------------------------
# 公共规则：turn / world_time / rng_state.last_seq
# ---------------------------------------------------------------------------
def test_same_turn_events_advance_world_time_once() -> None:
    """同一 turn_id 的连续事件只推进一次 world_time，turn 取 max。"""
    state = _initial()

    first = state.apply([_move_draft(turn_id=2)])
    second = first.apply([_talk_draft(turn_id=2)])
    third = second.apply([_talk_draft(turn_id=2)])

    assert (first.turn, first.world_time) == (2, 1)
    assert (second.turn, second.world_time) == (2, 1)
    assert (third.turn, third.world_time) == (2, 1)


def test_new_turn_advances_world_time() -> None:
    """event.turn_id > state.turn 时 world_time +1。"""
    state = _initial().apply([_move_draft(turn_id=2)])

    projected = state.apply([_talk_draft(turn_id=3)])

    assert projected.turn == 3
    assert projected.world_time == 2


def test_rng_last_seq_tracks_stamped_event_seq() -> None:
    """rng_state.last_seq 取已盖章事件 seq 的最大值。"""
    log = EventLog(_SAVE_SEED)
    first = log.stamp([_game_created_draft()])
    log.append(first)
    second = log.stamp([_move_draft(turn_id=2)])

    state = apply_events(None, [*first, *second])

    assert state.rng_state.last_seq == 2


# ---------------------------------------------------------------------------
# 快照 + 事件重建等价性
# ---------------------------------------------------------------------------
def test_snapshot_plus_events_equals_full_replay() -> None:
    """从「快照状态 + 之后事件」重建与全量事件重放逐字段一致（第 14 章 14.4）。"""
    log = EventLog(_SAVE_SEED)
    created = log.stamp([_game_created_draft()])
    log.append(created)
    moved = log.stamp([_move_draft(turn_id=2)])
    log.append(moved)
    taken = log.stamp([_take_draft(turn_id=3, item_id=ITEM_SWORD, from_room=ROOM_CELLAR)])
    log.append(taken)
    all_events = [*created, *moved, *taken]

    snapshot_state = apply_events(None, [*created, *moved])
    restored = apply_events(snapshot_state, taken)

    assert restored == apply_events(None, all_events)
