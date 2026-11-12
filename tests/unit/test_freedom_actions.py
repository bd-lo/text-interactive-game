"""R7-T1 行动自由度动作包单测（契约 `.work/r7/contracts.md` §1–§4）。

覆盖四层：
1. 声明层：4 个新 EventType、``freedom`` 工具 spec（Schema/scope/side_effect/policy）与
   ``build_game_tool_registry`` 注册；``build_m2_tool_registry`` 逐字不变（golden 安全）。
2. 投影层：4 个 reducer 的正常/非法分支（严格类型、hit=False identity、只改约定字段）。
3. 服务层：``ActionService`` 四方法成功产草稿/失败零事件；attack 同 seed 决定性。
4. 规则层：``RuleEngine`` 前置分支、needs_judge 生产点、improvise 确定性兜底文案。

全部纯计算，不联网、不写库、不依赖时钟。
"""

from __future__ import annotations

from typing import Any

import pytest

from textgame.domain.events import Event, EventType
from textgame.domain.models import GameState, Proposal
from textgame.domain.reducers import DEFAULT_REGISTRY
from textgame.domain.results import ErrorCode
from textgame.domain.rules.dice import DerivedRandom
from textgame.domain.rules.engine import RuleEngine
from textgame.domain.scenario import (
    ITEM_POTION,
    ITEM_TORCH,
    NPC_SENTINEL,
    PLAYER_ID,
    ROOM_CELLAR,
    build_initial_state,
)
from textgame.persistence.memory import InMemoryRepository
from textgame.services.action_service import ActionService
from textgame.services.query_service import QueryService
from textgame.services.tool_context import ToolContext
from textgame.tools.builtin import FREEDOM_TOOL_NAMES, FREEDOM_TOOLS, build_game_tool_registry
from textgame.tools.builtin.m2 import build_m2_tool_registry
from textgame.tools.gate import ToolGate

_SAVE_SEED = "save-42"
_CTX_KEY = "r7-t1-unit-key"
_FREEDOM_SET = frozenset(FREEDOM_TOOL_NAMES)


# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------
def _state() -> GameState:
    """m2 初始状态（玩家在入口、老哨兵同房间、火把/药水在地上）。"""
    return build_initial_state(save_seed=_SAVE_SEED)


def _state_carrying(*item_ids: str) -> GameState:
    """把指定物品从房间移到玩家背包（模拟 take_item 之后的状态）。"""
    state = _state()
    rooms = {
        room_id: room.model_copy(
            update={"item_ids": [item for item in room.item_ids if item not in item_ids]}
        )
        for room_id, room in state.rooms.items()
    }
    player = state.characters[PLAYER_ID].model_copy(update={"inventory": list(item_ids)})
    return state.model_copy(
        update={"characters": {**state.characters, PLAYER_ID: player}, "rooms": rooms}
    )


def _move_player_to(state: GameState, room_id: str) -> GameState:
    """把玩家移动到指定房间（构造「NPC 不同房间」等非法前置）。"""
    player = state.characters[PLAYER_ID].model_copy(update={"location": room_id})
    return state.model_copy(update={"characters": {**state.characters, PLAYER_ID: player}})


def _ctx(*, turn_id: int = 2) -> ToolContext:
    """标准玩家工具上下文。"""
    return ToolContext(
        actor_id=PLAYER_ID,
        turn_id=turn_id,
        save_seed=_SAVE_SEED,
        idempotency_key=_CTX_KEY,
        caller="player",
    )


def _event(
    event_type: EventType,
    *,
    method: str,
    params: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    turn_id: int = 2,
    actor_id: str = PLAYER_ID,
) -> Event:
    """构造草稿事件（reducer/apply_events 直接消费）。"""
    return Event(
        turn_id=turn_id,
        actor_id=actor_id,
        method=method,
        event_type=event_type,
        params=dict(params or {}),
        result=dict(result or {}),
    )


