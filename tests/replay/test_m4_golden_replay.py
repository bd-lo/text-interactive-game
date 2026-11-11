"""M4-018 制造 golden 回放测试（第 18 章 §18.3、第 14 章 §14.4、D-M4-13）。

golden 数据（tests/fixtures/golden/m4_golden.json）由生产同构装配（M3 编排链路 +
ToolGate/CraftService + 审计，character_agents={}，D-M4-13）在 save_seed=save-42、
scenario=m4 下一次性生成并人工核对；变更必须 Lead 审核并在进度文档登记，禁止
snapshot-update 掩盖回归。

覆盖：5 个 case（确定性成功/材料不足零写入/自由成功/自由 Schema 违规回滚/自由一致性
违规回滚）逐回合字段、失败回合零写入与审计行、craft 事件语义、只用盖章事件重建
state_hash、篡改 rng_seed 检出、双独立装配同 seed 一致、事件链连续与审计只增。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from conftest import build_m4_prod_harness
from textgame.domain.events import Event, EventLog, ReplayMismatchError
from textgame.domain.hashing import state_hash
from textgame.domain.models import GameState
from textgame.domain.reducers import apply_events
from textgame.domain.scenario import PLAYER_ID
from textgame.orchestration.dto import TurnRequest
from textgame.orchestration.turn_orchestrator import CreateGameRequest

pytestmark = [pytest.mark.replay]

_GOLDEN_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "golden" / "m4_golden.json"
_SAVE_SEED = "save-42"


def _load_golden() -> dict[str, Any]:
    """读取 golden fixture（只读；测试不得改写）。"""
    return json.loads(_GOLDEN_PATH.read_text(encoding="utf-8"))


_GOLDEN = _load_golden()
_CASES = _GOLDEN["cases"]
_CASE_IDS = [case["case_id"] for case in _CASES]
_FAILED_CASE_IDS = [
    "m4_case_insufficient_materials",
    "m4_case_free_schema_violation",
    "m4_case_free_consistency_violation",
]
_EVENT_CASE_IDS = ["m4_case_deterministic_success", "m4_case_free_craft_success"]

_TURN_FIELDS: tuple[str, ...] = (
    "input",
    "attempted_turn",
    "ok",
    "error_code",
    "turn_id",
    "version",
    "last_seq",
    "event_ids",
    "event_actors",
    "event_methods",
    "event_types",
    "rng_seeds",
    "state_hash",
    "text_len",
    "audit",
)


def _case(case_id: str) -> dict[str, Any]:
    for candidate in _CASES:
        if candidate["case_id"] == case_id:
            return candidate
    raise KeyError(case_id)


def _new_harness() -> Any:
    """构建独立的生产同构装配（内存仓储；关闭 HTTP 客户端，直接驱动编排器）。"""
    harness, client = build_m4_prod_harness()
    client.close()
    return harness


def _audit_summary(harness: Any, game_id: str, turn_id: int) -> list[dict[str, Any]]:
    rows = harness.audit.query(game_id, turn_id=turn_id)
    return [
        {
            "proposal_id": row.proposal_id,
            "actor_id": row.actor_id,
            "tool": row.tool,
            "verdict": row.verdict,
            "status": row.status,
            "attempt": row.attempt,
        }
        for row in sorted(rows, key=lambda row: (row.turn_id, row.proposal_id))
    ]


def _play(harness: Any, case: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """按 case 脚本逐回合执行，返回与 golden 同构的观测与 game_id。"""
    harness.fake_llm.reset(case.get("llm_responses", []))
    create = _GOLDEN["create_game"]
    view = harness.orchestrator.create_game(
        CreateGameRequest(
            seed=create["seed"],
            idempotency_key=create["idempotency_key"],
            scenario=create["scenario"],
        )
    )
    game_id = view.game_id
    record = harness.repo.load(game_id)
    assert record is not None
    version = record.version
    observations: list[dict[str, Any]] = []
    for index, text in enumerate(case["script"], start=1):
        before = harness.repo.load(game_id)
        assert before is not None
        attempted_turn = before.state.turn + 1
        result = harness.orchestrator.run(
            TurnRequest(
                game_id=game_id,
                text=text,
                expected_version=version,
                idempotency_key=f"m4-golden-turn-{index:02d}",
            )
        )
        after = harness.repo.load(game_id)
        assert after is not None
        observations.append(
            {
                "input": text,
                "attempted_turn": attempted_turn,
                "ok": result.ok,
                "error_code": None if result.error_code is None else result.error_code.value,
                "turn_id": after.state.turn,
                "version": after.version,
                "last_seq": after.last_seq,
                "event_ids": [event.id for event in result.events],
                "event_actors": [event.actor_id for event in result.events],
                "event_methods": [event.method for event in result.events],
                "event_types": [
                    None if event.event_type is None else event.event_type.value
                    for event in result.events
                ],
                "rng_seeds": [event.rng_seed for event in result.events],
                "state_hash": state_hash(after.state),
                "text_len": len(result.text),
                "audit": _audit_summary(harness, game_id, attempted_turn),
            }
        )
        version = after.version
    return observations, game_id


def _final(harness: Any, game_id: str) -> dict[str, Any]:
    record = harness.repo.load(game_id)
    assert record is not None
    events = harness.repo.read_events(game_id)
    return {
        "final_state_hash": state_hash(record.state),
        "final_version": record.version,
        "final_last_seq": record.last_seq,
        "final_turn": record.state.turn,
        "event_total": len(events),
        "audit_total": len(harness.audit.query(game_id)),
    }


def _rebuild_from_events(events: list[Event]) -> GameState:
    """只用盖章事件从空存档重建最终状态（含 game_created 投影，不读仓储）。"""
    return apply_events(None, events)


# ---------------------------------------------------------------------------
# 逐回合 golden
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("case", _CASES, ids=_CASE_IDS)
def test_m4_golden_replay_matches_every_turn(case: dict[str, Any]) -> None:
    """每个 case 的逐回合事件/种子/哈希/审计/文本长度与 golden 精确一致。"""
    harness = _new_harness()
    observations, game_id = _play(harness, case)

    assert len(observations) == len(case["turns"])
    for observed, expected in zip(observations, case["turns"], strict=True):
        for field in _TURN_FIELDS:
            assert observed[field] == expected[field], (
                f"{case['case_id']} turn {expected.get('attempted_turn')} "
                f"field {field} mismatch: golden={expected[field]!r} actual={observed[field]!r}"
            )
        if observed["ok"]:
            assert 80 <= observed["text_len"] <= 150

    final = _final(harness, game_id)
    for field in (
        "final_state_hash",
        "final_version",
        "final_last_seq",
        "final_turn",
        "event_total",
        "audit_total",
    ):
        assert final[field] == case[field]


@pytest.mark.parametrize(
    "case_id",
    _FAILED_CASE_IDS,
    ids=_FAILED_CASE_IDS,
)
def test_m4_golden_failed_turn_zero_write_and_audit(case_id: str) -> None:
    """失败回合：version/last_seq/state_hash 三不变、零事件，审计 rejected/aborted。"""
    case = _case(case_id)
    harness = _new_harness()
    observations, _ = _play(harness, case)

    failed = [turn for turn in observations if not turn["ok"]]
    assert len(failed) == 1
    failed_index = observations.index(failed[0])
    before = observations[failed_index - 1]
    actual = failed[0]
    assert actual["event_ids"] == []
    assert actual["version"] == before["version"]
    assert actual["last_seq"] == before["last_seq"]
    assert actual["state_hash"] == before["state_hash"]
    assert len(actual["audit"]) == 1
    assert actual["audit"][0]["verdict"] == "rejected"
    assert actual["audit"][0]["status"] == "aborted"
    assert actual["audit"][0]["actor_id"] == PLAYER_ID


# ---------------------------------------------------------------------------
# 事件语义 / 纯事件重建 / 篡改 / 双装配
# ---------------------------------------------------------------------------
def test_m4_golden_deterministic_craft_event_semantics() -> None:
    """确定性 case：CRAFT_DETERMINISTIC 载荷、成功/质量/副产物、rng_seed 与盖章一致。"""
    case = _case("m4_case_deterministic_success")
    harness = _new_harness()
    _, game_id = _play(harness, case)
    events = harness.repo.read_events(game_id)
    craft_events = [event for event in events if event.method == "craft_item"]
    assert len(craft_events) == 1
    event = craft_events[0]
    assert event.event_type is not None and event.event_type.value == "CRAFT_DETERMINISTIC"
    assert event.event_type.value not in {"CRAFT_GENERATED", "CRAFT_REJECTED"}
    assert event.params["mode"] == "recipe"
    assert event.params["recipe_id"] == "recipe.forge_sword"
    assert event.params["recipe_version"] == 1
    craft = event.result["craft"]
    assert craft["success"] is True
    assert craft["quality"] == "common"
    assert [item["template_id"] for item in craft["byproducts"]] == ["item.iron_slag"]
    assert len(event.result["produced_items"]) == 2
    assert event.rng_seed == craft["rng_seed"]
    record = harness.repo.load(game_id)
    assert record is not None
    names = [
        record.state.items[item_id].name for item_id in record.state.characters[PLAYER_ID].inventory
    ]
    assert "铁剑" in names
    assert "铁渣" in names


def test_m4_golden_free_craft_event_semantics() -> None:
    """自由 case：CRAFT_GENERATED、params 只落 sha256/proposal_hash/prompt_version，无数值。"""
    case = _case("m4_case_free_craft_success")
    harness = _new_harness()
    _, game_id = _play(harness, case)
    events = harness.repo.read_events(game_id)
    craft_events = [event for event in events if event.method == "craft_item"]
    assert len(craft_events) == 1
    event = craft_events[0]
    assert event.event_type is not None and event.event_type.value == "CRAFT_GENERATED"
    assert event.params["mode"] == "free"
    assert event.params["prompt_version"] == "craft@v1"
    assert len(event.params["proposal_hash"]) == 64
    assert len(event.params["free_description_sha256"]) == 64
    craft = event.result["craft"]
    assert craft["success"] is True
    produced = event.result["produced_items"]
    assert len(produced) == 1
    assert produced[0]["name"] == "简易电击棒"
    assert produced[0]["stats"] == {}
    assert event.rng_seed == craft["rng_seed"]
    raw = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
    assert "用铁锭和铜线" not in raw


@pytest.mark.parametrize("case", _CASES, ids=_CASE_IDS)
def test_m4_golden_rebuild_from_events_only(case: dict[str, Any]) -> None:
    """只用盖章事件重建最终状态，state_hash 与 golden 一致（事件溯源权威）。"""
    harness = _new_harness()
    _, game_id = _play(harness, case)
    events = harness.repo.read_events(game_id)
    rebuilt = _rebuild_from_events(events)

    assert state_hash(rebuilt) == case["final_state_hash"]


@pytest.mark.parametrize("case_id", _EVENT_CASE_IDS, ids=_EVENT_CASE_IDS)
def test_m4_golden_tamper_detection(case_id: str) -> None:
    """篡改制造事件的 rng_seed → ReplayMismatchError 带首个偏差 seq。"""
    case = _case(case_id)
    harness = _new_harness()
    _, game_id = _play(harness, case)
    real_events = harness.repo.read_events(game_id)
    craft_event = next(event for event in real_events if event.method == "craft_item")
    assert craft_event.seq is not None
    tampered = [
        event.model_copy(update={"rng_seed": (event.rng_seed or 0) + 1})
        if event.seq == craft_event.seq
        else event
        for event in real_events
    ]
    log = EventLog(_SAVE_SEED)
    log.append(real_events)
    log._events[craft_event.seq - 1] = tampered[craft_event.seq - 1]

    with pytest.raises(ReplayMismatchError) as excinfo:
        log.replay()
    assert excinfo.value.seq == craft_event.seq
    assert excinfo.value.field == "rng_seed"


@pytest.mark.parametrize("case_id", _EVENT_CASE_IDS, ids=_EVENT_CASE_IDS)
def test_m4_golden_deterministic_across_independent_harnesses(case_id: str) -> None:
    """同 seed 同脚本在两个独立装配上重跑：逐回合 events/state_hash/文本一致。"""
    case = _case(case_id)
    first = _new_harness()
    second = _new_harness()
    first_obs, first_game = _play(first, case)
    second_obs, second_game = _play(second, case)

    for left, right in zip(first_obs, second_obs, strict=True):
        for field in (
            "ok",
            "version",
            "last_seq",
            "event_ids",
            "rng_seeds",
            "state_hash",
            "text_len",
        ):
            assert left[field] == right[field]
        assert left["audit"] == right["audit"]
    assert _final(first, first_game) == _final(second, second_game)


@pytest.mark.parametrize("case", _CASES, ids=_CASE_IDS)
def test_m4_golden_event_chain_and_audit_monotonic(case: dict[str, Any]) -> None:
    """事件链全局 seq 连续、id=evt_<seq:06d>；审计只增且状态词合法。"""
    harness = _new_harness()
    _, game_id = _play(harness, case)
    events = harness.repo.read_events(game_id)

    assert [event.seq for event in events] == list(range(1, len(events) + 1))
    assert [event.id for event in events] == [f"evt_{seq:06d}" for seq in range(1, len(events) + 1)]
    assert len(events) == case["event_total"]
    rows = harness.audit.query(game_id)
    assert len(rows) == case["audit_total"]
    for row in rows:
        assert row.status in {"committed", "aborted", "conflicted"}
        assert row.verdict in {"applied", "rejected", "conflict", "deferred", None}
