"""单元测试：M3-012 ``ProposalQueue``（契约 ``.work/m3-contract.md`` §3.2、第 11 章 §11.2–§11.4）。

覆盖：``ProposalSource`` 词表、``QueueEntry`` 不可变/``extra=forbid``、``proposal_id`` 派生格式
（``p_{turn_id:06d}_{enqueue_seq:03d}``，第 21 章 §21.2.2）、同 actor + 同 tool + 同 args 的回合内
去重（canonical JSON 键序等价）、不同 actor/参数/工具不去重、``enqueue_seq`` 单调、
``(priority desc, enqueue_seq asc, actor_id asc)`` 排序（第 11 章 §11.3 规则 1）、``drain`` 截断与
``resolved`` 迁移、重复 ``drain`` 不重复返回、``expire_pending`` 只影响 pending、``entries``/
``pending`` 防御性复制、纯内存确定性，以及失败分支（``turn_id`` 非正、``max_items`` 非法、
``drain`` 负数、提案 ``turn_id`` 不一致、非法 ``source``、不可 JSON 序列化参数）。

文档依据：第 11 章 §11.2/§11.3/§11.4；第 21 章 §21.2.2/§21.2.7；第 9 章 §9.2 步骤 9。
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from textgame.domain.models import Proposal
from textgame.domain.scenario import ITEM_POTION, ITEM_SWORD
from textgame.orchestration.proposal_queue import (
    DEFAULT_MAX_ITEMS,
    ProposalQueue,
    ProposalSource,
    QueueEntry,
    _sort_key,
)

_TURN_ID = 2
_ACTOR_A = "char_01J9Z3K4Q7X8Y9Z0ABCDEFGHJA"
_ACTOR_B = "char_01J9Z3K4Q7X8Y9Z0ABCDEFGHJB"
_ACTOR_PLAYER = "char_01J9Z3K4Q7X8Y9Z0ABCDEFGHJN"


# ---------------------------------------------------------------------------
# 构造辅助
# ---------------------------------------------------------------------------
def _proposal(
    *,
    actor_id: str = _ACTOR_A,
    tool: str = "take_item",
    args: dict[str, Any] | None = None,
    priority: int = 50,
    turn_id: int = _TURN_ID,
    reason: str = "plan_step",
) -> Proposal:
    """构造合法 ``Proposal``；默认参数保证与 ``_TURN_ID`` 队列匹配。"""
    return Proposal(
        turn_id=turn_id,
        actor_id=actor_id,
        tool=tool,
        args={"item_id": ITEM_POTION} if args is None else args,
        reason=reason,
        priority=priority,
    )


def _queue(*, turn_id: int = _TURN_ID, max_items: int = DEFAULT_MAX_ITEMS) -> ProposalQueue:
    """构造空队列；``max_items`` 缺省取契约默认 8。"""
    return ProposalQueue(turn_id=turn_id, max_items=max_items)


def _ids(entries: list[QueueEntry]) -> list[str]:
    """取提案 ID 列表：``drain`` 返回的条目状态已迁移为 ``resolved``，比较 ID 更直观。"""
    return [entry.proposal_id for entry in entries]


# ---------------------------------------------------------------------------
# 数据契约：ProposalSource / QueueEntry / proposal_id
# ---------------------------------------------------------------------------
def test_proposal_source_values_are_fixed() -> None:
    """``ProposalSource`` 逐字为 player/npc/rule/system（契约 §3.2，禁止改名）。"""
    assert {source.value for source in ProposalSource} == {"player", "npc", "rule", "system"}
    assert ProposalSource.PLAYER == "player"  # str Enum：可直接与字符串比较
    assert isinstance(ProposalSource.NPC, str)


def test_queue_entry_defaults_to_pending_and_keeps_nested_proposal() -> None:
    """``QueueEntry`` 默认 ``status="pending"``，嵌套保留完整 ``Proposal``。"""
    proposal = _proposal()
    entry = QueueEntry(
        proposal_id="p_000002_001",
        enqueue_seq=1,
        source=ProposalSource.NPC,
        proposal=proposal,
    )
    assert entry.status == "pending"
    assert entry.proposal == proposal
    assert entry.source is ProposalSource.NPC


def test_queue_entry_is_frozen_and_forbids_extra_fields() -> None:
    """``QueueEntry`` frozen + ``extra=forbid``：多余字段与原地改状态都必须失败。"""
    with pytest.raises(ValidationError):
        QueueEntry(
            proposal_id="p_000002_001",
            enqueue_seq=1,
            source=ProposalSource.PLAYER,
            proposal=_proposal(),
            turn_id=_TURN_ID,  # type: ignore[call-arg]  # 契约无该字段，必须被拒绝
        )
    entry = QueueEntry(
        proposal_id="p_000002_001",
        enqueue_seq=1,
        source=ProposalSource.PLAYER,
        proposal=_proposal(),
    )
    with pytest.raises(ValidationError):
        entry.status = "resolved"  # type: ignore[misc]  # frozen 模型禁止赋值
    with pytest.raises(ValidationError):
        entry.enqueue_seq = 2  # type: ignore[misc]


def test_proposal_id_uses_six_digit_turn_and_three_digit_seq() -> None:
    """``proposal_id = p_{turn_id:06d}_{enqueue_seq:03d}``（第 21 章 §21.2.2 口径）。"""
    queue = ProposalQueue(turn_id=42)
    first = queue.enqueue(_proposal(turn_id=42, actor_id=_ACTOR_A), source=ProposalSource.PLAYER)
    second = queue.enqueue(
        _proposal(turn_id=42, actor_id=_ACTOR_B, args={"item_id": ITEM_SWORD}),
        source=ProposalSource.NPC,
    )
    assert first.proposal_id == "p_000042_001"
    assert second.proposal_id == "p_000042_002"
    assert (first.enqueue_seq, second.enqueue_seq) == (1, 2)


def test_enqueue_seq_is_monotonic_and_skips_deduped_proposals() -> None:
    """``enqueue_seq`` 从 1 起逐条 +1；去重命中不消耗新序号（不是 ``Event.seq``）。"""
    queue = _queue()
    first = queue.enqueue(_proposal(actor_id=_ACTOR_A), source=ProposalSource.PLAYER)
    duplicate = queue.enqueue(_proposal(actor_id=_ACTOR_A), source=ProposalSource.PLAYER)
    third = queue.enqueue(
        _proposal(actor_id=_ACTOR_B, args={"item_id": ITEM_SWORD}),
        source=ProposalSource.NPC,
    )
    assert duplicate.proposal_id == first.proposal_id
    assert duplicate.enqueue_seq == 1
    assert third.enqueue_seq == 2
    assert [entry.enqueue_seq for entry in queue.entries] == [1, 2]


def test_proposal_ids_are_unique_and_order_preserving() -> None:
    """不同 actor/参数的条目 ``proposal_id`` 唯一且按入队顺序递增。"""
    queue = _queue()
    ids = [
        queue.enqueue(
            _proposal(actor_id=actor_id, args={"item_id": item_id}),
            source=ProposalSource.NPC,
        ).proposal_id
        for actor_id, item_id in (
            (_ACTOR_A, ITEM_POTION),
            (_ACTOR_B, ITEM_SWORD),
            (_ACTOR_A, ITEM_SWORD),
        )
    ]
    assert ids == ["p_000002_001", "p_000002_002", "p_000002_003"]
    assert len(set(ids)) == len(ids)


def test_two_queues_for_same_turn_are_deterministic() -> None:
    """同一入队序列在两个实例上得到相同 ``proposal_id``/同顺序（可回放，第 11 章 §11.3）。"""
    proposals = [
        (_proposal(actor_id=_ACTOR_A), ProposalSource.PLAYER),
        (_proposal(actor_id=_ACTOR_B, args={"item_id": ITEM_SWORD}), ProposalSource.NPC),
    ]
    queue_a = _queue()
    queue_b = _queue()
    ids_a = [queue_a.enqueue(proposal, source=source).proposal_id for proposal, source in proposals]
    ids_b = [queue_b.enqueue(proposal, source=source).proposal_id for proposal, source in proposals]
    assert ids_a == ids_b
    assert queue_a.entries == queue_b.entries


# ---------------------------------------------------------------------------
# 回合内去重：同 actor + 同 tool + 同 args（canonical JSON）
# ---------------------------------------------------------------------------
def test_duplicate_enqueue_returns_existing_entry_without_growing_queue() -> None:
    """同 actor + 同 tool + 同 args 重复入队 → 返回已存在 entry、条目数不变。"""
    queue = _queue()
    first = queue.enqueue(_proposal(), source=ProposalSource.NPC)
    again = queue.enqueue(_proposal(), source=ProposalSource.NPC)
    assert again is not None
    assert again.proposal_id == first.proposal_id
    assert len(queue.entries) == 1
    assert len(queue.pending) == 1


def test_duplicate_detected_after_queue_has_other_entries() -> None:
    """去重针对整个回合：中间插入其他提案后重复入队仍返回最初 entry。"""
    queue = _queue()
    first = queue.enqueue(_proposal(actor_id=_ACTOR_A), source=ProposalSource.PLAYER)
    queue.enqueue(
        _proposal(actor_id=_ACTOR_B, args={"item_id": ITEM_SWORD}),
        source=ProposalSource.NPC,
    )
    duplicate = queue.enqueue(_proposal(actor_id=_ACTOR_A), source=ProposalSource.PLAYER)
    assert duplicate.proposal_id == first.proposal_id


def test_dedupe_canonicalizes_argument_key_order() -> None:
    """args 键序/嵌套键序不同但 JSON 规范等价 → 视为重复（canonical_json 去重口径）。"""
    queue = _queue()
    first = queue.enqueue(
        _proposal(args={"item_id": ITEM_POTION, "nested": {"b": 2, "a": 1}}),
        source=ProposalSource.NPC,
    )
    duplicate = queue.enqueue(
        _proposal(args={"nested": {"a": 1, "b": 2}, "item_id": ITEM_POTION}),
        source=ProposalSource.NPC,
    )
    assert duplicate.proposal_id == first.proposal_id
    assert len(queue.entries) == 1


def test_dedupe_does_not_merge_different_args() -> None:
    """同 actor + 同 tool 但 args 不同 → 两条独立提案。"""
    queue = _queue()
    queue.enqueue(_proposal(args={"item_id": ITEM_POTION}), source=ProposalSource.NPC)
    queue.enqueue(_proposal(args={"item_id": ITEM_SWORD}), source=ProposalSource.NPC)
    assert len(queue.entries) == 2


def test_dedupe_does_not_merge_different_actors() -> None:
    """不同 actor 即使 tool/args 完全相同也不去重（避免误合并不同角色的意图）。"""
    queue = _queue()
    queue.enqueue(_proposal(actor_id=_ACTOR_A), source=ProposalSource.NPC)
    queue.enqueue(_proposal(actor_id=_ACTOR_B), source=ProposalSource.NPC)
    assert len(queue.entries) == 2
    assert [entry.proposal.actor_id for entry in queue.entries] == [_ACTOR_A, _ACTOR_B]


def test_dedupe_does_not_merge_different_tools() -> None:
    """同 actor + 同 args 但工具不同 → 不去重。"""
    queue = _queue()
    queue.enqueue(_proposal(tool="take_item"), source=ProposalSource.NPC)
    queue.enqueue(_proposal(tool="use_item"), source=ProposalSource.NPC)
    assert len(queue.entries) == 2


def test_dedupe_after_drain_returns_resolved_entry() -> None:
    """drain 后重复入队仍返回同一（已 ``resolved``）entry，不新增条目。"""
    queue = _queue()
    first = queue.enqueue(_proposal(), source=ProposalSource.NPC)
    queue.drain()
    duplicate = queue.enqueue(_proposal(), source=ProposalSource.NPC)
    assert duplicate.proposal_id == first.proposal_id
    assert duplicate.status == "resolved"
    assert len(queue.entries) == 1


def test_dedupe_key_ignores_proposal_reason_and_priority() -> None:
    """去重键只含 actor/tool/args：reason/priority 不同不影响去重（契约 §3.2）。"""
    queue = _queue()
    first = queue.enqueue(_proposal(priority=50, reason="first"), source=ProposalSource.NPC)
    duplicate = queue.enqueue(_proposal(priority=90, reason="second"), source=ProposalSource.NPC)
    assert duplicate.proposal_id == first.proposal_id
    assert duplicate.proposal.priority == 50  # 返回的是既有 entry，不是新提案
    assert len(queue.entries) == 1


# ---------------------------------------------------------------------------
# 排序：(priority desc, enqueue_seq asc, actor_id asc)
# ---------------------------------------------------------------------------
def test_drain_orders_by_priority_desc() -> None:
    """玩家提案（priority=100）先于 NPC 提案（priority=50）先于低优先（priority=10）。"""
    queue = _queue()
    low = queue.enqueue(_proposal(actor_id=_ACTOR_A, priority=10), source=ProposalSource.SYSTEM)
    npc = queue.enqueue(_proposal(actor_id=_ACTOR_B, priority=50), source=ProposalSource.NPC)
    player = queue.enqueue(
        _proposal(actor_id=_ACTOR_PLAYER, priority=100),
        source=ProposalSource.PLAYER,
    )
    assert _ids(queue.drain()) == _ids([player, npc, low])
    assert queue.drain() == []


def test_drain_orders_by_enqueue_seq_within_same_priority() -> None:
    """同优先级按入队序升序（先到先得，第 11 章 §11.3 规则 2）。"""
    queue = _queue()
    first = queue.enqueue(_proposal(actor_id=_ACTOR_A), source=ProposalSource.NPC)
    second = queue.enqueue(
        _proposal(actor_id=_ACTOR_B, args={"item_id": ITEM_SWORD}),
        source=ProposalSource.NPC,
    )
    assert _ids(queue.drain()) == _ids([first, second])


def test_sort_key_tiebreaks_by_actor_id() -> None:
    """排序键第三维为 ``actor_id asc``（契约 §3.2 逐字口径；手工构造同序号条目验证）。"""
    entry_b = QueueEntry(
        proposal_id="p_000002_001",
        enqueue_seq=1,
        source=ProposalSource.NPC,
        proposal=_proposal(actor_id=_ACTOR_B),
    )
    entry_a = QueueEntry(
        proposal_id="p_000002_001",
        enqueue_seq=1,
        source=ProposalSource.NPC,
        proposal=_proposal(actor_id=_ACTOR_A),
    )
    assert sorted([entry_b, entry_a], key=_sort_key) == [entry_a, entry_b]
    assert _sort_key(entry_a) == (-50, 1, _ACTOR_A)
    # priority desc 是主键：低优先级但序号更小的条目排在后面。
    high_priority_late = QueueEntry(
        proposal_id="p_000002_002",
        enqueue_seq=2,
        source=ProposalSource.PLAYER,
        proposal=_proposal(actor_id=_ACTOR_PLAYER, priority=100),
    )
    assert sorted([high_priority_late, entry_a], key=_sort_key) == [high_priority_late, entry_a]


def test_drain_returns_entries_in_sorted_order_not_enqueue_order() -> None:
    """入队顺序与裁决顺序不同：drain 必须返回排序后顺序（第 11 章 §11.3）。"""
    queue = _queue()
    queue.enqueue(_proposal(actor_id=_ACTOR_B, priority=50), source=ProposalSource.NPC)
    queue.enqueue(
        _proposal(actor_id=_ACTOR_PLAYER, priority=100),
        source=ProposalSource.PLAYER,
    )
    drained = queue.drain()
    assert [entry.proposal.actor_id for entry in drained] == [_ACTOR_PLAYER, _ACTOR_B]


# ---------------------------------------------------------------------------
# drain：resolved 迁移、截断、边界与重复调用
# ---------------------------------------------------------------------------
def test_drain_marks_returned_entries_resolved() -> None:
    """drain 返回的条目状态为 ``resolved``，内部 entries/pending 同步更新。"""
    queue = _queue()
    queue.enqueue(_proposal(), source=ProposalSource.NPC)
    drained = queue.drain()
    assert [entry.status for entry in drained] == ["resolved"]
    assert [entry.status for entry in queue.entries] == ["resolved"]
    assert queue.pending == []


def test_drain_truncates_to_max_items_and_keeps_rest_pending() -> None:
    """超出 ``max_items`` 的条目保持 pending（契约 §3.2）。"""
    queue = _queue()
    for index in range(3):
        queue.enqueue(
            _proposal(actor_id=f"{_ACTOR_A}{index}", args={"item_id": ITEM_POTION}),
            source=ProposalSource.NPC,
        )
    drained = queue.drain(max_items=2)
    assert len(drained) == 2
    assert [entry.status for entry in drained] == ["resolved", "resolved"]
    assert [entry.status for entry in queue.pending] == ["pending"]
    assert len(queue.entries) == 3


def test_drain_uses_constructor_max_items_as_default() -> None:
    """``drain()`` 无参时使用构造注入的 ``max_items``。"""
    queue = _queue(max_items=2)
    for index in range(3):
        queue.enqueue(
            _proposal(actor_id=f"{_ACTOR_A}{index}", args={"item_id": ITEM_POTION}),
            source=ProposalSource.NPC,
        )
    assert len(queue.drain()) == 2
    assert len(queue.pending) == 1


def test_drain_zero_returns_empty_and_changes_nothing() -> None:
    """``drain(max_items=0)`` 是合法边界：返回空列表、所有条目保持 pending。"""
    queue = _queue()
    queue.enqueue(_proposal(), source=ProposalSource.NPC)
    assert queue.drain(max_items=0) == []
    assert len(queue.pending) == 1
    assert [entry.status for entry in queue.entries] == ["pending"]


def test_drain_negative_max_items_raises_value_error() -> None:
    """``drain(max_items=-1)`` 非法 → ``ValueError``（且不改队列状态）。"""
    queue = _queue()
    queue.enqueue(_proposal(), source=ProposalSource.NPC)
    with pytest.raises(ValueError, match="non-negative"):
        queue.drain(max_items=-1)
    assert [entry.status for entry in queue.entries] == ["pending"]


def test_second_drain_returns_only_still_pending_entries() -> None:
    """已 resolved 的条目不会重复 drain；后续批次继续按排序键返回。"""
    queue = _queue()
    first = queue.enqueue(_proposal(actor_id=_ACTOR_A, priority=50), source=ProposalSource.NPC)
    second = queue.enqueue(
        _proposal(actor_id=_ACTOR_B, args={"item_id": ITEM_SWORD}, priority=10),
        source=ProposalSource.NPC,
    )
    assert _ids(queue.drain(max_items=1)) == _ids([first])
    assert _ids(queue.drain(max_items=1)) == _ids([second])
    assert queue.drain() == []


def test_drain_on_empty_queue_returns_empty_list() -> None:
    """空队列 drain → ``[]``，不抛异常。"""
    assert _queue().drain() == []


def test_drain_is_deterministic_across_instances() -> None:
    """同一入队序列的两个实例 drain 出相同顺序（第 11 章 §11.3 可回放要求）。"""

    def build() -> ProposalQueue:
        queue = _queue()
        queue.enqueue(
            _proposal(actor_id=_ACTOR_B, args={"item_id": ITEM_SWORD}),
            source=ProposalSource.NPC,
        )
        queue.enqueue(
            _proposal(actor_id=_ACTOR_PLAYER, priority=100),
            source=ProposalSource.PLAYER,
        )
        return queue

    first, second = build(), build()
    assert first.drain() == second.drain()


# ---------------------------------------------------------------------------
# expire_pending：只影响 pending
# ---------------------------------------------------------------------------
def test_expire_pending_marks_only_pending_entries_expired() -> None:
    """``expire_pending`` 只迁移 pending；已 resolved 的条目保持 resolved。"""
    queue = _queue()
    resolved_entry = queue.enqueue(
        _proposal(actor_id=_ACTOR_A, priority=100), source=ProposalSource.PLAYER
    )
    still_pending = queue.enqueue(
        _proposal(actor_id=_ACTOR_B, args={"item_id": ITEM_SWORD}, priority=50),
        source=ProposalSource.NPC,
    )
    queue.drain(max_items=1)
    expired = queue.expire_pending()
    assert _ids(expired) == _ids([still_pending])
    assert [entry.status for entry in expired] == ["expired"]
    assert [entry.status for entry in queue.entries] == ["resolved", "expired"]
    assert queue.entries[0].proposal_id == resolved_entry.proposal_id
    assert queue.pending == []


def test_expire_pending_returns_priority_order() -> None:
    """过期条目的返回顺序与裁决顺序同口径（按排序键），便于审计落库。"""
    queue = _queue()
    queue.enqueue(_proposal(actor_id=_ACTOR_A, priority=10), source=ProposalSource.SYSTEM)
    queue.enqueue(
        _proposal(actor_id=_ACTOR_PLAYER, priority=100),
        source=ProposalSource.PLAYER,
    )
    expired = queue.expire_pending()
    assert [entry.proposal.priority for entry in expired] == [100, 10]


def test_expire_pending_on_empty_queue_returns_empty_list() -> None:
    """空队列 ``expire_pending`` → ``[]``。"""
    assert _queue().expire_pending() == []


def test_expire_pending_twice_returns_empty_second_time() -> None:
    """第二次 ``expire_pending`` 无 pending 可迁移 → ``[]``。"""
    queue = _queue()
    queue.enqueue(_proposal(), source=ProposalSource.NPC)
    assert len(queue.expire_pending()) == 1
    assert queue.expire_pending() == []


def test_drain_after_expire_returns_empty() -> None:
    """过期后的条目不会被 drain 取出（生命周期 pending → expired 终止）。"""
    queue = _queue()
    queue.enqueue(_proposal(), source=ProposalSource.NPC)
    queue.expire_pending()
    assert queue.drain() == []


# ---------------------------------------------------------------------------
# 只读属性：防御性复制
# ---------------------------------------------------------------------------
def test_entries_property_returns_defensive_copy() -> None:
    """修改 ``queue.entries`` 返回的列表不影响队列内部。"""
    queue = _queue()
    queue.enqueue(_proposal(), source=ProposalSource.NPC)
    snapshot = queue.entries
    snapshot.clear()
    assert len(queue.entries) == 1
    snapshot = queue.entries
    snapshot.append(queue.entries[0])  # 列表可再追加，但内部不受影响
    assert len(queue.entries) == 1


def test_pending_property_returns_defensive_copy() -> None:
    """修改 ``queue.pending`` 返回的列表不影响队列内部。"""
    queue = _queue()
    queue.enqueue(_proposal(), source=ProposalSource.NPC)
    pending = queue.pending
    pending.clear()
    assert len(queue.pending) == 1


def test_entries_and_pending_reflect_status_transitions() -> None:
    """属性是状态迁移的只读视图：drain/expire 后立即反映。"""
    queue = _queue()
    queue.enqueue(_proposal(actor_id=_ACTOR_A), source=ProposalSource.PLAYER)
    queue.enqueue(
        _proposal(actor_id=_ACTOR_B, args={"item_id": ITEM_SWORD}),
        source=ProposalSource.NPC,
    )
    queue.drain(max_items=1)
    assert len(queue.entries) == 2
    assert len(queue.pending) == 1
    queue.expire_pending()
    assert queue.pending == []


# ---------------------------------------------------------------------------
# 失败分支：构造参数、提案回合、参数序列化
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("turn_id", [0, -1])
def test_constructor_rejects_non_positive_turn_id(turn_id: int) -> None:
    """``turn_id`` 必须为正整数（``TurnId`` 口径）→ ``ValueError``。"""
    with pytest.raises(ValueError, match="turn_id"):
        ProposalQueue(turn_id=turn_id)


@pytest.mark.parametrize("max_items", [0, -1, -8])
def test_constructor_rejects_invalid_max_items(max_items: int) -> None:
    """构造 ``max_items`` 必须 >= 1（0/负数非法）→ ``ValueError``。"""
    with pytest.raises(ValueError, match="max_items"):
        ProposalQueue(turn_id=_TURN_ID, max_items=max_items)


def test_enqueue_rejects_proposal_from_another_turn() -> None:
    """提案 ``turn_id`` 与队列不一致 → ``ValueError``（``proposal_id`` 绑定队列回合）。"""
    queue = _queue(turn_id=2)
    with pytest.raises(ValueError, match="turn_id"):
        queue.enqueue(_proposal(turn_id=3), source=ProposalSource.PLAYER)
    assert queue.entries == []


def test_enqueue_rejects_unknown_source() -> None:
    """非法 ``source`` → Pydantic ``ValidationError``（来源词表受控，第 11 章 §11.1）。"""
    queue = _queue()
    with pytest.raises(ValidationError):
        queue.enqueue(_proposal(), source="admin")  # type: ignore[arg-type]


def test_enqueue_accepts_string_source_compatible_with_enum_value() -> None:
    """``str`` 来源值可被 Pydantic 归一为枚举（与 JSON 入口的字符串口径兼容）。"""
    queue = _queue()
    entry = queue.enqueue(_proposal(), source="npc")  # type: ignore[arg-type]
    assert entry.source is ProposalSource.NPC


def test_enqueue_rejects_non_json_serializable_args() -> None:
    """args 含不可 JSON 序列化对象 → ``TypeError``（去重键必须确定性，第 14 章 §14.4 口径）。"""

    class _Opaque:
        """故意不可序列化的占位对象。"""

    queue = _queue()
    proposal = _proposal(args={"payload": _Opaque()})
    with pytest.raises(TypeError):
        queue.enqueue(proposal, source=ProposalSource.NPC)
    assert queue.entries == []
