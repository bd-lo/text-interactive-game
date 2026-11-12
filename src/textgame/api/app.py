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

import ipaddress
import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from fastapi import FastAPI, Response
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from textgame import __version__
from textgame.api.errors import ApiError, error_body, install_error_handlers
from textgame.api.routes.app_control import router as app_control_router
from textgame.api.routes.cards import router as cards_router
from textgame.api.routes.games import router as games_router
from textgame.api.routes.health import router as health_router
from textgame.api.routes.saves import SAVE_IMPORT_PATH
from textgame.api.routes.saves import router as saves_router
from textgame.api.routes.scenarios import router as scenarios_router
from textgame.api.routes.settings import router as settings_router
from textgame.api.routes.turns import router as turns_router
from textgame.api.routes.world_books import router as world_books_router
from textgame.domain.results import ErrorCode
from textgame.launcher_control import LauncherController
from textgame.orchestration.turn_orchestrator import TurnOrchestrator
from textgame.services.audit_service import AuditService
from textgame.services.card_service import CardService
from textgame.services.query_service import QueryService
from textgame.services.save_service import MAX_SAVE_PACKAGE_BYTES, SaveService
from textgame.services.scenario_service import ScenarioService
from textgame.services.world_book_service import WorldBookService

#: 静态目录环境变量（显式覆盖；可指向任意已构建的 SPA 产物目录）。
STATIC_DIR_ENV = "TEXTGAME_STATIC_DIR"

#: D-M6-5① 旧默认值：当前工作目录下的 ``web/dist``（保留常量以兼容既有引用/测试）。
DEFAULT_STATIC_DIR = Path("web/dist")

#: 仓库根 ``web/dist``（``src/textgame/api/app.py`` → parents[3] 即仓库根；不依赖 CWD）。
_REPO_ROOT_STATIC_DIR = Path(__file__).resolve().parents[3] / "web" / "dist"

#: 包内静态产物（wheel ``force-include`` 的 ``web/dist`` → ``textgame/web_dist``）。
_PACKAGE_STATIC_DIR = Path(__file__).resolve().parents[1] / "web_dist"

#: 全局请求体上限（R-18）：导入端点自身另有 256KB/64KB 细粒度上限。
_MAX_REQUEST_BYTES = 1024 * 1024

#: 存档导入端点的请求体上限（R-18 例外）：后端契约上限 8MB + multipart/JSON 头预算；
#: 只对 ``SAVE_IMPORT_PATH`` 单独放行，其余端点仍保持 1MB（旧 413 行为不变）。
_MAX_SAVE_IMPORT_REQUEST_BYTES = MAX_SAVE_PACKAGE_BYTES + 64 * 1024

#: R-25：默认 Host 白名单（可用 ``TEXTGAME_ALLOWED_HOSTS`` 覆盖；逗号分隔）。
_DEFAULT_ALLOWED_HOSTS = ("127.0.0.1", "localhost", "[::1]", "testserver")

#: R-25：同机反代信任开关（默认 false）。未启用时任何 XFF/Forwarded 请求都拒绝，
#: 避免「本机反代把外部请求转给 loopback」时伪造头绕过 token 校验。
TRUST_PROXY_ENV = "TEXTGAME_TRUST_PROXY"

#: R-04/R-05：进程内锁与启动期自动迁移都假设单进程；这些 worker 环境变量 >1 时启动即失败。
_WORKER_COUNT_ENVS = ("WEB_CONCURRENCY", "UVICORN_WORKERS", "GUNICORN_WORKERS")
_ALLOW_MULTI_WORKER_ENV = "TEXTGAME_ALLOW_MULTI_WORKER"

#: 布尔环境变量可接受的真值写法（大小写不敏感；其余一律按 false 处理）。
_ENV_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def _allowed_hosts() -> list[str]:
    """读取 ``TEXTGAME_ALLOWED_HOSTS``；未设置时用默认本机白名单。"""
    raw = os.environ.get("TEXTGAME_ALLOWED_HOSTS", "")
    hosts = [host.strip() for host in raw.split(",") if host.strip()]
    return hosts or list(_DEFAULT_ALLOWED_HOSTS)


def _env_flag(name: str) -> bool:
    """读取布尔环境变量（``1/true/yes/on`` 为真；缺省/其它值一律 false）。"""
    return os.environ.get(name, "").strip().lower() in _ENV_TRUE_VALUES


