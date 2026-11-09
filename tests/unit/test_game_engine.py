"""单元测试：``GameEngine`` 唯一写入者（契约 §4.4、第 21 章 21.2.1）。

覆盖：开局写 ``game_created`` 首事件与初始快照、CAS 版本冲突、事件盖章
（seq/id/rng_seed 连续）、缺失 event_type / 已盖章事件 / 空批次拒绝、
投影失败零写入、快照阈值触发，以及跨回合 seq 连续性。
"""

from __future__ import annotations

import pytest

from textgame.domain.events import Event, EventLog, EventType
from textgame.domain.hashing import state_hash
from textgame.domain.results import ErrorCode, Result
from textgame.domain.scenario import (
    ITEM_TORCH,
    NPC_SENTINEL,
    PLAYER_ID,
    ROOM_CELLAR,
    ROOM_ENTRY,
)
from textgame.persistence.memory import InMemoryRepository
from textgame.services.game_engine import GameEngine

_SAVE_SEED = "save-42"
_GAME_ID = "game_01J9Z3K4Q7X8Y9Z0ABCDEFGHJX"
_UNKNOWN_ROOM = "room_01J9Z3K4Q7X8Y9Z0ABCDEFGHJV"
_RNG_SEED_MAX = 0x7FFF_FFFF_FFFF_FFFF


def _engine(**thresholds: int) -> tuple[InMemoryRepository, GameEngine]:
    """构造内存仓储 + 引擎；``thresholds`` 透传快照阈值。"""
    repo = InMemoryRepository()
    return repo, GameEngine(repo, **thresholds)


def _create(engine: GameEngine) -> Result:
    """创建固定 game_id 的存档。"""
    return engine.create_game(save_seed=_SAVE_SEED, game_id=_GAME_ID)


def _move_draft(*, turn_id: int = 2, to_room: str = ROOM_CELLAR) -> Event:
    """构造 move_to 草稿。"""
    return Event(
        turn_id=turn_id,
        actor_id=PLAYER_ID,
        method="move_to",
        event_type=EventType.CHARACTER_MOVED,
        params={"direction": "north", "from_room": ROOM_ENTRY, "to_room": to_room},
        result={"from": ROOM_ENTRY, "to": to_room},
    )


def _take_draft(*, turn_id: int = 3, item_id: str = ITEM_TORCH) -> Event:
    """构造 take_item 草稿（默认从入口拿起火把）。"""
    return Event(
        turn_id=turn_id,
        actor_id=PLAYER_ID,
        method="take_item",
        event_type=EventType.ITEM_TAKEN,
        params={"item_id": item_id, "from_room": ROOM_ENTRY},
        result={"inventory_size": 1},
    )


def _talk_draft(*, turn_id: int = 3) -> Event:
    """构造 talk_to 草稿。"""
    return Event(
        turn_id=turn_id,
        actor_id=PLAYER_ID,
        method="talk_to",
        event_type=EventType.DIALOGUE_EXCHANGED,
        params={"npc_id": NPC_SENTINEL, "topic": "你好"},
        result={"dialogue_state": "greeted"},
    )


# ---------------------------------------------------------------------------
# create_game
# ---------------------------------------------------------------------------
def test_create_game_commits_game_created_and_initial_snapshot() -> None:
    """开局：game_created 为 seq=1 首事件 + version=1 + 初始快照 version=1。"""
    repo, engine = _engine()

    result = _create(engine)

    assert result.ok
    data = result.data or {}
    assert data["game_id"] == _GAME_ID
    assert data["turn"] == 1
    assert data["version"] == 1
    assert data["snapshot_version"] == 1
    assert data["save_seed"] == _SAVE_SEED

    record = repo.load(_GAME_ID)
    assert record is not None
    assert record.version == 1
    assert record.last_seq == 1
    assert record.state.rng_state.save_seed == _SAVE_SEED
    assert record.state.rng_state.last_seq == 1

    events = repo.read_events(_GAME_ID)
    assert len(events) == 1
    [created] = events
    assert created.seq == 1
    assert created.id == "evt_000001"
    assert created.event_type is EventType.GAME_CREATED
    assert created.turn_id == 1
    assert created.actor_id == "system"
    assert created.rng_seed is not None
    assert 0 <= created.rng_seed <= _RNG_SEED_MAX
    assert created.params["save_seed"] == _SAVE_SEED
    assert created.params["initial_character_id"] == PLAYER_ID
    assert "initial_state" in created.result

    snapshot = repo.latest_snapshot(_GAME_ID)
    assert snapshot is not None
    assert snapshot.snapshot_version == 1
    assert snapshot.turn_id == 1
    assert snapshot.last_seq == 1
    assert snapshot.state == record.state
    assert snapshot.state_hash == state_hash(record.state)


