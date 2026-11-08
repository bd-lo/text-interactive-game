"""对局路由 (第 6 章 6.7、契约 §5.1)：创建存档与对局视图。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from textgame.api.deps import get_orchestrator, get_query_service, require_idempotency_key
from textgame.api.errors import ApiError
from textgame.api.schemas import CreatedGameResponse, CreateGameBody, GameViewResponse
from textgame.domain.results import ErrorCode
from textgame.orchestration.turn_orchestrator import (
    CreateGameError,
    CreateGameRequest,
    TurnOrchestrator,
)
from textgame.services.query_service import GameNotFoundError, QueryService

router = APIRouter(prefix="/api/v1", tags=["games"])


@router.post("/games", status_code=201, response_model=CreatedGameResponse)
def create_game(
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    orchestrator: Annotated[TurnOrchestrator, Depends(get_orchestrator)],
    body: CreateGameBody | None = None,
) -> CreatedGameResponse:
    """创建存档；同键同参幂等回放（零写入），同键异参 409。"""
    request = CreateGameRequest(
        seed=body.seed if body is not None else None,
        idempotency_key=idempotency_key,
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


__all__ = ["router"]
