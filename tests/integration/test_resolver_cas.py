"""M3-013 集成验收：Resolver 裁决 + CAS 提交（`tests/integration/test_resolver_cas.py`）。

覆盖 M3 出口与第 11 章 §11.3 执行建议：
- 两 NPC 同回合提交冲突提案（抢同一物品）→ 只一个 ``applied``、另一个 ``rejected`` 且理由可审计；
- 多事件回合事件全局 ``seq`` 连续、``version`` 每回合 +1、盖章事件可重建 ``state_hash``；
- CAS：过期 ``expected_version`` 零写入 + ``CONFLICT``；并发提交失败后最多 3 次重试且**只重裁决**；
- 玩家提案被 Resolver 拒绝 → 零写入 + 审计 ``aborted`` 行（失败也有记录）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from textgame.domain.hashing import state_hash
from textgame.domain.models import GameState, SideEffect, ToolSpec
from textgame.domain.scenario import (
    ITEM_POTION,
    M3_NPC_SCAVENGER_A,
    M3_NPC_SCAVENGER_B,
    PLAYER_ID,
)
from textgame.orchestration.turn_orchestrator import CreateGameRequest
from textgame.services.dto import TurnResult

_GAME_KEY = "m3-cas-game-0001"
_GOLDEN_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "golden" / "m3_golden.json"


def _create_m3_game(harness: Any, *, key: str = _GAME_KEY) -> dict[str, Any]:
    """用编排层创建 M3 多角色存档（不经过 HTTP，便于直接断言仓储状态）。"""
    view = harness.orchestrator.create_game(
        CreateGameRequest(seed=42, idempotency_key=key, scenario="m3")
    )
    return view.model_dump(mode="json")


def _run_turn(
    harness: Any,
    *,
    game_id: str,
    text: str,
    expected_version: int,
    key: str,
) -> TurnResult:
    """执行一个回合（编排层直接调用，返回 ``TurnResult``）。"""
    return harness.orchestrator.run(
        _turn_request(game_id=game_id, text=text, expected_version=expected_version, key=key)
    )


def _turn_request(*, game_id: str, text: str, expected_version: int, key: str) -> Any:
    """构造编排层回合请求。"""
    from textgame.orchestration.dto import TurnRequest

    return TurnRequest(
        game_id=game_id, input=text, expected_version=expected_version, idempotency_key=key
    )


def _record(harness: Any, game_id: str) -> Any:
    """读取当前存档记录（重试断言版本/事件用）。"""
    record = harness.repo.load(game_id)
    assert record is not None
    return record


# ---------------------------------------------------------------------------
# 冲突裁决
# ---------------------------------------------------------------------------
def test_two_npcs_conflict_one_applied_one_rejected(m3_game_harness) -> None:
    """两 NPC 同回合抢同一瓶药水：先裁决者得，后者 rejected 并附理由（第 11 章 §11.3）。"""
    harness = m3_game_harness
    game = _create_m3_game(harness)
    game_id = game["game_id"]

    result = _run_turn(
        harness,
        game_id=game_id,
        text="环顾四周",
        expected_version=1,
        key="m3-cas-turn-01",
    )

    assert result.ok is True
    assert result.version == 2
    assert len(result.events) == 1
    event = result.events[0]
    assert event.event_type is not None and event.event_type.value == "ITEM_TAKEN"
    assert event.actor_id == M3_NPC_SCAVENGER_A  # actor_id 升序先裁决
    assert event.params["item_id"] == ITEM_POTION
    assert event.id == "evt_000002" and event.seq == 2
    assert event.params["proposal_id"] == "p_000002_001"
    assert event.params["resolution_id"] == "r_000002_001"

    record = _record(harness, game_id)
    assert record.state.characters[M3_NPC_SCAVENGER_A].inventory == [ITEM_POTION]
    assert record.state.characters[M3_NPC_SCAVENGER_B].inventory == []
    assert (
        ITEM_POTION not in record.state.rooms[record.state.characters[PLAYER_ID].location].item_ids
    )

    audit_rows = harness.audit.query(game_id, turn_id=2)
    assert len(audit_rows) == 2
    by_actor = {row.actor_id: row for row in audit_rows}
    applied = by_actor[M3_NPC_SCAVENGER_A]
    rejected = by_actor[M3_NPC_SCAVENGER_B]
    assert applied.verdict == "applied" and applied.status == "committed"
    assert rejected.verdict == "rejected" and rejected.status == "committed"
    assert "not in" in rejected.verdict_reason  # item ... is not in room ...
    assert applied.tool == rejected.tool == "take_item"
    assert applied.source == rejected.source == "npc"
    assert 80 <= len(result.text) <= 150


def test_multi_event_turn_keeps_global_seq_continuous(m3_game_harness) -> None:
    """玩家 + NPC 同回合各产事件的回合：seq 连续、version 恰好 +1、事件可重建状态。"""
    harness = m3_game_harness
    game = _create_m3_game(harness)
    game_id = game["game_id"]

    _run_turn(harness, game_id=game_id, text="拿起火把", expected_version=1, key="m3-cas-turn-11")
    _run_turn(harness, game_id=game_id, text="向北走", expected_version=2, key="m3-cas-turn-12")

    events = harness.repo.read_events(game_id)
    assert [event.seq for event in events] == list(range(1, len(events) + 1))
    assert [event.id for event in events] == [f"evt_{seq:06d}" for seq in range(1, len(events) + 1)]
    record = _record(harness, game_id)
    assert record.version == 3
    assert record.last_seq == len(events) == 4  # game_created + 2（turn 2）+ 1（turn 3）
    assert record.state.turn == 3
    # 盖章事件叠加可重建当前 state（事件溯源一致性）。
    initial = GameState.model_validate(events[0].result["initial_state"])
    rebuilt = initial.apply(events[1:])
    assert state_hash(rebuilt) == state_hash(record.state)
    # turn 2 是玩家 + NPC 的同回合多事件提交；turn 3 只有玩家移动。
    turn2 = [event for event in events if event.turn_id == 2]
    assert len(turn2) == 2
    assert {event.actor_id for event in turn2} == {PLAYER_ID, M3_NPC_SCAVENGER_A}
    turn3 = [event for event in events if event.turn_id == 3]
    assert len(turn3) == 1
    assert turn3[0].actor_id == PLAYER_ID


# ---------------------------------------------------------------------------
# CAS
# ---------------------------------------------------------------------------
def test_stale_expected_version_conflicts_with_zero_write(m3_game_harness) -> None:
    """过期 expected_version → CONFLICT，且不写事件、版本不变（第 11 章提交门）。"""
    harness = m3_game_harness
    game = _create_m3_game(harness)
    game_id = game["game_id"]
    _run_turn(harness, game_id=game_id, text="环顾四周", expected_version=1, key="m3-cas-turn-21")
    before = _record(harness, game_id)
    events_before = harness.repo.read_events(game_id)

    result = _run_turn(
        harness, game_id=game_id, text="拿起火把", expected_version=1, key="m3-cas-turn-22"
    )

    assert result.ok is False
    assert result.error_code is not None and result.error_code.value == "CONFLICT"
    assert result.events == []
    after = _record(harness, game_id)
    assert after.version == before.version == 2
    assert after.last_seq == before.last_seq
    assert harness.repo.read_events(game_id) == events_before


def test_cas_conflict_retries_re_adjudicate_without_reparse(m3_game_harness) -> None:
    """提交冲突后最多 3 次重试：只重裁决，不重解析、不重复叙事（第 11 章 §11.3）。"""
    harness = m3_game_harness
    game = _create_m3_game(harness)
    game_id = game["game_id"]

    parse_calls = {"n": 0}
    original_parse = harness.orchestrator._parser.parse

    def counting_parse(*args: Any, **kwargs: Any) -> Any:
        parse_calls["n"] += 1
        return original_parse(*args, **kwargs)

    original_commit = harness.repo.commit
    commit_calls = {"n": 0}

    def flaky_commit(payload: Any) -> bool:
        commit_calls["n"] += 1
        if commit_calls["n"] == 1:
            return False  # 模拟并发提交抢先在 check 与 commit 之间写入
        return original_commit(payload)

    harness.orchestrator._parser.parse = counting_parse  # type: ignore[method-assign]
    harness.repo.commit = flaky_commit  # type: ignore[method-assign]
    try:
        result = _run_turn(
            harness, game_id=game_id, text="拿起火把", expected_version=1, key="m3-cas-turn-31"
        )
    finally:
        harness.orchestrator._parser.parse = original_parse  # type: ignore[method-assign]
        harness.repo.commit = original_commit  # type: ignore[method-assign]

    assert result.ok is True
    assert parse_calls["n"] == 1, "CAS 重试不得重新调用解析（不产生额外 LLM 成本）"
    assert commit_calls["n"] == 2
    record = _record(harness, game_id)
    assert record.version == 2
    assert record.last_seq == 3  # 玩家取火把 + 拾荒者甲取药水（同回合 2 条事件）
    audit_rows = harness.audit.query(game_id, turn_id=2)
    statuses = [row.status for row in audit_rows]
    # 冲突回合有 3 条提案（玩家取火把 + 两名拾荒者抢药水），每次尝试各 1 行：
    # 第 1 次提交冲突 → 3 行 conflicted；第 2 次提交成功 → 3 行 committed。
    assert statuses.count("conflicted") == 3
    assert statuses.count("committed") == 3
    assert sorted(row.attempt for row in audit_rows) == [1, 1, 1, 2, 2, 2]
    assert harness.repo.read_events(game_id)[-1].turn_id == 2


def test_failed_player_proposal_writes_aborted_audit_and_no_state(m3_game_harness) -> None:
    """玩家提案被 Resolver 拒绝：零写入、版本不变、审计记 aborted（失败也有记录）。"""
    harness = m3_game_harness
    game = _create_m3_game(harness)
    game_id = game["game_id"]
    before = _record(harness, game_id)
    events_before = harness.repo.read_events(game_id)

    # 药水在地上的房间里，背包里没有 → use_item 前置条件失败（服务端推导）。
    result = _run_turn(
        harness, game_id=game_id, text="使用治疗药水", expected_version=1, key="m3-cas-turn-41"
    )

    assert result.ok is False
    assert result.events == []
    after = _record(harness, game_id)
    assert after.version == before.version == 1
    assert after.last_seq == before.last_seq == 1
    assert harness.repo.read_events(game_id) == events_before
    audit_rows = harness.audit.query(game_id, turn_id=2)
    assert audit_rows, "被拒绝的回合也必须有审计行"
    assert all(row.status == "aborted" for row in audit_rows)
    player_rows = [row for row in audit_rows if row.actor_id == PLAYER_ID]
    assert player_rows and player_rows[0].verdict in {"rejected", "conflict"}


# ---------------------------------------------------------------------------
# Resolver 纯函数与 ToolGate 端口契约（白盒）
# ---------------------------------------------------------------------------
def test_resolver_is_pure_and_only_accepts_draft_events(m3_game_harness) -> None:
    """Resolver 端口契约：两次裁决同输入同输出、不产生盖章事件（第 11 章 §11.2）。"""
    from textgame.domain.models import Proposal
    from textgame.orchestration.proposal_queue import ProposalQueue, ProposalSource

    harness = m3_game_harness
    game = _create_m3_game(harness)
    game_id = game["game_id"]
    record = _record(harness, game_id)
    queue = ProposalQueue(turn_id=2)
    queue.enqueue(
        Proposal(
            turn_id=2,
            actor_id=M3_NPC_SCAVENGER_A,
            tool="take_item",
            args={"item_id": ITEM_POTION},
            reason="test",
            priority=50,
        ),
        source=ProposalSource.NPC,
    )
    entries = queue.drain()

    resolver = harness.orchestrator._resolver
    assert resolver is not None
    first = resolver.resolve(entries, record.state)
    second = resolver.resolve(entries, record.state)
    assert [resolution.model_dump(mode="json") for resolution in first] == [
        resolution.model_dump(mode="json") for resolution in second
    ]
    assert first[0].verdict == "applied"
    assert all(not event.is_stamped for event in first[0].applied_events)
    # 纯函数不改变输入状态。
    assert record.state.characters[M3_NPC_SCAVENGER_A].inventory == []
    # 事件参数带审计键（D-M3-8）。
    params = first[0].applied_events[0].params
    assert params["proposal_id"] == "p_000002_001"
    assert params["resolution_id"] == "r_000002_001"


def test_m3_scenario_assets_and_tool_specs_are_server_owned(m3_game_harness) -> None:
    """装配自检：M3 场景/工具声明由服务端静态配置提供，测试参数不可注入 scope。"""
    harness = m3_game_harness
    game = _create_m3_game(harness)
    record = _record(harness, game["game_id"])
    assert set(record.state.characters) >= {PLAYER_ID, M3_NPC_SCAVENGER_A, M3_NPC_SCAVENGER_B}
    registry = harness.orchestrator._registry
    assert registry is not None
    take_spec: ToolSpec = registry.get("take_item")
    assert take_spec.side_effect is SideEffect.DESTRUCTIVE
    assert take_spec.actor_scope == frozenset({"player", "system", "npc:scavenger"})
    with pytest.raises(KeyError):
        registry.get("no_such_tool")
    # golden fixture 与场景口径一致（无 process 级随机注入）。
    golden = json.loads(_GOLDEN_PATH.read_text(encoding="utf-8"))
    assert golden["create_game"]["scenario"] == "m3"
