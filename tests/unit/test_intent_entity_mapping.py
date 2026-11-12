"""R7 实体名→ID 映射单测：``resolve_entity_arg`` / ``normalize_parse_outcome``（契约 §5.3）。

背景：LLM 解析经常输出「展示名」而不是内部 ID（例如 ``inspect_item(item_id="火盆")``），
T3 编排层在解析后、gate 校验前调用 ``normalize_parse_outcome`` 做映射，使「火把」「药水」
「老哨兵」这类自然语言参数能命中白名单实体；未知/越界值保持原样交给 gate 报 NOT_FOUND
（零写入）。本文件固定映射顺序（精确 ID → 全名 → 别名 → difflib 模糊）与「未知 ID 不模糊」。
"""

from __future__ import annotations

from textgame.agents.dto import ParseOutcome, ParseStep
from textgame.domain.models import ActionIntent, EntityRef, PerceptionSlice
from textgame.domain.scenario import (
    ITEM_POTION,
    ITEM_POTION_NAME,
    ITEM_RELIC,
    ITEM_SWORD,
    ITEM_SWORD_NAME,
    ITEM_TORCH,
    ITEM_TORCH_NAME,
    M3_NPC_SCAVENGER_A,
    M3_NPC_SCAVENGER_A_NAME,
    NPC_SENTINEL,
    NPC_SENTINEL_NAME,
    PLAYER_ID,
    ROOM_ENTRY,
    ROOM_ENTRY_NAME,
)
from textgame.orchestration.intent import normalize_parse_outcome, resolve_entity_arg


def _ref(kind: str, entity_id: str, name: str) -> EntityRef:
    """构造感知切片实体引用（与 ``QueryService.build_perception_slice`` 同构）。"""
    return EntityRef(kind=kind, id=entity_id, name=name)


def _perception(*, with_character: bool = True) -> PerceptionSlice:
    """m2 入口感知：火把/药水/剑 + 老哨兵（可选：同房间角色「拾荒者甲」）。"""
    entities = [
        _ref("room", ROOM_ENTRY, ROOM_ENTRY_NAME),
        _ref("item", ITEM_TORCH, ITEM_TORCH_NAME),
        _ref("item", ITEM_POTION, ITEM_POTION_NAME),
        _ref("item", ITEM_SWORD, ITEM_SWORD_NAME),
        _ref("npc", NPC_SENTINEL, NPC_SENTINEL_NAME),
    ]
    if with_character:
        entities.append(_ref("character", M3_NPC_SCAVENGER_A, M3_NPC_SCAVENGER_A_NAME))
    return PerceptionSlice(actor_id=PLAYER_ID, visible_entities=entities)


# ---------------------------------------------------------------------------
# resolve_entity_arg：映射顺序与未知值
# ---------------------------------------------------------------------------
def test_resolve_item_by_exact_id() -> None:
    """精确 ID 原样返回（幂等）。"""
    assert resolve_entity_arg("item", ITEM_TORCH, _perception()) == ITEM_TORCH


def test_resolve_item_by_full_name() -> None:
    """全名子串 → 实体 ID（「火把」→ item_...Q）。"""
    assert resolve_entity_arg("item", ITEM_TORCH_NAME, _perception()) == ITEM_TORCH
    assert resolve_entity_arg("item", ITEM_POTION_NAME, _perception()) == ITEM_POTION


def test_resolve_item_by_alias_fragment() -> None:
    """名称片段别名（「药水」→ 治疗药水、「剑」太短不匹配、「锈剑」→ 锈剑）。"""
    assert resolve_entity_arg("item", "药水", _perception()) == ITEM_POTION
    assert resolve_entity_arg("item", ITEM_SWORD_NAME, _perception()) == ITEM_SWORD


def test_resolve_item_by_conservative_fuzzy() -> None:
    """difflib 模糊兜底（≥0.6）：轻微错字仍映射到唯一实体。"""
    assert resolve_entity_arg("item", "治疗谁药水", _perception()) == ITEM_POTION


def test_resolve_unknown_item_name_returns_none() -> None:
    """感知切片里没有的实体（斩龙剑）→ None（调用方保留原值，交 gate 报 NOT_FOUND）。"""
    assert resolve_entity_arg("item", "斩龙剑", _perception()) is None
    assert resolve_entity_arg("item", ITEM_RELIC, _perception()) is None  # 古物不在此感知切片


def test_resolve_npc_by_name_and_alias() -> None:
    """NPC 全名/别名（「老哨兵」「哨兵」）→ npc ID。"""
    assert resolve_entity_arg("npc", NPC_SENTINEL_NAME, _perception()) == NPC_SENTINEL
    assert resolve_entity_arg("npc", "哨兵", _perception()) == NPC_SENTINEL


def test_resolve_npc_includes_same_room_character_entities() -> None:
    """``npc`` 视角同时覆盖同房间 Character 实体（m3 拾荒者甲可被攻击/给予目标命中）。"""
    assert resolve_entity_arg("npc", M3_NPC_SCAVENGER_A_NAME, _perception()) == M3_NPC_SCAVENGER_A
    assert (
        resolve_entity_arg("npc", M3_NPC_SCAVENGER_A_NAME, _perception(with_character=False))
        is None
    )


