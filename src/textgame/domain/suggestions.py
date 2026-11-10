"""D7 玩家建议与失败反馈（R2 算法；domain 层纯函数，services/orchestration 共用）：确定性纯函数，只读 ``GameState``，零 IO / 零随机 / 零模型。

契约（backend 接线，接口冻结）：

- ``suggest_actions(state, *, limit=4) -> list[str]``：成功回合给玩家「下一步可提交的中文指令」。
  优先级：story 目标链（听→造→点亮）→ 房间 NPC → 房间物品 → 出口方向 → 背包可用消耗品 → 兜底
  「环顾四周 / 查看背包」；去重、最多 ``limit`` 条（``limit`` 上限 4）。
- ``failure_feedback(state, error_code, *, reason=None) -> tuple[str, list[str]]``：失败回合给
  「为什么 + 下一步」（``reason`` 为编排层可选传入的中文短句，英文/内部 ID 会被静默丢弃）。
  ``hint`` 只含玩家可读中文（不出现内部 ID / 英文术语 / 协议字段），``suggested_actions`` 复用
  :func:`suggest_actions` 保证「当前真的可执行」。

约束：不写状态、不产生事件、不改 ``TurnResult.text``；同一状态两次调用结果逐字相同（可回放）。
"""

from __future__ import annotations

import re
from collections.abc import Callable

from textgame.domain.models import NPC, Character, GameState, Item, Room
from textgame.domain.results import ErrorCode
from textgame.domain.scenario import DIRECTION_NAMES, PLAYER_ID
from textgame.domain.scenario_story import (
    STORY_TOWER_ROOM_ID,
    STORY_WATCHMAN_ID,
    is_story_state,
    story_ending,
    story_goal,
)

__all__ = ["failure_feedback", "suggest_actions"]

#: 至少一个中日韩统一表意文字（用于拒绝无中文的「原因」串）。
_CJK = re.compile(r"[\u4e00-\u9fff]")

#: 无更强建议时的兜底动作（永远可执行、零状态写入）。
_FALLBACK_ACTIONS: tuple[str, ...] = ("环顾四周", "查看背包")
_CONSUMABLE_TAG = "consumable"
_FORGE_STATION_TAG = "station.forge"
_SIGNAL_TORCH_NAME = "信号火把"
_TORCH_NAME = "火把"
_COPPER_NAME = "铜线"
_FLINT_NAME = "火石"
_MAX_SUGGESTIONS = 4


def _direction_cn(direction: str) -> str:
    """方向英文值 → 中文词；未知方向回退原值。"""
    return DIRECTION_NAMES.get(direction, direction)


def _move_action(direction: str) -> str:
    """方向 → 可直接提交的移动指令（如 ``向北走``）。"""
    return f"向{_direction_cn(direction)}走"


def _talk_action(npc_name: str) -> str:
    """NPC 名 → 可直接提交的对话指令（如 ``和守夜人老兵说话``）。"""
    return f"和{npc_name}说话"


def _room_of(state: GameState, character: Character) -> Room | None:
    """玩家当前房间；状态缺角色/房间时返回 ``None``（调用方走兜底）。"""
    return state.rooms.get(character.location)


def _items_of(state: GameState, item_ids: list[str]) -> list[Item]:
    """按给定 id 顺序取存在的物品（悬空引用静默跳过）。"""
    items: list[Item] = []
    for item_id in item_ids:
        item = state.items.get(item_id)
        if item is not None:
            items.append(item)
    return items


def _inventory_items(state: GameState, character: Character) -> list[Item]:
    """玩家行囊内的物品实例（按行囊顺序；重复实例保留）。"""
    return _items_of(state, character.inventory)


def _npcs_of(state: GameState, npc_ids: list[str]) -> list[NPC]:
    """按给定 id 顺序取存在的 NPC（悬空引用静默跳过）。"""
    npcs: list[NPC] = []
    for npc_id in npc_ids:
        npc = state.npcs.get(npc_id)
        if npc is not None:
            npcs.append(npc)
    return npcs


