"""M5E-2 裁判接线独立回归与红队（qa-tester，task-10）。

不 import backend-dev 的 `tests/integration/test_judge_wiring.py` / judge 单测 helper，只用
产品代码 + 现有测试底座（`cassette_transport` / `fakes` / `InMemoryRepository`）独立复核：

- A 默认等价：生产组合根（llm_enabled=false）`orchestrator._judge is None`、生产工具集为空集；
  llm_enabled=true 仅构造 LLM Judge（dormant）且 m2 回合零 judge 调用；`judge=None` 与省略
  参数逐字等价（事件墙钟 ts 除外）；非 needs_judge 提案零读取 judgements（ExplodingJudge）。
- B needs_judge 语义：allow→applied、deny→rejected、低置信/空白 reason/judge=None/裁判异常/
  LLM 不可用→确定性保守兜底；全部 rejected 路径零写入（version/事件数/last_seq/state_hash
  四元组）；审计 verdict/verdict_reason/status 可追溯且无内部 ID；单回合恰 1 次裁判；拒绝
  提示玩家可读。
- C 红队：恶意 Schema（confidence 越界/NaN、verdict 非法、reason 空或超长、evidence_refs
  超限或非 str、extra 字段、`model_construct` 绕过）→ ValidationError 或否定 Result；
  恶意 duck 对象喂 `verify_agent_override` 一律否定且不抛异常；LLMJudgeAgent 对非 JSON/非法
  枚举/越界/extra/证据越界返回 None（Schema 非法恰一次修复）；请求契约与 prompt 脱内部 ID；
  judge 路径零 socket。
- D cassette：judge_*.json 手工合成/无密钥、零网络回放；allow 端到端 applied、deny 端到端
  rejected、低置信/两次非法保守兜底、一次修复后采纳。

运行：`uv run pytest tests/integration/test_judge_redteam.py -q`
"""

from __future__ import annotations

import json
import re
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from cassette_transport import CASSETTE_DIR, CassetteTransport
from fakes import FakeLLM
from textgame import console
from textgame.agents.judge_agent import DeterministicJudgeAgent, JudgeAgentLike, LLMJudgeAgent
from textgame.agents.parser_agent import IntentParseAgent
from textgame.config.settings import get_settings
from textgame.domain.hashing import state_hash
from textgame.domain.judge import JUDGE_CONFIDENCE_FLOOR, JudgeOutput
from textgame.domain.models import Proposal, ResolutionPolicy
from textgame.domain.results import ErrorCode
from textgame.domain.rules.engine import RuleEngine
from textgame.domain.scenario import ITEM_TORCH, M3_NONPLAYER_TAGS, PLAYER_ID, build_initial_state
from textgame.llm.client import DisabledLLMClient, ModelTier
from textgame.llm.config import LLMClientConfig, load_llm_config
from textgame.llm.litellm_client import LiteLLMClient
from textgame.orchestration.dto import TurnRequest
from textgame.orchestration.intent import DeterministicIntentParser
from textgame.orchestration.narrator import TemplateNarrator
from textgame.orchestration.proposal_queue import ProposalQueue, ProposalSource
from textgame.orchestration.resolver import Resolver, ToolGateRouter
from textgame.orchestration.turn_orchestrator import CreateGameRequest, TurnOrchestrator
from textgame.persistence.memory import InMemoryRepository
from textgame.services.action_service import ActionService
from textgame.services.actor_directory import ActorDirectory
from textgame.services.audit_service import AuditService
from textgame.services.game_engine import GameEngine
from textgame.services.query_service import QueryService
from textgame.tools.builtin.m2 import build_m2_tool_registry
from textgame.tools.gate import ToolGate

pytestmark = [pytest.mark.integration]

REPO_ROOT = Path(__file__).resolve().parents[2]
_INTERNAL_ID = re.compile(r"\b(?:card|game|char|npc|item|room)_[0-9A-Za-z]{6,}\b")
_M2_TEXT = "拿起火把"
_SAVE_SEED = "save-42"
_ULID_EVIDENCE = "item_01J9Z3K4Q7X8Y9Z0ABCDEFGHJQ"

#: judge cassette 名（backend task-9 落地到 tests/fixtures/cassettes/）。
JUDGE_CASSETTES = (
    "judge_allow.json",
    "judge_deny.json",
    "judge_low_confidence.json",
    "judge_repair.json",
    "judge_invalid_twice.json",
)

_VALID_JUDGE_JSON: dict[str, Any] = {
    "verdict": "allow",
    "reason": "物品就在他脚边，取用无障碍。",
    "evidence_refs": ["火把"],
    "confidence": 0.9,
}


