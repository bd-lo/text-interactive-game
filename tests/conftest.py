"""pytest 全局 fixtures: 内存游戏装配、测试客户端与配置缓存隔离。

``game_harness`` 按组合根口径装配一份全新的内存仓储/引擎/编排器，测试可直接断言
``repo.read_events`` 与版本链；``app_client`` 保留 M1 的兼容接口（健康检查等复用）。
``m3_game_harness`` 是 M3 多角色装配（ProposalQueue/Resolver/ActorDirectory/
DeterministicCharacterAgent/AuditService 全接线），供 013/014 集成与回放测试使用。
"""

from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from fakes import FakeLLM
from textgame.agents.character_agent import DeterministicCharacterAgent
from textgame.agents.craft_agent import CraftAgent
from textgame.agents.dto import ParseOutcome
from textgame.agents.parser_agent import IntentParseAgent
from textgame.api.app import create_app
from textgame.config.settings import get_settings
from textgame.domain.models import ActionIntent
from textgame.domain.rules.engine import RuleEngine
from textgame.domain.scenario import M3_NONPLAYER_TAGS, M3_NPC_PLANS, PLAYER_ID
from textgame.llm.client import DisabledLLMClient
from textgame.orchestration.direct_gate import DirectGate
from textgame.orchestration.intent import DeterministicIntentParser
from textgame.orchestration.narrator import TemplateNarrator
from textgame.orchestration.resolver import Resolver, ToolGateRouter
from textgame.orchestration.turn_orchestrator import TurnOrchestrator
from textgame.persistence.memory import InMemoryRepository
from textgame.services.action_service import ActionService
from textgame.services.actor_directory import ActorDirectory
from textgame.services.audit_service import AuditService
from textgame.services.craft_service import CraftService
from textgame.services.game_engine import GameEngine
from textgame.services.query_service import QueryService
from textgame.services.recipe_catalog import RecipeCatalog
from textgame.tools.builtin import build_game_tool_registry
from textgame.tools.builtin.m2 import build_m2_tool_registry
from textgame.tools.gate import ToolGate


@dataclass
class GameHarness:
    """一套可断言的测试装配：HTTP 客户端 + 内存仓储 + 编排器 + 查询服务。"""

    client: TestClient
    repo: InMemoryRepository
    orchestrator: TurnOrchestrator
    query: QueryService


@dataclass
class M3GameHarness(GameHarness):
    """M3 多角色装配额外暴露审计服务（审计查询/回放断言用）。"""

    audit: AuditService


@dataclass
class M4GameHarness(GameHarness):
    """M4 制造装配额外暴露制造服务与配方目录（确定性制造集成/单元断言用）。"""

    craft: CraftService
    catalog: RecipeCatalog


@dataclass
class M4FreeHarness(M4GameHarness):
    """M4 自由制造装配额外暴露 CraftAgent 与 FakeLLM（脚本可注入，017 用）。"""

    generator: CraftAgent
    fake_llm: FakeLLM


