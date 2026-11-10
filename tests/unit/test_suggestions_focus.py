"""R3 P2-4：craft 失败聚焦建议（domain/suggestions.py 纯函数单测）。

口径（Lead 2026-11-11 批准）：
- hint 点名缺的材料/工具 + 所在房间（如「火把在废弃哨站·入口」）；
- ``suggested[0]`` 是可提交的一步：同房间 →「拿起X」；相邻房间 →「去{房间}拿X」
  （可被 D6 房间名导航解析为 ``move_to``）；更远 →「向{方向}走」；
- 只对 craft 失败（缺材料/缺工具/材料不够/工位不符）生效，其余失败与成功回合建议不变。
"""

from __future__ import annotations

import pytest

from textgame.domain.results import ErrorCode
from textgame.domain.scenario import PLAYER_ID, build_initial_state
from textgame.domain.scenario_story import build_story_state
from textgame.domain.suggestions import failure_feedback, suggest_actions
from textgame.orchestration.intent import DeterministicIntentParser
from textgame.persistence.memory import InMemoryRepository
from textgame.services.query_service import QueryService

_SAVE_SEED = "save-42"
_MISSING_TORCH = "缺少材料：火把（先把它拿进行囊）。"
_MISSING_COPPER = "缺少材料：铜线（先把它拿进行囊）。"
_MISSING_HAMMER = "缺少工具：锻锤。"
_WRONG_STATION = "这里不是合适的制作地点（需要铁匠炉）。"


def _room(state, keyword: str):
    """按房间展示名关键词取房间（story/m2 固定场景，唯一）。"""
    return next(room for room in state.rooms.values() if keyword in room.name)


def _take_off_floor(state, names: tuple[str, ...]):
    """把地面上的指定名称物品从房间里拿掉（模拟玩家已拾取）。"""
    removed = {item.id for item in state.items.values() if item.name in names}
    rooms = {
        room_id: room.model_copy(
            update={"item_ids": [iid for iid in room.item_ids if iid not in removed]}
        )
        for room_id, room in state.rooms.items()
    }
    return state.model_copy(update={"rooms": rooms})


def _story_at(room_keyword: str, inventory_names: tuple[str, ...] = ()):
    """story 状态：玩家位于指定房间，行囊为指定名称的物品实例。"""
    state = build_story_state(save_seed=_SAVE_SEED)
    state = _take_off_floor(state, inventory_names)
    inventory = [item_id for item_id, item in state.items.items() if item.name in inventory_names]
    room = _room(state, room_keyword)
    player = state.characters[PLAYER_ID].model_copy(
        update={"location": room.id, "inventory": inventory}
    )
    return state.model_copy(update={"characters": {**state.characters, PLAYER_ID: player}})


def _parse(state, suggestion: str):
    """用确定性解析器 + 当前感知切片解析建议（验证「建议可执行」）。"""
    perception = QueryService(InMemoryRepository()).build_perception_slice(
        state, PLAYER_ID, turn_id=2, save_seed=_SAVE_SEED
    )
    return DeterministicIntentParser().parse(suggestion, perception=perception)


def test_forge_missing_torch_names_item_and_location() -> None:
    """铁匠铺缺火把（AC-09c 复现）：hint 点名火把+入口房间，suggested[0] 可执行导航到入口。"""
    state = _story_at("铁匠铺", ("铜线",))

    hint, suggestions = failure_feedback(state, ErrorCode.NOT_FOUND, reason=_MISSING_TORCH)

    assert "缺少材料" in hint and "火把" in hint
    assert "废弃哨站·入口" in hint
    assert suggestions[0] == "去废弃哨站·入口拿火把"
    assert len(suggestions) <= 4 and len(set(suggestions)) == len(suggestions)
    outcome = _parse(state, suggestions[0])
    assert outcome.tool == "move_to" and outcome.args.get("direction") == "west"


def test_hub_missing_copper_names_item_and_forge() -> None:
    """入口缺铜线：hint 点名铜线+铁匠铺，suggested[0] 可执行导航到铁匠铺。"""
    state = _story_at("入口", ("火把",))

    hint, suggestions = failure_feedback(state, ErrorCode.NOT_FOUND, reason=_MISSING_COPPER)

    assert "铜线" in hint and "废弃铁匠铺" in hint
    assert suggestions[0] == "去废弃铁匠铺拿铜线"
    outcome = _parse(state, suggestions[0])
    assert outcome.tool == "move_to" and outcome.args.get("direction") == "east"


