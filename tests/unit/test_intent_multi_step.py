"""R7 多步解析单测：确定性解析器的多步拆分与旧映射回归（契约 §5.3）。

覆盖：
- 触发条件：分隔符（，,。；;、换行）或连接词（然后/接着/随后/再）；
- 成功条件：拆出 2–3 段且**每一段都解析为写工具**才返回 ``ParseOutcome.steps``
  （``tool``/``args`` 取第一步）；任一段解析失败/只读/超过 3 段 → 逐字回退旧整句单步解析；
- 旧 7 条 golden 与常用输入在多步解析上线后映射逐字不变（``steps`` 为空）。
"""

from __future__ import annotations

from typing import Any

import pytest

from textgame.domain.models import EntityRef, PerceptionSlice
from textgame.domain.scenario import (
    ITEM_POTION,
    ITEM_POTION_NAME,
    ITEM_SWORD,
    ITEM_SWORD_NAME,
    ITEM_TORCH,
    ITEM_TORCH_NAME,
    NPC_SENTINEL,
    NPC_SENTINEL_NAME,
    PLAYER_ID,
    ROOM_CELLAR_NAME,
    ROOM_ENTRY,
    ROOM_ENTRY_NAME,
)
from textgame.orchestration.intent import DeterministicIntentParser, _split_multi_steps

_PARSER = DeterministicIntentParser()


def _ref(kind: str, entity_id: str, name: str) -> EntityRef:
    """构造感知切片实体引用（与 ``QueryService.build_perception_slice`` 同构）。"""
    return EntityRef(kind=kind, id=entity_id, name=name)


def _entry() -> PerceptionSlice:
    """m2 入口：火把/治疗药水/锈剑 + 老哨兵 + 北出口（哨站地窖）。"""
    return PerceptionSlice(
        actor_id=PLAYER_ID,
        visible_entities=[
            _ref("room", ROOM_ENTRY, ROOM_ENTRY_NAME),
            _ref("item", ITEM_TORCH, ITEM_TORCH_NAME),
            _ref("item", ITEM_POTION, ITEM_POTION_NAME),
            _ref("item", ITEM_SWORD, ITEM_SWORD_NAME),
            _ref("npc", NPC_SENTINEL, NPC_SENTINEL_NAME),
        ],
        exit_refs=[_ref("room", "north", ROOM_CELLAR_NAME)],
    )


def _parse(text: str) -> Any:
    """用固定感知切片解析一句输入（确定性、零 IO）。"""
    return _PARSER.parse(text, perception=_entry())


# ---------------------------------------------------------------------------
# 拆分器本身
# ---------------------------------------------------------------------------
def test_split_requires_separator_or_connector() -> None:
    """无分隔符/连接词 → 不拆分（旧单步路径）。"""
    assert _split_multi_steps("拿起火把") is None
    assert _split_multi_steps("向北走进地窖") is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("拿起火把，向北走", ["拿起火把", "向北走"]),
        ("拿起火把然后向北走", ["拿起火把", "向北走"]),
        ("先拿起火把，然后向北走进地窖", ["先拿起火把", "向北走进地窖"]),
        ("拿起火把；使用药水", ["拿起火把", "使用药水"]),
        ("拿起火把、使用药水", ["拿起火把", "使用药水"]),
    ],
)
def test_split_multi_step_text(text: str, expected: list[str]) -> None:
    """分隔符/连接词拆分并剥离段首连接词。"""
    assert _split_multi_steps(text) == expected


def test_split_more_than_three_segments_returns_none() -> None:
    """超过 3 段不启用多步（回退旧整句单步解析）。"""
    assert _split_multi_steps("拿起火把，拿起药水，拿起锈剑，向北走") is None


# ---------------------------------------------------------------------------
# 多步成功路径
# ---------------------------------------------------------------------------
def test_two_step_comma_with_connector() -> None:
    """「先拿起火把，然后向北走进地窖」→ 2 步计划，tool/args = 第一步。"""
    outcome = _parse("先拿起火把，然后向北走进地窖")

    assert outcome.tool == "take_item"
    assert outcome.args == {"item_id": ITEM_TORCH}
    assert outcome.source == "keyword"
    assert outcome.confidence == 1.0
    assert [step.tool for step in outcome.steps] == ["take_item", "move_to"]
    assert outcome.steps[0].args == {"item_id": ITEM_TORCH}
    assert outcome.steps[1].args == {"direction": "north"}
    assert outcome.intent is not None
    assert outcome.intent.action == "take_item"


def test_two_step_without_separator_uses_connector() -> None:
    """只含连接词（然后）没有标点 → 同样可拆 2 步。"""
    outcome = _parse("拿起火把然后向北走进地窖")

    assert [step.tool for step in outcome.steps] == ["take_item", "move_to"]