def _multi_worker_setting() -> tuple[str, int] | None:
    """返回第一个 >1 的 worker 配置 ``(环境变量名, 数量)``；未配置/非法/≤1 返回 ``None``。"""
    for name in _WORKER_COUNT_ENVS:
        raw = os.environ.get(name, "").strip()
        if not raw:
            continue
        try:
            count = int(raw)
        except ValueError:
            continue
        if count > 1:
            return name, count
    return None


def _assert_single_worker() -> None:
    """R-04/R-05：启动期拒绝多 worker 部署（单进程假设 fail-fast）。

    进程内锁（CAS/幂等）、启动期自动迁移互斥与 LLM 运行期凭据热切换都按单进程设计；
    多 worker 下它们会各自为政（SQLite 写串行、迁移可能并发执行）。显式设置
    ``TEXTGAME_ALLOW_MULTI_WORKER=true`` 表示已知风险并接受。
    """
    found = _multi_worker_setting()
    if found is None or _env_flag(_ALLOW_MULTI_WORKER_ENV):
        return
    name, count = found
    raise RuntimeError(
        f"检测到 {name}={count}：本项目只支持单进程部署（进程内锁、启动期自动迁移互斥与"
        "运行期凭据热切换都假设单进程）。请将 worker 数设为 1；如确需多 worker，"
        "设置 TEXTGAME_ALLOW_MULTI_WORKER=true 显式接受风险。"
    )


class _BodyTooLargeError(BaseException):
    """请求体超过上限（R-18，ASGI receive 层抛出）。

    **必须继承 ``BaseException`` 而非 ``Exception``**：FastAPI 0.162 的 JSON body 解析用
    ``except Exception`` 兜底（``fastapi/routing.py`` 的 ``await request.body()`` 外层），会
    把 receive 阶段抛出的异常吞成 400 ``There was an error parsing the body``，导致声明
    Pydantic body 的 JSON 端点拿不到 413 统一信封（Content-Length 预检路径则正常 413）。
    改用 ``BaseException`` 后异常穿透 FastAPI 的 body 解析与 Starlette 异常中间件，由
    本文件的 ``_BodyLimitMiddleware`` 立即捕获并改写为 413；它是模块私有标记类、只在此处
    raise/catch，不会落到日志、不会泄漏 traceback。
    """


_LOOPBACK_HOSTS = frozenset({"", "127.0.0.1", "::1", "localhost", "testclient"})


def _is_loopback_host(host: str) -> bool:
    """判断请求来源是否本机（测试客户端 ``testclient`` 也视为本机）。"""
    if host.lower() in _LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _header_values(headers: list[tuple[bytes, bytes]], name: bytes) -> list[str]:
    """按名（大小写不敏感）收集全部原始请求头值（保留重复头/顺序）。"""
    return [value.decode("latin-1") for key, value in headers if key.lower() == name]


def _parse_forwarded_ip(raw: str) -> str | None:
    """把 XFF/Forwarded 里的一段客户端地址解析为合法 IP 文本；非法返回 ``None``。

    只接受**能通过 ``ipaddress`` 校验**的值（fail-closed）：允许 ``for=`` 常见的引号、
    IPv6 方括号与 ``ip:port`` 形式；``unknown``/域名/空串一律视为非法。
    """
    candidate = raw.strip().strip('"').strip()
    if not candidate:
        return None
    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1]
    elif candidate.count(":") == 1:
        # 仅 IPv4:port 形式去掉端口（IPv6 裸地址含多个冒号，不做拆分）
        host, _, port = candidate.partition(":")
        if port.isdigit():
            candidate = host
    try:
        ipaddress.ip_address(candidate)
    except ValueError:
        return None
    return candidate


def _forwarded_client_ip(headers: list[tuple[bytes, bytes]]) -> str | None:
    """从代理头取「首跳」客户端 IP；无代理头返回 ``None``；头非法返回空串表示拒绝。

    取值优先级：``X-Forwarded-For``（多个头按到达顺序拼接后取第一个逗号前的值）>
    ``Forwarded``（取第一个元素的 ``for=``）。多值只信第一跳，后续跳不参与判定，
    避免伪造追加值改变判定结果。
    """
    xff_values = _header_values(headers, b"x-forwarded-for")
    if xff_values:
        first = ",".join(xff_values).split(",")[0].strip()
        return _parse_forwarded_ip(first) or ""
    forwarded = _header_values(headers, b"forwarded")
    if forwarded:
        first_element = forwarded[0].split(",")[0]
        for pair in first_element.split(";"):
            key, separator, value = pair.partition("=")
            if not separator or key.strip().lower() != "for":
                continue
            parsed = _parse_forwarded_ip(value)
            if parsed is None:
                return ""
            return parsed
        return ""
    return None


