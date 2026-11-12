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


# ---------------------------------------------------------------------------
# R-25：同机反代信任（TEXTGAME_TRUST_PROXY）——防伪造代理头绕过 loopback 免 token
# ---------------------------------------------------------------------------
def test_forwarded_header_without_trust_proxy_is_forbidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未启用 trust_proxy 时，即使本机（loopback）请求带 XFF/Forwarded 也 403。

    场景：同机反代把外部请求转发到 127.0.0.1，若服务端盲目按 ``scope["client"]`` 判本机，
    外部请求可携带伪造 X-Forwarded-For 直接免 token；因此默认对代理头 fail-closed。
    """
    monkeypatch.delenv("TEXTGAME_TRUST_PROXY", raising=False)
    monkeypatch.delenv("TEXTGAME_API_TOKEN", raising=False)
    app = create_app()
    with TestClient(app) as client:
        for headers in (
            {"X-Forwarded-For": "127.0.0.1"},
            {"Forwarded": "for=127.0.0.1"},
        ):
            resp = client.get("/healthz", headers=headers)
            assert resp.status_code == 403, headers
            assert resp.json()["ok"] is False
            assert resp.json()["error_code"] == "NOT_ALLOWED"
            assert "TEXTGAME_TRUST_PROXY" in resp.json()["message"]


def test_trust_proxy_public_xff_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """trust_proxy=true 时按 XFF 首跳判定：公网 IP 视为非本机 → 无 token 403、带 token 200。"""
    monkeypatch.setenv("TEXTGAME_TRUST_PROXY", "true")
    monkeypatch.setenv("TEXTGAME_API_TOKEN", "secret-token")
    app = create_app()
    with TestClient(app) as client:
        denied = client.get("/healthz", headers={"X-Forwarded-For": "203.0.113.9"})
        assert denied.status_code == 403
        assert denied.json()["error_code"] == "NOT_ALLOWED"

        allowed = client.get(
            "/healthz",
            headers={"X-Forwarded-For": "203.0.113.9", "Authorization": "Bearer secret-token"},
        )
        assert allowed.status_code == 200


def test_trust_proxy_loopback_xff_allows_without_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """trust_proxy=true 且 XFF 首跳是本机（同机反代场景）→ 视同 loopback 放行。"""
    monkeypatch.setenv("TEXTGAME_TRUST_PROXY", "1")
    monkeypatch.delenv("TEXTGAME_API_TOKEN", raising=False)
    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/healthz", headers={"X-Forwarded-For": "127.0.0.1"})
        assert resp.status_code == 200


def test_trust_proxy_uses_first_xff_hop_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """多值 XFF 只信首跳：伪造「公网, 本机」仍需 token；「本机, 公网」才放行（记录口径）。"""
    monkeypatch.setenv("TEXTGAME_TRUST_PROXY", "true")
    monkeypatch.delenv("TEXTGAME_API_TOKEN", raising=False)
    app = create_app()
    with TestClient(app) as client:
        spoofed = client.get("/healthz", headers={"X-Forwarded-For": "203.0.113.9, 127.0.0.1"})
        assert spoofed.status_code == 403

        local_first = client.get("/healthz", headers={"X-Forwarded-For": "127.0.0.1, 203.0.113.9"})
        assert local_first.status_code == 200


def test_trust_proxy_forwarded_header_public_and_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``Forwarded: for=`` 基础支持：公网首跳需 token，IPv6 本机首跳放行。"""
    monkeypatch.setenv("TEXTGAME_TRUST_PROXY", "true")
    monkeypatch.delenv("TEXTGAME_API_TOKEN", raising=False)
    app = create_app()
    with TestClient(app) as client:
        denied = client.get("/healthz", headers={"Forwarded": "for=203.0.113.9;proto=http"})
        assert denied.status_code == 403
        assert denied.json()["error_code"] == "NOT_ALLOWED"

        allowed = client.get("/healthz", headers={"Forwarded": 'for="[::1]";proto=http'})
        assert allowed.status_code == 200


def test_trust_proxy_invalid_xff_is_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    """trust_proxy=true 但 XFF 首跳不是合法 IP → fail-closed 403（不做猜测/回退）。"""
    monkeypatch.setenv("TEXTGAME_TRUST_PROXY", "true")
    monkeypatch.delenv("TEXTGAME_API_TOKEN", raising=False)
    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/healthz", headers={"X-Forwarded-For": "not-an-ip"})
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "NOT_ALLOWED"
        assert "非法" in resp.json()["message"]


# ---------------------------------------------------------------------------
# R-04/R-05：单进程假设——多 worker 环境启动期 fail-fast
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("worker_env", ["WEB_CONCURRENCY", "UVICORN_WORKERS", "GUNICORN_WORKERS"])
def test_multi_worker_env_fails_fast(monkeypatch: pytest.MonkeyPatch, worker_env: str) -> None:
    """任一 worker 环境变量 >1 且未显式放行 → ``create_app`` 抛 RuntimeError（中文提示）。"""
    monkeypatch.delenv("TEXTGAME_ALLOW_MULTI_WORKER", raising=False)
    monkeypatch.setenv(worker_env, "2")
    with pytest.raises(RuntimeError, match="单进程"):
        create_app()


