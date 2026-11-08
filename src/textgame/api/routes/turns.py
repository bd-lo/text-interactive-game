"""回合路由 (第 9 章、契约 §5.1/§5.4)：``POST /api/v1/turns``。

业务失败仍返回 HTTP 200 + ``TurnResult(ok=false)``；只有协议/传输错误（缺
``Idempotency-Key``、body 校验失败）由异常处理器返回 422 信封。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from textgame.api.deps import get_orchestrator, require_idempotency_key
from textgame.api.schemas import TurnRequestBody
from textgame.orchestration.dto import TurnRequest
from textgame.orchestration.turn_orchestrator import TurnOrchestrator
from textgame.services.dto import TurnResult

router = APIRouter(prefix="/api/v1", tags=["turns"])


@router.post("/turns", response_model=TurnResult)
def create_turn(
    body: TurnRequestBody,
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    orchestrator: Annotated[TurnOrchestrator, Depends(get_orchestrator)],
) -> TurnResult:
    """执行一个回合：成功返回新版本与屏文本，业务失败返回 ``TurnResult(ok=false)``。"""
    request = TurnRequest.model_validate(
        {
            "game_id": body.game_id,
            "input": body.text,
            "expected_version": body.expected_version,
            "idempotency_key": idempotency_key,
        }
    )
    return orchestrator.run(request)


__all__ = ["router"]
