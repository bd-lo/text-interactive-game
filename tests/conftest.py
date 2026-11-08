"""pytest 全局 fixtures: 内存游戏装配、测试客户端与配置缓存隔离。

``game_harness`` 按组合根口径装配一份全新的内存仓储/引擎/编排器，测试可直接断言
``repo.read_events`` 与版本链；``app_client`` 保留 M1 的兼容接口（健康检查等复用）。
"""

from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from textgame.api.app import create_app
from textgame.config.settings import get_settings
from textgame.orchestration.direct_gate import DirectGate
from textgame.orchestration.intent import DeterministicIntentParser
from textgame.orchestration.turn_orchestrator import TurnOrchestrator
from textgame.persistence.memory import InMemoryRepository
from textgame.services.action_service import ActionService
from textgame.services.game_engine import GameEngine
from textgame.services.query_service import QueryService


@dataclass
class GameHarness:
    """一套可断言的测试装配：HTTP 客户端 + 内存仓储 + 编排器 + 查询服务。"""

    client: TestClient
    repo: InMemoryRepository
    orchestrator: TurnOrchestrator
    query: QueryService


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """每个用例前后清空 ``get_settings`` 缓存, 隔离环境变量与 .env 的影响。"""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def game_harness() -> Iterator[GameHarness]:
    """按 ``main.py`` 组合根口径装配一套内存游戏应用。"""
    repo = InMemoryRepository()
    engine = GameEngine(repo)
    query = QueryService(repo)
    action = ActionService(query)
    orchestrator = TurnOrchestrator(
        repo=repo,
        engine=engine,
        query=query,
        action=action,
        parser=DeterministicIntentParser(),
        gate=DirectGate(action, query),
    )
    with TestClient(create_app(orchestrator=orchestrator, query_service=query)) as client:
        yield GameHarness(
            client=client,
            repo=repo,
            orchestrator=orchestrator,
            query=query,
        )


@pytest.fixture
def app_client(game_harness: GameHarness) -> TestClient:
    """返回已挂载全部路由的测试客户端（M1 健康检查等用例复用）。"""
    return game_harness.client