def _gate() -> ToolGate:
    """真实 ActionService + QueryService 的 gate（写路径不依赖仓储写入）。"""
    query = QueryService(InMemoryRepository())
    action = ActionService(query)
    return ToolGate(registry=build_game_tool_registry(), query=query, action=action)


def _expected_attack(state: GameState, ctx: ToolContext) -> dict[str, Any]:
    """按契约 §3 公式独立复算攻击判定（测试期望值）。"""
    seed = DerivedRandom.derive_seed(ctx.save_seed, ctx.turn_id, ctx.actor_id, "attack", 0)
    roll = DerivedRandom(seed).randint(1, 20)
    mod = (state.characters[ctx.actor_id].attributes.get("str", 10) - 10) // 2
    dc = 10
    total = roll + mod
    hit = roll == 20 or (roll != 1 and total >= dc)
    damage = 0 if not hit else max(1, 3 + mod)
    return {"roll": roll, "total": total, "dc": dc, "hit": hit, "damage": damage, "mod": mod}


# ---------------------------------------------------------------------------
# 1. 声明层：EventType + 工具 spec / 注册表
# ---------------------------------------------------------------------------
def test_new_event_types_registered() -> None:
    """R7 四个 EventType 以大写同名值登记（D-N6 用户批准）。"""
    assert EventType.ITEM_DROPPED.value == "ITEM_DROPPED"
    assert EventType.ITEM_GIVEN.value == "ITEM_GIVEN"
    assert EventType.COMBAT_RESOLVED.value == "COMBAT_RESOLVED"
    assert EventType.IMPROVISED_ACTION.value == "IMPROVISED_ACTION"


def test_freedom_tool_names_and_specs() -> None:
    """四个工具 spec：DESTRUCTIVE、{player,system}；仅 improvise 为 AGENT_ASSISTED。"""
    registry = build_game_tool_registry()
    assert FREEDOM_TOOL_NAMES == ("attack", "drop_item", "give_item", "improvise")
    assert tuple(fn.__name__ for fn in FREEDOM_TOOLS) == FREEDOM_TOOL_NAMES
    schema_props = {
        "attack": {"npc_id"},
        "drop_item": {"item_id"},
        "give_item": {"item_id", "npc_id"},
        "improvise": {"action_text"},
    }
    for name in FREEDOM_TOOL_NAMES:
        spec = registry.get(name)
        assert spec.side_effect.value == "DESTRUCTIVE", name
        assert spec.actor_scope == frozenset({"player", "system"}), name
        expected_policy = "agent_assisted" if name == "improvise" else "deterministic"
        assert spec.resolution_policy.value == expected_policy, name
        assert spec.json_schema["type"] == "object"
        assert spec.json_schema["additionalProperties"] is False
        assert set(spec.json_schema["properties"]) == schema_props[name], name
        assert spec.description.strip()
    assert (
        registry.get("attack").description == "攻击同房间的一个目标（一次判定：命中则对方退出场景）"
    )
    assert registry.agent_assisted_tools() == frozenset({"improvise"})


def test_build_m2_tool_registry_is_unchanged() -> None:
    """``build_m2_tool_registry()`` 不注册 R7 工具、无 agent_assisted（旧 golden 零影响）。"""
    registry = build_m2_tool_registry()
    visible = {spec.name for spec in registry.visible_tools("player", frozenset({"player"}))}
    assert visible.isdisjoint(_FREEDOM_SET)
    assert registry.agent_assisted_tools() == frozenset()


def test_freedom_tool_functions_are_declarative_only() -> None:
    """直接调用工具函数/registry.invoke 写工具一律 NOT_ALLOWED（执行只经 gate）。"""
    ctx = _ctx()
    call_args: dict[str, dict[str, Any]] = {
        "attack": {"npc_id": NPC_SENTINEL},
        "drop_item": {"item_id": ITEM_TORCH},
        "give_item": {"item_id": ITEM_TORCH, "npc_id": NPC_SENTINEL},
        "improvise": {"action_text": "在墙上刻一道记号"},
    }
    for fn in FREEDOM_TOOLS:
        result = fn(ctx=ctx, **call_args[fn.__name__])
        assert result.ok is False
        assert result.error_code is ErrorCode.NOT_ALLOWED
        assert result.events == []
    registry = build_game_tool_registry()
    invoked = registry.invoke("attack", ctx, {"npc_id": NPC_SENTINEL})
    assert invoked.error_code is ErrorCode.NOT_ALLOWED


