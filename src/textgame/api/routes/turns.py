"""回合路由 (第 9 章、契约 §5.1/§5.4)。

``POST /api/v1/turns``：业务失败仍返回 HTTP 200 + ``TurnResult(ok=false)``；只有协议/传输
错误（缺 ``Idempotency-Key``、body 校验失败）由异常处理器返回 422 信封。

``POST /api/v1/turns/stream``（N2-038 / ADR D-N2-1/D-N2-2/D-N2-5）：同一回合语义的 NDJSON
流式端点。5 种事件 ``start``/``delta``/``replace``/``done``/``error``，每行一个 JSON；服务端
在**回合完整结束后**照常提交（断连不撤销），``done.result`` 与 ``/turns`` 的 ``TurnResult``
完全相同。流内业务失败（幂等冲突/版本过期/未知存档等）以 ``error`` 事件表达、HTTP 仍 200；
协议错误（缺头/body 非法）在流开始前返回 4xx 统一信封（与 ``/turns`` 同口径）。
"""

from __future__ import annotations

import json
import queue
import threading
from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from textgame.api.deps import get_orchestrator, require_idempotency_key
from textgame.api.errors import ApiError
from textgame.api.schemas import (
    NDJSON_MEDIA_TYPE,
    TurnRequestBody,
    TurnStreamDeltaEvent,
    TurnStreamDoneEvent,
    TurnStreamErrorEvent,
    TurnStreamReplaceEvent,
    TurnStreamStartEvent,
)
from textgame.config.settings import get_settings
from textgame.domain.results import ErrorCode
from textgame.orchestration.dto import TurnRequest
from textgame.orchestration.turn_orchestrator import TurnOrchestrator
from textgame.services.dto import TurnResult

router = APIRouter(prefix="/api/v1", tags=["turns"])

#: 队列哨兵：工作线程结束（``done``/``error`` 已入队）后由生成器收尾。
_STREAM_END = object()

#: ``error`` 事件内部异常的玩家可读消息（不泄漏实现细节，第 15 章）。
_INTERNAL_STREAM_ERROR_TEXT = "回合处理发生内部错误，本回合没有写入任何状态。"
#: ``replace`` 事件的机器可读原因（``TEMPLATE_FALLBACK``/``NARRATIVE_REVISED``）。
_REPLACE_REASON_TEMPLATE = "TEMPLATE_FALLBACK"
_REPLACE_REASON_REVISED = "NARRATIVE_REVISED"


def _encode_event(event: BaseModel) -> str:
    """事件模型 → 单行 JSON（UTF-8、紧凑分隔符；``ensure_ascii=False`` 保留中文）。"""
    payload = event.model_dump(mode="json")
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"


def _replace_reason(result: TurnResult) -> str:
    """权威文本与已显示增量不一致时，``replace`` 的机器可读原因。"""
    return (
        _REPLACE_REASON_TEMPLATE
        if result.narrative_source == "template"
        else _REPLACE_REASON_REVISED
    )


def _enqueue_final_events(
    enqueue: Callable[[BaseModel], None],
    result: TurnResult,
    streamed: str,
) -> None:
    """按权威 ``TurnResult`` 补齐流尾事件：``delta``(补足) / ``replace`` / ``done`` / ``error``。

    - 业务失败（未提交）→ 仅 ``error``（HTTP 200；与 ``/turns`` 的 ``TurnResult(ok=false)`` 同口径）；
    - 本地无增量（模板单段/幂等重放）→ 一条完整 ``delta`` 后 ``done``（D-N2-5 幂等重放语义）；
    - 已显示增量是权威文本前缀 → 补发剩余 ``delta``；
    - 其它（校验失败回退模板/一次修复/多角色拼接）→ ``replace`` 权威文本后 ``done``。
    """
    if not result.ok:
        enqueue(
            TurnStreamErrorEvent(
                error_code=result.error_code or ErrorCode.INTERNAL,
                message=result.text or "回合未提交",
            )
        )
        return
    final_text = result.text
    if final_text != streamed:
        if not streamed:
            if final_text:
                enqueue(TurnStreamDeltaEvent(text=final_text))
        elif final_text.startswith(streamed):
            remainder = final_text[len(streamed) :]
            if remainder:
                enqueue(TurnStreamDeltaEvent(text=remainder))
        else:
            enqueue(TurnStreamReplaceEvent(text=final_text, reason=_replace_reason(result)))
    enqueue(TurnStreamDoneEvent(result=result))


