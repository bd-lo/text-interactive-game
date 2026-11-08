"""应用工厂: 挂载路由与 API 文档, 不写业务逻辑、不建数据库。

组合根之一, 允许在此跨层组装对象, 见开发文档第 4 章 4.2、第 16 章 16.1。
"""

from fastapi import FastAPI

from textgame import __version__
from textgame.api.routes.health import router as health_router


def create_app() -> FastAPI:
    """创建 FastAPI 应用实例; uvicorn 入口见 ``textgame.main:app``。"""
    app = FastAPI(
        title="textgame",
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.include_router(health_router)
    return app
