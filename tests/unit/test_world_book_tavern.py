"""P1 酒馆级世界书单测：定时效果 / 正则键 / 包含组 / position-depth 排序 / 导入告警。

口径见 ``docs/design/world-book-format-compat.md``（§2 字段矩阵、§5 D-5 正则、D-8 定时效果、
D-9 包含组、D-10 排序）与 ``docs/decisions/2026-11-11-h4-worldbook-and-visual.md`` §6。

硬门禁：``history=None`` 时 ``activate_entries`` 与旧实现逐字等价（本文件用同书 timed=0
与 timed>0 两种构造断言结果列表完全一致）。
"""

from __future__ import annotations

from textgame.domain import world_book as world_book_module
from textgame.domain.events import Event
from textgame.domain.world_book import (
    ActivationTurn,
    WorldBook,
    WorldBookEntry,
    activate_entries,
    history_from_events,
    parse_import_payload,
)

_SAVE_SEED = "save-42"


# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------
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
    """构造测试世界书（manual 来源）。"""
    return WorldBook(name="测试世界书", entries=tuple(entries), source="manual", raw={})


def _activate(
    book: WorldBook,
    context: list[str],
    *,
    turn_id: int = 2,
    history: list[ActivationTurn] | tuple[ActivationTurn, ...] | None = None,
) -> list[WorldBookEntry]:
    """按固定 seed 激活（测试默认参数与生产一致）。"""
    return activate_entries(
        book,
        context_texts=context,
        turn_id=turn_id,
        save_seed=_SAVE_SEED,
        history=history,
    )


def _uids(entries: list[WorldBookEntry]) -> list[int]:
    """激活结果 → uid 列表（断言顺序用）。"""
    return [entry.uid for entry in entries]


def _history(*turns: tuple[int, str]) -> list[ActivationTurn]:
    """``(turn_id, narrative)`` 序列 → 历史回合（每回合 1 条叙事上下文）。"""
    return [ActivationTurn(turn_id=turn_id, context_texts=(text,)) for turn_id, text in turns]


def _event(turn_id: int, narrative: object = None) -> Event:
    """构造草稿事件（未盖章；``narrative`` 为 None 时不写 result.narrative）。"""
    result = {} if narrative is None else {"narrative": narrative}
    return Event(turn_id=turn_id, actor_id="player", method="move_to", result=result)


# ---------------------------------------------------------------------------
# 解析：定时效果 / 包含组 / 正则键 / 导入告警
# ---------------------------------------------------------------------------
def test_parse_timed_effects_and_group_fields() -> None:
    """SillyTavern 定时效果与包含组字段被解析（含 0..100 收敛与非整数字符串）。"""
    entry = parse_import_payload(
        {
            "entries": [
                {
                    "uid": 7,
                    "key": "灯",
                    "content": "灯。",
                    "sticky": 150,
                    "cooldown": -3,
                    "delay": "7",
                    "group": "  signal  ",
                    "groupWeight": "2.5",
                    "groupOverride": True,
                    "useGroupScoring": True,
                }
            ]
        }
    ).entries[0]

    assert entry.sticky == 100  # 越界收敛到上限
    assert entry.cooldown == 0  # 负数收敛到下限
    assert entry.delay == 7  # 数字字符串可解析
    assert entry.group == "signal"
    assert entry.group_weight == 2.5
    assert entry.group_override is True
    assert entry.group_scoring is True


def test_parse_regex_keys_kept_whole_not_split_by_commas() -> None:
    """正则键作为整体保留：数组项与「纯正则列表」字符串都不按逗号切开。"""
    book = parse_import_payload(
        {
            "entries": [
                {"uid": 0, "key": ["/龙|dragon/i", "火把"], "content": "a"},
                {"uid": 1, "key": "/a,b/i", "content": "b"},
                {"uid": 2, "key": "/x/i, /y/m", "content": "c"},
                {"uid": 3, "key": "火把,松脂", "content": "d"},
            ]
        }
    )

    assert book.entries[0].keys == ("/龙|dragon/i", "火把")
    assert book.entries[1].keys == ("/a,b/i",)
    assert book.entries[2].keys == ("/x/i", "/y/m")
    assert book.entries[3].keys == ("火把", "松脂")  # 纯文本逗号串仍按旧口径拆分


