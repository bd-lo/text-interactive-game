"""集成测试: 健康检查端点契约 (M1-001 walking skeleton)。"""

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def test_healthz_returns_ok(app_client: TestClient) -> None:
    """``GET /healthz`` 返回 200, 响应体恰为 ``{"status": "ok"}``。"""
    resp = app_client.get("/healthz")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
