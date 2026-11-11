"""启动器控制面（launcher control）：**无框架依赖**的心跳 / 关机 / 空闲判定状态机。

用途：普通玩家用 ``python -m textgame.launcher`` 启动本机游戏后，网页前端通过
``/api/v1/app/status|heartbeat|shutdown`` 三个端点显示启动器状态、上报心跳、请求退出。
本模块只维护状态与定时器，不 import fastapi/uvicorn（接入层见 ``api.routes.app_control``，
进程入口见 ``textgame.launcher``），因此可以单独单测（``now`` 可注入）。

职责：

- :meth:`LauncherController.touch`：记录一次心跳时间（前端轮询调用）；
- :meth:`LauncherController.request_shutdown`：幂等请求退出；绑定 uvicorn ``Server`` 后
  用 **0.2s 延迟 timer** 置 ``server.should_exit=True``（延迟让 HTTP 响应先发出，uvicorn
  再做优雅收尾）；未绑定 server 时只置标志；
- :meth:`LauncherController.idle_exit_due`：空闲判定——**仅当收到过至少 1 次心跳**且距最近
  心跳超过 ``idle_exit_seconds`` 秒才算超时（``0`` = 关闭空闲退出），供 launcher 的
  monitor daemon 线程轮询。

线程安全：所有读写都在同一把锁内；``request_shutdown`` 的 timer 线程只做一次
``should_exit = True`` 赋值，重复调用不会重复计时（幂等）。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Final, Protocol

__all__ = [
    "DEFAULT_IDLE_EXIT_SECONDS",
    "LAUNCHER_SHUTDOWN_DELAY_SECONDS",
    "SHUTDOWN_REASON_IDLE",
    "SHUTDOWN_REASON_SHUTDOWN",
    "LauncherController",
    "UvicornServerLike",
]

#: 空闲自动退出默认阈值（秒）；``0`` = 关闭（契约：默认 300 = 5 分钟）。
DEFAULT_IDLE_EXIT_SECONDS: Final[int] = 300

#: 请求退出后延迟多久置 ``server.should_exit``（让 shutdown 的 HTTP 200 先返回）。
LAUNCHER_SHUTDOWN_DELAY_SECONDS: Final[float] = 0.2

#: 退出原因：前端 POST /shutdown 触发。
SHUTDOWN_REASON_SHUTDOWN: Final[str] = "shutdown"
#: 退出原因：空闲 monitor 触发（超过阈值无心跳）。
SHUTDOWN_REASON_IDLE: Final[str] = "idle"


class UvicornServerLike(Protocol):
    """uvicorn ``Server`` 的最小结构协议：只需要可写的 ``should_exit``。

    用结构协议而不是 import uvicorn，保证控制面模块无框架依赖、单测可注入替身。
    """

    should_exit: bool


class LauncherController:
    """启动器控制面状态机：心跳时间、退出请求、空闲判定与 uvicorn 停机桥接。

    生命周期：launcher 进程创建控制器 → 注入 ``create_app(launcher_controller=...)`` →
    绑定 ``uvicorn.Server`` → 前端轮询心跳 / 请求退出；进程退出时控制器随之销毁。
    """

    def __init__(
        self,
        *,
        idle_exit_seconds: int = DEFAULT_IDLE_EXIT_SECONDS,
        now: Callable[[], float] | None = None,
    ) -> None:
        """配置空闲阈值与时钟（``now`` 注入用于单测；默认 ``time.monotonic``）。

        ``idle_exit_seconds`` 必须 ≥0（0 = 关闭空闲退出）；负数属装配错误，直接抛
        ``ValueError``，避免把非法阈值带进 monitor 线程。
        """
        if idle_exit_seconds < 0:
            raise ValueError(f"idle_exit_seconds must be >= 0, got {idle_exit_seconds}")
        self._now: Callable[[], float] = now if now is not None else time.monotonic
        self._lock = threading.Lock()
        self._idle_exit_seconds = idle_exit_seconds
        self._started_at = self._now()
        self._last_heartbeat: float | None = None
        self._shutdown_requested = False
        self._shutdown_reason: str | None = None
        self._server: UvicornServerLike | None = None
        self._exit_timer: threading.Timer | None = None

    # ------------------------------------------------------------------
    # 只读状态
    # ------------------------------------------------------------------
    @property
    def idle_exit_seconds(self) -> int:
        """空闲自动退出阈值（秒；``0`` = 关闭）。"""
        return self._idle_exit_seconds

    @property
    def started_at(self) -> float:
        """控制器创建时间（``now()`` 口径；启动诊断用）。"""
        return self._started_at

    @property
    def last_heartbeat(self) -> float | None:
        """最近一次心跳时间（``now()`` 口径）；从未收到心跳为 ``None``。"""
        with self._lock:
            return self._last_heartbeat

    @property
    def shutdown_requested(self) -> bool:
        """是否已请求退出（首次请求后恒为 ``True``）。"""
        with self._lock:
            return self._shutdown_requested

    @property
    def shutdown_reason(self) -> str | None:
        """退出原因：``"shutdown"`` / ``"idle"``；未请求退出为 ``None``。"""
        with self._lock:
            return self._shutdown_reason

    # ------------------------------------------------------------------
    # 心跳 / 空闲判定
    # ------------------------------------------------------------------
    def touch(self) -> None:
        """记录一次心跳（前端轮询 ``POST /api/v1/app/heartbeat`` 时调用）。"""
        with self._lock:
            self._last_heartbeat = self._now()

    def seconds_since_heartbeat(self) -> float | None:
        """距最近心跳的秒数；从未收到心跳返回 ``None``（诊断/单测用）。"""
        with self._lock:
            if self._last_heartbeat is None:
                return None
            return self._now() - self._last_heartbeat

    def idle_exit_due(self) -> bool:
        """是否应因空闲而退出：收到过心跳且距最近心跳 **严格超过** 阈值。

        未收到过心跳（前端从未连上）不判定空闲——避免「刚启动没打开网页就退出」；
        ``idle_exit_seconds == 0`` 表示关闭空闲退出；已请求退出后不再重复判定。
        """
        with self._lock:
            if self._shutdown_requested:
                return False
            if self._idle_exit_seconds <= 0 or self._last_heartbeat is None:
                return False
            return (self._now() - self._last_heartbeat) > self._idle_exit_seconds

    # ------------------------------------------------------------------
    # 退出
    # ------------------------------------------------------------------
    def bind_server(self, server: UvicornServerLike) -> None:
        """绑定 uvicorn ``Server``：之后 :meth:`request_shutdown` 会置 ``should_exit``。

        若在绑定前已经请求退出（边缘时序），绑定后立即置 ``should_exit=True``。
        """
        with self._lock:
            self._server = server
            already_requested = self._shutdown_requested
        if already_requested:
            server.should_exit = True

    def request_shutdown(
        self,
        *,
        reason: str = SHUTDOWN_REASON_SHUTDOWN,
        delay_s: float = LAUNCHER_SHUTDOWN_DELAY_SECONDS,
    ) -> bool:
        """请求优雅退出（幂等）：返回 ``True`` 表示本次调用发起了退出。

        已绑定 server 时用延迟 timer（默认 0.2s）置 ``server.should_exit=True``，让
        ``POST /api/v1/app/shutdown`` 的 HTTP 响应先发出；未绑定 server 时只置标志
        （launcher 尚未进入 serve 阶段 / 纯单测场景）。重复调用返回 ``False`` 且不改变
        首次的 ``shutdown_reason``。
        """
        with self._lock:
            if self._shutdown_requested:
                return False
            self._shutdown_requested = True
            self._shutdown_reason = reason
            server = self._server
            if server is None:
                return True
            timer = threading.Timer(max(delay_s, 0.0), self._mark_server_exit)
            timer.daemon = True
            self._exit_timer = timer
        timer.start()
        return True

    def _mark_server_exit(self) -> None:
        """timer 回调：置 ``server.should_exit=True``（uvicorn 优雅收尾）。"""
        with self._lock:
            server = self._server
        if server is not None:
            server.should_exit = True
