"""世界书路由（H4-CONTRACT §2）：导入/校验/列表/详情/软删，只做 HTTP 映射。

端点一览（`H4-CONTRACT.md` §2 冻结）：

==============================  ======  ==================================================
端点                            成功    说明
==============================  ======  ==================================================
GET  /api/v1/world-books        200     ``{world_books, total}``（``?query=&limit=&offset=``）
GET  /api/v1/world-books/{id}   200     ``{world_book, entries}``
POST /api/v1/world-books/import 201     multipart 上传 JSON（≤256KB，必带幂等键）
POST /api/v1/world-books/validate 200   ``{valid, errors, warnings, preview}``（只读）
DELETE /api/v1/world-books/{id} 200     ``{world_book_id, deleted}`` 软删（必带幂等键）
==============================  ======  ==================================================

失败统一为 4xx/5xx + ``{ok:false, error_code, message, violations?}``（第 6 章 §6.7）：
文件过大走 ``INVALID_INPUT`` + HTTP 413 特例；格式非法/JSON 非法 422；未知 404；幂等异参 409。
世界书**不参与事件溯源**：本模块只做资料库 CRUD + 只读校验，不产生 Event、不改 GameState。
"""

from __future__ import annotations

from email import policy
from email.parser import BytesParser
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict

from textgame.api.deps import require_idempotency_key
from textgame.api.errors import ApiError
from textgame.api.schemas import (
    WorldBookDeleteResponse,
    WorldBookDetailBodyResponse,
    WorldBookDetailResponse,
    WorldBookEntryResponse,
    WorldBookImportResponse,
    WorldBookListResponse,
    WorldBookSummaryResponse,
    WorldBookValidatePreviewResponse,
    WorldBookValidateResponse,
    WorldBookValidationErrorResponse,
    WorldBookWarningResponse,
)
from textgame.domain.results import ErrorCode
from textgame.services.world_book_service import (
    LIST_DEFAULT_LIMIT,
    LIST_MAX_LIMIT,
    MAX_QUERY_LENGTH,
    MAX_UPLOAD_BYTES,
    WorldBookService,
    WorldBookServiceError,
)

router = APIRouter(prefix="/api/v1/world-books", tags=["world-books"])

#: multipart 报文头/boundary 的额外字节预算（文件本体上限之外）。
_MULTIPART_OVERHEAD_BYTES = 16 * 1024
#: 上传体积超限时的错误消息（与 service 内 413 口径一致）。
_TOO_LARGE_MESSAGE = f"世界书 JSON 超过 {MAX_UPLOAD_BYTES // 1024}KB 上限"


class WorldBookValidateBody(BaseModel):
    """``POST /api/v1/world-books/validate`` 请求体：``{"payload": <任意 JSON 对象>}``。

    故意允许结构不符的 payload（以便逐字段返回 ``errors``），只要求外层 body 带 ``payload``。
    """

    model_config = ConfigDict(extra="forbid")

    payload: Any


def get_world_book_service(request: Request) -> WorldBookService:
    """返回组合根注入的 ``WorldBookService``；缺失视为组合根装配错误（500）。"""
    world_book_service: WorldBookService | None = getattr(
        request.app.state, "world_book_service", None
    )
    if world_book_service is None:
        raise ApiError(ErrorCode.INTERNAL, "服务未完成世界书服务装配")
    return world_book_service


WorldBookServiceDep = Annotated[WorldBookService, Depends(get_world_book_service)]
IdempotencyKeyDep = Annotated[str, Depends(require_idempotency_key)]


def _api_error(exc: WorldBookServiceError) -> ApiError:
    """``WorldBookServiceError`` → 统一失败信封（含 413/404/409/422 映射）。"""
    return ApiError(
        exc.error_code,
        exc.message,
        violations=exc.violations,
        status_override=exc.http_status,
    )


def _extract_multipart_upload(
    content_type_header: str, body: bytes
) -> tuple[str | None, str | None, bytes, str | None]:
    """从 multipart 报文体取出 ``file`` 部件与可选 ``name`` 表单字段。

    项目禁用新增运行时依赖（第 19 章），不复用需要 ``python-multipart`` 的 ``UploadFile``；
    这里用标准库 ``email`` 解析 multipart。返回 ``(filename, part_content_type, content, name)``；
    缺少 ``file`` 部件 → 422。
    """
    try:
        message = BytesParser(policy=policy.default).parsebytes(
            b"Content-Type: "
            + content_type_header.encode("latin-1", "replace")
            + b"\r\n\r\n"
            + body
        )
    except Exception as exc:  # 报文畸形不暴露内部细节
        raise ApiError(ErrorCode.INVALID_INPUT, "multipart 报文解析失败") from exc
    filename: str | None = None
    file_type: str | None = None
    file_content: bytes | None = None
    form_name: str | None = None
    parts = message.iter_parts() if message.is_multipart() else ()
    for part in parts:
        field_name = part.get_param("name", header="content-disposition")
        payload = part.get_payload(decode=True)
        if field_name == "file":
            if not isinstance(payload, bytes):
                raise ApiError(ErrorCode.INVALID_INPUT, "multipart 的 file 部件内容非法")
            filename = part.get_filename()
            file_type = part.get_content_type()
            file_content = payload
        elif field_name == "name" and isinstance(payload, bytes):
            try:
                form_name = payload.decode("utf-8").strip() or None
            except UnicodeDecodeError as exc:
                raise ApiError(
                    ErrorCode.INVALID_INPUT, "multipart 的 name 字段必须为 UTF-8"
                ) from exc
    if file_content is None:
        raise ApiError(ErrorCode.INVALID_INPUT, "multipart 缺少 file 字段")
    return filename, file_type, file_content, form_name