def test_three_step_plan_with_enumeration_separator() -> None:
    """3 段（、分隔）且都是写工具 → 3 步计划。"""
    outcome = _parse("拿起火把、使用治疗药水、向北走进地窖")

    assert [step.tool for step in outcome.steps] == ["take_item", "use_item", "move_to"]
    assert outcome.steps[1].args == {"item_id": ITEM_POTION}
    assert outcome.steps[2].args == {"direction": "north"}


def test_two_step_talk_then_move() -> None:
    """对话 + 移动的组合：第一步 talk_to（topic 只取第一段），第二步 move_to。"""
    outcome = _parse("对老哨兵问好，然后向北走")

    assert outcome.tool == "talk_to"
    assert outcome.args == {"npc_id": NPC_SENTINEL, "topic": "对老哨兵问好"}
    assert [step.tool for step in outcome.steps] == ["talk_to", "move_to"]
    assert outcome.steps[1].args == {"direction": "north"}


# ---------------------------------------------------------------------------
# 回退路径：不满足多步条件时旧整句单步解析逐字不变
# ---------------------------------------------------------------------------
def test_four_segments_fall_back_to_old_single_step() -> None:
    """4 段（>3）→ 回退旧整句解析：「向北走进地窖」的方向词优先 → move_to north。"""
    outcome = _parse("拿起火把，拿起治疗药水，拿起锈剑，向北走进地窖")

    assert outcome.tool == "move_to"
    assert outcome.args == {"direction": "north"}
    assert outcome.steps == ()


def test_read_only_segment_falls_back_to_old_single_step() -> None:
    """含只读段（环顾）不启用多步；旧整句解析仍是「查看 + 实体 → inspect_item」。"""
    outcome = _parse("拿起火把，环顾四周")

    assert outcome.tool == "inspect_item"
    assert outcome.args == {"item_id": ITEM_TORCH}
    assert outcome.steps == ()


def test_unparsable_segment_falls_back_to_old_single_step() -> None:
    """任一段解析失败（跳舞）→ 回退旧整句单步解析（拿起火把）。"""
    outcome = _parse("拿起火把，跳个舞")

    assert outcome.tool == "take_item"
    assert outcome.args == {"item_id": ITEM_TORCH}
    assert outcome.steps == ()


def test_inventory_segment_falls_back_to_old_single_step() -> None:
    """首段是只读背包查询 → 回退旧整句解析（背包词优先 → get_inventory）。"""
    outcome = _parse("查看背包，拿起火把")

    assert outcome.tool == "get_inventory"
    assert outcome.args == {}
    assert outcome.steps == ()


def test_improvise_style_segment_is_not_multi_step() -> None:
    """未映射的自由动作段不参与多步（``improvise`` 需裁判，也不在多步写工具集）。"""
    outcome = _parse("拿起火把，即兴跳个舞")

    assert outcome.tool == "take_item"
    assert outcome.steps == ()


def test_trailing_separator_single_segment_stays_single_step() -> None:
    """只有 1 个有效段（尾随标点）→ 不启用多步。"""
    outcome = _parse("拿起火把，")

    assert outcome.tool == "take_item"
    assert outcome.steps == ()


# ---------------------------------------------------------------------------
# 旧映射回归：15 条常用输入（含 7 条 m2 golden）+ steps 恒为空
# ---------------------------------------------------------------------------
_GOLDEN_15: tuple[tuple[str, str, dict[str, Any]], ...] = (
    ("环顾四周", "get_room_view", {}),
    ("对老哨兵问好", "talk_to", {"npc_id": NPC_SENTINEL, "topic": "对老哨兵问好"}),
    ("拿起火把", "take_item", {"item_id": ITEM_TORCH}),
    ("拿起治疗药水", "take_item", {"item_id": ITEM_POTION}),
    ("使用治疗药水", "use_item", {"item_id": ITEM_POTION}),
    ("向北走进地窖", "move_to", {"direction": "north"}),
    ("拿起锈剑", "take_item", {"item_id": ITEM_SWORD}),
    ("查看治疗药水", "inspect_item", {"item_id": ITEM_POTION}),
    ("看看四周", "get_room_view", {}),
    ("看看背包", "get_inventory", {}),
    ("喝下药水", "use_item", {"item_id": ITEM_POTION}),
    ("跟老哨兵说话", "talk_to", {"npc_id": NPC_SENTINEL, "topic": "跟老哨兵说话"}),
    ("把地上的火把捡起来", "take_item", {"item_id": ITEM_TORCH}),
    ("去地窖", "move_to", {"direction": "north"}),
    ("打量一下地窖", "get_room_view", {}),
)


@pytest.mark.parametrize(("text", "expected_tool", "expected_args"), _GOLDEN_15)
def test_old_inputs_map_unchanged_and_have_no_steps(
    text: str, expected_tool: str, expected_args: dict[str, Any]
) -> None:
    """多步解析上线后，旧输入映射逐字不变且不产生 steps（旧行为零回归）。"""
    outcome = _parse(text)

    assert outcome.tool == expected_tool
    assert outcome.args == expected_args
    assert outcome.confidence == 1.0
    assert outcome.source == "keyword"
    assert outcome.steps == ()
