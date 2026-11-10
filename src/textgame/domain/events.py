"""第 5 章 5.2/5.3 的 Event 与第 14 章 14.7 的 EventType 枚举。

``Event`` 同时服务两个阶段：服务层产出**草稿态**（无 ``id``/``seq``/``ts``/``rng_seed``），
``GameEngine`` 提交前由 ``EventLog.stamp()`` 补全为**盖章态**；用“全有或全无”校验避免出现
半盖章事件。``EventType`` 是 reducer 路由键：新增类型必须同时在 ``EventType`` 登记、注册
reducer、并写 ``event_schema`` 版本（第 14 章 14.7）。

``EventLog`` (M1 内存实现) 负责盖章、追加、读取与派生式回放；持久化 (SQLAlchemy
``events`` 表、快照、哈希链) 由 M2/M3 的 ``persistence`` 落地 (第 14 章 14.3/14.6)。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .rules.dice import DerivedRandom
from .types import EventId, TurnId


class Event(BaseModel):
    """一次写操作的不可变记录。草稿态 → GameEngine/EventLog 盖章后为完成态。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EventId | None = None  # 盖章时生成：f"evt_{seq:06d}"
    turn_id: TurnId
    seq: int | None = Field(default=None, ge=1)  # 存档内全局单调递增，跨回合连续，从 1 起
    actor_id: str = Field(min_length=1, max_length=64)
    method: Annotated[str, Field(pattern=r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$", max_length=48)]
    event_type: EventType | None = None  # M2 扩展 (第 14 章 14.2 可选字段之一)，reducer 路由键
    params: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    rng_seed: int | None = Field(default=None, ge=0, le=0x7FFF_FFFF_FFFF_FFFF)  # 有符号 64 位
    ts: datetime | None = None

    @model_validator(mode="after")
    def _stamped_all_or_none(self) -> Event:
        flags = (
            self.id is not None,
            self.seq is not None,
            self.rng_seed is not None,
            self.ts is not None,
        )
        if any(flags) and not all(flags):
            raise ValueError("event must be fully stamped or fully draft")
        return self

    @field_validator("ts")
    @classmethod
    def _ts_must_be_utc(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return None
        if v.tzinfo is None:
            raise ValueError("ts must be timezone-aware")
        return v.astimezone(timezone.utc)

    @property
    def is_stamped(self) -> bool:
        return self.id is not None


class EventType(str, Enum):
    """第 14 章 14.7 核心事件类型（reducer 路由键；值用文档字面名）。

    ``game_created`` 为 v0.2 定稿名（首事件，``create_game`` 单事务写入），其余为大写下划线。
    """

    GAME_CREATED = "game_created"
    CHARACTER_MOVED = "CHARACTER_MOVED"
    ITEM_TAKEN = "ITEM_TAKEN"
    ITEM_CONSUMED = "ITEM_CONSUMED"
    # M2 按第 6 章 §6.4 动作表扩展（第 14 章 §14.7 核心表未列，已登记 M2 契约偏离）：
    # talk_to -> dialogue_exchanged
    DIALOGUE_EXCHANGED = "DIALOGUE_EXCHANGED"
    CRAFT_DETERMINISTIC = "CRAFT_DETERMINISTIC"
    CRAFT_GENERATED = "CRAFT_GENERATED"
    CRAFT_REJECTED = "CRAFT_REJECTED"
    ADMIN_CORRECTION = "ADMIN_CORRECTION"
    # H4/L1 加法：可重复的环境伤害（story 枯井寒气等）；旧场景永不产生该事件，
    # reducer 只改 actor 的 hp（clamp 0..max_hp），不改背包/位置/其他字段。
    ENVIRONMENT_DAMAGE = "ENVIRONMENT_DAMAGE"


# Event.event_type 需要在 EventType 定义之后解析（模型定义在枚举之前，保持 M1 文件结构）。
Event.model_rebuild()


class ReplayMismatchError(ValueError):
    """回放重算与记录不一致 (第 18 章 18.3): 携带首个偏差事件的 ``seq`` 与字段。"""

    def __init__(self, *, seq: int, field: str, expected: object, actual: object) -> None:
        """记录偏差位置与新旧值，便于回放失败时定位到首个偏差事件。"""
        self.seq = seq
        self.field = field
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"replay mismatch at seq={seq}: field={field} expected={expected!r}, actual={actual!r}"
        )


class EventLog:
    """M1 内存事件日志: 盖章 (stamp)、追加 (append)、读取 (read) 与派生式回放 (replay)。

    第 5 章 5.3 / 第 14 章 14.2: ``seq`` 为存档内全局单调递增序号 (从 1 起、跨回合连续)，
    ``id = f"evt_{seq:06d}"``；同一事件重复派生必得同一子种子，回放只校验 ``rng_seed``
    与 ``id``/``seq`` 是否与派生规则一致，不重跑随机 (第 18 章 18.3)。
    M1 为内存实现；持久化 (SQLAlchemy ``events`` 表、快照、哈希链) 由 M2/M3 的
    ``persistence`` 落地 (第 14 章 14.3/14.6)。
    """

    def __init__(self, save_seed: str) -> None:
        """``save_seed`` 为存档种子 (非空)；空日志 ``last_seq == 0``、``seq`` 从 1 起。"""
        if not save_seed:
            raise ValueError("save_seed must be a non-empty string")
        self._save_seed: str = save_seed
        self._events: list[Event] = []
        self._last_seq: int = 0

    @property
    def last_seq(self) -> int:
        """最后一条已追加事件的 seq；空日志为 0。"""
        return self._last_seq

    def stamp(self, drafts: list[Event]) -> list[Event]:
        """为草稿事件分配 ``seq``/``id``/``rng_seed``/``ts`` 并返回新列表。

        按传入顺序从 ``self._last_seq + 1`` 起分配全局 ``seq``；``rng_seed`` 由
        ``DerivedRandom.derive_seed(save_seed, turn_id, actor_id, method, seq)`` 派生
        (返回值已掩码到 ``0..2**63-1``，不得重复掩码，第 10 章 10.3)；``ts`` 为 UTC。
        盖章前对每条事件做一次 ``json.dumps(..., allow_nan=False)`` 序列化校验 (第 5 章
        5.8：失败按 ``SCHEMA_VIOLATION`` 处理)。本方法**不写内部状态**，提交语义由
        ``append`` 完成；已盖章事件作为输入 → ``ValueError``。
        """
        stamped: list[Event] = []
        for index, draft in enumerate(drafts):
            if draft.is_stamped:
                raise ValueError(
                    f"stamp only accepts draft events; got stamped event at index {index}"
                )
            seq = self._last_seq + index + 1
            try:
                # 用 python 模式 dump 再 allow_nan=False 校验：Pydantic 的 mode="json" 默认
                # 把 NaN/Infinity 静默转成 null (ser_json_inf_nan="null")，会让 NaN 漏检。
                json.dumps(draft.model_dump(), allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"event at seq={seq} (method={draft.method}) is not JSON serializable: {exc}"
                ) from exc
            stamped.append(
                draft.model_copy(
                    update={
                        "id": f"evt_{seq:06d}",
                        "seq": seq,
                        "rng_seed": DerivedRandom.derive_seed(
                            self._save_seed,
                            draft.turn_id,
                            draft.actor_id,
                            draft.method,
                            seq,
                        ),
                        "ts": datetime.now(timezone.utc),
                    }
                )
            )
        return stamped

    def append(self, events: list[Event]) -> None:
        """追加一批盖章事件；任一条校验失败则整体拒绝 (不部分写入)。

        校验：事件已盖章、``seq`` 紧接当前 ``_last_seq`` 且连续、``id`` 等于
        ``f"evt_{seq:06d}"``、``ts`` 为 UTC-aware (第 5 章 5.3、第 14 章 14.2)。
        """
        for index, event in enumerate(events):
            expected_seq = self._last_seq + index + 1
            if not event.is_stamped:
                raise ValueError(f"append only accepts stamped events; got draft at index {index}")
            if event.seq != expected_seq:
                raise ValueError(
                    f"event at index {index} has seq={event.seq}, expected {expected_seq}"
                )
            expected_id = f"evt_{expected_seq:06d}"
            if event.id != expected_id:
                raise ValueError(
                    f"event at index {index} has id={event.id!r}, expected {expected_id!r}"
                )
            utc_aware = (
                event.ts is not None
                and event.ts.tzinfo is not None
                and event.ts.utcoffset() == timedelta(0)
            )
            if not utc_aware:
                raise ValueError(f"event at seq={expected_seq} ts must be UTC-aware")
        self._events.extend(events)
        self._last_seq += len(events)

    def read(self, turn_id: int | None = None) -> list[Event]:
        """按 ``seq`` 升序返回事件副本；给定 ``turn_id`` 时只返回该回合事件。

        ``append`` 已保证内部列表按 ``seq`` 升序，且返回的是副本，调用方修改不污染日志。
        """
        if turn_id is None:
            return list(self._events)
        return [event for event in self._events if event.turn_id == turn_id]

    def replay(self) -> list[Event]:
        """按第 18 章 18.3 口径重演并校验，全部一致时返回事件列表。

        逐条用 ``(save_seed, turn_id, actor_id, method, seq)`` 重算 ``rng_seed`` 并与
        记录比对，同时校验 ``seq`` 从 1 连续、``id == f"evt_{seq:06d}"``；首个偏差抛
        ``ReplayMismatchError`` (携带 seq 与字段)，用于检出事件篡改与实现回归。
        """
        for expected_seq, event in enumerate(self._events, start=1):
            if event.seq != expected_seq:
                raise ReplayMismatchError(
                    seq=expected_seq, field="seq", expected=expected_seq, actual=event.seq
                )
            expected_id = f"evt_{expected_seq:06d}"
            if event.id != expected_id:
                raise ReplayMismatchError(
                    seq=expected_seq, field="id", expected=expected_id, actual=event.id
                )
            expected_seed = DerivedRandom.derive_seed(
                self._save_seed,
                event.turn_id,
                event.actor_id,
                event.method,
                expected_seq,
            )
            if event.rng_seed != expected_seed:
                raise ReplayMismatchError(
                    seq=expected_seq,
                    field="rng_seed",
                    expected=expected_seed,
                    actual=event.rng_seed,
                )
        return list(self._events)
