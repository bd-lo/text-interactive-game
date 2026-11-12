"""R2 离线 NLU 映射矩阵（``orchestration/intent.py``）：≥40 条玩家输入 + 7 条 golden 逐字断言。

覆盖（D6/D7）：
- 7 条 m2 golden 输入映射逐字不变（tool/args/confidence/source 精确断言）；
- 细看 ``inspect_item``（item/npc + 别名 + 模糊）、背包同义词、方向变体、
  房间名导航（``PerceptionSlice.exit_refs``）、使用/拿取/对话的日常说法；
- 反例：无关输入/能力缺口不得乱猜（``tool=None, confidence=0.0``）；R7 战斗词表命中 npc → attack；
- 模糊匹配（difflib cutoff 0.6）只在动词命中且无精确/别名命中时兜底（防御性单测）。
"""

from __future__ import annotations

from typing import Any

import pytest

from textgame.domain.models import EntityRef, PerceptionSlice
from textgame.domain.scenario import (
    ITEM_POTION,
    ITEM_POTION_NAME,
    ITEM_RELIC,
    ITEM_RELIC_NAME,
    ITEM_SWORD,
    ITEM_SWORD_NAME,
    ITEM_TORCH,
    ITEM_TORCH_NAME,
    M4_ITEM_COPPER_WIRE,
    M4_ITEM_COPPER_WIRE_NAME,
    M4_ITEM_IRON_INGOT,
    M4_ITEM_IRON_INGOT_NAME,
    M4_ROOM_FORGE_NAME,
    NPC_SENTINEL,
    NPC_SENTINEL_NAME,
    PLAYER_ID,
    ROOM_CELLAR,
    ROOM_CELLAR_NAME,
    ROOM_ENTRY,
    ROOM_ENTRY_NAME,
)
from textgame.orchestration.intent import (
    DeterministicIntentParser,
    _match_entity_alias,
    _match_entity_fuzzy,
)

_PARSER = DeterministicIntentParser()
_PARSER_WITH_RECIPES = DeterministicIntentParser(
    recipe_names={"铁剑": "recipe.forge_sword", "信号火把": "recipe.signal_torch"}
)

_GOLDEN_7: tuple[tuple[str, str, dict[str, Any]], ...] = (
    ("环顾四周", "get_room_view", {}),
    ("对老哨兵问好", "talk_to", {"npc_id": NPC_SENTINEL, "topic": "对老哨兵问好"}),
    ("拿起火把", "take_item", {"item_id": ITEM_TORCH}),
    ("拿起治疗药水", "take_item", {"item_id": ITEM_POTION}),
    ("使用治疗药水", "use_item", {"item_id": ITEM_POTION}),
    ("向北走进地窖", "move_to", {"direction": "north"}),
    ("拿起锈剑", "take_item", {"item_id": ITEM_SWORD}),
)


def _ref(kind: str, entity_id: str, name: str) -> EntityRef:
    """构造感知切片实体引用（与 ``QueryService.build_perception_slice`` 同构）。"""
    return EntityRef(kind=kind, id=entity_id, name=name)


def _perception(
    *,
    room_id: str = ROOM_ENTRY,
    room_name: str = ROOM_ENTRY_NAME,
    items: tuple[tuple[str, str], ...] = (),
    npcs: tuple[tuple[str, str], ...] = (),
    exit_refs: tuple[EntityRef, ...] = (),
) -> PerceptionSlice:
    """按显式参数构造玩家感知切片（房间 + 可见物品/NPC + 出口名）。"""
    entities: list[EntityRef] = [_ref("room", room_id, room_name)]
    entities.extend(_ref("item", item_id, name) for item_id, name in items)
    entities.extend(_ref("npc", npc_id, name) for npc_id, name in npcs)
    return PerceptionSlice(actor_id=PLAYER_ID, visible_entities=entities, exit_refs=list(exit_refs))


def _entry() -> PerceptionSlice:
    """m2 入口（golden 感知：火把/治疗药水/锈剑/老哨兵；与 test_intent_inventory 同口径）。"""
    return _perception(
        items=(
            (ITEM_TORCH, ITEM_TORCH_NAME),
            (ITEM_POTION, ITEM_POTION_NAME),
            (ITEM_SWORD, ITEM_SWORD_NAME),
        ),
        npcs=((NPC_SENTINEL, NPC_SENTINEL_NAME),),
    )


