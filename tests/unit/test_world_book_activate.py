"""单测：``domain/world_book.py`` 确定性触发引擎（H4-CONTRACT §3 激活语义）。

覆盖（≥20 例）：constant、主关键词大小写/整词、secondary + selective_logic 四种模式、
selective=False、disabled 永不激活、scan_depth 窗口、order/uid 排序、char_budget/max_entries
裁剪、递归（深度 2 / exclude_recursion / prevent_recursion）、确定性概率（同参可回放）、
空书/空上下文、中文关键词。
"""

from __future__ import annotations

import pytest

from textgame.domain.scenario import PLAYER_ID, build_initial_state
from textgame.domain.world_book import (
    WorldBook,
    WorldBookEntry,
    activate_entries,
    build_activation_context,
    format_lore_entries,
)

_SAVE_SEED = "save-42"


def _entry(**overrides: object) -> WorldBookEntry:
    """构造测试条目（默认：无关键词、无内容的最小合法值）。"""
    base: dict[str, object] = {
        "uid": 0,
        "keys": (),
        "secondary_keys": (),
        "content": "",
    }
    base.update(overrides)
    return WorldBookEntry(**base)  # type: ignore[arg-type]


def _book(*entries: WorldBookEntry) -> WorldBook:
    """构造测试世界书（tavern 来源，便于区分默认值）。"""
    return WorldBook(name="测试世界书", entries=tuple(entries), source="tavern", raw={})


def _activate(
    book: WorldBook,
    context: list[str],
    *,
    turn_id: int = 2,
    max_entries: int = 8,
    char_budget: int = 800,
) -> list[WorldBookEntry]:
    """按固定 seed 激活（测试默认参数与生产一致）。"""
    return activate_entries(
        book,
        context_texts=context,
        turn_id=turn_id,
        save_seed=_SAVE_SEED,
        max_entries=max_entries,
        char_budget=char_budget,
    )


def _uids(entries: list[WorldBookEntry]) -> list[int]:
    """激活结果 → uid 列表（断言顺序用）。"""
    return [entry.uid for entry in entries]


# ---------------------------------------------------------------------------
# constant / 关键词 / 大小写 / 整词
# ---------------------------------------------------------------------------


def test_constant_entry_activates_without_keywords() -> None:
    """constant=True → 无关键词也激活（空上下文亦可）。"""
    book = _book(_entry(uid=1, constant=True, content="哨站的灯灭了。"))

    assert _uids(_activate(book, [])) == [1]


def test_disabled_entry_never_activates() -> None:
    """enabled=False 的条目即使 constant + 关键词命中也不激活。"""
    book = _book(
        _entry(uid=1, enabled=False, constant=True, content="不会被看到。"),
        _entry(uid=2, enabled=False, keys=("火把",), content="也不会。"),
    )

    assert _activate(book, ["火把"]) == []


def test_primary_keyword_case_insensitive_by_default() -> None:
    """默认大小写不敏感（ASCII 关键词）。"""
    book = _book(_entry(uid=1, keys=("Signal",), content="信号灯。"))

    assert _uids(_activate(book, ["the SIGNAL tower"])) == [1]


def test_case_sensitive_flag_requires_exact_case() -> None:
    """case_sensitive=True 时大小写必须一致。"""
    book = _book(_entry(uid=1, keys=("Signal",), case_sensitive=True, content="信号灯。"))

    assert _activate(book, ["signal tower"]) == []
    assert _uids(_activate(book, ["Signal tower"])) == [1]


def test_match_whole_words_for_ascii_key() -> None:
    """match_whole_words=True：ASCII 键不做子串误配（cat ≠ concatenate）。"""
    book = _book(_entry(uid=1, keys=("cat",), match_whole_words=True, content="猫。"))

    assert _activate(book, ["concatenate everything"]) == []
    assert _uids(_activate(book, ["a cat sleeps"])) == [1]


