"""应用工厂: 挂载路由与异常处理器, 不写业务逻辑、不建数据库。

组合根之一, 允许在此跨层组装对象, 见开发文档第 4 章 4.2、第 16 章 16.1。
编排器与查询服务由 ``main.py`` / 测试 fixture 注入 (``create_app(orchestrator=...)``),
本文件只负责挂载、异常映射与 M6 静态托管/SPA fallback (D-M6-5①)。

静态托管口径（R2 分发方案 A：``web/dist`` 入库 + wheel 内嵌）：

- 目录解析顺序：``TEXTGAME_STATIC_DIR`` 环境变量 → 当前工作目录 ``web/dist``（兼容旧行为）
  → 由 ``__file__`` 推导的仓库根 ``web/dist``（不依赖 CWD）→ 包内 ``textgame/web_dist``
  （wheel ``force-include``，``pip install`` 后无需 Node/pnpm 也有网页版）；
- 目录不存在时**跳过挂载**（API 正常、``/`` 按 404）并打 WARNING，运行期零 Node/pnpm；
- API 路由优先：``/api/*``/``/healthz`` 不会被 SPA 吞，未命中的 API 路径仍返回 JSON 信封；
- 其他未命中静态文件的 GET：**带扩展名的路径（``/assets/x.js``、``/favicon.ico``）404 JSON**，
  其余返回 ``index.html``（SPA 深链刷新 200）；无 ``index.html`` 时 404；
- 缓存口径：``index.html`` 与其它非哈希静态文件 ``no-cache``；``/assets/*``（Vite 内容哈希）
  ``public, max-age=31536000, immutable``；
- 只托管目录内普通文件（拒绝 ``..``/目录/越界路径，不返回目录列表），静态/SPA 响应统一带
  基础安全头（同源 CSP、``nosniff``、``no-referrer``）。
"""

from __future__ import annotations

import logging
import os
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
from textgame.api.routes.world_books import router as world_books_router
from textgame.domain.results import ErrorCode
from textgame.orchestration.turn_orchestrator import TurnOrchestrator
from textgame.services.audit_service import AuditService
from textgame.services.card_service import CardService
from textgame.services.query_service import QueryService
from textgame.services.world_book_service import WorldBookService

#: 静态目录环境变量（显式覆盖；可指向任意已构建的 SPA 产物目录）。
STATIC_DIR_ENV = "TEXTGAME_STATIC_DIR"

#: D-M6-5① 旧默认值：当前工作目录下的 ``web/dist``（保留常量以兼容既有引用/测试）。
DEFAULT_STATIC_DIR = Path("web/dist")

#: 仓库根 ``web/dist``（``src/textgame/api/app.py`` → parents[3] 即仓库根；不依赖 CWD）。
_REPO_ROOT_STATIC_DIR = Path(__file__).resolve().parents[3] / "web" / "dist"

#: 包内静态产物（wheel ``force-include`` 的 ``web/dist`` → ``textgame/web_dist``）。
_PACKAGE_STATIC_DIR = Path(__file__).resolve().parents[1] / "web_dist"

logger = logging.getLogger(__name__)

#: 静态/SPA 响应的基础安全头（同源 CSP；第 23 章 §23.7 / R-L3）。
SPA_SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}

#: ``index.html``/非哈希静态文件禁用强缓存，保证升级后立即生效。
NO_CACHE_HEADERS: dict[str, str] = {**SPA_SECURITY_HEADERS, "Cache-Control": "no-cache"}

#: ``/assets/*`` 为 Vite 内容哈希文件，可安全长缓存。
IMMUTABLE_HEADERS: dict[str, str] = {
    **SPA_SECURITY_HEADERS,
    "Cache-Control": "public, max-age=31536000, immutable",
}


def _is_asset_path(relative_path: str) -> bool:
    """是否「带扩展名的静态资源」路径（末段含 ``.`` 且不以 ``.`` 开头）。

    SPA 深链（``/games/x``、``/some/deep/link``）没有扩展名，仍回退 ``index.html``；
    ``/assets/nope.js``、``/favicon.ico`` 这类缺失资源返回 404 JSON，不把 HTML 当 JS/CSS 返回。
    """
    last = relative_path.rsplit("/", 1)[-1]
    return "." in last and not last.startswith(".")


