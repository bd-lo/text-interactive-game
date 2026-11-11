"""``python -m textgame.launcher``：普通玩家启动入口（uvicorn + 启动器控制面 + 空闲退出）。

与 ``uvicorn textgame.main:app`` 的区别：

- 创建 :class:`~textgame.launcher_control.LauncherController` 并经
  ``textgame.main._build_app(launcher_controller=...)`` 注入 app，从而启用
  ``/api/v1/app/status|heartbeat|shutdown``（网页「退出游戏」按钮经 POST shutdown 优雅停机）；
- 启动一个 **monitor daemon 线程**：前端至少上报过 1 次心跳后，若距最近心跳超过阈值
  （``TEXTGAME_IDLE_EXIT_SECONDS``，默认 300 秒；``--idle-exit-seconds`` 覆盖；``0``=关闭）
  则自动优雅退出（reason=idle）；从未收到心跳不判空闲（避免启动后没人打开网页就退出）；
- 日志契约：``LAUNCHER: ready on http://host:port``（serve 就绪后）、
  ``LAUNCHER: exit reason=shutdown|idle|signal``（进程结束前）；
- ``SIGINT``/``SIGTERM`` 由 uvicorn 捕获后优雅退出，退出码 0。

本模块是**进程入口**：只做参数解析、uvicorn 组装与线程编排，业务都在既有组合根/服务里；
默认装配（``textgame.main:app``）不经过本模块，行为逐字不变。
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading

import uvicorn

from textgame.config.settings import get_settings
from textgame.launcher_control import (
    DEFAULT_IDLE_EXIT_SECONDS,
    SHUTDOWN_REASON_IDLE,
    LauncherController,
)

__all__ = ["build_launcher_server", "main", "run_launcher"]

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_LOG_LEVEL = "INFO"

#: 空闲 monitor 轮询间隔（秒）：越小退出越及时，越大越省 CPU；1s 对 300s 阈值足够。
IDLE_MONITOR_POLL_SECONDS = 1.0
#: 等待 uvicorn ``started`` 的轮询间隔（秒）；就绪日志用。
_READY_POLL_SECONDS = 0.02

logger = logging.getLogger("textgame.launcher")


def _configure_logging(log_level: str) -> None:
    """配置 ``textgame.launcher`` logger：输出到 stdout，格式只保留消息体。

    uvicorn 的 logging 配置只接管 ``uvicorn.*`` logger；若不显式加 handler，
    启动器日志（INFO）会被静默丢弃，启动脚本无法用 ``LAUNCHER: ready`` 判断就绪。
    """
    level = getattr(logging, log_level.upper(), logging.INFO)
    logger.setLevel(level)
    logger.propagate = False
    if not logger.handlers:
        stream_handler = logging.StreamHandler(stream=sys.stdout)
        stream_handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(stream_handler)
    for existing_handler in logger.handlers:
        existing_handler.setLevel(level)


def build_launcher_server(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    idle_exit_seconds: int = DEFAULT_IDLE_EXIT_SECONDS,
    log_level: str = DEFAULT_LOG_LEVEL,
) -> tuple[uvicorn.Server, LauncherController]:
    """构造 uvicorn ``Server`` + 注入控制器的 app（测试可复用，不启动网络）。

    返回 ``(server, controller)``；``controller`` 已 ``bind_server(server)``，因此
    ``POST /api/v1/app/shutdown`` 会在 0.2s 后置 ``server.should_exit=True``。
    """
    controller = LauncherController(idle_exit_seconds=idle_exit_seconds)
    # 延迟 import：launcher 模块在非启动器路径（uvicorn textgame.main:app）不被加载；
    # 且 main 模块级 ``app = _build_app()`` 会构建一次默认装配（组合根既有行为）。
    from textgame.main import _build_app

    app = _build_app(launcher_controller=controller)
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level=log_level.lower(),
    )
    server = uvicorn.Server(config)
    controller.bind_server(server)
    return server, controller


def _wait_until_ready(
    server: uvicorn.Server,
    *,
    host: str,
    port: int,
    stop_event: threading.Event,
) -> None:
    """后台线程：uvicorn 完成 startup 后打一条 ``LAUNCHER: ready on ...`` 日志。"""
    while not stop_event.wait(_READY_POLL_SECONDS):
        if server.started:
            logger.info("LAUNCHER: ready on http://%s:%s", host, port)
            return
        if server.should_exit:
            return


def _monitor_idle(
    controller: LauncherController,
    stop_event: threading.Event,
    *,
    poll_seconds: float = IDLE_MONITOR_POLL_SECONDS,
) -> None:
    """monitor daemon 线程：空闲超时 → 请求优雅退出（reason=idle）。"""
    while not stop_event.wait(poll_seconds):
        if controller.idle_exit_due():
            logger.info("LAUNCHER: idle %ss without heartbeat", controller.idle_exit_seconds)
            controller.request_shutdown(reason=SHUTDOWN_REASON_IDLE)
            return


def run_launcher(
    *,
    host: str,
    port: int,
    idle_exit_seconds: int,
    log_level: str,
) -> int:
    """启动 uvicorn 并等待退出；返回进程退出码（正常退出恒为 0）。

    退出原因：``shutdown``（前端 POST）/``idle``（空闲 monitor）/``signal``（SIGINT/SIGTERM
    由 uvicorn 捕获后正常返回）。
    """
    _configure_logging(log_level)
    server, controller = build_launcher_server(
        host=host,
        port=port,
        idle_exit_seconds=idle_exit_seconds,
        log_level=log_level,
    )
    stop_event = threading.Event()
    if idle_exit_seconds > 0:
        monitor = threading.Thread(
            target=_monitor_idle,
            args=(controller, stop_event),
            name="textgame-launcher-idle",
            daemon=True,
        )
        monitor.start()
    ready = threading.Thread(
        target=_wait_until_ready,
        args=(server,),
        kwargs={"host": host, "port": port, "stop_event": stop_event},
        name="textgame-launcher-ready",
        daemon=True,
    )
    ready.start()
    try:
        server.run()
    finally:
        stop_event.set()
        ready.join(timeout=1.0)
    reason = controller.shutdown_reason or "signal"
    logger.info("LAUNCHER: exit reason=%s", reason)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    """构造 CLI 参数解析器（冻结契约：host/port/idle-exit-seconds/log-level）。"""
    parser = argparse.ArgumentParser(
        prog="python -m textgame.launcher",
        description="启动 textgame 本机游戏（uvicorn + 启动器控制面：网页可查看状态/退出）",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"监听地址（默认 {DEFAULT_HOST}，只监听本机回环）",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"监听端口（默认 {DEFAULT_PORT}）",
    )
    parser.add_argument(
        "--idle-exit-seconds",
        type=int,
        default=None,
        help=(
            "空闲自动退出秒数（0=关闭；缺省读 TEXTGAME_IDLE_EXIT_SECONDS，"
            f"未设置时为 {DEFAULT_IDLE_EXIT_SECONDS}）"
        ),
    )
    parser.add_argument(
        "--log-level",
        default=DEFAULT_LOG_LEVEL,
        help=f"日志级别（默认 {DEFAULT_LOG_LEVEL}）",
    )
    return parser


def _resolve_idle_exit_seconds(cli_value: int | None, parser: argparse.ArgumentParser) -> int:
    """``--idle-exit-seconds`` 优先，其次 ``TEXTGAME_IDLE_EXIT_SECONDS``/默认 300。"""
    if cli_value is not None:
        if cli_value < 0:
            parser.error("--idle-exit-seconds 必须 >= 0（0 = 关闭空闲退出）")
        return cli_value
    try:
        return get_settings().idle_exit_seconds
    except ValueError as exc:  # 环境变量非法（pydantic ValidationError 是 ValueError）
        parser.error(f"读取 TEXTGAME_IDLE_EXIT_SECONDS 失败：{exc}")


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：``python -m textgame.launcher --host ... --port ...``。"""
    parser = _build_parser()
    args = parser.parse_args(argv)
    idle_exit_seconds = _resolve_idle_exit_seconds(args.idle_exit_seconds, parser)
    return run_launcher(
        host=str(args.host),
        port=int(args.port),
        idle_exit_seconds=idle_exit_seconds,
        log_level=str(args.log_level).upper(),
    )


if __name__ == "__main__":
    raise SystemExit(main())
