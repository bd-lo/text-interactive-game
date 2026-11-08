"""pytest 全局 fixtures: 测试客户端与配置缓存隔离。"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from textgame.api.app import create_app
from textgame.config.settings import get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """每个用例前后清空 ``get_settings`` 缓存, 隔离环境变量与 .env 的影响。"""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def app_client() -> Iterator[TestClient]:
    """返回已挂载全部路由的测试客户端; ``with`` 上下文会触发 FastAPI lifespan。"""
    with TestClient(create_app()) as client:
        yield client