def test_create_game_duplicate_game_id_conflicts() -> None:
    """同一 game_id 重复开局 → CONFLICT，且不追加事件。"""
    repo, engine = _engine()
    assert _create(engine).ok

    second = _create(engine)

    assert not second.ok
    assert second.error_code is ErrorCode.CONFLICT
    assert len(repo.read_events(_GAME_ID)) == 1


def test_create_game_rejects_empty_save_seed() -> None:
    """空存档种子 → INVALID_INPUT（EventLog 与非空种子的前置条件）。"""
    _, engine = _engine()

    result = engine.create_game(save_seed="")

    assert not result.ok
    assert result.error_code is ErrorCode.INVALID_INPUT


# ---------------------------------------------------------------------------
# submit：盖章 + CAS + 零写入失败分支
# ---------------------------------------------------------------------------
def test_submit_stamps_event_and_bumps_version() -> None:
    """submit：事件 seq=2、id=evt_000002，版本 1 → 2，状态投影到地窖。"""
    repo, engine = _engine()
    assert _create(engine).ok

    result = engine.submit(game_id=_GAME_ID, expected_version=1, drafts=[_move_draft()])

    assert result.ok
    data = result.data or {}
    assert data["version"] == 2
    assert data["turn_id"] == 2
    [stamped] = result.events
    assert stamped.seq == 2
    assert stamped.id == "evt_000002"
    assert stamped.event_type is EventType.CHARACTER_MOVED
    assert stamped.rng_seed is not None
    assert 0 <= stamped.rng_seed <= _RNG_SEED_MAX

    record = repo.load(_GAME_ID)
    assert record is not None
    assert record.version == 2
    assert record.last_seq == 2
    assert record.state.characters[PLAYER_ID].location == ROOM_CELLAR
    assert record.state.world_time == 1


def test_submit_rejects_stale_expected_version_without_writing() -> None:
    """CAS 版本冲突 → CONFLICT 且零写入。"""
    repo, engine = _engine()
    assert _create(engine).ok

    result = engine.submit(game_id=_GAME_ID, expected_version=5, drafts=[_move_draft()])

    assert not result.ok
    assert result.error_code is ErrorCode.CONFLICT
    assert len(repo.read_events(_GAME_ID)) == 1
    record = repo.load(_GAME_ID)
    assert record is not None
    assert record.version == 1


def test_submit_unknown_game_not_found() -> None:
    """未知 game_id → NOT_FOUND。"""
    _, engine = _engine()

    result = engine.submit(game_id="game_unknown", expected_version=1, drafts=[_move_draft()])

    assert not result.ok
    assert result.error_code is ErrorCode.NOT_FOUND


def test_submit_requires_event_type() -> None:
    """GAME_CREATED 之外的事件缺失 event_type → SCHEMA_VIOLATION，零写入。"""
    repo, engine = _engine()
    assert _create(engine).ok
    draft = Event(turn_id=2, actor_id=PLAYER_ID, method="move_to")

    result = engine.submit(game_id=_GAME_ID, expected_version=1, drafts=[draft])

    assert not result.ok
    assert result.error_code is ErrorCode.SCHEMA_VIOLATION
    assert len(repo.read_events(_GAME_ID)) == 1


def test_submit_rejects_stamped_events() -> None:
    """submit 只接受草稿事件；已盖章事件 → SCHEMA_VIOLATION。"""
    repo, engine = _engine()
    assert _create(engine).ok
    log = EventLog(_SAVE_SEED)
    stamped = log.stamp([_move_draft()])

    result = engine.submit(game_id=_GAME_ID, expected_version=1, drafts=stamped)

    assert not result.ok
    assert result.error_code is ErrorCode.SCHEMA_VIOLATION
    assert len(repo.read_events(_GAME_ID)) == 1


def test_submit_rejects_empty_draft_batch() -> None:
    """空草稿批次 → INVALID_INPUT（禁止只升版本不写事件）。"""
    _, engine = _engine()
    assert _create(engine).ok

    result = engine.submit(game_id=_GAME_ID, expected_version=1, drafts=[])

    assert not result.ok
    assert result.error_code is ErrorCode.INVALID_INPUT


def test_submit_projection_failure_is_internal_and_zero_write() -> None:
    """投影失败（目标房间不存在）→ INTERNAL，且事件/版本都不变。"""
    repo, engine = _engine()
    assert _create(engine).ok

    result = engine.submit(
        game_id=_GAME_ID,
        expected_version=1,
        drafts=[_move_draft(to_room=_UNKNOWN_ROOM)],
    )

    assert not result.ok
    assert result.error_code is ErrorCode.INTERNAL
    assert len(repo.read_events(_GAME_ID)) == 1
    record = repo.load(_GAME_ID)
    assert record is not None
    assert record.version == 1


