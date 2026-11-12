"""场景库路由（设计契约 ``docs/design/user-scenario-modules.md`` §3.4/§6）：只做 HTTP 映射。

端点一览（6 个；前 5 个为 v1 冻结契约，``export`` 为 032 分享加法）：

========================================  ======  ==============================================
端点                                      成功    说明
========================================  ======  ==============================================
POST /api/v1/scenarios/validate           200     ``{ok, errors, warnings, summary}``（只读预检）
POST /api/v1/scenarios                    201     ``{scenario_id, slug, title, summary, warnings}``
GET  /api/v1/scenarios                    200     ``{scenarios, total}``（``?q=&limit=&offset=``）
GET  /api/v1/scenarios/{id}               200     详情（含原始定义对象、summary、warnings）
GET  /api/v1/scenarios/{id}/export        200     单文件 JSON 附件（可直接再导入；只读、零写入）
DELETE /api/v1/scenarios/{id}             204     软删（不影响已开局存档；重复删除 404）
========================================  ======  ==============================================

请求体口径：

- ``validate`` 与 ``import`` 的 body 都是**场景 JSON 原文**（``application/json``）；
  ``import`` 额外**兼容 multipart/form-data**（``file`` 部件，前端现状），两种形态都要求
  ≤256KB、UTF-8、JSON 对象；
- ``validate`` 无需幂等键（只读、不写库）；``import`` 必带 ``Idempotency-Key``；
- ``export`` 是只读 GET：响应体就是场景定义 JSON 原文（可直接作为 ``import`` 的 body），
  文件名取 ``<slug>.json``（ASCII 安全），无服务器路径/凭据/库内字段。

失败统一为 4xx/5xx + ``{ok:false, error_code, message, violations?}``（第 6 章 §6.7）：
场景定义非法 422（``violations`` 为 ``[{path,message}]``）；导入超 256KB 用
``INVALID_INPUT`` + HTTP 413 特例（与人物卡/世界书导入同口径）；未知/已删除 404；
幂等同键异参 409。场景库**不参与事件溯源**：本模块只做资料库 CRUD + 只读校验/导出。
"""

from __future__ import annotations

from email import policy
from email.parser import BytesParser
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response

from textgame.api.deps import get_scenario_service, require_idempotency_key
from textgame.api.errors import ApiError
from textgame.api.schemas import (
    ScenarioDetailResponse,
    ScenarioImportResponse,
    ScenarioListResponse,
    ScenarioStatsResponse,
    ScenarioSummaryResponse,
    ScenarioValidateResponse,
    ScenarioValidationErrorResponse,
    ScenarioWarningResponse,
)
from textgame.domain.results import ErrorCode
from textgame.domain.user_scenario import MAX_DEFINITION_BYTES
from textgame.services.scenario_service import (
    LIST_DEFAULT_LIMIT,
    LIST_MAX_LIMIT,
    MAX_QUERY_LENGTH,
    SUMMARY_KEYS,
    ScenarioService,
    ScenarioServiceError,
)

router = APIRouter(prefix="/api/v1/scenarios", tags=["scenarios"])

#: multipart 报文头/boundary 的额外字节预算（文件本体上限之外）。
_MULTIPART_OVERHEAD_BYTES = 16 * 1024
#: 导入体积超限时的错误消息（与 service 内 413 口径一致）。
_TOO_LARGE_MESSAGE = f"场景 JSON 超过 {MAX_DEFINITION_BYTES // 1024}KB 上限"

ScenarioServiceDep = Annotated[ScenarioService, Depends(get_scenario_service)]
IdempotencyKeyDep = Annotated[str, Depends(require_idempotency_key)]


def _api_error(exc: ScenarioServiceError) -> ApiError:
    """``ScenarioServiceError`` → 统一失败信封（含 413/404/409/422 映射）。"""
    return ApiError(
        exc.error_code,
        exc.message,
        violations=exc.violations,
        status_override=exc.http_status,
    )


