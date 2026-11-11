"""集成测试: 健康检查端点契约 (M1-001 walking skeleton)。"""

import pytest
from fastapi.testclient import TestClient

from textgame.api.app import create_app

pytestmark = pytest.mark.integration


def test_healthz_returns_ok(app_client: TestClient) -> None:
    """``GET /healthz`` 返回 200, 响应体恰为 ``{"status": "ok"}``。"""
    resp = app_client.get("/healthz")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_request_body_over_limit_returns_413(app_client: TestClient) -> None:
    """R-18：Content-Length 超过全局 1MB 上限 → 解析前直接 413 信封。"""
    resp = app_client.post("/healthz", content=b"x" * (1024 * 1024 + 1))

    assert resp.status_code == 413
    assert resp.json()["ok"] is False
    assert resp.json()["error_code"] == "INVALID_INPUT"


def test_untrusted_host_header_rejected(app_client: TestClient) -> None:
    """R-25：非白名单 Host（DNS rebinding 场景）在路由前被 TrustedHost 拒绝。"""
    resp = app_client.get("/healthz", headers={"Host": "evil.example"})

    assert resp.status_code == 400


def test_non_loopback_requires_api_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """R-25：非 loopback 客户端必须带 TEXTGAME_API_TOKEN（本机/测试客户端免 token）。"""
    monkeypatch.setenv("TEXTGAME_API_TOKEN", "secret-token")
    app = create_app()
    with TestClient(app, client=("203.0.113.9", 1234)) as client:
        denied = client.get("/healthz")
        assert denied.status_code == 403
        assert denied.json()["error_code"] == "NOT_ALLOWED"

        allowed = client.get("/healthz", headers={"Authorization": "Bearer secret-token"})
        assert allowed.status_code == 200


def test_non_loopback_without_configured_token_is_forbidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R-25：未配置 token 时非 loopback 访问一律 403（默认只适合 127.0.0.1）。"""
    monkeypatch.delenv("TEXTGAME_API_TOKEN", raising=False)
    app = create_app()
    with TestClient(app, client=("203.0.113.9", 1234)) as client:
        denied = client.get("/healthz")

    assert denied.status_code == 403
