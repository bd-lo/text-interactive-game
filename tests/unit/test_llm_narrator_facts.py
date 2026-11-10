"""单元测试：``LLMNarratorAgent`` 的**事实供给**（R2 A1，narrate@v2）。

覆盖：物品名 / 目标房间名 / 中文方位 / NPC 名 / NPC 回话 / 背包聚合数量 / 当前目标（story）
全部以玩家可见展示名进入 prompt；内部 ULID 绝不进入 prompt；玩家原话（topic）不进入 prompt；
默认离线客户端与 schema 失败仍回退模板（source=template）。只测 LLM 专属，不改模板断言。
"""

from __future__ import annotations

from typing import Any

import pytest

from fakes import FakeLLM
from textgame.agents.dto import ParseOutcome
from textgame.agents.narrator_agent import PROMPT_VERSION, LLMNarratorAgent
from textgame.domain.events import Event, EventType
from textgame.domain.models import ActionIntent, GameState
from textgame.domain.scenario import PLAYER_ID, build_initial_state
from textgame.domain.scenario_story import (
    STORY_WATCHMAN_ID,
    build_story_state,
    story_reply,
)
from textgame.llm.client import DisabledLLMClient
from textgame.orchestration.narrator import TemplateNarrator

_SAVE_SEED = "save-42"
_HUB_NAME = "废弃哨站·入口"
_CELLAR_NAME = "哨站地窖"
_TORCH_NAME = "火把"
_COPPER_NAME = "铜线"
_GOAL_TITLE = "让信号灯亮起来"

#: 合法的一屏叙事（80–150 字，供 FakeLLM 返回）。
_VALID_NARRATIVE = (
    "断桥横在封冻的河面上，桥板缺了三块，风从缺口里直灌上来。哨站就在桥那头，"
    "灯已经灭了两夜，天亮前点不亮它，今夜这条路上不会再有活人经过。"
    "你把衣领拉紧，朝哨站的方向迈出一步，脚下的积雪发出细碎的声响。"
)


# ---------------------------------------------------------------------------
# 装配辅助
# ---------------------------------------------------------------------------
def _room_id(state: GameState, name: str) -> str:
    """按房间展示名取房间 id（测试夹具用；被测代码不依赖）。"""
    return next(room.id for room in state.rooms.values() if room.name == name)


def _item_id(state: GameState, name: str) -> str:
    """按物品展示名取物品实例 id。"""
    return next(item.id for item in state.items.values() if item.name == name)


def _state_at(room_name: str | None = None) -> GameState:
    """剧情场景初始状态；给定房间名时把玩家移过去（保留其余状态）。"""
    state = build_story_state(save_seed=_SAVE_SEED)
    if room_name is None:
        return state
    player = state.characters[PLAYER_ID].model_copy(update={"location": _room_id(state, room_name)})
    return state.model_copy(update={"characters": {**state.characters, PLAYER_ID: player}})


def _outcome(tool: str) -> ParseOutcome:
    """构造与编排层同构的解析结果（只读/写动作名即可）。"""
    return ParseOutcome(
        intent=ActionIntent(action=tool, target=None, params={}, confidence=1.0),
        tool=tool,
        args={},
        confidence=1.0,
        source="keyword",
    )


def _narrate(
    *,
    state: GameState,
    action: ParseOutcome,
    events: list[Event] | None = None,
    result_data: dict[str, Any] | None = None,
    responses: list[Any] | None = None,
) -> tuple[FakeLLM, str, str]:
    """用 FakeLLM 跑一次叙事，返回 ``(fake, text, source)``；默认脚本为一条合法叙事。"""
    fake = FakeLLM(responses if responses is not None else [{"narrative": _VALID_NARRATIVE}])
    narrator = LLMNarratorAgent(fake, fallback=TemplateNarrator())
    text, source = narrator.render_with_meta(
        action=action,
        events=list(events or []),
        state=state,
        result_data=dict(result_data or {}),
    )
    return fake, text, source


def _prompt(fake: FakeLLM) -> str:
    """首次 LLM 请求的 system + user 全文（断言事实供给用）。"""
    request = fake.calls[0]
    return "\n".join(message.content for message in request.messages)


def _assert_no_internal_ids(prompt: str) -> None:
    """prompt 中不得出现剧情场景的任何内部 ULID 前缀（实体一律展示名）。"""
    for prefix in ("item_01J9Z3K4Q7X8Y9Z0ABCDEFGHK", "room_01J9Z3K4Q7X8Y9Z0ABCDEFGHK"):
        assert prefix not in prompt, f"prompt 泄漏内部 ID 前缀: {prefix}"


