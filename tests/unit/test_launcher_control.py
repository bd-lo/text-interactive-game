"""启动器控制面单测（``textgame.launcher_control``）：心跳 / 空闲判定 / 退出幂等 / 注入时钟。

全部纯内存、无网络、无框架：用可注入时钟与假 uvicorn Server 验证定时器行为，
真实 uvicorn 进程行为见 ``tests/integration/test_launcher_process.py``。
"""

from __future__ import annotations

import threading
from collections.abc import Callable

import pytest

from textgame.launcher_control import (
    DEFAULT_IDLE_EXIT_SECONDS,
    LAUNCHER_SHUTDOWN_DELAY_SECONDS,
    LauncherController,
)


class FakeClock:
    """可注入时钟：手动推进（秒）。"""

    def __init__(self, start: float = 1000.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeServer:
    """uvicorn ``Server`` 的最小替身（只要求 ``should_exit`` 可写）。"""

    def __init__(self) -> None:
        self.should_exit = False


def _wait_until(predicate: Callable[[], bool], *, timeout_s: float = 2.0) -> bool:
    """轮询直到谓词为真或超时（用于等待延迟 timer 线程）。"""
    tick = threading.Event()
    waited = 0.0
    while waited < timeout_s:
        if predicate():
            return True
        tick.wait(0.01)
        waited += 0.01
    return predicate()


# ---------------------------------------------------------------------------
# 构造 / 基础状态
# ---------------------------------------------------------------------------
def test_defaults_and_injected_clock() -> None:
    """默认阈值 300s；``started_at`` 取注入时钟；未心跳/未退出时状态干净。"""
    clock = FakeClock(500.0)
    controller = LauncherController(now=clock)
    assert controller.idle_exit_seconds == DEFAULT_IDLE_EXIT_SECONDS == 300
    assert controller.started_at == 500.0
    assert controller.last_heartbeat is None
    assert controller.shutdown_requested is False
    assert controller.shutdown_reason is None
    assert controller.seconds_since_heartbeat() is None


def test_negative_idle_seconds_rejected() -> None:
    """负阈值是装配错误（0 才表示关闭）。"""
    with pytest.raises(ValueError):
        LauncherController(idle_exit_seconds=-1)


# ---------------------------------------------------------------------------
# 心跳 / 空闲判定
# ---------------------------------------------------------------------------
def test_touch_records_last_heartbeat() -> None:
    """``touch`` 记录当前注入时钟为最近心跳。"""
    clock = FakeClock(100.0)
    controller = LauncherController(idle_exit_seconds=10, now=clock)
    clock.advance(5.0)
    controller.touch()
    assert controller.last_heartbeat == 105.0
    assert controller.seconds_since_heartbeat() == 0.0


def test_idle_never_due_without_any_heartbeat() -> None:
    """从未收到心跳时无论过多久都不判空闲（避免启动后无人打开网页就退出）。"""
    clock = FakeClock(0.0)
    controller = LauncherController(idle_exit_seconds=1, now=clock)
    clock.advance(10_000.0)
    assert controller.idle_exit_due() is False


def test_idle_due_only_after_threshold_strict() -> None:
    """距最近心跳**严格超过**阈值才空闲；恰好等于阈值不算。"""
    clock = FakeClock(0.0)
    controller = LauncherController(idle_exit_seconds=10, now=clock)
    controller.touch()
    clock.advance(10.0)
    assert controller.idle_exit_due() is False
    clock.advance(0.001)
    assert controller.idle_exit_due() is True


def test_idle_disabled_when_zero() -> None:
    """``idle_exit_seconds=0`` = 关闭空闲退出（心跳后过任意久都不退出）。"""
    clock = FakeClock(0.0)
    controller = LauncherController(idle_exit_seconds=0, now=clock)
    controller.touch()
    clock.advance(1_000_000.0)
    assert controller.idle_exit_due() is False


def test_idle_not_due_after_shutdown_requested() -> None:
    """已请求退出后不再重复判定空闲。"""
    clock = FakeClock(0.0)
    controller = LauncherController(idle_exit_seconds=1, now=clock)
    controller.touch()
    controller.request_shutdown(reason="shutdown")
    clock.advance(100.0)
    assert controller.idle_exit_due() is False


# ---------------------------------------------------------------------------
# 退出：幂等 / 原因 / 绑定 server 的延迟 timer
# ---------------------------------------------------------------------------
def test_request_shutdown_unbound_sets_flags_and_is_idempotent() -> None:
    """未绑定 server 时只置标志；重复请求返回 False 且不改首次原因。"""
    controller = LauncherController(idle_exit_seconds=10)
    assert controller.request_shutdown() is True
    assert controller.shutdown_requested is True
    assert controller.shutdown_reason == "shutdown"

    assert controller.request_shutdown(reason="idle") is False
    assert controller.shutdown_reason == "shutdown"  # 首次原因保留


def test_request_shutdown_keeps_idle_reason() -> None:
    """monitor 走 idle 原因时对外语义正确。"""
    controller = LauncherController(idle_exit_seconds=10)
    assert controller.request_shutdown(reason="idle") is True
    assert controller.shutdown_reason == "idle"
    assert controller.request_shutdown() is False
    assert controller.shutdown_reason == "idle"


def test_request_shutdown_delays_server_should_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """绑定 server 后：默认 0.2s 延迟 timer 置 ``should_exit``（HTTP 响应先返回）。"""
    captured: list[tuple[float, threading.Timer]] = []
    real_timer = threading.Timer

    def _capture_timer(interval: float, function: Callable[[], object]) -> threading.Timer:
        timer = real_timer(interval, function)
        captured.append((interval, timer))
        return timer

    monkeypatch.setattr(threading, "Timer", _capture_timer)
    server = FakeServer()
    controller = LauncherController(idle_exit_seconds=10)
    controller.bind_server(server)
    assert controller.request_shutdown() is True
    assert captured and captured[0][0] == pytest.approx(LAUNCHER_SHUTDOWN_DELAY_SECONDS)
    assert server.should_exit is False  # 延迟窗口内不立刻退出
    assert _wait_until(lambda: server.should_exit), "server.should_exit 未被置位"


def test_request_shutdown_zero_delay_sets_should_exit_quickly() -> None:
    """显式 ``delay_s=0`` 时 timer 很快置 ``should_exit``。"""
    server = FakeServer()
    controller = LauncherController(idle_exit_seconds=10)
    controller.bind_server(server)
    assert controller.request_shutdown(delay_s=0.0) is True
    assert _wait_until(lambda: server.should_exit)


def test_bind_after_shutdown_marks_should_exit_immediately() -> None:
    """退出请求早于 server 绑定的边缘时序：绑定后立即置 ``should_exit``。"""
    controller = LauncherController(idle_exit_seconds=10)
    controller.request_shutdown()
    server = FakeServer()
    controller.bind_server(server)
    assert server.should_exit is True
