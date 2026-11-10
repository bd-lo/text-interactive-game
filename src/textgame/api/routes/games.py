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
    GamePlayerViewResponse,
    GameViewResponse,
    ReplayEventResponse,
    ReplayResponse,
)
from textgame.domain.events import Event
from textgame.domain.results import ErrorCode
from textgame.orchestration.turn_orchestrator import (
    CreateGameError,
    CreateGameRequest,
    TurnOrchestrator,
)
from textgame.services.audit_service import AuditService
from textgame.services.query_service import GameNotFoundError, QueryService

router = APIRouter(prefix="/api/v1", tags=["games"])

#: M6 回放契约 D-M6-3：``limit`` 默认 200、上限 500。
_REPLAY_DEFAULT_LIMIT = 200
_REPLAY_MAX_LIMIT = 500


def _replay_event(event: Event) -> ReplayEventResponse:
    """已盖章 ``Event`` → 回放响应项；未完全盖章属存储层内部错误（映射 500 信封）。"""
    if event.id is None or event.seq is None or event.ts is None:
        raise ApiError(ErrorCode.INTERNAL, "回放事件未完全盖章")
    return ReplayEventResponse(
        id=event.id,
        seq=event.seq,
        turn_id=event.turn_id,
        actor_id=event.actor_id,
        method=event.method,
        event_type=event.event_type.value if event.event_type is not None else None,
        params=dict(event.params),
        result=dict(event.result),
        rng_seed=event.rng_seed if event.rng_seed is not None else 0,
        ts=event.ts,
    )


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


@router.get("/games/{game_id}/view", response_model=GamePlayerViewResponse)
def get_game_player_view(
    game_id: str,
    query_service: Annotated[QueryService, Depends(get_query_service)],
) -> GamePlayerViewResponse:
    """返回玩家视角对局视图（只读，零事件、version 不变）；未知存档 404 信封。

    契约：``{game_id, turn, version, snapshot_version, status, hp, max_hp,
    inventory[], room{room_id,title,description,exits,items,npcs}}``（M6 状态面板/终端客户端）。
    """
    try:
        view = query_service.get_player_view(game_id)
    except GameNotFoundError as exc:
        raise ApiError(ErrorCode.NOT_FOUND, f"存档不存在: {game_id}") from exc
    return GamePlayerViewResponse(**view)


@router.get("/games/{game_id}/replay", response_model=ReplayResponse)
def get_game_replay(
    game_id: str,
    query_service: Annotated[QueryService, Depends(get_query_service)],
    from_seq: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=_REPLAY_MAX_LIMIT)] = _REPLAY_DEFAULT_LIMIT,
) -> ReplayResponse:
    """按全局 ``seq`` 升序返回事件切片（M6 回放契约 D-M6-3，只读零写入）。

    ``from_seq`` 缺省 1（存档起点）、``limit`` 缺省 200（上限 500）；``result.narrative``
    原样保留供文本回放。未知存档 404 信封；``from_seq`` 非正整数或越过「末事件后一位」
    （``> last_seq + 1``）→ 422 ``INVALID_INPUT``；``from_seq == last_seq + 1`` 返回空窗口。
    """
    try:
        events = query_service.read_events(game_id)
    except GameNotFoundError as exc:
        raise ApiError(ErrorCode.NOT_FOUND, f"存档不存在: {game_id}") from exc
    last_seq = events[-1].seq or 0 if events else 0
    if from_seq > last_seq + 1:
        raise ApiError(
            ErrorCode.INVALID_INPUT,
            f"from_seq 越界: 存档最后事件序号为 {last_seq}",
        )
    window = [event for event in events if (event.seq or 0) >= from_seq][:limit]
    items = [_replay_event(event) for event in window]
    return ReplayResponse(
        events=items,
        from_seq=from_seq,
        to_seq=items[-1].seq if items else from_seq - 1,
    )


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
