"""进程入口: 组装依赖并暴露 uvicorn 目标 ``textgame.main:app``。

组合根: ``Settings`` → SQLite 仓储（``prepare_db`` + ``init_schema`` + ``SqliteRepository``）
→ ``GameEngine`` → ``QueryService``/``ActionService`` → ``ToolRegistry``（M2 六工具 + M4 craft_item）
→ ``CraftService``（M4 确定性制造，内置配方）
→ ``ToolGate`` → ``Resolver``（M3 纯函数裁决 + ``ToolGateRouter``）→ ``ActorDirectory``
（服务端标签推导）→ ``DeterministicCharacterAgent``（M3 确定性角色 Agent + 场景计划表）
→ ``AuditService`` → ``TurnOrchestrator`` → ``create_app``。本模块是唯一允许跨层 new 对象
的位置 (第 4 章 4.2、第 16 章 16.1)。

M3 采用第 3 章 §3.6 方案 A（离线确定性）：解析/叙事/角色 Agent 默认确定性，真实 LLM 在
M5 接入；``build_llm_client`` 默认返回 ``DisabledLLMClient``，保证默认装配零联网、可复现
(第 12 章 12.1、D-M5-6：``TEXTGAME_LLM_ENABLED=true`` 才构造真实客户端，缺密钥 fail-fast)。
"""

from fastapi import FastAPI

from textgame.agents.character_agent import DeterministicCharacterAgent
from textgame.agents.craft_agent import CraftAgent
from textgame.agents.judge_agent import JudgeAgentLike, LLMJudgeAgent
from textgame.agents.llm_character_agent import LLMCharacterAgent
from textgame.agents.narrator_agent import LLMNarratorAgent
from textgame.agents.parser_agent import IntentParseAgent
from textgame.api.app import create_app
from textgame.config.rng import save_seed_from_settings
from textgame.config.settings import get_settings
from textgame.domain.rules.engine import RuleEngine
from textgame.domain.scenario import M3_NONPLAYER_TAGS, M3_NPC_PLANS, PLAYER_ID
from textgame.launcher_control import LauncherController
from textgame.llm.runtime import RuntimeLLMClient
from textgame.orchestration.intent import DeterministicIntentParser
from textgame.orchestration.narrator import TemplateNarrator
from textgame.orchestration.resolver import Resolver, ToolGateRouter
from textgame.orchestration.turn_orchestrator import TurnOrchestrator
from textgame.persistence.card_repository import SqliteCardRepository
from textgame.persistence.database import init_schema, prepare_db
from textgame.persistence.scenario_repository import SqliteScenarioRepository
from textgame.persistence.sqlite_repository import SqliteRepository
from textgame.persistence.world_book_repository import SqliteWorldBookRepository
from textgame.services.action_service import ActionService
from textgame.services.actor_directory import ActorDirectory
from textgame.services.audit_service import AuditService
from textgame.services.card_service import CardService, LLMCardDraftGenerator
from textgame.services.craft_service import CraftService
from textgame.services.game_engine import GameEngine
from textgame.services.query_service import QueryService
from textgame.services.recipe_catalog import RecipeCatalog
from textgame.services.scenario_service import ScenarioService
from textgame.services.world_book_service import WorldBookService
from textgame.tools.builtin import build_game_tool_registry
from textgame.tools.gate import ToolGate