def _entry_with_exits(
    *extra: EntityRef,
) -> PerceptionSlice:
    """m2 入口 + 北出口（指向哨站地窖），可追加更多出口引用。"""
    return _perception(
        items=(
            (ITEM_TORCH, ITEM_TORCH_NAME),
            (ITEM_POTION, ITEM_POTION_NAME),
            (ITEM_SWORD, ITEM_SWORD_NAME),
        ),
        npcs=((NPC_SENTINEL, NPC_SENTINEL_NAME),),
        exit_refs=(_ref("room", "north", ROOM_CELLAR_NAME), *extra),
    )


def _cellar() -> PerceptionSlice:
    """m2 地窖（锈剑/古物；南出口回入口）。"""
    return _perception(
        room_id=ROOM_CELLAR,
        room_name=ROOM_CELLAR_NAME,
        items=((ITEM_SWORD, ITEM_SWORD_NAME), (ITEM_RELIC, ITEM_RELIC_NAME)),
        exit_refs=(_ref("room", "south", ROOM_ENTRY_NAME),),
    )


def _m4_entry() -> PerceptionSlice:
    """m4 入口（北→地窖、东→铁匠铺；背包材料的可见实体）。"""
    return _perception(
        items=(
            (ITEM_TORCH, ITEM_TORCH_NAME),
            (M4_ITEM_IRON_INGOT, M4_ITEM_IRON_INGOT_NAME),
            (M4_ITEM_COPPER_WIRE, M4_ITEM_COPPER_WIRE_NAME),
        ),
        npcs=((NPC_SENTINEL, NPC_SENTINEL_NAME),),
        exit_refs=(
            _ref("room", "north", ROOM_CELLAR_NAME),
            _ref("room", "east", M4_ROOM_FORGE_NAME),
        ),
    )


def _empty() -> PerceptionSlice:
    """空感知（无实体/无出口）：验证「不返回乱猜」。"""
    return PerceptionSlice(actor_id=PLAYER_ID)


_PERCEPTIONS: dict[str, Any] = {
    "entry": _entry,
    "entry_north": _entry_with_exits,
    "cellar": _cellar,
    "m4": _m4_entry,
    "empty": _empty,
}

