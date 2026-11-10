"""接入层请求/响应 DTO (第 6 章 6.7、契约 §5)。

成功响应直接返回业务 payload（不套 ``ok/data`` 信封）；请求体额外字段一律拒绝
（``extra="forbid"`` → 422 ``INVALID_INPUT``）。回合的 ``input`` 字段用别名实现，
避免在 Python 侧出现名为 ``input`` 的属性（ruff A003）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from textgame.domain.character_card import CardSource, CharacterCard


class TurnRequestBody(BaseModel):
    """``POST /api/v1/turns`` 请求体；JSON key 保持 ``input``。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    game_id: str = Field(min_length=1, max_length=64)
    text: str = Field(alias="input", min_length=1, max_length=500)
    expected_version: int = Field(ge=0)


class CreateGameBody(BaseModel):
    """``POST /api/v1/games`` 请求体；可省略，``seed`` 缺省用服务端默认种子。

    可选 ``scenario``（``m2`` 单场景 / ``m3`` 多角色场景 / ``m4`` 制造场景），缺省
    ``m2`` 保持 M2 契约与回放不变（D-M3-3/D-M4-11）。D-M6-2 加法：``card_id`` 指定人物卡
    建局（与 ``seed``/``scenario`` 互斥，同给 → 422 ``INVALID_INPUT``）；不提供时行为与
    M2–M4 完全一致。
    """

    model_config = ConfigDict(extra="forbid")

    card_id: str | None = Field(default=None, min_length=1, max_length=64)
    seed: int | None = Field(default=None, ge=0)
    scenario: Literal["m2", "m3", "m4"] = "m2"


class CreatedGameResponse(BaseModel):
    """``POST /api/v1/games`` 201 响应。"""

    model_config = ConfigDict(extra="forbid")

    game_id: str
    turn: int
    version: int
    snapshot_version: int
    save_seed: str


class GameViewResponse(BaseModel):
    """``GET /api/v1/games/{game_id}`` 200 响应（刷新/重连恢复用）。"""

    model_config = ConfigDict(extra="forbid")

    game_id: str
    turn: int
    version: int
    snapshot_version: int
    status: Literal["active", "ended"]
    last_text: str | None = None


class ItemRefResponse(BaseModel):
    """实体引用（房间可见物品 / 玩家背包物品共用）：``{item_id, name}``。"""

    model_config = ConfigDict(extra="forbid")

    item_id: str
    name: str


class NpcRefResponse(BaseModel):
    """房间可见 NPC 引用：``{npc_id, name}``。"""

    model_config = ConfigDict(extra="forbid")

    npc_id: str
    name: str


class RoomViewResponse(BaseModel):
    """玩家所在房间视图（只读；与 ``QueryService.get_room_view`` 同口径）。"""

    model_config = ConfigDict(extra="forbid")

    room_id: str
    title: str
    description: str
    exits: dict[str, str]
    items: list[ItemRefResponse]
    npcs: list[NpcRefResponse]


class InventoryItemResponse(BaseModel):
    """``/view`` 玩家背包物品（M6 加法字段 ``qty``：同模板实例聚合计数）。

    与 ``ItemRefResponse``（房间可见物品）分开建模，避免改房间物品既有契约；``item_id``/
    ``name`` 取该模板首个实例，``qty`` 为 ``item.tags[0]``（无 tags 时用自身 id）去重后的
    数量，与 ``CraftService`` 的背包聚合口径一致（D-M4-1）。
    """

    model_config = ConfigDict(extra="forbid")

    item_id: str
    name: str
    qty: int = Field(default=1, ge=1)


class GamePlayerViewResponse(BaseModel):
    """``GET /api/v1/games/{game_id}/view`` 200 响应（M6 状态面板/终端客户端契约）。

    只读：不产生事件、``version`` 不变；``version`` 语义与 ``GameViewResponse`` 一致。
    """

    model_config = ConfigDict(extra="forbid")

    game_id: str
    turn: int
    version: int
    snapshot_version: int
    status: Literal["active", "ended"]
    hp: int
    max_hp: int
    inventory: list[InventoryItemResponse]
    room: RoomViewResponse


