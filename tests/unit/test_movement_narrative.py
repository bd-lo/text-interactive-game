"""R3 P2-5：移动叙事方向词（仅 up/down 新分支；四方向保持旧模板逐字不变）。

约束：m2/m3/m4 golden 的移动方向只涉及 north/south/east/west，text_len 钉死；本改动只给
up/down 增加「爬梯/下梯」专项文案，其余方向必须保持「你沿着{方向}边的通道动身…」原模板。
"""

from __future__ import annotations

import pytest

from textgame.agents.dto import ParseOutcome
from textgame.domain.events import Event, EventType
from textgame.domain.scenario import PLAYER_ID, build_initial_state
from textgame.domain.scenario_story import build_story_state
from textgame.orchestration.narrator import TemplateNarrator

_SAVE_SEED = "save-42"
_MIN_TEXT_CHARS = 80
_MAX_TEXT_CHARS = 150
_OLD_MOVE_PREFIX = "你沿着{方向}边的通道动身"
_ACTION = ParseOutcome(tool="move_to", confidence=1.0, source="keyword")


def _move_event(*, direction: str, to_room: str) -> Event:
    """构造未盖章的 ``CHARACTER_MOVED`` 草稿事件（叙事器只读 params/result）。"""
    return Event(
        turn_id=2,
        actor_id=PLAYER_ID,
        method="move_to",
        event_type=EventType.CHARACTER_MOVED,
        params={"direction": direction, "from_room": ""},
        result={"to": to_room},
    )


def _render(direction: str, to_room: str, state) -> str:
    return TemplateNarrator().render(
        action=_ACTION,
        events=[_move_event(direction=direction, to_room=to_room)],
        state=state,
        result_data={},
    )


def test_up_direction_uses_climbing_narrative() -> None:
    """up（story 登瞭望塔）：不再出现「沿着上边」，改为爬梯文案，80–150 字且确定性。"""
    story = build_story_state(save_seed=_SAVE_SEED)
    tower = next(room for room in story.rooms.values() if "瞭望塔" in room.name)

    text = _render("up", tower.id, story)
    second = _render("up", tower.id, story)

    assert text == second
    assert "沿着上边" not in text
    assert "上边" not in text
    assert "窄梯" in text and "往上走" in text
    assert tower.name in text
    assert _MIN_TEXT_CHARS <= len(text) <= _MAX_TEXT_CHARS


def test_down_direction_uses_descending_narrative() -> None:
    """down（story 下塔）：不再出现「沿着下边」，改为下梯文案，80–150 字且确定性。"""
    story = build_story_state(save_seed=_SAVE_SEED)
    hub = next(room for room in story.rooms.values() if "入口" in room.name)

    text = _render("down", hub.id, story)
    second = _render("down", hub.id, story)

    assert text == second
    assert "沿着下边" not in text
    assert "下边" not in text
    assert "窄梯" in text and "往下走" in text
    assert hub.name in text
    assert _MIN_TEXT_CHARS <= len(text) <= _MAX_TEXT_CHARS


@pytest.mark.parametrize(
    ("direction", "direction_cn"),
    [("north", "北"), ("south", "南"), ("east", "东"), ("west", "西")],
)
def test_four_directions_keep_legacy_template(direction: str, direction_cn: str) -> None:
    """north/south/east/west 文案逐字保持旧模板前缀（golden 钉死路径，不得改动）。"""
    m2 = build_initial_state(save_seed=_SAVE_SEED)
    room = next(iter(m2.rooms.values()))

    text = _render(direction, room.id, m2)

    assert text.startswith(_OLD_MOVE_PREFIX.format(方向=direction_cn))
    assert "窄梯" not in text
    assert _MIN_TEXT_CHARS <= len(text) <= _MAX_TEXT_CHARS
