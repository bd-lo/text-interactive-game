"""M3-013/014 集成测试：审计只增表 ``audit_events`` 的仓储语义（契约 §3.5/D-M3-7）。

覆盖 SQLite 与内存两种 ``GameRepository`` 实现的同一组语义：

- ``save_audit`` 保存后 ``read_audit`` 可按字段逐项往返（含 ``args`` 嵌套 JSON、
  ``resolution_id``/``verdict`` 可空、中文 reason 文本）；
- ``read_audit`` 按 ``(turn_id, proposal_id, attempt)`` 升序返回，``turn_id`` 过滤，
  未知回合/未知存档返回空列表（不报错）；
- **只增**：重复投递同一 ``audit_uid``（game_id/turn_id/attempt/proposal_id 相同）时
  不覆盖、不删除既有行（先写入的内容保持不变），继续追加新行时旧行原样保留；
- 多存档隔离：按 ``game_id`` 只返回本存档审计行；
- SQLite 侧额外校验：表结构列/索引与 ``args_json`` 落库文本、行数、``created_at``。

测试只使用公开仓储 API；SQLite 用 ``sqlite:///:memory:``（StaticPool 单连接）建表。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import FrozenInstanceError, replace
from datetime import datetime
from typing import Any

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session, sessionmaker

from textgame.domain.scenario import ITEM_POTION, ITEM_TORCH, PLAYER_ID
from textgame.persistence.database import create_engine_and_session, init_schema
from textgame.persistence.memory import InMemoryRepository
from textgame.persistence.sqlite_repository import SqliteRepository
from textgame.persistence.tables import AuditEventRow
from textgame.services.ports import AuditRecord, GameRepository

GAME_ID = "game-m3-audit"
OTHER_GAME_ID = "game-m3-audit-other"


# ---------------------------------------------------------------------------
# 仓储 fixture（内存 / SQLite 参数化）
# ---------------------------------------------------------------------------
@pytest.fixture(params=["memory", "sqlite"])
def audit_repo(
    request: pytest.FixtureRequest,
) -> Iterator[tuple[GameRepository, sessionmaker[Session] | None]]:
    """返回 ``(repository, sqlite_sessionmaker_or_none)``；SQLite 内存库用完释放。"""
    if request.param == "memory":
        yield InMemoryRepository(), None
        return
    engine, factory = create_engine_and_session("sqlite:///:memory:")
    init_schema(engine)
    try:
        yield SqliteRepository(engine), factory
    finally:
        engine.dispose()


def _record(**overrides: Any) -> AuditRecord:
    """构造一条完整审计行；``overrides`` 覆盖任意字段。"""
    values: dict[str, Any] = {
        "game_id": GAME_ID,
        "turn_id": 2,
        "attempt": 1,
        "proposal_id": "p_000002_001",
        "resolution_id": "r_000002_001",
        "actor_id": PLAYER_ID,
        "tool": "take_item",
        "args": {"item_id": ITEM_POTION},
        "reason": "player_input",
        "priority": 100,
        "source": "player",
        "verdict": "applied",
        "verdict_reason": "",
        "status": "committed",
    }
    values.update(overrides)
    return AuditRecord(**values)


# ---------------------------------------------------------------------------
# 保存 / 读取往返
# ---------------------------------------------------------------------------
def test_save_and_read_audit_roundtrip(
    audit_repo: tuple[GameRepository, sessionmaker[Session] | None],
) -> None:
    """字段逐项往返：可空列、嵌套 args、中文原因文本、不同 status/verdict 组合。"""
    repo = audit_repo[0]
    records = [
        _record(),
        _record(
            turn_id=2,
            proposal_id="p_000002_002",
            resolution_id=None,
            actor_id="char_01J9Z3K4Q7X8Y9Z0ABCDEFGHJK",
            tool="move_to",
            args={"direction": "north", "nested": {"rooms": ["入口", "地窖"], "ok": True}},
            reason="npc_plan",
            priority=50,
            source="npc",
            verdict="rejected",
            verdict_reason="item is not in room",
            status="conflicted",
        ),
        _record(
            turn_id=3,
            attempt=2,
            proposal_id="p_000003_001",
            resolution_id=None,
            verdict=None,
            verdict_reason="",
            status="aborted",
            reason="conflict retry exhausted",
        ),
    ]
    repo.save_audit(records)
    loaded = repo.read_audit(GAME_ID)
    assert loaded == records
    for expected, actual in zip(records, loaded, strict=True):
        assert actual.args == expected.args
        assert actual.status == expected.status
        assert actual.verdict == expected.verdict


def test_empty_save_audit_is_noop(
    audit_repo: tuple[GameRepository, sessionmaker[Session] | None],
) -> None:
    repo = audit_repo[0]
    repo.save_audit([])
    assert repo.read_audit(GAME_ID) == []


# ---------------------------------------------------------------------------
# 排序 / turn 过滤 / 空结果
# ---------------------------------------------------------------------------
def test_read_audit_orders_by_turn_proposal_and_filters_turn(
    audit_repo: tuple[GameRepository, sessionmaker[Session] | None],
) -> None:
    """乱序写入后按 (turn_id, proposal_id, attempt) 升序；turn_id 过滤与未知值。"""
    repo = audit_repo[0]
    repo.save_audit(
        [
            _record(turn_id=4, proposal_id="p_000004_002", attempt=1),
            _record(turn_id=2, proposal_id="p_000002_002", attempt=1, actor_id=PLAYER_ID),
            _record(turn_id=3, proposal_id="p_000003_001", attempt=1),
            _record(turn_id=2, proposal_id="p_000002_001", attempt=2),
            _record(turn_id=2, proposal_id="p_000002_001", attempt=1),
        ]
    )

    all_rows = repo.read_audit(GAME_ID)
    assert [(row.turn_id, row.proposal_id, row.attempt) for row in all_rows] == [
        (2, "p_000002_001", 1),
        (2, "p_000002_001", 2),
        (2, "p_000002_002", 1),
        (3, "p_000003_001", 1),
        (4, "p_000004_002", 1),
    ]

    turn_two = repo.read_audit(GAME_ID, turn_id=2)
    assert [row.proposal_id for row in turn_two] == [
        "p_000002_001",
        "p_000002_001",
        "p_000002_002",
    ]
    assert repo.read_audit(GAME_ID, turn_id=99) == []
    assert repo.read_audit("game-does-not-exist") == []
    assert repo.read_audit("game-does-not-exist", turn_id=1) == []


# ---------------------------------------------------------------------------
# 只增：不 UPDATE / 不 DELETE
# ---------------------------------------------------------------------------
def test_save_audit_is_insert_only_and_never_overwrites(
    audit_repo: tuple[GameRepository, sessionmaker[Session] | None],
) -> None:
    """同 audit_uid 重投（内容不同）不覆盖旧行；追加新行不删除旧行。"""
    repo = audit_repo[0]
    original = _record()
    repo.save_audit([original])

    changed = _record(
        resolution_id="r_999999_999",
        args={"item_id": ITEM_TORCH},
        reason="rewritten",
        verdict="rejected",
        verdict_reason="should be ignored",
        status="aborted",
    )
    repo.save_audit([changed])  # 同 game_id/turn_id/attempt/proposal_id → INSERT 忽略

    rows = repo.read_audit(GAME_ID)
    assert rows == [original]  # 旧行内容原样保留，未被 UPDATE
    assert rows[0].status == "committed"

    second = _record(proposal_id="p_000002_002", source="npc", priority=50)
    repo.save_audit([second])  # 追加：不删除其它行
    rows = repo.read_audit(GAME_ID)
    assert [row.proposal_id for row in rows] == ["p_000002_001", "p_000002_002"]
    assert rows[0] == original
    assert rows[1] == second


def test_records_are_isolated_per_game(
    audit_repo: tuple[GameRepository, sessionmaker[Session] | None],
) -> None:
    repo = audit_repo[0]
    first = _record()
    other = _record(game_id=OTHER_GAME_ID, actor_id="npc-scavenger-a")
    repo.save_audit([first, other])

    assert repo.read_audit(GAME_ID) == [first]
    assert repo.read_audit(OTHER_GAME_ID) == [other]
    assert repo.read_audit(OTHER_GAME_ID, turn_id=2) == [other]
    assert repo.read_audit(OTHER_GAME_ID, turn_id=3) == []


# ---------------------------------------------------------------------------
# SQLite 落库细节：表结构 / 索引 / JSON 文本 / created_at / 行数
# ---------------------------------------------------------------------------
def test_sqlite_audit_table_schema_and_rows() -> None:
    engine, factory = create_engine_and_session("sqlite:///:memory:")
    init_schema(engine)
    try:
        inspector = inspect(engine)
        assert "audit_events" in inspector.get_table_names()
        columns = {column["name"] for column in inspector.get_columns("audit_events")}
        assert columns == {
            "audit_uid",
            "game_id",
            "turn_id",
            "attempt",
            "proposal_id",
            "resolution_id",
            "actor_id",
            "tool",
            "args_json",
            "reason",
            "priority",
            "source",
            "verdict",
            "verdict_reason",
            "status",
            "created_at",
        }
        index_names = {index["name"] for index in inspector.get_indexes("audit_events")}
        assert "ix_audit_events_game_turn" in index_names

        repo = SqliteRepository(engine)
        repo.save_audit(
            [
                _record(turn_id=1, args={"item_id": ITEM_POTION, "nested": {"n": 1}}),
                _record(turn_id=1, proposal_id="p_000002_002", attempt=2),
            ]
        )
        # 重复投递同一 audit_uid 不得新增行、不得改写既有行
        repo.save_audit([_record(turn_id=1, status="aborted", reason="ignored")])

        with factory() as session:
            rows = session.scalars(
                select(AuditEventRow).order_by(AuditEventRow.turn_id, AuditEventRow.attempt)
            ).all()
            assert all(isinstance(row.created_at, datetime) for row in rows)
            first = rows[0]
            assert first.audit_uid == f"aud_{GAME_ID}:000001:001:p_000002_001"
            assert (
                first.args_json == '{"item_id":"item_01J9Z3K4Q7X8Y9Z0ABCDEFGHJR","nested":{"n":1}}'
            )
            assert first.status == "committed"
            assert first.reason == "player_input"
            assert len(rows) == 2
        assert [row.attempt for row in repo.read_audit(GAME_ID)] == [1, 2]
    finally:
        engine.dispose()


def test_sqlite_audit_roundtrip_preserves_optional_columns() -> None:
    """SQLite 行级可空列（resolution_id/verdict）读出仍为 None。"""
    engine = create_engine_and_session("sqlite:///:memory:")[0]
    init_schema(engine)
    try:
        repo = SqliteRepository(engine)
        repo.save_audit([_record(resolution_id=None, verdict=None, verdict_reason="")])
        loaded = repo.read_audit(GAME_ID)
        assert loaded == [_record(resolution_id=None, verdict=None, verdict_reason="")]
        assert loaded[0].resolution_id is None
        assert loaded[0].verdict is None
    finally:
        engine.dispose()


def test_sqlite_audit_uid_primary_key_rejects_raw_duplicate_row() -> None:
    """表级主键保证物理上不能插入第二行同 audit_uid（只增表的结构兜底）。"""
    from sqlalchemy.exc import IntegrityError

    engine, factory = create_engine_and_session("sqlite:///:memory:")
    init_schema(engine)
    try:
        repo = SqliteRepository(engine)
        repo.save_audit([_record()])
        duplicate = AuditEventRow(
            audit_uid=f"aud_{GAME_ID}:000002:001:p_000002_001",
            game_id=GAME_ID,
            turn_id=2,
            attempt=1,
            proposal_id="p_000002_001",
            resolution_id=None,
            actor_id=PLAYER_ID,
            tool="take_item",
            args_json="{}",
            reason="raw duplicate",
            priority=100,
            source="player",
            verdict=None,
            verdict_reason="",
            status="committed",
            created_at=datetime.now(),
        )
        with factory() as session:
            session.add(duplicate)
            with pytest.raises(IntegrityError):
                session.commit()
        assert len(repo.read_audit(GAME_ID)) == 1
    finally:
        engine.dispose()


def test_audit_record_is_frozen_and_has_contract_fields() -> None:
    """契约字段集合固定（禁止改名/增删），frozen 防误改。"""
    record = _record()
    assert set(vars(record)) == {
        "game_id",
        "turn_id",
        "attempt",
        "proposal_id",
        "resolution_id",
        "actor_id",
        "tool",
        "args",
        "reason",
        "priority",
        "source",
        "verdict",
        "verdict_reason",
        "status",
    }
    with pytest.raises(FrozenInstanceError, match="cannot assign to field"):
        record.status = "committed"  # type: ignore[misc]
    # replace 构造新行不共享可变 args
    clone = replace(record, status="conflicted")
    assert clone.status == "conflicted"
    assert clone.args == {"item_id": ITEM_POTION}
