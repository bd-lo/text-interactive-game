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


# ---------------------------------------------------------------------------
# v2（schema_version=2）：flag / 目标进度 / 物品 / NPC / 时间 / 多结局（设计：scenario-dsl-v2.md）
# ---------------------------------------------------------------------------

_V2_SCENARIO_ID = "scn_01J9Z3K4Q7X8Y9Z0ABCDEFGHJ0"


def _v2() -> dict[str, Any]:
    """v2 完整定义（flags + 目标进度链 + 多结局；与 examples/scenarios/lantern-vigil.json 同构）。"""
    return {
        "schema_version": 2,
        "slug": "lantern-vigil",
        "meta": {"title": "灯守之夜", "author": "示例", "description": "v2 条件矩阵"},
        "player": {"name": "旅人", "hp": 12, "max_hp": 12, "location": "gate"},
        "rooms": [
            {
                "key": "gate",
                "name": "塔门",
                "description": "门缝里透出风。",
                "exits": {"east": "hall"},
            },
            {
                "key": "hall",
                "name": "厅堂",
                "description": "灯座空着。",
                "exits": {"west": "gate", "east": "shrine"},
                "npcs": ["keeper"],
                "items": ["candle"],
            },
            {
                "key": "shrine",
                "name": "灯室",
                "description": "石台上摆着灯油。",
                "exits": {"west": "hall"},
                "items": ["oil", "flint"],
            },
        ],
        "items": [
            {"key": "candle", "name": "蜡烛", "tags": ["quest"]},
            {"key": "oil", "name": "灯油", "tags": ["quest"]},
            {"key": "flint", "name": "火石", "tags": ["tool"]},
        ],
        "npcs": [
            {
                "key": "keeper",
                "name": "灯守",
                "room": "hall",
                "dialogue": {
                    "default": "灯要亮，得先有油。",
                    "topics": [
                        {
                            "keywords": ["任务", "灯"],
                            "reply": "去灯室取灯油。",
                            "dialogue_state": "briefed",
                        }
                    ],
                },
            }
        ],
        "flags": [
            {"key": "keeper_briefed", "when": {"npc_state": {"npc": "keeper", "state": "briefed"}}},
            {"key": "oil_waiting", "when": {"item_in_room": {"item": "oil", "room": "shrine"}}},
            {"key": "in_shrine", "when": {"in_room": "shrine"}},
            {"key": "keeper_at_hall", "when": {"npc_in_room": {"npc": "keeper", "room": "hall"}}},
            {
                "key": "supplies_ready",
                "when": {"has_item_tag_count": {"tag": "quest", "at_least": 2}},
            },
        ],
        "goal": {
            "title": "点亮长明灯",
            "objectives": [
                {"key": "listen", "text": "打听方法", "when": {"flag": "keeper_briefed"}},
                {"key": "gather", "text": "集齐材料", "when": {"flag": "supplies_ready"}},
                {
                    "key": "light",
                    "text": "带材料进入灯室",
                    "when": {"all": [{"objective_done": "gather"}, {"flag": "in_shrine"}]},
                },
            ],
        },
        "endings": [
            {
                "key": "lit",
                "kind": "win",
                "title": "灯亮",
                "text": "灯芯跳了一下。",
                "priority": 10,
                "when": {"all": [{"objective_done": "light"}, {"flag": "keeper_briefed"}]},
            },
            {
                "key": "empty_handed",
                "kind": "lose",
                "title": "空手",
                "text": "灯守摇了摇头。",
                "priority": 6,
                "when": {
                    "all": [
                        {"turn_gte": 8},
                        {"not": {"flag": "supplies_ready"}},
                        {"npc_in_room": {"npc": "keeper", "room": "hall"}},
                    ]
                },
            },
            {
                "key": "fallen",
                "kind": "lose",
                "title": "倒下",
                "text": "你倒在石台边。",
                "priority": 1,
                "when": {"hp_lte": 0},
            },
        ],
        "suggestions": [
            {"when": {"flag": "oil_waiting"}, "text": "把灯油拿起来"},
            {"when": {"flag": "keeper_at_hall"}, "text": "和灯守聊聊"},
        ],
    }


def _v2_parsed(definition: dict[str, Any] | None = None):
    """解析 v2 定义并断言成功（测试辅助）。"""
    parsed = parse_definition(
        json.dumps(definition if definition is not None else _v2(), ensure_ascii=False)
    )
    assert parsed.ok, parsed.errors
    assert parsed.definition is not None
    return parsed.definition


