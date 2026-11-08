"""应用工厂: 挂载路由与异常处理器, 不写业务逻辑、不建数据库。

组合根之一, 允许在此跨层组装对象, 见开发文档第 4 章 4.2、第 16 章 16.1。
编排器与查询服务由 ``main.py`` / 测试 fixture 注入 (``create_app(orchestrator=...)``),
本文件只负责挂载与异常映射。
"""

from fastapi import FastAPI

from textgame import __version__
from textgame.api.errors import install_error_handlers
from textgame.api.routes.games import router as games_router
from textgame.api.routes.health import router as health_router
from textgame.api.routes.turns import router as turns_router
from textgame.orchestration.turn_orchestrator import TurnOrchestrator
from textgame.services.query_service import QueryService


def create_app(
    *,
    orchestrator: TurnOrchestrator | None = None,
    query_service: QueryService | None = None,
) -> FastAPI:
    """创建 FastAPI 应用实例; 可选注入编排器与查询服务 (组合根负责装配)。"""
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
    if orchestrator is not None:
        app.state.orchestrator = orchestrator
    if query_service is not None:
        app.state.query_service = query_service
    return app
