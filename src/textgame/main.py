"""进程入口: 组装依赖并暴露 uvicorn 目标 ``textgame.main:app``。

组合根: ``Settings`` → SQLite 仓储（``prepare_db`` + ``ensure_schema`` + ``SqliteRepository``）
→ ``GameEngine`` → ``QueryService``/``ActionService`` → ``ToolRegistry``（玩家可见 8 工具）
→ ``CraftService``（确定性配方 + 可选自由制造）
→ ``ToolGate`` → ``Resolver``（纯函数裁决 + ``ToolGateRouter``）→ ``ActorDirectory``
→ ``RuntimeLLMClient`` 运行期门面（LLM 解析/叙事/角色/裁判/制造 Agent，失败回退确定性）
→ ``AuditService`` → ``TurnOrchestrator`` → ``create_app``。本模块是唯一允许跨层 new 对象
的位置 (第 4 章 4.2、第 16 章 16.1)。

M3 采用第 3 章 §3.6 方案 A（离线确定性）：解析/叙事/角色 Agent 默认确定性；M5 起 LLM
经 ``RuntimeLLMClient`` 运行期门面接入（有 key 自动启用、显式 false 强制离线、显式 true 无
key 时降级离线并如实报告；``llm/factory.py`` 的「启用缺 key fail-fast」仅保留为历史/测试
路径）。无 key 或 LLM 失败时各 Agent 回退确定性路径，保证默认装配零联网、可复现
(第 12 章 12.1、D-M5-6、D-LLM-1；2026-11-12 真实运行期实测口径见第 12 章备注)。
"""

import logging

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
from textgame.persistence.database import ensure_schema, prepare_db
from textgame.persistence.free_craft_settings import FreeCraftSettingsStore
from textgame.persistence.scenario_repository import SqliteScenarioRepository
from textgame.persistence.sqlite_repository import SqliteRepository
from textgame.persistence.world_book_repository import SqliteWorldBookRepository
from textgame.services.action_service import ActionService
from textgame.services.actor_directory import ActorDirectory
from textgame.services.audit_service import AuditService
from textgame.services.card_service import CardService, LLMCardDraftGenerator
from textgame.services.craft_service import CraftService
from textgame.services.free_craft import FreeCraftSwitch
from textgame.services.game_engine import GameEngine
from textgame.services.query_service import QueryService
from textgame.services.recipe_catalog import RecipeCatalog
from textgame.services.scenario_service import ScenarioService
from textgame.services.world_book_service import WorldBookService
from textgame.tools.builtin import build_game_tool_registry
from textgame.tools.gate import ToolGate

_LOGGER = logging.getLogger(__name__)


def _build_app(launcher_controller: LauncherController | None = None) -> FastAPI:
    """装配应用图（SQLite 仓储 + M3 多角色编排 + 离线确定性 Agent + 模板叙事 + 审计）。

    ``launcher_controller`` 为启动器模式加法（``python -m textgame.launcher`` 注入）；
    缺省 ``None`` 时装配/行为与旧路径逐字不变（控制面端点按契约降级）。
    """
    settings = get_settings()
    # 网页设置页持久化的自由制造偏好优先于 env（用户关掉后不会被 env 默认值重新打开）；
    # 无偏好文件时继续沿用 env + R-19 fail-fast，保证无头部署的显式 opt-in 语义不变。
    free_craft_store = FreeCraftSettingsStore(settings.free_craft_settings_file)
    stored_free_craft = free_craft_store.load()
    if stored_free_craft is not None:
        free_craft_source = "user"
        free_craft_risk_accepted = stored_free_craft.risk_accepted
        free_craft_enabled = stored_free_craft.enabled and free_craft_risk_accepted
        if stored_free_craft.enabled and not free_craft_risk_accepted:
            _LOGGER.warning("自由制造偏好缺少风险确认，按关闭处理：%s", free_craft_store.path)
    elif settings.free_craft_enabled:
        if not settings.allow_resolver_llm:
            # R-19：自由制造会在 Resolver 内调 LLM（CAS 重试可能重复扣费），无头默认禁止。
            raise RuntimeError(
                "TEXTGAME_FREE_CRAFT_ENABLED=true 需要在 Resolver 内调用 LLM（已知 K4 风险）。"
                "无头/生产请同时设置 TEXTGAME_ALLOW_RESOLVER_LLM=true 表示接受；"
                "或者在网页设置页开启自由制造（前端二次确认等价于运行期 opt-in）。"
            )
        free_craft_source = "env"
        free_craft_enabled = True
        free_craft_risk_accepted = True
    else:
        free_craft_source = "default"
        free_craft_enabled = False
        free_craft_risk_accepted = settings.allow_resolver_llm
    free_craft_switch = FreeCraftSwitch(
        enabled=free_craft_enabled, risk_accepted=free_craft_risk_accepted
    )
    engine, _session_factory = prepare_db(settings.db_url)
    # R-05：启动期自动检测旧库并升级（健康库/内存库 no-op），避免升级用户建局/读视图 500。
    ensure_schema(engine, db_url=settings.db_url)
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
        free_craft_switch=free_craft_switch,
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
            free_craft_switch=free_craft_switch,
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
    # 自由制造运行期开关（与 CraftService/解析器共享）+ 本机偏好持久化。
    app.state.free_craft_switch = free_craft_switch
    app.state.free_craft_store = free_craft_store
    app.state.free_craft_source = free_craft_source
    return app


app = _build_app()