def _v2_bundle(
    definition: dict[str, Any] | None = None,
) -> tuple[Any, ScenarioIds, Any, UserScenarioHooks]:
    """v2 定义 → (definition, ids, 初始状态, 钩子)。"""
    parsed_definition = _v2_parsed(definition)
    ids = derive_ids(_V2_SCENARIO_ID, parsed_definition)
    state = build_initial_state(parsed_definition, ids, save_seed="save-v2")
    return parsed_definition, ids, state, UserScenarioHooks(parsed_definition, ids)


def _v2_eval(payload: dict[str, Any], state: Any, definition: Any, ids: ScenarioIds) -> bool:
    """在 v2 定义上下文里求值一个条件（测试辅助）。"""
    return evaluate_condition(_condition(payload), state, ids, definition=definition)


def _move_player(state: Any, ids: ScenarioIds, room_key: str):
    """移动玩家到指定房间（测试辅助；等价于 CHARACTER_MOVED 的后置状态）。"""
    player = state.characters[PLAYER_ID].model_copy(update={"location": ids.rooms[room_key]})
    return state.model_copy(update={"characters": {PLAYER_ID: player}})


def _take_item(state: Any, ids: ScenarioIds, item_key: str, room_key: str):
    """模拟 ITEM_TAKEN 后置状态：物品从房间移到玩家行囊（测试辅助）。"""
    room = state.rooms[ids.rooms[room_key]]
    updated_room = room.model_copy(
        update={
            "item_ids": [existing for existing in room.item_ids if existing != ids.items[item_key]]
        }
    )
    player = state.characters[PLAYER_ID].model_copy(
        update={"inventory": [*state.characters[PLAYER_ID].inventory, ids.items[item_key]]}
    )
    return state.model_copy(
        update={
            "characters": {PLAYER_ID: player},
            "rooms": {**state.rooms, ids.rooms[room_key]: updated_room},
        }
    )


def _set_npc_state(state: Any, ids: ScenarioIds, npc_key: str, dialogue_state: str):
    """模拟 DIALOGUE_EXCHANGED 后置状态（测试辅助）。"""
    npc = state.npcs[ids.npcs[npc_key]].model_copy(update={"dialogue_state": dialogue_state})
    return state.model_copy(update={"npcs": {**state.npcs, ids.npcs[npc_key]: npc}})


def test_v2_parse_ok_and_contract_unchanged() -> None:
    """v2 解析成功；summary 仍是冻结的 6 键；flags 进入定义模型。"""
    parsed_definition, _ids, _state, _ = _v2_bundle()
    assert parsed_definition.schema_version == 2
    assert [flag.key for flag in parsed_definition.flags] == [
        "keeper_briefed",
        "oil_waiting",
        "in_shrine",
        "keeper_at_hall",
        "supplies_ready",
    ]
    parsed = parse_definition(json.dumps(_v2(), ensure_ascii=False))
    assert set(parsed.summary) == {
        "rooms",
        "items",
        "npcs",
        "objectives",
        "endings",
        "suggestions",
    }
    assert parsed.warnings == ()


def test_v2_flag_evaluates_declared_condition() -> None:
    """flag 是读时派生命题：npc dialogue_state 变化前后取值翻转。"""
    definition, ids, state, _ = _v2_bundle()
    assert _v2_eval({"flag": "keeper_briefed"}, state, definition, ids) is False
    briefed = _set_npc_state(state, ids, "keeper", "briefed")
    assert _v2_eval({"flag": "keeper_briefed"}, briefed, definition, ids) is True


def test_v2_flag_can_reference_flag() -> None:
    """flag 可引用 flag（声明顺序无关；求值按依赖递归）。"""
    definition = _v2()
    definition["flags"].append(
        {"key": "all_ready", "when": {"all": [{"flag": "keeper_briefed"}, {"flag": "in_shrine"}]}}
    )
    definition["suggestions"].append({"when": {"flag": "all_ready"}, "text": "可以点灯了"})
    parsed_definition, ids, state, _ = _v2_bundle(definition)
    assert _v2_eval({"flag": "all_ready"}, state, parsed_definition, ids) is False
    progressed = _set_npc_state(_move_player(state, ids, "shrine"), ids, "keeper", "briefed")
    assert _v2_eval({"flag": "all_ready"}, progressed, parsed_definition, ids) is True