#: (感知 key, 输入, 期望 tool, 期望 args, 期望 source)
_CASES: tuple[tuple[str, str, str | None, dict[str, Any], str], ...] = (
    # ---- 7 条 m2 golden（逐字不变）----
    ("entry", "环顾四周", "get_room_view", {}, "keyword"),
    (
        "entry",
        "对老哨兵问好",
        "talk_to",
        {"npc_id": NPC_SENTINEL, "topic": "对老哨兵问好"},
        "keyword",
    ),
    ("entry", "拿起火把", "take_item", {"item_id": ITEM_TORCH}, "keyword"),
    ("entry", "拿起治疗药水", "take_item", {"item_id": ITEM_POTION}, "keyword"),
    ("entry", "使用治疗药水", "use_item", {"item_id": ITEM_POTION}, "keyword"),
    ("entry", "向北走进地窖", "move_to", {"direction": "north"}, "keyword"),
    ("entry", "拿起锈剑", "take_item", {"item_id": ITEM_SWORD}, "keyword"),
    # ---- inspect 意图（细看实体优先于整间房）----
    ("entry", "查看治疗药水", "inspect_item", {"item_id": ITEM_POTION}, "keyword"),
    ("entry", "端详火把", "inspect_item", {"item_id": ITEM_TORCH}, "keyword"),
    ("entry", "瞅瞅火把", "inspect_item", {"item_id": ITEM_TORCH}, "keyword"),
    ("entry", "检查老哨兵", "inspect_item", {"npc_id": NPC_SENTINEL}, "keyword"),
    ("entry", "细看治疗药水", "inspect_item", {"item_id": ITEM_POTION}, "keyword"),
    ("cellar", "查看古物", "inspect_item", {"item_id": ITEM_RELIC}, "keyword"),
    # ---- 查看整间房（无实体目标，保持原行为）----
    ("entry", "看看四周", "get_room_view", {}, "keyword"),
    ("entry", "查看四周", "get_room_view", {}, "keyword"),
    ("entry", "观察一下周围", "get_room_view", {}, "keyword"),
    ("entry", "附近有什么", "get_room_view", {}, "keyword"),
    ("cellar", "打量一下地窖", "get_room_view", {}, "keyword"),
    # ---- 背包同义词（优先级最高）----
    ("entry", "看看背包", "get_inventory", {}, "keyword"),
    ("entry", "查看背包", "get_inventory", {}, "keyword"),
    ("entry", "我手里有什么", "get_inventory", {}, "keyword"),
    ("entry", "身上有什么", "get_inventory", {}, "keyword"),
    ("entry", "带的东西", "get_inventory", {}, "keyword"),
    ("entry", "打开物品栏", "get_inventory", {}, "keyword"),
    ("entry", "我的装备", "get_inventory", {}, "keyword"),
    ("entry", "携带了什么", "get_inventory", {}, "keyword"),
    # ---- 移动：方向变体 ----
    ("entry_north", "北边", "move_to", {"direction": "north"}, "keyword"),
    ("entry_north", "北方", "move_to", {"direction": "north"}, "keyword"),
    ("entry_north", "朝北走", "move_to", {"direction": "north"}, "keyword"),
    ("entry_north", "往北", "move_to", {"direction": "north"}, "keyword"),
    ("entry_north", "走北", "move_to", {"direction": "north"}, "keyword"),
    ("entry_north", "往南走", "move_to", {"direction": "south"}, "keyword"),
    ("entry_north", "下楼", "move_to", {"direction": "down"}, "keyword"),
    # ---- 移动：房间名导航（exit_refs）----
    ("entry_north", "去地窖", "move_to", {"direction": "north"}, "keyword"),
    ("entry_north", "进哨站", "move_to", {"direction": "north"}, "keyword"),
    ("entry_north", "走进地窖", "move_to", {"direction": "north"}, "keyword"),
    ("cellar", "回入口", "move_to", {"direction": "south"}, "keyword"),
    ("cellar", "去入口", "move_to", {"direction": "south"}, "keyword"),
    ("m4", "去铁匠铺", "move_to", {"direction": "east"}, "keyword"),
    ("m4", "去地窖", "move_to", {"direction": "north"}, "keyword"),
    ("m4", "回入口", None, {}, "keyword"),  # m4 出口名不含「入口」→ 不猜方向
    # ---- 使用：日常说法 + 别名 ----
    ("entry", "喝下药水", "use_item", {"item_id": ITEM_POTION}, "keyword"),
    ("entry", "把这瓶药水喝了", "use_item", {"item_id": ITEM_POTION}, "keyword"),
    ("entry", "把药水喝掉", "use_item", {"item_id": ITEM_POTION}, "keyword"),
    ("entry", "服用治疗药水", "use_item", {"item_id": ITEM_POTION}, "keyword"),
    ("entry", "饮下治疗药水", "use_item", {"item_id": ITEM_POTION}, "keyword"),
    ("entry", "喝掉火把", "use_item", {"item_id": ITEM_TORCH}, "keyword"),
    # ---- 拿取：日常说法 + 别名 ----
    ("entry", "把地上的火把捡起来", "take_item", {"item_id": ITEM_TORCH}, "keyword"),
    ("entry", "拾起药水", "take_item", {"item_id": ITEM_POTION}, "keyword"),
    ("entry", "捡起治疗药水", "take_item", {"item_id": ITEM_POTION}, "keyword"),
    ("entry", "抓起治疗药水", "take_item", {"item_id": ITEM_POTION}, "keyword"),
    ("cellar", "抓起锈剑", "take_item", {"item_id": ITEM_SWORD}, "keyword"),
    ("cellar", "收起古物", "take_item", {"item_id": ITEM_RELIC}, "keyword"),
    # ---- 对话：日常说法 + 别名 ----
    (
        "entry",
        "跟老哨兵说话",
        "talk_to",
        {"npc_id": NPC_SENTINEL, "topic": "跟老哨兵说话"},
        "keyword",
    ),
    (
        "entry",
        "和老哨兵聊聊",
        "talk_to",
        {"npc_id": NPC_SENTINEL, "topic": "和老哨兵聊聊"},
        "keyword",
    ),
    ("entry", "询问老哨兵", "talk_to", {"npc_id": NPC_SENTINEL, "topic": "询问老哨兵"}, "keyword"),
    (
        "entry",
        "向老哨兵打听地窖",
        "talk_to",
        {"npc_id": NPC_SENTINEL, "topic": "向老哨兵打听地窖"},
        "keyword",
    ),
    (
        "entry",
        "对老哨兵打个招呼",
        "talk_to",
        {"npc_id": NPC_SENTINEL, "topic": "对老哨兵打个招呼"},
        "keyword",
    ),
    (
        "entry",
        "老哨兵你好呀",
        "talk_to",
        {"npc_id": NPC_SENTINEL, "topic": "老哨兵你好呀"},
        "keyword",
    ),
    ("entry", "跟哨兵说话", "talk_to", {"npc_id": NPC_SENTINEL, "topic": "跟哨兵说话"}, "keyword"),
    # ---- m4 制造（配方名索引提供时）----
    (
        "m4",
        "锻造铁剑",
        "craft_item",
        {"mode": "recipe", "recipe_id": "recipe.forge_sword"},
        "keyword",
    ),
    (
        "m4",
        "打造铁剑",
        "craft_item",
        {"mode": "recipe", "recipe_id": "recipe.forge_sword"},
        "keyword",
    ),
    # ---- R7 战斗词表（attack 工具加入后：命中 npc 不再「乱猜」，而是明确攻击意图）----
    ("entry", "攻击老哨兵", "attack", {"npc_id": NPC_SENTINEL}, "keyword"),
    ("entry", "砍老哨兵", "attack", {"npc_id": NPC_SENTINEL}, "keyword"),
    ("entry", "拔剑砍向老哨兵", "attack", {"npc_id": NPC_SENTINEL}, "keyword"),
    # ---- 反例：无关/能力缺口不得乱猜 ----
    ("entry", "跳舞", None, {}, "keyword"),
    ("entry", "唱歌", None, {}, "keyword"),
    ("entry", "asdfgh", None, {}, "keyword"),
    ("entry", "我原地转三圈", None, {}, "keyword"),
    ("entry", "发呆", None, {}, "keyword"),
    ("entry", "你好", None, {}, "keyword"),
    ("entry", "随便聊聊", None, {}, "keyword"),
    ("entry", "唱一首歌吧", None, {}, "keyword"),
    ("entry", "帮助", None, {}, "keyword"),
    ("entry", "我的状态", None, {}, "keyword"),
    ("entry", "打他", None, {}, "keyword"),
    ("empty", "去地窖", None, {}, "keyword"),
    ("empty", "查看治疗药水", "get_room_view", {}, "keyword"),
)