class ReplayEventResponse(BaseModel):
    """``GET /api/v1/games/{game_id}/replay`` 的事件项（D-M6-3，与事件表字段一一对应）。

    ``result.narrative`` 原样保留（文本回放的数据源）；``ts`` 为 UTC 时间戳。
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    seq: int
    turn_id: int
    actor_id: str
    method: str
    event_type: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    rng_seed: int
    ts: datetime


class ReplayResponse(BaseModel):
    """``GET /api/v1/games/{game_id}/replay`` 200 响应；``events`` 按全局 ``seq`` 升序。

    ``to_seq`` 为本次返回末条事件的 ``seq``；空窗口（``from_seq == last_seq + 1``）时
    为 ``from_seq - 1``（即 ``to_seq < from_seq`` 表示无事件）。
    """

    model_config = ConfigDict(extra="forbid")

    events: list[ReplayEventResponse]
    from_seq: int
    to_seq: int


class CharacterCardListItemResponse(BaseModel):
    """``GET /api/v1/character-cards`` 列表项（第 6 章 §6.7：卡片库列表）。"""

    model_config = ConfigDict(extra="forbid")

    card_id: str
    name: str
    source: CardSource
    generated_by: str | None = None
    updated_at: datetime


class CharacterCardListResponse(BaseModel):
    """``GET /api/v1/character-cards`` 200 响应 ``{cards, total}``。"""

    model_config = ConfigDict(extra="forbid")

    cards: list[CharacterCardListItemResponse]
    total: int


class CharacterCardDetailResponse(BaseModel):
    """``GET /api/v1/character-cards/{card_id}`` 200 响应 ``{card}``。"""

    model_config = ConfigDict(extra="forbid")

    card: CharacterCard


class CharacterCardWarningResponse(BaseModel):
    """人物卡告警项（与生成接口的 ``validation.warnings`` 同构：``[{code, message, field?}]``）。"""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    field: str | None = None


class CharacterCardImportResponse(BaseModel):
    """``POST /api/v1/character-cards/import`` 201 响应 ``{card_id, card, warnings}``。"""

    model_config = ConfigDict(extra="forbid")

    card_id: str
    card: CharacterCard
    warnings: list[CharacterCardWarningResponse]


class CharacterCardSaveResponse(BaseModel):
    """``POST /api/v1/character-cards``（保存）201 响应 ``{card_id, card}``。"""

    model_config = ConfigDict(extra="forbid")

    card_id: str
    card: CharacterCard


class CharacterCardValidationErrorResponse(BaseModel):
    """查询式校验错误项：``{loc, type, msg}``（与第 6 章 §6.7 示例一致）。"""

    model_config = ConfigDict(extra="forbid")

    loc: list[str]
    type: str
    msg: str


class CharacterCardValidateResponse(BaseModel):
    """``POST /api/v1/character-cards/validate`` 200 响应 ``{valid, errors}``（只读无副作用）。"""

    model_config = ConfigDict(extra="forbid")

    valid: bool
    errors: list[CharacterCardValidationErrorResponse]


class AuditRecordResponse(BaseModel):
    """``GET /api/v1/games/{game_id}/audit`` 单条审计记录（读模型）。"""

    model_config = ConfigDict(extra="forbid")

    turn_id: int
    attempt: int
    proposal_id: str
    resolution_id: str | None = None
    actor_id: str
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    reason: str
    priority: int
    source: str
    verdict: str | None = None
    verdict_reason: str
    status: Literal["committed", "conflicted", "aborted"]


class AuditListResponse(BaseModel):
    """``GET /api/v1/games/{game_id}/audit`` 200 响应（只读，零写入）。"""

    model_config = ConfigDict(extra="forbid")

    records: list[AuditRecordResponse]


__all__ = [
    "AuditListResponse",
    "AuditRecordResponse",
    "CharacterCardDetailResponse",
    "CharacterCardImportResponse",
    "CharacterCardListItemResponse",
    "CharacterCardListResponse",
    "CharacterCardSaveResponse",
    "CharacterCardValidateResponse",
    "CharacterCardValidationErrorResponse",
    "CharacterCardWarningResponse",
    "CreateGameBody",
    "CreatedGameResponse",
    "GamePlayerViewResponse",
    "GameViewResponse",
    "InventoryItemResponse",
    "ItemRefResponse",
    "NpcRefResponse",
    "ReplayEventResponse",
    "ReplayResponse",
    "RoomViewResponse",
    "TurnRequestBody",
]
