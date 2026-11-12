"""M5 出口附加项接线验收：解析 free 分支 / LLM 叙事 / LLM 角色（cassette + FakeLLM，离线）。

覆盖：叙事成功落 ``result.narrative`` 且 ``narrative_source="llm"``；Schema 违规 → 恰好一次
修复 → 成功；修复仍失败/离线 → 模板兜底（source=template）且零额外写入；角色 Agent 只吃
私有感知切片、提案进既有 Resolver/队列链路、失败回退确定性计划；解析 free 分支开关关闭
fail-closed、开启时端到端提交；生产组合根默认离线装配逐字等价；cassette 手工合成且无密钥。
全部离线（conftest socket guard + 手工 cassette/FakeLLM，``--record-mode=none`` 语义）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from cassette_transport import CASSETTE_DIR, CassetteTransport
from fakes import FakeLLM
from textgame.agents.character_agent import CharacterAgentLike, DeterministicCharacterAgent
from textgame.agents.llm_character_agent import LLMCharacterAgent
from textgame.agents.narrator_agent import LLMNarratorAgent
from textgame.agents.parser_agent import IntentParseAgent
from textgame.config.settings import get_settings
from textgame.domain.models import PerceptionSlice
from textgame.domain.rules.engine import RuleEngine
from textgame.domain.scenario import (
    ITEM_POTION,
    M3_NONPLAYER_TAGS,
    M3_NPC_PLANS,
    M3_NPC_SCAVENGER_A,
    M3_NPC_SCAVENGER_B,
    PLAYER_ID,
)
from textgame.llm.client import DisabledLLMClient
from textgame.llm.config import LLMClientConfig, load_llm_config
from textgame.llm.litellm_client import LiteLLMClient
from textgame.orchestration.direct_gate import DirectGate
from textgame.orchestration.dto import TurnRequest
from textgame.orchestration.intent import DeterministicIntentParser
from textgame.orchestration.narrator import TemplateNarrator
from textgame.orchestration.resolver import Resolver, ToolGateRouter
from textgame.orchestration.turn_orchestrator import CreateGameRequest, TurnOrchestrator
from textgame.persistence.memory import InMemoryRepository
from textgame.services.action_service import ActionService
from textgame.services.actor_directory import ActorDirectory
from textgame.services.audit_service import AuditService
from textgame.services.dto import TurnResult
from textgame.services.game_engine import GameEngine
from textgame.services.query_service import QueryService
from textgame.services.tool_context import ToolContext
from textgame.tools.builtin.m2 import build_m2_tool_registry
from textgame.tools.gate import ToolGate

pytestmark = pytest.mark.integration

_M2_TEXT = "拿起火把"
_FREE_TEXT = "打造一把趁手的短刀"
_FREE_DRAFT: dict[str, Any] = {
    "item_name": "简易短刀",
    "output_template_id": "item.makeshift_knife",
    "inputs": [
        {"template_id": "item.iron_ingot", "qty": 1, "role": "material"},
        {"template_id": "item.copper_wire", "qty": 1, "role": "material"},
    ],
    "station": None,
    "steps": ["把铜线缠在铁片上", "打磨刃口"],
}


# ---------------------------------------------------------------------------
# 装配辅助
# ---------------------------------------------------------------------------
def _llm_client(cassette: str) -> tuple[LiteLLMClient, CassetteTransport]:
    """用``手工 cassette`` 构造 LiteLLMClient（transport 可注入 → 零网络）。"""
    transport = CassetteTransport(cassette)
    config = LLMClientConfig(
        provider="deepseek",
        model="cassette-model",
        api_base="https://cassette.invalid",
        api_key=SecretStr("cassette-no-secret"),
    )
    return LiteLLMClient(config, transport=transport, llm_config=load_llm_config()), transport


def _cassette_content(name: str, index: int = 0) -> str:
    """读取 cassette 第 index 条交互的响应正文文本。"""
    payload = json.loads((CASSETTE_DIR / name).read_text(encoding="utf-8"))
    return str(payload["interactions"][index]["response"]["body"]["content"])


def _m2_orchestrator(
    *, narrator: Any = None, parser: Any = None
) -> tuple[TurnOrchestrator, InMemoryRepository]:
    """按 M2 单角色口径装配（可注入 LLM 叙事器/解析器）。"""
    repo = InMemoryRepository()
    engine = GameEngine(repo)
    query = QueryService(repo)
    action = ActionService(query)
    orchestrator = TurnOrchestrator(
        repo=repo,
        engine=engine,
        query=query,
        action=action,
        parser=parser or DeterministicIntentParser(),
        gate=DirectGate(action, query),
        narrator=narrator,
    )
    return orchestrator, repo


def _play_m2(orchestrator: TurnOrchestrator, *, text: str = _M2_TEXT) -> TurnResult:
    """创建 M2 存档并执行一回合（确定性 seed=42）。"""
    created = orchestrator.create_game(
        CreateGameRequest(seed=42, idempotency_key="game-wiring-m2-0001")
    )
    return orchestrator.run(
        TurnRequest(
            game_id=created.game_id,
            text=text,
            expected_version=1,
            idempotency_key="turn-wiring-m2-0001",
        )
    )


def _m3_orchestrator(
    *, character_agents: dict[str, CharacterAgentLike]
) -> tuple[TurnOrchestrator, InMemoryRepository]:
    """按 main.py 口径装配 M3 多角色编排器（可注入 LLM/确定性角色 Agent）。"""
    repo = InMemoryRepository()
    engine = GameEngine(repo)
    query = QueryService(repo)
    action = ActionService(query)
    registry = build_m2_tool_registry()
    gate = ToolGate(registry=registry, query=query, action=action)
    rules = RuleEngine()
    actors = ActorDirectory(nonplayer_tags=M3_NONPLAYER_TAGS)
    resolver = Resolver(
        rules=rules, services=ToolGateRouter(gate), actors=actors, save_seed="save-42"
    )
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
    return orchestrator, repo


def _play_m3(orchestrator: TurnOrchestrator) -> TurnResult:
    """创建 scenario=m3 存档并执行第 2 回合（两拾荒者按计划抢药水）。"""
    created = orchestrator.create_game(
        CreateGameRequest(seed=42, idempotency_key="game-wiring-m3-0001", scenario="m3")
    )
    return orchestrator.run(
        TurnRequest(
            game_id=created.game_id,
            text="环顾四周",
            expected_version=1,
            idempotency_key="turn-wiring-m3-0001",
        )
    )


def _event_narrative(result: TurnResult) -> tuple[str, str]:
    """取成功回合首事件的 (narrative, narrative_source)。"""
    assert result.ok and result.events
    event = result.events[0]
    return str(event.result.get("narrative", "")), str(event.result.get("narrative_source", ""))


def _main_app(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    free_enabled: bool = False,
    llm_client: Any = None,
) -> Any:
    """以临时 SQLite + 指定开关构建组合根应用（可注入 FakeLLM 作为唯一 llm_client）。"""
    import textgame.main as main_module  # 延迟导入：避免默认数据目录副作用

    monkeypatch.setenv("TEXTGAME_DB_URL", f"sqlite:///{(tmp_path / 'main.db').as_posix()}")
    monkeypatch.setenv("TEXTGAME_LLM_ENABLED", "false")
    monkeypatch.setenv("TEXTGAME_FREE_CRAFT_ENABLED", "true" if free_enabled else "false")
    if free_enabled:
        # R-19：草稿已迁到进入 Resolver 前的 pre-queue；无头显式开启仍保留历史兼容的
        # 二次确认 TEXTGAME_ALLOW_RESOLVER_LLM（技术风险已消除，开关语义不变）。
        monkeypatch.setenv("TEXTGAME_ALLOW_RESOLVER_LLM", "true")
    if llm_client is not None:
        # 运行期 LLM 改为 RuntimeLLMClient（可热切换）；测试注入替身时直接替换构造结果。
        monkeypatch.setattr(main_module, "RuntimeLLMClient", lambda settings, **kwargs: llm_client)
    get_settings.cache_clear()
    return main_module._build_app()


# ---------------------------------------------------------------------------
# LLM 叙事 Agent（cassette）
# ---------------------------------------------------------------------------
def test_narrator_success_uses_llm_text_and_marks_source() -> None:
    """叙事成功：落库的是 cassette 叙事文本，narrative_source="llm"，长度 80–150。"""
    client, transport = _llm_client("narrate_success.json")
    narrator = LLMNarratorAgent(client, fallback=TemplateNarrator())
    orchestrator, _repo = _m2_orchestrator(narrator=narrator)

    result = _play_m2(orchestrator)

    narrative, source = _event_narrative(result)
    expected = json.loads(_cassette_content("narrate_success.json"))["narrative"]
    assert source == "llm"
    assert narrative == expected
    assert 80 <= len(narrative) <= 150
    assert transport.calls == 1


def test_narrator_schema_violation_is_repaired_once() -> None:
    """首次长度违规 → 恰好 1 次修复 → 第二次合法文本落库；调用恰 2 次。"""
    client, transport = _llm_client("narrate_repair.json")
    narrator = LLMNarratorAgent(client, fallback=TemplateNarrator())
    orchestrator, _repo = _m2_orchestrator(narrator=narrator)

    result = _play_m2(orchestrator)

    narrative, source = _event_narrative(result)
    expected = json.loads(_cassette_content("narrate_repair.json", 1))["narrative"]
    assert source == "llm"
    assert narrative == expected
    assert transport.calls == 2
    assert transport.remaining == 0


def test_narrator_repair_exhausted_falls_back_to_template() -> None:
    """两次都不合格 → 模板兜底（source=template）、回合正常提交、事件结构不变。"""
    client, transport = _llm_client("narrate_invalid_twice.json")
    narrator = LLMNarratorAgent(client, fallback=TemplateNarrator())
    orchestrator, _repo = _m2_orchestrator(narrator=narrator)

    result = _play_m2(orchestrator)

    narrative, source = _event_narrative(result)
    assert source == "template"
    assert 80 <= len(narrative) <= 150
    assert narrative not in {"not-json-at-all", "还是太短"}
    assert transport.calls == 2


def test_narrator_offline_client_falls_back_without_network() -> None:
    """默认离线客户端（DisabledLLMClient）→ 模板兜底，零网络、零异常。"""
    narrator = LLMNarratorAgent(DisabledLLMClient(), fallback=TemplateNarrator())
    orchestrator, _repo = _m2_orchestrator(narrator=narrator)

    result = _play_m2(orchestrator)

    narrative, source = _event_narrative(result)
    assert source == "template"
    assert 80 <= len(narrative) <= 150


# ---------------------------------------------------------------------------
# LLM 角色 Agent（cassette / FakeLLM）：私有感知、提案链路、兜底
# ---------------------------------------------------------------------------
def _character_agents(
    client: Any,
) -> dict[str, CharacterAgentLike]:
    """scavenger A 用 LLM Agent（注入 client+确定性兜底），B 保持确定性。"""
    deterministic = DeterministicCharacterAgent(M3_NPC_PLANS)
    return {
        M3_NPC_SCAVENGER_A: LLMCharacterAgent(client, fallback=deterministic),
        M3_NPC_SCAVENGER_B: deterministic,
    }


def test_character_agent_proposal_enters_existing_resolver_chain() -> None:
    """LLM 角色提案经 ProposalQueue/Resolver 既有链路提交（cassette 合法提案）。"""
    client, transport = _llm_client("character_success.json")
    orchestrator, _repo = _m3_orchestrator(character_agents=_character_agents(client))

    result = _play_m3(orchestrator)

    assert result.ok and result.version == 2
    npc_events = [
        event
        for event in result.events
        if event.actor_id in {M3_NPC_SCAVENGER_A, M3_NPC_SCAVENGER_B}
    ]
    assert len(npc_events) == 1  # 同物品冲突只应用一个
    assert npc_events[0].actor_id == M3_NPC_SCAVENGER_A  # LLM 提案优先于确定性计划
    assert npc_events[0].event_type.value == "ITEM_TAKEN"
    assert npc_events[0].params["item_id"] == ITEM_POTION
    assert transport.calls == 1


def test_character_agent_failure_falls_back_to_deterministic_plan() -> None:
    """两次 Schema 违规 → 确定性 CharacterAgent 兜底（M3 计划仍按原链路提交）。"""
    fake = FakeLLM(["not-json", '{"proposals": "bad"}'])
    orchestrator, _repo = _m3_orchestrator(character_agents=_character_agents(fake))

    result = _play_m3(orchestrator)

    assert result.ok and result.version == 2
    npc_events = [
        event
        for event in result.events
        if event.actor_id in {M3_NPC_SCAVENGER_A, M3_NPC_SCAVENGER_B}
    ]
    assert len(npc_events) == 1
    assert npc_events[0].event_type.value == "ITEM_TAKEN"
    assert len(fake.calls) == 2  # 一次修复链：2 次请求后回退


def test_character_agent_input_contains_only_private_perception() -> None:
    """请求体只含该角色私有感知切片（actor_id/private_memory/local_events），不含 GameState。"""
    fake = FakeLLM([])
    deterministic = DeterministicCharacterAgent(M3_NPC_PLANS)
    agent = LLMCharacterAgent(fake, fallback=deterministic)
    perception = PerceptionSlice(
        actor_id=PLAYER_ID, visible_entities=[], local_events=[], private_memory=["own_private_mem"]
    )

    proposals = agent.propose(perception, turn_id=2, save_seed="save-42")

    assert proposals == []  # 玩家无计划且客户端离线 → 确定性兜底为空
    request = fake.calls[0]
    system = request.messages[0].content
    assert "private_memory" in system
    assert "local_events" in system
    assert "own_private_mem" in system
    assert "GameState" not in system
    assert request.prompt_version == "character@v1"


# ---------------------------------------------------------------------------
# 解析接线：free 分支（fail-closed）+ 生产 HTTP 端到端
# ---------------------------------------------------------------------------
def test_parser_free_branch_is_fail_closed_by_default() -> None:
    """默认 ``free_craft_enabled=False``：自由句 → 无意图；显式开启才产出 free craft 意图。"""
    perception = PerceptionSlice(actor_id=PLAYER_ID)

    closed = DeterministicIntentParser(free_craft_enabled=False).parse(
        _FREE_TEXT, perception=perception
    )
    opened = DeterministicIntentParser(free_craft_enabled=True).parse(
        _FREE_TEXT, perception=perception
    )

    assert closed.tool is None
    assert opened.tool == "craft_item"
    assert opened.args["mode"] == "free"
    assert _FREE_TEXT in opened.args["free_description"]


def test_parser_llm_free_intent_uses_cassette() -> None:
    """解析 Agent 的 LLM 路径可产出 free 意图（cassette 合法 ParseOutput，白名单含 craft_item）。"""
    client, transport = _llm_client("intent_parse_free.json")
    agent = IntentParseAgent(
        client,
        DeterministicIntentParser(free_craft_enabled=True),
        tool_whitelist=frozenset({"craft_item", "get_room_view"}),
    )

    outcome = agent.parse(
        _FREE_TEXT,
        perception=PerceptionSlice(actor_id=PLAYER_ID),
        ctx=ToolContext(
            actor_id=PLAYER_ID,
            turn_id=2,
            save_seed="save-42",
            idempotency_key="idem-wiring-parse-0001",
            caller="player",
        ),
    )

    assert outcome.tool == "craft_item"
    assert outcome.source == "llm"
    assert outcome.args["mode"] == "free"
    assert transport.calls == 1


def test_production_http_free_disabled_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """生产 HTTP：开关关闭时自由句被拒（INVALID_INPUT）、零事件、版本不变。"""
    app = _main_app(monkeypatch, tmp_path, free_enabled=False)
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/games", headers={"Idempotency-Key": "game-wiring-http-0001"}, json={"seed": 42}
        ).json()
        game_id = created["game_id"]
        before = client.get(f"/api/v1/games/{game_id}").json()
        response = client.post(
            "/api/v1/turns",
            headers={"Idempotency-Key": "turn-wiring-http-0001"},
            json={"game_id": game_id, "input": _FREE_TEXT, "expected_version": 1},
        )
        body = response.json()
        after = client.get(f"/api/v1/games/{game_id}").json()

    assert response.status_code == 200
    assert body["ok"] is False
    assert body["error_code"] == "INVALID_INPUT"
    assert body["events"] == []
    assert after["version"] == before["version"] == 1
    assert after["turn"] == before["turn"] == 1


def test_production_http_free_enabled_commits_with_fake_llm(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """生产 HTTP：开关开启 + FakeLLM → free 意图经确定性解析兜底 → CRAFT_GENERATED 提交。"""
    # 前 2 条给解析 Agent（两次非法 → 确定性 free 兜底），第 3 条给 CraftAgent 生成草案
    fake = FakeLLM(["not-json", "still-not-json", _FREE_DRAFT])
    app = _main_app(monkeypatch, tmp_path, free_enabled=True, llm_client=fake)
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/games",
            headers={"Idempotency-Key": "game-wiring-http-0002"},
            json={"seed": 1, "scenario": "m4"},
        ).json()
        response = client.post(
            "/api/v1/turns",
            headers={"Idempotency-Key": "turn-wiring-http-0002"},
            json={
                "game_id": created["game_id"],
                "input": _FREE_TEXT,
                "expected_version": 1,
            },
        )
        body = response.json()

    assert body["ok"] is True, body
    assert [event["event_type"] for event in body["events"]] == ["CRAFT_GENERATED"]
    assert len(fake.calls) >= 3


def test_main_default_offline_assembly_is_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """默认离线组合根：LLM Agent 常驻但全部回退确定性路径，回合结果确定性可玩（零联网）。

    用户需求（2026-11-11 运行期 LLM 凭据）后装配改为「Agent 常驻 LLM 版本 + 内部运行期客户端」：
    ``TEXTGAME_LLM_ENABLED=false`` 时仍必须零 LLM 调用、文案与旧离线装配一致（回退路径）。
    """
    app = _main_app(monkeypatch, tmp_path, free_enabled=False)
    orchestrator = app.state.orchestrator
    runtime = app.state.llm_runtime
    assert runtime.status().enabled is False
    assert type(orchestrator._narrator).__name__ == "LLMNarratorAgent"
    assert type(orchestrator._narrator._fallback).__name__ == "TemplateNarrator"
    assert all(
        type(agent).__name__ == "LLMCharacterAgent"
        for agent in orchestrator._character_agents.values()
    )
    assert type(orchestrator._judge).__name__ == "LLMJudgeAgent"

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/games", headers={"Idempotency-Key": "game-wiring-http-0003"}, json={"seed": 42}
        ).json()
        response = client.post(
            "/api/v1/turns",
            headers={"Idempotency-Key": "turn-wiring-http-0003"},
            json={"game_id": created["game_id"], "input": _M2_TEXT, "expected_version": 1},
        ).json()

    assert response["ok"] is True
    assert 80 <= len(response["text"]) <= 150


# ---------------------------------------------------------------------------
# cassette 纪律：手工合成 / 无密钥
# ---------------------------------------------------------------------------
def test_cassettes_are_handwritten_and_secret_free() -> None:
    """cassette 全部为手工合成标记，且不含密钥/授权头/环境变量转储。"""
    forbidden = ("sk-", "api_key", "authorization", "bearer ", ".env", "player_input")
    cassette_files = sorted(CASSETTE_DIR.glob("*.json"))
    assert cassette_files, "cassette fixtures 不得为空"
    for path in cassette_files:
        text = path.read_text(encoding="utf-8").lower()
        for marker in forbidden:
            assert marker not in text, f"{path.name} 含敏感标记 {marker!r}"
        payload = json.loads(text)
        assert payload["recorded_with"] == "synthetic-handwritten"
        assert payload["interactions"]
