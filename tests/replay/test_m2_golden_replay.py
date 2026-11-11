"""M2-009 golden 固定种子回放测试 (第 18 章 §18.3、第 14 章 §14.4、契约 §3/§5/§7)。

golden 数据由固定种子 (``save_seed="save-42"``) 在 M2 生产装配下**一次性生成**
(``tests/fixtures/golden/m2_golden.json``)；变更必须 Lead 审核并在进度文档登记，
**禁止**用 ``--snapshot-update`` 之类手段掩盖回归。

回放口径 (第 18 章 §18.3)：
- 每回合断言 ``ok`` / ``turn_id`` / ``version`` / 事件 ``id`` / ``rng_seed`` / 提交后
  ``state_hash`` / 文本长度与 golden 精确相等；
- 只用盖章事件重建 ``GameState`` → ``state_hash`` 与 golden 最终值一致；
- ``EventLog(save_seed)`` + 历史事件 ``replay()`` 复算 ``id``/``seq``/``rng_seed`` 全部一致；
- 篡改任一事件 ``rng_seed`` → ``ReplayMismatchError`` 携带首个偏差 ``seq``；
- 同 seed 同脚本在两个独立装配上重跑 → 每回合文本与 ``state_hash`` 完全一致。

装配与 ``main.py`` 同构（InMemoryRepository + GameEngine + Query/ActionService +
ToolGate + IntentParseAgent(DisabledLLMClient) + TemplateNarrator + RuleEngine）；
刻意不 import ``main._build_app``，避免被 008 的 SQLite 接线并行改动波及。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from textgame.agents.parser_agent import IntentParseAgent
from textgame.domain.events import EventLog, ReplayMismatchError
from textgame.domain.hashing import state_hash
from textgame.domain.reducers import apply_events
from textgame.domain.rules.engine import RuleEngine
from textgame.domain.scenario import PLAYER_ID
from textgame.llm.client import DisabledLLMClient
from textgame.orchestration.dto import TurnRequest
from textgame.orchestration.intent import DeterministicIntentParser
from textgame.orchestration.narrator import TemplateNarrator
from textgame.orchestration.turn_orchestrator import CreateGameRequest, TurnOrchestrator
from textgame.persistence.memory import InMemoryRepository
from textgame.services.action_service import ActionService
from textgame.services.dto import TurnResult
from textgame.services.game_engine import GameEngine
from textgame.services.query_service import QueryService
from textgame.tools.builtin.m2 import build_m2_tool_registry
from textgame.tools.gate import ToolGate

pytestmark = [pytest.mark.replay]

_GOLDEN_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "golden" / "m2_golden.json"
_SAVE_SEED = "save-42"
_GAME_IDEMPOTENCY_KEY = "golden-game-0001"

#: 七回合脚本 (契约 §3.4 / 009 约定)：环顾(只读) → 问好 → 拿火把 → 拿药水 → 用药水 → 向北走 → 拿锈剑。
_SCRIPT: tuple[str, ...] = (
    "环顾四周",
    "对老哨兵问好",
    "拿起火把",
    "拿起治疗药水",
    "使用治疗药水",
    "向北走进地窖",
    "拿起锈剑",
)

_GOLDEN_COMMENT = (
    "M2 golden 固定种子回放数据 (009)：save_seed=save-42、内置单场景、7 回合脚本；"
    "由本任务在生产装配下一次性生成，逐字段人工核对。变更必须 Lead 审核并在进度文档 "
    "登记 (禁止 --snapshot-update 掩盖回归)。"
)


@dataclass
class _GoldenHarness:
    """golden 回放用装配：内存仓储 + 生产同构编排器 + 已创建存档。"""

    repo: InMemoryRepository
    orchestrator: TurnOrchestrator
    game_id: str
    save_seed: str


def _new_harness() -> _GoldenHarness:
    """按 ``main.py`` 同构装配并创建存档 (seed=42 → save_seed="save-42")。"""
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
        rules=RuleEngine(),
        save_seed_default=_SAVE_SEED,
    )
    view = orchestrator.create_game(
        CreateGameRequest(seed=42, idempotency_key=_GAME_IDEMPOTENCY_KEY)
    )
    return _GoldenHarness(
        repo=repo,
        orchestrator=orchestrator,
        game_id=view.game_id,
        save_seed=view.save_seed or "",
    )


def _record_golden_run(harness: _GoldenHarness, *, with_text: bool = False) -> dict[str, Any]:
    """按脚本跑完 7 回合，返回与 golden 文件同构的记录 (含 game_created 与逐回合期望)。"""
    events = harness.repo.read_events(harness.game_id)
    game_created = events[0]
    record = harness.repo.load(harness.game_id)
    assert record is not None

    turns: list[dict[str, Any]] = []
    version = record.version
    for index, text in enumerate(_SCRIPT):
        request = TurnRequest(
            game_id=harness.game_id,
            text=text,
            expected_version=version,
            idempotency_key=f"golden-turn-{index:06d}",
        )
        result: TurnResult = harness.orchestrator.run(request)
        assert result.ok is True, (index, text, result.error_code)
        assert result.text
        current = harness.repo.load(harness.game_id)
        assert current is not None
        turn: dict[str, Any] = {
            "input": text,
            "ok": result.ok,
            "turn_id": result.turn_id,
            "version": result.version,
            "last_seq": current.last_seq,
            "event_ids": [event.id for event in result.events],
            "rng_seeds": [event.rng_seed for event in result.events],
            "state_hash": state_hash(current.state),
            "text_len": len(result.text),
        }
        if with_text:
            turn["text"] = result.text
        turns.append(turn)
        version = result.version

    final = harness.repo.load(harness.game_id)
    assert final is not None
    return {
        "_comment": _GOLDEN_COMMENT,
        "save_seed": harness.save_seed,
        "create_game": {"seed": 42, "idempotency_key": _GAME_IDEMPOTENCY_KEY},
        "script": list(_SCRIPT),
        "game_created": {
            "seq": game_created.seq,
            "id": game_created.id,
            "turn_id": game_created.turn_id,
            "actor_id": game_created.actor_id,
            "method": game_created.method,
            "event_type": game_created.event_type.value if game_created.event_type else None,
            "rng_seed": game_created.rng_seed,
        },
        "turns": turns,
        "final_state_hash": state_hash(final.state),
        "final_turn": final.state.turn,
        "final_last_seq": final.last_seq,
        "final_version": final.version,
        "text_len_min": 80,
        "text_len_max": 150,
    }


def _load_golden() -> dict[str, Any]:
    """读取 golden fixture (UTF-8 JSON, 断言文件存在)。"""
    assert _GOLDEN_PATH.is_file(), f"golden fixture 缺失: {_GOLDEN_PATH}"
    document: dict[str, Any] = json.loads(_GOLDEN_PATH.read_text(encoding="utf-8"))
    return document


def _flatten_event_ids(golden: dict[str, Any]) -> list[str | None]:
    """golden 中按 seq 顺序展开的事件 id (game_created + 各回合写事件)。"""
    ids: list[str | None] = [golden["game_created"]["id"]]
    for turn in golden["turns"]:
        ids.extend(turn["event_ids"])
    return ids


def _flatten_rng_seeds(golden: dict[str, Any]) -> list[int | None]:
    """golden 中按 seq 顺序展开的 rng_seed。"""
    seeds: list[int | None] = [golden["game_created"]["rng_seed"]]
    for turn in golden["turns"]:
        seeds.extend(turn["rng_seeds"])
    return seeds


# ---------------------------------------------------------------------------
# 用例 1: 逐回合精确回放
# ---------------------------------------------------------------------------


def test_m2_golden_seven_turn_replay() -> None:
    """7 回合逐字段精确等于 golden: ok/turn_id/version/event_ids/rng_seeds/state_hash/长度。"""
    golden = _load_golden()
    actual = _record_golden_run(_new_harness())

    assert actual["save_seed"] == golden["save_seed"] == _SAVE_SEED
    assert actual["game_created"] == golden["game_created"]
    assert len(actual["turns"]) == len(golden["turns"]) == len(_SCRIPT)

    for index, (actual_turn, golden_turn) in enumerate(
        zip(actual["turns"], golden["turns"], strict=True)
    ):
        assert actual_turn["input"] == golden_turn["input"] == _SCRIPT[index]
        assert actual_turn["ok"] is golden_turn["ok"] is True
        assert actual_turn["turn_id"] == golden_turn["turn_id"], index
        assert actual_turn["version"] == golden_turn["version"], index
        assert actual_turn["last_seq"] == golden_turn["last_seq"], index
        assert actual_turn["event_ids"] == golden_turn["event_ids"], index
        assert actual_turn["rng_seeds"] == golden_turn["rng_seeds"], index
        assert actual_turn["state_hash"] == golden_turn["state_hash"], index
        assert actual_turn["text_len"] == golden_turn["text_len"], index
        assert golden["text_len_min"] <= actual_turn["text_len"] <= golden["text_len_max"]

    assert actual["final_state_hash"] == golden["final_state_hash"]
    assert actual["final_turn"] == golden["final_turn"]
    assert actual["final_last_seq"] == golden["final_last_seq"]
    assert actual["final_version"] == golden["final_version"]


# ---------------------------------------------------------------------------
# 用例 2: 只用事件重建状态 + EventLog 派生式 replay
# ---------------------------------------------------------------------------


def test_m2_replay_state_hash_from_events_only() -> None:
    """只用盖章事件重建 GameState → state_hash 等于 golden; EventLog.replay 校验全部种子。"""
    golden = _load_golden()
    harness = _new_harness()
    _record_golden_run(harness)
    events = harness.repo.read_events(harness.game_id)
    assert len(events) == golden["final_last_seq"]

    # 只用事件: 首条 game_created 从空存档投影, 其余经 GameState.apply 纯投影。
    first, *rest = events
    rebuilt = apply_events(None, [first]).apply(rest)
    assert state_hash(rebuilt) == golden["final_state_hash"]
    assert rebuilt.turn == golden["final_turn"]
    assert rebuilt.rng_state.last_seq == golden["final_last_seq"]

    # EventLog 派生式 replay: 按 save_seed 重算每条事件的 id/seq/rng_seed。
    log = EventLog(golden["save_seed"])
    log.append(events)
    replayed = log.replay()
    assert log.last_seq == golden["final_last_seq"]
    assert [event.seq for event in replayed] == list(range(1, len(events) + 1))
    assert [event.id for event in replayed] == _flatten_event_ids(golden)
    assert [event.rng_seed for event in replayed] == _flatten_rng_seeds(golden)


# ---------------------------------------------------------------------------
# 用例 3: 篡改检出 (首个偏差 seq)
# ---------------------------------------------------------------------------


def test_m2_golden_detects_tampered_rng_seed() -> None:
    """篡改某条事件的 rng_seed 后 replay 抛 ReplayMismatchError, 且携带首个偏差 seq。"""
    harness = _new_harness()
    _record_golden_run(harness)
    events = harness.repo.read_events(harness.game_id)
    tamper_index = 3  # seq=4 的写事件
    original = events[tamper_index]
    assert original.seq is not None
    tampered = list(events)
    tampered[tamper_index] = original.model_copy(update={"rng_seed": 0})

    log = EventLog(_SAVE_SEED)
    log.append(events)  # append 校验 seq/id/ts/rng_seed；篡改用白盒注入模拟已入库被改
    log._events[tamper_index] = tampered[tamper_index]
    with pytest.raises(ReplayMismatchError) as exc_info:
        log.replay()

    assert exc_info.value.seq == original.seq
    assert exc_info.value.field == "rng_seed"
    assert exc_info.value.actual == 0


# ---------------------------------------------------------------------------
# 用例 4: 同 seed 同输入两个独立装配
# ---------------------------------------------------------------------------


def test_m2_same_seed_same_inputs_same_hash() -> None:
    """两个独立 harness 重跑同一脚本: 每回合文本逐字一致、state_hash 与 golden 一致。"""
    golden = _load_golden()
    first = _record_golden_run(_new_harness(), with_text=True)
    second = _record_golden_run(_new_harness(), with_text=True)

    for index, (left, right) in enumerate(zip(first["turns"], second["turns"], strict=True)):
        assert left["text"] == right["text"], index
        assert left["state_hash"] == right["state_hash"], index
        assert left["event_ids"] == right["event_ids"], index
        assert left["rng_seeds"] == right["rng_seeds"], index
        assert left["state_hash"] == golden["turns"][index]["state_hash"], index

    assert first["final_state_hash"] == second["final_state_hash"] == golden["final_state_hash"]
