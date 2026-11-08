"""M3-014 golden 固定种子回放测试（第 18 章 §18.3、第 14 章 §14.4、契约 §3/§4）。

golden 数据（``tests/fixtures/golden/m3_golden.json``）由 Lead 在 M3 生产同构装配下
**一次性生成**并逐字段核对；变更必须 Lead 审核并在进度文档登记，**禁止**
``--snapshot-update`` 之类手段掩盖回归。

回放口径：
- 10 次玩家输入 + 场景计划表 NPC 计划，逐回合断言 ``ok``/``turn_id``/``version``/
  ``last_seq``/事件 ``id``/``actor_id``/``method``/``rng_seed``/提交后 ``state_hash``/
  文本长度与 golden 精确相等；
- turn 2 冲突回合：一个 ``applied``、一个 ``rejected``，且审计行成对存在；
- 只用盖章事件重建 ``GameState`` → ``state_hash`` 与 golden 最终值一致；
- ``EventLog(save_seed)`` 重演校验 ``seq``/``id``/``rng_seed``；篡改任一事件 → ``ReplayMismatchError``；
- 同 seed 同脚本在两个独立装配上重跑 → 每回合文本与 ``state_hash`` 完全一致。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from textgame.agents.character_agent import DeterministicCharacterAgent
from textgame.agents.parser_agent import IntentParseAgent
from textgame.domain.events import Event, EventLog, ReplayMismatchError
from textgame.domain.hashing import state_hash
from textgame.domain.models import GameState
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
from textgame.orchestration.dto import TurnRequest
from textgame.orchestration.intent import DeterministicIntentParser
from textgame.orchestration.narrator import TemplateNarrator
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

pytestmark = [pytest.mark.replay]

_GOLDEN_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "golden" / "m3_golden.json"
_SAVE_SEED = "save-42"


@dataclass
class _M3Harness:
    """golden 回放装配：内存仓储 + M3 生产同构编排器 + 已创建存档。"""

    repo: InMemoryRepository
    orchestrator: TurnOrchestrator
    audit: AuditService
    game_id: str
    save_seed: str


def _build_harness(seed: int, idempotency_key: str) -> _M3Harness:
    """按 ``main.py`` 组合根口径装配 M3 多角色应用（确定性、零联网）。"""
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
        save_seed=_SAVE_SEED,
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
        save_seed_default=_SAVE_SEED,
        registry=registry,
        resolver=resolver,
        actors=actors,
        character_agents=character_agents,
        audit=audit,
    )
    view = orchestrator.create_game(
        CreateGameRequest(seed=seed, idempotency_key=idempotency_key, scenario="m3")
    )
    return _M3Harness(
        repo=repo,
        orchestrator=orchestrator,
        audit=audit,
        game_id=view.game_id,
        save_seed=view.save_seed or _SAVE_SEED,
    )


def _load_golden() -> dict[str, Any]:
    """读取 golden fixture（只读；测试不得改写）。"""
    return json.loads(_GOLDEN_PATH.read_text(encoding="utf-8"))


def _play(harness: _M3Harness, script: list[str]) -> list[dict[str, Any]]:
    """按脚本逐回合执行并收集与 golden 同构的观测结果。"""
    record = harness.repo.load(harness.game_id)
    assert record is not None
    version = record.version
    observations: list[dict[str, Any]] = []
    for index, text in enumerate(script, start=1):
        result = harness.orchestrator.run(
            TurnRequest(
                game_id=harness.game_id,
                input=text,
                expected_version=version,
                idempotency_key=f"m3-golden-turn-{index:02d}",
            )
        )
        record = harness.repo.load(harness.game_id)
        assert record is not None
        observations.append(
            {
                "input": text,
                "ok": result.ok,
                "turn_id": record.state.turn,
                "version": record.version,
                "last_seq": record.last_seq,
                "event_ids": [event.id for event in result.events],
                "event_actors": [event.actor_id for event in result.events],
                "event_methods": [event.method for event in result.events],
                "rng_seeds": [event.rng_seed for event in result.events],
                "state_hash": state_hash(record.state),
                "text_len": len(result.text),
            }
        )
        version = record.version
    return observations


def _rebuild_from_events(events: list[Event]) -> GameState:
    """只用盖章事件重建最终状态（``game_created`` 载荷提供初始态）。"""
    initial = GameState.model_validate(events[0].result["initial_state"])
    return initial.apply(events[1:])


# ---------------------------------------------------------------------------
# golden 逐回合
# ---------------------------------------------------------------------------
def test_m3_golden_replay_matches_every_turn() -> None:
    """10 次输入的逐回合事件/种子/哈希/文本长度与 golden 精确一致。"""
    golden = _load_golden()
    harness = _build_harness(
        seed=golden["create_game"]["seed"],
        idempotency_key=golden["create_game"]["idempotency_key"],
    )
    assert harness.save_seed == golden["save_seed"]
    assert harness.repo.load(harness.game_id) is not None

    observations = _play(harness, golden["script"])

    assert len(observations) == len(golden["turns"])
    for observed, expected in zip(observations, golden["turns"], strict=True):
        for field in (
            "input",
            "ok",
            "turn_id",
            "version",
            "last_seq",
            "event_ids",
            "event_actors",
            "event_methods",
            "rng_seeds",
            "state_hash",
            "text_len",
        ):
            assert observed[field] == expected[field], (
                f"turn {expected.get('attempted_turn')} field {field} mismatch: "
                f"golden={expected[field]!r} actual={observed[field]!r}"
            )
        assert golden["text_len_min"] <= observed["text_len"] <= golden["text_len_max"]


def test_m3_golden_final_state_and_event_chain() -> None:
    """最终状态哈希、事件总数、全局 seq 连续与 golden 一致。"""
    golden = _load_golden()
    harness = _build_harness(
        seed=golden["create_game"]["seed"],
        idempotency_key=golden["create_game"]["idempotency_key"],
    )
    _play(harness, golden["script"])

    record = harness.repo.load(harness.game_id)
    assert record is not None
    events = harness.repo.read_events(harness.game_id)
    assert len(events) == golden["event_total"]
    assert record.last_seq == golden["final_last_seq"] == len(events)
    assert record.version == golden["final_version"]
    assert record.state.turn == golden["final_turn"]
    assert state_hash(record.state) == golden["final_state_hash"]
    assert [event.seq for event in events] == list(range(1, len(events) + 1))
    assert [event.id for event in events] == [f"evt_{seq:06d}" for seq in range(1, len(events) + 1)]
    # 纯事件重建 → 与当前投影一致（事件溯源权威）。
    rebuilt = _rebuild_from_events(events)
    assert state_hash(rebuilt) == golden["final_state_hash"]
    # EventLog 重演：seq/id/rng_seed 全部可复算。
    log = EventLog(_SAVE_SEED)
    log.append(events)
    assert [event.model_dump(mode="json") for event in log.replay()] == [
        event.model_dump(mode="json") for event in events
    ]


def test_m3_golden_conflict_turn_is_audited() -> None:
    """turn 2 冲突回合：同名物品只有一个 applied，另一个 rejected，审计成对且已提交。"""
    golden = _load_golden()
    harness = _build_harness(
        seed=golden["create_game"]["seed"],
        idempotency_key=golden["create_game"]["idempotency_key"],
    )
    _play(harness, golden["script"])

    rows = harness.audit.query(harness.game_id, turn_id=2)
    assert len(rows) == 2
    applied = [row for row in rows if row.verdict == "applied"]
    rejected = [row for row in rows if row.verdict == "rejected"]
    assert len(applied) == len(rejected) == 1
    assert applied[0].actor_id == M3_NPC_SCAVENGER_A
    assert applied[0].tool == "take_item"
    assert applied[0].args["item_id"] == ITEM_POTION
    assert rejected[0].actor_id == M3_NPC_SCAVENGER_B
    assert "not in" in rejected[0].verdict_reason
    assert all(row.status == "committed" for row in rows)
    # 审计只增：全量行数 = golden 期望（每个提交尝试每提案一行）。
    all_rows = harness.audit.query(harness.game_id)
    assert len(all_rows) == golden["audit_total"]
    assert all(row.status == "committed" for row in all_rows)
    assert all(
        row.verdict in {"applied", "rejected", "conflict", "deferred", None} for row in all_rows
    )


def test_m3_golden_tamper_detection() -> None:
    """篡改任一事件的 ``rng_seed`` → ``ReplayMismatchError`` 携带首个偏差 ``seq``。"""
    # 用真实装配生成事件列表后篡改（golden 不存全量事件，避免夹具膨胀）。
    golden = _load_golden()
    harness = _build_harness(
        seed=golden["create_game"]["seed"],
        idempotency_key=golden["create_game"]["idempotency_key"],
    )
    _play(harness, golden["script"])
    real_events = harness.repo.read_events(harness.game_id)
    tampered = [
        event.model_copy(update={"rng_seed": (event.rng_seed or 0) + 1})
        if event.seq == 3
        else event
        for event in real_events
    ]
    log = EventLog(_SAVE_SEED)
    log.append(tampered)
    with pytest.raises(ReplayMismatchError) as excinfo:
        log.replay()
    assert excinfo.value.seq == 3
    assert excinfo.value.field == "rng_seed"


def test_m3_golden_deterministic_across_independent_harnesses() -> None:
    """同 seed 同脚本在两个独立装配上重跑：每回合文本与 state_hash 完全一致。"""
    golden = _load_golden()
    first = _build_harness(
        seed=golden["create_game"]["seed"],
        idempotency_key="m3-golden-game-0001",
    )
    second = _build_harness(
        seed=golden["create_game"]["seed"],
        idempotency_key="m3-golden-game-0001-second",
    )
    first_obs = _play(first, golden["script"])
    second_obs = _play(second, golden["script"])

    for left, right in zip(first_obs, second_obs, strict=True):
        assert left["state_hash"] == right["state_hash"]
        assert left["event_ids"] == right["event_ids"]
        assert left["rng_seeds"] == right["rng_seeds"]
        assert left["text_len"] == right["text_len"]
    assert state_hash(_record_state(first)) == state_hash(_record_state(second))


def _record_state(harness: _M3Harness) -> GameState:
    """读取最终状态（确定性对比用）。"""
    record = harness.repo.load(harness.game_id)
    assert record is not None
    return record.state