# ---------------------------------------------------------------------------
# 装配 helper（自有实现；多角色链路 + 测试专用 agent_assisted 注入）
# ---------------------------------------------------------------------------
@dataclass
class _Harness:
    """进程内多角色编排器 harness（零网络、零落库；judge 可注入）。"""

    orchestrator: TurnOrchestrator
    repo: InMemoryRepository
    query: QueryService
    audit: AuditService
    resolver: Resolver
    registry: Any
    rules: RuleEngine


def _registry_with_agent_assisted(*tool_names: str) -> Any:
    """测试专用：把指定工具的 resolution_policy 临时改为 AGENT_ASSISTED（needs_judge 生产点）。"""
    registry = build_m2_tool_registry()
    for name in tool_names:
        spec = registry.get(name)
        # TODO(adapt)：若 backend 提供公开注入 API，改用公开 API；当前按设计 §2 用私有 dict。
        registry._specs[name] = spec.model_copy(
            update={"resolution_policy": ResolutionPolicy.AGENT_ASSISTED}
        )
    return registry


def _harness(
    *,
    judge: JudgeAgentLike | None = None,
    agent_assisted: tuple[str, ...] = (),
) -> _Harness:
    """按 main.py 多角色口径装配（judge 默认 None；agent_assisted 默认空 = 默认行为）。"""
    repo = InMemoryRepository()
    engine = GameEngine(repo)
    query = QueryService(repo)
    action = ActionService(query)
    registry = (
        _registry_with_agent_assisted(*agent_assisted)
        if agent_assisted
        else build_m2_tool_registry()
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
        audit=audit,
        judge=judge,  # TODO(adapt)：task-9 新增关键字参数（设计 §6）
    )
    return _Harness(
        orchestrator=orchestrator,
        repo=repo,
        query=query,
        audit=audit,
        resolver=resolver,
        registry=registry,
        rules=rules,
    )


def _create(harness: _Harness, *, key: str = "redteam") -> Any:
    """创建 m2 存档（seed=42）→ GameView。"""
    return harness.orchestrator.create_game(
        CreateGameRequest(seed=42, idempotency_key=f"game-{key}")
    )


def _play(harness: _Harness, *, text: str = _M2_TEXT, key: str = "redteam") -> tuple[Any, Any]:
    """创建 m2 存档并执行一回合 → (created, result)。"""
    created = _create(harness, key=key)
    result = harness.orchestrator.run(_turn_request(created.game_id, 1, f"turn-{key}", text=text))
    return created, result


def _turn_request(game_id: str, expected_version: int, key: str, *, text: str) -> Any:
    """构造回合请求（alias input 用文本字段名）。"""
    return TurnRequest(
        game_id=game_id,
        text=text,
        expected_version=expected_version,
        idempotency_key=key,
    )


def _state_tuple(harness: _Harness, game_id: str) -> tuple[int, int, int, str]:
    """零写入判据四元组：version / 事件数 / last_seq / state_hash。"""
    record = harness.repo.load(game_id)
    assert record is not None
    return (
        int(record.version),
        len(harness.repo.read_events(game_id)),
        int(record.last_seq),
        state_hash(record.state),
    )


def _audit_rows(harness: _Harness, game_id: str, turn_id: int = 2) -> list[Any]:
    """读取指定回合审计行（每提案一行）。"""
    return harness.audit.query(game_id, turn_id=turn_id)


def _assert_audit_reason_safe(row: Any, *, expected_terms: tuple[str, ...]) -> None:
    """审计理由必须玩家可读：含预期中文、无内部 ID、无 ASCII 协议词。"""
    reason = str(row.verdict_reason)
    for term in expected_terms:
        assert term in reason, (term, reason)
    assert _INTERNAL_ID.search(reason) is None, reason
    for forbidden in ("item_", "npc_", "room_", "game_id", "Traceback", "RuntimeError"):
        assert forbidden not in reason, (forbidden, reason)


def _assert_no_write(harness: _Harness, game_id: str, before: tuple[int, int, int, str]) -> None:
    """rejected 路径零写入。"""
    assert _state_tuple(harness, game_id) == before


# ---------------------------------------------------------------------------
# 测试替身
# ---------------------------------------------------------------------------
class _CountingJudge:
    """计数裁判：固定返回给定输出并记录调用。"""

    def __init__(self, output: JudgeOutput | None) -> None:
        self._output = output
        self.calls = 0

    def evaluate(self, perception: Any, proposal: Any, *, turn_id: int, save_seed: str) -> Any:
        """计数一次并返回固定输出。"""
        self.calls += 1
        return self._output