async def _read_multipart_upload(
    request: Request,
) -> tuple[str | None, str | None, bytes, str | None]:
    """读取 ``multipart/form-data`` 上传（总体积 ≤256KB + 报文头预算，超限 413）。"""
    content_type = request.headers.get("content-type", "")
    if not content_type.lower().startswith("multipart/form-data"):
        raise ApiError(ErrorCode.INVALID_INPUT, "只接受 multipart/form-data 上传")
    limit = MAX_UPLOAD_BYTES + _MULTIPART_OVERHEAD_BYTES
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise ApiError(ErrorCode.INVALID_INPUT, _TOO_LARGE_MESSAGE, status_override=413)
    body = await request.body()
    if len(body) > limit:
        raise ApiError(ErrorCode.INVALID_INPUT, _TOO_LARGE_MESSAGE, status_override=413)
    return _extract_multipart_upload(content_type, body)


#: OpenAPI 请求体声明：``import`` 是 multipart（文件部件 + 可选 name），供契约测试读取。
_IMPORT_OPENAPI_EXTRA: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {
                        "file": {
                            "type": "string",
                            "format": "binary",
                            "description": "世界书 JSON 文件（≤256KB，仅 .json）",
                        },
                        "name": {
                            "type": "string",
                            "description": "可选：覆盖世界书名（≤64 字符）",
                        },
                    },
                }
            }
        },
    }
}
#: 校验端点：接受任意 JSON 对象（故意允许 Schema 不符，以便逐字段返回 errors）。
_VALIDATE_OPENAPI_EXTRA: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "description": "``{payload: <待校验的世界书 JSON>}``（允许 Schema 不符）",
                }
            }
        },
    }
}


# ---------------------------------------------------------------------------
# 导入 / 校验（写 + 只读）
# ---------------------------------------------------------------------------
@router.post(
    "/import",
    status_code=201,
    response_model=WorldBookImportResponse,
    openapi_extra=_IMPORT_OPENAPI_EXTRA,
)
async def import_world_book(
    request: Request,
    idempotency_key: IdempotencyKeyDep,
    world_book_service: WorldBookServiceDep,
) -> WorldBookImportResponse:
    """上传世界书 JSON 并入库（复用既有 ``idempotency`` 幂等表）。"""
    filename, content_type, content, form_name = await _read_multipart_upload(request)
    try:
        payload = world_book_service.import_book(
            filename=filename,
            content_type=content_type,
            content=content,
            name=form_name,
            idempotency_key=idempotency_key,
        )
    except WorldBookServiceError as exc:
        raise _api_error(exc) from exc
    return WorldBookImportResponse.model_validate(payload)


@router.post(
    "/validate",
    response_model=WorldBookValidateResponse,
    openapi_extra=_VALIDATE_OPENAPI_EXTRA,
)
def validate_world_book(
    body: WorldBookValidateBody,
    world_book_service: WorldBookServiceDep,
) -> WorldBookValidateResponse:
    """只读校验任意世界书 payload（返回结构错误/告警/预览；无副作用）。"""
    payload = world_book_service.validate_payload(body.payload)
    return WorldBookValidateResponse(
        valid=bool(payload["valid"]),
        errors=[
            WorldBookValidationErrorResponse.model_validate(item) for item in payload["errors"]
        ],
        warnings=[WorldBookWarningResponse.model_validate(item) for item in payload["warnings"]],
        preview=WorldBookValidatePreviewResponse.model_validate(payload["preview"]),
    )


# ---------------------------------------------------------------------------
# 列表 / 详情 / 软删
# ---------------------------------------------------------------------------
@router.get("", response_model=WorldBookListResponse)
def list_world_books(
    world_book_service: WorldBookServiceDep,
    query: Annotated[str | None, Query(max_length=MAX_QUERY_LENGTH)] = None,
    limit: Annotated[int, Query(ge=1, le=LIST_MAX_LIMIT)] = LIST_DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> WorldBookListResponse:
    """世界书列表：``{world_books, total}``；按 ``updated_at`` 倒序，``query`` 匹配 name。"""
    try:
        payload = world_book_service.list_world_books(query=query, limit=limit, offset=offset)
    except WorldBookServiceError as exc:
        raise _api_error(exc) from exc
    return WorldBookListResponse(
        world_books=[
            WorldBookSummaryResponse.model_validate(item) for item in payload["world_books"]
        ],
        total=int(payload["total"]),
    )


@router.get("/{world_book_id}", response_model=WorldBookDetailResponse)
def get_world_book(
    world_book_id: str,
    world_book_service: WorldBookServiceDep,
) -> WorldBookDetailResponse:
    """世界书详情：``{world_book, entries}``；未知/已删除 404。"""
    try:
        payload = world_book_service.get_detail(world_book_id)
    except WorldBookServiceError as exc:
        raise _api_error(exc) from exc
    return WorldBookDetailResponse(
        world_book=WorldBookDetailBodyResponse.model_validate(payload["world_book"]),
        entries=[WorldBookEntryResponse.model_validate(item) for item in payload["entries"]],
    )


@router.delete("/{world_book_id}", response_model=WorldBookDeleteResponse)
def delete_world_book(
    world_book_id: str,
    idempotency_key: IdempotencyKeyDep,
    world_book_service: WorldBookServiceDep,
) -> WorldBookDeleteResponse:
    """软删除世界书（幂等：重复删返回 ``deleted=true``；从未存在 404）。"""
    try:
        payload = world_book_service.delete_book(
            world_book_id=world_book_id, idempotency_key=idempotency_key
        )
    except WorldBookServiceError as exc:
        raise _api_error(exc) from exc
    return WorldBookDeleteResponse(
        world_book_id=str(payload["world_book_id"]), deleted=bool(payload["deleted"])
    )


__all__ = ["router"]