# ---------------------------------------------------------------------------
# 2. reducer：正常分支
# ---------------------------------------------------------------------------
def test_item_dropped_reducer_moves_item_to_ground() -> None:
    """``ITEM_DROPPED``：背包移除（保持余下顺序），物品追加到当前房间地面。"""
    state = _state_carrying(ITEM_TORCH, ITEM_POTION)
    event = _event(
        EventType.ITEM_DROPPED,
        method="drop_item",
        params={"item_id": ITEM_TORCH, "from_room": state.characters[PLAYER_ID].location},
        result={"to_room": state.characters[PLAYER_ID].location, "item_name": "火把"},
    )

    result = state.apply([event])
    location = state.characters[PLAYER_ID].location

    assert result.characters[PLAYER_ID].inventory == [ITEM_POTION]
    assert result.rooms[location].item_ids == [ITEM_TORCH]
    assert result.turn == 2 and result.world_time == 1
    # 其它字段零变化（items/npcs）
    assert result.items == state.items
    assert result.npcs == state.npcs


def test_item_given_reducer_removes_item_from_inventory_only() -> None:
    """``ITEM_GIVEN``：背包移除，``state.items`` 保留物品定义；只改 characters。"""
    state = _state_carrying(ITEM_TORCH)
    event = _event(
        EventType.ITEM_GIVEN,
        method="give_item",
        params={"item_id": ITEM_TORCH, "npc_id": NPC_SENTINEL},
        result={"npc_name": "老哨兵"},
    )

    result = state.apply([event])

    assert result.characters[PLAYER_ID].inventory == []
    assert ITEM_TORCH in result.items  # NPC 无背包模型，物品定义保留
    assert result.rooms == state.rooms
    assert result.npcs == state.npcs


@pytest.mark.parametrize("hit", [True, False], ids=["hit", "miss"])
def test_combat_resolved_reducer_hit_removes_npc_miss_is_identity(hit: bool) -> None:
    """命中：NPC 从房间与 ``state.npcs`` 移除；未命中：状态零变化（仅公共规则）。"""
    state = _state()
    event = _event(
        EventType.COMBAT_RESOLVED,
        method="attack",
        params={"npc_id": NPC_SENTINEL, "room_id": state.characters[PLAYER_ID].location},
        result={"hit": hit, "roll": 15, "total": 16, "dc": 10, "damage": 4 if hit else 0},
    )

    result = state.apply([event])

    if hit:
        assert NPC_SENTINEL not in result.npcs
        assert NPC_SENTINEL not in result.rooms[state.characters[PLAYER_ID].location].npc_ids
    else:
        assert result.npcs == state.npcs
        assert result.rooms == state.rooms
    assert result.turn == 2 and result.world_time == 1


def test_improvised_action_reducer_is_identity() -> None:
    """``IMPROVISED_ACTION``：只校验 actor/文本，数值与物品零变化。"""
    state = _state()
    event = _event(
        EventType.IMPROVISED_ACTION,
        method="improvise",
        params={"action_text": "在墙上刻一道记号"},
        result={"narrative_hint": "在墙上刻一道记号"},
    )

    result = state.apply([event])

    assert result.characters == state.characters
    assert result.rooms == state.rooms
    assert result.items == state.items
    assert result.npcs == state.npcs
    assert result.turn == 2 and result.world_time == 1


