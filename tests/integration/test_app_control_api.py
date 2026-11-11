"""启动器控制面 API 集成测试（冻结契约 §1–§3）。

覆盖：``GET /api/v1/app/status``（启动器/非启动器两种形状、精确字段）、
``POST /api/v1/app/heartbeat``（200 + 404 无控制器）、
``POST /api/v1/app/shutdown``（200/幂等/404/403 非回环/422 confirm/未知字段）、
以及 OpenAPI 三端点存在。全部进程内 TestClient + 临时 SQLite，不占端口、不联网。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from textgame import __version__, console
from textgame.api.app import create_app
from textgame.config.settings import get_settings
from textgame.launcher_control import LauncherController

pytestmark = [pytest.mark.integration]

_STATUS_PATH = "/api/v1/app/status"
_HEARTBEAT_PATH = "/api/v1/app/heartbeat"
_SHUTDOWN_PATH = "/api/v1/app/shutdown"


def _assert_error(response: Any, status_code: int, error_code: str) -> dict[str, Any]:
    """断言统一失败信封（HTTP 状态 + ``{ok:false, error_code, message, violations?}``）。"""
    assert response.status_code == status_code, response.text
    body: dict[str, Any] = response.json()
    assert body.get("ok") is False
    assert body.get("error_code") == error_code
    assert isinstance(body.get("message"), str) and body["message"]
    assert set(body) <= {"ok", "error_code", "message", "violations"}
    return body


@pytest.fixture()
def launcher_app(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[tuple[FastAPI, LauncherController]]:
    """按组合根口径构建「启动器模式」app（临时 SQLite + 注入控制器）。"""
    monkeypatch.setenv("TEXTGAME_DB_URL", f"sqlite:///{(tmp_path / 'app-control.db').as_posix()}")
    get_settings.cache_clear()
    import textgame.main as main_module

    console.dispose_app_engine(main_module.app)
    controller = LauncherController(idle_exit_seconds=120)
    app = main_module._build_app(launcher_controller=controller)
    try:
        yield app, controller
    finally:
        console.dispose_app_engine(app)
        get_settings.cache_clear()


@pytest.fixture()
def launcher_client(launcher_app: tuple[FastAPI, LauncherController]) -> Iterator[TestClient]:
    """启动器模式 TestClient（Starlette 默认 client host = ``testclient``，契约允许）。"""
    app, _controller = launcher_app
    with TestClient(app) as client:
        yield client


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------
def test_status_launcher_mode(launcher_app: tuple[FastAPI, LauncherController]) -> None:
    """注入控制器：launcher_mode/shutdown_supported 为 true、idle 取控制器实际值、带版本。"""
    app, _controller = launcher_app
    with TestClient(app) as client:
        resp = client.get(_STATUS_PATH)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"launcher_mode", "idle_exit_seconds", "shutdown_supported", "version"}
    assert body["launcher_mode"] is True
    assert body["shutdown_supported"] is True
    assert body["idle_exit_seconds"] == 120
    assert body["version"] == __version__


def test_status_without_controller_is_default_off() -> None:
    """默认装配（无控制器）：稳定形状全 false/0，版本仍返回。"""
    with TestClient(create_app()) as client:
        resp = client.get(_STATUS_PATH)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"launcher_mode", "idle_exit_seconds", "shutdown_supported", "version"}
    assert body["launcher_mode"] is False
    assert body["shutdown_supported"] is False
    assert body["idle_exit_seconds"] == 0
    assert body["version"] == __version__


# ---------------------------------------------------------------------------
# heartbeat
# ---------------------------------------------------------------------------
def test_heartbeat_ok_and_touches_controller(
    launcher_client: TestClient, launcher_app: tuple[FastAPI, LauncherController]
) -> None:
    """心跳 200 ``{ok, idle_exit_seconds}`` 并刷新控制器最近心跳。"""
    _app, controller = launcher_app
    assert controller.last_heartbeat is None
    resp = launcher_client.post(_HEARTBEAT_PATH)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"ok", "idle_exit_seconds"}
    assert body == {"ok": True, "idle_exit_seconds": 120}
    assert controller.last_heartbeat is not None


def test_heartbeat_without_controller_404() -> None:
    """非启动器模式：心跳 404 统一失败信封。"""
    with TestClient(create_app()) as client:
        resp = client.post(_HEARTBEAT_PATH)
    _assert_error(resp, 404, "NOT_FOUND")


def test_heartbeat_does_not_require_idempotency_key(launcher_client: TestClient) -> None:
    """心跳不需要 Idempotency-Key（反复轮询是正常行为）。"""
    for _ in range(3):
        assert launcher_client.post(_HEARTBEAT_PATH).status_code == 200


# ---------------------------------------------------------------------------
# shutdown
# ---------------------------------------------------------------------------
def test_shutdown_ok_and_idempotent(
    launcher_client: TestClient, launcher_app: tuple[FastAPI, LauncherController]
) -> None:
    """``confirm=true`` → 200 契约响应；重复调用幂等 200、退出原因不变。"""
    _app, controller = launcher_app
    first = launcher_client.post(_SHUTDOWN_PATH, json={"confirm": True})
    assert first.status_code == 200, first.text
    body = first.json()
    assert set(body) == {"ok", "message"}
    assert body == {"ok": True, "message": "游戏正在退出"}
    assert controller.shutdown_requested is True
    assert controller.shutdown_reason == "shutdown"

    second = launcher_client.post(_SHUTDOWN_PATH, json={"confirm": True})
    assert second.status_code == 200
    assert second.json() == body
    assert controller.shutdown_requested is True
    assert controller.shutdown_reason == "shutdown"


def test_shutdown_confirm_must_be_true(launcher_client: TestClient) -> None:
    """``confirm`` 非 true / 缺失 / 多带未知字段 → 422 请求校验信封。"""
    for payload in (
        {"confirm": False},
        {"confirm": "true"},
        {"confirm": 1},
        {},
        {"confirm": True, "extra": 1},
    ):
        resp = launcher_client.post(_SHUTDOWN_PATH, json=payload)
        body = _assert_error(resp, 422, "INVALID_INPUT")
        assert "violations" in body


def test_shutdown_confirm_false_does_not_request_exit(
    launcher_app: tuple[FastAPI, LauncherController],
) -> None:
    """422 时不触发控制器退出。"""
    app, controller = launcher_app
    with TestClient(app) as client:
        assert client.post(_SHUTDOWN_PATH, json={"confirm": False}).status_code == 422
    assert controller.shutdown_requested is False


def test_shutdown_without_controller_404() -> None:
    """非启动器模式：shutdown（即使 confirm 合法）404。"""
    with TestClient(create_app()) as client:
        resp = client.post(_SHUTDOWN_PATH, json={"confirm": True})
    _assert_error(resp, 404, "NOT_FOUND")


def test_shutdown_from_non_loopback_client_forbidden(
    launcher_app: tuple[FastAPI, LauncherController],
) -> None:
    """非回环客户端（``request.client.host`` 不在白名单）→ 403 NOT_ALLOWED。"""
    app, controller = launcher_app
    with TestClient(app, client=("203.0.113.9", 54321)) as remote:
        resp = remote.post(_SHUTDOWN_PATH, json={"confirm": True})
    _assert_error(resp, 403, "NOT_ALLOWED")
    assert controller.shutdown_requested is False


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost", "testclient"])
def test_shutdown_from_allowed_loopback_hosts(
    launcher_app: tuple[FastAPI, LauncherController], host: str
) -> None:
    """契约白名单 4 个回环/测试 host 均允许退出。"""
    app, _controller = launcher_app
    with TestClient(app, client=(host, 50000)) as client:
        resp = client.post(_SHUTDOWN_PATH, json={"confirm": True})
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# OpenAPI / 默认行为
# ---------------------------------------------------------------------------
def test_app_control_endpoints_are_documented(launcher_client: TestClient) -> None:
    """live OpenAPI 暴露 3 个控制面端点（前端契约测试的数据源）。"""
    paths = launcher_client.app.openapi()["paths"]
    assert set(paths[_STATUS_PATH]) >= {"get"}
    assert set(paths[_HEARTBEAT_PATH]) >= {"post"}
    assert set(paths[_SHUTDOWN_PATH]) >= {"post"}
    body_schema = paths[_SHUTDOWN_PATH]["post"]["requestBody"]["content"]["application/json"][
        "schema"
    ]
    assert body_schema["$ref"].endswith("/AppShutdownBody")
