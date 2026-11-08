"""第 5 章 5.2 基础类型：ID 正则与共享枚举字面量。

ID 统一用“前缀 + ULID”字符串（``char_...``/``room_...``/``evt_000041``），不暴露自增主键：
事件日志可读、跨库合并无冲突、Agent Prompt 里也不会出现可猜的数字 ID。数据库里再叠加
自己的 BIGINT 主键，领域模型不感知（第 5 章 5.2）。

本模块只依赖标准库与 pydantic，不承载任何业务逻辑。
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

_ULID = r"[0-9A-HJKMNP-TV-Z]{26}"
CharacterId = Annotated[str, Field(pattern=rf"^char_{_ULID}$")]
RoomId = Annotated[str, Field(pattern=rf"^room_{_ULID}$")]
ItemId = Annotated[str, Field(pattern=rf"^item_{_ULID}$")]
NpcId = Annotated[str, Field(pattern=rf"^npc_{_ULID}$")]
QuestId = Annotated[str, Field(pattern=rf"^quest_{_ULID}$")]
EventId = Annotated[str, Field(pattern=r"^evt_[0-9]{6,}$")]
TurnId = Annotated[int, Field(ge=1)]
Direction = Literal["north", "south", "east", "west", "up", "down", "in", "out"]
Verdict = Literal["applied", "rejected", "conflict", "deferred"]