# ---------------------------------------------------------------------------
# 2b. reducer：非法分支（严格类型 → ValueError）
# ---------------------------------------------------------------------------
def test_item_dropped_reducer_rejects_invalid_payloads() -> None:
    """``ITEM_DROPPED``：缺 actor/物品不在背包/from_room 不符/已在地面/结果不符 → ValueError。"""
    state = _state_carrying(ITEM_TORCH)
    location = state.characters[PLAYER_ID].location
    good = {
        "method": "drop_item",
        "params": {"item_id": ITEM_TORCH, "from_room": location},
        "result": {"to_room": location, "item_name": "火把"},
    }
    bad_payloads: list[dict[str, Any]] = [
        {**good, "actor_id": "char_missing"},
        {**good, "params": {"item_id": ITEM_POTION, "from_room": location}},
        {**good, "params": {"item_id": ITEM_TORCH, "from_room": ROOM_CELLAR}},
        {**good, "params": {"item_id": 123, "from_room": location}},
        {**good, "result": {"to_room": ROOM_CELLAR, "item_name": "火把"}},
        {**good, "result": {"to_room": location, "item_name": ""}},
        {**good, "result": {"to_room": location}},
    ]
    for payload in bad_payloads:
        event = _event(EventType.ITEM_DROPPED, turn_id=2, **payload)
        with pytest.raises(ValueError):
            state.apply([event])


def test_item_dropped_reducer_rejects_item_already_on_ground() -> None:
    """物品仍在房间地面又在背包（数据不一致）→ ValueError，不重复入地。"""
    state = _state()
    player = state.characters[PLAYER_ID].model_copy(update={"inventory": [ITEM_TORCH]})
    corrupt = state.model_copy(update={"characters": {**state.characters, PLAYER_ID: player}})
    event = _event(
        EventType.ITEM_DROPPED,
        method="drop_item",
        params={"item_id": ITEM_TORCH, "from_room": player.location},
        result={"to_room": player.location, "item_name": "火把"},
    )

    with pytest.raises(ValueError):
        corrupt.apply([event])


def test_item_given_reducer_rejects_invalid_payloads() -> None:
    """``ITEM_GIVEN``：物品不在背包/NPC 不同房间/未知 NPC → ValueError（零部分投影）。"""
    state = _state_carrying(ITEM_TORCH)
    good = {
        "method": "give_item",
        "params": {"item_id": ITEM_TORCH, "npc_id": NPC_SENTINEL},
        "result": {"npc_name": "老哨兵"},
    }
    bad_states: list[tuple[GameState, dict[str, Any]]] = [
        (state, {**good, "params": {"item_id": ITEM_POTION, "npc_id": NPC_SENTINEL}}),
        (state, {**good, "params": {"item_id": "item_missing", "npc_id": NPC_SENTINEL}}),
        (state, {**good, "params": {"item_id": ITEM_TORCH, "npc_id": "npc_missing"}}),
        (state, {**good, "result": {"npc_name": ""}}),
        (_move_player_to(state, ROOM_CELLAR), good),  # NPC 不在玩家所在房间
    ]
    for target_state, payload in bad_states:
        event = _event(EventType.ITEM_GIVEN, turn_id=2, **payload)
        with pytest.raises(ValueError):
            target_state.apply([event])


@pytest.mark.parametrize(
    "result",
    [
        {"hit": 1, "roll": 15, "total": 16, "dc": 10, "damage": 4},
        {"hit": True, "roll": 0, "total": 1, "dc": 10, "damage": 4},
        {"hit": True, "roll": 21, "total": 22, "dc": 10, "damage": 4},
        {"hit": True, "roll": True, "total": 16, "dc": 10, "damage": 4},
        {"hit": True, "roll": 15, "total": "16", "dc": 10, "damage": 4},
        {"hit": True, "roll": 15, "total": 16, "dc": 10, "damage": -1},
        {"hit": True, "roll": 15, "total": 16, "dc": 10},
    ],
    ids=[
        "hit-int",
        "roll-zero",
        "roll-21",
        "roll-bool",
        "total-str",
        "damage-negative",
        "missing-damage",
    ],
)
def test_combat_resolved_reducer_rejects_invalid_result(result: dict[str, Any]) -> None:
    """``COMBAT_RESOLVED``：bool 冒充/越界/缺字段/负伤害等严格类型错误 → ValueError。"""
    state = _state()
    event = _event(
        EventType.COMBAT_RESOLVED,
        method="attack",
        params={"npc_id": NPC_SENTINEL, "room_id": state.characters[PLAYER_ID].location},
        result=result,
    )

    with pytest.raises(ValueError):
        state.apply([event])