def test_multi_worker_non_numeric_worker_env_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """非整数 worker 配置（如 ``auto``）与 0/1 不触发 fail-fast（按单进程处理）。"""
    monkeypatch.delenv("TEXTGAME_ALLOW_MULTI_WORKER", raising=False)
    monkeypatch.setenv("WEB_CONCURRENCY", "1")
    monkeypatch.setenv("UVICORN_WORKERS", "auto")
    app = create_app()
    assert app is not None


def test_multi_worker_allowed_by_explicit_risk_acceptance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``TEXTGAME_ALLOW_MULTI_WORKER=true`` 显式接受风险后可启动（不抛错）。"""
    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    monkeypatch.setenv("TEXTGAME_ALLOW_MULTI_WORKER", "true")
    app = create_app()
    assert app is not None


# ---------------------------------------------------------------------------
# R-18：chunked（Transfer-Encoding）请求体超限 → 413 统一信封
# ---------------------------------------------------------------------------
def test_chunked_request_body_over_limit_returns_413() -> None:
    """迭代器 content（httpx 以 chunked 发送、无 Content-Length）>1MB → 413 统一信封。

    上方 ``test_request_body_over_limit_returns_413`` 覆盖中间件的 Content-Length 预检分支；
    本用例覆盖**实际字节计数分支**（分块发送时没有长度头）。用真实 ``create_app`` 全栈 +
    原始 ``await request.body()`` 的只读预检端点（场景预检），不依赖 Content-Length 头。
    """
    from textgame.persistence.database import create_engine_and_session, init_schema
    from textgame.persistence.scenario_repository import SqliteScenarioRepository
    from textgame.services.scenario_service import ScenarioService

    engine, _factory = create_engine_and_session("sqlite:///:memory:")
    init_schema(engine)
    try:
        scenario_service = ScenarioService(SqliteScenarioRepository(engine))
        app = create_app(scenario_service=scenario_service)

        def _chunks():
            for _ in range(3):  # 3 × 512KB = 1.5MB > 全局 1MB 上限
                yield b"x" * (512 * 1024)

        with TestClient(app) as client:
            response = client.post("/api/v1/scenarios/validate", content=_chunks())
            assert response.request.headers.get("transfer-encoding") == "chunked"
            assert "content-length" not in response.request.headers
    finally:
        engine.dispose()

    assert response.status_code == 413
    body = response.json()
    assert body["ok"] is False
    assert body["error_code"] == "INVALID_INPUT"
    assert "上限" in body["message"] and "1024KB" in body["message"]


# ---------------------------------------------------------------------------
# R-18 补全：声明 Pydantic body 的 JSON 端点 chunked 超限 → 413 统一信封
# （FastAPI 0.162 的 body 解析 except Exception 曾把 receive 层异常吞成 400）
# ---------------------------------------------------------------------------
def test_chunked_over_limit_json_endpoint_returns_413(app_client: TestClient) -> None:
    """POST /api/v1/games（Pydantic body）chunked >1MB → 413 统一信封且业务零处理。"""
    games_before = app_client.get("/api/v1/games").json()

    def _chunks():
        for _ in range(5):  # 5 × 256KB = 1.25MB > 全局 1MB 上限
            yield b"x" * (256 * 1024)

    response = app_client.post(
        "/api/v1/games",
        content=_chunks(),
        headers={"Content-Type": "application/json", "Idempotency-Key": "chunked-over-limit"},
    )
    # 确认真的是 chunked 发送（无 Content-Length，走中间件实际字节计数分支）。
    assert response.request.headers.get("transfer-encoding") == "chunked"
    assert "content-length" not in response.request.headers

    assert response.status_code == 413
    body = response.json()
    assert body["ok"] is False
    assert body["error_code"] == "INVALID_INPUT"
    assert "1024KB" in body["message"]
    # 业务零处理：拒绝发生在路由/建局/幂等写入之前，游戏列表保持不变。
    assert app_client.get("/api/v1/games").json() == games_before


def test_valid_small_chunked_json_request_reaches_business(app_client: TestClient) -> None:
    """合法 chunked 小 JSON body 不被误杀：POST /api/v1/games 正常建局（201）。"""

    def _chunks():
        yield b'{"seed": 7}'

    response = app_client.post(
        "/api/v1/games",
        content=_chunks(),
        headers={"Content-Type": "application/json", "Idempotency-Key": "chunked-small-ok"},
    )
    assert response.request.headers.get("transfer-encoding") == "chunked"
    assert response.status_code == 201
    assert response.json()["game_id"]