def test_material_in_current_room_suggests_pick_up_directly() -> None:
    """火把就在当前房间地面：suggested[0] 直接是「拿起火把」，hint 提示就在这间屋里。"""
    state = _story_at("入口")  # 火把在入口地面、行囊里没有

    hint, suggestions = failure_feedback(state, ErrorCode.NOT_FOUND, reason=_MISSING_TORCH)

    assert suggestions[0] == "拿起火把"
    assert "就在这间屋里" in hint
    outcome = _parse(state, suggestions[0])
    assert outcome.tool == "take_item"


def test_missing_tool_in_current_room_suggests_pick_up() -> None:
    """缺工具：锻锤在铁匠铺地面 → suggested[0]「拿起锻锤」，hint 点名锻锤。"""
    state = _story_at("铁匠铺", ("铜线",))

    hint, suggestions = failure_feedback(state, ErrorCode.NOT_ALLOWED, reason=_MISSING_HAMMER)

    assert "锻锤" in hint
    assert suggestions[0] == "拿起锻锤"


def test_wrong_station_suggests_nearest_station_room() -> None:
    """工位不符（入口打造）：suggested 给出可执行的「去废弃铁匠铺」，hint 点名工位店。"""
    state = _story_at("入口")

    hint, suggestions = failure_feedback(state, ErrorCode.NOT_ALLOWED, reason=_WRONG_STATION)

    assert "铁匠铺" in hint
    assert suggestions[0] == "去废弃铁匠铺"
    outcome = _parse(state, suggestions[0])
    assert outcome.tool == "move_to" and outcome.args.get("direction") == "east"


def test_non_craft_failure_is_unchanged() -> None:
    """非 craft 失败（无北出口）：建议与修复前一致（suggested[0] 向西，无聚焦注记）。"""
    state = _story_at("铁匠铺", ("铜线",))

    _baseline_hint, baseline_actions = failure_feedback(state, ErrorCode.NOT_FOUND)
    hint, actions = failure_feedback(state, ErrorCode.INVALID_INPUT, reason="这里没有往北的路。")

    assert actions == baseline_actions
    assert actions[0].startswith("向西")
    assert "没有往北的路" in hint


def test_ascii_reason_degrades_to_generic_suggestions() -> None:
    """ASCII/内部诊断 reason 被过滤 → 不解析出聚焦建议，建议与通用路径一致。"""
    state = _story_at("铁匠铺", ("铜线",))
    generic = suggest_actions(state, limit=4)

    _hint, suggestions = failure_feedback(
        state, ErrorCode.NOT_FOUND, reason="missing item.torch from room_x"
    )

    assert suggestions == generic[:4]


def test_focus_feedback_is_deterministic() -> None:
    """同状态同 reason 两次调用逐字相同（可回放）。"""
    state = _story_at("铁匠铺", ("铜线",))
    first = failure_feedback(state, ErrorCode.NOT_FOUND, reason=_MISSING_TORCH)
    second = failure_feedback(state, ErrorCode.NOT_FOUND, reason=_MISSING_TORCH)

    assert first == second


def test_m2_state_craft_failure_focus_works_too() -> None:
    """非 story 场景（m2）同样生效：入口缺火把时火把就在同房间 → 拿起火把。"""
    m2 = build_initial_state(save_seed=_SAVE_SEED)
    hint, suggestions = failure_feedback(m2, ErrorCode.NOT_FOUND, reason=_MISSING_TORCH)

    assert "火把" in hint
    assert suggestions[0] == "拿起火把"


@pytest.mark.parametrize("code", [ErrorCode.CONFLICT, ErrorCode.INTERNAL])
def test_protocol_errors_ignore_craft_reason(code: ErrorCode) -> None:
    """协议级错误不使用 reason/聚焦（与 R2 口径一致）。"""
    state = _story_at("铁匠铺", ("铜线",))
    hint, _suggestions = failure_feedback(state, code, reason=_MISSING_TORCH)

    assert "火把在" not in hint