def _too_large() -> ApiError:
    """导入体积超限：``INVALID_INPUT`` + HTTP 413（契约特例）。"""
    return ApiError(ErrorCode.INVALID_INPUT, _TOO_LARGE_MESSAGE, status_override=413)


def _stats(summary: Any) -> ScenarioStatsResponse:
    """把 summary 补齐/裁剪成固定 6 键响应模型（校验失败时领域层可能给空 dict）。"""
    values = summary if isinstance(summary, dict) else {}
    return ScenarioStatsResponse(**{key: int(values.get(key, 0)) for key in SUMMARY_KEYS})


def _extract_multipart_file(content_type_header: str, body: bytes) -> bytes:
    """从 multipart 报文体取出 ``file`` 部件内容（前端兼容；仅标准库，不新增依赖）。

    缺少 ``file`` 部件 / 部件内容非法 → 422 ``INVALID_INPUT``。
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
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes):
            raise ApiError(ErrorCode.INVALID_INPUT, "multipart 的 file 部件内容非法")
        return payload
    raise ApiError(ErrorCode.INVALID_INPUT, "multipart 缺少 file 字段")


async def _read_import_body(request: Request) -> bytes:
    """读取导入请求体：JSON 原文或 multipart ``file`` 部件，均要求 ≤256KB（超限 413）。"""
    content_type = request.headers.get("content-type", "")
    declared = request.headers.get("content-length")
    if (
        declared is not None
        and declared.isdigit()
        and int(declared) > MAX_DEFINITION_BYTES + _MULTIPART_OVERHEAD_BYTES
    ):
        raise _too_large()
    body = await request.body()
    if len(body) > MAX_DEFINITION_BYTES + _MULTIPART_OVERHEAD_BYTES:
        raise _too_large()
    raw = (
        _extract_multipart_file(content_type, body)
        if content_type.lower().startswith("multipart/form-data")
        else body
    )
    if len(raw) > MAX_DEFINITION_BYTES:
        raise _too_large()
    return raw


#: 校验端点：body 就是场景 JSON 原文（故意允许 Schema 不符，以便逐字段返回 errors）。
_VALIDATE_OPENAPI_EXTRA: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "description": "场景 JSON 原文（允许 Schema 不符，逐字段返回 errors）",
                }
            }
        },
    }
}
#: 导入端点：body 是场景 JSON 原文（运行时额外兼容 multipart file 部件）。
_IMPORT_OPENAPI_EXTRA: dict[str, Any] = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "description": "场景 JSON 原文（≤256KB；也兼容 multipart/form-data 的 file 部件）",
                }
            },
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {
                        "file": {
                            "type": "string",
                            "format": "binary",
                            "description": "场景 JSON 文件（≤256KB，UTF-8）",
                        }
                    },
                }
            },
        },
    }
}


# ---------------------------------------------------------------------------
# 只读：校验 / 列表 / 详情
# ---------------------------------------------------------------------------
@router.post(
    "/validate",
    response_model=ScenarioValidateResponse,
    openapi_extra=_VALIDATE_OPENAPI_EXTRA,
)
async def validate_scenario(
    request: Request,
    scenario_service: ScenarioServiceDep,
) -> ScenarioValidateResponse:
    """只读预检场景 JSON 原文：200 ``{ok, errors, warnings, summary}``（无副作用）。"""
    raw = await request.body()
    parsed = scenario_service.validate(raw)
    return ScenarioValidateResponse(
        ok=parsed.ok,
        errors=[
            ScenarioValidationErrorResponse(path=issue.path, message=issue.message)
            for issue in parsed.errors
        ],
        warnings=[
            ScenarioWarningResponse(path=issue.path, message=issue.message)
            for issue in parsed.warnings
        ],
        summary=_stats(parsed.summary),
    )


@router.get("", response_model=ScenarioListResponse)
def list_scenarios(
    scenario_service: ScenarioServiceDep,
    q: Annotated[str | None, Query(max_length=MAX_QUERY_LENGTH)] = None,
    limit: Annotated[int, Query(ge=1, le=LIST_MAX_LIMIT)] = LIST_DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ScenarioListResponse:
    """场景库列表：``{scenarios, total}``；按 ``created_at`` 倒序，``q`` 搜索 title/author/slug。"""
    try:
        payload = scenario_service.list_scenarios(query=q, limit=limit, offset=offset)
    except ScenarioServiceError as exc:
        raise _api_error(exc) from exc
    return ScenarioListResponse(
        scenarios=[ScenarioSummaryResponse.model_validate(item) for item in payload["scenarios"]],
        total=int(payload["total"]),
    )


@router.get("/{scenario_id}", response_model=ScenarioDetailResponse)
def get_scenario(
    scenario_id: str,
    scenario_service: ScenarioServiceDep,
) -> ScenarioDetailResponse:
    """场景详情（含原始定义对象、summary、warnings）；未知/已删除 404。"""
    try:
        payload = scenario_service.get_scenario(scenario_id)
    except ScenarioServiceError as exc:
        raise _api_error(exc) from exc
    return ScenarioDetailResponse.model_validate(payload)


@router.get("/{scenario_id}/export")
def export_scenario(
    scenario_id: str,
    scenario_service: ScenarioServiceDep,
) -> Response:
    """导出场景为可直接再导入的单文件 JSON（``attachment`` 下载；只读、零写入）。

    响应体 = 入库时校验通过的场景定义原文（v1/v2 均保留 ``schema_version`` 与全部字段，
    **不含** summary/时间戳/服务器路径/凭据等库内字段）；同一文件可直接作为
    ``POST /api/v1/scenarios`` 的 body 再导入（分享 = 文件交换，不托管用户内容）。
    文件名取 ``<slug>.json``（ASCII 安全）；未知/已软删 404。
    """
    try:
        exported = scenario_service.export_scenario(scenario_id)
    except ScenarioServiceError as exc:
        raise _api_error(exc) from exc
    return Response(
        content=exported.payload,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{exported.filename}"'},
    )


# ---------------------------------------------------------------------------
# 写：导入（幂等）/ 软删
# ---------------------------------------------------------------------------
@router.post(
    "",
    status_code=201,
    response_model=ScenarioImportResponse,
    openapi_extra=_IMPORT_OPENAPI_EXTRA,
)
async def import_scenario(
    request: Request,
    idempotency_key: IdempotencyKeyDep,
    scenario_service: ScenarioServiceDep,
) -> ScenarioImportResponse:
    """导入场景 JSON 原文 → ``{scenario_id, slug, title, summary, warnings}``。

    同键同参幂等回放 201（零写入）、同键异参 409；定义非法 422；超过 256KB 413。
    """
    raw = await _read_import_body(request)
    try:
        payload = scenario_service.import_scenario(raw, idempotency_key=idempotency_key)
    except ScenarioServiceError as exc:
        raise _api_error(exc) from exc
    return ScenarioImportResponse(
        scenario_id=str(payload["scenario_id"]),
        slug=str(payload["slug"]),
        title=str(payload["title"]),
        summary=_stats(payload["summary"]),
        warnings=[ScenarioWarningResponse.model_validate(item) for item in payload["warnings"]],
    )


@router.delete("/{scenario_id}", status_code=204)
def delete_scenario(
    scenario_id: str,
    scenario_service: ScenarioServiceDep,
) -> Response:
    """软删除场景 → 204 无 body；未知/已删除 404（不影响已开局存档与 runtime 索引）。"""
    try:
        scenario_service.delete_scenario(scenario_id)
    except ScenarioServiceError as exc:
        raise _api_error(exc) from exc
    return Response(status_code=204)


__all__ = ["router"]