def _first_direction(state: GameState, start: str, target: str) -> str | None:
    """``start`` → ``target`` 的最短路径首步方向（BFS；方向顺序固定，保证确定性）。

    只读纯计算：房间数极小（≤6），代价可忽略；不可达返回 ``None``。
    """
    if start == target:
        return None
    frontier: list[tuple[str, str]] = [(start, "")]
    visited: set[str] = {start}
    while frontier:
        next_frontier: list[tuple[str, str]] = []
        for room_id, first_direction in frontier:
            room = state.rooms.get(room_id)
            if room is None:
                continue
            for direction, neighbour in room.exits.items():
                if neighbour in visited:
                    continue
                step = first_direction or direction
                if neighbour == target:
                    return step
                visited.add(neighbour)
                next_frontier.append((neighbour, step))
        frontier = next_frontier
    return None


def _moves_toward(state: GameState, character: Character, room_id: str) -> list[str]:
    """朝目标房间走一步的建议（已在目标房间 / 不可达 → 空列表）。"""
    direction = _first_direction(state, character.location, room_id)
    return [] if direction is None else [_move_action(direction)]


def _room_with_station(state: GameState, tag: str) -> str | None:
    """带指定 ambient tag 的房间 id（如 ``station.forge``）；按房间字典顺序取首个。"""
    for room_id, room in state.rooms.items():
        if tag in room.ambient_tags:
            return room_id
    return None


def _room_holding_item(state: GameState, predicate: Callable[[Item], bool]) -> str | None:
    """包含满足谓词物品的房间 id（只在房间地面找，不看背包；按房间顺序取首个）。"""
    for room_id, room in state.rooms.items():
        for item in _items_of(state, room.item_ids):
            if predicate(item):
                return room_id
    return None


def _count_in_inventory(items: list[Item], predicate: Callable[[Item], bool]) -> int:
    """行囊内满足谓词的实例数。"""
    return sum(1 for item in items if predicate(item))


def _is_torch(item: Item) -> bool:
    """火把类材料：名称含「火把」，或模板/标签标记为火把。"""
    return _TORCH_NAME in item.name or "item.torch" in item.tags


def _is_copper(item: Item) -> bool:
    """铜线材料：名称含「铜线」，或 tags 首项为 ``item.copper_wire``。"""
    return _COPPER_NAME in item.name or (bool(item.tags) and item.tags[0] == "item.copper_wire")


def _is_flint(item: Item) -> bool:
    """火石：名称含「火石」（story 场景目标 3 的关键道具）。"""
    return _FLINT_NAME in item.name


def _story_suggestions(state: GameState, character: Character, room: Room) -> list[str]:
    """story「余烬哨站」目标链建议：听老兵 → 造信号火把 → 带火石登塔点亮。"""
    goal = story_goal(state)
    if goal is None:
        return []
    done: dict[str, bool] = {}
    for objective in goal.get("objectives", []):
        if isinstance(objective, dict):
            done[str(objective.get("id"))] = bool(objective.get("done"))
    suggestions: list[str] = []
    inventory = _inventory_items(state, character)

    # 目标 1：听守夜人老兵说说任务（NPC 不在场就先走过去）。
    if not done.get("listen", False):
        watchman = state.npcs.get(STORY_WATCHMAN_ID)
        if watchman is not None:
            if watchman.room_id == character.location:
                suggestions.append(_talk_action(watchman.name))
            else:
                suggestions.extend(_moves_toward(state, character, watchman.room_id))

    # 目标 2：用火把 + 铜线造一支信号火把。
    if not done.get("craft", False):
        torch_held = _count_in_inventory(inventory, _is_torch) > 0
        copper_held = _count_in_inventory(inventory, _is_copper) >= 2
        forge_room = _room_with_station(state, _FORGE_STATION_TAG)
        at_forge = forge_room is not None and forge_room == character.location
        if at_forge and torch_held and copper_held:
            suggestions.append(f"打造{_SIGNAL_TORCH_NAME}")
        else:
            if not torch_held:
                torch_room = _room_holding_item(state, _is_torch)
                if torch_room == character.location:
                    suggestions.append(f"拿起{_TORCH_NAME}")
                elif torch_room is not None:
                    suggestions.extend(_moves_toward(state, character, torch_room))
            if not copper_held:
                copper_room = _room_holding_item(state, _is_copper)
                if copper_room == character.location:
                    suggestions.append(f"拿起{_COPPER_NAME}")
                elif copper_room is not None:
                    suggestions.extend(_moves_toward(state, character, copper_room))
            if forge_room is not None and not at_forge:
                suggestions.extend(_moves_toward(state, character, forge_room))

    # 目标 3：带火石登上瞭望塔点亮信号。
    if not done.get("light", False):
        flint_held = _count_in_inventory(inventory, _is_flint) > 0
        if not flint_held:
            flint_room = _room_holding_item(state, _is_flint)
            if flint_room == character.location:
                suggestions.append(f"拿起{_FLINT_NAME}")
            elif flint_room is not None:
                suggestions.extend(_moves_toward(state, character, flint_room))
        elif character.location != STORY_TOWER_ROOM_ID:
            suggestions.extend(_moves_toward(state, character, STORY_TOWER_ROOM_ID))
    return suggestions