class _RaisingJudge:
    """恶意裁判：evaluate 直接抛 RuntimeError（红队 I10）。"""

    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, perception: Any, proposal: Any, *, turn_id: int, save_seed: str) -> Any:
        """抛异常（不得外泄到回合/HTTP 层）。"""
        self.calls += 1
        raise RuntimeError("judge exploded")


class _ExplodingJudge:
    """任何属性访问即失败（证明非 needs_judge 完全不读 judgements）。"""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"judgements must not be read for non-needs_judge (accessed {name!r})")


class _BadJudgeObject:
    """恶意 duck 对象：字段类型错误，verify/fallback 必须不炸。"""

    def __init__(self, **fields: Any) -> None:
        self.__dict__.update(fields)


def _deterministic_judge(
    *,
    verdict: str = "allow",
    confidence: float = 0.9,
    reason: str = "现场有人作证",
    evidence_refs: tuple[str, ...] = ("火把",),
) -> Any:
    """构造测试用确定性裁判（DeterministicJudgeAgent 固定返回给定 JudgeOutput）。"""
    output = JudgeOutput(
        verdict=verdict,
        confidence=confidence,
        reason=reason,
        evidence_refs=list(evidence_refs),
    )
    return DeterministicJudgeAgent(output=output)


def _judge_output(**overrides: Any) -> JudgeOutput:
    """合法 JudgeOutput（可覆盖字段）。"""
    payload = dict(_VALID_JUDGE_JSON)
    payload.update(overrides)
    return JudgeOutput(**payload)


# ---------------------------------------------------------------------------
# LLM 客户端 helper（手工 cassette；零网络）
# ---------------------------------------------------------------------------
def _llm_client(cassette: str) -> tuple[LiteLLMClient, CassetteTransport]:
    """构造 cassette 回放客户端（与 test_llm_wiring 同款底座，独立实现）。"""
    transport = CassetteTransport(cassette)
    config = LLMClientConfig(
        provider="deepseek",
        model="cassette-model",
        api_base="https://cassette.invalid",
        api_key=SecretStr("cassette-no-secret"),
    )
    return LiteLLMClient(config, transport=transport, llm_config=load_llm_config()), transport


def _perception(harness: _Harness, game_id: str) -> Any:
    """当前存档的玩家感知切片（真值 build_perception_slice）。"""
    record = harness.repo.load(game_id)
    assert record is not None
    return harness.query.build_perception_slice(
        record.state, PLAYER_ID, turn_id=record.state.turn, save_seed=_SAVE_SEED
    )


def _proposal(*, tool: str = "take_item", args: dict[str, Any] | None = None) -> Proposal:
    """构造 needs_judge 提案（真实 Proposal 模型）。"""
    return Proposal(
        turn_id=2,
        actor_id=PLAYER_ID,
        tool=tool,
        args=dict(args or {"item_id": ITEM_TORCH}),
        reason="红队提案",
        priority=100,
    )


# ---------------------------------------------------------------------------
# A. 默认离线 / 逐字等价（无裁判调用）
# ---------------------------------------------------------------------------
def test_default_assembly_has_no_judge_and_empty_production_set() -> None:
    """默认装配：RuleEngine/Registry 的 agent_assisted 集合为空（needs_judge 不可达）。"""
    assert RuleEngine().agent_assisted_tools == frozenset()
    assert build_m2_tool_registry().agent_assisted_tools() == frozenset()


def test_default_production_app_has_no_judge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """生产组合根（llm_enabled=false）：裁判常驻但运行期离线（零调用），m2 回合正常。"""
    import textgame.main as main_module

    monkeypatch.setenv("TEXTGAME_LLM_ENABLED", "false")
    monkeypatch.setenv("TEXTGAME_DB_URL", f"sqlite:///{(tmp_path / 'default.db').as_posix()}")
    get_settings.cache_clear()
    console.dispose_app_engine(main_module.app)
    app = main_module._build_app()
    try:
        with TestClient(app) as client:
            orchestrator = app.state.orchestrator
            assert getattr(orchestrator, "_judge", "missing") is not None
            assert app.state.llm_runtime.status().enabled is False
            created = client.post(
                "/api/v1/games",
                headers={"Idempotency-Key": "redteam-default-game-0001"},
                json={"seed": 42, "scenario": "m2"},
            )
            assert created.status_code == 201, created.text
            turn = client.post(
                "/api/v1/turns",
                headers={"Idempotency-Key": "redteam-default-turn-0001"},
                json={
                    "game_id": created.json()["game_id"],
                    "input": _M2_TEXT,
                    "expected_version": 1,
                },
            )
            assert turn.status_code == 200, turn.text
            body = turn.json()
    finally:
        console.dispose_app_engine(app)
        get_settings.cache_clear()

    assert body["ok"] is True
    assert 80 <= len(body["text"]) <= 150


