"""用户自上传场景 JSON 领域层单测（契约：docs/design/user-scenario-modules.md §2/§3.1/§3.2）。

覆盖：解析/结构化错误、确定性 ULID 派生、建局、条件 DSL、目标/结局钩子、进入伤害、NPC 对话、
warnings 与体积上限。全部纯函数级（不启服务、不连库、不联网）。
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any

import pytest

from textgame.domain.scenario import PLAYER_ID
from textgame.domain.user_scenario import (
    MAX_DEFINITION_BYTES,
    Condition,
    ScenarioIds,
    UserScenarioHooks,
    build_initial_state,
    derive_ids,
    entity_id,
    evaluate_condition,
    parse_definition,
)

_ULID_PATTERN = re.compile(r"^[a-z]+_[0-9A-HJKMNP-TV-Z]{26}$")


def _tiny() -> dict[str, Any]:
    """最小可玩定义（与设计文档 §2.4 示例一致）。"""
    return {
        "schema_version": 1,
        "slug": "tiny-tower",
        "meta": {"title": "小塔", "author": "示例", "description": "两房间最小可玩场景"},
        "player": {
            "name": "旅人",
            "hp": 10,
            "max_hp": 10,
            "location": "gate",
            "inventory": [],
        },
        "rooms": [
            {
                "key": "gate",
                "name": "塔门",
                "description": "一扇虚掩的木门，门缝里透出风。",
                "exits": {"north": "hall"},
            },
            {
                "key": "hall",
                "name": "塔厅",
                "description": "螺旋楼梯向上，墙上挂着一盏没点亮的灯。",
                "exits": {"south": "gate"},
                "items": ["flint"],
                "on_enter": {"hp_delta": -2, "hint": "冷风从楼梯间灌下来。"},
            },
        ],
        "items": [
            {"key": "flint", "name": "火石", "description": "一块干燥的火石。", "tags": ["quest"]},
        ],
        "npcs": [],
        "goal": {
            "title": "爬上塔顶",
            "objectives": [
                {
                    "key": "climb",
                    "text": "带着火石进入塔厅",
                    "when": {"all": [{"in_room": "hall"}, {"has_item": "flint"}]},
                }
            ],
        },
        "endings": [
            {
                "key": "win",
                "kind": "win",
                "title": "你登了上去",
                "text": "塔顶的风很大，但灯终于亮了。",
                "priority": 10,
                "when": {"all": [{"in_room": "hall"}, {"has_item": "flint"}]},
            }
        ],
    }


def _rich() -> dict[str, Any]:
    """带 NPC/伤害/多目标的定义（覆盖对话、兜底结局、进入伤害）。"""
    definition = _tiny()
    definition["slug"] = "ember-watch"
    definition["meta"] = {"title": "余烬哨站（演示）", "author": "示例", "description": "演示场景"}
    definition["rooms"] = [
        {
            "key": "gate",
            "name": "断桥",
            "description": "风雪把桥面埋了一半。",
            "exits": {"east": "hub"},
        },
        {
            "key": "hub",
            "name": "哨站入口",
            "description": "废弃哨站的入口，墙角堆着木箱。",
            "exits": {"west": "gate", "down": "well", "up": "tower"},
            "npcs": ["watchman"],
        },
        {
            "key": "well",
            "name": "枯井",
            "description": "井底结着薄冰，寒气逼人。",
            "exits": {"up": "hub"},
            "on_enter": {"hp_delta": -6, "hint": "寒气顺着领口灌进来。"},
        },
        {
            "key": "tower",
            "name": "瞭望塔",
            "description": "塔顶的风很大，信号灯早已熄灭。",
            "exits": {"down": "hub"},
            "items": ["flint", "torch"],
        },
    ]
    definition["items"] = [
        {"key": "flint", "name": "火石", "description": "一块干燥的火石。", "tags": ["quest"]},
        {
            "key": "torch",
            "name": "火把",
            "description": "浸过松脂的火把。",
            "tags": ["quest"],
        },
    ]
    definition["npcs"] = [
        {
            "key": "watchman",
            "name": "守夜人",
            "room": "hub",
            "description": "他靠着断墙，眼睛没离开过楼梯口。",
            "dialogue": {
                "default": "他抬了抬下巴，示意你继续往里走。",
                "topics": [
                    {
                        "keywords": ["任务", "灯"],
                        "reply": "去塔顶把灯点起来。",
                        "dialogue_state": "briefed",
                    }
                ],
            },
        }
    ]
    definition["goal"] = {
        "title": "点亮信号灯",
        "description": "让塔顶的火重新烧起来。",
        "objectives": [
            {
                "key": "listen",
                "text": "向守夜人打听情况",
                "when": {"npc_state": {"npc": "watchman", "state": "briefed"}},
            },
            {
                "key": "light",
                "text": "带着火石和火把登上塔顶",
                "when": {
                    "all": [{"in_room": "tower"}, {"has_item": "flint"}, {"has_item": "torch"}]
                },
            },
        ],
    }
    definition["endings"] = [
        {
            "key": "victory",
            "kind": "win",
            "title": "灯亮了",
            "text": "信号火在夜里烧了起来。",
            "priority": 10,
            "when": {"all": [{"in_room": "tower"}, {"has_item": "flint"}]},
        },
        {
            "key": "fallen",
            "kind": "lose",
            "title": "倒在雪里",
            "text": "你倒在雪里，再没醒来。",
            "priority": 1,
            "when": {"hp_lte": 0},
        },
    ]
    return definition


def _parsed(definition: dict[str, Any]):
    """解析一份定义并断言成功（测试辅助）。"""
    parsed = parse_definition(json.dumps(definition, ensure_ascii=False))
    assert parsed.ok, parsed.errors
    assert parsed.definition is not None
    return parsed


def _hooks(
    definition: dict[str, Any], scenario_id: str = "scn_01J9Z3K4Q7X8Y9Z0ABCDEFGHJ0"
) -> UserScenarioHooks:
    """构造钩子（测试辅助）。"""
    parsed = _parsed(definition)
    assert parsed.definition is not None
    ids = derive_ids(scenario_id, parsed.definition)
    return UserScenarioHooks(parsed.definition, ids)


def _state_with_inventory(state, item_keys: list[str], ids: ScenarioIds):
    """复制状态并把指定 key 的物品塞进玩家行囊（测试辅助）。"""
    player = state.characters[PLAYER_ID]
    updated = player.model_copy(update={"inventory": [ids.items[key] for key in item_keys]})
    return state.model_copy(update={"characters": {PLAYER_ID: updated}})


def _state_in_room(state, room_key: str, ids: ScenarioIds):
    """复制状态并移动玩家到指定房间（测试辅助）。"""
    player = state.characters[PLAYER_ID]
    updated = player.model_copy(update={"location": ids.rooms[room_key]})
    return state.model_copy(update={"characters": {PLAYER_ID: updated}})


# ---------------------------------------------------------------------------
# 解析与错误定位
# ---------------------------------------------------------------------------


def test_parse_tiny_ok_with_summary() -> None:
    """最小定义解析成功，summary 数字与定义一致。"""
    parsed = _parsed(_tiny())
    assert parsed.summary == {
        "rooms": 2,
        "items": 1,
        "npcs": 0,
        "objectives": 1,
        "endings": 1,
        "suggestions": 0,
    }


def test_parse_json_syntax_error_has_path() -> None:
    """非法 JSON → ``$`` 路径的阻断错误（带行列信息）。"""
    parsed = parse_definition('{"schema_version": 1,')
    assert not parsed.ok
    assert parsed.errors[0].path == "$"
    assert "JSON" in parsed.errors[0].message


def test_parse_non_object_rejected() -> None:
    """顶层不是对象 → 阻断错误。"""
    parsed = parse_definition("[1, 2, 3]")
    assert not parsed.ok
    assert "顶层必须是 JSON 对象" in parsed.errors[0].message


def test_parse_too_large_rejected() -> None:
    """超过 256KB → 阻断错误（不尝试解析）。"""
    parsed = parse_definition("x" * (MAX_DEFINITION_BYTES + 1))
    assert not parsed.ok
    assert "文件过大" in parsed.errors[0].message


def test_unknown_field_rejected_with_path() -> None:
    """未知字段（extra=forbid）→ 错误路径指向该字段。"""
    definition = _tiny()
    definition["rooms"][0]["mystery"] = True
    parsed = parse_definition(json.dumps(definition, ensure_ascii=False))
    assert not parsed.ok
    assert any(error.path == "rooms[0].mystery" for error in parsed.errors)


def test_unknown_room_reference_path() -> None:
    """exits 引用不存在的房间 → 错误路径可定位。"""
    definition = _tiny()
    definition["rooms"][0]["exits"] = {"north": "nowhere"}
    parsed = parse_definition(json.dumps(definition, ensure_ascii=False))
    assert not parsed.ok
    assert any(error.path == "rooms[0].exits.north" for error in parsed.errors)


def test_duplicate_room_key_rejected() -> None:
    """房间 key 重复 → 阻断错误。"""
    definition = _tiny()
    definition["rooms"].append(copy.deepcopy(definition["rooms"][0]))
    definition["rooms"][1]["key"] = "gate"
    definition["rooms"][1]["name"] = "重复门"
    parsed = parse_definition(json.dumps(definition, ensure_ascii=False))
    assert not parsed.ok
    assert any(error.path == "rooms.key" for error in parsed.errors)


def test_item_placed_twice_rejected() -> None:
    """同一物品 key 放到两个房间 → 阻断错误。"""
    definition = _tiny()
    definition["rooms"][0]["items"] = ["flint"]
    parsed = parse_definition(json.dumps(definition, ensure_ascii=False))
    assert not parsed.ok
    assert any("只能放一处" in error.message for error in parsed.errors)


def test_invalid_direction_rejected() -> None:
    """非法方向 → 字段错误（路径含 exits）。"""
    definition = _tiny()
    definition["rooms"][0]["exits"] = {"northeast": "hall"}
    parsed = parse_definition(json.dumps(definition, ensure_ascii=False))
    assert not parsed.ok
    assert any("exits" in error.path for error in parsed.errors)


def test_player_hp_above_max_rejected() -> None:
    """player.hp > player.max_hp → 阻断错误。"""
    definition = _tiny()
    definition["player"]["hp"] = 99
    parsed = parse_definition(json.dumps(definition, ensure_ascii=False))
    assert not parsed.ok
    assert any("player" in error.path for error in parsed.errors)


def test_unknown_player_attribute_rejected_at_import() -> None:
    """R-02：未知属性键必须在导入阶段失败（否则建局 Character 校验 500）。"""
    definition = _tiny()
    definition["player"]["attributes"] = {"luck": 1}
    parsed = parse_definition(json.dumps(definition, ensure_ascii=False))
    assert not parsed.ok
    assert any(error.path == "player.attributes" for error in parsed.errors)


def test_room_self_exit_rejected_at_import() -> None:
    """R-02：房间出口指向自己必须在导入阶段失败。"""
    definition = _tiny()
    definition["rooms"][0]["exits"] = {"north": "gate"}
    parsed = parse_definition(json.dumps(definition, ensure_ascii=False))
    assert not parsed.ok
    assert any("不能把出口指向自己" in error.message for error in parsed.errors)


def test_item_in_inventory_and_room_rejected() -> None:
    """R-02：同一 item 同时出现在背包和房间必须在导入阶段失败。"""
    definition = _tiny()
    definition["player"]["inventory"] = ["flint"]  # flint 仍在 hall 的 items 里
    parsed = parse_definition(json.dumps(definition, ensure_ascii=False))
    assert not parsed.ok
    assert any("已经在玩家行囊里" in error.message for error in parsed.errors)


def test_duplicate_inventory_item_rejected() -> None:
    """R-02：玩家背包重复引用同一 item key 必须在导入阶段失败。"""
    definition = _tiny()
    definition["rooms"][1]["items"] = []
    definition["player"]["inventory"] = ["flint", "flint"]
    parsed = parse_definition(json.dumps(definition, ensure_ascii=False))
    assert not parsed.ok
    assert any(error.path == "player.inventory" for error in parsed.errors)


def test_parse_json_non_finite_rejected() -> None:
    """R-11：``NaN``/``Infinity`` 在导入期拒绝（否则后续 canonical_json 才 500）。"""
    parsed = parse_definition(
        '{"schema_version": 1, "slug": "bad-numbers", "meta": {"title": "x"}, '
        '"player": {"location": "r", "attributes": {"str": NaN}}, '
        '"rooms": [{"key": "r", "name": "R", "description": "d"}], "items": [], "npcs": []}'
    )
    assert not parsed.ok
    assert "非法数值" in parsed.errors[0].message


def test_unknown_condition_reference_rejected() -> None:
    """条件引用不存在的 NPC → 错误路径含 npc_state.npc。"""
    definition = _rich()
    definition["goal"]["objectives"][0]["when"] = {
        "npc_state": {"npc": "ghost", "state": "briefed"}
    }
    parsed = parse_definition(json.dumps(definition, ensure_ascii=False))
    assert not parsed.ok
    assert any("npc_state.npc" in error.path for error in parsed.errors)


def test_warnings_for_unreachable_item_and_missing_goal() -> None:
    """不可达物品/缺 goal/缺 endings 给 warning 而不是 error。"""
    definition = _tiny()
    definition["items"].append(
        {"key": "ghost_item", "name": "幽灵物", "description": "拿不到的东西"}
    )
    definition.pop("goal")
    definition.pop("endings")
    parsed = parse_definition(json.dumps(definition, ensure_ascii=False))
    assert parsed.ok
    paths = {warning.path for warning in parsed.warnings}
    assert "items[1]" in paths
    assert "goal" in paths
    assert "endings" in paths


# ---------------------------------------------------------------------------
# 确定性 ID / 建局
# ---------------------------------------------------------------------------


def test_entity_id_is_deterministic_and_valid() -> None:
    """同输入恒同 ID；形态通过领域 ID 正则；不同 key 不同 ID。"""
    first = entity_id("room", "scn_A", "gate")
    assert first == entity_id("room", "scn_A", "gate")
    assert _ULID_PATTERN.match(first)
    assert first != entity_id("room", "scn_A", "hall")
    assert first != entity_id("room", "scn_B", "gate")


def test_derive_ids_roundtrip() -> None:
    """派生 ID 映射覆盖定义的每个 key。"""
    parsed = _parsed(_rich())
    assert parsed.definition is not None
    ids = derive_ids("scn_01J9Z3K4Q7X8Y9Z0ABCDEFGHJ0", parsed.definition)
    assert set(ids.rooms) == {"gate", "hub", "well", "tower"}
    assert set(ids.items) == {"flint", "torch"}
    assert set(ids.npcs) == {"watchman"}


def test_build_initial_state_shape() -> None:
    """物化结果：玩家固定 PLAYER_ID、房间/物品/NPC 按定义放置。"""
    parsed = _parsed(_rich())
    assert parsed.definition is not None
    ids = derive_ids("scn_01J9Z3K4Q7X8Y9Z0ABCDEFGHJ0", parsed.definition)
    state = build_initial_state(parsed.definition, ids, save_seed="save-42")

    assert state.turn == 1
    assert state.world_time == 0
    assert PLAYER_ID in state.characters
    player = state.characters[PLAYER_ID]
    assert player.location == ids.rooms["gate"]
    assert len(state.rooms) == 4
    assert len(state.items) == 2
    assert len(state.npcs) == 1
    # NPC 挂在 hub；火石/火把放在 tower
    assert state.npcs[ids.npcs["watchman"]].room_id == ids.rooms["hub"]
    assert ids.items["flint"] in state.rooms[ids.rooms["tower"]].item_ids
    assert state.rooms[ids.rooms["gate"]].exits["east"] == ids.rooms["hub"]
    assert player.hp == 10


def test_build_initial_state_is_deterministic() -> None:
    """同定义 + 同 seed 建局两次 → 完全相同的状态（回放可复算）。"""
    parsed = _parsed(_rich())
    assert parsed.definition is not None
    ids = derive_ids("scn_01J9Z3K4Q7X8Y9Z0ABCDEFGHJ0", parsed.definition)
    first = build_initial_state(parsed.definition, ids, save_seed="save-42")
    second = build_initial_state(parsed.definition, ids, save_seed="save-42")
    assert first.model_dump() == second.model_dump()


# ---------------------------------------------------------------------------
# 条件 DSL
# ---------------------------------------------------------------------------


def _condition(payload: dict[str, Any]) -> Condition:
    """从 JSON 形状构造条件（测试辅助）。"""
    return Condition.model_validate(payload)


def test_condition_evaluation_matrix() -> None:
    """原子/组合条件在真实状态上逐项求值。"""
    parsed = _parsed(_rich())
    assert parsed.definition is not None
    ids = derive_ids("scn_01J9Z3K4Q7X8Y9Z0ABCDEFGHJ0", parsed.definition)
    state = build_initial_state(parsed.definition, ids, save_seed="save-42")

    assert evaluate_condition(_condition({"in_room": "gate"}), state, ids) is True
    assert evaluate_condition(_condition({"in_room": "tower"}), state, ids) is False
    assert evaluate_condition(_condition({"has_item": "flint"}), state, ids) is False
    with_item = _state_with_inventory(state, ["flint"], ids)
    assert evaluate_condition(_condition({"has_item": "flint"}), with_item, ids) is True
    assert evaluate_condition(_condition({"has_item_tag": "quest"}), with_item, ids) is True
    assert evaluate_condition(_condition({"hp_lte": 10}), state, ids) is True
    assert evaluate_condition(_condition({"hp_lte": 9}), state, ids) is False
    assert evaluate_condition(_condition({"hp_gte": 10}), state, ids) is True
    assert (
        evaluate_condition(
            _condition({"npc_state": {"npc": "watchman", "state": "idle"}}), state, ids
        )
        is True
    )
    assert evaluate_condition(_condition({"always": True}), state, ids) is True
    assert (
        evaluate_condition(_condition({"all": [{"in_room": "gate"}, {"hp_gte": 1}]}), state, ids)
        is True
    )
    assert (
        evaluate_condition(
            _condition({"any": [{"in_room": "tower"}, {"in_room": "gate"}]}), state, ids
        )
        is True
    )
    assert evaluate_condition(_condition({"not": {"in_room": "tower"}}), state, ids) is True


def test_condition_rejects_multiple_keys() -> None:
    """一个条件只能有一个键（防止歧义）。"""
    with pytest.raises(ValueError, match="只能包含一个键"):
        Condition.model_validate({"in_room": "gate", "hp_lte": 3})
    with pytest.raises(ValueError, match="只能包含一个键"):
        Condition.model_validate({})


# ---------------------------------------------------------------------------
# 钩子：目标 / 结局 / 伤害 / 对话
# ---------------------------------------------------------------------------


def test_goal_progress_from_state() -> None:
    """目标完成态由状态读时求值（NPC 对话状态推进 listen）。"""
    definition = _rich()
    hooks = _hooks(definition)
    parsed = _parsed(definition)
    assert parsed.definition is not None
    ids = derive_ids(hooks.scenario_id, parsed.definition)
    state = build_initial_state(parsed.definition, ids, save_seed="save-42")
    goal = hooks.goal(state)
    assert goal is not None
    objectives = goal["objectives"]
    assert [item["id"] for item in objectives] == ["listen", "light"]
    assert [item["done"] for item in objectives] == [False, False]

    watchman = state.npcs[ids.npcs["watchman"]].model_copy(update={"dialogue_state": "briefed"})
    progressed = state.model_copy(update={"npcs": {**state.npcs, ids.npcs["watchman"]: watchman}})
    goal2 = hooks.goal(progressed)
    assert goal2 is not None
    assert [item["done"] for item in goal2["objectives"]] == [True, False]


def test_ending_priority_and_kind() -> None:
    """显式结局按 priority 降序：高优先级胜利优先于低优先级倒下。"""
    hooks = _hooks(_rich())
    parsed = _parsed(_rich())
    assert parsed.definition is not None
    ids = derive_ids(hooks.scenario_id, parsed.definition)
    state = build_initial_state(parsed.definition, ids, save_seed="save-42")
    state = _state_with_inventory(state, ["flint"], ids)
    state = _state_in_room(state, "tower", ids)
    player = state.characters[PLAYER_ID].model_copy(update={"hp": 0})
    state = state.model_copy(update={"characters": {PLAYER_ID: player}})
    ending = hooks.ending(state)
    assert ending is not None
    assert ending["ending_id"] == "ending_ember-watch_victory"
    assert ending["kind"] == "win"


def test_ending_fallback_complete_and_fallen() -> None:
    """未写 endings 时：目标全完成=胜利兜底；HP 归零=倒下兜底。"""
    definition = _tiny()
    definition["endings"] = []
    hooks = _hooks(definition)
    parsed = _parsed(definition)
    assert parsed.definition is not None
    ids = derive_ids(hooks.scenario_id, parsed.definition)
    state = build_initial_state(parsed.definition, ids, save_seed="save-42")

    open_state = _state_in_room(_state_with_inventory(state, ["flint"], ids), "hall", ids)
    ending = hooks.ending(open_state)
    assert ending is not None
    assert ending["ending_id"] == "ending_tiny-tower_complete"
    assert ending["kind"] == "win"

    player = state.characters[PLAYER_ID].model_copy(update={"hp": 0})
    fallen_state = state.model_copy(update={"characters": {PLAYER_ID: player}})
    fallen = hooks.ending(fallen_state)
    assert fallen is not None
    assert fallen["ending_id"] == "ending_tiny-tower_fallen"
    assert fallen["kind"] == "lose"


def test_room_damage_and_hint() -> None:
    """进入伤害与提示来自 rooms[].on_enter；无定义返回 None。"""
    hooks = _hooks(_rich())
    parsed = _parsed(_rich())
    assert parsed.definition is not None
    ids = derive_ids(hooks.scenario_id, parsed.definition)
    assert hooks.room_damage(ids.rooms["well"]) == -6
    assert hooks.room_damage_hint(ids.rooms["well"]) == "寒气顺着领口灌进来。"
    assert hooks.room_damage(ids.rooms["gate"]) is None
    assert hooks.room_damage("room_01J9Z3K4Q7X8Y9Z0ABCDEFGHJK") is None


def test_npc_reply_keywords_default_and_unknown() -> None:
    """NPC 对话：关键词命中带 dialogue_state；未命中 default；未知 NPC None。"""
    hooks = _hooks(_rich())
    parsed = _parsed(_rich())
    assert parsed.definition is not None
    ids = derive_ids(hooks.scenario_id, parsed.definition)
    npc_id = ids.npcs["watchman"]
    assert hooks.npc_reply(npc_id, "能告诉我任务吗") == {
        "reply": "去塔顶把灯点起来。",
        "dialogue_state": "briefed",
    }
    assert hooks.npc_reply(npc_id, "你好") == {
        "reply": "他抬了抬下巴，示意你继续往里走。",
        "dialogue_state": None,
    }
    assert hooks.npc_reply("npc_01J9Z3K4Q7X8Y9Z0ABCDEFGHJK", "你好") is None


def test_generic_reply_when_dialogue_absent() -> None:
    """NPC 未定义 dialogue → 通用文案（不阻断玩法）。"""
    definition = _tiny()
    definition["npcs"] = [{"key": "npc1", "name": "路人", "room": "gate"}]
    hooks = _hooks(definition)
    parsed = _parsed(definition)
    assert parsed.definition is not None
    ids = derive_ids(hooks.scenario_id, parsed.definition)
    reply = hooks.npc_reply(ids.npcs["npc1"], "你好")
    assert reply is not None
    assert reply["reply"]