def test_combat_resolved_reducer_rejects_npc_not_in_room() -> None:
    """玩家与 NPC 不同房间（或 ``params.room_id`` 不一致）→ ValueError。"""
    state = _move_player_to(_state(), ROOM_CELLAR)
    event = _event(
        EventType.COMBAT_RESOLVED,
        method="attack",
        params={"npc_id": NPC_SENTINEL, "room_id": ROOM_CELLAR},
        result={"hit": True, "roll": 15, "total": 16, "dc": 10, "damage": 4},
    )

    with pytest.raises(ValueError):
        state.apply([event])


def test_improvised_action_reducer_rejects_invalid_payloads() -> None:
    """``IMPROVISED_ACTION``：缺 actor/空文本/缺 narrative_hint → ValueError。"""
    state = _state()
    base = {"method": "improvise", "params": {"action_text": "刻记号"}}
    bad: list[dict[str, Any]] = [
        {**base, "actor_id": "char_missing", "result": {"narrative_hint": "刻记号"}},
        {**base, "params": {"action_text": ""}, "result": {"narrative_hint": "刻记号"}},
        {**base, "params": {}, "result": {"narrative_hint": "刻记号"}},
        {**base, "result": {}},
    ]
    for payload in bad:
        with pytest.raises(ValueError):
            state.apply([_event(EventType.IMPROVISED_ACTION, turn_id=2, **payload)])


# ---------------------------------------------------------------------------
# 3. ActionService：成功草稿 / 失败零事件 / 决定性
# ---------------------------------------------------------------------------
def test_action_service_drop_item_success_and_failure_zero_events() -> None:
    """drop_item：成功一条 ``ITEM_DROPPED`` 草稿；物品不在背包 → NOT_FOUND 零事件。"""
    service = ActionService(QueryService(InMemoryRepository()))
    state = _state_carrying(ITEM_TORCH)

    ok = service.drop_item(_ctx(), state, item_id=ITEM_TORCH)
    fail = service.drop_item(_ctx(), state, item_id=ITEM_POTION)

    assert ok.ok is True and ok.data and ok.data["to_room"] == state.characters[PLAYER_ID].location
    [draft] = ok.events
    assert draft.event_type is EventType.ITEM_DROPPED
    assert draft.method == "drop_item"
    assert draft.params == {
        "item_id": ITEM_TORCH,
        "from_room": state.characters[PLAYER_ID].location,
    }
    assert draft.result["item_name"] == "火把"
    assert draft.is_stamped is False
    # 服务产出的草稿可被 reducer 正常投影
    projected = state.apply([draft])
    assert projected.characters[PLAYER_ID].inventory == []
    assert ITEM_TORCH in projected.rooms[projected.characters[PLAYER_ID].location].item_ids
    assert fail.ok is False and fail.error_code is ErrorCode.NOT_FOUND and fail.events == []


def test_action_service_give_item_success_and_failure_zero_events() -> None:
    """give_item：同房间 NPC 成功；NPC 不同房间/物品不在背包 → NOT_FOUND 零事件。"""
    service = ActionService(QueryService(InMemoryRepository()))
    state = _state_carrying(ITEM_TORCH)

    ok = service.give_item(_ctx(), state, item_id=ITEM_TORCH, npc_id=NPC_SENTINEL)
    moved = _move_player_to(state, ROOM_CELLAR)
    not_here = service.give_item(_ctx(), moved, item_id=ITEM_TORCH, npc_id=NPC_SENTINEL)
    not_owned = service.give_item(_ctx(), state, item_id=ITEM_POTION, npc_id=NPC_SENTINEL)

    assert ok.ok is True and ok.events[0].event_type is EventType.ITEM_GIVEN
    assert ok.events[0].result == {"npc_name": "老哨兵"}
    assert state.apply([ok.events[0]]).characters[PLAYER_ID].inventory == []
    for failure in (not_here, not_owned):
        assert failure.ok is False
        assert failure.error_code is ErrorCode.NOT_FOUND
        assert failure.events == []