class _AccessTokenMiddleware:
    """R-25：非 loopback 访问要求 ``TEXTGAME_API_TOKEN``（Bearer 或 X-API-Token）。

    ``trust_proxy=False``（默认）时**拒绝一切**带 ``X-Forwarded-For``/``Forwarded`` 的请求：
    同机反代把外部请求转到 loopback 后，伪造代理头可冒充本机绕过 token 校验；必须显式
    设置 ``TEXTGAME_TRUST_PROXY=true`` 表示「连接对端可信、代理头由它写入」。
    启用后按代理头**首跳**判定来源（多值只取第一个；非法 IP fail-closed 403），非 loopback
    仍照常要求 token。
    """

    def __init__(self, app: Any, *, token: str, trust_proxy: bool = False) -> None:
        """保存下游应用、访问 token（空串 = 未配置）与代理信任开关。"""
        self.app = app
        self.token = token
        self.trust_proxy = trust_proxy

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        """代理头未信任/非法 → 403；非本机请求缺 token/错 token → 403；本机请求直接放行。"""
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers: list[tuple[bytes, bytes]] = list(scope.get("headers") or [])
        forwarded_ip = _forwarded_client_ip(headers)
        if forwarded_ip is not None:
            if not self.trust_proxy:
                await self._reject(
                    scope,
                    receive,
                    send,
                    "请求带有 X-Forwarded-For/Forwarded 代理头，但未启用 TEXTGAME_TRUST_PROXY；"
                    "为防止同机反代绕过 loopback 免 token 校验，已拒绝该请求。"
                    "如确认由本机可信反代转发，请设置 TEXTGAME_TRUST_PROXY=true。",
                )
                return
            if not forwarded_ip:
                await self._reject(
                    scope,
                    receive,
                    send,
                    "X-Forwarded-For/Forwarded 首跳客户端地址非法，无法判定请求来源。",
                )
                return
            client_host = forwarded_ip
        else:
            client = scope.get("client") or ("", 0)
            client_host = str(client[0])
        if not _is_loopback_host(client_host):
            if not self.token:
                await self._reject(scope, receive, send, "非本机访问需要设置 TEXTGAME_API_TOKEN")
                return
            headers_map = dict(headers)
            authorization = headers_map.get(b"authorization", b"").decode("latin-1")
            provided = headers_map.get(b"x-api-token", b"").decode("latin-1")
            if authorization != f"Bearer {self.token}" and provided != self.token:
                await self._reject(scope, receive, send, "访问 token 缺失或无效")
                return
        await self.app(scope, receive, send)

    async def _reject(self, scope: Any, receive: Any, send: Any, message: str) -> None:
        """发送 403 统一失败信封。"""
        response = JSONResponse(
            status_code=403,
            content=error_body(ErrorCode.NOT_ALLOWED, message),
        )
        await response(scope, receive, send)