# ---------------------------------------------------------------------------
# 事实供给：写事件
# ---------------------------------------------------------------------------
def test_take_item_fact_uses_display_names() -> None:
    """ITEM_TAKEN：物品名与来源房间名进入 prompt，内部 ID 不出现。"""
    state = _state_at(_HUB_NAME)
    event = Event(
        turn_id=2,
        actor_id=PLAYER_ID,
        method="take_item",
        event_type=EventType.ITEM_TAKEN,
        params={"item_id": _item_id(state, _TORCH_NAME), "from_room": _room_id(state, _HUB_NAME)},
        result={"inventory_size": 1},
    )

    fake, text, source = _narrate(state=state, action=_outcome("take_item"), events=[event])

    prompt = _prompt(fake)
    assert source == "llm"
    assert text == _VALID_NARRATIVE
    assert _TORCH_NAME in prompt
    assert _HUB_NAME in prompt
    assert _item_id(state, _TORCH_NAME) not in prompt
    assert _room_id(state, _HUB_NAME) not in prompt
    _assert_no_internal_ids(prompt)


def test_move_fact_mentions_destination_and_room_after_turn() -> None:
    """CHARACTER_MOVED：中文方位 + 目标房间名 + room_after_turn（修正“走错房间”叙述）。"""
    state = _state_at(_HUB_NAME)
    event = Event(
        turn_id=3,
        actor_id=PLAYER_ID,
        method="move_to",
        event_type=EventType.CHARACTER_MOVED,
        params={"direction": "north", "from_room": _room_id(state, _HUB_NAME)},
        result={"to": _room_id(state, _CELLAR_NAME)},
    )

    fake, _text, source = _narrate(state=state, action=_outcome("move_to"), events=[event])

    prompt = _prompt(fake)
    assert source == "llm"
    assert "北" in prompt
    assert _CELLAR_NAME in prompt
    assert '"room_after_turn"' in prompt
    assert _room_id(state, _CELLAR_NAME) not in prompt
    _assert_no_internal_ids(prompt)


def test_dialogue_fact_uses_npc_name_and_reply_but_not_player_text() -> None:
    """DIALOGUE_EXCHANGED：NPC 展示名与 NPC 回话进入 prompt；玩家原话（topic）不进入。"""
    state = _state_at(_HUB_NAME)
    topic = "我想打听点私事，这跟信号灯没关系"
    branch = story_reply(STORY_WATCHMAN_ID, "信号灯怎么弄")
    assert branch is not None
    event = Event(
        turn_id=2,
        actor_id=PLAYER_ID,
        method="talk_to",
        event_type=EventType.DIALOGUE_EXCHANGED,
        params={"npc_id": STORY_WATCHMAN_ID, "topic": topic},
        result={"dialogue_state": branch["dialogue_state"], "reply": branch["reply"]},
    )

    fake, _text, _source = _narrate(state=state, action=_outcome("talk_to"), events=[event])

    prompt = _prompt(fake)
    assert "守夜人老兵" in prompt
    assert "信号火把" in prompt  # 来自 story_reply 的 NPC 回话
    assert topic not in prompt  # 玩家原话不进入 prompt（防注入/防复述）


# ---------------------------------------------------------------------------
# 事实供给：环境 / 只读查询 / 目标
# ---------------------------------------------------------------------------
def test_environment_lists_exit_display_names() -> None:
    """environment.exits：方向 → 目标房间名（入口的北出口 = 哨站地窖）。"""
    state = _state_at(_HUB_NAME)

    fake, _text, _source = _narrate(state=state, action=_outcome("get_room_view"))

    prompt = _prompt(fake)
    assert _CELLAR_NAME in prompt
    assert "北" in prompt
    assert _room_id(state, _CELLAR_NAME) not in prompt
    _assert_no_internal_ids(prompt)


def test_observed_inventory_uses_names_and_qty() -> None:
    """observed.items：只读查询的行囊清单投影为 ``{name, qty}``，不含 item_id。"""
    state = _state_at(_HUB_NAME)
    torch_id = _item_id(state, _TORCH_NAME)
    copper_ids = [item.id for item in state.items.values() if item.name == _COPPER_NAME]
    player = state.characters[PLAYER_ID].model_copy(update={"inventory": [torch_id, *copper_ids]})
    state = state.model_copy(update={"characters": {**state.characters, PLAYER_ID: player}})
    result_data = {
        "items": [
            {"item_id": torch_id, "name": _TORCH_NAME, "qty": 1},
            {"item_id": copper_ids[0], "name": _COPPER_NAME, "qty": 2},
        ]
    }

    fake, _text, _source = _narrate(
        state=state, action=_outcome("get_inventory"), result_data=result_data
    )

    prompt = _prompt(fake)
    assert _TORCH_NAME in prompt
    assert _COPPER_NAME in prompt
    assert '"qty": 2' in prompt
    assert torch_id not in prompt
    assert copper_ids[0] not in prompt