def test_submit_keeps_seq_continuous_across_turns() -> None:
    """跨回合提交：seq 连续（3 紧跟 2），id 与 seq 对齐。"""
    repo, engine = _engine()
    assert _create(engine).ok
    first = engine.submit(game_id=_GAME_ID, expected_version=1, drafts=[_move_draft()])
    assert first.ok

    second = engine.submit(
        game_id=_GAME_ID,
        expected_version=2,
        drafts=[_talk_draft(turn_id=3)],
    )

    assert second.ok
    [stamped] = second.events
    assert stamped.seq == 3
    assert stamped.id == "evt_000003"
    events = repo.read_events(_GAME_ID)
    assert [event.seq for event in events] == [1, 2, 3]


# ---------------------------------------------------------------------------
# 快照阈值
# ---------------------------------------------------------------------------
def test_submit_triggers_snapshot_on_event_threshold() -> None:
    """每 1 条事件触发一次快照：提交后出现 snapshot_version=2。"""
    repo, engine = _engine(snapshot_every_events=1)
    assert _create(engine).ok
    assert repo.latest_snapshot(_GAME_ID) is not None

    result = engine.submit(game_id=_GAME_ID, expected_version=1, drafts=[_move_draft()])

    assert result.ok
    snapshot = repo.latest_snapshot(_GAME_ID)
    assert snapshot is not None
    assert snapshot.snapshot_version == 2
    assert snapshot.last_seq == 2
    record = repo.load(_GAME_ID)
    assert record is not None
    assert snapshot.state_hash == state_hash(record.state)


def test_submit_triggers_snapshot_on_turn_threshold() -> None:
    """每 1 个回合触发一次快照。"""
    repo, engine = _engine(snapshot_every_turns=1)
    assert _create(engine).ok

    result = engine.submit(game_id=_GAME_ID, expected_version=1, drafts=[_move_draft()])

    assert result.ok
    snapshot = repo.latest_snapshot(_GAME_ID)
    assert snapshot is not None
    assert snapshot.snapshot_version == 2
    assert snapshot.turn_id == 2


def test_engine_rejects_non_positive_thresholds() -> None:
    """快照阈值为 0/负数属于装配错误。"""
    repo = InMemoryRepository()

    with pytest.raises(ValueError):
        GameEngine(repo, snapshot_every_events=0)


# ---------------------------------------------------------------------------
# M4 D-008-3：同库多存档（events 复合主键 (game_id, seq)）
# ---------------------------------------------------------------------------
def test_create_two_games_on_same_repository_are_isolated() -> None:
    """同一 repo 建两个 game 都成功；各自 seq=1 的 game_created 共存且状态隔离。"""
    repo, engine = _engine()
    game_a = "game_m4_a_0001"
    game_b = "game_m4_b_0001"

    first = engine.create_game(save_seed=_SAVE_SEED, game_id=game_a)
    second = engine.create_game(save_seed="save-99", game_id=game_b)

    assert first.ok and second.ok
    for game_id in (game_a, game_b):
        record = repo.load(game_id)
        assert record is not None
        assert (record.version, record.last_seq) == (1, 1)
        events = repo.read_events(game_id)
        assert [event.seq for event in events] == [1]
        assert events[0].id == "evt_000001"  # Event.id 是每存档编号
        assert events[0].event_type is EventType.GAME_CREATED
        assert events[0].params["save_seed"] == record.state.rng_state.save_seed

    # A 继续提交：B 的版本/事件流不受影响
    assert engine.submit(game_id=game_a, expected_version=1, drafts=[_move_draft()]).ok
    record_a = repo.load(game_a)
    record_b = repo.load(game_b)
    assert record_a is not None and record_b is not None
    assert (record_a.version, record_a.last_seq) == (2, 2)
    assert (record_b.version, record_b.last_seq) == (1, 1)
    assert [event.seq for event in repo.read_events(game_a)] == [1, 2]
    assert [event.seq for event in repo.read_events(game_b)] == [1]


def test_create_game_duplicate_game_id_message_is_generic() -> None:
    """重复同一 game_id 仍 CONFLICT，但文案不再限定「每库单存档」（D-008-3）。"""
    _, engine = _engine()
    assert _create(engine).ok

    second = _create(engine)

    assert second.ok is False
    assert second.error_code is ErrorCode.CONFLICT
    message = second.message or ""
    assert "already exists" in message or "已存在" in message
    assert "单存档" not in message
