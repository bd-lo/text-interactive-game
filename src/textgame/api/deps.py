"""接入层依赖 (第 6 章 6.2/6.7)：从 ``app.state`` 取组合根注入的编排/查询服务，
以及校验 ``Idempotency-Key`` 请求头。

组合根（``main.py``/测试 fixture）负责把 ``orchestrator`` 与 ``query_service`` 挂到
``app.state`` 上，路由只通过本模块依赖取用，避免路由模块持有全局状态。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Header, Request

from textgame.api.errors import ApiError
from textgame.domain.results import ErrorCode
from textgame.orchestration.turn_orchestrator import TurnOrchestrator
from textgame.services.audit_service import AuditService
from textgame.services.query_service import QueryService

KEY_MIN_LENGTH = 8
KEY_MAX_LENGTH = 128


def get_orchestrator(request: Request) -> TurnOrchestrator:
    """返回组合根注入的 ``TurnOrchestrator``；缺失视为组合根装配错误。"""
    orchestrator: TurnOrchestrator | None = getattr(request.app.state, "orchestrator", None)
    if orchestrator is None:
        raise ApiError(ErrorCode.INTERNAL, "服务未完成编排器装配")
    return orchestrator


def get_query_service(request: Request) -> QueryService:
    """返回组合根注入的 ``QueryService``；缺失视为组合根装配错误。"""
    query_service: QueryService | None = getattr(request.app.state, "query_service", None)
    if query_service is None:
        raise ApiError(ErrorCode.INTERNAL, "服务未完成查询服务装配")
    return query_service


def get_audit_service(request: Request) -> AuditService:
    """返回组合根注入的 ``AuditService``；缺失视为组合根装配错误。"""
    audit_service: AuditService | None = getattr(request.app.state, "audit_service", None)
    if audit_service is None:
        raise ApiError(ErrorCode.INTERNAL, "服务未完成审计服务装配")
    return audit_service


def require_idempotency_key(
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str:
    """校验 ``Idempotency-Key``：必须存在且长度 8..128，否则 422 ``INVALID_INPUT``。"""
    if idempotency_key is None or not idempotency_key.strip():
        raise ApiError(ErrorCode.INVALID_INPUT, "缺少 Idempotency-Key 请求头")
    if not (KEY_MIN_LENGTH <= len(idempotency_key) <= KEY_MAX_LENGTH):
        raise ApiError(
            ErrorCode.INVALID_INPUT,
            f"Idempotency-Key 长度必须在 {KEY_MIN_LENGTH}..{KEY_MAX_LENGTH} 之间",
        )
    return idempotency_key


__all__ = [
    "KEY_MAX_LENGTH",
    "KEY_MIN_LENGTH",
    "get_audit_service",
    "get_orchestrator",
    "get_query_service",
    "require_idempotency_key",
]
