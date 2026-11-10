"""H4 QA：世界书格式解析 + 激活引擎独立黑盒矩阵（不 import algorithm 自己的测试）。\r\n\r\n只用 `H4-CONTRACT.md` §3 冻结语义做断言：keys/secondary/4 种 selectiveLogic/constant/order/\r\nchar 预算/max_entries/scan_depth/递归（exclude+prevent）/概率确定性公式复算/错误格式。\r\n覆盖 R4 世界书「导入即可按设定触发」的算法验收；旧场景/golden 不受本模块影响（纯函数）。\r\n"""

from __future__ import annotations

import hashlib

import pytest

from textgame.domain.world_book import (
    WorldBook,
    WorldBookEntry,
    activate_entries,
    format_lore_entries,
    parse_import_payload,
)


def _entry(uid: int, *, keys=(), secondary=(), content="", **kwargs) -> WorldBookEntry:
    """构造规范化条目（默认值对齐契约）。"""
    base: dict[str, object] = {
        "uid": uid,
        "keys": tuple(keys),
        "secondary_keys": tuple(secondary),
        "content": content,
    }
    base.update(kwargs)
    return WorldBookEntry(**base)  # type: ignore[arg-type]


def _book(*entries: WorldBookEntry, name: str = "测试书") -> WorldBook:
    """构造世界书。"""
    return WorldBook(name=name, entries=tuple(entries), source="manual", raw={})


# ---------------------------------------------------------------------------
# 解析：三种格式 + 规范化
# ---------------------------------------------------------------------------
def test_parse_tavern_entries_map_and_array():
    map_payload = {
        "name": "酒馆书",
        "entries": {
            "3": {"uid": 3, "key": "哨站, 信号灯", "content": "灯的事", "order": 10},
            "1": {"uid": 1, "key": ["老兵", "守夜人"], "content": "老兵的事"},
        },
    }
    book = parse_import_payload(map_payload)
    assert book.source == "tavern" and book.name == "酒馆书"
    assert [e.uid for e in book.entries] == [1, 3]
    assert book.entries[1].keys == ("哨站", "信号灯")

    array_payload = {"entries": [{"uid": 7, "key": "锈剑", "content": "剑"}]}
    book2 = parse_import_payload(array_payload)
    assert book2.source == "tavern" and book2.entries[0].uid == 7

    pure_map = {"5": {"key": "河谷", "content": "河谷很冷"}}
    book3 = parse_import_payload(pure_map)
    assert book3.source == "tavern" and book3.entries[0].uid == 5


def test_parse_character_card_character_book():
    payload = {
        "spec": "chara_card_v2",
        "data": {
            "name": "灰烬旅人",
            "character_book": {
                "name": "旅人手记",
                "entries": [
                    {"id": 1, "keys": ["旧提灯"], "content": "提灯有点旧"},
                    {"id": 2, "keys": ["矿镇"], "content": "矿镇"},
                ],
            },
        },
    }
    book = parse_import_payload(payload)
    assert book.source == "character_card" and book.name == "旅人手记"
    assert [e.uid for e in book.entries] == [1, 2]


def test_parse_simplified_schema_and_tavern_field_aliases():
    payload = {
        "name": "简化书",
        "entries": [{"uid": 1, "keys": ["火把"], "content": "受潮的火把"}],
    }
    book = parse_import_payload(payload)
    assert book.source == "manual"

    tavern_payload = {
        "name": "别名书",
        "entries": [
            {
                "uid": 1,
                "keys": ["火把"],
                "content": "受潮的火把",
                "keysecondary": "老兵, 灯",
                "selectiveLogic": "AND_ALL",
                "insertion_order": 5,
                "disable": True,
                "use_probability": True,
                "probability": 60,
                "scan_depth": 2,
            }
        ],
    }
    book = parse_import_payload(tavern_payload)
    entry = book.entries[0]
    assert book.source == "tavern"  # 含酒馆字段标记 → 归为 tavern 格式
    assert entry.secondary_keys == ("老兵", "灯")
    assert entry.selective_logic == 3
    assert entry.order == 5 and entry.enabled is False
    assert entry.use_probability is True and entry.probability == 60
    assert entry.scan_depth == 2