def test_parse_clean_tavern_entry_has_no_warnings() -> None:
    """普通 SillyTavern 条目（含 UI 辅助字段/关闭的布尔字段）不产生任何告警。"""
    book = parse_import_payload(
        {
            "entries": {
                "0": {
                    "uid": 0,
                    "key": ["灯"],
                    "content": "灯。",
                    "comment": "备注",
                    "sticky": 0,
                    "cooldown": 0,
                    "delay": 0,
                    "vectorized": False,
                    "automationId": "",
                    "role": None,
                    "useGroupScoring": None,
                    "group": "",
                    "extensions": {},
                }
            }
        }
    )

    assert book.warnings == ()


def test_parse_warns_invalid_regex_key_and_keeps_literal() -> None:
    """非法/超长/非法 flags 的正则键 → 逐条 ``REGEX_KEY_INVALID`` 告警，键按字面量保留。"""
    book = parse_import_payload(
        {
            "entries": [
                {"uid": 0, "key": "/[/i", "content": "a"},
                {"uid": 1, "key": "/" + "a" * 201 + "/", "content": "b"},
                {"uid": 2, "key": "/foo/g", "content": "c"},
            ]
        }
    )
    codes = [warning.code for warning in book.warnings]

    assert codes == ["REGEX_KEY_INVALID"] * 3
    assert all("已按字面量关键词匹配" in warning.message for warning in book.warnings)
    assert [entry.keys for entry in book.entries] == [
        ("/[/i",),
        ("/" + "a" * 201 + "/",),
        ("/foo/g",),
    ]


def test_parse_warns_timed_effect_clamped_and_group_weight_invalid() -> None:
    """定时效果越界/非法与 groupWeight 非法 → 对应告警（含条目 uid 与原值）。"""
    book = parse_import_payload(
        {
            "entries": [
                {
                    "uid": 3,
                    "key": "灯",
                    "content": "a",
                    "sticky": 999,
                    "delay": "不在",
                    "groupWeight": "bad",
                }
            ]
        }
    )
    by_code = {warning.code: warning.message for warning in book.warnings}

    assert "TIMED_EFFECT_CLAMPED" in by_code
    assert "条目 3" in by_code["TIMED_EFFECT_CLAMPED"]
    assert "GROUP_WEIGHT_INVALID" in by_code
    assert book.entries[0].sticky == 100
    assert book.entries[0].delay == 0
    assert book.entries[0].group_weight == 1.0


def test_parse_warns_group_scoring_and_unsupported_fields() -> None:
    """useGroupScoring / 未支持语义字段 → 逐条说明；UI 辅助字段不告警。"""
    book = parse_import_payload(
        {
            "entries": [
                {
                    "uid": 1,
                    "key": "灯",
                    "content": "a",
                    "useGroupScoring": True,
                    "vectorized": True,
                    "characterFilter": {"names": ["老兵"]},
                    "triggers": [1],
                    "automationId": "script:run",
                    "delayUntilRecursion": True,
                    "role": "system",
                    "addMemo": True,
                    "displayIndex": 3,
                }
            ]
        }
    )
    codes = [warning.code for warning in book.warnings]

    assert codes[0] == "GROUP_SCORING_UNSUPPORTED"
    assert codes.count("UNSUPPORTED_FIELD") == 6
    messages = " ".join(warning.message for warning in book.warnings)
    assert "条目 1" in messages
    assert "vectorized" in messages and "automationId" in messages


