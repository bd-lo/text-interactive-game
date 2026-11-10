"""动作服务 (第 6 章 6.3、契约 §4.3)：只产草稿 ``Event``，不提交、不写库。

写方法失败返回 ``Result.fail(...)`` 且 ``events=[]``（原子性）；L0 预条件在本层自检
（出口存在、物品在房间/背包、NPC 同房间），不依赖工具层或编排层兜底。草稿事件字段
``turn_id/actor_id/method/event_type/params/result`` 由本层填好，``id/seq/rng_seed/ts``
留给 ``GameEngine`` 盖章（第 5 章 5.3）。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from textgame.domain.events import Event, EventType
from textgame.domain.models import GameState
from textgame.domain.results import ErrorCode, Result
from textgame.domain.scenario import DIRECTION_NAMES
from textgame.domain.types import Direction, ItemId, NpcId
from textgame.services.query_service import QueryService
from textgame.services.tool_context import ToolContext

#: 背包上限与 ``Character.inventory`` 的模型约束保持一致（第 5 章 5.4）。
_INVENTORY_LIMIT = 64

#: 剧情/任务类物品标签前缀（R1 玩家实测 P0-3 安全子集）：带这些标签的物品不可被
#: 「使用」消耗，否则任务物品会被永久销毁。场景数据里「古物」的 tags 为 ``["quest"]``
#: （``domain/scenario.py``）；``quest_item``/``quest.*``/``story.*`` 等同前缀均视为剧情类。
#: 普通消耗品（如 ``consumable``）与一般物品标签（``light``/``weapon``/``item.*``）不受影响。
_STORY_TAG_PREFIXES: tuple[str, ...] = ("quest", "story")


def _is_story_item(tags: Sequence[str]) -> bool:
    """物品是否带剧情/任务标签（前缀匹配；仅供 ``use_item`` 的前置保护使用）。"""
    for tag in tags:
        for prefix in _STORY_TAG_PREFIXES:
            if tag == prefix or tag.startswith(f"{prefix}_") or tag.startswith(f"{prefix}."):
                return True
    return False


def _direction_name(direction: str) -> str:
    """方向英文枚举值 → 中文方向词（叙事与失败消息用）。"""
    return DIRECTION_NAMES.get(direction, direction)


def _room_text(view: dict[str, Any]) -> str:
    """把房间视图拼成一段确定性中文描述（``look`` 的 ``data["text"]``）。"""
    parts: list[str] = [f"{view['title']}。", f"{view['description']}"]
    items = view.get("items") or []
    npcs = view.get("npcs") or []
    exits = view.get("exits") or {}
    if items:
        parts.append("地上有" + "、".join(str(item["name"]) for item in items) + "。")
    if npcs:
        parts.append("这里站着" + "、".join(str(npc["name"]) for npc in npcs) + "。")
    if exits:
        parts.append("可以往" + "、".join(_direction_name(str(key)) for key in exits) + "走。")
    else:
        parts.append("四下没有明显的出口。")
    return "".join(parts)


class ActionService:
    """动作写方法；构造注入只读查询服务，``look`` 复用房间视图。"""

    def __init__(self, query: QueryService) -> None:
        """``query`` 用于 ``look`` 读取房间视图（只读，不产生状态）。"""
        self._query = query

    def look(self, ctx: ToolContext, state: GameState) -> Result:
        """``READ_ONLY``：返回房间描述文本，不产生事件。"""
        view = self._query.get_room_view(state, ctx.actor_id)
        return Result.success(data={"text": _room_text(view)})

    def move_to(self, ctx: ToolContext, state: GameState, *, direction: Direction) -> Result:
        """移动到相邻房间；出口不存在 → ``NOT_FOUND``（L0 预条件）。"""
        character = state.characters.get(ctx.actor_id)
        if character is None:
            return Result.fail(ErrorCode.NOT_FOUND, f"角色不存在: {ctx.actor_id}")
        room = state.rooms.get(character.location)
        if room is None:
            return Result.fail(ErrorCode.NOT_FOUND, f"角色所在地点不存在: {character.location}")
        target_room = room.exits.get(direction)
        if target_room is None:
            return Result.fail(ErrorCode.NOT_FOUND, f"「{_direction_name(direction)}」边没有出口")
        draft = Event(
            turn_id=ctx.turn_id,
            actor_id=ctx.actor_id,
            method="move_to",
            event_type=EventType.CHARACTER_MOVED,
            params={"direction": direction, "from_room": room.id, "to_room": target_room},
            result={"from": room.id, "to": target_room},
        )
        return Result.success(
            data={"from_room": room.id, "to_room": target_room, "direction": direction},
            events=[draft],
        )

    def take_item(self, ctx: ToolContext, state: GameState, *, item_id: ItemId) -> Result:
        """从当前房间拿起物品；不在房间 → ``NOT_FOUND``，背包满 → ``OUT_OF_RANGE``。"""
        character = state.characters.get(ctx.actor_id)
        if character is None:
            return Result.fail(ErrorCode.NOT_FOUND, f"角色不存在: {ctx.actor_id}")
        room = state.rooms.get(character.location)
        if room is None:
            return Result.fail(ErrorCode.NOT_FOUND, f"角色所在地点不存在: {character.location}")
        item = state.items.get(item_id)
        if item is None or item_id not in room.item_ids:
            return Result.fail(ErrorCode.NOT_FOUND, "这里没有这件物品")
        if len(character.inventory) >= _INVENTORY_LIMIT:
            return Result.fail(ErrorCode.OUT_OF_RANGE, "背包已满，无法再拿起物品")
        inventory_size = len(character.inventory) + 1
        draft = Event(
            turn_id=ctx.turn_id,
            actor_id=ctx.actor_id,
            method="take_item",
            event_type=EventType.ITEM_TAKEN,
            params={"item_id": item_id, "from_room": room.id},
            result={"inventory_size": inventory_size},
        )
        return Result.success(
            data={"item_id": item_id, "item_name": item.name, "inventory_size": inventory_size},
            events=[draft],
        )

    def use_item(self, ctx: ToolContext, state: GameState, *, item_id: ItemId) -> Result:
        """使用背包内物品；不在背包 → ``NOT_FOUND``，剧情/任务物品 → ``INVALID_INPUT``。

        剧情/任务物品禁止被「使用」销毁（R1 实测：古物曾可被 use 永久消耗）：
        前置拒绝且零事件、背包不变；普通消耗品的既有行为与事件结构完全不变。
        """
        character = state.characters.get(ctx.actor_id)
        if character is None:
            return Result.fail(ErrorCode.NOT_FOUND, f"角色不存在: {ctx.actor_id}")
        item = state.items.get(item_id)
        if item is None or item_id not in character.inventory:
            return Result.fail(ErrorCode.NOT_FOUND, "背包里没有这件物品")
        if _is_story_item(item.tags):
            return Result.fail(ErrorCode.INVALID_INPUT, "这件东西不能这样用")
        draft = Event(
            turn_id=ctx.turn_id,
            actor_id=ctx.actor_id,
            method="use_item",
            event_type=EventType.ITEM_CONSUMED,
            params={"item_id": item_id},
            result={"consumed": True},
        )
        return Result.success(
            data={"item_id": item_id, "item_name": item.name},
            events=[draft],
        )

    def talk_to(self, ctx: ToolContext, state: GameState, *, npc_id: NpcId, topic: str) -> Result:
        """与同房间 NPC 对话；NPC 不存在或不同房间 → ``NOT_FOUND``。"""
        character = state.characters.get(ctx.actor_id)
        if character is None:
            return Result.fail(ErrorCode.NOT_FOUND, f"角色不存在: {ctx.actor_id}")
        npc = state.npcs.get(npc_id)
        if npc is None or npc.room_id != character.location:
            return Result.fail(ErrorCode.NOT_FOUND, "这里没有可以交谈的人")
        draft = Event(
            turn_id=ctx.turn_id,
            actor_id=ctx.actor_id,
            method="talk_to",
            event_type=EventType.DIALOGUE_EXCHANGED,
            params={"npc_id": npc_id, "topic": topic},
            result={"dialogue_state": "greeted"},
        )
        return Result.success(
            data={"npc_id": npc_id, "npc_name": npc.name, "dialogue_state": "greeted"},
            events=[draft],
        )


__all__ = ["ActionService", "_room_text"]
