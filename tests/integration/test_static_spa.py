"""M6 后端前置·静态托管 + SPA fallback 契约测试（D-M6-5①）。

覆盖：``web/dist`` 缺失时跳过挂载且 API 正常（``/`` 404）；存在时 ``/`` 与深链返回
``index.html``；静态资源 200 且 content-type 正确；API 路由（含未知 ``/api/*``）不被 SPA
吞；路径遍历被拒；基础安全头（同源 CSP/nosniff）存在；无 ``index.html`` 时 404；非 GET
深链不被改写为 HTML。合成夹具在 ``tests/fixtures/web_dist/``（非真实构建产物）。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from textgame.api.app import create_app

_FIXTURE_DIST = Path(__file__).resolve().parents[1] / "fixtures" / "web_dist"
_INDEX_MARKER = "textgame-spa-fixture"


def _client(static_dir: str | Path | None) -> TestClient:
    """构造指定静态目录的应用客户端（``None`` 走 ``web/dist`` 默认值）。"""
    return TestClient(create_app(static_dir=static_dir))


def test_missing_dist_skips_static_and_keeps_api(tmp_path: Path) -> None:
    """静态目录缺失：不挂载 SPA，``/`` 404 不崩，API 与 openapi 正常。"""
    client = _client(tmp_path / "missing-dist")

    assert client.get("/").status_code == 404
    assert client.get("/healthz").status_code == 200
    assert client.get("/api/openapi.json").status_code == 200


def test_root_serves_index_html(tmp_path: Path) -> None:
    """静态目录存在：``/`` 返回 ``index.html``（text/html + 夹具标记）。"""
    client = _client(_FIXTURE_DIST)

    resp = client.get("/")

    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert _INDEX_MARKER in resp.text


def test_spa_deep_link_refresh_returns_index(tmp_path: Path) -> None:
    """SPA 深链（刷新）返回 ``index.html``，不 404。"""
    client = _client(_FIXTURE_DIST)

    for path in ("/games/game-abc", "/some/deep/link?x=1"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert "text/html" in resp.headers["content-type"]
        assert _INDEX_MARKER in resp.text


def test_static_asset_served_with_content_type() -> None:
    """静态资源命中文件：200、JS content-type、内容为夹具文件本身。"""
    client = _client(_FIXTURE_DIST)

    resp = client.get("/assets/app.js")

    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"]
    assert "__TEXTGAME_FIXTURE__" in resp.text


def test_api_routes_are_not_swallowed_by_spa() -> None:
    """API 优先：``/healthz``/openapi 返回 JSON，未知 ``/api/*`` 返回 JSON 404 信封。"""
    client = _client(_FIXTURE_DIST)

    health = client.get("/healthz")
    assert health.status_code == 200
    assert "application/json" in health.headers["content-type"]
    assert client.get("/api/openapi.json").status_code == 200
    unknown = client.get("/api/v1/definitely-not-a-route")
    assert unknown.status_code == 404
    assert "application/json" in unknown.headers["content-type"]
    assert unknown.json()["ok"] is False
    assert unknown.json()["error_code"] == "NOT_FOUND"
    assert _INDEX_MARKER not in unknown.text


def test_path_traversal_is_rejected() -> None:
    """路径遍历（``..``/百分号编码/反斜杠）被拒为 404，不泄露仓库根文件内容。"""
    client = _client(_FIXTURE_DIST)

    probes = (
        "/%2e%2e/%2e%2e/pyproject.toml",
        "/assets/%2e%2e/%2e%2e/README.md",
        "/..%5Cpyproject.toml",
    )
    for path in probes:
        resp = client.get(path)
        assert resp.status_code == 404, path
        assert "[build-system]" not in resp.text, path


def test_security_headers_on_spa_and_assets() -> None:
    """index 与静态资源响应带同源 CSP、``nosniff`` 与 ``no-referrer``。"""
    client = _client(_FIXTURE_DIST)

    for path in ("/", "/games/deep-link", "/assets/app.js"):
        headers = client.get(path).headers
        assert "default-src 'self'" in headers["content-security-policy"], path
        assert headers["x-content-type-options"] == "nosniff", path
        assert headers["referrer-policy"] == "no-referrer", path


def test_directory_path_does_not_list_files() -> None:
    """目录路径不返回目录列表：``/assets`` 走 SPA fallback 返回 index。"""
    client = _client(_FIXTURE_DIST)

    resp = client.get("/assets")

    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert _INDEX_MARKER in resp.text
    assert "app.js" not in resp.text  # 不是目录列表


def test_dist_without_index_html_falls_back_to_404(tmp_path: Path) -> None:
    """目录存在但无 ``index.html``：未命中文件的路径 404，静态文件仍可访问。"""
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (assets / "app.js").write_text("// fixture", encoding="utf-8")
    client = _client(dist)

    assert client.get("/").status_code == 404
    assert client.get("/games/abc").status_code == 404
    assert client.get("/assets/app.js").status_code == 200


def test_non_get_deep_link_is_not_rewritten_to_index() -> None:
    """非 GET 请求不被 SPA 改写：``POST /games/x`` 不返回 index.html。"""
    client = _client(_FIXTURE_DIST)

    resp = client.post("/games/game-abc")

    assert resp.status_code == 405
    assert _INDEX_MARKER not in resp.text
