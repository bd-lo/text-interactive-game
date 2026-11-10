"""单测：``domain/world_book.py`` 导入格式解析（SillyTavern / Character Card V2 / 简化 schema）。

覆盖：三种格式自动检测与书名兜底、key/keysecondary 逗号串规范化、selectiveLogic 数字/枚举、
enabled/disable、insertion_order→order、position 字符串、probability 收敛、uid 生成与重复检测、
非法结构 ``ValueError``、普通 schema 缺省值。
"""

from __future__ import annotations

import pytest

from textgame.domain.world_book import WorldBook, parse_import_payload


def _tavern_book_payload() -> dict[str, object]:
    """SillyTavern ``world_info``：entries 为 uid map。"""
    return {
        "name": "雪夜哨站",
        "entries": {
            "0": {
                "uid": 0,
                "key": "火把, 松脂",
                "content": "火把用松脂浸过，点起来不容易被风吹灭。",
                "order": 10,
            },
            "1": {
                "uid": 1,
                "key": ["哨站", "老兵"],
                "keysecondary": "雪,夜",
                "content": "哨站夜里很冷。",
                "order": 20,
                "selectiveLogic": "AND_ANY",
            },
        },
    }


def test_detect_tavern_world_info_entries_map() -> None:
    """SillyTavern entries map → source="tavern"，keys 逗号串规范化、条目按 uid 升序。"""
    book = parse_import_payload(_tavern_book_payload())

    assert isinstance(book, WorldBook)
    assert book.source == "tavern"
    assert book.name == "雪夜哨站"
    assert [entry.uid for entry in book.entries] == [0, 1]
    assert book.entries[0].keys == ("火把", "松脂")
    assert book.entries[1].keys == ("哨站", "老兵")
    assert book.entries[1].secondary_keys == ("雪", "夜")
    assert book.entries[1].selective_logic == 0


def test_detect_tavern_entries_list_with_legacy_fields() -> None:
    """SillyTavern entries 数组：insertion_order→order、disable 取反、缺 uid 用下标生成。"""
    payload = {
        "entries": [
            {"key": "钟", "content": "钟声很闷。", "insertion_order": 7},
            {"key": "门", "content": "门轴冻住了。", "disable": True},
        ]
    }

    book = parse_import_payload(payload)

    assert book.source == "tavern"
    assert [entry.uid for entry in book.entries] == [0, 1]
    assert book.entries[0].order == 7
    assert book.entries[1].enabled is False


def test_detect_pure_uid_map_payload() -> None:
    """纯 uid → entry 映射（无 entries 包裹）也识别为 tavern。"""
    book = parse_import_payload(
        {
            "5": {"key": "铁匠铺", "content": "铁匠铺有冷灰。"},
            "6": {"key": "炉", "content": "炉膛塌了。"},
        }
    )

    assert book.source == "tavern"
    assert [entry.uid for entry in book.entries] == [5, 6]


def test_detect_character_card_v2_name_chain() -> None:
    """Character Card V2：data.character_book 检测 + 书名优先 character_book.name。"""
    payload = {
        "data": {
            "name": "旅人卡",
            "character_book": {
                "name": "老兵的回忆",
                "entries": [{"keys": ["枯井"], "content": "井底有寒气。", "id": 9}],
            },
        }
    }

    book = parse_import_payload(payload)

    assert book.source == "character_card"
    assert book.name == "老兵的回忆"
    assert book.entries[0].uid == 9
    assert book.entries[0].keys == ("枯井",)


def test_detect_character_card_top_level_book() -> None:
    """顶层 ``character_book``（无 data 包裹）同样识别；书名回退卡名。"""
    payload = {
        "name": "无卡名",
        "character_book": {"entries": [{"keys": "月", "content": "月亮被云遮住。"}]},
    }

    book = parse_import_payload(payload)

    assert book.source == "character_card"
    assert book.name == "无卡名"
    assert book.entries[0].keys == ("月",)


def test_detect_simplified_schema_as_manual() -> None:
    """项目简化 schema（entries 数组、无酒馆字段）→ source="manual"。"""
    payload = {
        "name": "简化书",
        "entries": [{"keys": "地窖,地牢", "content": "地窖潮湿。", "order": 3}],
    }

    book = parse_import_payload(payload)

    assert book.source == "manual"
    assert book.name == "简化书"
    assert book.entries[0].keys == ("地窖", "地牢")
    assert book.entries[0].order == 3


