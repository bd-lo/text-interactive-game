"""单测：确定性解析器的「可观察状态」入口（背包可达）与 M2 golden 7 输入零回归。

背景（M6 玩家安全子集）：生产解析器此前没有任何「背包/行囊」关键词，``get_inventory``
在生产 HTTP 链路不可达；且「查看背包」会被 ``_LOOK_WORDS`` 的「查看」抢占为房间查看。
本文件固定两件事：① 新关键词优先于 ``_LOOK_WORDS``；② M2 golden 7 个输入的解析结果
逐字不变（tool/args/confidence/source 精确断言），防止本次改动造成解析回归。
"""

from __future__ import annotations

import pytest

from textgame.agents.dto import ParseOutcome
from textgame.domain.models import EntityRef, PerceptionSlice
from textgame.domain.scenario import (
    ITEM_POTION,
    ITEM_POTION_NAME,
    ITEM_SWORD,
    ITEM_SWORD_NAME,
    ITEM_TORCH,
    ITEM_TORCH_NAME,
    NPC_SENTINEL,
    NPC_SENTINEL_NAME,
    PLAYER_ID,
    ROOM_ENTRY,
    ROOM_ENTRY_NAME,
)
from textgame.orchestration.intent import DeterministicIntentParser

_PARSER = DeterministicIntentParser()


def _perception() -> PerceptionSlice:
    """构造覆盖 golden 实体（入口房间/火把/药水/锈剑/老哨兵）的玩家感知切片。"""
    return PerceptionSlice(
        actor_id=PLAYER_ID,
        visible_entities=[
            EntityRef(kind="room", id=ROOM_ENTRY, name=ROOM_ENTRY_NAME),
            EntityRef(kind="item", id=ITEM_TORCH, name=ITEM_TORCH_NAME),
            EntityRef(kind="item", id=ITEM_POTION, name=ITEM_POTION_NAME),
            EntityRef(kind="item", id=ITEM_SWORD, name=ITEM_SWORD_NAME),
            EntityRef(kind="npc", id=NPC_SENTINEL, name=NPC_SENTINEL_NAME),
        ],
    )


def _parse(text: str) -> ParseOutcome:
    """用固定感知切片解析一句玩家输入（确定性、零 IO）。"""
    return _PARSER.parse(text, perception=_perception())


@pytest.mark.parametrize(
    "text",
    ["背包", "查看背包", "行囊里有什么", "我身上带了什么", "打开物品栏", "我的装备"],
)
def test_inventory_phrases_map_to_get_inventory(text: str) -> None:
    """「背包/行囊/物品栏/身上/带了/装备」→ ``get_inventory``（修生产链路不可达）。"""
    outcome = _parse(text)

    assert outcome.tool == "get_inventory"
    assert outcome.args == {}
    assert outcome.confidence == 1.0
    assert outcome.source == "keyword"


def test_inventory_check_precedes_look_words() -> None:
    """「查看背包」含 look 词「查看」，必须被 inventory 分支抢先命中。"""
    assert _parse("查看背包").tool == "get_inventory"
    # 纯房间查看仍保持原语义（不得被背包分支误伤）
    assert _parse("环顾四周").tool == "get_room_view"
    assert _parse("查看四周").tool == "get_room_view"


@pytest.mark.parametrize(
    ("text", "expected_tool", "expected_args"),
    [
        ("环顾四周", "get_room_view", {}),
        ("对老哨兵问好", "talk_to", {"npc_id": NPC_SENTINEL, "topic": "对老哨兵问好"}),
        ("拿起火把", "take_item", {"item_id": ITEM_TORCH}),
        ("拿起治疗药水", "take_item", {"item_id": ITEM_POTION}),
        ("使用治疗药水", "use_item", {"item_id": ITEM_POTION}),
        ("向北走进地窖", "move_to", {"direction": "north"}),
        ("拿起锈剑", "take_item", {"item_id": ITEM_SWORD}),
    ],
)
def test_golden_seven_inputs_parse_unchanged(
    text: str,
    expected_tool: str,
    expected_args: dict[str, str],
) -> None:
    """M2 golden 7 输入解析逐字不变（tool/args/confidence/source 精确断言）。"""
    outcome = _parse(text)

    assert outcome.tool == expected_tool
    assert outcome.args == expected_args
    assert outcome.confidence == 1.0
    assert outcome.source == "keyword"


def test_unmapped_input_still_returns_no_tool() -> None:
    """无关键词的输入仍返回 ``tool=None``（既有兜底路径不变）。"""
    outcome = _parse("跳舞")

    assert outcome.tool is None
    assert outcome.confidence == 0.0