def _parse(
    text: str, *, perception: PerceptionSlice, parser: DeterministicIntentParser = _PARSER
) -> Any:
    """用固定解析器 + 感知切片解析一句输入。"""
    return parser.parse(text, perception=perception)


def test_matrix_size_is_at_least_40() -> None:
    """映射矩阵规模下限：任务要求 ≥40 条真实玩家输入（含正例/反例/golden）。"""
    assert len(_CASES) >= 40


@pytest.mark.parametrize(
    ("perception_key", "text", "expected_tool", "expected_args", "expected_source"),
    _CASES,
    ids=[f"{key}:{text}" for key, text, *_ in _CASES],
)
def test_player_input_mapping_matrix(
    perception_key: str,
    text: str,
    expected_tool: str | None,
    expected_args: dict[str, Any],
    expected_source: str,
) -> None:
    """逐条断言玩家输入 → (tool, args, source)；未映射输入必须 confidence=0 且不乱猜。"""
    perception = _PERCEPTIONS[perception_key]()
    parser = _PARSER_WITH_RECIPES if perception_key == "m4" else _PARSER

    outcome = _parse(text, perception=perception, parser=parser)

    assert outcome.tool == expected_tool, (text, outcome.tool, outcome.args)
    assert outcome.args == expected_args, (text, outcome.args)
    assert outcome.source == expected_source
    if expected_tool is None:
        assert outcome.confidence == 0.0
    else:
        assert outcome.confidence == 1.0
        assert outcome.intent is not None
        assert outcome.intent.action == expected_tool


