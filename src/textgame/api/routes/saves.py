"""N3-048 存档导出/导入路由（第 6 章 §6.7；只读导出 + 幂等导入，后端优先）。

=====================================  ======  ==========================================
端点                                   成功    说明
=====================================  ======  ==========================================
GET  /api/v1/games/{game_id}/export    200     单文件存档包（只读、不写库/事件/快照）
POST /api/v1/saves/import              201     ``{game_id, turn, version, snapshot_version}``
                                                （校验后经 GameEngine 重放为新存档；必带幂等键）
=====================================  ======  ==========================================

失败统一为 4xx/5xx + ``{ok:false, error_code, message, violations?}``（第 6 章 §6.7）：
未知存档 404 ``NOT_FOUND``；包结构/校验和/回放不一致 422 ``SCHEMA_VIOLATION``；
版本不支持/事件数超限 422 ``INVALID_INPUT``；包体积超限 413 ``INVALID_INPUT``；
同幂等键异包 409 ``CONFLICT``。

依赖装配：优先用组合根注入的 ``app.state.save_service``；未注入时（如测试用
``create_app(orchestrator=...)``）从既有 ``TurnOrchestrator`` 已持有的仓储/引擎惰性装配
一次并缓存（``GameEngine`` 是唯一写入者，导入不旁路事件溯源）。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from textgame.api.deps import require_idempotency_key
from textgame.api.errors import ApiError
from textgame.api.save_schemas import SaveImportResponse
from textgame.domain.results import ErrorCode
from textgame.services.save_service import SaveService, SaveServiceError

router = APIRouter(prefix="/api/v1", tags=["saves"])

#: 导入端点路径（``app.py`` 的请求体上限中间件据此对该路径单独放行 8MB+报文头预算）。
SAVE_IMPORT_PATH = "/api/v1/saves/import"


def _api_error(exc: SaveServiceError) -> ApiError:
    """``SaveServiceError`` → 统一失败信封（含 413 等状态覆盖）。"""
    return ApiError(
        exc.error_code,
        exc.message,
        violations=exc.violations,
        status_override=exc.http_status,
    )


def get_save_service(request: Request) -> SaveService:
    """返回组合根注入的 ``SaveService``；缺失时从已注入编排器惰性装配并缓存。"""
    service: SaveService | None = getattr(request.app.state, "save_service", None)
    if service is not None:
        return service
    orchestrator = getattr(request.app.state, "orchestrator", None)
    repo = getattr(orchestrator, "_repo", None) if orchestrator is not None else None
    engine = getattr(orchestrator, "_engine", None) if orchestrator is not None else None
    if repo is None or engine is None:
        raise ApiError(ErrorCode.INTERNAL, "服务未完成存档服务装配")
    service = SaveService(repo=repo, engine=engine)
    request.app.state.save_service = service
    return service


@router.get("/games/{game_id}/export")
def export_save(
    game_id: str,
    save_service: Annotated[SaveService, Depends(get_save_service)],
) -> dict[str, Any]:
    """导出一局游戏为单文件 JSON 存档包（只读：不改库、不写事件、不写快照）。

    响应即第 6 章 §6.7 的存档包对象（``export_version="1"`` + ``game``/``events``/
    ``snapshot``/``checksum``）；未知/已删除存档 404 ``NOT_FOUND``。
    """
    try:
        return save_service.export_save(game_id)
    except SaveServiceError as exc:
        raise _api_error(exc) from exc


@router.post("/saves/import", status_code=201, response_model=SaveImportResponse)
def import_save(
    body: dict[str, Any],
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    save_service: Annotated[SaveService, Depends(get_save_service)],
) -> SaveImportResponse:
    """把一个存档包导入为**新的一局**（必带 ``Idempotency-Key``）。

    先校验包结构/checksum/事件回放一致性（失败零写入），通过后经唯一写入者
    ``GameEngine`` 建局并逐回合重放；同键同包幂等回放首次 201 响应，同键异包 409。
    """
    try:
        result = save_service.import_save(body, idempotency_key=idempotency_key)
    except SaveServiceError as exc:
        raise _api_error(exc) from exc
    return SaveImportResponse(**result)


__all__ = ["SAVE_IMPORT_PATH", "router"]
