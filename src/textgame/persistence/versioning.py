"""快照/事件重建提交版本号（M3 契约 D-M3-6、§3.8；第 14 章 14.4）。

背景：M2 每次提交恰好产生 1 条事件，因此历史上 ``version == last_seq``；M3 多角色
同回合多事件后二者不再相等，``game_state_current`` 行丢失时必须从「快照 version +
快照后事件里 distinct ``turn_id`` 数」重建，才能与下一次 ``GameEngine.submit`` 的 CAS
expected_version 对齐。

兜底规则（旧数据/未显式写 version 的快照）：``SnapshotRecord.version`` 为 0 时按 M2
不变量取 ``snapshot.last_seq`` 作为 base（M2 快照一事件一版本），再加快照后 distinct
``turn_id`` 数；无快照时直接以事件流 distinct ``turn_id`` 数作版本（``game_created``
占 turn=1，之后每个成功提交恰好推进一个回合）。以上规则对 M2 单事件数据与 M3 多事件
数据给出同一结果，既有 M2 恢复测试断言（version=last_seq）不回归。
"""

from __future__ import annotations

from collections.abc import Sequence

from textgame.domain.events import Event
from textgame.services.ports import SnapshotRecord


def rebuild_version(events: Sequence[Event], *, snapshot: SnapshotRecord | None = None) -> int:
    """按 D-M3-6 规则重建提交版本号。

    ``events`` 为快照之后的事件（``snapshot`` 非空时）或空档重建的完整事件流
    （``snapshot`` 为 ``None`` 时）；``snapshot`` 非空时仍会过滤 ``seq <= last_seq``
    的事件，允许调用方传全量事件流。
    """
    if snapshot is None:
        return len({event.turn_id for event in events})
    later = [event for event in events if (event.seq or 0) > snapshot.last_seq]
    # M2 兜底：旧快照没有 version 列，按「一事件一版本」取 last_seq；M3 快照写真实版本号。
    base = snapshot.version if snapshot.version > 0 else snapshot.last_seq
    return base + len({event.turn_id for event in later})


__all__ = ["rebuild_version"]