def test_llm_enabled_assembly_constructs_judge_but_never_calls_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """llm_enabled=true：装配 LLM Judge（dormant），m2 回合仍零 judge 调用。"""
    import textgame.main as main_module

    fake = FakeLLM()
    monkeypatch.setattr(main_module, "RuntimeLLMClient", lambda settings, **kwargs: fake)
    monkeypatch.setenv("TEXTGAME_LLM_ENABLED", "true")
    monkeypatch.setenv("TEXTGAME_DB_URL", f"sqlite:///{(tmp_path / 'enabled.db').as_posix()}")
    get_settings.cache_clear()
    console.dispose_app_engine(main_module.app)
    app = main_module._build_app()
    try:
        with TestClient(app) as client:
            orchestrator = app.state.orchestrator
            assert getattr(orchestrator, "_judge", None) is not None, (
                "llm_enabled=true 应构造 LLM Judge"
            )
            created = client.post(
                "/api/v1/games",
                headers={"Idempotency-Key": "redteam-enabled-game-0001"},
                json={"seed": 42, "scenario": "m2"},
            ).json()
            turn = client.post(
                "/api/v1/turns",
                headers={"Idempotency-Key": "redteam-enabled-turn-0001"},
                json={"game_id": created["game_id"], "input": _M2_TEXT, "expected_version": 1},
            )
            assert turn.status_code == 200, turn.text
            assert turn.json()["ok"] is True
    finally:
        console.dispose_app_engine(app)
        get_settings.cache_clear()

    judge_calls = [call for call in fake.calls if call.task == "judge"]
    assert judge_calls == [], "默认工具集为空时不得发生任何裁判模型调用"


def _strip_wall_clock(dump: dict[str, Any]) -> dict[str, Any]:
    """剔除事件落库时间戳（墙钟，非确定性），其余字段逐字比较。"""
    cleaned = json.loads(json.dumps(dump, ensure_ascii=False))
    for event in cleaned.get("events") or []:
        if isinstance(event, dict):
            event.pop("ts", None)
    return cleaned


def test_judge_none_equals_omitted_judge() -> None:
    """显式 judge=None 与省略参数：TurnResult（除墙钟 ts）/状态四元组逐字等价。"""
    omitted = _harness()
    explicit = _harness(judge=None)
    created_a, result_a = _play(omitted, key="eq-a")
    created_b, result_b = _play(explicit, key="eq-b")
    assert _strip_wall_clock(result_a.model_dump(mode="json")) == _strip_wall_clock(
        result_b.model_dump(mode="json")
    )
    assert _state_tuple(omitted, created_a.game_id) == _state_tuple(explicit, created_b.game_id)


def test_non_needs_judge_does_not_read_judgements() -> None:
    """非 needs_judge 提案：judgements=[ExplodingJudge] 也零属性访问、正常 applied。"""
    harness = _harness()
    state = build_initial_state(save_seed=_SAVE_SEED)
    queue = ProposalQueue(turn_id=2)
    queue.enqueue(_proposal(), source=ProposalSource.PLAYER)
    entries = queue.drain()
    resolutions = harness.resolver.resolve(entries, state, judgements=[_ExplodingJudge()])
    assert [resolution.verdict for resolution in resolutions] == ["applied"]


# ---------------------------------------------------------------------------
# B. needs_judge 注入后的裁判语义 + 审计 + 零写入
# ---------------------------------------------------------------------------
def test_needs_judge_allow_applies_and_audit_traces_judge_reason() -> None:
    """allow 高置信 → applied；审计 verdict=applied/committed 且理由=裁判理由。"""
    judge = _deterministic_judge(reason="现场有人作证，可见物品随手可取")
    harness = _harness(judge=judge, agent_assisted=("take_item",))
    created, result = _play(harness, key="allow")
    rows = _audit_rows(harness, created.game_id)
    assert result.ok is True, result
    assert result.version == 2
    event_types = [event.event_type.value for event in result.events if event.event_type]
    assert "ITEM_TAKEN" in event_types, event_types
    assert len(rows) == 1, rows
    assert rows[0].verdict == "applied"
    assert rows[0].status == "committed"
    _assert_audit_reason_safe(rows[0], expected_terms=("现场有人作证",))