def _generic_suggestions(state: GameState, character: Character, room: Room) -> list[str]:
    """非 story / story 兜底建议：NPC → 首件物品 → 出口 → 其余物品 → 背包消耗品 → 固定兜底。

    交错排序的理由：4 条上限内尽量同时覆盖「人/物/路/用法」四类引导，而不是被同房间大量
    物品或大量出口挤占（确定性、只依赖状态）。
    """
    suggestions: list[str] = []
    for npc in _npcs_of(state, room.npc_ids):
        suggestions.append(_talk_action(npc.name))
    room_items = _items_of(state, room.item_ids)
    if room_items:
        suggestions.append(f"拿起{room_items[0].name}")
    for direction in room.exits:
        suggestions.append(_move_action(direction))
    for item in room_items[1:]:
        suggestions.append(f"拿起{item.name}")
    for item in _inventory_items(state, character):
        if _CONSUMABLE_TAG in item.tags:
            suggestions.append(f"使用{item.name}")
    suggestions.extend(_FALLBACK_ACTIONS)
    return suggestions


def _dedupe_truncate(actions: list[str], limit: int) -> list[str]:
    """去空/去重/截断：保持首次出现顺序（确定性）。"""
    result: list[str] = []
    for action in actions:
        text = action.strip()
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def suggest_actions(state: GameState, *, limit: int = 4) -> list[str]:
    """生成当前状态可直接提交的中文建议指令（确定性、去重、≤``limit``、无内部 ID）。

    结局已达成（story 胜利/倒下）时返回空列表：本局不再接受新行动。
    ``limit`` 上限 4（D7 契约），非正数返回空列表；状态缺角色/房间时只给固定兜底。
    """
    if limit <= 0:
        return []
    effective_limit = min(limit, _MAX_SUGGESTIONS)
    character = state.characters.get(PLAYER_ID)
    if character is None:
        return _dedupe_truncate(list(_FALLBACK_ACTIONS), effective_limit)
    if is_story_state(state) and story_ending(state) is not None:
        return []
    room = _room_of(state, character)
    if room is None:
        return _dedupe_truncate(list(_FALLBACK_ACTIONS), effective_limit)
    ordered: list[str] = []
    if is_story_state(state):
        ordered.extend(_story_suggestions(state, character, room))
    ordered.extend(_generic_suggestions(state, character, room))
    return _dedupe_truncate(ordered, effective_limit)


# ---------------------------------------------------------------------------
# 失败反馈（为什么 + 下一步）
# ---------------------------------------------------------------------------

_HINT_INVALID = "这句话没能对上一个动作：换个说法，或从下面的建议里选一个现在能做的。"
_HINT_CONFLICT = "对局状态刚刚被更新过，这一回合没有写入；刷新对局视图后再试一次。"
_HINT_INTERNAL = "刚才处理时出了点岔子，本回合没有写入任何状态；再试一次就好。"
_HINT_UNKNOWN = "这一回合没有生效：换一种说法，或先看看四周再决定下一步。"


def _room_summary(state: GameState, room: Room | None) -> str:
    """玩家可读的当前场景概要：出口方向 + 地面物品 + 在场 NPC（都不含内部 ID）。"""
    if room is None:
        return ""
    parts: list[str] = []
    directions = "、".join(_direction_cn(direction) for direction in room.exits)
    if directions:
        parts.append(f"这里可以往{directions}走")
    items = _items_of(state, room.item_ids)
    if items:
        parts.append("地上有" + "、".join(item.name for item in items[:3]))
    npcs = _npcs_of(state, room.npc_ids)
    if npcs:
        parts.append("旁边是" + "、".join(npc.name for npc in npcs[:2]))
    return "；".join(parts) + "。" if parts else ""


