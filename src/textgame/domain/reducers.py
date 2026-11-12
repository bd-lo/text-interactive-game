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
from typing import Any

from pydantic import ValidationError

from textgame.domain.crafting import CraftResult
from textgame.domain.events import Event, EventType
from textgame.domain.models import GameState, Item

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


def _item_hp_delta(item: Item | None) -> int:
    """物品 ``stats`` 的 HP 变动量（D4）：``heal`` 正数加血，``hp_delta`` 有符号增减。

    - ``heal`` 只接受正数（治疗药水 ``{"heal": 8}``）；``hp_delta`` 可正可负
      （story 的「结霜的烈酒」``{"hp_delta": -4}``）；两者同时出现时 ``heal`` 优先。
    - 缺失/非数值/非整数语义一律按 0 处理（旧场景物品无 stats，行为不变）；
      ``bool`` 是 ``int`` 子类，必须显式排除，避免 ``True`` 被当成 1 点治疗。
    """
    if item is None:
        return 0
    heal = item.stats.get("heal")
    if isinstance(heal, (int, float)) and not isinstance(heal, bool) and heal > 0:
        return int(heal)
    delta = item.stats.get("hp_delta")
    if isinstance(delta, (int, float)) and not isinstance(delta, bool):
        return int(delta)
    return 0


def _reduce_item_consumed(state: GameState | None, event: Event) -> GameState:
    """``ITEM_CONSUMED``：角色背包移除该物品，并按物品 ``stats`` 应用 HP 变动（D4）。

    ``heal``/``hp_delta`` 从 ``state.items`` 的物品定义读取（不新增事件 result 字段）：
    残血喝药回血、烈酒扣血，两者都 clamp 到 ``0..max_hp``；**HP 实际不变时返回原状态**
    （m2 golden 满血用治疗药水、m3 golden 用无 stats 火把 → ``state_hash`` 逐字不变）。
    """
    current = _require_state(state, event)
    character = current.characters.get(event.actor_id)
    if character is None:
        raise ValueError(f"ITEM_CONSUMED actor {event.actor_id} not in state")
    item_id = event.params.get("item_id")
    if not isinstance(item_id, str) or item_id not in character.inventory:
        raise ValueError(f"ITEM_CONSUMED item {item_id!r} not in inventory")
    inventory = [existing for existing in character.inventory if existing != item_id]
    hp_delta = _item_hp_delta(current.items.get(item_id))
    max_hp = character.attributes.get("max_hp", character.hp)
    new_hp = max(0, min(max_hp, character.hp + hp_delta))
    update: dict[str, object] = {"inventory": inventory}
    if new_hp != character.hp:
        update["hp"] = new_hp
    characters = dict(current.characters)
    characters[event.actor_id] = character.model_copy(update=update)
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
        """取 reducer；非 ``EventType`` 或未注册时抛 ``ValueError``（禁止静默跳过未知事件）。

        T7 加固：``Event.model_construct(event_type="X")`` 可绕过 pydantic 校验传入非
        ``EventType`` 值；错误信息不得访问 ``.value``（否则抛 ``AttributeError``），
        统一按运行时防御回退为 ``ValueError``。合法已注册类型的行为逐字不变。
        """
        if isinstance(event_type, EventType):
            reducer = self._reducers.get(event_type)
            if reducer is not None:
                return reducer
        raw = getattr(event_type, "value", event_type)
        raise ValueError(f"no reducer registered for event_type {raw!r}")

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


def _craft_payload(event: Event) -> tuple[CraftResult, list[Item]]:
    """解析 CRAFT_* 事件的 result.craft 与 result.produced_items；缺失/非法 → ValueError。"""
    raw_craft = event.result.get("craft")
    if not isinstance(raw_craft, dict):
        raise ValueError("CRAFT event must carry a result.craft object")
    try:
        craft = CraftResult.model_validate(raw_craft)
    except ValidationError as exc:
        raise ValueError(f"CRAFT result.craft is invalid: {exc}") from exc
    raw_items = event.result.get("produced_items", [])
    if not isinstance(raw_items, list):
        raise ValueError("CRAFT event result.produced_items must be a list")
    produced_items: list[Item] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            raise ValueError("CRAFT event produced_items entries must be objects")
        try:
            produced_items.append(Item.model_validate(raw_item))
        except ValidationError as exc:
            raise ValueError(f"CRAFT produced item is invalid: {exc}") from exc
    return craft, produced_items