def test_parse_warns_book_global_settings_and_timed_history() -> None:
    """角色卡全局激活设置未参与运行 + 定时效果需要历史 → 书级告警各一次。"""
    book = parse_import_payload(
        {
            "data": {
                "name": "角色",
                "character_book": {
                    "name": "书",
                    "scan_depth": 6,
                    "token_budget": 500,
                    "recursive_scanning": True,
                    "entries": [{"uid": 0, "key": "灯", "content": "a", "sticky": 2}],
                },
            }
        }
    )
    codes = [warning.code for warning in book.warnings]

    assert "BOOK_GLOBAL_SETTINGS_IGNORED" in codes
    assert codes.count("TIMED_EFFECTS_HISTORY_ONLY") == 1
    global_message = next(
        warning.message
        for warning in book.warnings
        if warning.code == "BOOK_GLOBAL_SETTINGS_IGNORED"
    )
    assert "全局扫描深度" in global_message
    assert "token 预算" in global_message


# ---------------------------------------------------------------------------
# 正则键激活（Python re；与 JS 差异见兼容文档）
# ---------------------------------------------------------------------------
def test_regex_key_matches_with_alternation_and_default_case_insensitive() -> None:
    """正则键支持分支与默认大小写不敏感；普通关键词行为不变。"""
    book = _book(
        _entry(uid=1, keys=("/枯井|井底/",), content="寒气。"),
        _entry(uid=2, keys=("/signal/i",), content="信号。"),
        _entry(uid=3, keys=("火把",), content="火把。"),
    )

    assert _uids(_activate(book, ["你走向枯井"])) == [1]
    assert _uids(_activate(book, ["THE SIGNAL TOWER"])) == [2]
    assert _uids(_activate(book, ["拿起火把"])) == [3]


def test_regex_key_flags_and_case_sensitive_entry() -> None:
    """``/pattern/flags`` 的 m/s flags 生效；条目 case_sensitive=True 时不自动加 IGNORECASE。"""
    multiline = _book(_entry(uid=1, keys=("/^灯$/m",), content="a"))
    dotall = _book(_entry(uid=2, keys=("/灯.*火/s",), content="b"))
    sensitive = _book(_entry(uid=3, keys=("/signal/",), case_sensitive=True, content="c"))

    assert _uids(_activate(multiline, ["无\n灯\n无"])) == [1]
    assert _uids(_activate(dotall, ["灯\n火"])) == [2]
    assert _activate(sensitive, ["Signal"]) == []
    assert _uids(_activate(sensitive, ["signal"])) == [3]


def test_invalid_regex_key_falls_back_to_literal_matching() -> None:
    """非法正则键按字面量匹配（含斜杠与 flags），不静默丢弃。"""
    book = _book(_entry(uid=1, keys=("/[/i",), content="怪。"))

    assert _uids(_activate(book, ["他念出 /[/i 这串符号"])) == [1]
    assert _activate(book, ["枯井"]) == []


def test_regex_match_text_is_truncated_to_2000_chars() -> None:
    """正则只匹配文本前 2000 字符（防灾难性回溯；普通关键词不受影响）。"""
    book = _book(
        _entry(uid=1, keys=("/尾/",), content="尾。"),
        _entry(uid=2, keys=("尾",), content="尾。"),
    )

    # 2001 字上下文：正则只看前 2000 字（不命中），普通关键词仍扫描全文（uid2 命中）。
    assert _uids(_activate(book, ["a" * 2000 + "尾"])) == [2]
    assert _uids(_activate(book, ["a" * 1999 + "尾"])) == [1, 2]


