"""M5E-2 裁判 Agent 接线集成验收（默认离线等价 + needs_judge 分支 + cassette 零网络）。

装配口径与 ``main.py`` 同构（内存仓储 + 真实 GameEngine/Query/Action/ToolGate/Resolver/
Audit + 确定性解析/模板叙事；可注入 ``judge`` 与测试专用 agent_assisted 策略）。覆盖：
1. 默认离线（``llm_enabled=false``）生产组合根无裁判、零调用；
2. ``judge=None``（省略/显式）裁决结果逐字等价（默认路径零回归）；
3. needs_judge + 合法证据 → applied 且 audit 可追溯裁判理由；
4. needs_judge + deny / 低置信 / LLM 不可用 / 非法 Schema×2 / 证据越界 / 裁判异常 →
   确定性保守兜底：rejected、零事件、零写入、不 5xx/不外泄异常；
5. 每回合裁判调用上限 1（m3 双 NPC 提案同回合）；
6. 生产 ``llm_enabled=true`` 装配：构造 LLMJudgeAgent 但 needs_judge 集合为空 → 零裁判调用。
零网络：cassette（``tests/fixtures/cassettes/judge_*.json``）/FakeLLM + conftest socket guard。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from cassette_transport import CassetteTransport
from fakes import FakeLLM
from textgame import console
from textgame.agents.character_agent import DeterministicCharacterAgent
from textgame.agents.judge_agent import DeterministicJudgeAgent, LLMJudgeAgent
from textgame.agents.parser_agent import IntentParseAgent
from textgame.config.settings import get_settings
from textgame.domain.hashing import state_hash
from textgame.domain.judge import JudgeOutput
from textgame.domain.models import GameState, ResolutionPolicy
from textgame.domain.rules.engine import RuleEngine
from textgame.domain.scenario import (
    M3_NONPLAYER_TAGS,
    M3_NPC_PLANS,
    PLAYER_ID,
)
from textgame.llm.client import DisabledLLMClient
from textgame.llm.config import LLMClientConfig, load_llm_config
from textgame.llm.litellm_client import LiteLLMClient
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
from textgame.tools.builtin.m2 import build_m2_tool_registry
from textgame.tools.gate import ToolGate
from textgame.tools.registry import ToolRegistry

pytestmark = [pytest.mark.integration]

_SAVE_SEED = "save-42"
_TAKE_TORCH = "拿起火把"
_JUDGE_REASON = "物品就在他脚边，取用没有阻碍。"


# ---------------------------------------------------------------------------
# 装配辅助
# ---------------------------------------------------------------------------
def _llm_client(cassette: str) -> tuple[LiteLLMClient, CassetteTransport]:
    """手工 cassette + ``LiteLLMClient``（transport 可注入 → 零网络，D-M5-13 口径）。"""
    transport = CassetteTransport(cassette)
    config = LLMClientConfig(
        provider="deepseek",
        model="cassette-model",
        api_base="https://cassette.invalid",
        api_key=SecretStr("cassette-no-secret"),
    )
    return LiteLLMClient(config, transport=transport, llm_config=load_llm_config()), transport


def _agent_assisted_registry(*tool_names: str) -> ToolRegistry:
    """测试专用策略注入：把指定工具标记为 ``agent_assisted``（不改生产工具声明）。"""
    registry = build_m2_tool_registry()
    for name in tool_names:
        spec = registry.get(name)
        registry._specs[name] = spec.model_copy(
            update={"resolution_policy": ResolutionPolicy.AGENT_ASSISTED}
        )
    return registry


def _judge_output(**overrides: object) -> JudgeOutput:
    payload: dict[str, object] = {
        "verdict": "allow",
        "reason": _JUDGE_REASON,
        "evidence_refs": ["火把"],
        "confidence": 0.9,
    }
    payload.update(overrides)
    return JudgeOutput.model_validate(payload)


@dataclass
class _Harness:
    """与生产组合根同构的多角色编排 harness（可注入裁判与 agent_assisted 策略）。"""

    orchestrator: TurnOrchestrator
    repo: InMemoryRepository
    audit: AuditService
    registry: ToolRegistry
    rules: RuleEngine
    game_id: str
    judge: Any

    def play(self, text: str = _TAKE_TORCH) -> TurnResult:
        """推进第 2 回合（玩家输入 → 解析 → 提案 → 裁判 → 裁决 → 提交）。"""
        return self.orchestrator.run(
            TurnRequest(
                game_id=self.game_id,
                text=text,
                expected_version=1,
                idempotency_key=f"judge-turn-{uuid.uuid4().hex}",
            )
        )

    def write_snapshot(self) -> tuple[int, int, str, int]:
        """零写入断言四元组：(version, 事件数, state_hash, last_seq)。"""
        record = self.repo.load(self.game_id)
        assert record is not None
        return (
            record.version,
            len(self.repo.read_events(self.game_id)),
            state_hash(record.state),
            record.last_seq,
        )

    def audit_rows(self, turn_id: int = 2) -> list[Any]:
        """本回合审计行（只读）。"""
        return self.audit.query(self.game_id, turn_id=turn_id)


def _harness(
    *,
    judge: Any = None,
    agent_assisted: tuple[str, ...] = (),
    scenario: str = "m2",
) -> _Harness:
    """按 ``main.py`` 同构装配（不联网、不读配置）；``agent_assisted`` 为空 = 默认策略。"""
    repo = InMemoryRepository()
    engine = GameEngine(repo)
    query = QueryService(repo)
    action = ActionService(query)
    registry = (
        _agent_assisted_registry(*agent_assisted) if agent_assisted else build_m2_tool_registry()
    )
    gate = ToolGate(registry=registry, query=query, action=action)
    rules = RuleEngine(agent_assisted_tools=frozenset(agent_assisted))
    actors = ActorDirectory(nonplayer_tags=M3_NONPLAYER_TAGS)
    resolver = Resolver(
        rules=rules, services=ToolGateRouter(gate), actors=actors, save_seed=_SAVE_SEED
    )
    parser = IntentParseAgent(
        DisabledLLMClient(),
        DeterministicIntentParser(),
        tool_specs=registry.visible_tools(PLAYER_ID, frozenset({"player"})),
    )
    audit = AuditService(repo)
    character_agents = (
        {actor_id: DeterministicCharacterAgent(M3_NPC_PLANS) for actor_id in M3_NPC_PLANS}
        if scenario == "m3"
        else {}
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
        save_seed_default=_SAVE_SEED,
        registry=registry,
        resolver=resolver,
        actors=actors,
        character_agents=character_agents,
        audit=audit,
        judge=judge,
    )
    created = orchestrator.create_game(
        CreateGameRequest(
            seed=42,
            idempotency_key=f"judge-game-{uuid.uuid4().hex}",
            scenario=scenario,
        )
    )
    return _Harness(
        orchestrator=orchestrator,
        repo=repo,
        audit=audit,
        registry=registry,
        rules=rules,
        game_id=created.game_id,
        judge=judge,
    )


def _main_app(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any, *, llm_enabled: bool, llm_client: Any = None
) -> Any:
    """构建生产组合根（临时 SQLite）；``llm_enabled`` 两态可测。"""
    import textgame.main as main_module

    monkeypatch.setenv("TEXTGAME_DB_URL", f"sqlite:///{(tmp_path / 'judge-main.db').as_posix()}")
    monkeypatch.setenv("TEXTGAME_LLM_ENABLED", "true" if llm_enabled else "false")
    monkeypatch.setenv("TEXTGAME_FREE_CRAFT_ENABLED", "false")
    if llm_client is not None:
        monkeypatch.setattr(main_module, "build_llm_client", lambda settings: llm_client)
    get_settings.cache_clear()
    return main_module._build_app()


class _RaisingJudge:
    """裁判异常替身：验证编排层不会因裁判失败而中断回合。"""

    def evaluate(
        self, perception: Any, proposal: Any, *, turn_id: int, save_seed: str
    ) -> JudgeOutput:
        raise RuntimeError("judge exploded")


def _hint_text(result: TurnResult) -> str:
    """失败回合 effects.hint 拼接。"""
    hints = [str(effect.get("hint", "")) for effect in result.effects]
    return " ".join(hints)


def _normalized_result(result: TurnResult) -> dict[str, Any]:
    """去掉盖章时间戳（``ts`` 为真实时钟，不参与确定性对比）后的结果快照。"""
    dumped = result.model_dump(mode="json")
    dumped["events"] = [
        {key: value for key, value in event.items() if key != "ts"}
        for event in dumped.get("events", [])
    ]
    return dumped


# ---------------------------------------------------------------------------
# 1) 默认离线 / judge=None 等价
# ---------------------------------------------------------------------------
def test_default_offline_assembly_has_no_judge_and_zero_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """``llm_enabled=false`` 生产组合根：不构造裁判、agent_assisted 空集、回合正常。"""
    app = _main_app(monkeypatch, tmp_path, llm_enabled=False)
    try:
        orchestrator = app.state.orchestrator
        assert orchestrator._judge is None
        assert orchestrator._registry.agent_assisted_tools() == frozenset()
        assert orchestrator._rules.agent_assisted_tools == frozenset()
        with TestClient(app) as client:
            created = client.post(
                "/api/v1/games",
                headers={"Idempotency-Key": f"judge-game-{uuid.uuid4().hex}"},
                json={"seed": 42},
            ).json()
            turn = client.post(
                "/api/v1/turns",
                headers={"Idempotency-Key": f"judge-turn-{uuid.uuid4().hex}"},
                json={
                    "game_id": created["game_id"],
                    "input": _TAKE_TORCH,
                    "expected_version": created["version"],
                },
            ).json()
    finally:
        console.dispose_app_engine(app)

    assert turn["ok"] is True
    assert [event["event_type"] for event in turn["events"]] == ["ITEM_TAKEN"]


def test_judge_none_is_bitwise_equivalent_to_omitted() -> None:
    """省略 judge（默认）与显式 ``judge=None``：同一回合 TurnResult 逐字相等。"""
    implicit = _harness()
    explicit = _harness(judge=None)

    implicit_result = implicit.play()
    explicit_result = explicit.play()

    assert _normalized_result(implicit_result) == _normalized_result(explicit_result)
    assert implicit.write_snapshot() == explicit.write_snapshot()


# ---------------------------------------------------------------------------
# 2) needs_judge 分支：applied / rejected / 兜底
# ---------------------------------------------------------------------------
def test_needs_judge_applied_with_cassette_allow() -> None:
    """cassette allow：提案 applied、事件提交、audit 记录裁判理由、恰 1 次模型调用。"""
    client, transport = _llm_client("judge_allow.json")
    harness = _harness(judge=LLMJudgeAgent(client), agent_assisted=("take_item",))

    result = harness.play()

    assert result.ok is True and result.version == 2
    assert [event.event_type.value for event in result.events] == ["ITEM_TAKEN"]
    assert transport.calls == 1
    rows = harness.audit_rows()
    assert len(rows) == 1
    assert rows[0].verdict == "applied" and rows[0].status == "committed"
    assert _JUDGE_REASON in rows[0].verdict_reason


def test_needs_judge_rejected_with_cassette_deny() -> None:
    """cassette deny：rejected、零事件/零写入、audit 可追溯裁判理由。"""
    client, transport = _llm_client("judge_deny.json")
    harness = _harness(judge=LLMJudgeAgent(client), agent_assisted=("take_item",))
    before = harness.write_snapshot()

    result = harness.play()

    assert result.ok is False and result.error_code is not None
    assert result.events == []
    assert harness.write_snapshot() == before
    assert transport.calls == 1
    rows = harness.audit_rows()
    assert rows and rows[0].verdict == "rejected" and rows[0].status == "aborted"
    assert "另有主人" in rows[0].verdict_reason


def test_low_confidence_judge_uses_deterministic_fallback() -> None:
    """低置信（0.5）allow → 不采纳、确定性保守兜底（audit 含「保守」）、零写入。"""
    client, transport = _llm_client("judge_low_confidence.json")
    harness = _harness(judge=LLMJudgeAgent(client), agent_assisted=("take_item",))
    before = harness.write_snapshot()

    result = harness.play()

    assert result.ok is False and result.events == []
    assert harness.write_snapshot() == before
    assert transport.calls == 1
    rows = harness.audit_rows()
    assert rows and "保守" in rows[0].verdict_reason


def test_llm_unavailable_judge_falls_back_without_error() -> None:
    """LLM 不可用 → 裁判断言 None → 确定性兜底；回合返回失败但无异常/无 5xx。"""
    harness = _harness(judge=LLMJudgeAgent(DisabledLLMClient()), agent_assisted=("take_item",))
    before = harness.write_snapshot()

    result = harness.play()

    assert result.ok is False and result.events == []
    assert harness.write_snapshot() == before
    assert "保守" in _hint_text(result)


def test_invalid_schema_repaired_once_then_applied() -> None:
    """首次 Schema 违规 → 恰好一次修复 → 第二次 allow 被采纳（2 次模型调用）。"""
    client, transport = _llm_client("judge_repair.json")
    harness = _harness(judge=LLMJudgeAgent(client), agent_assisted=("take_item",))

    result = harness.play()

    assert result.ok is True and result.version == 2
    assert transport.calls == 2


def test_invalid_schema_twice_falls_back() -> None:
    """两次 Schema 违规 → 修复耗尽 → 确定性兜底、零写入（2 次模型调用）。"""
    client, transport = _llm_client("judge_invalid_twice.json")
    harness = _harness(judge=LLMJudgeAgent(client), agent_assisted=("take_item",))
    before = harness.write_snapshot()

    result = harness.play()

    assert result.ok is False and harness.write_snapshot() == before
    assert transport.calls == 2


def test_evidence_outside_slice_is_rejected_as_invalid() -> None:
    """裁判证据引用切片外实体 → 视为无效证据 → 确定性兜底、零写入。"""
    fake = FakeLLM(
        [
            {
                "verdict": "allow",
                "reason": "可以。",
                "evidence_refs": ["门外的巨龙"],
                "confidence": 0.9,
            }
        ]
    )
    harness = _harness(judge=LLMJudgeAgent(fake), agent_assisted=("take_item",))
    before = harness.write_snapshot()

    result = harness.play()

    assert result.ok is False and harness.write_snapshot() == before


def test_raising_judge_does_not_break_turn() -> None:
    """裁判实现抛异常 → 编排层捕获 → 确定性兜底、零写入、不外泄异常。"""
    harness = _harness(judge=_RaisingJudge(), agent_assisted=("take_item",))
    before = harness.write_snapshot()

    result = harness.play()

    assert result.ok is False and result.events == []
    assert harness.write_snapshot() == before


def test_rejected_judge_hint_is_player_readable() -> None:
    """裁判拒绝的失败提示为中文「裁判/保守」类文案，无内部 ID/协议字段。"""
    client, _transport = _llm_client("judge_deny.json")
    harness = _harness(judge=LLMJudgeAgent(client), agent_assisted=("take_item",))

    result = harness.play()

    hint = _hint_text(result)
    assert "保守" in hint
    assert result.text and "裁判" not in result.text  # 叙事 text 不暴露裁判内部概念
    for marker in ("item_", "npc_", "game_id", "json"):
        assert marker not in hint


# ---------------------------------------------------------------------------
# 3) 每回合裁判调用上限 + 生产 dormant 证据
# ---------------------------------------------------------------------------
def test_judge_call_budget_is_one_per_turn() -> None:
    """m3 同回合（玩家 + 2 NPC 的 agent_assisted take_item）裁判只调 1 次，其余保守兜底。"""
    judge = DeterministicJudgeAgent(_judge_output())
    harness = _harness(judge=judge, agent_assisted=("take_item",), scenario="m3")

    result = harness.play()

    assert len(judge.calls) == 1, "每回合最多 1 次裁判调用（第 8/12 章预算）"
    assert result.ok is True
    player_events = [event for event in result.events if event.actor_id == PLAYER_ID]
    assert [event.event_type.value for event in player_events] == ["ITEM_TAKEN"]
    rows = harness.audit_rows()
    assert len(rows) == 3  # 玩家 + 2 NPC 各一行审计
    player_rows = [row for row in rows if row.actor_id == PLAYER_ID]
    npc_rows = [row for row in rows if row.actor_id != PLAYER_ID]
    assert player_rows and player_rows[0].verdict == "applied"
    assert npc_rows and all(row.verdict == "rejected" for row in npc_rows)


def test_production_llm_enabled_assembly_wires_judge_but_zero_judge_calls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """``llm_enabled=true`` 生产装配：构造 LLMJudgeAgent，但 needs_judge 空集 → 零裁判调用。"""
    fake = FakeLLM([])  # 该装配的解析/叙事/角色 LLM 调用全部失败回退，也不应有 judge 调用
    app = _main_app(monkeypatch, tmp_path, llm_enabled=True, llm_client=fake)
    try:
        orchestrator = app.state.orchestrator
        assert isinstance(orchestrator._judge, LLMJudgeAgent)
        assert orchestrator._registry.agent_assisted_tools() == frozenset()
        assert orchestrator._rules.agent_assisted_tools == frozenset()
        with TestClient(app) as client:
            created = client.post(
                "/api/v1/games",
                headers={"Idempotency-Key": f"judge-game-{uuid.uuid4().hex}"},
                json={"seed": 42},
            ).json()
            body = client.post(
                "/api/v1/turns",
                headers={"Idempotency-Key": f"judge-turn-{uuid.uuid4().hex}"},
                json={
                    "game_id": created["game_id"],
                    "input": _TAKE_TORCH,
                    "expected_version": created["version"],
                },
            ).json()
    finally:
        console.dispose_app_engine(app)

    assert body["ok"] is True
    judge_calls = [call for call in fake.calls if call.task == "judge"]
    assert judge_calls == []


# ---------------------------------------------------------------------------
# 4) 无状态污染 / 默认快照回归锚
# ---------------------------------------------------------------------------
def test_judge_does_not_mutate_snapshot() -> None:
    """裁判分支只读快照：裁决前后入参 GameState 不变（纯函数）。"""
    harness = _harness(
        judge=DeterministicJudgeAgent(_judge_output()), agent_assisted=("take_item",)
    )
    record = harness.repo.load(harness.game_id)
    assert record is not None
    before_hash = state_hash(record.state)

    result = harness.play()

    assert result.ok is True
    record_after = harness.repo.load(harness.game_id)
    assert record_after is not None
    assert state_hash(record.state) == before_hash  # 入参状态未被就地修改
    assert isinstance(record_after.state, GameState)


def test_failed_result_has_no_events_and_zero_write_contract() -> None:
    """通用零写入契约：失败 TurnResult 无事件、version/last_seq/state_hash 不变。"""
    harness = _harness(judge=DeterministicJudgeAgent(None), agent_assisted=("take_item",))
    before = harness.write_snapshot()

    result = harness.play()

    assert result.events == []
    assert result.error_code is not None
    assert harness.write_snapshot() == before