def _reduce_craft(state: GameState | None, event: Event) -> GameState:
    """CRAFT_DETERMINISTIC/CRAFT_GENERATED：扣材料 + 产出/副产物入包与 state.items。

    第 14 章 §14.7 口径：成功时按 result.craft.consumed 的模板键与数量逐件移除同模板
    实例（不足 → ValueError），再按 result.produced_items 顺序追加实例到 inventory 与
    state.items（重复 id 或背包 >64 → ValueError），并校验实例定义的模板键顺序与
    result.craft.produced/byproducts 一致；掷骰失败（success=false）时 inventory/items
    零变化，仅公共规则推进 turn/world_time。
    """
    current = _require_state(state, event)
    craft, produced_items = _craft_payload(event)
    if not craft.success:
        if produced_items or craft.consumed or craft.produced or craft.byproducts:
            raise ValueError("failed CRAFT event must not carry consumed/produced/byproducts")
        return current

    expected_templates: list[str] = []
    for stack in craft.produced:
        expected_templates.extend([stack.template_id] * stack.qty)
    for byproduct in craft.byproducts:
        expected_templates.extend([byproduct.template_id] * byproduct.qty)
    if len(produced_items) != len(expected_templates):
        raise ValueError("CRAFT produced_items count does not match produced/byproducts quantities")
    for item, template_id in zip(produced_items, expected_templates, strict=True):
        if not item.tags or item.tags[0] != template_id:
            raise ValueError(
                f"CRAFT produced item {item.id!r} template tag does not match {template_id!r}"
            )

    character = current.characters.get(event.actor_id)
    if character is None:
        raise ValueError(f"CRAFT actor {event.actor_id!r} not in state")
    inventory = list(character.inventory)
    for stack in craft.consumed:
        removed = 0
        remaining: list[str] = []
        for item_id in inventory:
            entry = current.items.get(item_id)
            tag = entry.tags[0] if entry is not None and entry.tags else None
            if removed < stack.qty and tag == stack.template_id:
                removed += 1
                continue
            remaining.append(item_id)
        if removed < stack.qty:
            raise ValueError(
                f"CRAFT consumed template {stack.template_id!r} is insufficient in inventory"
            )
        inventory = remaining

    items = dict(current.items)
    for produced in produced_items:
        if produced.id in items:
            raise ValueError(
                f"CRAFT produced item id {produced.id!r} already exists in state.items"
            )
        items[produced.id] = produced
        inventory.append(produced.id)
    if len(inventory) > _INVENTORY_LIMIT:
        raise ValueError(f"inventory exceeds {_INVENTORY_LIMIT} items after craft")

    characters = dict(current.characters)
    characters[event.actor_id] = character.model_copy(update={"inventory": inventory})
    return current.model_copy(update={"characters": characters, "items": items})


def _reduce_environment_damage(state: GameState | None, event: Event) -> GameState:
    """``ENVIRONMENT_DAMAGE``（H4/L1）：按 ``event.result["hp_delta"]`` 改 HP，clamp ``0..max_hp``。

    只改 ``event.actor_id`` 角色的 ``hp``：不动物品/背包/位置/其他字段；``hp_delta`` 必须
    是非零整数（负=扣血，正=回血，便于测试与未来复用），``bool``/非整数/缺失 → ``ValueError``
    （回放不能静默跳过坏事件）。旧场景不产生该事件 → 旧存档回放/哈希零影响。
    """
    current = _require_state(state, event)
    character = current.characters.get(event.actor_id)
    if character is None:
        raise ValueError(f"ENVIRONMENT_DAMAGE actor {event.actor_id} not in state")
    hp_delta = event.result.get("hp_delta")
    if not isinstance(hp_delta, int) or isinstance(hp_delta, bool) or hp_delta == 0:
        raise ValueError("ENVIRONMENT_DAMAGE result.hp_delta must be a non-zero integer")
    max_hp = character.attributes.get("max_hp", character.hp)
    new_hp = max(0, min(max_hp, character.hp + hp_delta))
    if new_hp == character.hp:
        return current
    characters = dict(current.characters)
    characters[event.actor_id] = character.model_copy(update={"hp": new_hp})
    return current.model_copy(update={"characters": characters})


def _reduce_identity(state: GameState | None, event: Event) -> GameState:
    """CRAFT_REJECTED 预留 identity reducer（M4 不产生该事件，只做状态零变化投影）。"""
    return _require_state(state, event)


# ---------------------------------------------------------------------------
# R7（D-N6，用户批准新增 EventType）：行动自由度动作包的纯投影
# ---------------------------------------------------------------------------
def _require_str(mapping: dict[str, Any], key: str, *, where: str) -> str:
    """取 ``mapping[key]`` 的非空字符串值；缺失/非字符串/空串 → ``ValueError``。"""
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{where}.{key} must be a non-empty string")
    return value


