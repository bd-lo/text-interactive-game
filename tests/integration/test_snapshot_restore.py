"""M2-008 集成测试：SQLite 事件表/快照与重启恢复（第 14 章 14.3/14.4/14.5）。

覆盖：tmp 文件库建表、创建游戏 + 5 回合写操作、重启恢复、删除 current 行后的
「快照 + 事件」重建（哈希一致）与续写、快照内容一致性与版本递增、双实例 CAS
冲突、幂等表跨重启、事件 seq/id/params/result 完整性、无 GAME_CREATED 事件流与
快照哈希不符两个损坏分支。

事件盖章复用 M1 的 ``EventLog``（与 ``GameEngine`` 同一套域内实现），提交走
``SqliteRepository.commit``；这样测试不依赖 005 未完成的 TurnOrchestrator，但经过
真实的 reducer 投影（``GameState.apply``）与快照重建路径。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import Engine, delete, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from textgame.domain.events import Event, EventLog, EventType
from textgame.domain.hashing import canonical_json, state_hash
from textgame.domain.models import GameState
from textgame.domain.scenario import (
    ITEM_TORCH,
    PLAYER_ID,
    ROOM_CELLAR,
    ROOM_ENTRY,
    build_initial_state,
)
from textgame.persistence.database import create_engine_and_session, init_schema
from textgame.persistence.memory import InMemoryRepository
from textgame.persistence.sqlite_repository import SqliteRepository
from textgame.persistence.tables import EventRow, GameStateCurrentRow, SnapshotRow
from textgame.services.dto import TurnResult
from textgame.services.game_engine import GameEngine
from textgame.services.ports import CommitPayload, IdempotencyRecord, SnapshotRecord

GAME_ID = "game-m2-008"
SAVE_SEED = "save-42"
_MAX_SEED = 0x7FFF_FFFF_FFFF_FFFF


# ---------------------------------------------------------------------------
# 建库与事件/快照构造辅助（只走公开的仓储 API + M1 的 EventLog）
# ---------------------------------------------------------------------------
def _db_url(tmp_path: Path) -> str:
    """tmp 目录下的文件库 URL（不写仓库 data/）。"""
    return f"sqlite:///{tmp_path.joinpath('textgame.db').as_posix()}"


def _open(db_url: str) -> tuple[Engine, sessionmaker[Session], SqliteRepository]:
    """建 engine/session 工厂并建表，返回可直接查表的 repository。"""
    engine, factory = create_engine_and_session(db_url)
    init_schema(engine)
    return engine, factory, SqliteRepository(engine)


def _stamped(drafts: list[Event], history: list[Event]) -> list[Event]:
    """按 M1 EventLog 语义盖章：先追加历史事件，再 stamp 草稿。"""
    log = EventLog(SAVE_SEED)
    log.append(history)
    return log.stamp(drafts)


def _seq(event: Event) -> int:
    assert event.seq is not None
    return event.seq


def _snapshot(state: GameState, *, snapshot_version: int, last_seq: int) -> SnapshotRecord:
    return SnapshotRecord(
        game_id=GAME_ID,
        snapshot_version=snapshot_version,
        turn_id=state.turn,
        last_seq=last_seq,
        state=state,
        state_hash=state_hash(state),
    )


def _game_created_draft(initial: GameState) -> Event:
    """M2 契约 §3.2 的 game_created 草稿（result 带整状态快照供空档重建）。"""
    return Event(
        turn_id=1,
        actor_id="system",
        method="game_created",
        event_type=EventType.GAME_CREATED,
        params={
            "card_id": None,
            "starting_room_id": ROOM_ENTRY,
            "initial_character_id": PLAYER_ID,
            "save_seed": SAVE_SEED,
            "snapshot_version": 1,
        },
        result={"initial_state": initial.model_dump(mode="json")},
    )


def _move_draft(*, turn_id: int, from_room: str, to_room: str, direction: str) -> Event:
    """契约 §3.3 的 CHARACTER_MOVED 草稿（reducer 把角色 location 改为 to_room）。"""
    return Event(
        turn_id=turn_id,
        actor_id=PLAYER_ID,
        method="move_to",
        event_type=EventType.CHARACTER_MOVED,
        params={"direction": direction, "from_room": from_room, "to_room": to_room},
        result={"from": from_room, "to": to_room},
    )


def _create_game(repo: SqliteRepository, *, save_snapshot: bool = True) -> GameState:
    """提交 game_created（expected_version=0 → version=1, seq=1），可选存初始快照。"""
    initial = build_initial_state(save_seed=SAVE_SEED)
    stamped = _stamped([_game_created_draft(initial)], history=[])
    new_state = initial.apply(stamped)  # 与 GameEngine.create_game 一样先做纯投影
    ok = repo.commit(
        CommitPayload(
            game_id=GAME_ID,
            expected_version=0,
            new_version=1,
            last_seq=_seq(stamped[-1]),
            new_state=new_state,
            stamped_events=tuple(stamped),
        )
    )
    assert ok
    if save_snapshot:
        repo.save_snapshot(_snapshot(new_state, snapshot_version=1, last_seq=_seq(stamped[-1])))
    return new_state


def _commit_next_turn(
    repo: SqliteRepository, drafts: list[Event], *, snapshot_version: int | None = None
) -> GameState:
    """用当前记录盖章 + apply 投影 + CAS 提交；返回新状态。"""
    record = repo.load(GAME_ID)
    assert record is not None
    stamped = _stamped(drafts, history=repo.read_events(GAME_ID))
    new_state = record.state.apply(stamped)
    ok = repo.commit(
        CommitPayload(
            game_id=GAME_ID,
            expected_version=record.version,
            new_version=record.version + 1,
            last_seq=_seq(stamped[-1]),
            new_state=new_state,
            stamped_events=tuple(stamped),
        )
    )
    assert ok
    if snapshot_version is not None:
        repo.save_snapshot(
            _snapshot(new_state, snapshot_version=snapshot_version, last_seq=_seq(stamped[-1]))
        )
    return new_state


def _play_moves(
    repo: SqliteRepository, *, count: int, snapshot_at: tuple[int, ...] = ()
) -> GameState:
    """连续走 ``count`` 个移动回合（入口↔地窖往返）；``snapshot_at`` 指定回合后存快照。"""
    record = repo.load(GAME_ID)
    assert record is not None
    state = record.state
    latest = repo.latest_snapshot(GAME_ID)
    next_snapshot_version = (latest.snapshot_version + 1) if latest is not None else 1
    for _ in range(count):
        turn_id = state.turn + 1
        from_room = state.characters[PLAYER_ID].location
        to_room = ROOM_CELLAR if from_room == ROOM_ENTRY else ROOM_ENTRY
        direction = "north" if to_room == ROOM_CELLAR else "south"
        takes_snapshot = turn_id in snapshot_at
        state = _commit_next_turn(
            repo,
            [
                _move_draft(
                    turn_id=turn_id, from_room=from_room, to_room=to_room, direction=direction
                )
            ],
            snapshot_version=next_snapshot_version if takes_snapshot else None,
        )
        if takes_snapshot:
            next_snapshot_version += 1
    return state


def _delete_current_row(factory: sessionmaker[Session]) -> None:
    """模拟 current 缓存丢失（快照重建路径的触发条件）。"""
    with factory() as session, session.begin():
        session.execute(delete(GameStateCurrentRow).where(GameStateCurrentRow.game_id == GAME_ID))


# ---------------------------------------------------------------------------
# 建库与读取基线
# ---------------------------------------------------------------------------
def test_init_schema_creates_four_tables(tmp_path: Path) -> None:
    engine, _factory, _repo = _open(_db_url(tmp_path))
    tables = set(inspect(engine).get_table_names())
    assert {"events", "snapshots", "game_state_current", "idempotency"} <= tables
    engine.dispose()


def test_memory_sqlite_repository_roundtrip() -> None:
    """内存库用 StaticPool 共享连接：建表、提交与读取在同一连接上可见。"""
    engine, _factory, repo = _open("sqlite:///:memory:")
    created = _create_game(repo)
    record = repo.load(GAME_ID)
    assert record is not None
    assert record.version == 1
    assert state_hash(record.state) == state_hash(created)
    engine.dispose()


def test_load_unknown_game_returns_none(tmp_path: Path) -> None:
    _engine, _factory, repo = _open(_db_url(tmp_path))
    assert repo.load("game-missing") is None
    assert repo.latest_snapshot("game-missing") is None
    assert repo.read_events("game-missing") == []


def test_create_game_commits_game_created_and_initial_snapshot(tmp_path: Path) -> None:
    _engine, _factory, repo = _open(_db_url(tmp_path))
    created_state = _create_game(repo)

    record = repo.load(GAME_ID)
    assert record is not None
    assert record.version == 1
    assert record.last_seq == 1
    assert state_hash(record.state) == state_hash(created_state)

    events = repo.read_events(GAME_ID)
    assert len(events) == 1
    assert events[0].id == "evt_000001"
    assert events[0].event_type is EventType.GAME_CREATED
    assert events[0].params["save_seed"] == SAVE_SEED
    assert events[0].result["initial_state"]["turn"] == 1

    snapshot = repo.latest_snapshot(GAME_ID)
    assert snapshot is not None
    assert snapshot.snapshot_version == 1
    assert snapshot.turn_id == 1
    assert snapshot.last_seq == 1
    assert snapshot.state_hash == state_hash(snapshot.state)


# ---------------------------------------------------------------------------
# 5 回合写操作 + 事件完整性
# ---------------------------------------------------------------------------
def test_five_turn_writes_keep_event_stream_and_params_roundtrip(tmp_path: Path) -> None:
    _engine, _factory, repo = _open(_db_url(tmp_path))
    _create_game(repo)
    _play_moves(repo, count=5, snapshot_at=(2, 4))

    events = repo.read_events(GAME_ID)
    assert len(events) == 6  # game_created + 5 回合
    assert [event.seq for event in events] == list(range(1, 7))
    assert [event.id for event in events] == [f"evt_{seq:06d}" for seq in range(1, 7)]
    assert all(event.rng_seed is not None and 0 <= event.rng_seed <= _MAX_SEED for event in events)
    assert all(
        event.ts is not None
        and event.ts.tzinfo is not None
        and event.ts.utcoffset() == timedelta(0)
        for event in events
    )
    assert [event.event_type for event in events[1:]] == [EventType.CHARACTER_MOVED] * 5
    # params/result 经 TEXT JSON 往返后与盖章事件逐字段一致
    assert events[1].params == {
        "direction": "north",
        "from_room": ROOM_ENTRY,
        "to_room": ROOM_CELLAR,
    }
    assert events[1].result == {"from": ROOM_ENTRY, "to": ROOM_CELLAR}
    assert events[2].params["to_room"] == ROOM_ENTRY

    record = repo.load(GAME_ID)
    assert record is not None
    assert record.version == 6
    assert record.last_seq == 6
    assert record.state.turn == 6
    assert record.state.characters[PLAYER_ID].location == ROOM_CELLAR


# ---------------------------------------------------------------------------
# 重启恢复 / 快照重建
# ---------------------------------------------------------------------------
def test_restart_on_same_file_restores_current_state(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    engine, _factory, repo = _open(db_url)
    _create_game(repo)
    _play_moves(repo, count=5, snapshot_at=(2, 4))
    before = repo.load(GAME_ID)
    assert before is not None
    engine.dispose()

    engine2, _factory2, repo2 = _open(db_url)
    after = repo2.load(GAME_ID)
    assert after is not None
    assert after.version == before.version
    assert after.last_seq == before.last_seq
    assert state_hash(after.state) == state_hash(before.state)
    engine2.dispose()


def test_rebuild_from_snapshot_after_current_row_deleted(tmp_path: Path) -> None:
    _engine, factory, repo = _open(_db_url(tmp_path))
    _create_game(repo)
    _play_moves(repo, count=5, snapshot_at=(2, 4))
    before = repo.load(GAME_ID)
    assert before is not None
    snapshot = repo.latest_snapshot(GAME_ID)
    assert snapshot is not None
    assert snapshot.snapshot_version == 3
    assert snapshot.last_seq == 4  # 快照覆盖到第 4 回合事件

    _delete_current_row(factory)
    assert repo.load(GAME_ID) is not None  # 重建路径可读
    rebuilt = repo.load(GAME_ID)
    assert rebuilt is not None
    assert state_hash(rebuilt.state) == state_hash(before.state)
    assert rebuilt.last_seq == before.last_seq == 6
    # current 行缺失时 version 取 last_seq（M2 每次提交恰好 1 条事件）
    assert rebuilt.version == 6

    # 重建后继续提交下一回合：current 行按 INSERT 补齐缓存，事件 seq 续接
    _play_moves(repo, count=1)
    final = repo.load(GAME_ID)
    assert final is not None
    assert final.version == 7
    assert final.last_seq == 7
    assert len(repo.read_events(GAME_ID)) == 7


def test_rebuild_from_events_when_no_snapshot(tmp_path: Path) -> None:
    _engine, factory, repo = _open(_db_url(tmp_path))
    _create_game(repo, save_snapshot=False)
    _play_moves(repo, count=3)
    before = repo.load(GAME_ID)
    assert before is not None
    assert repo.latest_snapshot(GAME_ID) is None

    _delete_current_row(factory)
    rebuilt = repo.load(GAME_ID)
    assert rebuilt is not None
    assert state_hash(rebuilt.state) == state_hash(before.state)
    assert rebuilt.last_seq == before.last_seq == 4
    assert rebuilt.state.turn == 4


def test_snapshot_rows_are_consistent_and_increasing(tmp_path: Path) -> None:
    _engine, factory, repo = _open(_db_url(tmp_path))
    _create_game(repo)
    _play_moves(repo, count=5, snapshot_at=(2, 4))

    with factory() as session:
        rows = session.scalars(
            select(SnapshotRow)
            .where(SnapshotRow.game_id == GAME_ID)
            .order_by(SnapshotRow.snapshot_version.asc())
        ).all()
    assert [row.snapshot_version for row in rows] == [1, 2, 3]
    assert [row.turn_id for row in rows] == [1, 2, 4]
    assert [row.last_seq for row in rows] == [1, 2, 4]
    for row in rows:
        parsed = GameState.model_validate_json(row.state_json)
        assert row.state_hash == state_hash(parsed)
        assert row.schema_version == 1

    latest = repo.latest_snapshot(GAME_ID)
    assert latest is not None
    assert latest.snapshot_version == 3
    assert latest.state_hash == state_hash(latest.state)


# ---------------------------------------------------------------------------
# 并发/CAS：双实例读同一 version
# ---------------------------------------------------------------------------
def test_cas_conflict_between_two_repository_instances(tmp_path: Path) -> None:
    engine, _factory, repo_a = _open(_db_url(tmp_path))
    repo_b = SqliteRepository(engine)  # 同一数据库上的第二个仓储实例
    _create_game(repo_a)
    record_a = repo_a.load(GAME_ID)
    record_b = repo_b.load(GAME_ID)
    assert record_a is not None and record_b is not None
    assert record_a.version == record_b.version == 1

    # A 先提交成功（version 1 → 2）
    turn_a = _stamped(
        [_move_draft(turn_id=2, from_room=ROOM_ENTRY, to_room=ROOM_CELLAR, direction="north")],
        history=repo_a.read_events(GAME_ID),
    )
    state_a = record_a.state.apply(turn_a)
    assert repo_a.commit(
        CommitPayload(
            game_id=GAME_ID,
            expected_version=1,
            new_version=2,
            last_seq=_seq(turn_a[-1]),
            new_state=state_a,
            stamped_events=tuple(turn_a),
        )
    )

    # B 持有过期 version=1：事件 seq 续接数据库当前 max（隔离出纯 CAS 冲突）
    turn_b = _stamped(
        [_move_draft(turn_id=3, from_room=ROOM_CELLAR, to_room=ROOM_ENTRY, direction="south")],
        history=repo_a.read_events(GAME_ID),
    )
    assert (
        repo_b.commit(
            CommitPayload(
                game_id=GAME_ID,
                expected_version=record_b.version,
                new_version=record_b.version + 1,
                last_seq=_seq(turn_b[-1]),
                new_state=record_b.state.apply(turn_b),
                stamped_events=tuple(turn_b),
            )
        )
        is False
    )

    events = repo_a.read_events(GAME_ID)
    assert len(events) == 2  # B 的事件零写入
    current = repo_a.load(GAME_ID)
    assert current is not None
    assert current.version == 2
    assert current.last_seq == 2
    assert state_hash(current.state) == state_hash(state_a)


# ---------------------------------------------------------------------------
# 幂等表跨重启
# ---------------------------------------------------------------------------
def test_idempotency_record_survives_restart_and_overwrites(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    engine, _factory, repo = _open(db_url)
    _create_game(repo)
    game_created = repo.read_events(GAME_ID)[0]
    key = f"turn:{GAME_ID}:key-0001"
    first = TurnResult.success(version=2, turn_id=2, text="你向北走进地窖。", events=[game_created])
    repo.save_idempotent(
        IdempotencyRecord(
            key=key,
            params_hash="hash-0001",
            payload=first.model_dump(mode="json"),
            events=(game_created,),
        )
    )
    assert repo.get_idempotent("turn:missing:key") is None
    engine.dispose()

    _engine2, _factory2, repo2 = _open(db_url)
    loaded = repo2.get_idempotent(key)
    assert loaded is not None
    assert loaded.key == key
    assert loaded.params_hash == "hash-0001"
    assert TurnResult.model_validate(loaded.payload) == first
    assert loaded.events == (game_created,)

    second = TurnResult.success(version=3, turn_id=3, text="更新后的结果")
    repo2.save_idempotent(
        IdempotencyRecord(
            key=key,
            params_hash="hash-0002",
            payload=second.model_dump(mode="json"),
        )
    )
    replaced = repo2.get_idempotent(key)
    assert replaced is not None
    assert replaced.params_hash == "hash-0002"
    assert TurnResult.model_validate(replaced.payload) == second
    assert replaced.events == ()


# ---------------------------------------------------------------------------
# 失败分支：重复提交 / 非法事件 / 损坏存档 / 快照哈希不符
# ---------------------------------------------------------------------------
def test_replaying_same_commit_payload_returns_false(tmp_path: Path) -> None:
    _engine, _factory, repo = _open(_db_url(tmp_path))
    _create_game(repo)
    record = repo.load(GAME_ID)
    assert record is not None
    stamped = _stamped(
        [_move_draft(turn_id=2, from_room=ROOM_ENTRY, to_room=ROOM_CELLAR, direction="north")],
        history=repo.read_events(GAME_ID),
    )
    payload = CommitPayload(
        game_id=GAME_ID,
        expected_version=record.version,
        new_version=record.version + 1,
        last_seq=_seq(stamped[-1]),
        new_state=record.state.apply(stamped),
        stamped_events=tuple(stamped),
    )
    assert repo.commit(payload) is True
    assert repo.commit(payload) is False
    assert len(repo.read_events(GAME_ID)) == 2
    current = repo.load(GAME_ID)
    assert current is not None
    assert current.version == 2


def test_commit_rejects_draft_wrong_uid_and_seq_gap(tmp_path: Path) -> None:
    _engine, _factory, repo = _open(_db_url(tmp_path))
    _create_game(repo)
    record = repo.load(GAME_ID)
    assert record is not None
    stamped = _stamped(
        [_move_draft(turn_id=2, from_room=ROOM_ENTRY, to_room=ROOM_CELLAR, direction="north")],
        history=repo.read_events(GAME_ID),
    )
    draft = Event(
        turn_id=2,
        actor_id=PLAYER_ID,
        method="move_to",
        event_type=EventType.CHARACTER_MOVED,
    )
    wrong_uid = stamped[0].model_copy(update={"id": "evt_999999"})
    gap = stamped[0].model_copy(update={"seq": 5, "id": "evt_000005"})
    for bad_events, last_seq in (
        ((draft,), 2),
        ((wrong_uid,), _seq(stamped[0])),
        ((gap,), 5),
    ):
        assert (
            repo.commit(
                CommitPayload(
                    game_id=GAME_ID,
                    expected_version=record.version,
                    new_version=record.version + 1,
                    last_seq=last_seq,
                    new_state=record.state,
                    stamped_events=tuple(bad_events),
                )
            )
            is False
        )
    assert len(repo.read_events(GAME_ID)) == 1
    current = repo.load(GAME_ID)
    assert current is not None
    assert current.version == 1


def test_event_uid_unique_constraint_enforced_by_database(tmp_path: Path) -> None:
    _engine, factory, repo = _open(_db_url(tmp_path))
    _create_game(repo)
    duplicate = EventRow(
        seq=2,
        event_uid="evt_000001",  # 与已有事件重复
        game_id=GAME_ID,
        turn_id=2,
        event_type="CHARACTER_MOVED",
        actor_id=PLAYER_ID,
        method="move_to",
        params="{}",
        result="{}",
        rng_seed=0,
        ts=datetime.now(timezone.utc),
    )
    with factory() as session:
        session.add(duplicate)
        with pytest.raises(IntegrityError):
            session.commit()


def test_corrupted_stream_without_game_created_raises(tmp_path: Path) -> None:
    _engine, factory, repo = _open(_db_url(tmp_path))
    with factory() as session, session.begin():
        session.add(
            EventRow(
                seq=1,
                event_uid="evt_000001",
                game_id=GAME_ID,
                turn_id=1,
                event_type=EventType.CHARACTER_MOVED.value,
                actor_id=PLAYER_ID,
                method="move_to",
                params=canonical_json({"direction": "north"}),
                result=canonical_json({"from": ROOM_ENTRY, "to": ROOM_CELLAR}),
                rng_seed=0,
                ts=datetime.now(timezone.utc),
            )
        )
    with pytest.raises(ValueError, match="game_created"):
        repo.load(GAME_ID)


def test_corrupted_event_stream_gap_raises(tmp_path: Path) -> None:
    _engine, factory, repo = _open(_db_url(tmp_path))
    _create_game(repo)  # 初始快照 last_seq=1
    _play_moves(repo, count=2)
    _delete_current_row(factory)
    with factory() as session, session.begin():
        session.execute(delete(EventRow).where(EventRow.seq == 2))
    with pytest.raises(ValueError, match="gap"):
        repo.load(GAME_ID)


def test_snapshot_hash_mismatch_raises(tmp_path: Path) -> None:
    _engine, factory, repo = _open(_db_url(tmp_path))
    _create_game(repo)
    with factory() as session, session.begin():
        row = session.scalars(select(SnapshotRow).where(SnapshotRow.game_id == GAME_ID)).one()
        row.state_hash = "0" * 64  # 模拟快照被篡改
    _delete_current_row(factory)
    with pytest.raises(ValueError, match="state_hash mismatch"):
        repo.load(GAME_ID)


# ---------------------------------------------------------------------------
# GameEngine 端到端：创建 + 5 回合 + 阈值快照 + 重启 + 缓存丢失后重建续写
# ---------------------------------------------------------------------------
def test_game_engine_snapshots_persist_and_restore(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    engine, factory, repo = _open(db_url)
    game_engine = GameEngine(repo, snapshot_every_turns=2)
    created = game_engine.create_game(save_seed=SAVE_SEED, game_id=GAME_ID)
    assert created.ok, created.message
    assert created.data is not None
    assert created.data["version"] == 1
    assert created.data["snapshot_version"] == 1

    record = repo.load(GAME_ID)
    assert record is not None
    for _ in range(5):
        state = record.state
        from_room = state.characters[PLAYER_ID].location
        to_room = ROOM_CELLAR if from_room == ROOM_ENTRY else ROOM_ENTRY
        result = game_engine.submit(
            game_id=GAME_ID,
            expected_version=record.version,
            drafts=[
                _move_draft(
                    turn_id=state.turn + 1,
                    from_room=from_room,
                    to_room=to_room,
                    direction="north" if to_room == ROOM_CELLAR else "south",
                )
            ],
        )
        assert result.ok, result.message
        assert len(result.events) == 1
        record = repo.load(GAME_ID)
        assert record is not None
    assert record.version == 6
    assert record.last_seq == 6

    # 阈值 snapshot_every_turns=2：初始 v1 + 第 3、5 回合后各一条快照
    with factory() as session:
        versions = session.scalars(
            select(SnapshotRow.snapshot_version)
            .where(SnapshotRow.game_id == GAME_ID)
            .order_by(SnapshotRow.snapshot_version.asc())
        ).all()
    assert list(versions) == [1, 2, 3]
    latest = repo.latest_snapshot(GAME_ID)
    assert latest is not None
    assert latest.snapshot_version == 3
    assert latest.last_seq == 5
    assert latest.state_hash == state_hash(latest.state)

    # 重启恢复：同一文件库新实例读到一致状态
    engine.dispose()
    engine2, factory2, repo2 = _open(db_url)
    restored = repo2.load(GAME_ID)
    assert restored is not None
    assert restored.version == 6
    assert restored.last_seq == 6
    assert state_hash(restored.state) == state_hash(record.state)

    # 删除 current 行 → 快照 + 事件重建；引擎在恢复出的版本上继续提交下一回合
    _delete_current_row(factory2)
    rebuilt = repo2.load(GAME_ID)
    assert rebuilt is not None
    assert state_hash(rebuilt.state) == state_hash(restored.state)
    game_engine2 = GameEngine(repo2, snapshot_every_turns=2)
    from_room = rebuilt.state.characters[PLAYER_ID].location
    to_room = ROOM_CELLAR if from_room == ROOM_ENTRY else ROOM_ENTRY
    next_turn = rebuilt.state.turn + 1
    result = game_engine2.submit(
        game_id=GAME_ID,
        expected_version=rebuilt.version,
        drafts=[
            _move_draft(
                turn_id=next_turn,
                from_room=from_room,
                to_room=to_room,
                direction="north" if to_room == ROOM_CELLAR else "south",
            )
        ],
    )
    assert result.ok, result.message
    final = repo2.load(GAME_ID)
    assert final is not None
    assert final.version == rebuilt.version + 1
    assert final.last_seq == 7
    assert len(repo2.read_events(GAME_ID)) == 7
    engine2.dispose()


# ===========================================================================
# M3 D-M3-6 只增补：版本持久化与 current 行缺失后的版本重建
#
# 既有 M2 断言（version=last_seq）全部保持原样；以下新增用例覆盖 M3 多事件单回合后
# `snapshots.version` 的写入与「快照 version + 快照后 distinct turn 数」重建规则，
# 以及旧 M2 快照（version=0）兜底与无快照空档重建两种分支。
# ===========================================================================
def _take_then_move_drafts(*, turn_id: int) -> list[Event]:
    """M3 多事件单回合：同一回合先拿火把再向北移动（2 条事件、1 次提交）。"""
    return [
        Event(
            turn_id=turn_id,
            actor_id=PLAYER_ID,
            method="take_item",
            event_type=EventType.ITEM_TAKEN,
            params={"item_id": ITEM_TORCH, "from_room": ROOM_ENTRY},
            result={"inventory_size": 1},
        ),
        _move_draft(turn_id=turn_id, from_room=ROOM_ENTRY, to_room=ROOM_CELLAR, direction="north"),
    ]


def _use_torch_draft(*, turn_id: int) -> Event:
    """单事件回合：消耗背包里的火把（ITEM_CONSUMED）。"""
    return Event(
        turn_id=turn_id,
        actor_id=PLAYER_ID,
        method="use_item",
        event_type=EventType.ITEM_CONSUMED,
        params={"item_id": ITEM_TORCH},
        result={"consumed": True},
    )


def _versioned_snapshot(
    state: GameState, *, snapshot_version: int, last_seq: int, version: int
) -> SnapshotRecord:
    """M3 快照：显式携带提交版本号（`SnapshotRecord.version`）。"""
    return SnapshotRecord(
        game_id=GAME_ID,
        snapshot_version=snapshot_version,
        turn_id=state.turn,
        last_seq=last_seq,
        state=state,
        state_hash=state_hash(state),
        schema_version=1,
        version=version,
    )


def test_m3_versioned_snapshot_roundtrip_and_rebuild(tmp_path: Path) -> None:
    """M3：快照写 version；删 current 行后 version = 快照 version + 快照后 distinct turn。"""
    _engine, factory, repo = _open(_db_url(tmp_path))
    _create_game(repo)  # 初始快照 v1（M2 helper 不传 version，先按旧口径落 0）
    initial = repo.load(GAME_ID)
    assert initial is not None and initial.version == 1
    # 模拟 M3 GameEngine.create_game：初始快照带 version=1 重写同一 snapshot_version
    repo.save_snapshot(
        _versioned_snapshot(initial.state, snapshot_version=1, last_seq=1, version=1)
    )
    assert repo.latest_snapshot(GAME_ID) is not None
    assert repo.latest_snapshot(GAME_ID).version == 1

    # 回合 2：2 条事件 1 次提交 → version=2、last_seq=3；快照记录 version=2
    _commit_next_turn(repo, _take_then_move_drafts(turn_id=2))
    record_turn2 = repo.load(GAME_ID)
    assert record_turn2 is not None
    assert (record_turn2.version, record_turn2.last_seq) == (2, 3)
    repo.save_snapshot(
        _versioned_snapshot(
            record_turn2.state, snapshot_version=2, last_seq=3, version=record_turn2.version
        )
    )
    snapshot = repo.latest_snapshot(GAME_ID)
    assert snapshot is not None
    assert (snapshot.snapshot_version, snapshot.version, snapshot.last_seq) == (2, 2, 3)

    # 回合 3：1 条事件 → version=3、last_seq=4（不再写快照）
    _commit_next_turn(repo, [_use_torch_draft(turn_id=3)])
    before = repo.load(GAME_ID)
    assert before is not None
    assert (before.version, before.last_seq) == (3, 4)

    with factory() as session:
        versions = session.scalars(
            select(SnapshotRow.version).order_by(SnapshotRow.snapshot_version.asc())
        ).all()
    assert list(versions) == [1, 2]  # snapshots.version 列落库往返

    _delete_current_row(factory)
    rebuilt = repo.load(GAME_ID)
    assert rebuilt is not None
    assert rebuilt.version == 3  # 2 + 快照后 distinct turn 数（仅回合 3）
    assert rebuilt.last_seq == 4
    assert state_hash(rebuilt.state) == state_hash(before.state)

    # 重建后可继续提交：expected_version=3 → version=4，事件 seq 续接
    _play_moves(repo, count=1)
    final = repo.load(GAME_ID)
    assert final is not None
    assert final.version == 4
    assert final.last_seq == 5
    assert len(repo.read_events(GAME_ID)) == 5


def test_m3_legacy_snapshot_without_version_falls_back_with_multi_events(tmp_path: Path) -> None:
    """旧 M2 快照（version=0）遇 M3 多事件数据：base 取 last_seq 兜底后重建正确版本。"""
    _engine, factory, repo = _open(_db_url(tmp_path))
    _create_game(repo)  # 初始快照 v1，version 缺省 0（M2 旧数据口径）
    snapshot = repo.latest_snapshot(GAME_ID)
    assert snapshot is not None and snapshot.version == 0

    _commit_next_turn(repo, _take_then_move_drafts(turn_id=2))
    _commit_next_turn(repo, [_use_torch_draft(turn_id=3)])
    before = repo.load(GAME_ID)
    assert before is not None
    assert (before.version, before.last_seq) == (3, 4)
    assert len(repo.read_events(GAME_ID)) == 4  # 事件数 4 > 提交数 3（多事件回合）

    _delete_current_row(factory)
    rebuilt = repo.load(GAME_ID)
    assert rebuilt is not None
    # 旧快照无 version：base=last_seq=1（M2 一事件一版本兜底）+ 快照后 2 个回合 = 3
    assert rebuilt.version == 3 == before.version
    assert rebuilt.last_seq == 4
    assert state_hash(rebuilt.state) == state_hash(before.state)


def test_m3_rebuild_from_events_uses_distinct_turns_when_no_snapshot(tmp_path: Path) -> None:
    """无快照空档重建：version = 事件流 distinct turn_id 数（多事件回合不重复计数）。"""
    _engine, factory, repo = _open(_db_url(tmp_path))
    _create_game(repo, save_snapshot=False)
    _commit_next_turn(repo, _take_then_move_drafts(turn_id=2))
    _commit_next_turn(repo, [_use_torch_draft(turn_id=3)])
    before = repo.load(GAME_ID)
    assert before is not None
    assert (before.version, before.last_seq) == (3, 4)
    assert repo.latest_snapshot(GAME_ID) is None

    _delete_current_row(factory)
    rebuilt = repo.load(GAME_ID)
    assert rebuilt is not None
    assert rebuilt.version == 3  # distinct turn：{1, 2, 3}，不是事件数 4
    assert rebuilt.last_seq == 4
    assert rebuilt.state.turn == 3
    assert state_hash(rebuilt.state) == state_hash(before.state)


def test_m3_memory_repository_rebuilds_version_with_same_rule() -> None:
    """内存仓储 current 行丢失后与 SQLite 同规则重建版本（快照 version + distinct turn）。"""
    repo = InMemoryRepository()
    game_engine = GameEngine(repo)
    created = game_engine.create_game(save_seed=SAVE_SEED, game_id=GAME_ID)
    assert created.ok, created.message
    assert created.data is not None
    assert created.data["version"] == 1
    snapshot = repo.latest_snapshot(GAME_ID)
    assert snapshot is not None and snapshot.version == 1  # 引擎初始快照写 version=1

    multi = game_engine.submit(
        game_id=GAME_ID,
        expected_version=1,
        drafts=_take_then_move_drafts(turn_id=2),
    )
    assert multi.ok, multi.message
    single = game_engine.submit(
        game_id=GAME_ID,
        expected_version=2,
        drafts=[_use_torch_draft(turn_id=3)],
    )
    assert single.ok, single.message
    before = repo.load(GAME_ID)
    assert before is not None
    assert (before.version, before.last_seq) == (3, 4)

    repo.drop_current_state(GAME_ID)
    rebuilt = repo.load(GAME_ID)
    assert rebuilt is not None
    assert rebuilt.version == 3 == before.version  # 1 + 快照后 distinct turn 数（2、3）
    assert rebuilt.last_seq == 4
    assert state_hash(rebuilt.state) == state_hash(before.state)
