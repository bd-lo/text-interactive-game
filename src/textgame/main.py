"""进程入口: 组装依赖并暴露 uvicorn 目标 ``textgame.main:app``。

组合根: ``Settings`` → ``InMemoryRepository`` → ``GameEngine`` → ``QueryService``/
``ActionService`` → ``ToolRegistry``（M2 六工具声明）→ ``IntentParseAgent``（M2 默认
``DisabledLLMClient`` 离线 + 确定性关键词兜底）→ ``ToolGate`` → ``TurnOrchestrator`` →
``create_app``。M2 005 的仓储是内存实现 (008 换成 SQLite)；本模块是唯一允许跨层
new 对象的位置 (第 4 章 4.2、第 16 章 16.1)。

M2 采用第 3 章 §3.6 方案 A：解析/叙事默认确定性，真实 LLM 在 M5 接入；
``DisabledLLMClient`` 保证默认装配零联网、可复现 (第 12 章 12.1)。
"""

from fastapi import FastAPI

from textgame.agents.parser_agent import IntentParseAgent
from textgame.api.app import create_app
from textgame.config.rng import save_seed_from_settings
from textgame.config.settings import get_settings
from textgame.domain.scenario import PLAYER_ID
from textgame.llm.client import DisabledLLMClient
from textgame.orchestration.intent import DeterministicIntentParser
from textgame.orchestration.turn_orchestrator import TurnOrchestrator
from textgame.persistence.memory import InMemoryRepository
from textgame.services.action_service import ActionService
from textgame.services.game_engine import GameEngine
from textgame.services.query_service import QueryService
from textgame.tools.builtin.m2 import build_m2_tool_registry
from textgame.tools.gate import ToolGate


def _build_app() -> FastAPI:
    """装配 M2 006 应用图（内存仓储 + LLM 解析 Agent 离线兜底 + ToolGate + 模板叙事）。"""
    settings = get_settings()
    repo = InMemoryRepository()
    engine = GameEngine(repo)
    query = QueryService(repo)
    action = ActionService(query)
    registry = build_m2_tool_registry()
    parser = IntentParseAgent(
        DisabledLLMClient(),
        DeterministicIntentParser(),
        tool_specs=registry.visible_tools(PLAYER_ID, frozenset({"player"})),
    )
    orchestrator = TurnOrchestrator(
        repo=repo,
        engine=engine,
        query=query,
        action=action,
        parser=parser,
        gate=ToolGate(registry=registry, query=query, action=action),
        save_seed_default=save_seed_from_settings(settings.seed),
    )
    return create_app(orchestrator=orchestrator, query_service=query)


app = _build_app()
