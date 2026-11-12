"""R-19 集成测试：自由制造草稿迁移 pre-queue（Resolver 内零 LLM）。

装配：``conftest.build_m4_prod_harness()``（M3 多角色链路 ProposalQueue→Resolver→ToolGate +
CraftService/CraftAgent(FakeLLM) + 审计，scenario=m4，自由制造开关开启）。覆盖：

- 正常回合：编排层在进入 Resolver 前生成 1 次草稿，CRAFT_GENERATED 事件照常提交；
- CAS 冲突重试：同一草稿被复用，LLM 调用仍恰好 1 次；Resolver 每次裁决的 LLM 增量为 0；
- LLM 不可用 / 草稿 Schema 违规（含一次修复）/ 状态一致性违规：零事件零写入；
- 开关关闭（默认）：编排层不预生成，Resolver 服务直接 NOT_ALLOWED → LLM 0 次、零写入。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from conftest import build_m4_prod_harness
from textgame.domain.hashing import state_hash
from textgame.orchestration.dto import TurnRequest
from textgame.orchestration.turn_orchestrator import CreateGameRequest
from textgame.services.dto import TurnResult

_SEED = 1
_TEXT = "把铁锭和铜线做成一个简易电击棒"
_CREATE_KEY = "r19-pre-queue-create-0001"
_TURN_KEY = "r19-pre-queue-turn-0001"


def _draft(**overrides: Any) -> dict[str, Any]:
    """合法自由制造草案（只含名称/材料/工位/步骤，无任何数值字段）。"""
    payload: dict[str, Any] = {
        "item_name": "简易电击棒",
        "output_template_id": "item.makeshift_taser",
        "inputs": [
            {"template_id": "item.iron_ingot", "qty": 1, "role": "material"},
            {"template_id": "item.copper_wire", "qty": 1, "role": "material"},
        ],
        "station": None,
        "steps": ["把铜线缠在铁锭上", "固定接头"],
    }
    payload.update(overrides)
    return payload


def _harness(*, free_craft_enabled: bool = True) -> Any:
    """生产同构装配（多角色链路）；``free_craft_enabled=False`` 模拟默认关闭。"""
    harness, client = build_m4_prod_harness()
    client.close()
    if not free_craft_enabled:
        harness.craft._free_craft_switch.set_enabled(False)
    return harness


def _create(harness: Any, *, key: str = _CREATE_KEY) -> str:
    view = harness.orchestrator.create_game(
        CreateGameRequest(seed=_SEED, idempotency_key=key, scenario="m4")
    )
    return view.game_id


def _run(
    harness: Any,
    game_id: str,
    *,
    expected_version: int = 1,
    key: str = _TURN_KEY,
) -> TurnResult:
    return harness.orchestrator.run(
        TurnRequest(
            game_id=game_id,
            input=_TEXT,
            expected_version=expected_version,
            idempotency_key=key,
        )
    )


class _TrackingResolver:
    """包装真实 Resolver：断言「pre-queue 载荷随提案传入」且裁决内 LLM 调用增量为 0。"""

    def __init__(self, inner: Any, fake_llm: Any) -> None:
        self._inner = inner
        self._fake_llm = fake_llm
        self.prepared_seen: list[dict[str, dict[str, Any]] | None] = []
        self.llm_deltas: list[int] = []

    def resolve(
        self,
        entries: Sequence[Any],
        snapshot: Any,
        judgements: Sequence[Any] | None = None,
        *,
        prepared_crafts: dict[str, dict[str, Any]] | None = None,
    ) -> list[Any]:
        before = len(self._fake_llm.calls)
        if prepared_crafts is None:
            result = self._inner.resolve(entries, snapshot, judgements)
        else:
            result = self._inner.resolve(
                entries, snapshot, judgements, prepared_crafts=prepared_crafts
            )
        self.llm_deltas.append(len(self._fake_llm.calls) - before)
        self.prepared_seen.append(dict(prepared_crafts) if prepared_crafts is not None else None)
        return list(result)


def _record(harness: Any, game_id: str) -> Any:
    record = harness.repo.load(game_id)
    assert record is not None
    return record


def _assert_readable_failure(result: TurnResult) -> None:
    """失败回合必须给玩家可读提示（中文 text + effects.hint），且零事件。"""
    assert result.ok is False
    assert result.events == []
    assert result.text
    assert result.effects
    hint = result.effects[0].get("hint")
    assert isinstance(hint, str) and hint


def _zero_write_snapshot(harness: Any, game_id: str) -> tuple[int, int, str, int]:
    """(version, last_seq, state_hash, 事件数) 快照，失败回合必须四项全不变。"""
    record = _record(harness, game_id)
    return (
        record.version,
        record.last_seq,
        state_hash(record.state),
        len(harness.repo.read_events(game_id)),
    )


# ---------------------------------------------------------------------------
# 成功：pre-queue 生成一次 + 事件提交
# ---------------------------------------------------------------------------
def test_pre_queue_single_llm_call_and_commit() -> None:
    """开启自由制造：进入 Resolver 前生成一次草稿，事件与旧路径语义一致。"""
    harness = _harness()
    game_id = _create(harness)
    harness.fake_llm.reset([_draft()])
    tracker = _TrackingResolver(harness.orchestrator._resolver, harness.fake_llm)
    harness.orchestrator._resolver = tracker

    result = _run(harness, game_id)

    assert result.ok is True
    assert len(harness.fake_llm.calls) == 1
    assert tracker.llm_deltas == [0], "Resolver 裁决链路内不得再调用 LLM"
    assert tracker.prepared_seen and tracker.prepared_seen[0]
    prepared = next(iter(tracker.prepared_seen[0].values()))
    assert prepared["ok"] is True
    assert prepared["draft"]["item_name"] == "简易电击棒"
    assert prepared["prompt_version"] == "craft@v1"

    assert len(result.events) == 1
    event = result.events[0]
    assert event.event_type is not None and event.event_type.value == "CRAFT_GENERATED"
    assert event.params["mode"] == "free"
    assert event.params["prompt_version"] == "craft@v1"
    assert event.rng_seed == event.result["craft"]["rng_seed"]
    after = _record(harness, game_id)
    names = [
        after.state.items[item_id].name
        for item_id in after.state.characters[event.actor_id].inventory
    ]
    assert "简易电击棒" in names


# ---------------------------------------------------------------------------
# CAS 冲突重试：同一草稿复用，LLM 恰好 1 次
# ---------------------------------------------------------------------------
def test_cas_conflict_retry_reuses_draft_with_exactly_one_llm_call() -> None:
    """提交冲突触发一次重裁决：pre-queue 草稿复用，LLM 调用仍恰好 1 次。"""
    harness = _harness()
    game_id = _create(harness)
    harness.fake_llm.reset([_draft()])
    tracker = _TrackingResolver(harness.orchestrator._resolver, harness.fake_llm)
    harness.orchestrator._resolver = tracker
    before = _record(harness, game_id)

    original_commit = harness.repo.commit
    commit_calls = {"n": 0}

    def flaky_commit(payload: Any) -> bool:
        commit_calls["n"] += 1
        if commit_calls["n"] == 1:
            return False  # 模拟并发提交抢先写入 → 编排层 CAS 重试一次
        return original_commit(payload)

    harness.repo.commit = flaky_commit
    try:
        result = _run(harness, game_id)
    finally:
        harness.repo.commit = original_commit

    assert result.ok is True
    assert commit_calls["n"] == 2, "CAS 冲突后必须重试一次"
    assert len(harness.fake_llm.calls) == 1, "CAS 重试不得重新生成草稿（不得重复计费）"
    assert tracker.llm_deltas == [0, 0], "两次裁决的 LLM 增量都必须为 0"
    assert all(seen is not None for seen in tracker.prepared_seen)
    assert tracker.prepared_seen[0] == tracker.prepared_seen[1], "两次裁决必须复用同一份草稿"

    after = _record(harness, game_id)
    assert after.version == before.version + 1
    audit_rows = harness.audit.query(game_id, turn_id=2)
    statuses = [row.status for row in audit_rows]
    assert statuses.count("conflicted") == 1
    assert statuses.count("committed") == 1


# ---------------------------------------------------------------------------
# 失败：零事件零写入（失败载荷由 Resolver 纯拒绝，不重试 LLM）
# ---------------------------------------------------------------------------
def test_llm_unavailable_zero_llm_retry_and_zero_write() -> None:
    """LLM 不可用：预生成一次后失败，Resolver 只拒绝不重试 → 零事件零写入。"""
    harness = _harness()
    game_id = _create(harness)
    harness.fake_llm.reset([RuntimeError("timeout")])
    before = _zero_write_snapshot(harness, game_id)

    result = _run(harness, game_id)

    _assert_readable_failure(result)
    assert result.error_code is not None and result.error_code.value == "INVALID_INPUT"
    assert len(harness.fake_llm.calls) == 1, "失败载荷后执行路径不得再调用 LLM"
    assert _zero_write_snapshot(harness, game_id) == before
    audit_rows = harness.audit.query(game_id, turn_id=2)
    assert audit_rows and all(row.status == "aborted" for row in audit_rows)


def test_schema_violation_after_repair_zero_write() -> None:
    """草稿 Schema 违规（含一次修复仍失败）：零事件零写入，LLM 只调用 2 次（生成+修复）。"""
    harness = _harness()
    game_id = _create(harness)
    harness.fake_llm.reset(["not-json", "still-not-json"])
    before = _zero_write_snapshot(harness, game_id)

    result = _run(harness, game_id)

    _assert_readable_failure(result)
    assert result.error_code is not None and result.error_code.value == "INVALID_INPUT"
    assert len(harness.fake_llm.calls) == 2
    assert harness.fake_llm.remaining == 0
    assert _zero_write_snapshot(harness, game_id) == before


def test_consistency_violation_zero_write() -> None:
    """草稿引用了背包外材料：pre-queue 状态一致性校验失败 → 零事件零写入。"""
    harness = _harness()
    game_id = _create(harness)
    bad = _draft(inputs=[{"template_id": "item.unobtainium", "qty": 1, "role": "material"}])
    harness.fake_llm.reset([bad])
    before = _zero_write_snapshot(harness, game_id)

    result = _run(harness, game_id)

    _assert_readable_failure(result)
    assert len(harness.fake_llm.calls) == 1
    assert _zero_write_snapshot(harness, game_id) == before


# ---------------------------------------------------------------------------
# 开关关闭（默认）：编排层不预生成，LLM 0 次，行为与旧路径一致
# ---------------------------------------------------------------------------
def test_free_craft_disabled_zero_llm_zero_write() -> None:
    """开关关闭：不进入 pre-queue 生成，Resolver 服务直接 NOT_ALLOWED → 零 LLM、零写入。"""
    harness = _harness(free_craft_enabled=False)
    game_id = _create(harness)
    harness.fake_llm.reset([_draft()])
    before = _zero_write_snapshot(harness, game_id)
    tracker = _TrackingResolver(harness.orchestrator._resolver, harness.fake_llm)
    harness.orchestrator._resolver = tracker

    result = _run(harness, game_id)

    _assert_readable_failure(result)
    assert result.error_code is not None and result.error_code.value == "INVALID_INPUT"
    assert len(harness.fake_llm.calls) == 0
    assert harness.fake_llm.remaining == 1, "关闭时脚本响应不得被消费"
    assert _zero_write_snapshot(harness, game_id) == before
    assert tracker.prepared_seen == [None], "开关关闭时不得构造 pre-queue 载荷"
    audit_rows = harness.audit.query(game_id, turn_id=2)
    assert audit_rows and all(row.status == "aborted" for row in audit_rows)
