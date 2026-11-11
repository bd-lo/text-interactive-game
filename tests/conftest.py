"""pytest 全局 fixtures: 内存游戏装配、测试客户端与配置缓存隔离。

``game_harness`` 按组合根口径装配一份全新的内存仓储/引擎/编排器，测试可直接断言
``repo.read_events`` 与版本链；``app_client`` 保留 M1 的兼容接口（健康检查等复用）。
``m3_game_harness`` 是 M3 多角色装配（ProposalQueue/Resolver/ActorDirectory/
DeterministicCharacterAgent/AuditService 全接线），供 013/014 集成与回放测试使用。
"""

import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fakes import FakeLLM
from textgame.agents.character_agent import DeterministicCharacterAgent
from textgame.agents.craft_agent import CraftAgent
from textgame.agents.dto import ParseOutcome
from textgame.agents.parser_agent import IntentParseAgent
from textgame.api.app import create_app
from textgame.config.settings import get_settings
from textgame.domain.models import ActionIntent, PerceptionSlice
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

# ---- 测试必须离线（Q9）--------------------------------------------------------
# ``import litellm`` 默认会从 raw.githubusercontent.com 拉取模型成本表
# （litellm/__init__.py 的 get_model_cost_map 调用）；CI 无代理环境下会发起真实连接，
# 被本文件下方的 socket 禁网 guard 拦截后抛 ``RuntimeError``，而 litellm 只捕获
# ``httpx.HTTPError/InvalidURL``，导致 import 失败。上面这些 textgame import 不会导入
# litellm（SDK 只在 ``llm/providers.py`` 函数内延迟 import，且 providers 模块自身也
# setdefault 同名变量），这里在用例首次 import litellm 之前强制使用包内备份表。
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"


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


@dataclass
class M4ProdHarness(M4FreeHarness):
    """M4 生产同构装配（D-M4-13）：M3 编排链路 + 制造工具 + 审计（018 golden 用）。"""

    audit: AuditService


class FreeCraftIntentParser:
    """测试专用确定性解析器：配方名命中→craft_item(recipe)，否则制造关键词→free。

    - ``recipe_names=None``（017 m4_free_harness）：任意输入映射为 craft_item(mode=free)，
      只用于端到端验证自由制造校验/回滚链路；
    - 注入 ``recipe_names``（018 m4_prod_harness）：先走确定性解析器，配方路径优先，
      其次制造关键词→free，其余动作（移动/拿取/查看等）保持原解析结果。
    """

    _CRAFT_WORDS: tuple[str, ...] = ("打造", "锻造", "制作", "制造", "合成", "做个", "做成")

    def __init__(
        self,
        recipe_names: Mapping[str, str] | None = None,
        *,
        free_enabled: bool = True,
    ) -> None:
        self._recipe_names: dict[str, str] | None = (
            dict(recipe_names) if recipe_names is not None else None
        )
        self._free_enabled = free_enabled
        self._base = DeterministicIntentParser(recipe_names=self._recipe_names)

    def _free_outcome(self, text: str) -> ParseOutcome:
        description = text.strip()[:200] or "做一个简单物件"
        args = {"mode": "free", "free_description": description}
        return ParseOutcome(
            intent=ActionIntent(action="craft_item", params=args, confidence=1.0),
            tool="craft_item",
            args=args,
            confidence=1.0,
            source="keyword",
        )

    def parse(
        self,
        text: str,
        *,
        perception: PerceptionSlice,
        ctx: object = None,
    ) -> ParseOutcome:
        if self._recipe_names is None:
            return self._free_outcome(text)
        outcome = self._base.parse(text, perception=perception, ctx=ctx)  # type: ignore[arg-type]
        if outcome.tool == "craft_item":
            return outcome
        if self._free_enabled and any(word in text for word in self._CRAFT_WORDS):
            return self._free_outcome(text)
        return outcome


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


@pytest.fixture(autouse=True)
def _isolate_llm_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """测试默认禁用真实 LLM：即使开发者本机 ``.env`` 有开发 key，也不允许测试自动启用/联网。

    组合根会按「本机有开发 key 则默认启用」判定 LLM；测试必须显式隔离，否则开发者本机
    会读到 ``.env`` 而尝试真实联网（被上面的 Q9 禁网守卫拦截成 RuntimeError）。
    需要测「自动启用」的用例自行 ``monkeypatch.setenv("TEXTGAME_LLM_ENABLED", ...)`` 或
    用 ``Settings(_env_file=None, llm_api_key=...)`` 构造。

    注意：``litellm`` 首次 import 会 ``load_dotenv()`` 把仓库 ``.env`` 写进进程环境
    （Q9 禁网前它已先 import 过），这样后续用例的 ``Settings()`` 仍会读到开发 key；
    因此这里同时清掉 API key，保证每个用例要么显式注入、要么保持离线。
    """
    monkeypatch.setenv("TEXTGAME_LLM_ENABLED", "false")
    monkeypatch.delenv("TEXTGAME_LLM_API_KEY", raising=False)


@pytest.fixture(autouse=True)
def _isolate_free_craft_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """测试不读写开发者本机 ``data/free_craft.json``，也不继承本机自由制造 env。

    需要验证持久化的用例可自行覆盖 ``TEXTGAME_FREE_CRAFT_SETTINGS_FILE`` 或直接构造
    ``FreeCraftSettingsStore(path=...)``。
    """
    monkeypatch.setenv("TEXTGAME_FREE_CRAFT_SETTINGS_FILE", str(tmp_path / "free_craft.json"))
    monkeypatch.delenv("TEXTGAME_FREE_CRAFT_ENABLED", raising=False)
    monkeypatch.delenv("TEXTGAME_ALLOW_RESOLVER_LLM", raising=False)


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


def build_m4_prod_harness() -> tuple[M4ProdHarness, TestClient]:
    """构建 018 golden 用「生产同构」装配（D-M4-13）。

    与 main.py 同构：M3 编排链路（ProposalQueue→Resolver→ToolGate）+ 制造服务 +
    审计服务；character_agents={}（m4 无 NPC Agent，无跨角色冲突），自由制造开关
    打开但生成器为 CraftAgent(FakeLLM)（仅自由 case 注入脚本，不联网）。
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
    rules = RuleEngine()
    actors = ActorDirectory(nonplayer_tags=M3_NONPLAYER_TAGS)
    resolver = Resolver(
        rules=rules,
        services=ToolGateRouter(gate),
        actors=actors,
        save_seed="save-42",
    )
    audit = AuditService(repo)
    parser = IntentParseAgent(
        DisabledLLMClient(),
        FreeCraftIntentParser(catalog.name_index(), free_enabled=True),
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
        character_agents={},
        audit=audit,
    )
    client = TestClient(
        create_app(orchestrator=orchestrator, query_service=query, audit_service=audit)
    )
    harness = M4ProdHarness(
        client=client,
        repo=repo,
        orchestrator=orchestrator,
        query=query,
        craft=craft,
        catalog=catalog,
        generator=generator,
        fake_llm=fake_llm,
        audit=audit,
    )
    return harness, client


@pytest.fixture
def m4_prod_harness() -> Iterator[M4ProdHarness]:
    """M4 生产同构装配 fixture（018 golden 回放用）；每个用例独立内存仓储。"""
    harness, client = build_m4_prod_harness()
    with client:
        yield harness