def _build_app(launcher_controller: LauncherController | None = None) -> FastAPI:
    """装配应用图（SQLite 仓储 + M3 多角色编排 + 离线确定性 Agent + 模板叙事 + 审计）。

    ``launcher_controller`` 为启动器模式加法（``python -m textgame.launcher`` 注入）；
    缺省 ``None`` 时装配/行为与旧路径逐字不变（控制面端点按契约降级）。
    """
    settings = get_settings()
    engine, _session_factory = prepare_db(settings.db_url)
    init_schema(engine)
    repo = SqliteRepository(engine)
    game_engine = GameEngine(repo)
    # H4 世界书：独立资料库（不参与事件溯源）；同一 service 作为 WorldBookProvider 注入查询/编排层。
    world_book_service = WorldBookService(SqliteWorldBookRepository(engine))
    # 用户上传场景：独立资料库 + 运行时钩子索引（服务按房间/NPC 派生 ID 分发目标/结局/伤害/对话）。
    scenario_service = ScenarioService(SqliteScenarioRepository(engine))
    scenario_service.reload()
    scenario_runtime = scenario_service.runtime
    query = QueryService(
        repo,
        world_book_provider=world_book_service,
        scenario_runtime=scenario_runtime,
    )
    action = ActionService(query, scenario_runtime=scenario_runtime)
    catalog = RecipeCatalog.load_builtin()
    # 运行期 LLM（用户需求）：本机 `.env` 开发 key → 默认启用；干净克隆无 key → 离线可玩，
    # 玩家在网页设置页填入 key 后热切换（无需重启）。默认离线行为与旧装配逐字等价。
    llm_client = RuntimeLLMClient(settings)
    # M6 卡生成（028 后端前置）：LLM 草稿生成器常驻；离线时由 LLM_UNAVAILABLE 回退模板兜底。
    card_service = CardService(
        SqliteCardRepository(engine),
        draft_generator=LLMCardDraftGenerator(llm_client),
    )
    craft_service = CraftService(
        catalog,
        generator=CraftAgent(llm_client),
        free_craft_enabled=settings.free_craft_enabled,
    )
    registry = build_game_tool_registry()
    gate = ToolGate(registry=registry, query=query, action=action, craft=craft_service)
    # M5E-2：needs_judge 生产点由 registry 策略派生（当前 0 个 agent_assisted 工具 → 空集；
    # 默认 RuleEngine 行为逐字不变，裁判分支不可达）。
    rules = RuleEngine(agent_assisted_tools=registry.agent_assisted_tools())
    save_seed_default = save_seed_from_settings(settings.seed)
    actors = ActorDirectory(nonplayer_tags=M3_NONPLAYER_TAGS)
    resolver = Resolver(
        rules=rules,
        services=ToolGateRouter(gate),
        actors=actors,
        save_seed=save_seed_default,
    )
    deterministic_character_agent = DeterministicCharacterAgent(M3_NPC_PLANS)
    # 用户需求：Agent 常驻 LLM 版本（内部是运行期客户端），离线时各 Agent 自动回退确定性路径：
    # 叙事→TemplateNarrator、角色→DeterministicCharacterAgent、裁判→确定性裁决。
    # 这样网页里保存 API Key 后当回合即可生效，无需重启服务。
    narrator = LLMNarratorAgent(llm_client, fallback=TemplateNarrator())
    character_agents = {
        actor_id: LLMCharacterAgent(llm_client, fallback=deterministic_character_agent)
        for actor_id in M3_NPC_PLANS
    }
    # M5E-2 裁判接线：needs_judge 生产点当前为空集 → 生产零裁判调用（机制就绪）。
    judge: JudgeAgentLike | None = LLMJudgeAgent(llm_client)
    audit_service = AuditService(repo)
    parser = IntentParseAgent(
        llm_client,
        DeterministicIntentParser(
            recipe_names=catalog.name_index(),
            free_craft_enabled=settings.free_craft_enabled,
        ),
        tool_specs=registry.visible_tools(PLAYER_ID, frozenset({"player"})),
    )
    orchestrator = TurnOrchestrator(
        repo=repo,
        engine=game_engine,
        query=query,
        action=action,
        parser=parser,
        gate=gate,
        narrator=narrator,
        rules=rules,
        save_seed_default=save_seed_default,
        registry=registry,
        resolver=resolver,
        actors=actors,
        character_agents=character_agents,
        audit=audit_service,
        judge=judge,
        world_book_provider=world_book_service,
        scenario_runtime=scenario_runtime,
    )
    app = create_app(
        orchestrator=orchestrator,
        query_service=query,
        audit_service=audit_service,
        card_service=card_service,
        world_book_service=world_book_service,
        scenario_service=scenario_service,
        launcher_controller=launcher_controller,
    )
    # 设置端点（GET/PUT/DELETE /api/v1/settings/llm）读取同一实例：保存 key 后热切换。
    app.state.llm_runtime = llm_client
    return app


app = _build_app()
