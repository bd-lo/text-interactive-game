"""``ScenarioRuntime`` 单测：默认空表 = 旧行为；注册后按房间/NPC 分发用户场景钩子。"""

from __future__ import annotations

import json
from typing import Any

from textgame.domain.models import GameState
from textgame.domain.scenario import build_initial_state as build_m2_state
from textgame.domain.scenario_runtime import ScenarioRuntime
from textgame.domain.user_scenario import (
    UserScenarioHooks,
    build_initial_state,
    derive_ids,
    parse_definition,
)


def _definition(slug: str, *, title: str) -> dict[str, Any]:
    """构造一份单房间定义（测试辅助）。"""
    return {
        "schema_version": 1,
        "slug": slug,
        "meta": {"title": title},
        "player": {"location": "hall"},
        "rooms": [
            {"key": "hall", "name": "厅", "description": "一间空厅。"},
        ],
        "items": [],
        "npcs": [],
        "goal": {
            "title": "走到厅里",
            "objectives": [{"key": "here", "text": "在厅里", "when": {"in_room": "hall"}}],
        },
        "endings": [
            {
                "key": "win",
                "kind": "win",
                "title": "完成",
                "text": "你到了。",
                "when": {"in_room": "hall"},
            }
        ],
    }


def _hooks(slug: str, scenario_id: str, *, title: str) -> tuple[UserScenarioHooks, GameState]:
    """解析定义 → 建局，返回钩子与初始状态（测试辅助）。"""
    parsed = parse_definition(json.dumps(_definition(slug, title=title), ensure_ascii=False))
    assert parsed.definition is not None
    ids = derive_ids(scenario_id, parsed.definition)
    state = build_initial_state(parsed.definition, ids, save_seed="save-42")
    return UserScenarioHooks(parsed.definition, ids), state


def test_empty_runtime_keeps_builtin_behavior() -> None:
    """未注册任何用户场景：所有分发返回 None/False，旧场景不变。"""
    runtime = ScenarioRuntime()
    m2_state = build_m2_state(save_seed="save-42")

    assert len(runtime) == 0
    assert runtime.resolve(m2_state) is None
    assert runtime.goal(m2_state) is None
    assert runtime.ending(m2_state) is None
    assert runtime.scenario_id(m2_state) is None
    assert runtime.hooks_for_room("room_01J9Z3K4Q7X8Y9Z0ABCDEFGHJK") is None
    assert runtime.hooks_for_npc("npc_01J9Z3K4Q7X8Y9Z0ABCDEFGHJK") is None
    assert runtime.room_damage("room_01J9Z3K4Q7X8Y9Z0ABCDEFGHJK") is None
    assert runtime.npc_reply("npc_01J9Z3K4Q7X8Y9Z0ABCDEFGHJK", "你好") is None


def test_register_and_resolve_by_room_id() -> None:
    """注册后：该场景的状态能解析回钩子；内置 m2 状态仍解析为 None。"""
    hooks, state = _hooks("room-hall", "scn_01J9Z3K4Q7X8Y9Z0ABCDEFGHJ0", title="单间")
    runtime = ScenarioRuntime([hooks])

    assert len(runtime) == 1
    assert runtime.resolve(state) is hooks
    assert runtime.scenario_id(state) == "scn_01J9Z3K4Q7X8Y9Z0ABCDEFGHJ0"
    assert runtime.room_damage(next(iter(hooks.room_ids))) is None
    assert runtime.resolve(build_m2_state(save_seed="save-42")) is None


def test_goal_ending_and_npc_dispatch() -> None:
    """目标/结局分发到注册场景；未注册状态返回 None。"""
    hooks, state = _hooks("room-hall", "scn_01J9Z3K4Q7X8Y9Z0ABCDEFGHJ0", title="单间")
    runtime = ScenarioRuntime([hooks])

    assert runtime.goal(state) is not None
    ending = runtime.ending(state)
    assert ending is not None
    assert ending["ending_id"] == "ending_room-hall_win"
    assert runtime.goal(build_m2_state(save_seed="save-42")) is None


def test_register_same_scenario_replaces() -> None:
    """同一 scenario_id 重复注册 → 以新对象为准，数量不增加。"""
    hooks, state = _hooks("room-hall", "scn_01J9Z3K4Q7X8Y9Z0ABCDEFGHJ0", title="旧标题")
    runtime = ScenarioRuntime([hooks])
    replacement, _ = _hooks("room-hall", "scn_01J9Z3K4Q7X8Y9Z0ABCDEFGHJ0", title="新标题")

    runtime.register(replacement)
    assert len(runtime) == 1
    assert runtime.resolve(state) is replacement


def test_multiple_scenarios_resolve_independently() -> None:
    """两个场景各自解析到自己的钩子，房间索引互不干扰。"""
    first, first_state = _hooks("room-hall-a", "scn_01J9Z3K4Q7X8Y9Z0ABCDEFGHJ0", title="A")
    second, second_state = _hooks("room-hall-b", "scn_01J9Z3K4Q7X8Y9Z0ABCDEFGHJ1", title="B")
    runtime = ScenarioRuntime([first, second])

    assert len(runtime) == 2
    assert runtime.resolve(first_state) is first
    assert runtime.resolve(second_state) is second
    for room_id in first.room_ids:
        assert runtime.hooks_for_room(room_id) is first
    for room_id in second.room_ids:
        assert runtime.hooks_for_room(room_id) is second


def test_clear_restores_empty() -> None:
    """``clear`` 后回到默认空表行为（测试隔离用）。"""
    hooks, state = _hooks("room-hall", "scn_01J9Z3K4Q7X8Y9Z0ABCDEFGHJ0", title="单间")
    runtime = ScenarioRuntime([hooks])
    runtime.clear()

    assert len(runtime) == 0
    assert runtime.resolve(state) is None
    assert runtime.goal(state) is None
