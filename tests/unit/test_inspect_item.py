"""D6：``inspect_item`` 只读工具（细看物品/NPC）与 story NPC 回话（D8）单测。

覆盖：工具注册/可见性/schema（item_id 与 npc_id 二选一）、gate 只读路由零事件、
ActionService.inspect 的可见性/错误码/世界内文案、叙事器细看屏长度与确定性、
story 老兵回话事件结构（``reply``/``dialogue_state``），以及旧场景 NPC 事件零变化。
"""

from __future__ import annotations

from typing import Any

import pytest

from textgame.agents.dto import ParseOutcome
from textgame.domain.models import ActionIntent, GameState
from textgame.domain.results import ErrorCode
from textgame.domain.scenario import NPC_SENTINEL, PLAYER_ID, build_initial_state
from textgame.domain.scenario_story import (
    STORY_ENTRY_ROOM_ID,
    STORY_WATCHMAN_ID,
    build_story_state,
)
from textgame.orchestration.narrator import TemplateNarrator
from textgame.persistence.memory import InMemoryRepository
from textgame.services.action_service import ActionService
from textgame.services.query_service import QueryService
from textgame.services.tool_context import ToolContext
from textgame.tools.builtin.m2 import M2_TOOL_NAMES, build_m2_tool_registry
from textgame.tools.gate import ToolGate

_SAVE_SEED = "save-42"
_ITEM_TORCH = "item_01J9Z3K4Q7X8Y9Z0ABCDEFGHJQ"


def _ctx(actor_id: str = PLAYER_ID, *, turn_id: int = 2) -> ToolContext:
    """玩家上下文（M2/M3 同构六字段）。"""
    return ToolContext(
        actor_id=actor_id,
        actor_tags=frozenset({"player"}),
        turn_id=turn_id,
        save_seed=_SAVE_SEED,
        idempotency_key="inspect-unit-key",
        caller="player",
    )


def _service() -> ActionService:
    """动作服务（只读方法不触碰仓储）。"""
    return ActionService(QueryService(InMemoryRepository()))


def _outcome(tool: str) -> ParseOutcome:
    """只读动作的解析结果（叙事器按 ``action.tool`` 分支）。"""
    return ParseOutcome(
        intent=ActionIntent(action=tool, params={}, confidence=1.0),
        tool=tool,
        args={},
        confidence=1.0,
        source="keyword",
    )


# ---------------------------------------------------------------------------
# 工具注册与 gate 路由
# ---------------------------------------------------------------------------
def test_inspect_item_is_registered_as_read_only_player_tool() -> None:
    """``inspect_item`` 已注册：M2 工具名列表 + READ_ONLY + player/npc/system 可见。"""
    registry = build_m2_tool_registry()
    spec = registry.get("inspect_item")

    assert "inspect_item" in M2_TOOL_NAMES
    assert spec.side_effect.value == "READ_ONLY"
    assert spec.actor_scope == frozenset({"player", "npc", "system"})
    schema = spec.json_schema
    assert schema["additionalProperties"] is False
    assert "item_id" in schema["properties"]
    assert "npc_id" in schema["properties"]
    assert schema.get("required") in (None, [])


def test_gate_routes_inspect_item_to_action_service_read_only() -> None:
    """gate 只读路由：``inspect_item`` → ActionService.inspect，零事件。"""
    state = build_initial_state(save_seed=_SAVE_SEED)
    query = QueryService(InMemoryRepository())
    action = ActionService(query)
    gate = ToolGate(registry=build_m2_tool_registry(), query=query, action=action)

    result = gate.dispatch(
        tool="inspect_item", args={"item_id": _ITEM_TORCH}, ctx=_ctx(), state=state
    )

    assert result.ok is True, result.message
    assert result.events == []
    assert result.data is not None
    assert result.data["kind"] == "item"
    assert result.data["name"] == "火把"
    assert "受潮" in result.data["description"]


