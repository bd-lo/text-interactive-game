"""应用工厂: 挂载路由与异常处理器, 不写业务逻辑、不建数据库。

组合根之一, 允许在此跨层组装对象, 见开发文档第 4 章 4.2、第 16 章 16.1。
编排器与查询服务由 ``main.py`` / 测试 fixture 注入 (``create_app(orchestrator=...)``),
本文件只负责挂载、异常映射与 M6 静态托管/SPA fallback (D-M6-5①)。

静态托管口径：

- ``static_dir`` 缺省 ``web/dist``；目录不存在时**跳过挂载**（API 正常、``/`` 按 404），
  运行期零 Node/pnpm（构建产物只在构建期生成，D-M6-5②）；
- API 路由优先：``/api/*``/``/healthz`` 不会被 SPA 吞，未命中的 API 路径仍返回 JSON 信封；
- 其他未命中静态文件的 GET 返回 ``index.html``（SPA 深链刷新 200）；无 ``index.html`` 时 404；
- 只托管目录内普通文件（拒绝 ``..``/目录/越界路径，不返回目录列表），静态/SPA 响应统一带
  基础安全头（同源 CSP、``nosniff``、``no-referrer``）。
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote

from fastapi import FastAPI, Response
from fastapi.responses import FileResponse

from textgame import __version__
from textgame.api.errors import ApiError, install_error_handlers
from textgame.api.routes.cards import router as cards_router
from textgame.api.routes.games import router as games_router
from textgame.api.routes.health import router as health_router
from textgame.api.routes.turns import router as turns_router
from textgame.domain.results import ErrorCode
from textgame.orchestration.turn_orchestrator import TurnOrchestrator
from textgame.services.audit_service import AuditService
from textgame.services.card_service import CardService
from textgame.services.query_service import QueryService

#: D-M6-5①：默认静态产物目录（``web/dist``；gitignore 忽略，构建期生成）。
DEFAULT_STATIC_DIR = Path("web/dist")

#: 静态/SPA 响应的基础安全头（同源 CSP；第 23 章 §23.7 / R-L3）。
SPA_SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


def _normalize_relative_path(relative_path: str) -> str | None:
    """URL 路径 → 安全相对路径；含空段/``.``/``..``/反斜杠越界等非法段返回 ``None``。

    先做百分号解码（防 ``%2e%2e`` 编码绕过），统一反斜杠后逐段校验；绝对路径首段为空
    也会被判非法。返回值为可直接拼接 ``root`` 的相对路径。
    """
    normalized = unquote(relative_path).replace("\\", "/")
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return None
    return normalized


def _static_file_or_none(root: Path, relative_path: str) -> Path | None:
    """URL 相对路径 → ``root`` 内的普通文件；越界/目录/空路径一律返回 ``None``。

    先用 :func:`_normalize_relative_path` 拒绝 ``..`` 等非法段，再用 ``resolve()`` 复核
    仍在 ``root`` 内（双保险）；目录不返回（禁止目录列表）。
    """
    normalized = _normalize_relative_path(relative_path)
    if normalized is None:
        return None
    candidate = (root / normalized).resolve()
    if candidate != root and root not in candidate.parents:
        return None
    return candidate if candidate.is_file() else None


def _register_spa_fallback(app: FastAPI, static_dir: Path) -> None:
    """登记 SPA fallback 路由（须在 API 路由之后调用；仅当静态目录存在时）。"""
    root = static_dir.resolve()
    index_file = root / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str) -> Response:
        """静态文件优先，其次 ``index.html``；API 前缀保持 JSON 404 信封不落 SPA。"""
        if full_path in {"healthz", "api"} or full_path.startswith("api/"):
            raise ApiError(ErrorCode.NOT_FOUND, f"接口不存在: /{full_path}")
        if full_path and _normalize_relative_path(full_path) is None:
            raise ApiError(ErrorCode.NOT_FOUND, "非法的静态资源路径")
        static_file = _static_file_or_none(root, full_path)
        if static_file is not None:
            return FileResponse(static_file, headers=SPA_SECURITY_HEADERS)
        if index_file.is_file():
            return FileResponse(
                index_file,
                media_type="text/html",
                headers=SPA_SECURITY_HEADERS,
            )
        raise ApiError(ErrorCode.NOT_FOUND, "静态资源不存在")


def create_app(
    *,
    orchestrator: TurnOrchestrator | None = None,
    query_service: QueryService | None = None,
    audit_service: AuditService | None = None,
    card_service: CardService | None = None,
    static_dir: str | Path | None = None,
) -> FastAPI:
    """创建 FastAPI 应用实例; 可选注入编排器、查询服务、审计服务、人物卡服务与静态目录。

    ``static_dir`` 缺省 ``web/dist``：目录存在则挂载静态托管 + SPA fallback，缺失则跳过
    （API 与现有测试行为不变；D-M6-5①）。组合根仍只负责装配，本文件不做业务计算。
    """
    app = FastAPI(
        title="textgame",
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    install_error_handlers(app)
    app.include_router(health_router)
    app.include_router(games_router)
    app.include_router(turns_router)
    app.include_router(cards_router)
    if orchestrator is not None:
        app.state.orchestrator = orchestrator
    if query_service is not None:
        app.state.query_service = query_service
    if audit_service is not None:
        app.state.audit_service = audit_service
    if card_service is not None:
        app.state.card_service = card_service
    dist = Path(static_dir) if static_dir is not None else DEFAULT_STATIC_DIR
    if dist.is_dir():
        _register_spa_fallback(app, dist)
    return app
