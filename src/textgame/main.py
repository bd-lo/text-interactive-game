"""进程入口: 组装依赖并暴露 uvicorn 目标 ``textgame.main:app``。

组合根: ``Settings`` → ``InMemoryRepository`` → ``GameEngine`` → ``TurnOrchestrator`` →
``create_app``。M2 005 的仓储是内存实现 (008 换成 SQLite)；本模块是唯一允许跨层
new 对象的位置 (第 4 章 4.2、第 16 章 16.1)。

启动命令: ``uv run uvicorn textgame.main:app --reload --port 8000``
"""

from fastapi import FastAPI

from textgame.api.app import create_app
from textgame.config.rng import save_seed_from_settings
from textgame.config.settings import get_settings
from textgame.orchestration.direct_gate import DirectGate
from textgame.orchestration.intent import DeterministicIntentParser
from textgame.orchestration.turn_orchestrator import TurnOrchestrator
from textgame.persistence.memory import InMemoryRepository
from textgame.services.action_service import ActionService
from textgame.services.game_engine import GameEngine
from textgame.services.query_service import QueryService


def _build_app() -> FastAPI:
    """装配 M2 005 的应用图（内存仓储 + 确定性解析 + 模板叙事）。"""
    settings = get_settings()
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
        save_seed_default=save_seed_from_settings(settings.seed),
    )
    return create_app(orchestrator=orchestrator, query_service=query)


app = _build_app()