# ---------------------------------------------------------------------------
# ActionService.inspect 分支
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "args",
    [{}, {"item_id": _ITEM_TORCH, "npc_id": NPC_SENTINEL}],
    ids=["neither", "both"],
)
def test_inspect_requires_exactly_one_target(args: dict[str, Any]) -> None:
    """``item_id``/``npc_id`` 必须二选一；同时给或都不给 → INVALID_INPUT、零事件。"""
    result = _service().inspect(_ctx(), build_initial_state(save_seed=_SAVE_SEED), **args)

    assert result.ok is False
    assert result.error_code is ErrorCode.INVALID_INPUT
    assert result.events == []


def test_inspect_room_item_returns_description() -> None:
    """房间可见物品 → ``{kind:item,name,description}``。"""
    result = _service().inspect(
        _ctx(), build_initial_state(save_seed=_SAVE_SEED), item_id=_ITEM_TORCH
    )

    assert result.ok is True
    assert result.data is not None
    assert result.data["kind"] == "item"
    assert "松脂" in result.data["description"]


def test_inspect_inventory_item_returns_description() -> None:
    """背包内物品同样可细看（可见性 = 房间可见 ∪ 自己背包）。"""
    state = build_initial_state(save_seed=_SAVE_SEED)
    player = state.characters[PLAYER_ID]
    state = state.model_copy(
        update={
            "characters": {
                **state.characters,
                PLAYER_ID: player.model_copy(update={"inventory": [_ITEM_TORCH]}),
            }
        }
    )

    result = _service().inspect(_ctx(), state, item_id=_ITEM_TORCH)

    assert result.ok is True


def test_inspect_unknown_or_invisible_item_not_found() -> None:
    """未在世界/不在房间也不在背包的物品 → NOT_FOUND（世界内文案，不泄漏 ID）。"""
    state = build_initial_state(save_seed=_SAVE_SEED)

    result = _service().inspect(_ctx(), state, item_id="item_01J9Z3K4Q7X8Y9Z0ABCDEFGHK9")

    assert result.ok is False
    assert result.error_code is ErrorCode.NOT_FOUND
    assert result.message == "这里没有可以细看的东西"


def test_inspect_same_room_npc_returns_description() -> None:
    """同房间 NPC → ``{kind:npc,name,description}``（NPC 无描述字段时确定性拼文案）。"""
    result = _service().inspect(
        _ctx(), build_initial_state(save_seed=_SAVE_SEED), npc_id=NPC_SENTINEL
    )

    assert result.ok is True
    assert result.data is not None
    assert result.data["kind"] == "npc"
    assert "老哨兵" in result.data["name"]
    assert "老哨兵" in result.data["description"]


def test_inspect_npc_in_other_room_not_found() -> None:
    """NPC 不在同房间 → NOT_FOUND（不给玩家看别处的人）；同房间后可细看。"""
    story = build_story_state(save_seed=_SAVE_SEED)
    result = _service().inspect(_ctx(), story, npc_id=STORY_WATCHMAN_ID)

    # 出生点是断桥，老兵在入口 → 不同房间
    assert result.ok is False
    assert result.error_code is ErrorCode.NOT_FOUND
    assert _service().inspect(_ctx(), _story_state_at_hub(), npc_id=STORY_WATCHMAN_ID).ok is True


# ---------------------------------------------------------------------------
# 叙事器：细看屏
# ---------------------------------------------------------------------------
def test_narrator_inspect_screen_length_and_content() -> None:
    """细看结果渲染成 80–150 字一屏，包含名称与描述关键信息，同输入两次逐字一致。"""
    narrator = TemplateNarrator()
    state = build_initial_state(save_seed=_SAVE_SEED)
    data = {
        "kind": "item",
        "name": "火把",
        "description": "一支受潮的火把，布头上还留着松脂的气味。",
    }

    first = narrator.render(
        action=_outcome("inspect_item"), events=[], state=state, result_data=data
    )
    second = narrator.render(
        action=_outcome("inspect_item"), events=[], state=state, result_data=data
    )

    assert 80 <= len(first) <= 150
    assert "火把" in first
    assert "松脂" in first
    assert "你凑近细看" in first
    assert "看得比刚才更清楚" not in first
    assert "细节记在心里" in first
    assert first == second


