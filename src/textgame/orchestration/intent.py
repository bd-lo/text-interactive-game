"""确定性关键词解析 (契约 §3.5；005 初版，006 的 LLM 兜底路径复用)。

不调模型、不使用随机：中文关键词 + 感知切片实体名匹配。命中即 ``confidence=1.0``、
``source="keyword"``；无法映射返回 ``tool=None, confidence=0.0``，由编排层转
``INVALID_INPUT``（零状态写入，第 9 章步骤 3/5 的确定性兜底）。

R2 离线 NLU 增强（D6/D7，**不改变 7 条 golden 输入的原映射**）：

- 词表扩展：看/细看/检查/端详/瞅/附近（查看）、手里/口袋/携带（背包）、饮/吃/服用（使用）、
  抓/收起/装进（拿取）、朝/往/回/上楼/下楼（移动）、打听/告诉/招呼/搭话（对话）、炼制/铸造（制造）；
- ``inspect_item``：查看类词命中 item/npc 实体时细看该实体（优先级高于整间房 ``get_room_view``；
  背包词优先级仍最高）；
- 实体别名：全名子串 → 名称片段（≥2 字，如「药水」→「治疗药水」、「哨兵」→「老哨兵」）→
  ``difflib.SequenceMatcher`` 保守模糊（cutoff 0.6，仅在动词命中且无精确/别名命中时启用）；
- 房间名导航：``PerceptionSlice.exit_refs``（R2 加法字段；``id``=方向英文值、``name``=目标房间名）
  按「地窖∈哨站地窖」「铁匠铺∈废弃铁匠铺」「入口∈废弃哨站·入口」把地名映射为出口方向。
"""

from __future__ import annotations

from collections.abc import Mapping
from difflib import SequenceMatcher
from typing import Any

from textgame.agents.dto import ParseOutcome
from textgame.domain.models import ActionIntent, EntityRef, PerceptionSlice
from textgame.domain.scenario import DIRECTION_ALIASES, DIRECTION_KEYWORDS, DIRECTION_NAMES
from textgame.services.free_craft import FreeCraftSwitch
from textgame.services.tool_context import ToolContext

#: 查看类词（整间房 / 细看实体的共同入口）。
_LOOK_WORDS: tuple[str, ...] = (
    "环顾",
    "四周",
    "查看",
    "观察",
    "看",
    "瞅",
    "瞧",
    "端详",
    "检查",
    "打量",
    "细看",
    "附近",
    "周围",
)
#: 背包类词（优先级最高：``查看背包`` 必须先于 ``查看`` 命中）。
_INVENTORY_WORDS: tuple[str, ...] = (
    "背包",
    "行囊",
    "物品栏",
    "身上",
    "带了",
    "带的",
    "装备",
    "手里",
    "口袋",
    "携带",
)
#: 使用类词。
_USE_WORDS: tuple[str, ...] = (
    "使用",
    "服用",
    "喝",
    "吃",
    "用",
    "饮",
    "吞",
    "服下",
    "灌",
)
#: 拿取类词。
_TAKE_WORDS: tuple[str, ...] = (
    "拿起",
    "拿",
    "捡",
    "拾",
    "取",
    "抓",
    "收起",
    "收进",
    "装进",
    "放入",
    "揣",
)
#: 移动类词（含方向变体与上下楼固定说法；纯「上/下」不入表，避免误命中其他句子）。
_MOVE_WORDS: tuple[str, ...] = (
    "走",
    "去",
    "前往",
    "进",
    "向",
    "朝",
    "往",
    "回",
    "爬",
    "登",
    "上楼",
    "下楼",
    "上去",
    "下去",
    "上塔",
    "下塔",
)
#: 对话类词。
_TALK_WORDS: tuple[str, ...] = (
    "说",
    "问",
    "聊",
    "喊",
    "交谈",
    "打听",
    "告诉",
    "招呼",
    "搭话",
    "对话",
    "问候",
    "问好",
    "汇报",
    "你好",
)
#: 制造类词。
_CRAFT_WORDS: tuple[str, ...] = (
    "打造",
    "锻造",
    "制作",
    "制造",
    "合成",
    "做个",
    "铸造",
    "炼制",
    "加工",
)
#: 可被「细看」的实体种类（房间细看仍走 ``get_room_view``）。
_INSPECT_KINDS: frozenset[str] = frozenset({"item", "npc"})
#: 实体别名片段的最短长度（过短会误配，如单字「剑」）。
_ALIAS_MIN_CHARS = 2
#: difflib 模糊匹配阈值（仅在动词命中且无精确/别名命中时启用）。
_FUZZY_CUTOFF = 0.6
#: 合法出口方向值（``PerceptionSlice.exit_refs[].id`` 口径；含 in/out，R-17）。
_DIRECTION_VALUES: frozenset[str] = frozenset(DIRECTION_NAMES)
#: 纯方向短语允许出现的字符（「北边」「北方」「北面」「朝北」「里面」「出去」等；不含其他动作词）。
_DIRECTION_PHRASE_CHARS: frozenset[str] = frozenset("北南东西上下里外进出内边方面朝向往楼道回走上")