def test_action_service_attack_is_deterministic_for_same_seed() -> None:
    """attack：同 seed/turn/actor 两次判定逐值一致，且符合契约公式；失败零事件。"""
    service = ActionService(QueryService(InMemoryRepository()))
    state = _state()
    expected = _expected_attack(state, _ctx())

    first = service.attack(_ctx(), state, npc_id=NPC_SENTINEL)
    second = service.attack(_ctx(), state, npc_id=NPC_SENTINEL)

    assert first.ok is True and second.ok is True
    assert first.events == second.events
    [draft] = first.events
    assert draft.event_type is EventType.COMBAT_RESOLVED
    assert draft.method == "attack"
    assert draft.params == {"npc_id": NPC_SENTINEL, "room_id": state.characters[PLAYER_ID].location}
    assert draft.result == {
        "hit": expected["hit"],
        "roll": expected["roll"],
        "total": expected["total"],
        "dc": 10,
        "damage": expected["damage"],
    }
    assert draft.result["damage"] == (0 if not expected["hit"] else max(1, 3 + expected["mod"]))
    # 不同 turn_id 派生不同种子（但仍确定性）
    other = service.attack(_ctx(turn_id=3), state, npc_id=NPC_SENTINEL)
    assert other.ok is True
    assert other.events[0].result["roll"] == _expected_attack(state, _ctx(turn_id=3))["roll"]
    # 目标不在房间 → NOT_FOUND 零事件
    fail = service.attack(_ctx(), _move_player_to(state, ROOM_CELLAR), npc_id=NPC_SENTINEL)
    assert fail.ok is False and fail.error_code is ErrorCode.NOT_FOUND and fail.events == []


def test_action_service_improvise_truncates_hint_and_rejects_bad_text() -> None:
    """improvise：``narrative_hint=action_text[:120]``；空/超长文本 → INVALID_INPUT 零事件。"""
    service = ActionService(QueryService(InMemoryRepository()))
    state = _state()
    long_text = "刻" * 200

    ok = service.improvise(_ctx(), state, action_text=long_text)
    empty = service.improvise(_ctx(), state, action_text="   ")
    too_long = service.improvise(_ctx(), state, action_text="刻" * 201)

    assert ok.ok is True and ok.events[0].event_type is EventType.IMPROVISED_ACTION
    assert ok.events[0].result == {"narrative_hint": "刻" * 120}
    assert ok.events[0].params == {"action_text": long_text}
    [draft] = ok.events
    assert state.apply([draft]).characters == state.characters  # identity
    for failure in (empty, too_long):
        assert failure.ok is False
        assert failure.error_code is ErrorCode.INVALID_INPUT
        assert failure.events == []


# ---------------------------------------------------------------------------
# 4. ToolGate 路由
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("tool", "args", "event_type", "method"),
    [
        ("attack", {"npc_id": NPC_SENTINEL}, EventType.COMBAT_RESOLVED, "attack"),
        ("drop_item", {"item_id": ITEM_TORCH}, EventType.ITEM_DROPPED, "drop_item"),
        (
            "give_item",
            {"item_id": ITEM_TORCH, "npc_id": NPC_SENTINEL},
            EventType.ITEM_GIVEN,
            "give_item",
        ),
        (
            "improvise",
            {"action_text": "在墙上刻一道记号"},
            EventType.IMPROVISED_ACTION,
            "improvise",
        ),
    ],
)
def test_gate_routes_freedom_tools_to_action_service(
    tool: str, args: dict[str, Any], event_type: EventType, method: str
) -> None:
    """gate.dispatch：四工具均路由到 ActionService 并返回对应 event_type 的草稿。"""
    result = _gate().dispatch(tool=tool, args=args, ctx=_ctx(), state=_state_carrying(ITEM_TORCH))

    assert result.ok is True, result.message
    assert len(result.events) == 1
    draft = result.events[0]
    assert draft.event_type is event_type
    assert draft.method == method
    assert draft.actor_id == PLAYER_ID
    assert draft.is_stamped is False