def test_parse_rejects_invalid_payloads():
    with pytest.raises(ValueError):
        parse_import_payload([1, 2, 3])  # 根不是对象
    with pytest.raises(ValueError):
        parse_import_payload({"entries": 42})  # entries 非法
    with pytest.raises(ValueError):
        parse_import_payload(
            {
                "entries": [
                    {"uid": 1, "keys": ["a"], "content": "x"},
                    {"uid": 1, "keys": ["b"], "content": "y"},
                ]
            }
        )  # uid 重复
    with pytest.raises(ValueError):
        parse_import_payload({"foo": "bar"})  # 无法识别


# ---------------------------------------------------------------------------
# 激活：constant / enabled / keys / selectiveLogic / scan_depth
# ---------------------------------------------------------------------------
def _ctx(*texts: str) -> list[str]:
    return list(texts)


def test_constant_and_disabled_entries():
    book = _book(
        _entry(1, keys=(), content="恒定设定", constant=True),
        _entry(2, keys=(), content="被禁设定", constant=True, enabled=False),
        _entry(3, keys=["哨站"], content="命中设定"),
    )
    active = activate_entries(book, context_texts=_ctx("哨站"), turn_id=1, save_seed="save-42")
    assert [e.uid for e in active] == [1, 3]


def test_primary_keys_case_and_whole_word_and_case_sensitive():
    book = _book(
        _entry(1, keys=["哨站"], content="中文子串"),
        _entry(2, keys=["torch"], content="ASCII 默认子串", order=101),
        _entry(3, keys=["torch"], content="ASCII 整词", order=102, match_whole_words=True),
        _entry(4, keys=["Signal"], content="大小写敏感", order=103, case_sensitive=True),
    )
    active = activate_entries(
        book,
        context_texts=_ctx("哨站 torches signal SIGNAL"),
        turn_id=1,
        save_seed="save-42",
    )
    uids = [e.uid for e in active]
    assert 1 in uids and 2 in uids
    assert 3 not in uids  # torches 不满足整词
    assert 4 not in uids  # 大小写敏感时 signal != Signal


@pytest.mark.parametrize(
    ("logic", "secondary", "context", "expected"),
    [
        (0, ("灯", "井"), "只有灯", True),  # AND_ANY
        (0, ("灯", "井"), "都没有", False),
        (1, ("灯", "井"), "只有灯", True),  # NOT_ALL：并非全部命中
        (1, ("灯", "井"), "灯和井", False),
        (2, ("灯", "井"), "都没有", True),  # NOT_ANY：一个都没命中
        (2, ("灯", "井"), "只有灯", False),
        (3, ("灯", "井"), "灯和井", True),  # AND_ALL
        (3, ("灯", "井"), "只有灯", False),
    ],
)
def test_selective_logic_matrix(logic, secondary, context, expected):
    book = _book(_entry(1, keys=["哨站"], secondary=secondary, content="x", selective_logic=logic))
    active = activate_entries(
        book, context_texts=_ctx(f"哨站 {context}"), turn_id=1, save_seed="save-42"
    )
    assert (len(active) == 1) is expected


def test_selective_false_ignores_secondary():
    book = _book(
        _entry(
            1,
            keys=["哨站"],
            secondary=("永远不出现",),
            content="x",
            selective=False,
            selective_logic=3,
        )
    )
    active = activate_entries(book, context_texts=_ctx("哨站"), turn_id=1, save_seed="save-42")
    assert [e.uid for e in active] == [1]


def test_scan_depth_limits_context_window():
    # key 只在最旧的一条上下文里；scan_depth=1 只看最后一条，不应命中
    book = _book(_entry(1, keys=["退伍"], content="x", scan_depth=1))
    assert (
        activate_entries(
            book, context_texts=_ctx("退伍的老兵", "别的"), turn_id=1, save_seed="save-42"
        )
        == []
    )
    book2 = _book(_entry(1, keys=["退伍"], content="x", scan_depth=2))
    assert [
        e.uid
        for e in activate_entries(
            book2, context_texts=_ctx("退伍的老兵", "别的"), turn_id=1, save_seed="save-42"
        )
    ] == [1]