def test_chinese_keyword_matches_substring() -> None:
    """中文关键词按子串匹配（整词开关不影响中文）。"""
    book = _book(_entry(uid=1, keys=("枯井",), content="井底有寒气。"))

    assert _uids(_activate(book, ["你走向枯井旁边"])) == [1]


# ---------------------------------------------------------------------------
# secondary + selective_logic（0 AND_ANY / 1 NOT_ALL / 2 NOT_ANY / 3 AND_ALL）
# ---------------------------------------------------------------------------


def test_selective_and_any_requires_one_secondary() -> None:
    """0 AND_ANY：secondary 任一命中且主关键词命中 → 激活。"""
    book = _book(_entry(uid=1, keys=("哨站",), secondary_keys=("雪", "夜"), content="很冷。"))

    assert _uids(_activate(book, ["哨站", "夜里下雪"])) == [1]
    assert _activate(book, ["哨站", "白天"]) == []


def test_selective_not_all_requires_missing_secondary() -> None:
    """1 NOT_ALL：secondary 并非全部命中才激活（缺一个即可）。"""
    book = _book(
        _entry(
            uid=1,
            keys=("哨站",),
            secondary_keys=("雪", "夜"),
            selective_logic=1,
            content="很冷。",
        )
    )

    assert _uids(_activate(book, ["哨站", "雪"])) == [1]
    assert _activate(book, ["哨站", "雪夜"]) == []


def test_selective_not_any_requires_no_secondary() -> None:
    """2 NOT_ANY：secondary 一个都不命中才激活。"""
    book = _book(
        _entry(
            uid=1,
            keys=("哨站",),
            secondary_keys=("雪", "夜"),
            selective_logic=2,
            content="很冷。",
        )
    )

    assert _uids(_activate(book, ["哨站", "风"])) == [1]
    assert _activate(book, ["哨站", "夜里下雪"]) == []


def test_selective_and_all_requires_all_secondary() -> None:
    """3 AND_ALL：secondary 全部命中才激活。"""
    book = _book(
        _entry(
            uid=1,
            keys=("哨站",),
            secondary_keys=("雪", "夜"),
            selective_logic=3,
            content="很冷。",
        )
    )

    assert _uids(_activate(book, ["哨站", "雪", "夜"])) == [1]
    assert _activate(book, ["哨站", "雪"]) == []


def test_selective_false_ignores_secondary() -> None:
    """selective=False：只看主关键词，secondary 不参与判定。"""
    book = _book(
        _entry(
            uid=1,
            keys=("哨站",),
            secondary_keys=("雪",),
            selective=False,
            content="很冷。",
        )
    )

    assert _uids(_activate(book, ["哨站"])) == [1]


# ---------------------------------------------------------------------------
# 上下文窗口 scan_depth
# ---------------------------------------------------------------------------


def test_scan_depth_limits_context_window() -> None:
    """scan_depth=1：只扫最后 1 条上下文，早期文本里的关键词不触发。"""
    book = _book(_entry(uid=1, keys=("火把",), scan_depth=1, content="火把很旧。"))

    assert _activate(book, ["火把", "后来风停了"]) == []
    assert _uids(_activate(book, ["后来风停了", "火把"])) == [1]


def test_default_scan_depth_is_four() -> None:
    """scan_depth 缺省 4：第 5 条之前的文本不进窗口。"""
    book = _book(_entry(uid=1, keys=("火把",), content="火把很旧。"))
    context = ["火把", "二", "三", "四", "五"]

    assert _activate(book, context) == []
    assert _uids(_activate(book, ["二", "三", "四", "火把"])) == [1]


def test_scan_depth_zero_means_empty_window() -> None:
    """scan_depth=0：窗口为空，普通条目永不命中（constant 仍生效）。"""
    book = _book(
        _entry(uid=1, keys=("火把",), scan_depth=0, content="火把很旧。"),
        _entry(uid=2, constant=True, content="常亮。"),
    )

    assert _uids(_activate(book, ["火把"])) == [2]


# ---------------------------------------------------------------------------
# 排序 / 预算 / 条数上限
# ---------------------------------------------------------------------------


