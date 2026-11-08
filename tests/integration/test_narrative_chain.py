"""集成测试: M2-007 叙事链 (TemplateNarrator + RuleEngine 接线, 契约 §4.5、第 9 章 9.2)。

覆盖任务验收:
- 七回合脚本每回合 ``text`` 80–150 字 (读/说/拿/用/走);
- 写事件 ``result["narrative"]`` / ``result["narrative_source"]="template"`` 存在,
  且与 ``TurnResult.text`` 逐字一致;
- 规则门拒绝时 ``TurnResult(ok=False)``、零事件、版本与 ``state_hash`` 不变
  (示例: 未拿药水时“使用治疗药水”);
- 同 seed 同输入两次独立运行, 文本逐字一致、事件 rng_seed/id 一致 (可回放)。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient

from textgame.agents.parser_agent import IntentParseAgent
from textgame.api.app import create_app
from textgame.domain.hashing import state_hash
from textgame.domain.results import ErrorCode, Result
from textgame.domain.rules.engine import RuleEngine
from textgame.domain.scenario import PLAYER_ID
from textgame.llm.client import DisabledLLMClient
from textgame.orchestration.intent import DeterministicIntentParser
from textgame.orchestration.narrator import TemplateNarrator
from textgame.orchestration.turn_orchestrator import TurnOrchestrator
from textgame.persistence.memory import InMemoryRepository
from textgame.services.action_service import ActionService
from textgame.services.game_engine import GameEngine
from textgame.services.query_service import QueryService
from textgame.tools.builtin.m2 import build_m2_tool_registry
from textgame.tools.gate import ToolGate

_MIN_TEXT = 80
_MAX_TEXT = 150
_SAVE_SEED = "save-42"
_GAME_KEY = "narrative-game-0001"

#: 七回合脚本: (输入, 上一版本, 新版本, 是否有写事件)。
_SCRIPT: tuple[tuple[str, int, int, bool], ...] = (
    ("环顾四周", 1, 1, False),
    ("对老哨兵问好", 1, 2, True),
    ("拿起火把", 2, 3, True),
    ("拿起治疗药水", 3, 4, True),
    ("使用治疗药水", 4, 5, True),
    ("向北走进地窖", 5, 6, True),
    ("拿起锈剑", 6, 7, True),
)


class _RejectingRules:
    """``RuleEngineLike`` 测试替身: 任何写工具前置判定都拒绝, 用于验证接线与零写入。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def evaluate_preconditions(
        self,
        *,
        tool: str,
        args: dict[str, Any],
        actor_id: str,
        turn_id: int,
        save_seed: str,
        state: Any,
    ) -> Result:
        """记录调用并返回 ``NOT_ALLOWED`` (不写状态、不产生事件)。"""
        self.calls.append(tool)
        return Result.fail(ErrorCode.NOT_ALLOWED, "前置判定拒绝了这个动作")


@dataclass
class NarrativeHarness:
    """一套与 ``main.py`` 同构的测试装配 (内存仓储 + ToolGate + 007 叙事/规则)。"""

    client: TestClient
    repo: InMemoryRepository
    orchestrator: TurnOrchestrator


def _build_harness(*, rules: Any = None) -> NarrativeHarness:
    """按组合根口径装配: IntentParseAgent + ToolGate + TemplateNarrator + RuleEngine。"""
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
        narrator=TemplateNarrator(),
        rules=RuleEngine() if rules is None else rules,
        save_seed_default=_SAVE_SEED,
    )
    client = TestClient(create_app(orchestrator=orchestrator, query_service=query))
    return NarrativeHarness(client=client, repo=repo, orchestrator=orchestrator)


@pytest.fixture
def harness() -> Iterator[NarrativeHarness]:
    """默认生产装配 (TemplateNarrator + RuleEngine)。"""
    built = _build_harness()
    with built.client:
        yield built