# ---------------------------------------------------------------------------
# 定时效果（history 逐回合回放）
# ---------------------------------------------------------------------------
def test_history_none_is_identical_to_untimed_book() -> None:
    """``history=None`` 时常量/关键词/递归行为与旧实现逐字一致（定时字段被忽略）。"""
    timed = _book(
        _entry(uid=1, keys=("灯",), content="灯。", sticky=3, cooldown=2, delay=1),
        _entry(uid=2, constant=True, content="常亮。"),
        _entry(uid=3, keys=("灯",), content="递归。", delay=5),
    )
    plain = _book(
        _entry(uid=1, keys=("灯",), content="灯。"),
        _entry(uid=2, constant=True, content="常亮。"),
        _entry(uid=3, keys=("灯",), content="递归。"),
    )

    for context in (["灯"], ["无"], ["无", "灯"]):
        assert _uids(_activate(timed, context)) == _uids(_activate(plain, context))


def test_history_empty_tuple_starts_delay_counting() -> None:
    """``history=()`` 表示「启用历史模拟但还没有过去回合」：delay 首次命中当回合不激活。"""
    book = _book(_entry(uid=1, keys=("灯",), content="灯。", delay=1))

    assert _activate(book, ["灯"], turn_id=2, history=[]) == []
    assert _uids(_activate(book, ["灯"], turn_id=3, history=_history((2, "灯")))) == [1]


def test_sticky_keeps_entry_active_without_keyword_hits() -> None:
    """sticky=2：触发回合（2）之后 2 回合内（3、4）即使不命中关键词仍持续激活，第 5 回合消失。"""
    book = _book(_entry(uid=1, keys=("灯",), content="灯。", sticky=2, scan_depth=1))
    history = _history((2, "灯"))

    assert _uids(_activate(book, ["无关"], turn_id=3, history=history)) == [1]
    assert _uids(_activate(book, ["无关"], turn_id=4, history=history)) == [1]
    assert _activate(book, ["无关"], turn_id=5, history=history) == []


def test_sticky_does_not_leak_into_untimed_behavior_without_history() -> None:
    """无 history 时 sticky 不生效（旧测试零改动的硬门禁）。"""
    book = _book(_entry(uid=1, keys=("灯",), content="灯。", sticky=2, scan_depth=1))

    assert _activate(book, ["无关"], turn_id=3, history=None) == []


def test_cooldown_blocks_retrigger_for_n_turns() -> None:
    """cooldown=2：触发后 2 回合内（3、4）关键词命中也不激活，第 5 回合恢复。"""
    book = _book(_entry(uid=1, keys=("灯",), content="灯。", cooldown=2))
    history = _history((2, "灯"))

    assert _activate(book, ["灯"], turn_id=3, history=history) == []
    assert _activate(book, ["灯"], turn_id=4, history=history) == []
    assert _uids(_activate(book, ["灯"], turn_id=5, history=history)) == [1]


def test_delay_waits_two_turns_after_first_match() -> None:
    """delay=2：首次命中（2）后延迟 2 回合，第 4 回合起命中才激活。"""
    book = _book(_entry(uid=1, keys=("灯",), content="灯。", delay=2))

    assert _activate(book, ["灯"], turn_id=2, history=[]) == []
    assert _activate(book, ["灯"], turn_id=3, history=_history((2, "灯"))) == []
    assert _uids(_activate(book, ["灯"], turn_id=4, history=_history((2, "灯"), (3, "灯")))) == [1]


def test_constant_entry_active_under_timed_history() -> None:
    """constant 条目在历史模拟下仍恒激活（仍受预算/条数裁剪）。"""
    book = _book(_entry(uid=1, constant=True, content="常亮。", sticky=1, cooldown=1))

    assert _uids(_activate(book, [], turn_id=2, history=[])) == [1]
    assert _uids(_activate(book, [], turn_id=5, history=_history((2, "无"), (3, "无")))) == [1]


def test_timed_gating_only_applies_to_first_recursion_round() -> None:
    """定时效果只作用于首轮关键词命中；递归轮（内容触发）不受 delay 约束。"""
    book = _book(
        _entry(uid=1, keys=("井",), content="灯亮着"),
        _entry(uid=2, keys=("灯",), content="灯。", delay=5),
    )

    assert _uids(_activate(book, ["井"], turn_id=2, history=[])) == [1, 2]