@pytest.mark.parametrize(("text", "expected_tool", "expected_args"), _GOLDEN_7)
def test_golden_seven_inputs_are_byte_identical(
    text: str, expected_tool: str, expected_args: dict[str, Any]
) -> None:
    """7 条 m2 golden 输入映射逐字不变（tool/args/confidence/source 精确断言）。"""
    outcome = _parse(text, perception=_entry())

    assert outcome.tool == expected_tool
    assert outcome.args == expected_args
    assert outcome.confidence == 1.0
    assert outcome.source == "keyword"


def test_inspect_priority_over_room_look() -> None:
    """「查看+实体」→ 细看实体；「查看四周/环顾四周」→ 整间房（golden 不回归）。"""
    assert _parse("查看治疗药水", perception=_entry()).tool == "inspect_item"
    assert _parse("查看治疗药水", perception=_entry()).args == {"item_id": ITEM_POTION}
    assert _parse("查看四周", perception=_entry()).tool == "get_room_view"
    assert _parse("环顾四周", perception=_entry()).tool == "get_room_view"


def test_inventory_priority_over_inspect() -> None:
    """「查看背包」必须先命中背包（不被「查看」抢占），背包词优先级最高。"""
    assert _parse("查看背包", perception=_entry()).tool == "get_inventory"


def test_room_navigation_without_exit_refs_stays_unknown() -> None:
    """未填充 ``exit_refs``（旧装配）时房间名导航不会误猜方向（R2 前行为不变）。"""
    outcome = _parse("去地窖", perception=_entry())

    assert outcome.tool is None
    assert outcome.confidence == 0.0


def test_alias_match_handles_daily_short_names() -> None:
    """别名片段匹配：「药水」→「治疗药水」、「哨兵」→「老哨兵」。"""
    items = [_ref("item", ITEM_POTION, ITEM_POTION_NAME)]
    npcs = [_ref("npc", NPC_SENTINEL, NPC_SENTINEL_NAME)]

    assert _match_entity_alias("喝下药水", items).id == ITEM_POTION
    assert _match_entity_alias("跟哨兵说话", npcs).id == NPC_SENTINEL
    assert _match_entity_alias("跳舞", items + npcs) is None


def test_fuzzy_match_is_conservative_and_above_cutoff() -> None:
    """difflib 模糊匹配：≥0.6 才命中（防误配），无关输入返回 None。"""
    items = [_ref("item", ITEM_POTION, ITEM_POTION_NAME)]
    npcs = [_ref("npc", NPC_SENTINEL, NPC_SENTINEL_NAME)]

    assert _match_entity_fuzzy("查看治疗谁药水", items).id == ITEM_POTION
    assert _match_entity_fuzzy("跳舞", items + npcs) is None
    assert _match_entity_fuzzy("唱一首歌吧", items + npcs) is None


def test_empty_input_returns_unknown() -> None:
    """空白输入 → ``tool=None, confidence=0``（同既有兜底路径）。"""
    outcome = _parse("   ", perception=_entry())

    assert outcome.tool is None
    assert outcome.confidence == 0.0


# ---------------------------------------------------------------------------
# R-17：in/out 出口可导航 + 中文提示不泄漏英文
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(("text", "direction"), [("里面", "in"), ("进去", "in"), ("出去", "out")])
def test_in_out_alias_maps_to_move(text: str, direction: str) -> None:
    """``in``/``out`` 的安全中文别名应解析为 move_to（不再只有 LLM 能走）。"""
    outcome = _parse(text, perception=_entry())

    assert outcome.tool == "move_to"
    assert outcome.args == {"direction": direction}


def test_in_out_exit_ref_navigation() -> None:
    """``exit_refs`` 里的 ``in``/``out`` 方向可被房间名导航命中。"""
    perception = _perception(exit_refs=(_ref("room", "in", "里间"), _ref("room", "out", "外院")))

    assert _parse("往里间走", perception=perception).args == {"direction": "in"}
    assert _parse("去外院", perception=perception).args == {"direction": "out"}