# ---------------------------------------------------------------------------
# 排序 / 预算 / 条数上限
# ---------------------------------------------------------------------------
def test_order_sort_and_budget_and_max_entries():
    a = _entry(1, keys=["k"], content="A" * 10, order=30)
    b = _entry(2, keys=["k"], content="B" * 10, order=10)
    c = _entry(3, keys=["k"], content="C" * 10, order=20)
    book = _book(a, b, c)
    active = activate_entries(book, context_texts=_ctx("k"), turn_id=1, save_seed="save-42")
    assert [e.uid for e in active] == [2, 3, 1]  # order 升序

    # max_entries=2
    assert [
        e.uid
        for e in activate_entries(
            book, context_texts=_ctx("k"), turn_id=1, save_seed="save-42", max_entries=2
        )
    ] == [2, 3]

    # 预算 = 10 + 1 + 10 = 21 只够前两条（第三条需 +1+10）
    active_budget = activate_entries(
        book, context_texts=_ctx("k"), turn_id=1, save_seed="save-42", char_budget=22
    )
    assert [e.uid for e in active_budget] == [2, 3]


# ---------------------------------------------------------------------------
# 概率：确定性 + 边界
# ---------------------------------------------------------------------------
def test_probability_is_deterministic_and_replayable():
    book = _book(_entry(1, keys=["哨站"], content="x", use_probability=True, probability=50))
    ctx = _ctx("哨站")
    first = activate_entries(book, context_texts=ctx, turn_id=1, save_seed="save-42")
    second = activate_entries(book, context_texts=ctx, turn_id=1, save_seed="save-42")
    assert [e.uid for e in first] == [e.uid for e in second]

    # 期望值按契约公式可复算：sha256("seed:turn:uid:ctx_hash")[:8] % 100 < probability
    joined = "\x1f".join(ctx)
    ctx_hash = hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]
    digest = hashlib.sha256(f"save-42:1:1:{ctx_hash}".encode()).hexdigest()
    expected = int(digest[:8], 16) % 100 < 50
    assert (len(first) == 1) is expected


def test_probability_zero_and_hundred_bounds():
    never = _entry(1, keys=["k"], content="x", use_probability=True, probability=0)
    always = _entry(2, keys=["k"], content="y", use_probability=True, probability=100, order=101)
    active = activate_entries(
        _book(never, always), context_texts=_ctx("k"), turn_id=1, save_seed="save-42"
    )
    assert [e.uid for e in active] == [2]


# ---------------------------------------------------------------------------
# 递归（≤2 轮；exclude/prevent）
# ---------------------------------------------------------------------------
def test_recursion_activates_second_round():
    first = _entry(1, keys=["枯井"], content="井里有火石，火石能点灯", order=10)
    second = _entry(2, keys=["火石"], content="火石设定", order=20)
    active = activate_entries(
        _book(first, second), context_texts=_ctx("枯井"), turn_id=1, save_seed="save-42"
    )
    assert [e.uid for e in active] == [1, 2]


def test_recursion_respects_prevent_and_exclude():
    # prevent_recursion：第二轮不被触发（仅第一轮可命中）
    first = _entry(1, keys=["枯井"], content="井里有火石", order=10)
    second = _entry(2, keys=["火石"], content="火石设定", order=20, prevent_recursion=True)
    active = activate_entries(
        _book(first, second), context_texts=_ctx("枯井"), turn_id=1, save_seed="save-42"
    )
    assert [e.uid for e in active] == [1]

    # exclude_recursion：首轮条目的 content 不进入递归上下文
    first2 = _entry(1, keys=["枯井"], content="井里有火石", order=10, exclude_recursion=True)
    active2 = activate_entries(
        _book(first2, second), context_texts=_ctx("枯井"), turn_id=1, save_seed="save-42"
    )
    assert [e.uid for e in active2] == [1]


# ---------------------------------------------------------------------------
# 上下文构造 / 注入格式
# ---------------------------------------------------------------------------
def test_format_lore_entries_and_empty_content():
    entries = [_entry(1, keys=["k"], content="设定内容"), _entry(2, keys=["k"], content="  ")]
    assert format_lore_entries(entries) == ["【世界设定】设定内容"]
