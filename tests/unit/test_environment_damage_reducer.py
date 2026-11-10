"""H4/L1 单元测试：``ENVIRONMENT_DAMAGE`` 事件 + reducer + story 房间伤害 + L3 字段。

覆盖：事件类型已登记/已注册 reducer；reducer 只改 actor 的 hp（clamp 0..max、0 伤害时状态
除公共规则外不变）；非法 ``hp_delta``（缺失/0/bool/非 int）→ ``ValueError``；``story_room_damage``
只对 story 枯井返回 -6 且纯函数；``TurnResult.narrative_source`` 加法字段默认 None/可填/可回放。
"""

from __future__ import annotations

import pytest

from textgame.domain.events import Event, EventType
from textgame.domain.models import GameState
from textgame.domain.reducers import apply_events
from textgame.domain.results import ErrorCode
from textgame.domain.scenario import PLAYER_ID, build_initial_state
from textgame.domain.scenario_story import (
    STORY_TOWER_ROOM_ID,
    STORY_WELL_ROOM_ID,
    build_story_state,
    story_room_damage,
)
from textgame.services.dto import TurnResult

_SAVE_SEED = "save-42"


def _damage_event(*, hp_delta: object, actor_id: str = PLAYER_ID, turn_id: int = 2) -> Event:
    """构造 ``ENVIRONMENT_DAMAGE`` 草稿事件（result.hp_delta 可传任意非法值用于反例）。"""
    return Event(
        turn_id=turn_id,
        actor_id=actor_id,
        method="environment_damage",
        event_type=EventType.ENVIRONMENT_DAMAGE,
        params={"room_id": STORY_WELL_ROOM_ID},
        result={"hp_delta": hp_delta},
    )


def _story_state() -> GameState:
    """story 初始状态（玩家 12/20 HP，位于断桥）。"""
    return build_story_state(save_seed=_SAVE_SEED)


def test_environment_damage_event_type_is_registered() -> None:
    """``EventType.ENVIRONMENT_DAMAGE`` 已登记，且有 reducer（apply 不抛未注册错误）。"""
    assert EventType.ENVIRONMENT_DAMAGE.value == "ENVIRONMENT_DAMAGE"
    state = _story_state()
    result = apply_events(state, [_damage_event(hp_delta=-6)])
    assert result.characters[PLAYER_ID].hp == 6


def test_environment_damage_only_changes_hp() -> None:
    """伤害事件只改 hp：背包/位置/物品/NPC/任务均不变（turn/world_time 按公共规则推进）。"""
    state = _story_state()
    player = state.characters[PLAYER_ID].model_copy(
        update={"inventory": [], "location": "room_" + "01J9Z3K4Q7X8Y9Z0ABCDEFGHK2"}
    )
    state = state.model_copy(update={"characters": {**state.characters, PLAYER_ID: player}})
    result = apply_events(state, [_damage_event(hp_delta=-6)])

    before = state.characters[PLAYER_ID].model_dump()
    after = result.characters[PLAYER_ID].model_dump()
    assert after["hp"] == before["hp"] - 6
    for key in set(before) - {"hp"}:
        assert after[key] == before[key], key
    assert result.items == state.items
    assert result.npcs == state.npcs
    assert result.rooms == state.rooms
    assert result.turn == 2


def test_environment_damage_clamps_at_zero() -> None:
    """过量伤害 clamp 到 0，不会出现负 HP（``Character.hp >= 0`` 模型约束不破）。"""
    result = apply_events(_story_state(), [_damage_event(hp_delta=-999)])
    assert result.characters[PLAYER_ID].hp == 0


def test_environment_damage_positive_heals_and_caps_at_max_hp() -> None:
    """正 ``hp_delta`` 按有符号处理并封顶 max_hp（复用同一 reducer，便于未来恢复类事件）。"""
    state = _story_state()
    healed = apply_events(state, [_damage_event(hp_delta=3)])
    assert healed.characters[PLAYER_ID].hp == 15
    capped = apply_events(state, [_damage_event(hp_delta=999)])
    assert capped.characters[PLAYER_ID].hp == 20


@pytest.mark.parametrize("hp_delta", [None, 0, True, False, "很多", 2.5])
def test_environment_damage_rejects_invalid_hp_delta(hp_delta: object) -> None:
    """缺失/0/bool/非 int 的 ``hp_delta`` → ``ValueError``（回放禁止静默跳过坏事件）。"""
    with pytest.raises(ValueError, match="hp_delta"):
        apply_events(_story_state(), [_damage_event(hp_delta=hp_delta)])


def test_environment_damage_unknown_actor_raises() -> None:
    """actor 不在状态中 → ``ValueError``（不做部分投影）。"""
    with pytest.raises(ValueError, match="not in state"):
        apply_events(
            _story_state(),
            [_damage_event(hp_delta=-6, actor_id="char_01J9Z3K4Q7X8Y9Z0ABCDEFGHJX")],
        )


def test_story_room_damage_only_for_well() -> None:
    """``story_room_damage``：枯井 -6；其他 story 房间/旧场景房间/未知 → None。"""
    assert story_room_damage(STORY_WELL_ROOM_ID) == -6
    assert story_room_damage(STORY_TOWER_ROOM_ID) is None
    assert story_room_damage("room_01J9Z3K4Q7X8Y9Z0ABCDEFGHJK") is None
    assert story_room_damage("room_unknown") is None
    # 纯函数：同一入参两次调用结果相等
    assert story_room_damage(STORY_WELL_ROOM_ID) == story_room_damage(STORY_WELL_ROOM_ID)


def test_legacy_scenario_state_never_has_well_room() -> None:
    """旧 m2 场景不含 story 枯井房间 → 旧路径永不命中 room damage（golden 零影响）。"""
    legacy = build_initial_state(save_seed=_SAVE_SEED)
    assert STORY_WELL_ROOM_ID not in legacy.rooms


def test_turn_result_narrative_source_is_additive() -> None:
    """L3：``TurnResult.narrative_source`` 默认 None；``success()`` 可填；序列化可回放。"""
    default = TurnResult.success(version=1, text="x")
    assert default.narrative_source is None

    filled = TurnResult.success(version=2, text="x", narrative_source="template")
    assert filled.narrative_source == "template"
    restored = TurnResult.model_validate(filled.model_dump(mode="json"))
    assert restored == filled
    assert restored.narrative_source == "template"

    failed = TurnResult.failed(ErrorCode.INVALID_INPUT, version=3, text="x")
    assert failed.narrative_source is None