def test_observed_look_text_included() -> None:
    """observed.text：环顾四周的只读房间文本进入 prompt（不编造不可见信息）。"""
    state = _state_at()
    look_text = state.rooms[_room_id(state, "哨站外·断桥")].description

    fake, _text, _source = _narrate(
        state=state, action=_outcome("get_room_view"), result_data={"text": look_text}
    )

    assert look_text[:20] in _prompt(fake)


def test_story_goal_included_in_prompt() -> None:
    """story 场景：当前目标标题 + 三个目标步骤（含 listen）进入 prompt。"""
    state = _state_at()

    fake, _text, _source = _narrate(state=state, action=_outcome("get_room_view"))

    prompt = _prompt(fake)
    assert _GOAL_TITLE in prompt
    assert "问问守夜人老兵：信号灯怎么点" in prompt
    assert "找齐火把和两卷铜线，打造一支信号火把" in prompt


def test_non_story_state_has_no_goal_field() -> None:
    """m2 场景（非 story）不注入 goal 字段，事实供给保持通用。"""
    state = build_initial_state(save_seed=_SAVE_SEED)

    fake, _text, _source = _narrate(state=state, action=_outcome("get_room_view"))

    assert '"goal"' not in _prompt(fake)


# ---------------------------------------------------------------------------
# 协议与兜底：版本 / 长度 / 离线 / schema 失败
# ---------------------------------------------------------------------------
def test_request_contract_is_unchanged_except_prompt_version() -> None:
    """请求契约不变：task=narrate、ECONOMY、一次修复、turn_id 取事件回合、prompt 版本 v2。"""
    state = _state_at(_HUB_NAME)
    event = Event(
        turn_id=7,
        actor_id=PLAYER_ID,
        method="take_item",
        event_type=EventType.ITEM_TAKEN,
        params={"item_id": _item_id(state, _TORCH_NAME)},
        result={},
    )

    fake, text, source = _narrate(state=state, action=_outcome("take_item"), events=[event])

    request = fake.calls[0]
    assert source == "llm"
    assert 80 <= len(text) <= 150
    assert request.task == "narrate"
    assert request.agent_id == "narrator"
    assert request.turn_id == 7
    assert request.max_repair == 1
    assert request.prompt_version == PROMPT_VERSION == "narrate@v2"


def test_offline_client_falls_back_to_template() -> None:
    """默认离线客户端（DisabledLLMClient）→ 模板兜底且 source=template。"""
    narrator = LLMNarratorAgent(DisabledLLMClient(), fallback=TemplateNarrator())

    text, source = narrator.render_with_meta(
        action=_outcome("get_room_view"), events=[], state=_state_at(), result_data={}
    )

    assert source == "template"
    assert 80 <= len(text) <= 150


def test_schema_violation_twice_falls_back_to_template() -> None:
    """两次 schema 不合格 → 恰好一次修复后回退模板（source=template，不写不稳定输出）。"""
    fake, text, source = _narrate(
        state=_state_at(),
        action=_outcome("get_room_view"),
        responses=["not-json-at-all", '{"narrative": "太短"}'],
    )

    assert source == "template"
    assert len(fake.calls) == 2
    assert 80 <= len(text) <= 150


def test_fallback_text_never_raises_when_template_narrator_breaks() -> None:
    """模板叙事器自身异常时仍有通用短句兜底（叙事不影响回合提交）。"""

    class _BrokenNarrator:
        def render(self, **_kwargs: Any) -> str:
            raise RuntimeError("template broken")

    narrator = LLMNarratorAgent(DisabledLLMClient(), fallback=_BrokenNarrator())

    text, source = narrator.render_with_meta(
        action=_outcome("get_room_view"), events=[], state=_state_at(), result_data={}
    )

    assert source == "template"
    assert isinstance(text, str) and text


@pytest.mark.parametrize("narrative", [_VALID_NARRATIVE])
def test_valid_narrative_length_is_in_contract(narrative: str) -> None:
    """夹具叙事本身满足 80–150 字契约（防止测试夹具漂移）。"""
    assert 80 <= len(narrative) <= 150