def test_timed_history_is_deterministic() -> None:
    """同 history/同上下文重复激活结果逐字一致（可回放）。"""
    book = _book(_entry(uid=1, keys=("灯",), content="灯。", sticky=2, cooldown=1))
    history = _history((2, "灯"), (3, "无"))

    first = _activate(book, ["灯"], turn_id=4, history=history)
    second = _activate(book, ["灯"], turn_id=4, history=history)

    assert first == second == [book.entries[0]]


def test_history_replay_is_capped_at_last_100_turns() -> None:
    """内部只回放最近 100 个有叙事的历史回合：更早的命中不影响 sticky 窗口。"""
    book = _book(_entry(uid=1, keys=("灯",), content="灯。", sticky=100, scan_depth=1))
    # 第 51 回合命中后连续 150 回合无关键词；当前第 151 回合时最近 100 回合是 51..150。
    turns = [_history((turn_id, "灯" if turn_id == 51 else "无"))[0] for turn_id in range(51, 151)]

    assert _uids(_activate(book, ["无"], turn_id=151, history=turns)) == [1]

    stale = [_history((turn_id, "灯" if turn_id == 50 else "无"))[0] for turn_id in range(50, 150)]
    assert _activate(book, ["无"], turn_id=151, history=stale) == []


def test_history_from_events_groups_turns_and_dedupes_narratives() -> None:
    """``history_from_events``：按 turn_id 升序、每回合叙事去空去重、无叙事回合跳过。"""
    events = [
        _event(1),  # game_created / 无叙事 → 跳过
        _event(3, "第三回合"),
        _event(2, "第二回合"),
        _event(2, "第二回合"),  # 同回合重复叙事 → 去重
        _event(4, "   "),  # 空白叙事 → 跳过
        _event(5, ""),
    ]

    history = history_from_events(events)

    assert history == (
        ActivationTurn(turn_id=2, context_texts=("第二回合",)),
        ActivationTurn(turn_id=3, context_texts=("第三回合",)),
    )


def test_history_from_events_limit_and_internal_cap() -> None:
    """``limit`` 只保留最近 N 回合；默认仍受内部 100 回合上限约束。"""
    events = [_event(turn_id, f"第{turn_id}回合") for turn_id in range(1, 151)]

    limited = history_from_events(events, limit=2)
    capped = history_from_events(events)

    assert [turn.turn_id for turn in limited] == [149, 150]
    assert len(capped) == world_book_module._MAX_HISTORY_TURNS
    assert capped[-1].turn_id == 150


# ---------------------------------------------------------------------------
# 包含组（同组每回合最多 1 条）
# ---------------------------------------------------------------------------
def test_group_keeps_only_highest_weight_entry() -> None:
    """同 group 两条都命中 → 只激活 group_weight 更大的那条。"""
    book = _book(
        _entry(uid=1, keys=("灯",), content="A", group="signal", group_weight=1.0),
        _entry(uid=2, keys=("灯",), content="B", group="signal", group_weight=2.0),
    )

    assert _uids(_activate(book, ["灯"])) == [2]


def test_group_override_beats_weight() -> None:
    """group_override=true 优先于普通条目（与 group_weight 无关）。"""
    book = _book(
        _entry(uid=1, keys=("灯",), content="A", group="signal", group_override=True),
        _entry(uid=2, keys=("灯",), content="B", group="signal", group_weight=100.0),
    )

    assert _uids(_activate(book, ["灯"])) == [1]


