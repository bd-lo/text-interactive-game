"""单元测试：``@tool`` Schema 生成、ToolRegistry 白名单/校验与 ToolGate 路由。

覆盖第 18 章 §18.2 必测项：JSON Schema 与 ``additionalProperties=false``、
``actor_scope`` 可见工具集合、越权/参数不合法/未知工具失败码、READ_ONLY 零事件；
另覆盖 ToolGate 的读/写路由与失败分支 (M2 由 006 替换 005 的 direct_gate)。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

import pytest
from jsonschema import Draft202012Validator
from pydantic import Field

from textgame.domain.models import GameState, RngState, SideEffect
from textgame.domain.results import ErrorCode, Result
from textgame.domain.types import Direction, ItemId, NpcId
from textgame.services.tool_context import ToolContext
from textgame.tools.decorators import ToolSchemaError, tool
from textgame.tools.gate import ToolGate
from textgame.tools.registry import ToolRegistry

_ROOM_ID = "room_01J9Z3K4Q7X8Y9Z0ABCDEFGHJK"
_TORCH_ID = "item_01J9Z3K4Q7X8Y9Z0ABCDEFGHJQ"
_NPC_ID = "npc_01J9Z3K4Q7X8Y9Z0ABCDEFGHJP"
_PLAYER_TAGS: frozenset[str] = frozenset({"player"})
_NPC_TAGS: frozenset[str] = frozenset({"npc", "npc:blacksmith"})


def _ctx(
    *,
    actor_id: str = "player",
    actor_tags: frozenset[str] = _PLAYER_TAGS,
) -> ToolContext:
    """构造真实 ``ToolContext`` (契约 §4.1 六字段)，避免测试替身与实现漂移。"""
    caller: Literal["player", "npc_agent", "parser", "system", "resolver"] = (
        "player" if actor_id == "player" else "npc_agent"
    )
    return ToolContext(
        actor_id=actor_id,
        actor_tags=actor_tags,
        turn_id=2,
        save_seed="save-test",
        idempotency_key="idem-0001",
        caller=caller,
    )


def _state() -> GameState:
    """最小合法 GameState (空世界，仅用于 gate 透传)。"""
    return GameState(turn=1, world_time=0, rng_state=RngState(save_seed="save-test"))


# ---------------------------------------------------------------------------
# 测试工具：scope/side_effect 与 M2 六工具矩阵一致 (player/system 可见，npc 标签不可见)
# ---------------------------------------------------------------------------
@tool(actor_scope={"player", "system"}, side_effect=SideEffect.READ_ONLY)
def get_room_view(ctx: ToolContext) -> Result:
    """查看当前所在房间的可见信息。"""
    return Result.success(data={"actor": ctx.actor_id, "room_id": _ROOM_ID})


@tool(actor_scope={"player", "system"}, side_effect=SideEffect.READ_ONLY)
def get_inventory(ctx: ToolContext) -> Result:
    """查看当前角色背包。"""
    return Result.success(data={"actor": ctx.actor_id, "items": []})


@tool(actor_scope={"player", "system"}, side_effect=SideEffect.DESTRUCTIVE)
def move_to(ctx: ToolContext, direction: Direction, *, force: bool = False) -> Result:
    """向一个方向移动。"""
    return Result.success(data={"direction": direction, "force": force, "actor": ctx.actor_id})


@tool(actor_scope={"player", "system"}, side_effect=SideEffect.DESTRUCTIVE)
def take_item(ctx: ToolContext, item_id: ItemId) -> Result:
    """从当前房间拿起物品。"""
    return Result.success(data={"item_id": item_id, "actor": ctx.actor_id})


@tool(actor_scope={"player", "system"}, side_effect=SideEffect.DESTRUCTIVE)
def use_item(ctx: ToolContext, item_id: ItemId) -> Result:
    """使用背包中的物品。"""
    return Result.success(data={"item_id": item_id, "actor": ctx.actor_id})


@tool(actor_scope={"player", "system"}, side_effect=SideEffect.DESTRUCTIVE)
def talk_to(
    ctx: ToolContext,
    npc_id: NpcId,
    topic: Annotated[str, Field(max_length=500)],
) -> Result:
    """与同房间 NPC 交谈。"""
    return Result.success(data={"npc_id": npc_id, "topic": topic, "actor": ctx.actor_id})


_M2_TOOLS = (get_room_view, get_inventory, move_to, take_item, use_item, talk_to)


def _registry() -> ToolRegistry:
    """注册 M2 六工具并返回注册表 (每个用例独立实例)。"""
    registry = ToolRegistry()
    for fn in _M2_TOOLS:
        registry.register(fn)
    return registry


# ---------------------------------------------------------------------------
# Schema 生成与校验
# ---------------------------------------------------------------------------
def test_tool_schema_is_object_with_additional_properties_false() -> None:
    """@tool 生成的 Schema 根为 object、额外字段被禁、必填/默认值正确。"""
    spec = _registry().get("move_to")

    schema = spec.json_schema
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["direction"]
    assert schema["properties"]["direction"]["enum"] == [
        "north",
        "south",
        "east",
        "west",
        "up",
        "down",
        "in",
        "out",
    ]
    assert schema["properties"]["force"]["default"] is False
    assert spec.name == "move_to"
    assert spec.side_effect is SideEffect.DESTRUCTIVE
    assert spec.actor_scope == frozenset({"player", "system"})


def test_tool_schema_rejects_unknown_field_via_draft_2020_12() -> None:
    """第 18 章 §18.2：Draft202012Validator 对多余字段报 ``additionalProperties``。"""
    spec = _registry().get("move_to")

    errors = list(
        Draft202012Validator(spec.json_schema).iter_errors({"direction": "north", "extra": 1})
    )

    assert errors
    assert errors[0].validator == "additionalProperties"


def test_tool_decorator_requires_docstring() -> None:
    """无 docstring 的工具注册即失败 (描述用作 Prompt，第 7 章 7.2)。"""
    with pytest.raises(ToolSchemaError, match="docstring"):

        @tool(actor_scope={"player"})
        def undocumented() -> Result:
            return Result.success()


def test_tool_decorator_rejects_unannotated_parameter() -> None:
    """缺类型注解的参数无法生成 Schema，注册期直接失败。"""
    with pytest.raises(ToolSchemaError, match="type annotation"):

        @tool(actor_scope={"player"})
        def bad_tool(value) -> Result:  # type: ignore[no-untyped-def]
            """坏工具。"""
            return Result.success()


# ---------------------------------------------------------------------------
# 可见性与注册
# ---------------------------------------------------------------------------
def test_visibility_matrix_player_and_system_see_all() -> None:
    """玩家与系统可见 M2 六工具；npc/npc:blacksmith 标签看不到 take_item/use_item/talk_to。"""
    registry = _registry()

    player_visible = {spec.name for spec in registry.visible_tools("player", _PLAYER_TAGS)}
    system_visible = {spec.name for spec in registry.visible_tools("system", frozenset({"system"}))}
    npc_visible = {spec.name for spec in registry.visible_tools("npc_1", _NPC_TAGS)}

    assert player_visible == {spec.name for spec in registry.visible_tools("player")}
    assert {"get_room_view", "get_inventory", "move_to", "take_item", "use_item", "talk_to"} <= (
        player_visible
    )
    assert system_visible >= {
        "get_room_view",
        "get_inventory",
        "move_to",
        "take_item",
        "use_item",
        "talk_to",
    }
    assert "take_item" not in npc_visible
    assert "use_item" not in npc_visible
    assert "talk_to" not in npc_visible


def test_duplicate_register_raises() -> None:
    """同名工具重复注册抛 ValueError (避免实现被静默覆盖)。"""
    registry = _registry()

    with pytest.raises(ValueError, match="duplicate tool: move_to"):
        registry.register(move_to)


# ---------------------------------------------------------------------------
# invoke 失败分支与 READ_ONLY 零事件
# ---------------------------------------------------------------------------
def test_invoke_unknown_tool_returns_not_found() -> None:
    """工具名不存在 → NOT_FOUND (第 7 章 §7.5：与 actor_scope 越权不混用)。"""
    result = _registry().invoke("admin_override", _ctx(), {})

    assert not result.ok
    assert result.error_code is ErrorCode.NOT_FOUND
    assert result.events == []


def test_invoke_invisible_tool_returns_not_allowed() -> None:
    """actor_scope 越权 → NOT_ALLOWED (npc 标签不可见 take_item)。"""
    npc_ctx = _ctx(actor_id="npc_1", actor_tags=_NPC_TAGS)

    result = _registry().invoke("take_item", npc_ctx, {"item_id": _TORCH_ID})

    assert not result.ok
    assert result.error_code is ErrorCode.NOT_ALLOWED
    assert result.events == []


@pytest.mark.parametrize(
    ("args", "expected_fragment"),
    [
        ({"direction": "north", "extra": 1}, "additionalProperties"),
        ({"direction": "north", "force": "yes"}, "type"),
        ({}, "required"),
        ({"direction": "sideways"}, "enum"),
    ],
)
def test_invoke_invalid_args_returns_invalid_input(
    args: dict[str, Any], expected_fragment: str
) -> None:
    """参数不符合 JSON Schema → INVALID_INPUT，message 携带 violations；零事件。"""
    result = _registry().invoke("move_to", _ctx(), args)

    assert not result.ok
    assert result.error_code is ErrorCode.INVALID_INPUT
    assert expected_fragment in (result.message or "")
    assert result.events == []


def test_invoke_read_only_tool_executes_with_zero_events() -> None:
    """READ_ONLY 工具即时执行：拿到数据，且不产生任何事件。"""
    result = _registry().invoke("get_room_view", _ctx(), {})

    assert result.ok
    assert result.events == []
    assert result.data == {"actor": "player", "room_id": _ROOM_ID}


def test_invoke_write_tool_rejected_directly() -> None:
    """写工具不得直接 invoke (必须经 gate 路由 / 提案)。"""
    result = _registry().invoke("move_to", _ctx(), {"direction": "north"})

    assert not result.ok
    assert result.error_code is ErrorCode.NOT_ALLOWED
    assert "ToolGate" in (result.message or "")
    assert result.events == []


# ---------------------------------------------------------------------------
# build_proposal
# ---------------------------------------------------------------------------
def test_build_proposal_for_visible_write_tool() -> None:
    """可见写工具 + 合法参数 → Proposal；只读/越权/非法参数返回 None。"""
    registry = _registry()

    proposal = registry.build_proposal(
        "take_item",
        {"item_id": _TORCH_ID},
        actor_id="player",
        actor_tags=_PLAYER_TAGS,
        turn_id=2,
        reason="玩家拿火把",
        priority=100,
    )

    assert proposal is not None
    assert proposal.tool == "take_item"
    assert proposal.args == {"item_id": _TORCH_ID}
    assert proposal.turn_id == 2
    assert (
        registry.build_proposal(
            "take_item",
            {"item_id": _TORCH_ID},
            actor_id="npc_1",
            actor_tags=_NPC_TAGS,
            turn_id=2,
            reason="越权",
            priority=50,
        )
        is None
    )
    assert (
        registry.build_proposal(
            "get_room_view",
            {},
            actor_id="player",
            actor_tags=_PLAYER_TAGS,
            turn_id=2,
            reason="只读",
            priority=100,
        )
        is None
    )
    assert (
        registry.build_proposal(
            "move_to",
            {"direction": "north", "extra": 1},
            actor_id="player",
            actor_tags=_PLAYER_TAGS,
            turn_id=2,
            reason="非法参数",
            priority=100,
        )
        is None
    )


# ---------------------------------------------------------------------------
# ToolGate：读/写路由与失败分支 (Phase 2 全链路测试的基础)
# ---------------------------------------------------------------------------
class FakeQuery:
    """只读查询服务替身：记录调用，返回固定背包视图。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def get_inventory(self, state: GameState, actor_id: str) -> dict[str, Any]:
        self.calls.append(("get_inventory", actor_id))
        return {"items": [{"item_id": _TORCH_ID, "name": "火把"}]}