def test_gate_rejects_freedom_tools_for_npc_actor_and_bad_args() -> None:
    """scope 越权 → NOT_ALLOWED；Schema 不合 → INVALID_INPUT；均零事件。"""
    gate = _gate()
    npc_ctx = ToolContext(
        actor_id="npc_1",
        actor_tags=frozenset({"npc"}),
        turn_id=2,
        save_seed=_SAVE_SEED,
        idempotency_key=_CTX_KEY,
        caller="player",
    )
    state = _state_carrying(ITEM_TORCH)

    invisible = gate.dispatch(
        tool="attack", args={"npc_id": NPC_SENTINEL}, ctx=npc_ctx, state=state
    )
    bad_args = gate.dispatch(tool="attack", args={"npc_id": "x" * 65}, ctx=_ctx(), state=state)
    missing = gate.dispatch(tool="give_item", args={"item_id": ITEM_TORCH}, ctx=_ctx(), state=state)

    assert invisible.ok is False and invisible.error_code is ErrorCode.NOT_ALLOWED
    assert invisible.events == []
    for failure in (bad_args, missing):
        assert failure.ok is False
        assert failure.error_code is ErrorCode.INVALID_INPUT
        assert failure.events == []


# ---------------------------------------------------------------------------
# 5. RuleEngine：L0 前置 / needs_judge / deterministic_fallback
# ---------------------------------------------------------------------------
_ALLOWED_ARGS: dict[str, dict[str, Any]] = {
    "attack": {"npc_id": NPC_SENTINEL},
    "drop_item": {"item_id": ITEM_TORCH},
    "give_item": {"item_id": ITEM_TORCH, "npc_id": NPC_SENTINEL},
    "improvise": {"action_text": "在墙上刻一道记号"},
}


def _preconditions(
    engine: RuleEngine,
    *,
    tool: str,
    args: dict[str, Any] | None = None,
    state: GameState | None = None,
) -> Any:
    return engine.evaluate_preconditions(
        tool=tool,
        args=dict(_ALLOWED_ARGS[tool] if args is None else args),
        actor_id=PLAYER_ID,
        turn_id=2,
        save_seed=_SAVE_SEED,
        state=_state_carrying(ITEM_TORCH) if state is None else state,
    )


@pytest.mark.parametrize("tool", list(_ALLOWED_ARGS), ids=list(_ALLOWED_ARGS))
def test_rules_allow_allowed_freedom_tools(tool: str) -> None:
    """四个工具 L0 通过：默认引擎 data 仍逐字为 allowed/reason（不额外产生 needs_judge）。"""
    result = _preconditions(RuleEngine(), tool=tool)

    assert result.ok is True
    assert result.data == {"allowed": True, "reason": ""}
    assert result.events == []


def test_rules_improvise_needs_judge_when_agent_assisted() -> None:
    """``improvise`` 在 agent_assisted_tools 中 → L0 通过时带 needs_judge=True。"""
    engine = RuleEngine(agent_assisted_tools=frozenset({"improvise"}))

    improvise = _preconditions(engine, tool="improvise")
    attack = _preconditions(engine, tool="attack")

    assert improvise.data == {"allowed": True, "needs_judge": True, "reason": ""}
    assert "needs_judge" not in (attack.data or {})


