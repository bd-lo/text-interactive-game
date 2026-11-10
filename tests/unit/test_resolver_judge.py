"""M5E-2 Resolver needs_judge 分支单测（纯函数裁决 + 裁判证据懒读取）。

覆盖：非 needs_judge 提案完全不读取 judgements（ExplodingJudge 零访问，保持既有安全测试
口径）；needs_judge + 合法证据 → verify 采纳；judge=None/低置信 → deterministic_fallback
且服务层不被调用；judgements 缺省/短列表不越界；槽位与 entries 同序；同输入两次裁决逐字相同。
"""

from __future__ import annotations

from typing import Any

import pytest

from textgame.domain.events import Event, EventType
from textgame.domain.judge import JudgeOutput
from textgame.domain.models import GameState, Proposal, Resolution
from textgame.domain.results import Result
from textgame.domain.rules.engine import RuleEngine
from textgame.domain.scenario import (
    ITEM_SWORD,
    ITEM_TORCH,
    M3_NONPLAYER_TAGS,
    PLAYER_ID,
    build_initial_state,
)
from textgame.orchestration.proposal_queue import ProposalQueue, ProposalSource, QueueEntry
from textgame.orchestration.resolver import Resolver
from textgame.services.actor_directory import ActorDirectory

_SAVE_SEED = "save-42"
_TURN_ID = 2


class _ExplodingJudge:
    """任何属性访问都失败：证明确实没有被 Resolver 读取。"""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"judgements must not be read for non-needs_judge (accessed {name!r})")