def test_order_then_uid_sorting() -> None:
    """结果按 (order 升序, uid 升序) 排序。"""
    book = _book(
        _entry(uid=5, keys=("火把",), order=200, content="E"),
        _entry(uid=2, keys=("火把",), order=10, content="B"),
        _entry(uid=9, keys=("火把",), order=10, content="C"),
        _entry(uid=1, keys=("火把",), order=20, content="D"),
    )

    assert _uids(_activate(book, ["火把"])) == [2, 9, 1, 5]


def test_char_budget_truncates_by_order() -> None:
    """char_budget 按排序累加 content 长度（含 1 字节分隔符）截断。"""
    book = _book(
        _entry(uid=1, order=1, keys=("火把",), content="aaaa"),
        _entry(uid=2, order=2, keys=("火把",), content="bbbb"),
        _entry(uid=3, order=3, keys=("火把",), content="cccc"),
    )

    # 4 + 1(分隔) + 4 = 9 ≤ 9；再 +1+4 = 14 > 9 → 只保留前两条。
    assert _uids(_activate(book, ["火把"], char_budget=9)) == [1, 2]
    assert _uids(_activate(book, ["火把"], char_budget=8)) == [1]
    assert _ids_empty(_activate(book, ["火把"], char_budget=0))


def _ids_empty(entries: list[WorldBookEntry]) -> bool:
    """辅助断言：结果为空列表。"""
    return entries == []


def test_max_entries_cap() -> None:
    """max_entries 上限：超过上限的条目即使预算足够也不注入。"""
    book = _book(
        *[_entry(uid=index, order=index, keys=("火把",), content="x") for index in range(1, 6)]
    )

    assert _uids(_activate(book, ["火把"], max_entries=3)) == [1, 2, 3]
    assert _activate(book, ["火把"], max_entries=0) == []


# ---------------------------------------------------------------------------
# 递归（深度 ≤2 / exclude / prevent）
# ---------------------------------------------------------------------------


def test_recursion_second_round_triggers_related_entry() -> None:
    """首轮激活条目 content 追加进上下文 → 第二轮触发只由该 content 命中的条目。"""
    book = _book(
        _entry(uid=1, order=1, keys=("枯井",), content="井底躺着一位旅人。"),
        _entry(uid=2, order=2, keys=("旅人",), content="旅人身边有瓶酒。"),
    )

    assert _uids(_activate(book, ["你走向枯井"])) == [1, 2]


def test_exclude_recursion_blocks_content_from_next_round() -> None:
    """exclude_recursion=True：首轮条目的 content 不进递归上下文 → 相关条目不被触发。"""
    book = _book(
        _entry(
            uid=1, order=1, keys=("枯井",), content="井底躺着一位旅人。", exclude_recursion=True
        ),
        _entry(uid=2, order=2, keys=("旅人",), content="旅人身边有瓶酒。"),
    )

    assert _uids(_activate(book, ["你走向枯井"])) == [1]


def test_prevent_recursion_entry_not_activated_by_recursion() -> None:
    """prevent_recursion=True：只由递归上下文命中的条目不激活。"""
    book = _book(
        _entry(uid=1, order=1, keys=("枯井",), content="井底躺着一位旅人。"),
        _entry(uid=2, order=2, keys=("旅人",), content="旅人身边有瓶酒。", prevent_recursion=True),
    )

    assert _uids(_activate(book, ["你走向枯井"])) == [1]


def test_recursion_depth_is_at_most_two_rounds() -> None:
    """递归最多 2 轮：C 只由 B 的 content 命中、B 由 A 命中 → C 不激活。"""
    book = _book(
        _entry(uid=1, order=1, keys=("枯井",), content="井底有旅人。"),
        _entry(uid=2, order=2, keys=("旅人",), content="旅人留下地图。"),
        _entry(uid=3, order=3, keys=("地图",), content="地图指向河边。"),
    )

    assert _uids(_activate(book, ["你走向枯井"])) == [1, 2]


