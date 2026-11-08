"""第 8 章 §8.2 私有感知切片的领域纯函数：``local_events`` 的可见性裁剪。

结构性防泄露（第 8 章 §8.2「私有感知切片与私有记忆：结构性防泄露」、第 15 章 §15.4）：
感知切片的 ``local_events`` 只能包含该 actor 自己的事件，禁止把全局事件流水或他人事件
交给 Agent。本模块只做纯数据裁剪：不读仓储、不读时钟、不碰 IO，也不依赖任何框架
（import-linter「domain 不得依赖框架与 IO」契约，见 ``pyproject.toml``）。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from textgame.domain.events import Event

#: ``local_events`` 的 M3 保守可见性上限（切片 010 任务要求 4；第 8 章 §8.2 规则 2）。
LOCAL_EVENTS_LIMIT: Final[int] = 10


def _event_seq(event: Event) -> int:
    """取盖章事件的 ``seq``；未盖章事件无法按序号排序 → ``ValueError``。

    第 5 章 §5.3 / 第 14 章 §14.2：``seq`` 是存档内全局单调递增的事件序号（从 1 起），
    也是 ``local_events`` 判定“最近”的唯一依据；草稿事件没有 ``seq``（``None``），
    既排不了序，也不代表已提交事实，故直接拒绝而不是静默丢弃。
    """
    seq = event.seq
    if seq is None:
        raise ValueError(
            "local_events requires stamped events (seq is None): "
            f"method={event.method} actor_id={event.actor_id}"
        )
    return seq


def filter_local_events(
    events: Sequence[Event],
    actor_id: str,
    *,
    limit: int = LOCAL_EVENTS_LIMIT,
) -> list[Event]:
    """只返回 ``actor_id`` 自己的事件：按 ``seq`` 升序取最近 ``limit`` 条。

    第 8 章 §8.2 构造规则 2 要求 ``local_events`` 只含该 actor 可见事件、不给全局流水；
    M3 采用保守可见性口径（切片 010 任务要求 4）：可见性 = ``event.actor_id == actor_id``，
    不含规则判定事件、也不含其他角色的行动事件。``seq`` 全局唯一，故同一 actor 的事件
    按 ``seq`` 升序即时间序，取尾部 ``limit`` 条即“最近”。

    失败分支：``actor_id`` 为空 / ``limit < 1`` / 输入含未盖章事件 → ``ValueError``
    （感知切片只消费已提交事件，见 ``_event_seq``）。
    """
    if not actor_id:
        raise ValueError("actor_id must be a non-empty string")
    if limit < 1:
        raise ValueError(f"limit must be positive, got {limit}")
    visible = [event for event in events if event.actor_id == actor_id]
    ordered = sorted(visible, key=_event_seq)
    return ordered[-limit:]
