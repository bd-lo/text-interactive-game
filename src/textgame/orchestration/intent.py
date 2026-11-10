"""确定性关键词解析 (契约 §3.5；005 初版，006 的 LLM 兜底路径复用)。

不调模型、不使用随机：中文关键词 + 感知切片实体名子串匹配，命中即 ``confidence=1.0``、
``source="keyword"``；无法映射返回 ``tool=None, confidence=0.0``，由编排层转
``INVALID_INPUT``（零状态写入，第 9 章步骤 3/5 的确定性兜底）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from textgame.agents.dto import ParseOutcome
from textgame.domain.models import ActionIntent, EntityRef, PerceptionSlice
from textgame.domain.scenario import DIRECTION_KEYWORDS
from textgame.services.tool_context import ToolContext

_LOOK_WORDS: tuple[str, ...] = ("环顾", "四周", "查看", "观察", "看")
_INVENTORY_WORDS: tuple[str, ...] = ("背包", "行囊", "物品栏", "身上", "带了", "装备")
_USE_WORDS: tuple[str, ...] = ("使用", "服用", "喝", "吃", "用")
_TAKE_WORDS: tuple[str, ...] = ("拿起", "拿", "捡", "拾", "取")
_MOVE_WORDS: tuple[str, ...] = ("走", "去", "前往", "进", "向")
_TALK_WORDS: tuple[str, ...] = ("说", "问", "聊", "喊", "交谈")
_CRAFT_WORDS: tuple[str, ...] = ("打造", "锻造", "制作", "制造", "合成", "做个")

_UNKNOWN = ParseOutcome(confidence=0.0, source="keyword")


def _contains_any(text: str, words: tuple[str, ...]) -> bool:
    """任一关键词出现在文本中即视为命中。"""
    return any(word in text for word in words)


def _match_entity(text: str, entities: list[EntityRef]) -> EntityRef | None:
    """按名称子串匹配感知切片实体；长名优先，避免「火把」误配到更短实体。"""
    for entity in sorted(entities, key=lambda item: len(item.name), reverse=True):
        if entity.name and entity.name in text:
            return entity
    return None


def _match_direction(text: str) -> str | None:
    """匹配中文方向词（契约 §3.4：北/南/东/西/上/下）。"""
    for keyword, direction in DIRECTION_KEYWORDS.items():
        if keyword in text:
            return direction
    return None


def _outcome(tool: str, args: dict[str, Any]) -> ParseOutcome:
    """构造命中结果（``confidence=1.0``，附 ``ActionIntent`` 供审计/回放）。"""
    target = args.get("item_id") or args.get("npc_id") or args.get("direction")
    intent = ActionIntent(
        action=tool,
        target=target if isinstance(target, str) else None,
        params=args,
        confidence=1.0,
    )
    return ParseOutcome(
        intent=intent,
        tool=tool,
        args=args,
        confidence=1.0,
        source="keyword",
    )


class DeterministicIntentParser:
    """关键词解析器；M2 的默认解析器，006 作为 ``IntentParseAgent`` 的兜底复用。

    ``recipe_names`` 为可选「配方中文名 → recipe_id」映射（组合根从 RecipeCatalog 注入）：
    提供时，制造关键词且命中配方名才产出 ``craft_item``（M4-016）；缺省 None 时行为与
    M2 完全一致（不产生任何 craft 意图，旧测试/回放不回归）。

    ``free_craft_enabled``（M5 出口接线，P2-4(a)）：制造关键词未命中任何配方名时，只有
    显式开启（组合根传 ``Settings.free_craft_enabled``，默认 **False** = fail-closed）才
    产出 ``craft_item(mode="free")`` 自由制造意图；关闭时行为与 M2–M4 完全一致。
    """

    def __init__(
        self,
        recipe_names: Mapping[str, str] | None = None,
        *,
        free_craft_enabled: bool = False,
    ) -> None:
        """注入可选配方名索引与自由制造开关（默认均关闭 = M2 行为）。"""
        self._recipe_names: dict[str, str] | None = (
            dict(recipe_names) if recipe_names is not None else None
        )
        self._free_craft_enabled = free_craft_enabled

    def parse(
        self,
        text: str,
        *,
        perception: PerceptionSlice,
        ctx: ToolContext | None = None,
    ) -> ParseOutcome:
        """解析一句玩家输入；无法映射时返回 ``tool=None, confidence=0.0``。

        ``ctx`` 只为与 ``IntentParseAgent`` 保持同一端口签名（确定性解析不需要回合上下文）。
        """
        normalized = text.strip()
        if not normalized:
            return _UNKNOWN
        entities = perception.visible_entities
        items = [entity for entity in entities if entity.kind == "item"]
        npcs = [entity for entity in entities if entity.kind == "npc"]
        item = _match_entity(normalized, items)
        npc = _match_entity(normalized, npcs)

        if _contains_any(normalized, _INVENTORY_WORDS):
            # 背包/行囊查询必须优先于 ``_LOOK_WORDS``：修「查看背包」被 ``查看`` 抢占为
            # 房间查看（M6 状态面板/终端客户端的可观察状态入口）。
            return _outcome("get_inventory", {})
        if _contains_any(normalized, _LOOK_WORDS):
            # 读工具名用第 7 章 §7.3 的规范名 ``get_room_view``（``look`` 只是服务方法名）。
            return _outcome("get_room_view", {})
        if _contains_any(normalized, _CRAFT_WORDS):
            if self._recipe_names is not None:
                matched = next(
                    (
                        recipe_id
                        for name, recipe_id in sorted(
                            self._recipe_names.items(),
                            key=lambda item: len(item[0]),
                            reverse=True,
                        )
                        if name and name in normalized
                    ),
                    None,
                )
                if matched is not None:
                    return _outcome("craft_item", {"mode": "recipe", "recipe_id": matched})
            if self._free_craft_enabled:
                # P2-4(a)：自由制造自由句分支（fail-closed：仅开关显式开启时可达）。
                return _outcome(
                    "craft_item",
                    {"mode": "free", "free_description": normalized[:200]},
                )
        direction = _match_direction(normalized)
        if direction is not None and _contains_any(normalized, _MOVE_WORDS):
            return _outcome("move_to", {"direction": direction})
        if item is not None and _contains_any(normalized, _USE_WORDS):
            return _outcome("use_item", {"item_id": item.id})
        if item is not None and _contains_any(normalized, _TAKE_WORDS):
            return _outcome("take_item", {"item_id": item.id})
        if npc is not None and _contains_any(normalized, _TALK_WORDS):
            return _outcome("talk_to", {"npc_id": npc.id, "topic": normalized})
        return _UNKNOWN


__all__ = ["DeterministicIntentParser"]