class FreeCraftIntentParser:
    """017 测试专用确定性解析器：任意输入映射为 craft_item(mode=free)。

    只用于端到端验证自由制造校验/回滚链路（不进入生产装配）；真正的意图解析仍由
    006/016 的解析器负责。
    """

    def parse(
        self,
        text: str,
        *,
        perception: object = None,
        ctx: object = None,
    ) -> ParseOutcome:
        del perception, ctx
        description = text.strip()[:200] or "做一个简单物件"
        args = {"mode": "free", "free_description": description}
        return ParseOutcome(
            intent=ActionIntent(action="craft_item", params=args, confidence=1.0),
            tool="craft_item",
            args=args,
            confidence=1.0,
            source="keyword",
        )


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """每个用例前后清空 ``get_settings`` 缓存, 隔离环境变量与 .env 的影响。"""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _block_real_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Q9 定案：测试套件默认禁网（只放行本机回环，防真实 LLM/外网调用）。

    通过拦截 ``socket.socket.connect`` 实现：任何非 127.0.0.1/::1/localhost 的连接抛
    ``RuntimeError``。真实 LLM 路径必须用 ``FakeLLM``/cassette（第 12、18 章）。
    """
    import socket as socket_module

    real_connect = socket_module.socket.connect
    loopback = {"127.0.0.1", "::1", "localhost"}

    def guarded_connect(self: socket_module.socket, address: object) -> None:
        host = address[0] if isinstance(address, tuple) and address else None
        if host in loopback:
            return real_connect(self, address)  # type: ignore[arg-type]
        raise RuntimeError(
            f"network access is blocked in the test suite (Q9): {address!r}; "
            "use FakeLLM/cassette instead"
        )

    monkeypatch.setattr(socket_module.socket, "connect", guarded_connect)


@pytest.fixture
def game_harness() -> Iterator[GameHarness]:
    """按 ``main.py`` 组合根口径装配一套内存游戏应用（M2 单角色路径）。"""
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
def m3_game_harness() -> Iterator[M3GameHarness]:
    """按 ``main.py`` 组合根口径装配 M3 多角色内存应用（确定性、零联网）。"""
    repo = InMemoryRepository()
    engine = GameEngine(repo)
    query = QueryService(repo)
    action = ActionService(query)
    registry = build_m2_tool_registry()
    gate = ToolGate(registry=registry, query=query, action=action)
    rules = RuleEngine()
    actors = ActorDirectory(nonplayer_tags=M3_NONPLAYER_TAGS)
    resolver = Resolver(
        rules=rules,
        services=ToolGateRouter(gate),
        actors=actors,
        save_seed="save-42",
    )
    character_agent = DeterministicCharacterAgent(M3_NPC_PLANS)
    character_agents = dict.fromkeys(M3_NPC_PLANS, character_agent)
    audit = AuditService(repo)
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
        gate=gate,
        narrator=TemplateNarrator(),
        rules=rules,
        save_seed_default="save-42",
        registry=registry,
        resolver=resolver,
        actors=actors,
        character_agents=character_agents,
        audit=audit,
    )
    with TestClient(
        create_app(orchestrator=orchestrator, query_service=query, audit_service=audit)
    ) as client:
        yield M3GameHarness(
            client=client,
            repo=repo,
            orchestrator=orchestrator,
            query=query,
            audit=audit,
        )


@pytest.fixture
def app_client(game_harness: GameHarness) -> TestClient:
    """返回已挂载全部路由的测试客户端（M1 健康检查等用例复用）。"""
    return game_harness.client


@pytest.fixture
def m4_game_harness() -> Iterator[M4GameHarness]:
    """M2 同构单角色装配 + 内置配方/CraftService + scenario=m4（无 NPC Agent）。"""
    repo = InMemoryRepository()
    engine = GameEngine(repo)
    query = QueryService(repo)
    action = ActionService(query)
    catalog = RecipeCatalog.load_builtin()
    craft = CraftService(catalog)
    registry = build_game_tool_registry()
    gate = ToolGate(registry=registry, query=query, action=action, craft=craft)
    parser = IntentParseAgent(
        DisabledLLMClient(),
        DeterministicIntentParser(recipe_names=catalog.name_index()),
        tool_specs=registry.visible_tools(PLAYER_ID, frozenset({"player"})),
    )
    orchestrator = TurnOrchestrator(
        repo=repo,
        engine=engine,
        query=query,
        action=action,
        parser=parser,
        gate=gate,
        narrator=TemplateNarrator(),
        rules=RuleEngine(),
        save_seed_default="save-42",
    )
    with TestClient(create_app(orchestrator=orchestrator, query_service=query)) as client:
        yield M4GameHarness(
            client=client,
            repo=repo,
            orchestrator=orchestrator,
            query=query,
            craft=craft,
            catalog=catalog,
        )


@pytest.fixture
def m4_free_harness() -> Iterator[M4FreeHarness]:
    """M4 自由制造装配：free_craft_enabled=True + CraftAgent(FakeLLM) + free 解析器。

    与 m4_game_harness 同构（M2 单角色路径 + scenario=m4）；FakeLLM 脚本由用例通过
    harness.fake_llm.reset([...]) 注入，保证不联网、可复现。
    """
    repo = InMemoryRepository()
    engine = GameEngine(repo)
    query = QueryService(repo)
    action = ActionService(query)
    catalog = RecipeCatalog.load_builtin()
    fake_llm = FakeLLM()
    generator = CraftAgent(fake_llm)
    craft = CraftService(catalog, generator=generator, free_craft_enabled=True)
    registry = build_game_tool_registry()
    gate = ToolGate(registry=registry, query=query, action=action, craft=craft)
    orchestrator = TurnOrchestrator(
        repo=repo,
        engine=engine,
        query=query,
        action=action,
        parser=FreeCraftIntentParser(),
        gate=gate,
        narrator=TemplateNarrator(),
        rules=RuleEngine(),
        save_seed_default="save-42",
    )
    with TestClient(create_app(orchestrator=orchestrator, query_service=query)) as client:
        yield M4FreeHarness(
            client=client,
            repo=repo,
            orchestrator=orchestrator,
            query=query,
            craft=craft,
            catalog=catalog,
            generator=generator,
            fake_llm=fake_llm,
        )
