"""接入层请求/响应 DTO (第 6 章 6.7、契约 §5)。

成功响应直接返回业务 payload（不套 ``ok/data`` 信封）；请求体额外字段一律拒绝
（``extra="forbid"`` → 422 ``INVALID_INPUT``）。回合的 ``input`` 字段用别名实现，
避免在 Python 侧出现名为 ``input`` 的属性（ruff A003）。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class TurnRequestBody(BaseModel):
    """``POST /api/v1/turns`` 请求体；JSON key 保持 ``input``。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    game_id: str = Field(min_length=1, max_length=64)
    text: str = Field(alias="input", min_length=1, max_length=500)
    expected_version: int = Field(ge=0)


class CreateGameBody(BaseModel):
    """``POST /api/v1/games`` 请求体；可省略，``seed`` 缺省用服务端默认种子。

    M3 新增可选 ``scenario``（``m2`` 单场景 / ``m3`` 多角色场景），缺省 ``m2``
    保持 M2 契约与回放不变（D-M3-3）。
    """

    model_config = ConfigDict(extra="forbid")

    seed: int | None = Field(default=None, ge=0)
    scenario: Literal["m2", "m3"] = "m2"


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
    "CreateGameBody",
    "CreatedGameResponse",
    "GameViewResponse",
    "TurnRequestBody",
]