def test_simplified_name_falls_back_to_default() -> None:
    """简化 schema 缺 name → 缺省书名「未命名世界书」。"""
    book = parse_import_payload({"entries": [{"keys": "风", "content": "风很大。"}]})

    assert book.name == "未命名世界书"


def test_key_string_array_and_chinese_separators_normalized() -> None:
    """key 支持数组/逗号串混写与中文标点分隔，去空白、去空、去重保序。"""
    payload = {
        "entries": [
            {
                "key": ["火把,松脂", " 火把 "],
                "keysecondary": "雪，夜；风、霜",
                "content": "测试。",
            }
        ]
    }

    entry = parse_import_payload(payload).entries[0]

    assert entry.keys == ("火把", "松脂")
    assert entry.secondary_keys == ("雪", "夜", "风", "霜")


def test_selective_logic_numeric_and_enum() -> None:
    """selectiveLogic 兼容数字（含数字串）、枚举名（大小写不敏感）与非法值（回退 0）。"""
    payload = {
        "entries": [
            {"key": "a", "content": "1", "selectiveLogic": 3},
            {"key": "b", "content": "2", "selectiveLogic": "NOT_ANY"},
            {"key": "c", "content": "3", "selectiveLogic": "and_all"},
            {"key": "d", "content": "4", "selectiveLogic": "2"},
            {"key": "e", "content": "5", "selectiveLogic": "unknown"},
        ]
    }

    logic = [entry.selective_logic for entry in parse_import_payload(payload).entries]

    assert logic == [3, 2, 3, 2, 0]


def test_enabled_and_disable_combination() -> None:
    """enabled 缺省 True；``disable=True`` 覆盖 enabled=True 为禁用。"""
    payload = {
        "entries": [
            {"key": "a", "content": "1"},
            {"key": "b", "content": "2", "enabled": True, "disable": True},
            {"key": "c", "content": "3", "enabled": False},
        ]
    }

    enabled = [entry.enabled for entry in parse_import_payload(payload).entries]

    assert enabled == [True, False, False]


def test_position_string_mapping_and_probability_clamp() -> None:
    """position 字符串（before_char/after_char）→ 0/1；probability 收敛到 0..100。"""
    payload = {
        "entries": [
            {"key": "a", "content": "1", "position": "after_char", "probability": 150},
            {"key": "b", "content": "2", "position": "before_char", "probability": -5},
        ]
    }

    entries = parse_import_payload(payload).entries

    assert entries[0].position == 1
    assert entries[0].probability == 100
    assert entries[1].position == 0
    assert entries[1].probability == 0


def test_optional_flags_are_mapped_from_camel_case() -> None:
    """scanDepth/caseSensitive/matchWholeWords/excludeRecursion/preventRecursion 映射。"""
    payload = {
        "entries": [
            {
                "key": "CAT",
                "content": "猫。",
                "scanDepth": 2,
                "caseSensitive": True,
                "matchWholeWords": True,
                "excludeRecursion": True,
                "preventRecursion": True,
                "useProbability": True,
            }
        ]
    }

    entry = parse_import_payload(payload).entries[0]

    assert entry.scan_depth == 2
    assert entry.case_sensitive is True
    assert entry.match_whole_words is True
    assert entry.exclude_recursion is True
    assert entry.prevent_recursion is True
    assert entry.use_probability is True


def test_duplicate_uid_raises_value_error() -> None:
    """条目 uid 重复 → ``ValueError``（接入层映射 422）。"""
    payload = {
        "entries": [
            {"uid": 3, "key": "a", "content": "1"},
            {"uid": 3, "key": "b", "content": "2"},
        ]
    }

    with pytest.raises(ValueError, match="uid"):
        parse_import_payload(payload)


@pytest.mark.parametrize("payload", [None, [], "text", {"foo": 1}, {"entries": "bad"}])
def test_invalid_payload_raises_value_error(payload: object) -> None:
    """非对象 / 无任何合法格式 / entries 类型非法 → ``ValueError``。"""
    with pytest.raises(ValueError):
        parse_import_payload(payload)


def test_empty_entries_list_is_allowed() -> None:
    """显式空 entries 合法（entry_count=0 由接入层决定是否接受）。"""
    book = parse_import_payload({"name": "空书", "entries": []})

    assert book.entries == ()
    assert book.source == "manual"


def test_raw_payload_is_preserved() -> None:
    """``raw`` 保留原始导入 payload（审计/回放口径），不拷贝成规范化结构。"""
    payload = _tavern_book_payload()

    book = parse_import_payload(payload)

    assert book.raw is payload