_UNKNOWN = ParseOutcome(confidence=0.0, source="keyword")


def _contains_any(text: str, words: tuple[str, ...]) -> bool:
    """任一关键词出现在文本中即视为命中。"""
    return any(word in text for word in words)


def _name_fragments(name: str) -> list[str]:
    """实体名可匹配片段（长片段优先）：按「·」分段 + 全部 ≥2 字连续子串。

    例：``治疗药水`` → ``治疗药水/治疗药/疗药水/治疗/疗药/药水``；``废弃哨站·入口`` →
    ``废弃哨站/入口`` 等。用于「药水」「哨兵」「地窖」「铁匠铺」「入口」这类日常简称。
    """
    fragments: list[str] = []
    seen: set[str] = set()

    def add(fragment: str) -> None:
        if len(fragment) >= _ALIAS_MIN_CHARS and fragment not in seen:
            seen.add(fragment)
            fragments.append(fragment)

    for part in name.split("·"):
        add(part)
    for start in range(len(name) - _ALIAS_MIN_CHARS + 1):
        for end in range(start + _ALIAS_MIN_CHARS, len(name) + 1):
            add(name[start:end])
    fragments.sort(key=len, reverse=True)
    return fragments


def _match_entity_exact(text: str, entities: list[EntityRef]) -> EntityRef | None:
    """按名称全名子串匹配；长名优先，避免「火把」误配到更短实体（既有行为）。"""
    for entity in sorted(entities, key=lambda item: len(item.name), reverse=True):
        if entity.name and entity.name in text:
            return entity
    return None


def _match_entity_alias(text: str, entities: list[EntityRef]) -> EntityRef | None:
    """按名称片段别名匹配（≥2 字）；片段越长越优先，同长按实体名长排序（确定性）。"""
    best: tuple[int, int, EntityRef] | None = None
    for entity in sorted(entities, key=lambda item: len(item.name), reverse=True):
        if not entity.name:
            continue
        for fragment in _name_fragments(entity.name):
            if fragment in text:
                candidate = (len(fragment), len(entity.name), entity)
                if best is None or candidate[:2] > best[:2]:
                    best = candidate
                break
    return None if best is None else best[2]


def _match_entity(text: str, entities: list[EntityRef]) -> EntityRef | None:
    """实体匹配：全名子串（原行为）→ 名称片段别名（R2 加法）。"""
    exact = _match_entity_exact(text, entities)
    if exact is not None:
        return exact
    return _match_entity_alias(text, entities)


def _fuzzy_ratio(name: str, text: str) -> float:
    """实体名与文本滑动窗口的最大相似度（窗口长度 = 名字长 ±1；确定性）。"""
    best = 0.0
    for length in (max(_ALIAS_MIN_CHARS, len(name) - 1), len(name), len(name) + 1):
        last_start = len(text) - length
        for start in range(last_start + 1):
            ratio = SequenceMatcher(None, name, text[start : start + length]).ratio()
            if ratio > best:
                best = ratio
    return best


def _match_entity_fuzzy(text: str, entities: list[EntityRef]) -> EntityRef | None:
    """difflib 保守模糊匹配：仅在无精确/别名命中时兜底，低于阈值返回 ``None``。"""
    best_entity: EntityRef | None = None
    best_ratio = _FUZZY_CUTOFF
    for entity in sorted(entities, key=lambda item: len(item.name), reverse=True):
        if not entity.name:
            continue
        ratio = _fuzzy_ratio(entity.name, text)
        if ratio >= best_ratio and (best_entity is None or ratio > best_ratio):
            best_entity = entity
            best_ratio = ratio
    return best_entity