def test_needs_judge_deny_rejects_with_zero_write_and_audit_reason() -> None:
    """deny 高置信 → rejected、零写入、审计 rejected/aborted 且理由可追溯。"""
    judge = _deterministic_judge(verdict="deny", confidence=0.95, reason="这不合当前场景的规矩")
    harness = _harness(judge=judge, agent_assisted=("take_item",))
    created = _create(harness, key="deny")
    before = _state_tuple(harness, created.game_id)
    result = harness.orchestrator.run(_turn_request(created.game_id, 1, "turn-deny", text=_M2_TEXT))
    rows = _audit_rows(harness, created.game_id)
    assert result.ok is False
    assert result.events == []
    _assert_no_write(harness, created.game_id, before)
    assert rows[0].verdict == "rejected"
    assert rows[0].status == "aborted"
    _assert_audit_reason_safe(rows[0], expected_terms=("这不合当前场景的规矩",))


def test_needs_judge_low_confidence_falls_back_conservatively() -> None:
    """置信度低于 0.7 → 确定性兜底（保守拒绝），审计理由含「保守」。"""
    judge = _deterministic_judge(confidence=0.5, reason="大概可以")
    harness = _harness(judge=judge, agent_assisted=("take_item",))
    created = _create(harness, key="low")
    before = _state_tuple(harness, created.game_id)
    result = harness.orchestrator.run(_turn_request(created.game_id, 1, "turn-low", text=_M2_TEXT))
    rows = _audit_rows(harness, created.game_id)
    assert result.ok is False and result.events == []
    _assert_no_write(harness, created.game_id, before)
    _assert_audit_reason_safe(rows[0], expected_terms=("保守",))
    assert JUDGE_CONFIDENCE_FLOOR == 0.7


def test_needs_judge_blank_reason_falls_back() -> None:
    """reason 纯空白（Schema min_length=1 允许）→ 兜底拒绝、零写入。"""
    judge = _deterministic_judge(confidence=0.9, reason="   ")
    harness = _harness(judge=judge, agent_assisted=("take_item",))
    created = _create(harness, key="blank")
    before = _state_tuple(harness, created.game_id)
    result = harness.orchestrator.run(
        _turn_request(created.game_id, 1, "turn-blank", text=_M2_TEXT)
    )
    assert result.ok is False and result.events == []
    _assert_no_write(harness, created.game_id, before)


def test_needs_judge_without_judge_falls_back() -> None:
    """needs_judge + judge=None → 确定性兜底拒绝、零写入。"""
    harness = _harness(judge=None, agent_assisted=("take_item",))
    created = _create(harness, key="nojudge")
    before = _state_tuple(harness, created.game_id)
    result = harness.orchestrator.run(
        _turn_request(created.game_id, 1, "turn-nojudge", text=_M2_TEXT)
    )
    assert result.ok is False and result.events == []
    _assert_no_write(harness, created.game_id, before)


def test_judge_exception_falls_back_without_5xx() -> None:
    """裁判 evaluate 抛异常 → 回合正常返回（HTTP 200/ok=false）、无堆栈泄漏、零写入。"""
    judge = _RaisingJudge()
    harness = _harness(judge=judge, agent_assisted=("take_item",))
    from textgame.api.app import create_app

    app = create_app(orchestrator=harness.orchestrator, query_service=harness.query)
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/games",
            headers={"Idempotency-Key": "redteam-raise-game-0001"},
            json={"seed": 42, "scenario": "m2"},
        ).json()
        before = _state_tuple(harness, created["game_id"])
        response = client.post(
            "/api/v1/turns",
            headers={"Idempotency-Key": "redteam-raise-turn-0001"},
            json={"game_id": created["game_id"], "input": _M2_TEXT, "expected_version": 1},
        )
        body = response.json()
        after = _state_tuple(harness, created["game_id"])
        rows = _audit_rows(harness, created["game_id"])
    assert response.status_code == 200, response.text
    assert body["ok"] is False
    assert body["events"] == []
    assert before == after
    assert "Traceback" not in response.text
    assert "RuntimeError" not in response.text
    assert rows and rows[0].verdict == "rejected"


def test_judge_llm_unavailable_falls_back() -> None:
    """LLMJudgeAgent(DisabledLLMClient()) → 保守兜底拒绝、零写入、无异常。"""
    judge = LLMJudgeAgent(DisabledLLMClient())
    harness = _harness(judge=judge, agent_assisted=("take_item",))
    created = _create(harness, key="unavailable")
    before = _state_tuple(harness, created.game_id)
    result = harness.orchestrator.run(
        _turn_request(created.game_id, 1, "turn-unavailable", text=_M2_TEXT)
    )
    assert result.ok is False and result.events == []
    _assert_no_write(harness, created.game_id, before)


def test_judge_called_at_most_once_per_turn() -> None:
    """单回合最多 1 次裁判调用（单 needs_judge 提案场景）。"""
    judge = _CountingJudge(_judge_output())
    harness = _harness(judge=judge, agent_assisted=("take_item",))
    _created, result = _play(harness, key="once")
    assert result.ok is True
    assert judge.calls == 1, judge.calls