def test_v2_unknown_flag_reference_rejected() -> None:
    """引用未声明 flag → 结构化错误，路径定位到 when.flag。"""
    definition = _v2()
    definition["goal"]["objectives"][0]["when"] = {"flag": "ghost_flag"}
    parsed = parse_definition(json.dumps(definition, ensure_ascii=False))
    assert not parsed.ok
    assert any(
        error.path == "goal.objectives[0].when.flag" and "ghost_flag" in error.message
        for error in parsed.errors
    )


def test_v2_unknown_objective_reference_rejected() -> None:
    """objective_done 引用不存在目标 → 结构化错误。"""
    definition = _v2()
    definition["goal"]["objectives"][2]["when"] = {"objective_done": "ghost"}
    parsed = parse_definition(json.dumps(definition, ensure_ascii=False))
    assert not parsed.ok
    assert any(error.path == "goal.objectives[2].when.objective_done" for error in parsed.errors)
    assert any("ghost" in error.message for error in parsed.errors)


def test_v2_flag_cycle_rejected() -> None:
    """flag 互相引用成环 → 校验期拒绝（不能运行期死循环）。"""
    definition = _v2()
    definition["flags"] = [
        {"key": "a", "when": {"flag": "b"}},
        {"key": "b", "when": {"flag": "a"}},
    ]
    parsed = parse_definition(json.dumps(definition, ensure_ascii=False))
    assert not parsed.ok
    assert any("循环引用" in error.message for error in parsed.errors)
    assert any(error.path.startswith("flags[") for error in parsed.errors)


def test_v2_flag_objective_cycle_rejected() -> None:
    """flag 与目标进度跨类型成环 → 统一依赖图检出。"""
    definition = _v2()
    definition["flags"] = [{"key": "loop", "when": {"objective_done": "listen"}}]
    definition["goal"]["objectives"][0]["when"] = {"flag": "loop"}
    parsed = parse_definition(json.dumps(definition, ensure_ascii=False))
    assert not parsed.ok
    assert any("循环引用" in error.message for error in parsed.errors)


def test_v2_duplicate_flag_key_rejected() -> None:
    """flag key 重复 → 阻断错误。"""
    definition = _v2()
    definition["flags"].append({"key": "keeper_briefed", "when": {"always": True}})
    parsed = parse_definition(json.dumps(definition, ensure_ascii=False))
    assert not parsed.ok
    assert any(error.path == "flags.key" for error in parsed.errors)


def test_v2_objective_done_evaluates_target_progress() -> None:
    """objective_done = 被引用目标的 when 求值（目标链）；未完成目标返回 False。"""
    definition, ids, state, _ = _v2_bundle()
    condition = {"objective_done": "listen"}
    assert _v2_eval(condition, state, definition, ids) is False
    briefed = _set_npc_state(state, ids, "keeper", "briefed")
    assert _v2_eval(condition, briefed, definition, ids) is True
    assert _v2_eval({"objective_done": "gather"}, briefed, definition, ids) is False


def test_v2_goal_panel_uses_objective_chain() -> None:
    """目标面板：gather 完成且进入灯室后 light 才完成（objective_done 链）。"""
    _definition, ids, state, hooks = _v2_bundle()
    assert hooks.goal(state) is not None
    assert [item["done"] for item in hooks.goal(state)["objectives"]] == [False, False, False]

    progressed = _set_npc_state(state, ids, "keeper", "briefed")
    assert [item["done"] for item in hooks.goal(progressed)["objectives"]] == [True, False, False]

    gathered = _take_item(_take_item(progressed, ids, "candle", "hall"), ids, "oil", "shrine")
    assert [item["done"] for item in hooks.goal(gathered)["objectives"]] == [True, True, False]

    in_shrine = _move_player(gathered, ids, "shrine")
    assert [item["done"] for item in hooks.goal(in_shrine)["objectives"]] == [True, True, True]


