"""人物卡库路由（第 6 章 §6.7、第 23 章 §23.4/§23.5）：只做 HTTP 映射，业务在 ``CardService``。

M6 后端前置 character-cards 的 **8 个**端点（``generate`` 为 M6-028 后端前置，本批交付）：

=====================================  ======  ==========================================
端点                                   成功    说明
=====================================  ======  ==========================================
GET  /api/v1/character-cards/schema    200     JSON Schema 对象本身（draft 2020-12）
GET  /api/v1/character-cards           200     ``{cards, total}``（?query=&limit=&offset=）
GET  /api/v1/character-cards/{id}      200     ``{card}``
POST /api/v1/character-cards/import    201     multipart 上传 JSON（≤64KB，必带幂等键）
POST /api/v1/character-cards/validate  200     ``{valid, errors}``（只读无副作用）
POST /api/v1/character-cards/generate  200     ``{draft, validation, generated_by}``（草稿不保存）
POST /api/v1/character-cards           201     ``{card_id, card}``（保存已校验卡片，必带幂等键）
DELETE /api/v1/character-cards/{id}    204     软删除（必带幂等键）
=====================================  ======  ==========================================

失败统一为 4xx/5xx + ``{ok:false, error_code, message, violations?}``（第 6 章 §6.7）；
文件过大走 ``INVALID_INPUT`` + HTTP 413 特例；生成限流走 ``OUT_OF_RANGE`` + HTTP 429 +
``Retry-After``；``CardService`` 的异常由本模块翻译为信封。
"""

from __future__ import annotations

from email import policy
from email.parser import BytesParser
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from textgame.api.deps import require_idempotency_key
from textgame.api.errors import ApiError, error_body
from textgame.api.schemas import (
    CharacterCardDetailResponse,
    CharacterCardImportResponse,
    CharacterCardListItemResponse,
    CharacterCardListResponse,
    CharacterCardSaveResponse,
    CharacterCardValidateResponse,
    CharacterCardValidationErrorResponse,
    CharacterCardWarningResponse,
)
from textgame.domain.character_card import MAX_BRIEF_CHARS, CharacterCard
from textgame.domain.results import ErrorCode
from textgame.services.card_service import (
    LIST_DEFAULT_LIMIT,
    LIST_MAX_LIMIT,
    MAX_QUERY_LENGTH,
    MAX_UPLOAD_BYTES,
    CardService,
    CardServiceError,
)

router = APIRouter(prefix="/api/v1/character-cards", tags=["character-cards"])


class CardGenerateBody(BaseModel):
    """``POST /api/v1/character-cards/generate`` 请求体（第 23 章 §23.6）。

    ``brief`` 为待理解的数据（≤1000 字符）；``options`` 仅参与生成/幂等，不执行其中的指令。
    """

    model_config = ConfigDict(extra="forbid")

    brief: str = Field(min_length=1, max_length=MAX_BRIEF_CHARS)
    schema_version: str = "1.0"
    options: dict[str, Any] = Field(default_factory=dict)


class CardGenerateValidation(BaseModel):
    """生成草稿的服务端校验结果（与 import 同一校验管线；``warnings`` 含 ``TEMPLATE_FALLBACK``）。"""

    model_config = ConfigDict(extra="forbid")

    valid: bool
    errors: list[CharacterCardValidationErrorResponse] = Field(default_factory=list)
    warnings: list[CharacterCardWarningResponse] = Field(default_factory=list)


class CardGenerateResponse(BaseModel):
    """``POST /api/v1/character-cards/generate`` 200 响应 ``{draft, validation, generated_by}``。"""

    model_config = ConfigDict(extra="forbid")

    draft: CharacterCard
    validation: CardGenerateValidation
    generated_by: str | None = None


def get_card_service(request: Request) -> CardService:
    """返回组合根注入的 ``CardService``；缺失视为组合根装配错误（500）。"""
    card_service: CardService | None = getattr(request.app.state, "card_service", None)
    if card_service is None:
        raise ApiError(ErrorCode.INTERNAL, "服务未完成人物卡服务装配")
    return card_service


CardServiceDep = Annotated[CardService, Depends(get_card_service)]
IdempotencyKeyDep = Annotated[str, Depends(require_idempotency_key)]


def _api_error(exc: CardServiceError) -> ApiError:
    """``CardServiceError`` → 统一失败信封（含 413/403/404/409/422 映射）。"""
    return ApiError(
        exc.error_code,
        exc.message,
        violations=exc.violations,
        status_override=exc.http_status,
    )


#: 生成端点限流的会话标识头（R-09 起不再参与配额；保留常量供兼容文档/测试引用）。
_SESSION_HEADER = "X-Session-Id"