def test_rejected_turn_hint_is_player_readable() -> None:
    """裁判拒绝的失败提示为中文且无内部 ID；建议动作非空。"""
    judge = _deterministic_judge(verdict="deny", confidence=0.95, reason="这不合当前场景的规矩")
    harness = _harness(judge=judge, agent_assisted=("take_item",))
    _created, result = _play(harness, key="hint")
    hints = " ".join(str(effect.get("hint", "")) for effect in result.effects or [])
    assert hints, result.effects
    assert "保守" in hints or "规矩" in hints
    assert _INTERNAL_ID.search(hints) is None
    assert result.suggested_actions


# ---------------------------------------------------------------------------
# C. 红队：恶意 Schema / 恶意对象 / LLM 输出
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "payload",
    [
        {**_VALID_JUDGE_JSON, "confidence": 1.5},
        {**_VALID_JUDGE_JSON, "confidence": -0.1},
        {**_VALID_JUDGE_JSON, "verdict": "maybe"},
        {**_VALID_JUDGE_JSON, "verdict": "ALLOW"},
        {**_VALID_JUDGE_JSON, "reason": ""},
        {**_VALID_JUDGE_JSON, "reason": "长" * 301},
        {**_VALID_JUDGE_JSON, "evidence_refs": ["火把"] * 6},
        {**_VALID_JUDGE_JSON, "evidence_refs": [1, 2]},
        {**_VALID_JUDGE_JSON, "allowed": True},
        {**_VALID_JUDGE_JSON, "proposal_id": "p1"},
        {**_VALID_JUDGE_JSON, "tool": "take_item"},
        {**_VALID_JUDGE_JSON, "args": {"item_id": "x"}},
        {**_VALID_JUDGE_JSON, "confidence_override": 1.0},
    ],
    ids=lambda payload: ",".join(sorted(payload)),
)
def test_judge_output_schema_rejects_malicious_fields(payload: dict[str, Any]) -> None:
    """恶意/畸形 JudgeOutput 字段 → ValidationError（extra=forbid）。"""
    with pytest.raises(ValidationError):
        JudgeOutput(**payload)


def test_judge_output_allowed_property_is_derived() -> None:
    """allowed 为 verdict 派生只读属性（allow=True；deny/modify=False）。"""
    assert _judge_output(verdict="allow").allowed is True
    assert _judge_output(verdict="deny").allowed is False
    assert _judge_output(verdict="modify").allowed is False


@pytest.mark.parametrize(
    "bad",
    [
        _BadJudgeObject(verdict="allow", reason="理由", confidence="0.95"),
        _BadJudgeObject(verdict="allow", reason="理由", confidence=None),
        _BadJudgeObject(verdict="allow", reason=None, confidence=0.9),
        _BadJudgeObject(verdict="allow", reason=123, confidence=0.9),
        _BadJudgeObject(verdict=None, reason="理由", confidence=0.9),
        _BadJudgeObject(verdict=object(), reason="理由", confidence=0.9),
        _BadJudgeObject(verdict="deny", reason="理由", confidence=0.9),
        _BadJudgeObject(verdict="allow", reason="   ", confidence=0.9),
        _BadJudgeObject(verdict="allow", reason="理由", confidence=float("nan")),
    ],
    ids=[
        "confidence-str",
        "confidence-none",
        "reason-none",
        "reason-int",
        "verdict-none",
        "verdict-object",
        "deny",
        "reason-blank",
        "confidence-nan",
    ],
)
def test_verify_agent_override_handles_malicious_objects(bad: Any) -> None:
    """恶意 duck 对象喂 verify：返回否定 Result、不抛异常（证据不足 → 保守）。"""
    engine = RuleEngine()
    result = engine.verify_agent_override(None, _proposal(), bad)
    assert result.ok is False
    assert result.error_code is ErrorCode.NOT_ALLOWED
    assert result.message


def test_deterministic_fallback_is_pure_and_deterministic() -> None:
    """确定性兜底：恒否定、中文、两次逐字相同、零事件。"""
    engine = RuleEngine()
    state = build_initial_state(save_seed=_SAVE_SEED)
    proposal = _proposal()
    first = engine.deterministic_fallback(None, proposal, state)
    second = engine.deterministic_fallback(None, proposal, state)
    assert first.ok is False and first.error_code is ErrorCode.NOT_ALLOWED
    assert first.message == second.message
    assert first.events == [] and second.events == []
    assert _INTERNAL_ID.search(str(first.message)) is None