class FakeAction:
    """动作服务替身：``look`` 只读、其余只返回草稿结果，不产生事件、不写库。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def look(self, ctx: ToolContext, state: GameState) -> Result:
        self.calls.append(("look", {"actor": ctx.actor_id}))
        return Result.success(data={"text": "废弃哨站·入口。风从断裂的门楣间穿过。"})

    def move_to(self, ctx: ToolContext, state: GameState, *, direction: Direction) -> Result:
        self.calls.append(("move_to", {"direction": direction}))
        return Result.success(data={"direction": direction})

    def take_item(self, ctx: ToolContext, state: GameState, *, item_id: ItemId) -> Result:
        self.calls.append(("take_item", {"item_id": item_id}))
        return Result.success(data={"item_id": item_id})

    def use_item(self, ctx: ToolContext, state: GameState, *, item_id: ItemId) -> Result:
        self.calls.append(("use_item", {"item_id": item_id}))
        return Result.success(data={"item_id": item_id})

    def talk_to(self, ctx: ToolContext, state: GameState, *, npc_id: NpcId, topic: str) -> Result:
        self.calls.append(("talk_to", {"npc_id": npc_id, "topic": topic}))
        return Result.success(data={"npc_id": npc_id})


def _gate() -> tuple[ToolGate, FakeQuery, FakeAction]:
    """构造注入测试端口的 ToolGate。"""
    query = FakeQuery()
    action = FakeAction()
    gate = ToolGate(registry=_registry(), query=query, action=action)
    return gate, query, action


def test_gate_read_only_routes_to_query_service() -> None:
    """READ_ONLY 工具 ``get_inventory`` → QueryService，零事件。"""
    gate, query, action = _gate()

    result = gate.dispatch(tool="get_inventory", args={}, ctx=_ctx(), state=_state())

    assert result.ok
    assert result.events == []
    assert result.data == {"items": [{"item_id": _TORCH_ID, "name": "火把"}]}
    assert query.calls == [("get_inventory", "player")]
    assert action.calls == []


def test_gate_room_view_routes_to_action_look() -> None:
    """READ_ONLY ``get_room_view`` → ActionService.look，返回一屏房间文本、零事件。"""
    gate, query, action = _gate()

    result = gate.dispatch(tool="get_room_view", args={}, ctx=_ctx(), state=_state())

    assert result.ok
    assert result.events == []
    assert "text" in (result.data or {})
    assert action.calls == [("look", {"actor": "player"})]
    assert query.calls == []


def test_gate_destructive_routes_to_action_service() -> None:
    """DESTRUCTIVE 工具 → ActionService (只拿草稿结果，不提交)。"""
    gate, query, action = _gate()

    result = gate.dispatch(
        tool="move_to",
        args={"direction": "north"},
        ctx=_ctx(),
        state=_state(),
    )

    assert result.ok
    assert result.data == {"direction": "north"}
    assert action.calls == [("move_to", {"direction": "north"})]
    assert query.calls == []


def test_gate_rejects_schema_violation_before_service_call() -> None:
    """参数校验失败 → INVALID_INPUT，且服务层不被调用 (零副作用)。"""
    gate, query, action = _gate()

    result = gate.dispatch(
        tool="move_to",
        args={"direction": "north", "sneak": True},
        ctx=_ctx(),
        state=_state(),
    )

    assert not result.ok
    assert result.error_code is ErrorCode.INVALID_INPUT
    assert action.calls == []
    assert query.calls == []


def test_gate_rejects_unknown_and_invisible_tools() -> None:
    """未知工具 → NOT_FOUND；actor_scope 越权 → NOT_ALLOWED (第 7 章 §7.5)。"""
    gate, query, action = _gate()
    npc_ctx = _ctx(actor_id="npc_1", actor_tags=_NPC_TAGS)

    unknown = gate.dispatch(tool="admin_override", args={}, ctx=_ctx(), state=_state())
    invisible = gate.dispatch(
        tool="talk_to",
        args={"npc_id": _NPC_ID, "topic": "你好"},
        ctx=npc_ctx,
        state=_state(),
    )

    assert unknown.error_code is ErrorCode.NOT_FOUND
    assert invisible.error_code is ErrorCode.NOT_ALLOWED
    assert action.calls == []
    assert query.calls == []
