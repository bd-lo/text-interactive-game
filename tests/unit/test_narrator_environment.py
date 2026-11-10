"""单测：TemplateNarrator 的 H4/L1 环境伤害（``移动 + ENVIRONMENT_DAMAGE``）专属叙事屏。

覆盖：三档文案（hp_after≥8 / 1–7 / 0）、边界值、缺 payload 回退 S1、非负伤害回退移动模板、
只有伤害无移动不误命中、旧场景（仅移动事件）文本逐字不变、长度 80–150、确定性。
"""

from __future__ import annotations

import pytest

from textgame.agents.dto import ParseOutcome
from textgame.domain.events import Event, EventType
from textgame.domain.models import ActionIntent
from textgame.domain.scenario import PLAYER_ID
from textgame.domain.scenario_story import STORY_ENTRY_ROOM_ID, build_story_state
from textgame.orchestration.narrator import TemplateNarrator

_SAVE_SEED = "save-42"
_TARGET_ROOM = "room_01J9Z3K4Q7X8Y9Z0ABCDEFGHK3"


def _state():
    """story 初始状态（断桥）。"""
    return build_story_state(save_seed=_SAVE_SEED)


def _moved_event() -> Event:
    """移动草稿事件（断桥 → 入口方向）。"""
    return Event(
        turn_id=2,
        actor_id=PLAYER_ID,
        method="move_to",
        event_type=EventType.CHARACTER_MOVED,
        params={"direction": "east", "from_room": STORY_ENTRY_ROOM_ID},
        result={"to": _TARGET_ROOM},
    )


def _damage_event(*, hp_delta: int = -6) -> Event:
    """环境伤害草稿事件（枯井寒气）。"""
    return Event(
        turn_id=2,
        actor_id=PLAYER_ID,
        method="environment_damage",
        event_type=EventType.ENVIRONMENT_DAMAGE,
        params={"room_id": _TARGET_ROOM},
        result={"hp_delta": hp_delta},
    )


def _action(tool: str = "move_to") -> ParseOutcome:
    """构造解析结果（与编排层传给叙事器的对象同构）。"""
    return ParseOutcome(
        intent=ActionIntent(action=tool, params={}, confidence=1.0),
        tool=tool,
        args={},
        confidence=1.0,
        source="keyword",
    )


def _render(hp_after: int | None, *, hp_delta: int = -6, events: list[Event] | None = None) -> str:
    """按 hp_after 渲染一屏（``None`` = 缺 payload）。"""
    result_data: dict[str, object] = {}
    if hp_after is not None:
        result_data["environment"] = {
            "room_id": _TARGET_ROOM,
            "hp_delta": hp_delta,
            "hp_after": hp_after,
            "max_hp": 20,
        }
    return TemplateNarrator().render(
        action=_action(),
        events=list(events)
        if events is not None
        else [_moved_event(), _damage_event(hp_delta=hp_delta)],
        state=_state(),
        result_data=result_data,
    )


# ---------------------------------------------------------------------------
# 三档文案
# ---------------------------------------------------------------------------


def test_light_tier_when_hp_after_at_least_eight() -> None:
    """hp_after=8（边界）→ S1 轻度：「这地方不对劲」，屏幕 80–150 字。"""
    text = _render(8)

    assert "这地方不对劲" in text
    assert "再下去一次" not in text
    assert 80 <= len(text) <= 150


def test_severe_tier_when_hp_after_between_one_and_seven() -> None:
    """hp_after=7 / 1（两端）→ S2 重度：含「再下去一次，你多半就上不来了」死亡警告。"""
    for hp_after in (7, 1):
        text = _render(hp_after)
        assert "再下去一次，你多半就上不来了" in text, hp_after
        assert 80 <= len(text) <= 150, hp_after


def test_fatal_tier_when_hp_after_zero() -> None:
    """hp_after=0 → S3 致命：与「倒下」结局衔接的收尾屏。"""
    text = _render(0)

    assert "然后，风停了" in text
    assert "再下去一次" not in text
    assert 80 <= len(text) <= 150


def test_hazard_tiers_are_mutually_exclusive_screens() -> None:
    """三档返回三份不同文案（不会重复/拼接移动模板）。"""
    light, severe, fatal = _render(8), _render(7), _render(0)

    assert len({light, severe, fatal}) == 3
    assert "你沿着" not in light  # 移动模板「你沿着…边的通道动身」不得出现
    assert "你沿着" not in severe
    assert "你沿着" not in fatal


# ---------------------------------------------------------------------------
# 回退与边界
# ---------------------------------------------------------------------------


def test_missing_payload_falls_back_to_light_when_damage_negative() -> None:
    """缺 ``result_data["environment"]`` 且伤害为负 → 保守回退 S1。"""
    text = _render(None)

    assert "这地方不对劲" in text


def test_missing_payload_with_non_negative_delta_falls_back_to_move_template() -> None:
    """缺 payload 且伤害非负（非本设计口径）→ 走普通移动模板（不误报寒气）。"""
    text = _render(None, hp_delta=3)

    assert "这地方不对劲" not in text
    assert "你沿着" in text


def test_hazard_branch_requires_both_events() -> None:
    """只有 ENVIRONMENT_DAMAGE、没有 CHARACTER_MOVED → 不命中专属屏。"""
    text = _render(6, events=[_damage_event()])

    assert "这地方不对劲" not in text
    assert "再下去一次" not in text


def test_old_scene_move_text_is_byte_identical_without_hazard_events() -> None:
    """旧场景（仅 CHARACTER_MOVED、无 environment payload）→ 文本与无 payload 渲染逐字相同。"""
    narrator = TemplateNarrator()
    state = _state()
    moved = _moved_event()

    baseline = narrator.render(action=_action(), events=[moved], state=state, result_data={})
    with_empty_env = narrator.render(
        action=_action(), events=[moved], state=state, result_data={"environment": None}
    )

    assert baseline == with_empty_env
    assert "寒气" not in baseline
    assert "你沿着" in baseline


def test_hazard_render_is_deterministic() -> None:
    """同一事件/状态两次渲染逐字相同（可回放）。"""
    assert _render(6) == _render(6)


@pytest.mark.parametrize("hp_after", [8, 7, 1, 0])
def test_hazard_text_length_within_screen_budget(hp_after: int) -> None:
    """四档边界屏文本均在 80–150 字口径内。"""
    assert 80 <= len(_render(hp_after)) <= 150
