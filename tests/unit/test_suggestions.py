"""单测：D7 玩家建议 + 失败反馈（``orchestration/suggestions.py``）。

覆盖（≥10 例）：
- m2 / story / m4 初始状态的成功建议：可执行、按优先级、≤4 条、无内部 ID；
- story 目标链（听老兵 → 打造信号火把 → 带火石登塔）、结局后不再建议；
- 空房间/缺角色兜底、limit 边界、去重、确定性（两次调用逐字相同）；
- 失败反馈：各 ``ErrorCode`` 的具体 hint、场景/行囊概要、结局态提示、未知错误不出内部术语。
"""

from __future__ import annotations

import re

import pytest

from textgame.domain.models import GameState, Item
from textgame.domain.results import ErrorCode
from textgame.domain.scenario import (
    PLAYER_ID,
    build_initial_state,
    build_m4_state,
)
from textgame.domain.scenario_story import (
    STORY_FLINT_ID,
    STORY_SIGNAL_TORCH_TEMPLATE,
    STORY_TOWER_ROOM_ID,
    build_story_state,
)
from textgame.domain.suggestions import failure_feedback, suggest_actions

_SAVE_SEED = "save-42"
_INTERNAL_ID = re.compile(r"(item_0|room_0|npc_0|char_0|game_|p_000|r_000)")
_CJK = re.compile(r"[\u4e00-\u9fff]")


def _m2() -> GameState:
    """内置 m2 初始状态（入口：老哨兵 + 火把 + 治疗药水 + 北出口）。"""
    return build_initial_state(save_seed=_SAVE_SEED)


def _story() -> GameState:
    """story 初始状态（断桥：唯一出口往东；玩家 HP 12/20、行囊空）。"""
    return build_story_state(save_seed=_SAVE_SEED)


def _with_player(
    state: GameState,
    *,
    location: str | None = None,
    inventory: list[str] | None = None,
) -> GameState:
    """复制状态并更新玩家位置/行囊（不修改原对象）。"""
    player = state.characters[PLAYER_ID]
    updated = player
    if location is not None:
        updated = updated.model_copy(update={"location": location})
    if inventory is not None:
        updated = updated.model_copy(update={"inventory": list(inventory)})
    return state.model_copy(update={"characters": {**state.characters, PLAYER_ID: updated}})


def _room_id_by_name(state: GameState, name: str) -> str:
    """按房间展示名找房间 id（测试定位用，避免硬编码内部 ID）。"""
    for room_id, room in state.rooms.items():
        if room.name == name:
            return room_id
    raise AssertionError(f"room not found: {name}")


def _room_id_by_tag(state: GameState, tag: str) -> str:
    """按 ambient tag 找房间 id（如铁匠铺的 ``station.forge``）。"""
    for room_id, room in state.rooms.items():
        if tag in room.ambient_tags:
            return room_id
    raise AssertionError(f"room tag not found: {tag}")


def _item_id_by_name(state: GameState, name: str) -> str:
    """按物品展示名找物品 id（测试定位用）。"""
    for item in state.items.values():
        if item.name == name:
            return item.id
    raise AssertionError(f"item not found: {name}")


def _victory_story() -> GameState:
    """构造 story 胜利态：人在瞭望塔 + 行囊有信号火把 + 火石（story_ending 非 None）。"""
    state = _story()
    signal_torch = Item(
        id="item_01J9Z3K4Q7X8Y9Z0ABCDEFGHKT",
        name="信号火把",
        description="用铜线缠过的火把。",
        tags=[STORY_SIGNAL_TORCH_TEMPLATE, "light", "crafted"],
    )
    items = {**state.items, signal_torch.id: signal_torch}
    return _with_player(
        state.model_copy(update={"items": items}),
        location=STORY_TOWER_ROOM_ID,
        inventory=[signal_torch.id, STORY_FLINT_ID],
    )


# ---------------------------------------------------------------------------
# suggest_actions：初始状态 / 场景 / 目标链
# ---------------------------------------------------------------------------


