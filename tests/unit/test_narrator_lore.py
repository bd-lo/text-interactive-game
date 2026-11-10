"""单测：TemplateNarrator 世界设定追加（H4-CONTRACT §5 / Lead 实测 bug 修复）。

覆盖：
- 正文先按句末标点收敛到「150 - 设定句 - 分隔符」预算，再以句号分隔拼接 —— **任何情况下
  不得句中截断**（回归 Lead 实测的「你在雪里走你隐约想起…」bug）；
- 设定句只追加 1 条、按完整句截到 ≤60 字；取不出完整句换下一条；
- 有 lore 的 look/inspect 屏仍在 80–150 字且以句末标点收尾、设定内容可见；
- 无 lore / 旧场景路径逐字不变。
"""

from __future__ import annotations

from textgame.agents.dto import ParseOutcome
from textgame.domain.events import Event, EventType
from textgame.domain.models import ActionIntent
from textgame.domain.scenario import PLAYER_ID, build_initial_state
from textgame.domain.scenario_story import STORY_ENTRY_ROOM_ID, build_story_state
from textgame.orchestration.narrator import TemplateNarrator, _join_lore

_SAVE_SEED = "save-42"
_SENTENCE_END = "。！？；…"
_BRIDGE_LORE = (
    "余烬哨站立在封冻的河谷北岸，风把雪吹成一道一道的白线，灯灭了整整两夜。"
    "这条路上已经很久没有活人经过了。"
)


def _outcome(tool: str) -> ParseOutcome:
    """构造只读动作解析结果（与编排层传给叙事器的对象同构）。"""
    return ParseOutcome(
        intent=ActionIntent(action=tool, params={}, confidence=1.0),
        tool=tool,
        args={},
        confidence=1.0,
        source="keyword",
    )


def _story_look_result_data() -> dict[str, object]:
    """story 断桥 look 的 result_data（长房间描述，复现 Lead 实测场景）。"""
    state = build_story_state(save_seed=_SAVE_SEED)
    room = state.rooms[STORY_ENTRY_ROOM_ID]
    return {"text": f"{room.name}。{room.description}"}


def _render_look(result_data: dict[str, object]) -> str:
    """渲染 story 断桥 look。"""
    return TemplateNarrator().render(
        action=_outcome("get_room_view"),
        events=[],
        state=build_story_state(save_seed=_SAVE_SEED),
        result_data=result_data,
    )


def _assert_screen(text: str) -> None:
    """一屏文本口径：80–150 字且以句末标点收尾。"""
    assert 80 <= len(text) <= 150, (len(text), text)
    assert text[-1] in _SENTENCE_END, text


def test_long_base_cut_at_sentence_boundary_not_mid_sentence() -> None:
    """Lead 实测回归：长房间描述 + 长设定句 → 正文在句末截断、句号分隔、不出现「走你隐约」。"""
    result_data = _story_look_result_data()
    result_data["lore_entries"] = [f"【世界设定】{_BRIDGE_LORE}"]

    text = _render_look(result_data)

    _assert_screen(text)
    assert "你隐约想起一段旧事：" in text
    assert "余烬哨站立在封冻的河谷北岸" in text
    base_part, lore_part = text.split("你隐约想起一段旧事：", 1)
    assert base_part.endswith("。"), base_part  # 正文以完整句结束
    assert "走你" not in text  # 无「你在雪里走你隐约」这类缺标点拼接
    assert "走。你隐约" in text or base_part.endswith("。")  # 有标点分隔
    assert lore_part.endswith("。")  # 设定句完整
    # 房间里的一句完整描述必须保留（证明不是硬截到句中）
    assert "旧伤在肋下隐隐作痛。" in base_part


def test_only_one_lore_entry_is_appended() -> None:
    """设定句只追加 1 条（第一条可用完整句），后续条目不出现在屏里。"""
    result_data = _story_look_result_data()
    result_data["lore_entries"] = [
        "【世界设定】枯井的井底比外头暖和。",
        "【世界设定】老兵不肯往河边走。",
    ]

    text = _render_look(result_data)

    _assert_screen(text)
    assert "枯井的井底比外头暖和。" in text
    assert "老兵不肯往河边走。" not in text
    assert text.count("你隐约想起一段旧事：") == 1


def test_long_lore_content_is_truncated_at_sentence_boundary() -> None:
    """设定句正文超 60 字 → 按完整句截取（不超过 60 字），整屏仍 80–150。"""
    long_content = (
        "哨站的墙根下埋着旧年的火种与冷灰。"
        "铁匠铺的炉灰里还留着上一个人的余温。"
        "瞭望塔的梯子被雪压弯过，又被人一寸寸重新钉好。"
        "第四句完整的话，这句一定放不下。"
    )
    result_data = _story_look_result_data()
    result_data["lore_entries"] = [f"【世界设定】{long_content}"]

    text = _render_look(result_data)

    _assert_screen(text)
    lore_sentence = text.split("你隐约想起一段旧事：", 1)[1]
    assert len(lore_sentence) <= 60
    assert lore_sentence[-1] in _SENTENCE_END
    assert "哨站的墙根下埋着旧年的火种与冷灰。" in lore_sentence
    assert "第四句完整的话" not in lore_sentence