def _stream_generator(
    orchestrator: TurnOrchestrator,
    request: TurnRequest,
    *,
    expected_version: int,
) -> Iterator[str]:
    """NDJSON 生成器：工作线程跑同一 ``orchestrator.run``，本线程从队列读事件并 yield。

    断连语义（D-N2-5）：客户端断开只停止消费事件，工作线程继续完成叙事/校验/提交（回合
    已消耗 LLM 成本，提交比回滚更符合唯一写入者/审计口径）；重连后经 ``GET /games/{id}/view``
    与 ``/replay`` 看到已提交回合。
    """
    events: queue.Queue[object] = queue.Queue()
    streamed_parts: list[str] = []

    def enqueue(event: BaseModel) -> None:
        events.put(_encode_event(event))

    def on_delta(text: str) -> None:
        if not text:
            return
        streamed_parts.append(text)
        enqueue(TurnStreamDeltaEvent(text=text))

    # start 先入队：保证「先 start、后 delta/done/error」的事件顺序。
    enqueue(
        TurnStreamStartEvent(
            turn_id=expected_version + 1,
            expected_version=expected_version,
        )
    )

    def worker() -> None:
        try:
            result = orchestrator.run(request, on_delta=on_delta)
            _enqueue_final_events(enqueue, result, "".join(streamed_parts))
        except Exception:
            enqueue(
                TurnStreamErrorEvent(
                    error_code=ErrorCode.INTERNAL,
                    message=_INTERNAL_STREAM_ERROR_TEXT,
                )
            )
        finally:
            events.put(_STREAM_END)

    worker_thread = threading.Thread(target=worker, name="turn-stream", daemon=True)
    worker_thread.start()
    while True:
        item = events.get()
        if item is _STREAM_END:
            break
        assert isinstance(item, str)
        yield item
    # 正常路径工作线程已结束；清理引用（断连路径由 daemon 线程继续完成提交，不阻塞收尾）。
    worker_thread.join(timeout=0)


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


@router.post(
    "/turns/stream",
    response_model=None,
    responses={200: {"content": {NDJSON_MEDIA_TYPE: {}}}},
)
def create_turn_stream(
    body: TurnRequestBody,
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    orchestrator: Annotated[TurnOrchestrator, Depends(get_orchestrator)],
) -> StreamingResponse:
    """流式执行一个回合（NDJSON 5 事件；D-N2-1/D-N2-2/D-N2-5）。

    - 开关 ``TEXTGAME_STREAM_ENABLED=false`` → 404 统一失败信封（前端自动降级 ``/turns``）；
    - 请求体/``Idempotency-Key`` 校验失败 → 流开始前 4xx（与 ``/turns`` 同口径）；
    - 流内失败一律 ``error`` 事件、HTTP 200；``Idempotency-Key``/CAS/幂等语义完全复用
      ``TurnOrchestrator.run``（同一唯一写入者），事件只在回合完整结束后提交。
    """
    if not get_settings().stream_enabled:
        raise ApiError(
            ErrorCode.NOT_FOUND,
            "流式回合端点未启用（TEXTGAME_STREAM_ENABLED=false）；请改用 POST /api/v1/turns",
        )
    request = TurnRequest.model_validate(
        {
            "game_id": body.game_id,
            "input": body.text,
            "expected_version": body.expected_version,
            "idempotency_key": idempotency_key,
        }
    )
    return StreamingResponse(
        _stream_generator(orchestrator, request, expected_version=body.expected_version),
        media_type=NDJSON_MEDIA_TYPE,
        headers={"Cache-Control": "no-store"},
    )


__all__ = ["router"]