def test_m2_initial_suggestions_are_actionable() -> None:
    """m2 初始：先和在场 NPC 说话，再拿物品/往北走；≤4 条且无内部 ID。"""
    suggestions = suggest_actions(_m2())

    assert suggestions[0] == "和老哨兵说话"
    assert "拿起火把" in suggestions
    assert "向北走" in suggestions
    assert len(suggestions) <= 4
    assert all(_INTERNAL_ID.search(action) is None for action in suggestions)


def test_m4_initial_suggestions_include_take_and_exit() -> None:
    """m4 初始：建议包含可拿的物品与东侧铁匠铺出口（新场景/新出口也通用）。"""
    suggestions = suggest_actions(build_m4_state(save_seed=_SAVE_SEED))

    assert "拿起火把" in suggestions
    assert "向东走" in suggestions
    assert len(suggestions) <= 4


def test_story_initial_suggestions_point_to_watchman() -> None:
    """story 断桥开局：目标链第一步是「向东走」去入口找守夜人老兵。"""
    suggestions = suggest_actions(_story())

    assert suggestions[0] == "向东走"
    assert len(suggestions) <= 4
    assert all(_CJK.search(action) for action in suggestions)


def test_story_forge_with_materials_suggests_craft() -> None:
    """story 在铁匠铺且火把+铜线齐备：建议「打造信号火把」（目标 2）。"""
    state = _story()
    forge_room = _room_id_by_tag(state, "station.forge")
    torch_id = _item_id_by_name(state, "火把")
    copper_ids = [item.id for item in state.items.values() if item.name == "铜线"]
    assert len(copper_ids) >= 2
    state = _with_player(state, location=forge_room, inventory=[torch_id, *copper_ids])

    suggestions = suggest_actions(state)

    assert "打造信号火把" in suggestions
    assert len(suggestions) <= 4


def test_story_victory_state_returns_no_suggestions() -> None:
    """story 结局达成后不再给行动建议（编排层也会拒绝新回合）。"""
    assert suggest_actions(_victory_story()) == []


# ---------------------------------------------------------------------------
# 兜底 / limit / 去重 / 确定性 / 无内部 ID
# ---------------------------------------------------------------------------


def test_empty_room_falls_back_to_look_and_inventory() -> None:
    """房间无 NPC/物品/出口且行囊为空：只给固定兜底动作。"""
    state = _m2()
    room = state.rooms[state.characters[PLAYER_ID].location]
    bare_room = room.model_copy(update={"exits": {}, "item_ids": [], "npc_ids": []})
    state = state.model_copy(update={"rooms": {**state.rooms, room.id: bare_room}})

    assert suggest_actions(state) == ["环顾四周", "查看背包"]


def test_limit_is_respected_and_deduped() -> None:
    """limit 边界：1 条、0 条；结果无重复、保持顺序稳定。"""
    state = _m2()

    one = suggest_actions(state, limit=1)
    zero = suggest_actions(state, limit=0)
    default = suggest_actions(state)

    assert len(one) == 1
    assert zero == []
    assert len(default) == len(set(default))
    assert len(default) <= 4


def test_suggestions_are_deterministic() -> None:
    """同一状态两次调用逐字相同（可回放，不读时钟/随机）。"""
    state = _story()

    assert suggest_actions(state) == suggest_actions(state)
    assert failure_feedback(state, ErrorCode.NOT_FOUND) == failure_feedback(
        state, ErrorCode.NOT_FOUND
    )


def test_missing_player_falls_back_without_crash() -> None:
    """状态缺玩家角色（防御性兜底）：返回固定建议而不是抛异常。"""
    state = _m2().model_copy(update={"characters": {}})

    assert suggest_actions(state) == ["环顾四周", "查看背包"]


# ---------------------------------------------------------------------------
# failure_feedback：为什么 + 下一步
# ---------------------------------------------------------------------------


def test_failure_feedback_not_found_explains_room() -> None:
    """NOT_FOUND：提示目标不在这里，并给出当前出口/地面物品/NPC 概要。"""
    hint, suggestions = failure_feedback(_m2(), ErrorCode.NOT_FOUND)

    assert "不在这里" in hint
    assert "北" in hint
    assert "火把" in hint
    assert "老哨兵" in hint
    assert suggestions and suggestions[0] == "和老哨兵说话"
    assert all(_INTERNAL_ID.search(action) is None for action in suggestions)