def _client_key(request: Request) -> str:
    """限流主体：仅客户端 IP（R-09：``X-Session-Id`` 可由客户端轮换，不能参与配额）。"""
    host = request.client.host if request.client is not None else "unknown"
    return host


async def _read_limited_body(request: Request) -> bytes:
    """读取 JSON 请求体并做 ≤64KB 双重校验（Content-Length 预检 + 实际长度）。"""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
        raise ApiError(
            ErrorCode.INVALID_INPUT,
            f"人物卡 JSON 超过 {MAX_UPLOAD_BYTES // 1024}KB 上限",
            status_override=413,
        )
    body = await request.body()
    if len(body) > MAX_UPLOAD_BYTES:
        raise ApiError(
            ErrorCode.INVALID_INPUT,
            f"人物卡 JSON 超过 {MAX_UPLOAD_BYTES // 1024}KB 上限",
            status_override=413,
        )
    return body


#: multipart 报文头/boundary 的额外字节预算（文件本体上限之外）。
_MULTIPART_OVERHEAD_BYTES = 16 * 1024


def _extract_multipart_file(
    content_type_header: str, body: bytes
) -> tuple[str | None, str | None, bytes]:
    """从 multipart 报文体中取出 ``file`` 部件 → ``(filename, part_content_type, content)``。

    项目禁用新增运行时依赖（第 19 章），因此不复用需要 ``python-multipart`` 的
    ``UploadFile``；这里用标准库 ``email`` 解析 multipart（浏览器/httpx 的编码均兼容）。
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
    parts = message.iter_parts() if message.is_multipart() else ()
    for part in parts:
        if part.get_param("name", header="content-disposition") != "file":
            continue
        filename = part.get_filename()
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes):
            raise ApiError(ErrorCode.INVALID_INPUT, "multipart 的 file 部件内容非法")
        return filename, part.get_content_type(), payload
    raise ApiError(ErrorCode.INVALID_INPUT, "multipart 缺少 file 字段")


async def _read_multipart_file(request: Request) -> tuple[str | None, str | None, bytes]:
    """读取 ``multipart/form-data`` 上传并取出 ``file`` 部件（总体积 ≤64KB + 报文头预算）。"""
    content_type = request.headers.get("content-type", "")
    if not content_type.lower().startswith("multipart/form-data"):
        raise ApiError(ErrorCode.INVALID_INPUT, "只接受 multipart/form-data 上传")
    limit = MAX_UPLOAD_BYTES + _MULTIPART_OVERHEAD_BYTES
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise ApiError(
            ErrorCode.INVALID_INPUT,
            f"人物卡 JSON 超过 {MAX_UPLOAD_BYTES // 1024}KB 上限",
            status_override=413,
        )
    body = await request.body()
    if len(body) > limit:
        raise ApiError(
            ErrorCode.INVALID_INPUT,
            f"人物卡 JSON 超过 {MAX_UPLOAD_BYTES // 1024}KB 上限",
            status_override=413,
        )
    return _extract_multipart_file(content_type, body)


# ---------------------------------------------------------------------------
# 只读：Schema / 列表 / 详情
# ---------------------------------------------------------------------------
#: OpenAPI 请求体声明：``import`` 是 multipart（文件部件），无 ``python-multipart`` 依赖时
#: FastAPI 无法从签名推断，故手工补 ``requestBody``，供前端契约测试/类型生成读取。
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
                            "description": "人物卡 JSON 文件（≤64KB，仅 .json）",
                        }
                    },
                }
            }
        },
    }
}
#: 保存端点：body 就是已校验的 ``CharacterCard``。
_SAVE_OPENAPI_EXTRA: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/CharacterCard"}}},
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
                    "description": "待校验的人物卡 JSON（允许 Schema 不符）",
                }
            }
        },
    }
}


@router.get("/schema", response_model=None)
def get_character_card_schema(card_service: CardServiceDep) -> dict[str, Any]:
    """返回人物卡 JSON Schema 对象本身（不套信封；前端 Ajv2020 直接复用）。"""
    return card_service.get_schema()


@router.get("", response_model=CharacterCardListResponse)
def list_character_cards(
    card_service: CardServiceDep,
    query: Annotated[str | None, Query(max_length=MAX_QUERY_LENGTH)] = None,
    limit: Annotated[int, Query(ge=1, le=LIST_MAX_LIMIT)] = LIST_DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CharacterCardListResponse:
    """卡片库列表：``{cards, total}``；按 ``updated_at`` 倒序，``query`` 匹配 name。"""
    try:
        payload = card_service.list_cards(query=query, limit=limit, offset=offset)
    except CardServiceError as exc:
        raise _api_error(exc) from exc
    return CharacterCardListResponse(
        cards=[CharacterCardListItemResponse(**item) for item in payload["cards"]],
        total=payload["total"],
    )


@router.get("/{card_id}", response_model=CharacterCardDetailResponse)
def get_character_card(card_id: str, card_service: CardServiceDep) -> CharacterCardDetailResponse:
    """卡片详情 ``{card}``；未知/已删除 404，ID 格式非法 422。"""
    try:
        payload = card_service.get_card(card_id)
    except CardServiceError as exc:
        raise _api_error(exc) from exc
    return CharacterCardDetailResponse(**payload)


# ---------------------------------------------------------------------------
# 写：导入 / 校验（只读）/ 保存 / 删除
# ---------------------------------------------------------------------------
@router.post(
    "/import",
    status_code=201,
    response_model=CharacterCardImportResponse,
    openapi_extra=_IMPORT_OPENAPI_EXTRA,
)
async def import_character_card(
    request: Request,
    card_service: CardServiceDep,
    idempotency_key: IdempotencyKeyDep,
) -> CharacterCardImportResponse:
    """multipart 上传 JSON 并入库 → ``{card_id, card, warnings}``（source 强制 upload）。"""
    filename, content_type, content = await _read_multipart_file(request)
    try:
        payload = card_service.import_card(
            filename=filename,
            content_type=content_type,
            content=content,
            idempotency_key=idempotency_key,
        )
    except CardServiceError as exc:
        raise _api_error(exc) from exc
    return CharacterCardImportResponse(**payload)


@router.post(
    "/validate",
    response_model=CharacterCardValidateResponse,
    openapi_extra=_VALIDATE_OPENAPI_EXTRA,
)
async def validate_character_card(
    request: Request,
    card_service: CardServiceDep,
) -> CharacterCardValidateResponse:
    """查询式校验：200 ``{valid, errors}``（只读无副作用）；非 JSON/超大才 4xx。"""
    content = await _read_limited_body(request)
    try:
        payload = card_service.validate_bytes(content)
    except CardServiceError as exc:
        raise _api_error(exc) from exc
    return CharacterCardValidateResponse(**payload)


@router.post("/generate", response_model=CardGenerateResponse)
def generate_character_card(
    request: Request,
    body: CardGenerateBody,
    card_service: CardServiceDep,
    idempotency_key: IdempotencyKeyDep,
) -> CardGenerateResponse | JSONResponse:
    """按 ``brief`` 生成人物卡草稿（200 ``{draft, validation, generated_by}``，不保存/不建局）。

    LLM 可用时经 ``LLMClient``（``task="character_card_generate"``、STRONG、``max_repair=1``）；
    默认离线/LLM 不可用 → 确定性模板兜底（``warnings`` 含 ``TEMPLATE_FALLBACK``、
    ``source="manual"``、``generated_by=null``）。限流 5 次/分钟（IP+session 进程内滑窗）→
    HTTP 429 ``OUT_OF_RANGE`` + ``Retry-After`` 头；同键同参复用首结果、同键异参 409。
    """
    try:
        payload = card_service.generate_card(
            brief=body.brief,
            schema_version=body.schema_version,
            options=body.options,
            idempotency_key=idempotency_key,
            client_key=_client_key(request),
        )
    except CardServiceError as exc:
        if exc.http_status == 429:
            return JSONResponse(
                status_code=429,
                content=error_body(exc.error_code, exc.message),
                headers={"Retry-After": str(exc.retry_after or 1)},
            )
        raise _api_error(exc) from exc
    return CardGenerateResponse(**payload)


@router.post(
    "",
    status_code=201,
    response_model=CharacterCardSaveResponse,
    openapi_extra=_SAVE_OPENAPI_EXTRA,
)
async def save_character_card(
    request: Request,
    card_service: CardServiceDep,
    idempotency_key: IdempotencyKeyDep,
) -> CharacterCardSaveResponse:
    """保存已校验卡片 → ``{card_id, card}``；服务端强制 ``card_id`` 与 ``source`` 判定。"""
    content = await _read_limited_body(request)
    try:
        payload = card_service.save_card(content=content, idempotency_key=idempotency_key)
    except CardServiceError as exc:
        raise _api_error(exc) from exc
    return CharacterCardSaveResponse(**payload)


@router.delete("/{card_id}", status_code=204)
def delete_character_card(
    card_id: str,
    card_service: CardServiceDep,
    idempotency_key: IdempotencyKeyDep,
) -> Response:
    """软删除卡片 → 204 无 body；同键重放仍 204，未知/已删除 404。"""
    try:
        card_service.delete_card(card_id=card_id, idempotency_key=idempotency_key)
    except CardServiceError as exc:
        raise _api_error(exc) from exc
    return Response(status_code=204)


__all__ = ["router"]