def test_v2_has_item_tag_count_threshold() -> None:
    """收集类条件：持有 tag=quest 的物品数量达到阈值（含边界）。"""
    definition, ids, state, _ = _v2_bundle()
    condition = {"has_item_tag_count": {"tag": "quest", "at_least": 2}}
    assert _v2_eval(condition, state, definition, ids) is False
    one = _take_item(state, ids, "candle", "hall")
    assert _v2_eval(condition, one, definition, ids) is False
    two = _take_item(one, ids, "oil", "shrine")
    assert _v2_eval(condition, two, definition, ids) is True
    exact = {"has_item_tag_count": {"tag": "quest", "at_least": 1}}
    assert _v2_eval(exact, one, definition, ids) is True
    tool = {"has_item_tag_count": {"tag": "tool", "at_least": 1}}
    flint = _take_item(one, ids, "flint", "shrine")
    assert _v2_eval(tool, flint, definition, ids) is True


def test_v2_item_in_room_tracks_item_location() -> None:
    """item_in_room：物品在房间时为真，被拿走后为假。"""
    definition, ids, state, _ = _v2_bundle()
    condition = {"item_in_room": {"item": "oil", "room": "shrine"}}
    assert _v2_eval(condition, state, definition, ids) is True
    taken = _take_item(state, ids, "oil", "shrine")
    assert _v2_eval(condition, taken, definition, ids) is False


def test_v2_item_in_room_unknown_reference_rejected() -> None:
    """item_in_room 的 item/room 引用不存在 → 两条结构化错误带子路径。"""
    definition = _v2()
    definition["endings"][0]["when"] = {"item_in_room": {"item": "ghost", "room": "nowhere"}}
    parsed = parse_definition(json.dumps(definition, ensure_ascii=False))
    assert not parsed.ok
    paths = {error.path for error in parsed.errors}
    assert "endings[0].when.item_in_room.item" in paths
    assert "endings[0].when.item_in_room.room" in paths


def test_v2_npc_in_room_atom() -> None:
    """npc_in_room：按派生 ID 比较 NPC 当前房间。"""
    definition, ids, state, _ = _v2_bundle()
    assert _v2_eval({"npc_in_room": {"npc": "keeper", "room": "hall"}}, state, definition, ids)
    assert not _v2_eval(
        {"npc_in_room": {"npc": "keeper", "room": "shrine"}}, state, definition, ids
    )


def test_v2_npc_in_room_unknown_reference_rejected() -> None:
    """npc_in_room 的 npc/room 引用不存在 → 结构化错误。"""
    definition = _v2()
    definition["endings"][0]["when"] = {"npc_in_room": {"npc": "ghost", "room": "nowhere"}}
    parsed = parse_definition(json.dumps(definition, ensure_ascii=False))
    assert not parsed.ok
    paths = {error.path for error in parsed.errors}
    assert "endings[0].when.npc_in_room.npc" in paths
    assert "endings[0].when.npc_in_room.room" in paths


def test_v2_time_conditions_boundaries() -> None:
    """时间条件：turn / world_time 的 gte/lte 都是含边界比较。"""
    definition, ids, state, _ = _v2_bundle()
    timed = state.model_copy(update={"turn": 5, "world_time": 4})
    assert _v2_eval({"turn_gte": 5}, timed, definition, ids) is True
    assert _v2_eval({"turn_gte": 6}, timed, definition, ids) is False
    assert _v2_eval({"turn_lte": 5}, timed, definition, ids) is True
    assert _v2_eval({"turn_lte": 4}, timed, definition, ids) is False
    assert _v2_eval({"world_time_gte": 4}, timed, definition, ids) is True
    assert _v2_eval({"world_time_lte": 4}, timed, definition, ids) is True
    assert _v2_eval({"world_time_lte": 3}, timed, definition, ids) is False
    # 初始状态：turn=1、world_time=0
    assert _v2_eval({"turn_gte": 1}, state, definition, ids) is True
    assert _v2_eval({"world_time_gte": 0}, state, definition, ids) is True


def test_v2_time_condition_bounds_rejected() -> None:
    """时间条件字段有下界（turn≥1、world_time≥0）与上限，越界 → 字段错误。"""
    definition = _v2()
    definition["endings"][0]["when"] = {"turn_gte": 0}
    parsed = parse_definition(json.dumps(definition, ensure_ascii=False))
    assert not parsed.ok
    assert any("endings[0].when.turn_gte" in error.path for error in parsed.errors)

    definition = _v2()
    definition["endings"][0]["when"] = {"world_time_gte": -1}
    parsed = parse_definition(json.dumps(definition, ensure_ascii=False))
    assert not parsed.ok
    assert any("endings[0].when.world_time_gte" in error.path for error in parsed.errors)


