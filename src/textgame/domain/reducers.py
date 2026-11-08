"""事件 → ``GameState`` 的纯投影 (第 14 章 14.4/14.7 的 ``ReducerRegistry``)。

reducer 是回放与回合提交共用的唯一投影实现：``GameState.apply`` 直接委托
``apply_events``，``GameEngine``/``persistence`` 只调用本模块，不各自实现一套。
纯投影：不写库、不读时钟、不随机；同一事件序列必得同一状态（第 18 章 18.3）。

公共规则（契约 §3.3，每条事件应用后）：
- ``turn = max(state.turn, event.turn_id)``；
- ``world_time += 1`` 当 ``event.turn_id > state.turn``（``game_created`` 从空存档投影时
  以事件自身的 ``turn_id`` 为基准，保证与 ``build_initial_state`` 的 ``world_time=0`` 一致）；
- ``rng_state.last_seq = max(last_seq, event.seq)``。

未知/缺失 ``event_type`` 一律抛 ``ValueError``（回放时映射 ``INTERNAL``，禁止静默跳过）。
"""

from __future__ import annotations

from collections.abc import Callable

from textgame.domain.events import Event, EventType
from textgame.domain.models import GameState

__all__ = [
    "DEFAULT_REGISTRY",
    "ReducerRegistry",
    "apply_events",
]

#: reducer 签名：``(前一个状态 | None, 事件) -> 新状态``；``None`` 只允许出现在首条 ``game_created``。
Reducer = Callable[[GameState | None, Event], GameState]

_INVENTORY_LIMIT = 64


def _require_state(state: GameState | None, event: Event) -> GameState:
    """取当前状态；首条事件不是 ``game_created`` 时拒绝投影。"""
    if state is None:
        raise ValueError(
            f"event seq={event.seq} cannot be projected without a prior state "
            "(first event must be GAME_CREATED)"
        )
    return state


def _reduce_game_created(state: GameState | None, event: Event) -> GameState:
    """``game_created``：直接用事件载荷里的整状态快照重建（M2 简化，契约 §3.2）。"""
    raw_state = event.result.get("initial_state")
    if not isinstance(raw_state, dict):
        raise ValueError("GAME_CREATED event must carry result.initial_state object")
    return GameState.model_validate(raw_state)


def _reduce_character_moved(state: GameState | None, event: Event) -> GameState:
    """``CHARACTER_MOVED``：把角色 ``location`` 更新为目标房间（契约 §3.3）。"""
    current = _require_state(state, event)
    character = current.characters.get(event.actor_id)
    if character is None:
        raise ValueError(f"CHARACTER_MOVED actor {event.actor_id} not in state")
    to_room = event.params.get("to_room", event.result.get("to"))
    if not isinstance(to_room, str) or to_room not in current.rooms:
        raise ValueError(f"CHARACTER_MOVED target room {to_room!r} not in state")
    updated = character.model_copy(update={"location": to_room})
    characters = dict(current.characters)
    characters[event.actor_id] = updated
    return current.model_copy(update={"characters": characters})


def _reduce_item_taken(state: GameState | None, event: Event) -> GameState:
    """``ITEM_TAKEN``：房间移除物品、角色背包追加（≤64）。"""
    current = _require_state(state, event)
    character = current.characters.get(event.actor_id)
    if character is None:
        raise ValueError(f"ITEM_TAKEN actor {event.actor_id} not in state")
    item_id = event.params.get("item_id")
    if not isinstance(item_id, str) or item_id not in current.items:
        raise ValueError(f"ITEM_TAKEN unknown item {item_id!r}")
    room_id = event.params.get("from_room", character.location)
    if not isinstance(room_id, str) or room_id not in current.rooms:
        raise ValueError(f"ITEM_TAKEN unknown room {room_id!r}")
    room = current.rooms[room_id]
    if item_id not in room.item_ids:
        raise ValueError(f"ITEM_TAKEN item {item_id!r} not in room {room_id}")
    inventory = [*character.inventory, item_id]
    if len(inventory) > _INVENTORY_LIMIT:
        raise ValueError(f"inventory exceeds {_INVENTORY_LIMIT} items")
    characters = dict(current.characters)
    characters[event.actor_id] = character.model_copy(update={"inventory": inventory})
    rooms = dict(current.rooms)
    rooms[room_id] = room.model_copy(
        update={"item_ids": [existing for existing in room.item_ids if existing != item_id]}
    )
    return current.model_copy(update={"characters": characters, "rooms": rooms})