def resolve_static_dir(static_dir: str | Path | None = None) -> Path | None:
    """按「env → CWD → 仓库根 → 包内」解析静态产物目录；全部缺失时返回 ``None``。

    显式传入 ``static_dir``（测试/嵌入式调用）时只认该目录，缺失即跳过挂载（行为与 D-M6-5①
    一致），不静默回退到其它候选，避免测试夹具被默认产物污染。
    """
    if static_dir is not None:
        explicit = Path(static_dir)
        if explicit.is_dir():
            return explicit
        logger.warning("指定的静态目录不存在：%s（网页版将不可用，API 正常）", explicit)
        return None
    env_value = os.environ.get(STATIC_DIR_ENV)
    if env_value:
        override = Path(env_value)
        if override.is_dir():
            return override
        logger.warning(
            "环境变量 %s 指向的静态目录不存在：%s（网页版将不可用，API 正常）",
            STATIC_DIR_ENV,
            override,
        )
        return None
    for candidate in (DEFAULT_STATIC_DIR, _REPO_ROOT_STATIC_DIR, _PACKAGE_STATIC_DIR):
        if candidate.is_dir():
            return candidate
    logger.warning(
        "未找到网页版静态产物（web/dist / textgame/web_dist）：GET / 将返回 404。"
        "网页版需要先构建（pnpm --dir web build）或使用含产物的分发包；"
        "终端版可直接运行：uv run --no-dev python -m textgame.console"
    )
    return None


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
        """静态文件优先，其次 ``index.html``；API 前缀与缺失的带扩展名资源保持 JSON 404。"""
        if full_path in {"healthz", "api"} or full_path.startswith("api/"):
            raise ApiError(ErrorCode.NOT_FOUND, f"接口不存在: /{full_path}")
        normalized = "" if not full_path else _normalize_relative_path(full_path)
        if full_path and normalized is None:
            raise ApiError(ErrorCode.NOT_FOUND, "非法的静态资源路径")
        assert normalized is not None
        static_file = _static_file_or_none(root, full_path)
        if static_file is not None:
            headers = IMMUTABLE_HEADERS if normalized.startswith("assets/") else NO_CACHE_HEADERS
            return FileResponse(static_file, headers=headers)
        # 缺失的带扩展名路径是「资源不存在」，不能把 index.html 当 JS/CSS 返回（浏览器会报
        # MIME 错误）；只有无扩展名的 SPA 深链才回退 index.html。
        if normalized and _is_asset_path(normalized):
            raise ApiError(ErrorCode.NOT_FOUND, f"静态资源不存在: /{normalized}")
        if index_file.is_file():
            return FileResponse(
                index_file,
                media_type="text/html",
                headers=NO_CACHE_HEADERS,
            )
        raise ApiError(ErrorCode.NOT_FOUND, "静态资源不存在")


def create_app(
    *,
    orchestrator: TurnOrchestrator | None = None,
    query_service: QueryService | None = None,
    audit_service: AuditService | None = None,
    card_service: CardService | None = None,
    world_book_service: WorldBookService | None = None,
    static_dir: str | Path | None = None,
) -> FastAPI:
    """创建 FastAPI 应用实例; 可选注入编排器、查询服务、审计/人物卡/世界书服务与静态目录。

    ``static_dir`` 缺省按 :func:`resolve_static_dir` 解析（env → CWD → 仓库根 → 包内）；
    目录存在则挂载静态托管 + SPA fallback，缺失则跳过并打 WARNING（API 与既有测试行为不变；
    D-M6-5①/R2 分发方案 A）。组合根仍只负责装配，本文件不做业务计算。
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
    app.include_router(world_books_router)
    if orchestrator is not None:
        app.state.orchestrator = orchestrator
    if query_service is not None:
        app.state.query_service = query_service
    if audit_service is not None:
        app.state.audit_service = audit_service
    if card_service is not None:
        app.state.card_service = card_service
    if world_book_service is not None:
        app.state.world_book_service = world_book_service
    dist = resolve_static_dir(static_dir)
    #: 实际挂载的静态目录（``None``=未挂载）；供启动诊断与验收断言读取。
    app.state.static_dir = str(dist) if dist is not None else None
    if dist is not None:
        _register_spa_fallback(app, dist)
    return app