def _create_game(client: TestClient, *, key: str = _GAME_KEY) -> dict[str, Any]:
    """POST /api/v1/games(seed=42), 返回响应体。"""
    response = client.post("/api/v1/games", headers={"Idempotency-Key": key}, json={"seed": 42})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def _post_turn(
    client: TestClient, *, game_id: str, text: str, expected_version: int, key: str
) -> dict[str, Any]:
    """POST /api/v1/turns; 业务失败也是 HTTP 200 + TurnResult(ok=false)。"""
    response = client.post(
        "/api/v1/turns",
        headers={"Idempotency-Key": key},
        json={"game_id": game_id, "input": text, "expected_version": expected_version},
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _event_projection(event: dict[str, Any]) -> tuple[Any, ...]:
    """事件中与回放/可复现相关的字段投影 (不含盖章时刻 ts)。"""
    return (
        event["seq"],
        event["id"],
        event["event_type"],
        event["rng_seed"],
        event["method"],
        event["result"].get("narrative"),
        event["result"].get("narrative_source"),
    )


def test_seven_turn_script_texts_and_narrative_metadata(harness: NarrativeHarness) -> None:
    """七回合脚本: 每屏 80–150 字, 写事件 narrative 与 TurnResult.text 一致。"""
    game = _create_game(harness.client)
    game_id = game["game_id"]
    texts: list[str] = []

    for index, (text, expected_version, new_version, has_event) in enumerate(_SCRIPT):
        body = _post_turn(
            harness.client,
            game_id=game_id,
            text=text,
            expected_version=expected_version,
            key=f"narrative-turn-{index:06d}",
        )

        assert body["ok"] is True, body
        assert body["version"] == new_version
        assert body["turn_id"] == new_version
        assert isinstance(body["text"], str)
        assert _MIN_TEXT <= len(body["text"]) <= _MAX_TEXT, body["text"]
        texts.append(body["text"])

        if has_event:
            assert len(body["events"]) == 1
            result = body["events"][0]["result"]
            assert result["narrative"] == body["text"]
            assert result["narrative_source"] == "template"
        else:
            assert body["events"] == []

    # 落库事件与响应逐条对齐: game_created 之外每条写事件都带模板叙事。
    events = harness.repo.read_events(game_id)
    assert [event.seq for event in events] == list(range(1, 8))
    write_events = events[1:]
    write_texts = [texts[index] for index, turn in enumerate(_SCRIPT) if turn[3]]
    assert len(write_events) == len(write_texts)
    for event, expected_text in zip(write_events, write_texts, strict=True):
        assert event.result["narrative"] == expected_text
        assert event.result["narrative_source"] == "template"

    view = harness.client.get(f"/api/v1/games/{game_id}").json()
    assert view["last_text"] == texts[-1]


def test_rules_gate_is_wired_and_rejection_is_zero_write() -> None:
    """RuleEngineLike 接线: 写工具过 gate 后仍被规则前置判定拒绝, 零事件/版本/hash 不变。"""
    rejecting = _RejectingRules()
    harness = _build_harness(rules=rejecting)
    with harness.client:
        game = _create_game(harness.client)
        game_id = game["game_id"]
        record = harness.repo.load(game_id)
        assert record is not None
        hash_before = state_hash(record.state)
        events_before = len(harness.repo.read_events(game_id))

        body = _post_turn(
            harness.client,
            game_id=game_id,
            text="拿起火把",
            expected_version=1,
            key="narrative-rule-reject-01",
        )

        assert body["ok"] is False
        assert body["error_code"] == "NOT_ALLOWED"
        assert body["events"] == []
        assert body["version"] == 1
        assert rejecting.calls == ["take_item"]
        after = harness.repo.load(game_id)
        assert after is not None
        assert after.version == 1
        assert state_hash(after.state) == hash_before
        assert len(harness.repo.read_events(game_id)) == events_before


def test_use_potion_without_taking_is_rejected_zero_write(harness: NarrativeHarness) -> None:
    """规则/工具门拒绝分支: 未拿药水时“使用治疗药水”→ ok=false、零事件、版本/hash 不变。"""
    game = _create_game(harness.client)
    game_id = game["game_id"]
    record = harness.repo.load(game_id)
    assert record is not None
    hash_before = state_hash(record.state)
    events_before = len(harness.repo.read_events(game_id))

    body = _post_turn(
        harness.client,
        game_id=game_id,
        text="使用治疗药水",
        expected_version=1,
        key="narrative-use-reject-01",
    )

    assert body["ok"] is False
    assert body["events"] == []
    assert body["version"] == 1
    assert body["error_code"] in {"NOT_FOUND", "NOT_ALLOWED"}
    after = harness.repo.load(game_id)
    assert after is not None
    assert after.version == 1
    assert state_hash(after.state) == hash_before
    assert len(harness.repo.read_events(game_id)) == events_before


def test_same_seed_same_script_reproduces_identical_texts_and_events() -> None:
    """同 seed 同输入两次独立运行: 文本逐字一致、事件 seq/id/rng_seed/narrative 一致。"""
    runs: list[list[tuple[Any, ...]]] = []
    final_hashes: list[str] = []

    for run_index in range(2):
        harness = _build_harness()
        with harness.client:
            game = _create_game(harness.client, key=f"narrative-replay-game-{run_index:02d}")
            game_id = game["game_id"]
            turn_projection: list[tuple[Any, ...]] = []
            for index, (text, expected_version, _new_version, _has_event) in enumerate(_SCRIPT):
                body = _post_turn(
                    harness.client,
                    game_id=game_id,
                    text=text,
                    expected_version=expected_version,
                    key=f"narrative-replay-turn-{run_index:02d}-{index:06d}",
                )
                assert body["ok"] is True, body
                turn_projection.append(
                    (
                        body["ok"],
                        body["text"],
                        body["turn_id"],
                        body["version"],
                        tuple(_event_projection(event) for event in body["events"]),
                    )
                )
            record = harness.repo.load(game_id)
            assert record is not None
            final_hashes.append(state_hash(record.state))
            runs.append(turn_projection)

    assert runs[0] == runs[1]
    assert final_hashes[0] == final_hashes[1]