def _reduce_item_consumed(state: GameState | None, event: Event) -> GameState:
    """``ITEM_CONSUMED``：角色背包移除该物品（物品定义保留在 ``state.items``）。"""
    current = _require_state(state, event)
    character = current.characters.get(event.actor_id)
    if character is None:
        raise ValueError(f"ITEM_CONSUMED actor {event.actor_id} not in state")
    item_id = event.params.get("item_id")
    if not isinstance(item_id, str) or item_id not in character.inventory:
        raise ValueError(f"ITEM_CONSUMED item {item_id!r} not in inventory")
    inventory = [existing for existing in character.inventory if existing != item_id]
    characters = dict(current.characters)
    characters[event.actor_id] = character.model_copy(update={"inventory": inventory})
    return current.model_copy(update={"characters": characters})


def _reduce_dialogue_exchanged(state: GameState | None, event: Event) -> GameState:
    """``DIALOGUE_EXCHANGED``：把 NPC 的 ``dialogue_state`` 更新为事件结果。"""
    current = _require_state(state, event)
    npc_id = event.params.get("npc_id")
    if not isinstance(npc_id, str) or npc_id not in current.npcs:
        raise ValueError(f"DIALOGUE_EXCHANGED unknown npc {npc_id!r}")
    dialogue_state = event.result.get("dialogue_state")
    if not isinstance(dialogue_state, str) or not dialogue_state:
        raise ValueError("DIALOGUE_EXCHANGED result.dialogue_state must be a non-empty string")
    npcs = dict(current.npcs)
    npcs[npc_id] = current.npcs[npc_id].model_copy(update={"dialogue_state": dialogue_state})
    return current.model_copy(update={"npcs": npcs})


def _apply_common_rules(previous: GameState | None, state: GameState, event: Event) -> GameState:
    """应用契约 §3.3 的公共规则（turn / world_time / rng_state.last_seq）。"""
    # 空存档首条事件必为 game_created：以事件自身 turn_id 为基准，不额外推进 world_time
    prior_turn = event.turn_id if previous is None else previous.turn
    world_time = state.world_time + 1 if event.turn_id > prior_turn else state.world_time
    rng_state = state.rng_state.model_copy(
        update={"last_seq": max(state.rng_state.last_seq, event.seq or 0)}
    )
    return state.model_copy(
        update={
            "turn": max(state.turn, event.turn_id),
            "world_time": world_time,
            "rng_state": rng_state,
        }
    )


class ReducerRegistry:
    """事件类型 → reducer 的注册表；应用时先路由再补公共规则。"""

    def __init__(self) -> None:
        """创建空注册表；默认注册表见模块级 ``DEFAULT_REGISTRY``。"""
        self._reducers: dict[EventType, Reducer] = {}

    def register(self, event_type: EventType, reducer: Reducer) -> None:
        """注册 reducer；同一 ``event_type`` 重复注册视为编程错误。"""
        if event_type in self._reducers:
            raise ValueError(f"reducer already registered for {event_type.value}")
        self._reducers[event_type] = reducer

    def get(self, event_type: EventType) -> Reducer:
        """取 reducer；未注册时抛 ``ValueError``（禁止静默跳过未知事件）。"""
        reducer = self._reducers.get(event_type)
        if reducer is None:
            raise ValueError(f"no reducer registered for event_type {event_type.value}")
        return reducer

    def reduce(self, state: GameState | None, event: Event) -> GameState:
        """投影单条事件；返回新状态，不修改入参。"""
        event_type = event.event_type
        if event_type is None:
            raise ValueError(f"event seq={event.seq} (method={event.method}) is missing event_type")
        if state is None and event_type is not EventType.GAME_CREATED:
            raise ValueError(
                f"event seq={event.seq} ({event_type.value}) cannot start an empty story; "
                "first event must be GAME_CREATED"
            )
        projected = self.get(event_type)(state, event)
        return _apply_common_rules(state, projected, event)


DEFAULT_REGISTRY = ReducerRegistry()
DEFAULT_REGISTRY.register(EventType.GAME_CREATED, _reduce_game_created)
DEFAULT_REGISTRY.register(EventType.CHARACTER_MOVED, _reduce_character_moved)
DEFAULT_REGISTRY.register(EventType.ITEM_TAKEN, _reduce_item_taken)
DEFAULT_REGISTRY.register(EventType.ITEM_CONSUMED, _reduce_item_consumed)
DEFAULT_REGISTRY.register(EventType.DIALOGUE_EXCHANGED, _reduce_dialogue_exchanged)


def apply_events(state: GameState | None, events: list[Event]) -> GameState:
    """按顺序纯投影事件列表；空列表返回入参状态（不得为 ``None``）。

    从空存档重建时 ``state`` 传 ``None``，首条事件必须是 ``GAME_CREATED``；否则抛
    ``ValueError``。任何 reducer 失败都立即抛出，不做部分投影（回放不能“跳过坏事件”）。
    """
    if not events:
        if state is None:
            raise ValueError("cannot project an empty event list onto an empty story")
        return state
    current = state
    for event in events:
        current = DEFAULT_REGISTRY.reduce(current, event)
    if current is None:  # pragma: no cover - 首条 game_created 后必有状态
        raise ValueError("event projection produced no state")
    return current