def _match_direction(text: str) -> str | None:
    """匹配中文方向词（北/南/东/西/上/下 + in/out 安全别名；R-17）。"""
    for keyword, direction in DIRECTION_KEYWORDS.items():
        if keyword in text:
            return direction
    for alias, direction in DIRECTION_ALIASES.items():
        if alias in text:
            return direction
    return None


def _is_bare_direction_phrase(text: str, direction: str) -> bool:
    """纯方向短语判定（如「北边」「北方」「上面」）：只含方向词/方位后缀，不含其他动作。

    用途：``北边`` 这类省略了「走/去」的日常说法也应映射为移动，但不让
    「向北走进地窖」以外的长句（含物品名等）误判。
    """
    stripped = text.strip("，。！？、；：… \t")
    if not stripped or len(stripped) > 4:
        return False
    keyword = next((key for key, value in DIRECTION_KEYWORDS.items() if value == direction), None)
    if keyword is None:
        keyword = next(
            (alias for alias, value in DIRECTION_ALIASES.items() if value == direction), None
        )
    if keyword is None or keyword not in stripped:
        return False
    return all(char in _DIRECTION_PHRASE_CHARS for char in stripped)


def _match_exit_direction(text: str, exit_refs: list[EntityRef]) -> str | None:
    """房间名导航：``exit_refs`` 目标房间展示名的片段 → 出口方向（最长片段优先）。

    ``exit_refs`` 约定：``kind="room"``、``id``=方向英文值、``name``=目标房间展示名；
    非法/未知方向条目跳过（防御旧装配或未来字段扩展）。
    """
    best_direction: str | None = None
    best_length = 0
    for ref in exit_refs:
        if ref.kind != "room" or ref.id not in _DIRECTION_VALUES or not ref.name:
            continue
        for fragment in _name_fragments(ref.name):
            if fragment in text and len(fragment) > best_length:
                best_direction = ref.id
                best_length = len(fragment)
    return best_direction


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
        free_craft_switch: FreeCraftSwitch | None = None,
    ) -> None:
        """注入可选配方名索引与自由制造开关（默认均关闭 = M2 行为）。

        ``free_craft_switch`` 存在时优先使用共享运行期开关（网页设置页热切换）；
        否则用 ``free_craft_enabled`` 创建内部开关（保持旧测试/装配兼容）。
        """
        self._recipe_names: dict[str, str] | None = (
            dict(recipe_names) if recipe_names is not None else None
        )
        self._free_craft_switch = free_craft_switch or FreeCraftSwitch(enabled=free_craft_enabled)

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
            # 查看类词 + 命中 item/npc → 细看该实体（D6）；无实体目标 → 整间房视图。
            target = item if item is not None else npc
            if target is None:
                target = _match_entity_fuzzy(normalized, items) or _match_entity_fuzzy(
                    normalized, npcs
                )
            if target is not None and target.kind in _INSPECT_KINDS:
                key = "item_id" if target.kind == "item" else "npc_id"
                return _outcome("inspect_item", {key: target.id})
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
            if self._free_craft_switch.enabled:
                # P2-4(a)：自由制造自由句分支（fail-closed：仅开关显式开启时可达）。
                return _outcome(
                    "craft_item",
                    {"mode": "free", "free_description": normalized[:200]},
                )
        direction = _match_direction(normalized)
        if direction is not None and (
            _contains_any(normalized, _MOVE_WORDS)
            or _is_bare_direction_phrase(normalized, direction)
        ):
            return _outcome("move_to", {"direction": direction})
        if _contains_any(normalized, _MOVE_WORDS):
            # 房间名导航（R2 加法）：把「去地窖/进哨站/回入口/去铁匠铺」映射为出口方向。
            exit_direction = _match_exit_direction(normalized, perception.exit_refs)
            if exit_direction is not None:
                return _outcome("move_to", {"direction": exit_direction})
        if item is None and _contains_any(normalized, _USE_WORDS + _TAKE_WORDS):
            item = _match_entity_fuzzy(normalized, items)
        if item is not None and _contains_any(normalized, _USE_WORDS):
            return _outcome("use_item", {"item_id": item.id})
        if item is not None and _contains_any(normalized, _TAKE_WORDS):
            return _outcome("take_item", {"item_id": item.id})
        if npc is None and _contains_any(normalized, _TALK_WORDS):
            npc = _match_entity_fuzzy(normalized, npcs)
        if npc is not None and _contains_any(normalized, _TALK_WORDS):
            return _outcome("talk_to", {"npc_id": npc.id, "topic": normalized})
        return _UNKNOWN


__all__ = ["DeterministicIntentParser"]
