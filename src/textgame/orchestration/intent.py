"""确定性关键词解析 (契约 §3.5；005 初版，006 的 LLM 兜底路径复用)。

不调模型、不使用随机：中文关键词 + 感知切片实体名子串匹配，命中即 ``confidence=1.0``、
``source="keyword"``；无法映射返回 ``tool=None, confidence=0.0``，由编排层转
``INVALID_INPUT``（零状态写入，第 9 章步骤 3/5 的确定性兜底）。
"""

from __future__ import annotations

from typing import Any

from textgame.agents.dto import ParseOutcome
from textgame.domain.models import ActionIntent, EntityRef, PerceptionSlice
from textgame.domain.scenario import DIRECTION_KEYWORDS
from textgame.services.tool_context import ToolContext

_LOOK_WORDS: tuple[str, ...] = ("环顾", "四周", "查看", "观察", "看")
_USE_WORDS: tuple[str, ...] = ("使用", "服用", "喝", "吃", "用")
_TAKE_WORDS: tuple[str, ...] = ("拿起", "拿", "捡", "拾", "取")
_MOVE_WORDS: tuple[str, ...] = ("走", "去", "前往", "进", "向")
_TALK_WORDS: tuple[str, ...] = ("说", "问", "聊", "喊", "交谈")

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
    """关键词解析器；M2 的默认解析器，006 作为 ``IntentParseAgent`` 的兜底复用。"""

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

        if _contains_any(normalized, _LOOK_WORDS):
            # 读工具名用第 7 章 §7.3 的规范名 ``get_room_view``（``look`` 只是服务方法名）。
            return _outcome("get_room_view", {})
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