def test_lore_entry_without_sentence_boundary_falls_back_to_next_entry() -> None:
    """第一条取不出完整句（无标点且 >60 字）→ 换下一条完整句。"""
    result_data = _story_look_result_data()
    result_data["lore_entries"] = [
        "【世界设定】" + "没有句号的超长设定" * 10,
        "【世界设定】这条有完整句子。",
    ]

    text = _render_look(result_data)

    _assert_screen(text)
    assert "这条有完整句子。" in text
    assert "没有句号的超长设定" not in text


def test_base_without_sentence_end_gets_punctuation_separator() -> None:
    """正文末尾没有句末标点 → 拼接时自动补「。」（不出现「走你隐约」式粘连）。"""
    assert (
        _join_lore("你站在原地，风从北面吹来", "你隐约想起一段旧事：风里带着雪。")
        == "你站在原地，风从北面吹来。你隐约想起一段旧事：风里带着雪。"
    )
    assert (
        _join_lore("你在原地站定。", "你隐约想起一段旧事：风里带着雪。")
        == "你在原地站定。你隐约想起一段旧事：风里带着雪。"
    )


def test_render_with_lore_keeps_complete_screen() -> None:
    """正文缺句末标点 + 设定句的整屏渲染仍完整（80–150、句末收尾、设定可见）。"""
    result_data = {"text": "你站在原地，风从北面吹来"}
    result_data["lore_entries"] = ["【世界设定】风里带着雪。"]
    state = build_story_state(save_seed=_SAVE_SEED)

    text = TemplateNarrator().render(
        action=_outcome("get_room_view"), events=[], state=state, result_data=result_data
    )

    _assert_screen(text)
    assert (
        "风。你隐约想起一段旧事：风里带着雪。" in text or "你隐约想起一段旧事：风里带着雪。" in text
    )
    assert "来你隐约" not in text


def test_lore_sentence_keeps_screen_punctuation_ending() -> None:
    """拼接后整屏仍以句末标点收尾（不会以设定句中的半句结束）。"""
    result_data = _story_look_result_data()
    result_data["lore_entries"] = [f"【世界设定】{_BRIDGE_LORE}"]

    text = _render_look(result_data)

    assert text[-1] in _SENTENCE_END
    assert not text.endswith("走")


def test_inspect_with_lore_is_complete_sentence_screen() -> None:
    """inspect 动作同样保证完整句 + 句号分隔 + 80–150 字。"""
    state = build_story_state(save_seed=_SAVE_SEED)
    watchman = state.npcs[next(iter(state.npcs))]
    result_data = {
        "name": watchman.name,
        "description": (
            "老兵坐在火盆边，没有抬头，手边放着一块磨得发亮的火石，"
            "他袖口上结着霜，像是刚从外头回来。"
        ),
        "kind": "npc",
        "lore_entries": ["【世界设定】老兵守了这座哨站十七年。"],
    }

    text = TemplateNarrator().render(
        action=_outcome("inspect_item"), events=[], state=state, result_data=result_data
    )

    _assert_screen(text)
    assert "你隐约想起一段旧事：老兵守了这座哨站十七年。" in text
    assert "你隐约想起" in text


def test_no_lore_render_is_byte_identical_for_look_and_inspect() -> None:
    """无 lore（缺键/空列表/None）三种旧路径逐字相同（旧场景零漂移）。"""
    narrator = TemplateNarrator()
    state = build_story_state(save_seed=_SAVE_SEED)
    look_data = _story_look_result_data()
    inspect_data = {
        "name": "火把",
        "description": "一支受潮的火把，布头上还留着松脂的气味。",
        "kind": "item",
    }

    for tool, data in (("get_room_view", look_data), ("inspect_item", inspect_data)):
        baseline = narrator.render(
            action=_outcome(tool), events=[], state=state, result_data=dict(data)
        )
        empty = narrator.render(
            action=_outcome(tool), events=[], state=state, result_data={**data, "lore_entries": []}
        )
        none_value = narrator.render(
            action=_outcome(tool),
            events=[],
            state=state,
            result_data={**data, "lore_entries": None},
        )
        assert baseline == empty == none_value
        assert "你隐约想起" not in baseline


def test_old_m2_look_without_lore_is_unchanged() -> None:
    """旧 m2 场景 look（无 lore 字段）文本仍为既有模板（不含设定句）。"""
    state = build_initial_state(save_seed=_SAVE_SEED)
    result_data = {"text": "废弃哨站·入口。风从断裂的门楣间穿过。"}

    text = TemplateNarrator().render(
        action=_outcome("get_room_view"), events=[], state=state, result_data=result_data
    )

    _assert_screen(text)
    assert "你隐约想起" not in text
    assert text.startswith("废弃哨站·入口。")


def test_write_action_still_ignores_lore_entries() -> None:
    """写动作（CHARACTER_MOVED）不追加设定句（H4-CONTRACT §5 只读动作限定）。"""
    state = build_story_state(save_seed=_SAVE_SEED)
    moved = Event(
        turn_id=2,
        actor_id=PLAYER_ID,
        method="move_to",
        event_type=EventType.CHARACTER_MOVED,
        params={"direction": "east", "from_room": STORY_ENTRY_ROOM_ID},
        result={"to": "room_01J9Z3K4Q7X8Y9Z0ABCDEFGHK3"},
    )
    narrator = TemplateNarrator()

    base = narrator.render(action=_outcome("move_to"), events=[moved], state=state, result_data={})
    with_lore = narrator.render(
        action=_outcome("move_to"),
        events=[moved],
        state=state,
        result_data={"lore_entries": ["【世界设定】不该出现。"]},
    )

    assert base == with_lore
    assert "不该出现" not in with_lore