def test_llm_judge_agent_returns_none_on_malicious_outputs() -> None:
    """LLMJudgeAgent：Schema 非法 → 一次修复（2 次调用）后仍非法 → None；证据越界 → 直接 None。"""
    bad_outputs: list[tuple[Any, int]] = [
        # Schema 级非法：首次 + 一次修复 = 2 次调用，最终 None。
        ("not-json-at-all", 2),
        (
            {"verdict": "maybe", "reason": "理由", "evidence_refs": ["火把"], "confidence": 0.9},
            2,
        ),
        ({**_VALID_JUDGE_JSON, "confidence": 2.0}, 2),
        ({**_VALID_JUDGE_JSON, "allowed": True}, 2),
        ({**_VALID_JUDGE_JSON, "proposal_id": "forged"}, 2),
        ({**_VALID_JUDGE_JSON, "reason": ""}, 2),
        # Schema 合法但证据越界：不做修复，直接 None（1 次调用）。
        ({**_VALID_JUDGE_JSON, "evidence_refs": [_ULID_EVIDENCE]}, 1),
    ]
    harness = _harness(agent_assisted=("take_item",))
    created = _create(harness, key="llm-bad")
    perception = _perception(harness, created.game_id)
    proposal = _proposal()
    for bad, expected_calls in bad_outputs:
        fake = FakeLLM([bad, bad])
        judge = LLMJudgeAgent(fake)
        output = judge.evaluate(perception, proposal, turn_id=2, save_seed=_SAVE_SEED)
        assert output is None, bad
        assert len(fake.calls) == expected_calls, (bad, len(fake.calls), expected_calls)


def test_llm_judge_agent_repairs_once_then_adopts() -> None:
    """第 1 条非法、第 2 条合法：返回合法 JudgeOutput，恰 2 次调用。"""
    fake = FakeLLM(["not-json", json.dumps(_VALID_JUDGE_JSON, ensure_ascii=False)])
    harness = _harness(agent_assisted=("take_item",))
    created = _create(harness, key="llm-repair")
    perception = _perception(harness, created.game_id)
    judge = LLMJudgeAgent(fake)
    output = judge.evaluate(perception, _proposal(), turn_id=2, save_seed=_SAVE_SEED)
    assert output is not None
    assert output.verdict == "allow"
    assert len(fake.calls) == 2


def test_llm_judge_agent_request_contract_and_prompt_hygiene() -> None:
    """请求契约：task/agent_id/prompt_version/STRONG/max_repair/output_schema；prompt 脱内部 ID。"""
    fake = FakeLLM([json.dumps(_VALID_JUDGE_JSON, ensure_ascii=False)])
    harness = _harness(agent_assisted=("take_item",))
    created = _create(harness, key="llm-contract")
    perception = _perception(harness, created.game_id)
    judge = LLMJudgeAgent(fake)
    output = judge.evaluate(perception, _proposal(), turn_id=2, save_seed=_SAVE_SEED)
    assert output is not None
    assert len(fake.calls) == 1
    request = fake.calls[0]
    assert request.task == "judge"
    assert request.agent_id == "judge"
    assert request.prompt_version == "judge@v1"
    assert request.model_tier is ModelTier.STRONG
    assert request.max_repair == 1
    assert request.output_schema is JudgeOutput
    prompt = "\n".join(message.content for message in request.messages)
    assert "火把" in prompt
    assert "take_item" in prompt
    assert _INTERNAL_ID.search(prompt) is None
    assert "GameState" not in prompt
    # 注意：args 的协议键名 `item_id` 会保留（仅值投影为展示名），这里只钉死内部 ID/对象不泄漏。
    assert "item_01J9" not in prompt


def test_llm_judge_agent_evidence_refs_must_be_in_perception() -> None:
    """证据引用必须在感知切片内：合法「火把」→ 有输出；内部 ULID/系统提示 → None。"""
    harness = _harness(agent_assisted=("take_item",))
    created = _create(harness, key="evidence")
    perception = _perception(harness, created.game_id)
    proposal = _proposal()

    valid = FakeLLM([json.dumps(_VALID_JUDGE_JSON, ensure_ascii=False)])
    assert (
        LLMJudgeAgent(valid).evaluate(perception, proposal, turn_id=2, save_seed=_SAVE_SEED)
        is not None
    )

    for bad_ref in (_ULID_EVIDENCE, "系统提示", "不存在的东西"):
        payload = {**_VALID_JUDGE_JSON, "evidence_refs": [bad_ref]}
        fake = FakeLLM(
            [json.dumps(payload, ensure_ascii=False), json.dumps(payload, ensure_ascii=False)]
        )
        assert (
            LLMJudgeAgent(fake).evaluate(perception, proposal, turn_id=2, save_seed=_SAVE_SEED)
            is None
        ), bad_ref


