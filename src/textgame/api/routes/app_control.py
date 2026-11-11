"""启动器控制面路由（冻结契约）：``GET /status``、``POST /heartbeat``、``POST /shutdown``。

只做 HTTP 映射与回环校验；状态机在 ``textgame.launcher_control.LauncherController``。

契约（前端/启动器按此实现，不得改名）：

- ``GET  /api/v1/app/status``：控制器存在 → ``launcher_mode=true``/``shutdown_supported=true``/
  ``idle_exit_seconds=控制器实际值``；无控制器（默认 ``uvicorn textgame.main:app``）→ 全 false/0；
- ``POST /api/v1/app/heartbeat``：200 ``{ok:true, idle_exit_seconds}``；无控制器 → 404 ``NOT_FOUND``；
- ``POST /api/v1/app/shutdown``：body ``{"confirm": true}``（``extra=forbid``，非 true → 422）→
  200 ``{ok:true, message:"游戏正在退出"}``；无控制器 → 404；非回环客户端 → 403；重复调用幂等 200。
  不要求 ``Idempotency-Key``（退出不是业务写操作，控制器自身幂等）。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from textgame import __version__
from textgame.api.errors import ApiError
from textgame.api.schemas import (
    AppHeartbeatResponse,
    AppShutdownBody,
    AppShutdownResponse,
    AppStatusResponse,
)
from textgame.domain.results import ErrorCode
from textgame.launcher_control import LauncherController

router = APIRouter(prefix="/api/v1/app", tags=["app-control"])

#: 允许调用 shutdown 的客户端主机：本机回环 + Starlette ``TestClient`` 默认 host。
_LOOPBACK_CLIENT_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})


def get_launcher_controller_or_none(request: Request) -> LauncherController | None:
    """返回组合根注入的 ``LauncherController``；非启动器模式（默认）为 ``None``。"""
    controller: LauncherController | None = getattr(request.app.state, "launcher_controller", None)
    return controller


LauncherControllerDep = Annotated[
    LauncherController | None, Depends(get_launcher_controller_or_none)
]


def _is_loopback_client(request: Request) -> bool:
    """请求是否来自本机回环（``request.client`` 缺失按非回环处理）。"""
    client = request.client
    if client is None:
        return False
    return client.host in _LOOPBACK_CLIENT_HOSTS


@router.get("/status", response_model=AppStatusResponse)
def get_app_status(controller: LauncherControllerDep) -> AppStatusResponse:
    """启动器状态：非启动器模式（无控制器）返回全 false/0 的稳定形状。"""
    if controller is None:
        return AppStatusResponse(
            launcher_mode=False,
            idle_exit_seconds=0,
            shutdown_supported=False,
            version=__version__,
        )
    return AppStatusResponse(
        launcher_mode=True,
        idle_exit_seconds=controller.idle_exit_seconds,
        shutdown_supported=True,
        version=__version__,
    )


@router.post("/heartbeat", response_model=AppHeartbeatResponse)
def post_app_heartbeat(controller: LauncherControllerDep) -> AppHeartbeatResponse:
    """上报一次心跳（刷新空闲计时）；无控制器 → 404 统一失败信封。"""
    if controller is None:
        raise ApiError(ErrorCode.NOT_FOUND, "启动器未启用：/api/v1/app/heartbeat 不可用")
    controller.touch()
    return AppHeartbeatResponse(ok=True, idle_exit_seconds=controller.idle_exit_seconds)


@router.post("/shutdown", response_model=AppShutdownResponse)
def post_app_shutdown(
    body: AppShutdownBody,
    request: Request,
    controller: LauncherControllerDep,
) -> AppShutdownResponse:
    """请求优雅退出：无控制器 404、非回环 403、``confirm`` 非 true 由请求校验给 422。

    ``body.confirm`` 由 Pydantic 校验为字面量 ``true``；本函数只做存在性/来源/幂等处理。
    重复调用仍返回 200 相同响应（``request_shutdown`` 幂等）。
    """
    del body  # 已由 AppShutdownBody 校验 confirm is True；语义上无其它字段
    if controller is None:
        raise ApiError(ErrorCode.NOT_FOUND, "启动器未启用：/api/v1/app/shutdown 不可用")
    if not _is_loopback_client(request):
        raise ApiError(ErrorCode.NOT_ALLOWED, "仅允许本机回环客户端退出游戏")
    controller.request_shutdown()
    return AppShutdownResponse(ok=True, message="游戏正在退出")


__all__ = ["router"]