def test_v2_combined_conditions_mix_new_atoms() -> None:
    """组合子可与 v2 原子混用（all/any/not + flag/计数/NPC/时间）。"""
    definition, ids, state, _ = _v2_bundle()
    progressed = _set_npc_state(
        _take_item(_move_player(state, ids, "hall"), ids, "candle", "hall"),
        ids,
        "keeper",
        "briefed",
    )
    condition = {
        "all": [
            {"flag": "keeper_briefed"},
            {"not": {"item_in_room": {"item": "candle", "room": "hall"}}},
            {
                "any": [
                    {"turn_gte": 99},
                    {"has_item_tag_count": {"tag": "quest", "at_least": 1}},
                ]
            },
        ]
    }
    assert _v2_eval(condition, progressed, definition, ids) is True
    assert _v2_eval(condition, state, definition, ids) is False


def test_v2_version_gate_rejects_flags_for_v1() -> None:
    """schema_version=1 + flags → 版本门禁错误（不静默升级）。"""
    definition = _v2()
    definition["schema_version"] = 1
    parsed = parse_definition(json.dumps(definition, ensure_ascii=False))
    assert not parsed.ok
    assert any(error.path == "flags" for error in parsed.errors)
    assert any("schema_version=2" in error.message for error in parsed.errors)


@pytest.mark.parametrize(
    ("condition", "predicate"),
    [
        ({"flag": "keeper_briefed"}, "flag"),
        ({"objective_done": "listen"}, "objective_done"),
        ({"has_item_tag_count": {"tag": "quest", "at_least": 1}}, "has_item_tag_count"),
        ({"item_in_room": {"item": "oil", "room": "shrine"}}, "item_in_room"),
        ({"npc_in_room": {"npc": "keeper", "room": "hall"}}, "npc_in_room"),
        ({"turn_gte": 2}, "turn_gte"),
        ({"turn_lte": 9}, "turn_lte"),
        ({"world_time_gte": 1}, "world_time_gte"),
        ({"world_time_lte": 9}, "world_time_lte"),
    ],
)
def test_v2_version_gate_rejects_each_v2_predicate_for_v1(
    condition: dict[str, Any], predicate: str
) -> None:
    """v1 声明使用任一 v2 谓词 → 精确路径 + 「需要 schema_version=2」提示。"""
    definition = _v2()
    definition["schema_version"] = 1
    definition["suggestions"][0]["when"] = condition
    parsed = parse_definition(json.dumps(definition, ensure_ascii=False))
    assert not parsed.ok
    expected_path = f"suggestions[0].when.{predicate}"
    assert any(error.path == expected_path for error in parsed.errors), parsed.errors
    assert any("需要 schema_version=2" in error.message for error in parsed.errors)


def test_v2_unknown_predicate_structured_error() -> None:
    """未知谓词（v2 包内）→ 结构化错误带路径，消息提示白名单。"""
    definition = _v2()
    definition["endings"][0]["when"] = {"teleport": "shrine"}
    parsed = parse_definition(json.dumps(definition, ensure_ascii=False))
    assert not parsed.ok
    assert any(error.path == "endings[0].when.teleport" for error in parsed.errors)
    assert any("未知谓词" in error.message for error in parsed.errors)


def test_v2_unknown_condition_field_extra_forbid() -> None:
    """条件节点多余字段 → extra=forbid 结构化错误（路径带字段名）。"""
    definition = _v2()
    definition["endings"][0]["when"] = {"flag": "keeper_briefed", "typo": 1}
    parsed = parse_definition(json.dumps(definition, ensure_ascii=False))
    assert not parsed.ok
    assert any(error.path == "endings[0].when.typo" for error in parsed.errors)
    assert any("未知谓词" in error.message for error in parsed.errors)


def test_v2_condition_single_key_with_new_atoms() -> None:
    """v2 原子同样遵守「一个条件恰好一个键」。"""
    with pytest.raises(ValueError, match="只能包含一个键"):
        Condition.model_validate({"flag": "keeper_briefed", "turn_gte": 1})
    with pytest.raises(ValueError, match="只能包含一个键"):
        Condition.model_validate({"not": {"flag": "keeper_briefed"}, "always": True})


