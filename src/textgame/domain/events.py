"""第 5 章 5.2/5.3 的 Event 与第 14 章 14.7 的 EventType 枚举。

``Event`` 同时服务两个阶段：服务层产出**草稿态**（无 ``id``/``seq``/``ts``/``rng_seed``），
``GameEngine`` 提交前由 ``EventLog.stamp()`` 补全为**盖章态**；用“全有或全无”校验避免出现
半盖章事件。``EventType`` 是 reducer 路由键：新增类型必须同时在 ``EventType`` 登记、注册
reducer、并写 ``event_schema`` 版本（第 14 章 14.7）。

本切片只放模型与枚举；``EventLog`` 归档与盖章实现由 M1-003 追加。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .types import EventId, TurnId


class Event(BaseModel):
    """一次写操作的不可变记录。草稿态 → GameEngine/EventLog 盖章后为完成态。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EventId | None = None  # 盖章时生成：f"evt_{seq:06d}"
    turn_id: TurnId
    seq: int | None = Field(default=None, ge=1)  # 存档内全局单调递增，跨回合连续，从 1 起
    actor_id: str = Field(min_length=1, max_length=64)
    method: Annotated[str, Field(pattern=r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$", max_length=48)]
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
    CRAFT_DETERMINISTIC = "CRAFT_DETERMINISTIC"
    CRAFT_GENERATED = "CRAFT_GENERATED"
    CRAFT_REJECTED = "CRAFT_REJECTED"
    ADMIN_CORRECTION = "ADMIN_CORRECTION"