class _RecordingRouter:
    """服务层替身：记录调用并返回可通过投影的草稿事件（take_item / move_to）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call(self, *, tool: str, ctx: Any, args: dict[str, Any], state: GameState) -> Result:
        self.calls.append((tool, dict(args)))
        character = state.characters[ctx.actor_id]
        if tool == "take_item":
            event = Event(
                turn_id=ctx.turn_id,
                actor_id=ctx.actor_id,
                method=tool,
                event_type=EventType.ITEM_TAKEN,
                params=dict(args),
                result={"to_inventory": True},
            )
        else:
            room = state.rooms[character.location]
            event = Event(
                turn_id=ctx.turn_id,
                actor_id=ctx.actor_id,
                method=tool,
                event_type=EventType.CHARACTER_MOVED,
                params=dict(args),
                result={"to": room.exits[str(args["direction"])]},
            )
        return Result.success(data={"tool": tool}, events=[event])


class _CountingRules(RuleEngine):
    """真实 RuleEngine + 调用计数（验证分支走 verify 还是 fallback）。"""

    def __init__(self, *, agent_assisted_tools: frozenset[str] = frozenset()) -> None:
        super().__init__(agent_assisted_tools=agent_assisted_tools)
        self.verify_calls = 0
        self.fallback_calls = 0

    def verify_agent_override(self, ctx: Any, proposal: Proposal, judge: JudgeOutput) -> Result:
        self.verify_calls += 1
        return super().verify_agent_override(ctx, proposal, judge)

    def deterministic_fallback(self, ctx: Any, proposal: Proposal, snapshot: GameState) -> Result:
        self.fallback_calls += 1
        return super().deterministic_fallback(ctx, proposal, snapshot)


def _entries(*specs: tuple[str, dict[str, Any]]) -> list[QueueEntry]:
    """构造已出队提案（ProposalQueue 派生 proposal_id，顺序即裁决顺序）。"""
    queue = ProposalQueue(turn_id=_TURN_ID)
    for tool, args in specs:
        queue.enqueue(
            Proposal(
                turn_id=_TURN_ID,
                actor_id=PLAYER_ID,
                tool=tool,
                args=dict(args),
                reason="玩家明确动作",
                priority=100,
            ),
            source=ProposalSource.PLAYER,
        )
    return queue.drain()


def _resolver(rules: RuleEngine) -> Resolver:
    return Resolver(
        rules=rules,
        services=_RecordingRouter(),
        actors=ActorDirectory(nonplayer_tags=M3_NONPLAYER_TAGS),
        save_seed=_SAVE_SEED,
    )


def _state() -> GameState:
    return build_initial_state(save_seed=_SAVE_SEED)


def _judge(**overrides: object) -> JudgeOutput:
    payload: dict[str, object] = {
        "verdict": "allow",
        "reason": "火把就在脚边，取用没有阻碍。",
        "evidence_refs": ["火把"],
        "confidence": 0.9,
    }
    payload.update(overrides)
    return JudgeOutput.model_validate(payload)


def _take_torch() -> list[QueueEntry]:
    return _entries(("take_item", {"item_id": ITEM_TORCH}))


MOVE_NORTH: tuple[str, dict[str, Any]] = ("move_to", {"direction": "north"})


# ---------------------------------------------------------------------------
# 非 needs_judge：judgements 零访问
# ---------------------------------------------------------------------------
def test_non_needs_judge_does_not_read_judgements() -> None:
    """非 needs_judge 提案注入 ExplodingJudge 依然 applied（旧安全测试口径保持）。"""
    rules = _CountingRules(agent_assisted_tools=frozenset({"take_item"}))
    services = _RecordingRouter()
    resolver = Resolver(
        rules=rules,
        services=services,
        actors=ActorDirectory(nonplayer_tags=M3_NONPLAYER_TAGS),
        save_seed=_SAVE_SEED,
    )

    results = resolver.resolve(_entries(MOVE_NORTH), _state(), [_ExplodingJudge()])

    assert results[0].verdict == "applied"
    assert rules.verify_calls == 0 and rules.fallback_calls == 0
    assert len(services.calls) == 1


# ---------------------------------------------------------------------------
# needs_judge 分支
# ---------------------------------------------------------------------------
def test_needs_judge_with_valid_judge_is_applied_with_judge_reason() -> None:
    """合法证据 → verify 采纳、服务层调用、applied reason = 裁判理由。"""
    rules = _CountingRules(agent_assisted_tools=frozenset({"take_item"}))
    services = _RecordingRouter()
    resolver = Resolver(
        rules=rules,
        services=services,
        actors=ActorDirectory(nonplayer_tags=M3_NONPLAYER_TAGS),
        save_seed=_SAVE_SEED,
    )

    results = resolver.resolve(_take_torch(), _state(), [_judge()])

    assert results[0].verdict == "applied"
    assert results[0].reason == "火把就在脚边，取用没有阻碍。"
    assert rules.verify_calls == 1 and rules.fallback_calls == 0
    assert len(services.calls) == 1
    assert not results[0].applied_events[0].is_stamped


@pytest.mark.parametrize("judgements", [None, [], [None]], ids=["none", "empty", "empty-slot"])
def test_needs_judge_without_evidence_uses_conservative_fallback(
    judgements: list[JudgeOutput | None] | None,
) -> None:
    """证据缺失（None/空列表/槽位 None）→ fallback、服务层不被调用、rejected 零事件。"""
    rules = _CountingRules(agent_assisted_tools=frozenset({"take_item"}))
    services = _RecordingRouter()
    resolver = Resolver(
        rules=rules,
        services=services,
        actors=ActorDirectory(nonplayer_tags=M3_NONPLAYER_TAGS),
        save_seed=_SAVE_SEED,
    )

    results = resolver.resolve(_take_torch(), _state(), judgements)

    assert results[0].verdict == "rejected"
    assert "保守" in results[0].reason
    assert results[0].applied_events == []
    assert rules.verify_calls == 0 and rules.fallback_calls == 1
    assert services.calls == []


def test_needs_judge_low_confidence_goes_to_fallback_without_verify() -> None:
    """低置信证据 → fallback（不调 verify），保守拒绝、零写入。"""
    rules = _CountingRules(agent_assisted_tools=frozenset({"take_item"}))
    resolver = Resolver(
        rules=rules,
        services=_RecordingRouter(),
        actors=ActorDirectory(nonplayer_tags=M3_NONPLAYER_TAGS),
        save_seed=_SAVE_SEED,
    )

    results = resolver.resolve(_take_torch(), _state(), [_judge(confidence=0.5)])

    assert results[0].verdict == "rejected"
    assert rules.verify_calls == 0 and rules.fallback_calls == 1


def test_needs_judge_deny_is_run_through_verify_and_rejected() -> None:
    """置信足够的 deny 走 verify（复核调用 1 次）后被拒绝。"""
    rules = _CountingRules(agent_assisted_tools=frozenset({"take_item"}))
    resolver = Resolver(
        rules=rules,
        services=_RecordingRouter(),
        actors=ActorDirectory(nonplayer_tags=M3_NONPLAYER_TAGS),
        save_seed=_SAVE_SEED,
    )

    results = resolver.resolve(_take_torch(), _state(), [_judge(verdict="deny", confidence=0.95)])

    assert results[0].verdict == "rejected"
    assert rules.verify_calls == 1 and rules.fallback_calls == 0


def test_judgement_slots_align_with_entries_and_lazy_read() -> None:
    """槽位同序：第 1 条非 needs 不消费第 1 槽（ExplodingJudge 零访问），第 2 条 needs 用第 2 槽。"""
    rules = _CountingRules(agent_assisted_tools=frozenset({"take_item"}))
    services = _RecordingRouter()
    resolver = Resolver(
        rules=rules,
        services=services,
        actors=ActorDirectory(nonplayer_tags=M3_NONPLAYER_TAGS),
        save_seed=_SAVE_SEED,
    )
    entries = _entries(MOVE_NORTH, ("take_item", {"item_id": ITEM_SWORD}))

    results = resolver.resolve(entries, _state(), [_ExplodingJudge(), _judge()])

    assert [resolution.verdict for resolution in results] == ["applied", "applied"]
    assert rules.verify_calls == 1 and len(services.calls) == 2


def test_resolution_is_deterministic_for_same_input() -> None:
    """同一 (entries, snapshot, judgements) 两次裁决逐字相同（纯函数/可回放）。"""
    rules = _CountingRules(agent_assisted_tools=frozenset({"take_item"}))
    resolver = Resolver(
        rules=rules,
        services=_RecordingRouter(),
        actors=ActorDirectory(nonplayer_tags=M3_NONPLAYER_TAGS),
        save_seed=_SAVE_SEED,
    )
    entries = _take_torch()

    first: list[Resolution] = resolver.resolve(entries, _state(), [_judge()])
    second: list[Resolution] = resolver.resolve(entries, _state(), [_judge()])

    assert [item.model_dump(mode="json") for item in first] == [
        item.model_dump(mode="json") for item in second
    ]