def test_group_tie_breaks_by_order_desc_then_uid_asc() -> None:
    """同 weight 时按 order 降序，再按 uid 升序（组内选择方向与预算排序不同）。"""
    by_order = _book(
        _entry(uid=1, keys=("灯",), content="A", group="signal", order=10),
        _entry(uid=2, keys=("灯",), content="B", group="signal", order=20),
    )
    by_uid = _book(
        _entry(uid=1, keys=("灯",), content="A", group="signal", order=10),
        _entry(uid=2, keys=("灯",), content="B", group="signal", order=10),
    )

    assert _uids(_activate(by_order, ["灯"])) == [2]
    assert _uids(_activate(by_uid, ["灯"])) == [1]


def test_groups_are_independent_and_ungrouped_entries_unaffected() -> None:
    """不同 group 各出 1 条；无 group 的命中条目全部保留（旧行为不变）。"""
    book = _book(
        _entry(uid=1, keys=("灯",), content="A", group="a"),
        _entry(uid=2, keys=("灯",), content="B", group="b"),
        _entry(uid=3, keys=("灯",), content="C"),
        _entry(uid=4, keys=("灯",), content="D"),
    )

    assert _uids(_activate(book, ["灯"])) == [1, 2, 3, 4]


def test_group_dedupes_across_recursion_rounds() -> None:
    """跨递归轮同样去重：第 1 轮已激活组内条目，第 2 轮同组候选不再激活。"""
    book = _book(
        _entry(uid=1, keys=("井",), content="灯亮着", group="g"),
        _entry(uid=2, keys=("灯",), content="同组第二条", group="g", group_weight=9.0),
    )

    assert _uids(_activate(book, ["井"])) == [1]


def test_group_override_replaces_entry_from_earlier_recursion_round() -> None:
    """group_override 条目在后续轮命中 → 替换组内已激活条目（被替换者从结果移除）。"""
    book = _book(
        _entry(uid=1, keys=("井",), content="灯亮着", group="g"),
        _entry(uid=2, keys=("灯",), content="同组 override", group="g", group_override=True),
    )

    assert _uids(_activate(book, ["井"])) == [2]


# ---------------------------------------------------------------------------
# position / depth 排序（lore 块拼接顺序；不改变 prompt 消息结构）
# ---------------------------------------------------------------------------
def test_lore_order_uses_position_then_depth_then_order_then_uid() -> None:
    """激活结果按 position 升序、depth 降序、order 升序、uid 升序返回。"""
    book = _book(
        _entry(uid=1, constant=True, content="A", position=0, depth=4, order=100),
        _entry(uid=2, constant=True, content="B", position=1, depth=4, order=100),
        _entry(uid=3, constant=True, content="C", position=0, depth=8, order=100),
        _entry(uid=4, constant=True, content="D", position=0, depth=8, order=50),
    )

    assert _uids(_activate(book, [])) == [4, 3, 1, 2]


def test_budget_truncation_still_uses_order_before_lore_ordering() -> None:
    """预算截断仍按 (order, uid) 选条，再按 position/depth/order/uid 重排返回。"""
    book = _book(
        _entry(uid=1, constant=True, content="aa", order=1, position=5, depth=4),
        _entry(uid=2, constant=True, content="bb", order=2, position=0, depth=4),
    )

    active = activate_entries(
        book, context_texts=[], turn_id=2, save_seed=_SAVE_SEED, char_budget=2
    )

    # 预算只够第 1 条（order=1, uid=1）；position 排序只作用于被选中的集合。
    assert _uids(active) == [1]


def test_default_position_depth_preserve_old_order_sorting() -> None:
    """缺省 position=0/depth=4 时等价旧 ``(order, uid)`` 排序（旧测试零改动保证）。"""
    book = _book(
        _entry(uid=5, keys=("火把",), order=200, content="E"),
        _entry(uid=2, keys=("火把",), order=10, content="B"),
        _entry(uid=9, keys=("火把",), order=10, content="C"),
        _entry(uid=1, keys=("火把",), order=20, content="D"),
    )

    assert _uids(_activate(book, ["火把"])) == [2, 9, 1, 5]