def test_v2_unsupported_schema_version_structured_error() -> None:
    """schema_version 只接受整数 1/2；3 与字符串都给出 path=schema_version 的错误。"""
    for value in (3, "2", True, None):
        definition = _v2()
        definition["schema_version"] = value
        parsed = parse_definition(json.dumps(definition, ensure_ascii=False))
        assert not parsed.ok
        assert any(error.path == "schema_version" for error in parsed.errors)
        assert any("schema_version" in error.message for error in parsed.errors)


def test_v2_multiple_endings_priority_order() -> None:
    """多个结局同时满足：priority 高者胜（与 v1 语义一致，v2 谓词参与判断）。"""
    definition = _v2()
    definition["endings"] = [
        {
            "key": "low",
            "kind": "lose",
            "title": "低",
            "text": "低。",
            "priority": 1,
            "when": {"always": True},
        },
        {
            "key": "high",
            "kind": "win",
            "title": "高",
            "text": "高。",
            "priority": 20,
            "when": {"flag": "keeper_briefed"},
        },
    ]
    _parsed_definition, ids, state, hooks = _v2_bundle(definition)
    briefed = _set_npc_state(state, ids, "keeper", "briefed")
    ending = hooks.ending(briefed)
    assert ending is not None
    assert ending["ending_id"] == "ending_lantern-vigil_high"
    # 未满足时回退到 always 的结局
    fallback = hooks.ending(state)
    assert fallback is not None
    assert fallback["ending_id"] == "ending_lantern-vigil_low"


def test_v2_multiple_endings_tie_declaration_order() -> None:
    """同 priority 多结局：按声明顺序取第一个满足项（顺序稳定、可回放）。"""
    definition = _v2()
    definition["endings"] = [
        {
            "key": "first",
            "kind": "win",
            "title": "一",
            "text": "一。",
            "priority": 5,
            "when": {"always": True},
        },
        {
            "key": "second",
            "kind": "win",
            "title": "二",
            "text": "二。",
            "priority": 5,
            "when": {"always": True},
        },
    ]
    _, _, state, hooks = _v2_bundle(definition)
    ending = hooks.ending(state)
    assert ending is not None
    assert ending["ending_id"] == "ending_lantern-vigil_first"


def test_v2_ending_choice_driven_by_player_state() -> None:
    """「选择」由玩家动作造成的状态差异表达：留蜡烛=蜡烛结局，留灯油=灯油结局。"""
    definition = _v2()
    definition["endings"] = [
        {
            "key": "candle_path",
            "kind": "win",
            "title": "烛",
            "text": "烛。",
            "priority": 10,
            "when": {"has_item": "candle"},
        },
        {
            "key": "oil_path",
            "kind": "win",
            "title": "油",
            "text": "油。",
            "priority": 10,
            "when": {"has_item": "oil"},
        },
    ]
    _parsed_definition, ids, state, hooks = _v2_bundle(definition)
    with_candle = _take_item(state, ids, "candle", "hall")
    assert hooks.ending(with_candle) is not None
    assert hooks.ending(with_candle)["ending_id"] == "ending_lantern-vigil_candle_path"
    with_oil = _take_item(state, ids, "oil", "shrine")
    assert hooks.ending(with_oil)["ending_id"] == "ending_lantern-vigil_oil_path"


def test_v2_ending_uses_flag_and_objective_chain() -> None:
    """显式结局可用 flag/目标进度链：只有 light 完成才触发胜利。"""
    _definition, ids, state, hooks = _v2_bundle()
    assert hooks.ending(state) is None
    progressed = _set_npc_state(state, ids, "keeper", "briefed")
    assert hooks.ending(progressed) is None  # flag 成立但 light 未完成
    gathered = _take_item(_take_item(progressed, ids, "candle", "hall"), ids, "oil", "shrine")
    in_shrine = _move_player(gathered, ids, "shrine")
    ending = hooks.ending(in_shrine)
    assert ending is not None
    assert ending["ending_id"] == "ending_lantern-vigil_lit"
    assert ending["kind"] == "win"


