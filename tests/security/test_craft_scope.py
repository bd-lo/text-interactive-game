"""安全测试：M4 craft_item 的 actor_scope 矩阵、Schema 攻击与越权零写入（第 7 章 §7.3-§7.5）。

覆盖：craft_item 矩阵行（player/system/npc:blacksmith 可见，默认 npc/npc:scavenger 不可见）、
M2 注册表不回归、默认 NPC 越权拒绝、额外字段/超长 free_description/非法 mode/缺 recipe_id
的 Schema 拒绝、未知配方与自由制造关闭的零事件。所有失败必须零事件。
"""

from __future__ import annotations

from typing import Any

from textgame.domain.results import ErrorCode
from textgame.domain.scenario import PLAYER_ID
from textgame.orchestration.turn_orchestrator import CreateGameRequest
from textgame.services.action_service import ActionService
from textgame.services.query_service import QueryService
from textgame.services.tool_context import ToolContext
from textgame.tools.builtin import build_game_tool_registry
from textgame.tools.builtin.m2 import build_m2_tool_registry
from textgame.tools.gate import ToolGate

_CRAFT_ARGS: dict[str, Any] = {"mode": "recipe", "recipe_id": "recipe.forge_sword"}


def _create_state(harness: Any, *, key: str):
    view = harness.orchestrator.create_game(
        CreateGameRequest(seed=0, idempotency_key=key, scenario="m4")
    )
    record = harness.repo.load(view.game_id)
    assert record is not None
    return record.state


def _gate(harness: Any) -> ToolGate:
    query = QueryService(harness.repo)
    action = ActionService(query)
    return ToolGate(
        registry=build_game_tool_registry(),
        query=query,
        action=action,
        craft=harness.craft,
    )


def _ctx(*, actor_id: str, tags: set[str], turn_id: int) -> ToolContext:
    return ToolContext(
        actor_id=actor_id,
        actor_tags=frozenset(tags),
        turn_id=turn_id,
        save_seed="save-0",
        idempotency_key="security-craft-0001",
        caller="player",
    )


def test_craft_item_visibility_matrix_row() -> None:
    registry = build_game_tool_registry()

    def visible(actor_id: str, tags: set[str]) -> set[str]:
        return {spec.name for spec in registry.visible_tools(actor_id, frozenset(tags))}

    assert "craft_item" in visible("player", {"player"})
    assert "craft_item" in visible("system", {"system"})
    assert "craft_item" in visible("npc_1", {"npc", "npc:blacksmith"})
    assert "craft_item" not in visible("npc_1", {"npc"})
    assert "craft_item" not in visible("npc_1", {"npc", "npc:scavenger"})
    # M2 注册表保持六工具，旧安全用例不回归（第 7 章 §7.3 矩阵扩展仅限 M4 完整注册表）。
    m2_names = {
        spec.name
        for spec in build_m2_tool_registry().visible_tools("player", frozenset({"player"}))
    }
    assert "craft_item" not in m2_names


def test_default_npc_cannot_dispatch_craft_item(m4_game_harness: Any) -> None:
    state = _create_state(m4_game_harness, key="sec-create-0001")
    gate = _gate(m4_game_harness)
    ctx = _ctx(actor_id="npc_1", tags={"npc"}, turn_id=state.turn + 1)

    result = gate.dispatch(tool="craft_item", args=dict(_CRAFT_ARGS), ctx=ctx, state=state)

    assert result.ok is False
    assert result.error_code is ErrorCode.NOT_ALLOWED
    assert result.events == []


def test_craft_schema_rejects_extra_fields(m4_game_harness: Any) -> None:
    state = _create_state(m4_game_harness, key="sec-create-0002")
    gate = _gate(m4_game_harness)
    ctx = _ctx(actor_id=PLAYER_ID, tags={"player"}, turn_id=state.turn + 1)
    attacked = {**_CRAFT_ARGS, "stats": {"attack": 99}}

    result = gate.dispatch(tool="craft_item", args=attacked, ctx=ctx, state=state)

    assert result.ok is False
    assert result.error_code is ErrorCode.INVALID_INPUT
    assert result.events == []


def test_craft_free_description_over_limit_rejected(m4_game_harness: Any) -> None:
    state = _create_state(m4_game_harness, key="sec-create-0003")
    gate = _gate(m4_game_harness)
    ctx = _ctx(actor_id=PLAYER_ID, tags={"player"}, turn_id=state.turn + 1)

    result = gate.dispatch(
        tool="craft_item",
        args={"mode": "free", "free_description": "x" * 201},
        ctx=ctx,
        state=state,
    )

    assert result.ok is False
    assert result.error_code is ErrorCode.INVALID_INPUT
    assert result.events == []


def test_craft_invalid_mode_rejected_by_schema(m4_game_harness: Any) -> None:
    state = _create_state(m4_game_harness, key="sec-create-0004")
    gate = _gate(m4_game_harness)
    ctx = _ctx(actor_id=PLAYER_ID, tags={"player"}, turn_id=state.turn + 1)

    result = gate.dispatch(
        tool="craft_item",
        args={"mode": "bogus", "recipe_id": "recipe.forge_sword"},
        ctx=ctx,
        state=state,
    )

    assert result.ok is False
    assert result.error_code is ErrorCode.INVALID_INPUT
    assert result.events == []


def test_craft_recipe_mode_requires_recipe_id(m4_game_harness: Any) -> None:
    state = _create_state(m4_game_harness, key="sec-create-0005")
    gate = _gate(m4_game_harness)
    ctx = _ctx(actor_id=PLAYER_ID, tags={"player"}, turn_id=state.turn + 1)

    result = gate.dispatch(tool="craft_item", args={"mode": "recipe"}, ctx=ctx, state=state)

    assert result.ok is False
    assert result.error_code is ErrorCode.INVALID_INPUT
    assert result.events == []


def test_craft_unknown_recipe_zero_events(m4_game_harness: Any) -> None:
    state = _create_state(m4_game_harness, key="sec-create-0006")
    gate = _gate(m4_game_harness)
    ctx = _ctx(actor_id=PLAYER_ID, tags={"player"}, turn_id=state.turn + 1)

    result = gate.dispatch(
        tool="craft_item",
        args={"mode": "recipe", "recipe_id": "recipe.no_such_recipe"},
        ctx=ctx,
        state=state,
    )

    assert result.ok is False
    assert result.error_code is ErrorCode.NOT_FOUND
    assert result.events == []


def test_craft_free_mode_disabled_zero_events(m4_game_harness: Any) -> None:
    state = _create_state(m4_game_harness, key="sec-create-0007")
    gate = _gate(m4_game_harness)
    ctx = _ctx(actor_id=PLAYER_ID, tags={"player"}, turn_id=state.turn + 1)

    result = gate.dispatch(
        tool="craft_item",
        args={"mode": "free", "free_description": "把火把和铜线绑在一起"},
        ctx=ctx,
        state=state,
    )

    assert result.ok is False
    assert result.error_code is ErrorCode.NOT_ALLOWED
    assert result.events == []