def test_rules_reject_invalid_freedom_preconditions() -> None:
    """L0 失败分支：目标不同房间/物品不在背包/文本非法 → 稳定错误码、零事件。"""
    engine = RuleEngine()
    moved = _move_player_to(_state_carrying(ITEM_TORCH), ROOM_CELLAR)

    attack = _preconditions(engine, tool="attack", state=moved)
    drop = _preconditions(engine, tool="drop_item", args={"item_id": ITEM_POTION})
    give = _preconditions(
        engine, tool="give_item", args={"item_id": ITEM_TORCH, "npc_id": "npc_missing"}
    )
    improvise_empty = _preconditions(engine, tool="improvise", args={"action_text": "  "})
    improvise_long = _preconditions(engine, tool="improvise", args={"action_text": "刻" * 201})

    for failure in (attack, drop, give):
        assert failure.ok is False
        assert failure.error_code is ErrorCode.NOT_FOUND
        assert failure.events == []
    for failure in (improvise_empty, improvise_long):
        assert failure.ok is False
        assert failure.error_code is ErrorCode.INVALID_INPUT
        assert failure.events == []


def _proposal(tool: str) -> Proposal:
    """裁判链路用的提案（args 形状不参与 deterministic_fallback）。"""
    return Proposal(
        turn_id=2,
        actor_id=PLAYER_ID,
        tool=tool,
        args=dict(_ALLOWED_ARGS.get(tool, {})),
        reason="玩家明确动作",
        priority=100,
    )


def test_deterministic_fallback_improvise_uses_specific_chinese_reason() -> None:
    """裁判不可用：improvise 专属中文拒绝；其它工具保持原文案；均零事件。"""
    engine = RuleEngine()
    state = _state()

    improvise = engine.deterministic_fallback(_ctx(), _proposal("improvise"), state)
    take = engine.deterministic_fallback(_ctx(), _proposal("take_item"), state)

    assert improvise.ok is False
    assert improvise.error_code is ErrorCode.NOT_ALLOWED
    assert improvise.message == "裁判不可用：这个临场动作没有被世界确认。"
    assert improvise.events == []
    assert take.ok is False
    assert take.message == "裁判不可用：降级为确定性保守判定。"
    assert take.events == []


# ---------------------------------------------------------------------------
# 6. T7 红队加固：ReducerRegistry.get 对非 EventType 输入统一 ValueError
# ---------------------------------------------------------------------------
def test_reducer_registry_get_rejects_non_event_type_with_value_error() -> None:
    """``Event.model_construct(event_type="X")`` 绕过校验后 ``get`` → ValueError 而非 AttributeError。

    ``model_construct`` 不做 pydantic 校验，``event_type`` 可能是任意对象；注册表查询必须
    统一回退为 ``ValueError``（错误信息不得访问 ``.value``），且不可哈希输入也不得抛
    ``TypeError``。
    """
    forged = Event.model_construct(
        turn_id=2,
        actor_id=PLAYER_ID,
        method="forged",
        event_type="UNKNOWN_X",
        params={},
        result={},
    )

    with pytest.raises(ValueError) as caught:
        DEFAULT_REGISTRY.get(forged.event_type)  # type: ignore[arg-type]
    assert not isinstance(caught.value, AttributeError)
    assert "UNKNOWN_X" in str(caught.value)

    # 任意非 EventType（含不可哈希对象）同样统一 ValueError。
    with pytest.raises(ValueError):
        DEFAULT_REGISTRY.get([])  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        DEFAULT_REGISTRY.get("ITEM_DROPPED")  # type: ignore[arg-type]


def test_reducer_registry_get_accepts_registered_event_types() -> None:
    """合法 ``EventType`` 的查询行为不变：已注册类型返回可调用 reducer。"""
    for event_type in (
        EventType.GAME_CREATED,
        EventType.ITEM_DROPPED,
        EventType.COMBAT_RESOLVED,
        EventType.IMPROVISED_ACTION,
    ):
        assert callable(DEFAULT_REGISTRY.get(event_type))