def test_v2_fallback_endings_unchanged() -> None:
    """v2 未写 endings 时沿用 v1 兜底：目标全完成=胜利；HP≤0=倒下；完成优先于倒下。"""
    definition = _v2()
    definition["endings"] = []
    _parsed_definition, ids, state, hooks = _v2_bundle(definition)
    completed = _move_player(
        _take_item(
            _take_item(_set_npc_state(state, ids, "keeper", "briefed"), ids, "candle", "hall"),
            ids,
            "oil",
            "shrine",
        ),
        ids,
        "shrine",
    )
    ending = hooks.ending(completed)
    assert ending is not None
    assert ending["ending_id"] == "ending_lantern-vigil_complete"
    assert ending["kind"] == "win"

    player = state.characters[PLAYER_ID].model_copy(update={"hp": 0})
    fallen_state = state.model_copy(update={"characters": {PLAYER_ID: player}})
    fallen = hooks.ending(fallen_state)
    assert fallen is not None
    assert fallen["ending_id"] == "ending_lantern-vigil_fallen"
    assert fallen["kind"] == "lose"

    # 同状态既完成目标又 HP=0：v1 口径「完成」优先（v2 不改兜底顺序）。
    both = completed.model_copy(
        update={
            "characters": {PLAYER_ID: completed.characters[PLAYER_ID].model_copy(update={"hp": 0})}
        }
    )
    both_ending = hooks.ending(both)
    assert both_ending is not None
    assert both_ending["ending_id"] == "ending_lantern-vigil_complete"


def test_v2_suggestions_conditions_validated() -> None:
    """suggestions[].when 与目标/结局同口径做静态校验（引用不存在的 flag）。"""
    definition = _v2()
    definition["suggestions"][0]["when"] = {"flag": "ghost"}
    parsed = parse_definition(json.dumps(definition, ensure_ascii=False))
    assert not parsed.ok
    assert any(error.path == "suggestions[0].when.flag" for error in parsed.errors)


def test_v2_unused_flag_warning() -> None:
    """声明但未被任何条件引用的 flag → warning（不阻断）。"""
    definition = _v2()
    definition["flags"].append({"key": "spare", "when": {"always": True}})
    parsed = parse_definition(json.dumps(definition, ensure_ascii=False))
    assert parsed.ok, parsed.errors
    assert any(
        warning.path == "flags[5]" and "spare" in warning.message for warning in parsed.warnings
    )


def test_v2_evaluation_deterministic_and_state_immutable() -> None:
    """求值纯函数：同状态重复求值结果一致，且不修改入参状态（回放/state_hash 安全）。"""
    definition, ids, state, hooks = _v2_bundle()
    progressed = _set_npc_state(
        _take_item(_take_item(state, ids, "candle", "hall"), ids, "oil", "shrine"),
        ids,
        "keeper",
        "briefed",
    )
    before = progressed.model_dump()
    condition = _condition({"all": [{"flag": "keeper_briefed"}, {"flag": "supplies_ready"}]})
    first = evaluate_condition(condition, progressed, ids, definition=definition)
    second = evaluate_condition(condition, progressed, ids, definition=definition)
    assert first is True and second is True
    assert progressed.model_dump() == before
    assert hooks.ending(progressed) == hooks.ending(progressed)
    assert progressed.model_dump() == before


def test_v2_reader_keeps_v1_definition_unchanged() -> None:
    """v2 读取器读 v1 文件：schema_version 保留 1、flags 为空、行为与 v1 逐字一致。"""
    parsed = _parsed(_tiny())
    assert parsed.definition is not None
    assert parsed.definition.schema_version == 1
    assert parsed.definition.flags == []
    ids = derive_ids(_V2_SCENARIO_ID, parsed.definition)
    state = build_initial_state(parsed.definition, ids, save_seed="save-v2")
    # v1 条件无需 definition 上下文也照常求值（旧调用签名兼容）。
    assert evaluate_condition(_condition({"in_room": "gate"}), state, ids) is True
    assert evaluate_condition(_condition({"in_room": "hall"}), state, ids) is False


def test_v2_flags_max_length_enforced() -> None:
    """flags 上限 16（超过 → 字段错误，避免条件图膨胀）。"""
    definition = _v2()
    definition["flags"] = [
        {"key": f"flag_{index}", "when": {"always": True}} for index in range(17)
    ]
    definition["suggestions"] = [{"when": {"flag": "flag_0"}, "text": "x"}]
    parsed = parse_definition(json.dumps(definition, ensure_ascii=False))
    assert not parsed.ok
    assert any(error.path == "flags" for error in parsed.errors)


def test_v2_has_item_tag_count_at_least_boundary_rejected() -> None:
    """has_item_tag_count.at_least 必须 ≥1（0 无意义，负值/0 → 字段错误）。"""
    definition = _v2()
    definition["endings"][0]["when"] = {"has_item_tag_count": {"tag": "quest", "at_least": 0}}
    parsed = parse_definition(json.dumps(definition, ensure_ascii=False))
    assert not parsed.ok
    assert any("at_least" in error.path for error in parsed.errors)