def test_resolve_unknown_internal_id_does_not_fuzzy_match_other_entity() -> None:
    """以内部 ID 前缀开头但不存在 → 不模糊映射（避免把错 ID 映射成别的实体）。"""
    assert resolve_entity_arg("item", "item_01J9Z3K4Q7X8Y9Z0ABCDEFGHK9", _perception()) is None
    assert resolve_entity_arg("npc", "npc_01J9Z3K4Q7X8Y9Z0ABCDEFGHK9", _perception()) is None
    assert resolve_entity_arg("npc", "char_01J9Z3K4Q7X8Y9Z0ABCDEFGHK9", _perception()) is None


def test_resolve_kind_isolated() -> None:
    """item 查询不会命中 npc 实体（反之亦然）。"""
    assert resolve_entity_arg("item", NPC_SENTINEL_NAME, _perception()) is None
    assert resolve_entity_arg("npc", ITEM_TORCH_NAME, _perception()) is None


def test_resolve_empty_or_non_string_value_returns_none() -> None:
    """空值/非字符串 → None（解析层防御，不抛异常）。"""
    assert resolve_entity_arg("item", "", _perception()) is None
    assert resolve_entity_arg("item", "   ", _perception()) is None


# ---------------------------------------------------------------------------
# normalize_parse_outcome：顶层 args
# ---------------------------------------------------------------------------
def test_normalize_maps_display_name_args_to_ids() -> None:
    """顶层 args 的 item_id/npc_id 展示名 → ID。"""
    outcome = ParseOutcome(
        tool="give_item",
        args={"item_id": ITEM_TORCH_NAME, "npc_id": NPC_SENTINEL_NAME},
        confidence=1.0,
    )

    mapped = normalize_parse_outcome(outcome, _perception())

    assert mapped is not outcome
    assert mapped.args == {"item_id": ITEM_TORCH, "npc_id": NPC_SENTINEL}
    # 原对象（frozen）不被修改
    assert outcome.args == {"item_id": ITEM_TORCH_NAME, "npc_id": NPC_SENTINEL_NAME}


def test_normalize_updates_intent_params_and_target() -> None:
    """intent.params 与顶层 args 保持一致；target 若原为展示名也映射为 ID。"""
    intent = ActionIntent(
        action="take_item",
        target=ITEM_TORCH_NAME,
        params={"item_id": ITEM_TORCH_NAME},
        confidence=1.0,
    )
    outcome = ParseOutcome(
        intent=intent, tool="take_item", args={"item_id": ITEM_TORCH_NAME}, confidence=1.0
    )

    mapped = normalize_parse_outcome(outcome, _perception())

    assert mapped.intent is not None
    assert mapped.intent.params == {"item_id": ITEM_TORCH}
    assert mapped.intent.target == ITEM_TORCH


def test_normalize_keeps_unknown_values_and_returns_identity_when_no_change() -> None:
    """未知/越界值原样保留；无任何变化时返回原对象（调用方可按恒等判断跳过）。"""
    outcome = ParseOutcome(tool="take_item", args={"item_id": "item_missing_0001"}, confidence=1.0)

    mapped = normalize_parse_outcome(outcome, _perception())

    assert mapped is outcome
    assert mapped.args == {"item_id": "item_missing_0001"}


def test_normalize_no_mapping_fields_returns_identity() -> None:
    """只有 direction 等非实体参数 → 原对象返回。"""
    outcome = ParseOutcome(tool="move_to", args={"direction": "north"}, confidence=1.0)

    assert normalize_parse_outcome(outcome, _perception()) is outcome


# ---------------------------------------------------------------------------
# normalize_parse_outcome：steps / candidate_args
# ---------------------------------------------------------------------------
def test_normalize_maps_steps_args() -> None:
    """多步计划里的每一步 item_id/npc_id 都映射（首步与顶层一致）。"""
    steps = (
        ParseStep(tool="take_item", args={"item_id": ITEM_TORCH_NAME}),
        ParseStep(tool="give_item", args={"item_id": ITEM_POTION_NAME, "npc_id": "哨兵"}),
    )
    outcome = ParseOutcome(
        tool="take_item",
        args={"item_id": ITEM_TORCH_NAME},
        steps=steps,
        confidence=1.0,
    )

    mapped = normalize_parse_outcome(outcome, _perception())

    assert mapped.steps[0].args == {"item_id": ITEM_TORCH}
    assert mapped.steps[1].args == {"item_id": ITEM_POTION, "npc_id": NPC_SENTINEL}
    assert mapped.args == {"item_id": ITEM_TORCH}
    # 原 steps 不被修改（frozen ParseStep）
    assert outcome.steps[0].args == {"item_id": ITEM_TORCH_NAME}


def test_normalize_maps_candidate_args_without_executing() -> None:
    """低置信候选参数也做映射（供编排层生成建议），但 tool/args 仍为空（不执行）。"""
    outcome = ParseOutcome(
        tool=None,
        args={},
        confidence=0.2,
        candidate_tool="take_item",
        candidate_args={"item_id": ITEM_TORCH_NAME},
    )

    mapped = normalize_parse_outcome(outcome, _perception())

    assert mapped.tool is None
    assert mapped.args == {}
    assert mapped.candidate_tool == "take_item"
    assert mapped.candidate_args == {"item_id": ITEM_TORCH}


def test_normalize_empty_candidate_args_stays_empty() -> None:
    """无候选参数时保持空 dict（不改变旧字段形状）。"""
    outcome = ParseOutcome(tool="get_room_view", args={}, confidence=1.0)

    mapped = normalize_parse_outcome(outcome, _perception())

    assert mapped is outcome
    assert mapped.candidate_args == {}