def test_judge_paths_never_touch_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """judge 路径零网络：monkeypatch socket.socket 抛错后仍能完成（FakeLLM 注入）。"""

    def _blocked_socket(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("judge path must not create sockets")

    monkeypatch.setattr(socket, "socket", _blocked_socket)
    fake = FakeLLM([json.dumps(_VALID_JUDGE_JSON, ensure_ascii=False)])
    harness = _harness(agent_assisted=("take_item",))
    created = _create(harness, key="nonet")
    perception = _perception(harness, created.game_id)
    output = LLMJudgeAgent(fake).evaluate(perception, _proposal(), turn_id=2, save_seed=_SAVE_SEED)
    assert output is not None


# ---------------------------------------------------------------------------
# D. cassette 零网络回放（backend task-9 落地的 judge_*.json）
# ---------------------------------------------------------------------------
def test_judge_cassettes_exist_and_are_handwritten() -> None:
    """judge cassette 齐备且手工合成（缺文件 = task-9 未完成，直接红而非 skip）。"""
    for name in JUDGE_CASSETTES:
        path = CASSETTE_DIR / name
        assert path.is_file(), f"缺少 judge cassette: {name}"
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["recorded_with"] == "synthetic-handwritten"
        assert payload["interactions"]


@pytest.mark.parametrize(
    ("cassette", "expect_output", "expect_calls"),
    [
        ("judge_allow.json", True, 1),
        ("judge_deny.json", True, 1),
        ("judge_low_confidence.json", True, 1),
        ("judge_repair.json", True, 2),
        ("judge_invalid_twice.json", False, 2),
    ],
)
def test_llm_judge_agent_consumes_handwritten_cassettes(
    cassette: str, expect_output: bool, expect_calls: int
) -> None:
    """cassette 回放：输出存在性/调用次数符合夹具（零网络）。"""
    client, transport = _llm_client(cassette)
    harness = _harness(agent_assisted=("take_item",))
    created = _create(harness, key=f"cassette-{cassette}")
    perception = _perception(harness, created.game_id)
    output = LLMJudgeAgent(client).evaluate(
        perception, _proposal(), turn_id=2, save_seed=_SAVE_SEED
    )
    if expect_output:
        assert output is not None, cassette
    else:
        assert output is None, cassette
    assert transport.calls == expect_calls, (cassette, transport.calls)


def test_cassette_allow_applies_through_orchestrator() -> None:
    """端到端：allow cassette → needs_judge applied；审计理由可追溯 cassette 理由。"""
    client, transport = _llm_client("judge_allow.json")
    judge = LLMJudgeAgent(client)
    harness = _harness(judge=judge, agent_assisted=("take_item",))
    created, result = _play(harness, key="cassette-allow")
    rows = _audit_rows(harness, created.game_id)
    assert result.ok is True
    assert result.events
    assert transport.calls == 1
    assert rows[0].verdict == "applied"
    assert rows[0].status == "committed"
    expected_reason = json.loads((CASSETTE_DIR / "judge_allow.json").read_text(encoding="utf-8"))[
        "interactions"
    ][0]["response"]["body"]["content"]
    assert json.loads(expected_reason)["reason"] in rows[0].verdict_reason


def test_cassette_deny_rejects_through_orchestrator() -> None:
    """端到端：deny cassette → needs_judge rejected、零写入、审计 rejected/aborted。"""
    client, transport = _llm_client("judge_deny.json")
    judge = LLMJudgeAgent(client)
    harness = _harness(judge=judge, agent_assisted=("take_item",))
    created = _create(harness, key="cassette-deny")
    before = _state_tuple(harness, created.game_id)
    result = harness.orchestrator.run(
        _turn_request(created.game_id, 1, "turn-cassette-deny", text=_M2_TEXT)
    )
    rows = _audit_rows(harness, created.game_id)
    assert result.ok is False and result.events == []
    _assert_no_write(harness, created.game_id, before)
    assert transport.calls == 1
    assert rows[0].verdict == "rejected"
    assert rows[0].status == "aborted"


# ---------------------------------------------------------------------------
# 供 task-10 落地时复用：断言 judge cassette 不含密钥样字符串（独立复验）
# ---------------------------------------------------------------------------
def test_judge_cassettes_have_no_secret_like_values() -> None:
    """judge cassette 不含 sk-/api_key 等密钥样字符串（手工合成、零隐私）。"""
    for name in JUDGE_CASSETTES:
        text = (CASSETTE_DIR / name).read_text(encoding="utf-8")
        lowered = text.lower()
        for token in ("sk-", "api_key:", "authorization", "bearer "):
            assert token not in lowered, (name, token)