def test_v2_objective_self_cycle_rejected() -> None:
    """目标进度自引用（objective 引用自身）→ 循环引用错误。"""
    definition = _v2()
    definition["goal"]["objectives"][0]["when"] = {"objective_done": "listen"}
    parsed = parse_definition(json.dumps(definition, ensure_ascii=False))
    assert not parsed.ok
    assert any("循环引用" in error.message for error in parsed.errors)


def test_v2_npc_state_v1_predicate_works_in_v2() -> None:
    """v1 npc_state 谓词在 v2 定义中语义不变（v2 是加法扩展）。"""
    definition, ids, state, _ = _v2_bundle()
    condition = {"npc_state": {"npc": "keeper", "state": "briefed"}}
    assert _v2_eval(condition, state, definition, ids) is False
    assert _v2_eval(condition, _set_npc_state(state, ids, "keeper", "briefed"), definition, ids)


# ---------------------------------------------------------------------------
# QA P2-7：v2 条件常量/字段严格校验（拒绝 Pydantic lax 强转；v1 兼容）
# ---------------------------------------------------------------------------
def _v2_with_ending_when(payload: dict[str, Any]) -> dict[str, Any]:
    """把 v2 第一个结局的 ``when`` 换成给定条件（解析错误路径断言用）。"""
    definition = _v2()
    definition["endings"][0]["when"] = payload
    return definition


@pytest.mark.parametrize(
    ("payload", "path"),
    [
        ({"always": "true"}, "endings[0].when.always"),
        ({"always": "false"}, "endings[0].when.always"),
        ({"turn_gte": "3"}, "endings[0].when.turn_gte"),
        ({"turn_lte": "9"}, "endings[0].when.turn_lte"),
        ({"world_time_gte": "1"}, "endings[0].when.world_time_gte"),
        ({"world_time_lte": "2"}, "endings[0].when.world_time_lte"),
        (
            {"has_item_tag_count": {"tag": "quest", "at_least": "2"}},
            "endings[0].when.has_item_tag_count.at_least",
        ),
    ],
    ids=[
        "always-true-string",
        "always-false-string",
        "turn-gte-string",
        "turn-lte-string",
        "world-time-gte-string",
        "world-time-lte-string",
        "at-least-string",
    ],
)
def test_p2_7_v2_condition_fields_reject_lax_coercion(payload: dict[str, Any], path: str) -> None:
    """v2 常量/数值字段不接受字符串强转（``"true"``/``"3"`` 等 → 结构化错误带路径）。"""
    parsed = parse_definition(json.dumps(_v2_with_ending_when(payload), ensure_ascii=False))

    assert parsed.ok is False
    assert any(error.path == path for error in parsed.errors), [e.path for e in parsed.errors]


def test_p2_7_condition_strict_at_model_level_but_v1_lax_unchanged() -> None:
    """模型层：v2 字段/``always`` 严格；v1 条件（hp_lte 等）lax 语义保持冻结。"""
    with pytest.raises(ValueError):
        Condition.model_validate({"always": "true"})
    with pytest.raises(ValueError):
        Condition.model_validate({"turn_gte": "3"})
    # v1 字段读法不变：数字字符串仍按旧行为转换，避免破坏既有 v1 文件。
    assert Condition.model_validate({"hp_lte": "5"}).hp_lte == 5
    assert Condition.model_validate({"always": True}).always is True


def test_p2_7_valid_v2_int_and_bool_conditions_still_parse() -> None:
    """真正的整数/布尔字面量不受影响：合法 v2 定义照常导入。"""
    definition = _v2_with_ending_when({"all": [{"turn_gte": 2}, {"always": True}]})

    parsed = parse_definition(json.dumps(definition, ensure_ascii=False))

    assert parsed.ok is True, parsed.errors


def test_p2_7_v1_definition_with_bool_constant_still_parses() -> None:
    """v1 兼容：``{"always": true}`` 是 v1/v2 共用组合子，布尔字面量照常接受。"""
    definition = _tiny()
    definition["endings"][0]["when"] = {"always": True}

    parsed = parse_definition(json.dumps(definition, ensure_ascii=False))

    assert parsed.ok is True, parsed.errors