class _BodyLimitMiddleware:
    """R-18：Content-Length 预检 + 实际字节计数（覆盖 chunked 请求）。

    超出上限时在路由/解析前返回 413 统一信封；若响应已开始则不再二次写。
    ``max_import_bytes``（N3-048）：仅 ``SAVE_IMPORT_PATH`` 使用更宽的存档包上限
    （8MB+报文头预算），其余路径保持全局 1MB，旧 413 行为不变。
    """

    def __init__(self, app: Any, *, max_bytes: int, max_import_bytes: int | None = None) -> None:
        """保存下游应用与上限（字节）；导入路径上限可选。"""
        self.app = app
        self.max_bytes = max_bytes
        self.max_import_bytes = max_import_bytes

    def _limit_for(self, scope: Any) -> int:
        """按请求路径选择有效上限（只对存档导入端点放宽）。"""
        if self.max_import_bytes is not None and scope.get("path") == SAVE_IMPORT_PATH:
            return self.max_import_bytes
        return self.max_bytes

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        """ASGI 入口：只处理 HTTP；非 HTTP 原样透传。"""
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        limit = self._limit_for(scope)
        headers = dict(scope.get("headers") or [])
        declared = headers.get(b"content-length", b"")
        if declared.isdigit() and int(declared) > limit:
            await self._reject(scope, receive, send, limit)
            return
        received = 0
        started = False

        async def send_wrapper(message: Any) -> None:
            nonlocal started
            if message.get("type") == "http.response.start":
                started = True
            await send(message)

        async def limited_receive() -> Any:
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _BodyTooLargeError
            return message

        try:
            await self.app(scope, limited_receive, send_wrapper)
        except _BodyTooLargeError:
            if not started:
                await self._reject(scope, receive, send, limit)

    async def _reject(self, scope: Any, receive: Any, send: Any, limit: int) -> None:
        """发送 413 统一失败信封。"""
        response = JSONResponse(
            status_code=413,
            content=error_body(
                ErrorCode.INVALID_INPUT,
                f"请求体超过 {limit // 1024}KB 上限",
            ),
        )
        await response(scope, receive, send)


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
    scenario_service: ScenarioService | None = None,
    launcher_controller: LauncherController | None = None,
    static_dir: str | Path | None = None,
) -> FastAPI:
    """创建 FastAPI 应用实例; 可选注入编排器、查询服务、审计/人物卡/世界书/场景服务与静态目录。

    ``launcher_controller`` 为启动器模式加法：非 ``None`` 时挂
    ``/api/v1/app/status|heartbeat|shutdown`` 并置 ``app.state.launcher_controller``；
    缺省 ``None`` 时控制面端点按契约降级（status 全 false/0、heartbeat/shutdown 404），
    既有端点/行为零变化。

    ``static_dir`` 缺省按 :func:`resolve_static_dir` 解析（env → CWD → 仓库根 → 包内）；
    目录存在则挂载静态托管 + SPA fallback，缺失则跳过并打 WARNING（API 与既有测试行为不变；
    D-M6-5①/R2 分发方案 A）。组合根仍只负责装配，本文件不做业务计算。

    R-04/R-05：多 worker 环境（``WEB_CONCURRENCY``/``UVICORN_WORKERS``/``GUNICORN_WORKERS``
    >1）且未设 ``TEXTGAME_ALLOW_MULTI_WORKER=true`` 时**启动即抛 RuntimeError**（单进程假设）。
    R-25：代理头信任开关 ``TEXTGAME_TRUST_PROXY``（默认 false）在此读取并注入中间件。
    """
    # R-04/R-05：单进程假设 fail-fast（必须在任何装配/建库动作之前）。
    _assert_single_worker()
    app = FastAPI(
        title="textgame",
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    install_error_handlers(app)
    # R-25：Host 校验（默认仅本机/测试客户端；局域网/反代需显式设置 TEXTGAME_ALLOWED_HOSTS）。
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=_allowed_hosts())
    # R-18：请求体上限（Content-Length 预检 + chunked 实际计数）；存档导入端点按契约
    # 单独放宽到 8MB+报文头预算（N3-048），其余端点保持 1MB。
    app.add_middleware(
        _BodyLimitMiddleware,
        max_bytes=_MAX_REQUEST_BYTES,
        max_import_bytes=_MAX_SAVE_IMPORT_REQUEST_BYTES,
    )
    # R-25：非 loopback 访问要求 TEXTGAME_API_TOKEN（本机/启动器无需 token）；
    # TEXTGAME_TRUST_PROXY=true 时才按代理头首跳判定来源（默认拒绝代理头，防 loopback 绕过）。
    app.add_middleware(
        _AccessTokenMiddleware,
        token=os.environ.get("TEXTGAME_API_TOKEN", "").strip(),
        trust_proxy=_env_flag(TRUST_PROXY_ENV),
    )

    app.include_router(health_router)
    app.include_router(games_router)
    app.include_router(turns_router)
    app.include_router(cards_router)
    app.include_router(saves_router)
    app.include_router(world_books_router)
    app.include_router(scenarios_router)
    app.include_router(settings_router)
    app.include_router(app_control_router)
    if orchestrator is not None:
        app.state.orchestrator = orchestrator
        # N3-048 存档导出/导入：组合根从编排器已持有的仓储/引擎装配只读导出 + 校验后导入
        # 服务（避免改动并行任务中的 orchestration/main.py；GameEngine 仍是唯一写入者）。
        save_repo = getattr(orchestrator, "_repo", None)
        save_engine = getattr(orchestrator, "_engine", None)
        if save_repo is not None and save_engine is not None:
            app.state.save_service = SaveService(repo=save_repo, engine=save_engine)
    if query_service is not None:
        app.state.query_service = query_service
    if audit_service is not None:
        app.state.audit_service = audit_service
    if card_service is not None:
        app.state.card_service = card_service
    if world_book_service is not None:
        app.state.world_book_service = world_book_service
    if scenario_service is not None:
        app.state.scenario_service = scenario_service
    if launcher_controller is not None:
        app.state.launcher_controller = launcher_controller
    dist = resolve_static_dir(static_dir)
    #: 实际挂载的静态目录（``None``=未挂载）；供启动诊断与验收断言读取。
    app.state.static_dir = str(dist) if dist is not None else None
    if dist is not None:
        _register_spa_fallback(app, dist)
    return app