# ---------------------------------------------------------------------------
# 确定性概率
# ---------------------------------------------------------------------------


def test_probability_zero_never_activates_and_hundred_always() -> None:
    """probability=0 永不激活；probability=100 恒激活（use_probability=True 时）。"""
    zero = _book(_entry(uid=1, keys=("火把",), content="x", use_probability=True, probability=0))
    hundred = _book(
        _entry(uid=2, keys=("火把",), content="y", use_probability=True, probability=100)
    )

    assert _activate(zero, ["火把"]) == []
    assert _uids(_activate(hundred, ["火把"])) == [2]


def test_probability_is_deterministic_for_same_inputs() -> None:
    """同 seed/turn/上下文 → 概率判定逐次一致（可回放）。"""
    book = _book(_entry(uid=7, keys=("火把",), content="x", use_probability=True, probability=50))

    first = _activate(book, ["火把", "入口"], turn_id=9)
    second = _activate(book, ["火把", "入口"], turn_id=9)

    assert _uids(first) == _uids(second)


def test_probability_varies_across_turns_or_contexts() -> None:
    """不同 turn/上下文会派生不同伪随机值（不保证每样本都不同，但集合应出现变化）。"""
    book = _book(_entry(uid=7, keys=("火把",), content="x", use_probability=True, probability=50))
    outcomes = {bool(_activate(book, ["火把"], turn_id=turn_id)) for turn_id in range(2, 40)}

    assert outcomes == {True, False}


# ---------------------------------------------------------------------------
# 空输入 / 上下文构造
# ---------------------------------------------------------------------------


def test_empty_book_returns_empty() -> None:
    """条目为空 → 返回空列表。"""
    assert activate_entries(_book(), context_texts=["火把"], turn_id=2, save_seed=_SAVE_SEED) == []


def test_build_activation_context_includes_room_items_npcs_and_input() -> None:
    """上下文构造：房间名/描述、地面物品、NPC、最近叙事（≤6）、玩家输入，输入在最后。"""
    state = build_initial_state(save_seed=_SAVE_SEED)
    narratives = [f"叙事{index}" for index in range(1, 9)]

    context = build_activation_context(state, narrative_texts=narratives, player_input="拿起火把")

    room = state.rooms[state.characters[PLAYER_ID].location]
    assert context[-3] == room.name  # 房间名紧邻玩家输入，默认窗口必含
    assert "火把" in "、".join(context)
    assert "老哨兵" in "、".join(context)
    assert context[-1] == "拿起火把"
    assert not any(text == "叙事1" for text in context)  # 只保留最近 6 条
    assert any(text == "叙事8" for text in context)


def test_format_lore_entries_prefixes_content() -> None:
    """激活条目 → 叙事注入文本：``【世界设定】内容``，空内容跳过。"""
    entries = [
        _entry(uid=1, content="井底有寒气。"),
        _entry(uid=2, content="   "),
        _entry(uid=3, content="灯已经灭了两夜。"),
    ]

    assert format_lore_entries(entries) == [
        "【世界设定】井底有寒气。",
        "【世界设定】灯已经灭了两夜。",
    ]


def test_activate_returns_stable_entry_objects() -> None:
    """返回的是书内条目对象（uid/content 与输入一致，便于调用方取 uid 做审计）。"""
    entry = _entry(uid=42, keys=("火把",), content="火把很旧。")
    book = _book(entry)

    active = _activate(book, ["火把"])

    assert active == [entry]
    assert active[0].uid == 42


@pytest.mark.parametrize("max_entries,char_budget", [(8, 800), (1, 1), (0, 800), (8, 0)])
def test_budget_parameter_edges(max_entries: int, char_budget: int) -> None:
    """预算/上限边界参数不抛异常（0 侧返回空，正常侧返回条目）。"""
    book = _book(_entry(uid=1, keys=("火把",), content="火把很旧。"))

    active = _activate(book, ["火把"], max_entries=max_entries, char_budget=char_budget)

    assert isinstance(active, list)