def _require_int(
    mapping: dict[str, Any],
    key: str,
    *,
    where: str,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    """取 ``mapping[key]`` 的严格整数（显式排除 ``bool``）并做区间校验。"""
    value = mapping.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{where}.{key} must be an int")
    if minimum is not None and value < minimum:
        raise ValueError(f"{where}.{key} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{where}.{key} must be <= {maximum}")
    return value


def _reduce_item_dropped(state: GameState | None, event: Event) -> GameState:
    """``ITEM_DROPPED``（R7）：背包移除物品并追加到当前房间地面（纯投影）。

    校验：actor 存在；``item_id`` 是背包内的已知物品；``params.from_room`` 等于 actor
    当前房间且房间存在；地面尚未有该物品；``result.to_room`` 与 ``from_room`` 一致、
    ``result.item_name`` 为非空字符串。成功只改 ``characters``/``rooms``。
    """
    current = _require_state(state, event)
    character = current.characters.get(event.actor_id)
    if character is None:
        raise ValueError(f"ITEM_DROPPED actor {event.actor_id} not in state")
    item_id = _require_str(event.params, "item_id", where="ITEM_DROPPED params")
    from_room = _require_str(event.params, "from_room", where="ITEM_DROPPED params")
    if item_id not in character.inventory:
        raise ValueError(f"ITEM_DROPPED item {item_id!r} not in inventory")
    if item_id not in current.items:
        raise ValueError(f"ITEM_DROPPED unknown item {item_id!r}")
    if from_room != character.location:
        raise ValueError(
            f"ITEM_DROPPED from_room {from_room!r} does not match actor location "
            f"{character.location!r}"
        )
    room = current.rooms.get(from_room)
    if room is None:
        raise ValueError(f"ITEM_DROPPED unknown room {from_room!r}")
    if item_id in room.item_ids:
        raise ValueError(f"ITEM_DROPPED item {item_id!r} already on the ground")
    to_room = _require_str(event.result, "to_room", where="ITEM_DROPPED result")
    if to_room != from_room:
        raise ValueError(
            f"ITEM_DROPPED result.to_room {to_room!r} does not match params.from_room {from_room!r}"
        )
    _require_str(event.result, "item_name", where="ITEM_DROPPED result")
    inventory = [existing for existing in character.inventory if existing != item_id]
    characters = dict(current.characters)
    characters[event.actor_id] = character.model_copy(update={"inventory": inventory})
    rooms = dict(current.rooms)
    rooms[from_room] = room.model_copy(update={"item_ids": [*room.item_ids, item_id]})
    return current.model_copy(update={"characters": characters, "rooms": rooms})


def _reduce_item_given(state: GameState | None, event: Event) -> GameState:
    """``ITEM_GIVEN``（R7）：背包移除物品交给同房间 NPC；``state.items`` 保留该物品。

    NPC 模型没有背包字段（第 5 章基线模型不新增字段），物品只从玩家持有中离开；
    校验：actor 存在、``item_id`` 在背包且是已知物品、``npc_id`` 存在且与 actor 同房间、
    ``result.npc_name`` 为非空字符串。成功只改 ``characters``。
    """
    current = _require_state(state, event)
    character = current.characters.get(event.actor_id)
    if character is None:
        raise ValueError(f"ITEM_GIVEN actor {event.actor_id} not in state")
    item_id = _require_str(event.params, "item_id", where="ITEM_GIVEN params")
    npc_id = _require_str(event.params, "npc_id", where="ITEM_GIVEN params")
    if item_id not in character.inventory:
        raise ValueError(f"ITEM_GIVEN item {item_id!r} not in inventory")
    if item_id not in current.items:
        raise ValueError(f"ITEM_GIVEN unknown item {item_id!r}")
    npc = current.npcs.get(npc_id)
    if npc is None:
        raise ValueError(f"ITEM_GIVEN unknown npc {npc_id!r}")
    if npc.room_id != character.location:
        raise ValueError(
            f"ITEM_GIVEN npc {npc_id!r} is in {npc.room_id!r}, not actor location "
            f"{character.location!r}"
        )
    _require_str(event.result, "npc_name", where="ITEM_GIVEN result")
    inventory = [existing for existing in character.inventory if existing != item_id]
    characters = dict(current.characters)
    characters[event.actor_id] = character.model_copy(update={"inventory": inventory})
    return current.model_copy(update={"characters": characters})


def _reduce_combat_resolved(state: GameState | None, event: Event) -> GameState:
    """``COMBAT_RESOLVED``（R7）：一次攻击判定；命中则目标退出场景。

    校验：actor 存在；``params.npc_id`` 存在且 ``params.room_id == npc.room_id ==
    actor.location``；``result`` 为严格类型（``hit`` 必须是 bool，``roll`` 是 1..20 的 int，
    ``total``/``dc`` 是 int，``damage`` 是 >=0 的 int；``bool`` 不得冒充 int）。
    ``hit=True``：从房间 ``npc_ids`` 移除并从 ``state.npcs`` 删除；``hit=False``：状态零变化
    （仅由注册表补公共规则 turn/world_time）。
    """
    current = _require_state(state, event)
    character = current.characters.get(event.actor_id)
    if character is None:
        raise ValueError(f"COMBAT_RESOLVED actor {event.actor_id} not in state")
    npc_id = _require_str(event.params, "npc_id", where="COMBAT_RESOLVED params")
    room_id = _require_str(event.params, "room_id", where="COMBAT_RESOLVED params")
    npc = current.npcs.get(npc_id)
    if npc is None:
        raise ValueError(f"COMBAT_RESOLVED unknown npc {npc_id!r}")
    if room_id != character.location or npc.room_id != room_id:
        raise ValueError(
            f"COMBAT_RESOLVED npc {npc_id!r} (room {npc.room_id!r}) is not with actor "
            f"{event.actor_id!r} in room {room_id!r}"
        )
    if room_id not in current.rooms:
        raise ValueError(f"COMBAT_RESOLVED unknown room {room_id!r}")
    hit = event.result.get("hit")
    if not isinstance(hit, bool):
        raise ValueError("COMBAT_RESOLVED result.hit must be a bool")
    _require_int(event.result, "roll", where="COMBAT_RESOLVED result", minimum=1, maximum=20)
    _require_int(event.result, "total", where="COMBAT_RESOLVED result")
    _require_int(event.result, "dc", where="COMBAT_RESOLVED result")
    _require_int(event.result, "damage", where="COMBAT_RESOLVED result", minimum=0)
    if not hit:
        return current
    room = current.rooms[room_id]
    rooms = dict(current.rooms)
    rooms[room_id] = room.model_copy(
        update={"npc_ids": [existing for existing in room.npc_ids if existing != npc_id]}
    )
    npcs = {key: value for key, value in current.npcs.items() if key != npc_id}
    return current.model_copy(update={"rooms": rooms, "npcs": npcs})


def _reduce_improvised_action(state: GameState | None, event: Event) -> GameState:
    """``IMPROVISED_ACTION``（R7）：裁判已确认的临场动作，identity 投影。

    不产生数值/物品；只校验 actor 存在与 ``params.action_text`` / ``result.narrative_hint``
    为非空字符串（严格类型），状态零变化后由注册表补公共规则 turn/world_time。
    """
    current = _require_state(state, event)
    if event.actor_id not in current.characters:
        raise ValueError(f"IMPROVISED_ACTION actor {event.actor_id} not in state")
    _require_str(event.params, "action_text", where="IMPROVISED_ACTION params")
    _require_str(event.result, "narrative_hint", where="IMPROVISED_ACTION result")
    return current


DEFAULT_REGISTRY = ReducerRegistry()
DEFAULT_REGISTRY.register(EventType.GAME_CREATED, _reduce_game_created)
DEFAULT_REGISTRY.register(EventType.CHARACTER_MOVED, _reduce_character_moved)
DEFAULT_REGISTRY.register(EventType.ITEM_TAKEN, _reduce_item_taken)
DEFAULT_REGISTRY.register(EventType.ITEM_CONSUMED, _reduce_item_consumed)
DEFAULT_REGISTRY.register(EventType.DIALOGUE_EXCHANGED, _reduce_dialogue_exchanged)
DEFAULT_REGISTRY.register(EventType.CRAFT_DETERMINISTIC, _reduce_craft)
DEFAULT_REGISTRY.register(EventType.CRAFT_GENERATED, _reduce_craft)
DEFAULT_REGISTRY.register(EventType.CRAFT_REJECTED, _reduce_identity)
DEFAULT_REGISTRY.register(EventType.ENVIRONMENT_DAMAGE, _reduce_environment_damage)
DEFAULT_REGISTRY.register(EventType.ITEM_DROPPED, _reduce_item_dropped)
DEFAULT_REGISTRY.register(EventType.ITEM_GIVEN, _reduce_item_given)
DEFAULT_REGISTRY.register(EventType.COMBAT_RESOLVED, _reduce_combat_resolved)
DEFAULT_REGISTRY.register(EventType.IMPROVISED_ACTION, _reduce_improvised_action)


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
