"""接入层错误码 → HTTP 映射与统一失败信封 (第 6 章 6.7、第 21 章 21.3)。

成功响应直接返回业务 payload；失败响应统一为
``{"ok": false, "error_code": <ErrorCode>, "message": "...", "violations": [...]?}``。
``POST /api/v1/turns`` 的业务失败是例外：仍 HTTP 200 + ``TurnResult(ok=false)``（契约 §5.4）。
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from textgame.domain.results import ErrorCode

#: 契约 §4.6/§5.3：错误码 → HTTP 状态码（429 配额/限流留 M5）。
_HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.INVALID_INPUT: 422,
    ErrorCode.SCHEMA_VIOLATION: 422,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.NOT_ALLOWED: 403,
    ErrorCode.CONFLICT: 409,
    ErrorCode.LLM_UNAVAILABLE: 503,
    ErrorCode.INTERNAL: 500,
    ErrorCode.OUT_OF_RANGE: 422,
}


class ApiError(Exception):
    """接入层可映射异常：携带稳定错误码、消息与可选字段级 violations。

    ``status_override`` 用于契约里「错误码相同但状态码不同」的特例（文件过大：
    ``INVALID_INPUT`` 却必须返回 HTTP 413，第 6 章 §6.7）；缺省仍按 ``_HTTP_STATUS`` 映射。
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        violations: list[dict[str, Any]] | None = None,
        status_override: int | None = None,
    ) -> None:
        """构造失败信封所需的元数据。"""
        super().__init__(message)
        self.code = code
        self.message = message
        self.violations = violations
        self._status_override = status_override

    @property
    def status_code(self) -> int:
        """该错误码对应的 HTTP 状态码（``status_override`` 优先）。"""
        if self._status_override is not None:
            return self._status_override
        return _HTTP_STATUS[self.code]


def error_body(
    code: ErrorCode,
    message: str,
    violations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """构造统一失败信封；``violations`` 为 ``None`` 时不出现该字段。"""
    body: dict[str, Any] = {
        "ok": False,
        "error_code": code.value,
        "message": message,
    }
    if violations is not None:
        body["violations"] = violations
    return body


def _validation_violations(exc: RequestValidationError) -> list[dict[str, Any]]:
    """把 Pydantic 校验错误裁剪成可 JSON 序列化的 ``{loc, type, msg}``。"""
    violations: list[dict[str, Any]] = []
    for error in exc.errors():
        violations.append(
            {
                "loc": [str(part) for part in error.get("loc", ())],
                "type": str(error.get("type", "")),
                "msg": str(error.get("msg", "")),
            }
        )
    return violations


def install_error_handlers(app: FastAPI) -> None:
    """注册 ``ApiError`` 与请求校验错误处理器（契约 §4.6）。"""

    @app.exception_handler(ApiError)
    async def _handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.code, exc.message, exc.violations),
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=_HTTP_STATUS[ErrorCode.INVALID_INPUT],
            content=error_body(
                ErrorCode.INVALID_INPUT,
                "请求参数校验失败",
                _validation_violations(exc),
            ),
        )

    @app.exception_handler(ValueError)
    async def _handle_value_error(request: Request, exc: ValueError) -> JSONResponse:
        """存档状态/领域不变量校验失败按 ``INTERNAL`` 信封返回（第 14 章回放不一致不可降级）。

        先于本处理器注册的 ``RequestValidationError`` 有更精确的处理器，不会被这里拦截；
        此处只兜住仓储/投影抛出的 ``ValueError``（损坏存档、快照哈希不符等），避免把
        内部异常以 text/plain 500 暴露给客户端。
        """
        message = "服务内部错误：存档状态校验失败，请联系运维审计"
        del exc  # 不回显内部细节（第 15 章：不向客户端暴露实现信息）
        return JSONResponse(
            status_code=_HTTP_STATUS[ErrorCode.INTERNAL],
            content=error_body(ErrorCode.INTERNAL, message),
        )


__all__ = ["ApiError", "error_body", "install_error_handlers"]