def _inventory_summary(state: GameState, character: Character | None) -> str:
    """玩家可读的行囊概要（无名/空行囊也给出可行动文案）。"""
    if character is None:
        return ""
    items = _inventory_items(state, character)
    if not items:
        return "行囊现在是空的。"
    names = "、".join(item.name for item in items[:3])
    return f"行囊里有{names}。"


def _safe_reason(reason: str | None) -> str | None:
    """把编排层传来的失败原因收敛为可展示中文短句（防御内部诊断/超长/空值）。

    与 ``turn_orchestrator._safe_player_reason`` 同口径：含 ASCII 字母/数字/下划线一律视为
    内部诊断（工具名、ULID、英文规则消息）不展示；只接受 ≤60 字、含中文的短句。
    """
    if not isinstance(reason, str):
        return None
    text = " ".join(reason.split()).strip()
    if not text or len(text) > 60:
        return None
    if any(char.isascii() and (char.isalnum() or char == "_") for char in text):
        return None
    if not _CJK.search(text):
        return None
    return text


def _as_sentence(text: str) -> str:
    """给短句补句末标点（已是 ``。！？；`` 结尾则原样返回），保证与后续提示拼接自然。"""
    stripped = text.strip()
    if not stripped:
        return stripped
    if stripped[-1] in "。！？；…":
        return stripped
    return stripped + "。"


def failure_feedback(
    state: GameState,
    error_code: ErrorCode | None,
    *,
    reason: str | None = None,
) -> tuple[str, list[str]]:
    """失败回合的「为什么 + 下一步」：返回 ``(hint, suggested_actions)``。

    ``hint`` 只含玩家可读中文，不出现内部 ID / 英文诊断 / 协议字段；建议动作复用
    :func:`suggest_actions`（当前状态真正可执行、去重、≤4 条），未知/内部错误也不例外。
    story 结局后返回结束提示且不再给行动建议（编排层会拒绝新回合）。

    ``reason``（R2 加法，编排层可选传入）：服务层/规则层的具体失败原因。只有通过
    :func:`_safe_reason` 的中文短句才会作为「为什么」拼进 hint（英文诊断/内部 ID 静默丢弃，
    退回按错误码分级的通用提示）；协议级错误（CONFLICT/INTERNAL）不使用 reason。
    """
    character = state.characters.get(PLAYER_ID)
    suggestions = suggest_actions(state, limit=_MAX_SUGGESTIONS)
    if character is not None and is_story_state(state) and story_ending(state) is not None:
        return ("这一局的故事已经结束，不再接受新的行动；可以重新开始一局。", [])
    room = _room_of(state, character) if character is not None else None
    summary = _room_summary(state, room)
    inventory = _inventory_summary(state, character)
    safe_reason = _safe_reason(reason)
    if safe_reason is not None:
        safe_reason = _as_sentence(safe_reason)
    if error_code == ErrorCode.NOT_FOUND:
        hint = (safe_reason or "你要找的东西或方向不在这里。") + summary
    elif error_code == ErrorCode.OUT_OF_RANGE:
        hint = (safe_reason or "这一步的条件还差一点（东西不够或拿不动）。") + inventory + summary
    elif error_code == ErrorCode.NOT_ALLOWED:
        hint = (safe_reason or "现在不能这么做：先完成手上这一步，或换个对象。") + summary
    elif error_code == ErrorCode.CONFLICT:
        hint = _HINT_CONFLICT
    elif error_code == ErrorCode.INTERNAL:
        hint = _HINT_INTERNAL
    elif error_code == ErrorCode.INVALID_INPUT:
        if safe_reason is not None:
            hint = safe_reason + "可以从下面的建议里挑一个现在能做的。" + summary
        else:
            hint = _HINT_INVALID + summary
    else:
        hint = _HINT_UNKNOWN
    hint = hint.strip()
    if not hint:
        hint = _HINT_UNKNOWN
    return (hint, suggestions[:_MAX_SUGGESTIONS])
