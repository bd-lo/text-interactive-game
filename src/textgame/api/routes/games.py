"""对局路由 (第 6 章 6.7、契约 §5.1)：创建存档、对局视图与审计查询 (M3)。"""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from textgame.api.deps import (
    get_audit_service,
    get_orchestrator,
    get_query_service,
    require_idempotency_key,
)
from textgame.api.errors import ApiError
from textgame.api.schemas import (
    AuditListResponse,
    AuditRecordResponse,
    CreatedGameResponse,
    CreateGameBody,
    GameViewResponse,
)
from textgame.domain.results import ErrorCode
from textgame.orchestration.turn_orchestrator import (
    CreateGameError,
    CreateGameRequest,
    TurnOrchestrator,
)
from textgame.services.audit_service import AuditService
from textgame.services.query_service import GameNotFoundError, QueryService

router = APIRouter(prefix="/api/v1", tags=["games"])


@router.post("/games", status_code=201, response_model=CreatedGameResponse)
def create_game(
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    orchestrator: Annotated[TurnOrchestrator, Depends(get_orchestrator)],
    body: CreateGameBody | None = None,
) -> CreatedGameResponse:
    """创建存档；同键同参幂等回放（零写入），同键异参 409。

    M3 可选 ``scenario``（``m2``/``m3``），缺省 ``m2`` 保持 M2 行为与回放不变。
    """
    request = CreateGameRequest(
        seed=body.seed if body is not None else None,
        idempotency_key=idempotency_key,
        scenario=body.scenario if body is not None else "m2",
    )
    try:
        view = orchestrator.create_game(request)
    except CreateGameError as exc:
        raise ApiError(exc.error_code, exc.message) from exc
    return CreatedGameResponse(
        game_id=view.game_id,
        turn=view.turn,
        version=view.version,
        snapshot_version=view.snapshot_version,
        save_seed=view.save_seed or "",
    )


@router.get("/games/{game_id}", response_model=GameViewResponse)
def get_game(
    game_id: str,
    query_service: Annotated[QueryService, Depends(get_query_service)],
) -> GameViewResponse:
    """返回对局视图（只读，不产生状态）；未知存档 404 信封。"""
    try:
        view = query_service.get_game_view(game_id)
    except GameNotFoundError as exc:
        raise ApiError(ErrorCode.NOT_FOUND, f"存档不存在: {game_id}") from exc
    return GameViewResponse(**view)


@router.get("/games/{game_id}/audit", response_model=AuditListResponse)
def get_audit(
    game_id: str,
    audit_service: Annotated[AuditService, Depends(get_audit_service)],
    turn_id: Annotated[int | None, Query(ge=1)] = None,
) -> AuditListResponse:
    """只读审计查询（M3 契约 D-M3-7）；未知存档 404，非法 ``turn_id`` 422。"""
    try:
        records = audit_service.query(game_id, turn_id=turn_id)
    except GameNotFoundError as exc:
        raise ApiError(ErrorCode.NOT_FOUND, f"存档不存在: {game_id}") from exc
    return AuditListResponse(
        records=[
            AuditRecordResponse.model_validate(
                {key: value for key, value in asdict(record).items() if key != "game_id"}
            )
            for record in records
        ]
    )


__all__ = ["router"]