def test_failure_feedback_out_of_range_explains_inventory() -> None:
    """OUT_OF_RANGE：提示条件差一点，并给出当前行囊概要（空行囊也说明）。"""
    hint, _ = failure_feedback(_m2(), ErrorCode.OUT_OF_RANGE)

    assert "差一点" in hint
    assert "行囊" in hint


def test_failure_feedback_invalid_input_suggests_rephrase() -> None:
    """INVALID_INPUT：提示换说法，并附当前场景概要；建议仍可执行。"""
    hint, suggestions = failure_feedback(_m2(), ErrorCode.INVALID_INPUT)

    assert "换个说法" in hint
    assert "这里可以往北走" in hint
    assert suggestions


@pytest.mark.parametrize(
    ("code", "keyword"),
    [
        (ErrorCode.CONFLICT, "刷新"),
        (ErrorCode.INTERNAL, "没有写入"),
        (ErrorCode.SCHEMA_VIOLATION, "生效"),
        (ErrorCode.LLM_UNAVAILABLE, "生效"),
    ],
)
def test_failure_feedback_other_codes_are_player_readable(code: ErrorCode, keyword: str) -> None:
    """其余错误码：hint 非空、含中文、不出现英文错误码/内部 ID/协议字段。"""
    hint, suggestions = failure_feedback(_m2(), code)

    assert hint and _CJK.search(hint)
    assert keyword in hint
    assert code.value not in hint
    assert _INTERNAL_ID.search(hint) is None
    assert len(suggestions) <= 4


def test_failure_feedback_none_code_is_readable() -> None:
    """error_code 为 None（防御性无错误码）：也要给出可读提示。"""
    hint, suggestions = failure_feedback(_m2(), None)

    assert hint and _CJK.search(hint)
    assert suggestions


def test_failure_feedback_uses_player_safe_reason_when_provided() -> None:
    """编排层传入中文具体原因（R2 加法 ``reason``）：作为「为什么」拼进 hint。"""
    hint, _ = failure_feedback(_m2(), ErrorCode.INVALID_INPUT, reason="这里没有往南的路。")

    assert hint.startswith("这里没有往南的路。")
    assert "这里可以往北走" in hint
    assert _INTERNAL_ID.search(hint) is None


def test_failure_feedback_filters_unsafe_reason() -> None:
    """英文诊断/内部 ID 的 reason 被静默丢弃（退回按错误码分级的通用提示）。"""
    baseline = failure_feedback(_m2(), ErrorCode.INVALID_INPUT)
    unsafe = failure_feedback(
        _m2(), ErrorCode.INVALID_INPUT, reason="no exit to 'south' from room_01J9"
    )

    assert unsafe == baseline


def test_failure_feedback_ignores_reason_for_protocol_errors() -> None:
    """协议级错误（CONFLICT/INTERNAL）不使用世界内 reason（玩家可自行刷新/重试）。"""
    hint, _ = failure_feedback(_m2(), ErrorCode.CONFLICT, reason="这里没有往南的路。")

    assert "刷新" in hint
    assert "往南" not in hint


def test_failure_feedback_ended_story_is_terminal() -> None:
    """story 结局后失败反馈：提示故事已结束，且不再给行动建议。"""
    hint, suggestions = failure_feedback(_victory_story(), ErrorCode.INVALID_INPUT)

    assert "结束" in hint
    assert suggestions == []


def test_suggestions_for_potion_inventory_recommend_use() -> None:
    """背包里有可用消耗品：建议「使用治疗药水」（story 场景 HP<max 时可观测回血）。"""
    state = _story()
    potion_id = _item_id_by_name(state, "治疗药水")
    state = _with_player(state, inventory=[potion_id])

    suggestions = suggest_actions(state)

    assert any("治疗药水" in action and action.startswith("使用") for action in suggestions)