def test_narrator_inspect_missing_fields_falls_back() -> None:
    """细看数据缺字段时走通用兜底（长度仍达标，不崩）。"""
    narrator = TemplateNarrator()
    text = narrator.render(
        action=_outcome("inspect_item"),
        events=[],
        state=build_initial_state(save_seed=_SAVE_SEED),
        result_data={},
    )

    assert 80 <= len(text) <= 150


# ---------------------------------------------------------------------------
# D8：story NPC 回话 vs 旧场景零变化
# ---------------------------------------------------------------------------
def _story_state_at_hub() -> GameState:
    """story 状态：把玩家放到入口（老兵同房间）。"""
    story = build_story_state(save_seed=_SAVE_SEED)
    player = story.characters[PLAYER_ID].model_copy(update={"location": STORY_ENTRY_ROOM_ID})
    # 入口是 STORY_ENTRY_ROOM_ID 的东侧房间；由 exits 反推保证与场景定义一致
    hub = next(room for room in story.rooms.values() if STORY_ENTRY_ROOM_ID in room.exits.values())
    player = player.model_copy(update={"location": hub.id})
    return story.model_copy(update={"characters": {**story.characters, PLAYER_ID: player}})


def test_story_talk_matching_topic_sets_briefed_reply() -> None:
    """story 老兵：任务关键词 → ``dialogue_state=briefed`` 且事件带非空 reply。"""
    shot = _service().talk_to(
        _ctx(), _story_state_at_hub(), npc_id=STORY_WATCHMAN_ID, topic="信号灯怎么点亮？"
    )

    assert shot.ok is True
    assert shot.events
    event = shot.events[0]
    assert event.result["dialogue_state"] == "briefed"
    assert isinstance(event.result["reply"], str) and len(event.result["reply"]) >= 40


def test_story_talk_other_topic_sets_talked_reply() -> None:
    """story 老兵：**已完成任务说明后**的普通话题 → ``dialogue_state=talked``，reply 非空。

    首次搭话无论话题都会 briefed（D-R2-6：LLM 解析 topic 不一定含任务关键词）；
    这里先把 NPC 置为已 briefed，再验证闲聊分支仍然可用。
    """
    state = _story_state_at_hub()
    npc = state.npcs[STORY_WATCHMAN_ID]
    briefed = state.model_copy(
        update={
            "npcs": {
                **state.npcs,
                STORY_WATCHMAN_ID: npc.model_copy(update={"dialogue_state": "briefed"}),
            }
        }
    )
    shot = _service().talk_to(_ctx(), briefed, npc_id=STORY_WATCHMAN_ID, topic="你冷不冷")

    assert shot.ok is True
    assert shot.events[0].result["dialogue_state"] == "talked"
    assert shot.events[0].result["reply"]


def test_legacy_talk_event_keeps_old_shape() -> None:
    """旧场景老哨兵：事件 result 仍只有 ``dialogue_state=greeted``（m2 golden 结构不变）。"""
    shot = _service().talk_to(
        _ctx(),
        build_initial_state(save_seed=_SAVE_SEED),
        npc_id=NPC_SENTINEL,
        topic="你好",
    )

    assert shot.ok is True
    assert shot.events[0].result == {"dialogue_state": "greeted"}


def test_narrator_story_reply_renders_npc_words() -> None:
    """带 reply 的事件渲染 NPC 真回话；长度 80–150 且包含回话关键片段。"""
    narrator = TemplateNarrator()
    shot = _service().talk_to(
        _ctx(), _story_state_at_hub(), npc_id=STORY_WATCHMAN_ID, topic="信号灯怎么办"
    )
    assert shot.events
    text = narrator.render(
        action=_outcome("talk_to"),
        events=list(shot.events),
        state=_story_state_at_hub(),
        result_data={},
    )

    assert 80 <= len(text) <= 150
    assert "信号火把" in text
    # 回话外不再包一层「」，避免与 reply 自带引号嵌套
    assert "「" not in text and "」" not in text
