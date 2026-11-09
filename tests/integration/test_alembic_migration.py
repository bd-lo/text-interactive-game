"""M3/M4 集成测试：Alembic 迁移链（0001 baseline → 0002 复合主键；契约 D-M3-6/D-008-3）。

覆盖：

- ``alembic upgrade head`` 在临时 SQLite 文件库上建出全部 5 张表
  （events / snapshots / game_state_current / idempotency / audit_events）、
  ``alembic_version=0002``，``events`` 为 ``(game_id, seq)`` 复合主键 +
  ``(game_id, event_uid)`` 复合唯一 + ``(game_id, turn_id, seq)`` 索引；
- 迁移库 schema 与 ORM ``Base.metadata.create_all`` 的**表/列/主键/索引/唯一约束**
  一致（防止迁移与 tables.py 漂移）；
- 重复 ``upgrade head`` 幂等；``downgrade base`` 删除全部表后可再次 upgrade；
- 0001（M3 schema）单存档数据 upgrade head 后行级保留、可插入第二个存档
  （同 seq=1 可共存），删掉第二个存档数据后可 downgrade 回 0001（旧全局唯一约束）再
  upgrade，数据仍保留；
- 迁移建出的库可直接被 ``SqliteRepository`` + ``GameEngine`` 使用（单/多存档 + 审计）。

测试只写 ``tmp_path``，不触碰仓库 ``data/``；用程序化 ``alembic.config.Config``
指定绝对 ``script_location``，不依赖当前工作目录。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, inspect, text

from textgame.domain.events import Event, EventType
from textgame.domain.scenario import ITEM_TORCH, PLAYER_ID, ROOM_ENTRY, build_initial_state
from textgame.persistence.database import init_schema
from textgame.persistence.sqlite_repository import SqliteRepository
from textgame.persistence.tables import Base
from textgame.services.game_engine import GameEngine
from textgame.services.ports import AuditRecord

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXPECTED_TABLES = {
    "events",
    "snapshots",
    "game_state_current",
    "idempotency",
    "audit_events",
}
_HEAD_REVISION = "0002"
_TS = "2026-11-09 00:00:00.000000"


def _db_url(tmp_path: Path, name: str = "alembic.db") -> str:
    """tmp 目录下的 SQLite 文件库 URL（统一正斜杠，Windows 亦可）。"""
    return f"sqlite:///{tmp_path.joinpath(name).as_posix()}"


def _alembic_config(db_url: str) -> Config:
    """按仓库根目录的 ``alembic.ini`` 构造程序化配置（绝对 script_location）。"""
    config = Config(str(_REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_REPO_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", db_url)
    config.attributes["sqlalchemy.url"] = db_url
    return config


def _upgrade(db_url: str, revision: str = "head") -> None:
    """执行 ``alembic upgrade <revision>``。"""
    command.upgrade(_alembic_config(db_url), revision)


def _engine(db_url: str) -> Engine:
    """打开临时 SQLite 文件库的 Engine（用完 dispose）。"""
    return create_engine(db_url)


def _schema_snapshot(engine: Engine) -> dict[str, dict[str, object]]:
    """反射出「表 → 列/可空/主键/索引/唯一约束」结构快照（忽略 alembic_version）。"""
    inspector = inspect(engine)
    snapshot: dict[str, dict[str, object]] = {}
    for table in sorted(inspector.get_table_names()):
        if table == "alembic_version":
            continue
        columns = inspector.get_columns(table)
        snapshot[table] = {
            "columns": {
                (column["name"], str(column["type"]), bool(column["nullable"]))
                for column in columns
            },
            "pk": tuple(inspector.get_pk_constraint(table)["constrained_columns"] or ()),
            "indexes": {
                index["name"]: tuple(index["column_names"])
                for index in inspector.get_indexes(table)
                if not str(index["name"]).startswith("sqlite_autoindex")
            },
            "unique": {
                tuple(constraint["column_names"])
                for constraint in inspector.get_unique_constraints(table)
                if constraint["name"] is None
                or not str(constraint["name"]).startswith("sqlite_autoindex")
            },
        }
    return snapshot


def _alembic_revision(engine: Engine) -> str:
    with engine.connect() as connection:
        value = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert isinstance(value, str)
    return value


def _insert_raw_event(connection: Any, *, game_id: str, seq: int, uid: str) -> None:
    """按 0001/head 共有列插入一行事件（测试数据保留用，params/result 用空对象）。"""
    connection.execute(
        text(
            "INSERT INTO events (seq, event_uid, game_id, turn_id, event_type, actor_id, "
            "method, params, result, rng_seed, ts) VALUES (:seq, :uid, :game_id, :turn, "
            "'CHARACTER_MOVED', :actor, 'move_to', '{}', '{}', 7, :ts)"
        ),
        {
            "seq": seq,
            "uid": uid,
            "game_id": game_id,
            "turn": seq,
            "actor": PLAYER_ID,
            "ts": _TS,
        },
    )


# ---------------------------------------------------------------------------
# upgrade head：建表 + alembic_version
# ---------------------------------------------------------------------------
def test_upgrade_head_creates_all_tables(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    _upgrade(db_url)
    engine = _engine(db_url)
    try:
        tables = set(inspect(engine).get_table_names())
        assert tables >= _EXPECTED_TABLES
        assert "alembic_version" in tables
        assert _alembic_revision(engine) == _HEAD_REVISION

        columns = {column["name"] for column in inspect(engine).get_columns("audit_events")}
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
        snapshot_columns = {column["name"] for column in inspect(engine).get_columns("snapshots")}
        assert "version" in snapshot_columns  # D-M3-6 新增列必须在 baseline 内

        inspector = inspect(engine)
        assert tuple(inspector.get_pk_constraint("events")["constrained_columns"] or ()) == (
            "game_id",
            "seq",
        )
        unique = {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints("events")
        }
        assert ("game_id", "event_uid") in unique
        index_names = {str(index["name"]) for index in inspector.get_indexes("events")}
        assert "ix_events_game_turn_seq" in index_names
        assert "ix_events_game_id" not in index_names  # 复合主键前缀已覆盖
        assert "ix_events_turn_id" not in index_names
        audit_indexes = {str(index["name"]) for index in inspector.get_indexes("audit_events")}
        assert "ix_audit_events_game_turn" in audit_indexes
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# head 与 ORM metadata 一致性
# ---------------------------------------------------------------------------
def test_head_schema_matches_orm_metadata(tmp_path: Path) -> None:
    """迁移 head 库 vs ``Base.metadata.create_all`` 库：表/列/主键/索引/唯一约束一致。"""
    migrated_url = _db_url(tmp_path, "migrated.db")
    _upgrade(migrated_url)
    migrated_engine = _engine(migrated_url)
    created_engine = _engine(_db_url(tmp_path, "created.db"))
    try:
        init_schema(created_engine)
        migrated = _schema_snapshot(migrated_engine)
        created = _schema_snapshot(created_engine)
        assert set(migrated) == set(created) == _EXPECTED_TABLES
        for table in sorted(_EXPECTED_TABLES):
            assert migrated[table] == created[table], f"schema drift on table {table}"
    finally:
        migrated_engine.dispose()
        created_engine.dispose()


# ---------------------------------------------------------------------------
# 幂等 / downgrade
# ---------------------------------------------------------------------------
def test_upgrade_head_is_idempotent(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    _upgrade(db_url)
    _upgrade(db_url)  # 已在 head：再次 upgrade 为空操作

    engine = _engine(db_url)
    try:
        with engine.connect() as connection:
            count = connection.execute(text("SELECT COUNT(*) FROM alembic_version")).scalar()
        assert count == 1
        assert _alembic_revision(engine) == _HEAD_REVISION
    finally:
        engine.dispose()


def test_downgrade_base_drops_all_tables_then_reupgrade(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    _upgrade(db_url)
    command.downgrade(_alembic_config(db_url), "base")
    engine = _engine(db_url)
    try:
        assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
    finally:
        engine.dispose()

    _upgrade(db_url)  # 可再次 upgrade 回 head
    engine = _engine(db_url)
    try:
        assert set(inspect(engine).get_table_names()) >= _EXPECTED_TABLES
        assert _alembic_revision(engine) == _HEAD_REVISION
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# 0001 单存档数据 → head 数据保留（0002 batch recreate），并可回滚
# ---------------------------------------------------------------------------
def test_0001_to_head_preserves_single_save_data_and_rolls_back(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    _upgrade(db_url, "0001")
    engine = _engine(db_url)
    try:
        with engine.begin() as connection:
            for seq in (1, 2, 3):
                _insert_raw_event(connection, game_id="game-m3", seq=seq, uid=f"evt_{seq:06d}")
        assert _alembic_revision(engine) == "0001"
    finally:
        engine.dispose()

    _upgrade(db_url)  # 0001 → head（0002 重建 events 主键）
    engine = _engine(db_url)
    try:
        assert _alembic_revision(engine) == _HEAD_REVISION
        with engine.connect() as connection:
            rows = connection.execute(
                text("SELECT game_id, seq, event_uid FROM events ORDER BY game_id, seq")
            ).all()
        assert [tuple(row) for row in rows] == [
            ("game-m3", 1, "evt_000001"),
            ("game-m3", 2, "evt_000002"),
            ("game-m3", 3, "evt_000003"),
        ]
        # 复合主键生效：第二个存档的 seq=1 / evt_000001 可与第一个存档共存
        with engine.begin() as connection:
            _insert_raw_event(connection, game_id="game-m4", seq=1, uid="evt_000001")
        with engine.connect() as connection:
            count = connection.execute(text("SELECT COUNT(*) FROM events")).scalar()
        assert count == 4
    finally:
        engine.dispose()

    # downgrade 回 0001：先删掉会与全局唯一约束冲突的第二存档数据（多存档 downgrade 不安全）
    engine = _engine(db_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM events WHERE game_id = 'game-m4'"))
    finally:
        engine.dispose()
    command.downgrade(_alembic_config(db_url), "0001")
    engine = _engine(db_url)
    try:
        assert _alembic_revision(engine) == "0001"
        inspector = inspect(engine)
        assert tuple(inspector.get_pk_constraint("events")["constrained_columns"] or ()) == ("seq",)
        with engine.connect() as connection:
            rows = connection.execute(
                text("SELECT game_id, seq, event_uid FROM events ORDER BY seq")
            ).all()
        assert [tuple(row) for row in rows] == [
            ("game-m3", 1, "evt_000001"),
            ("game-m3", 2, "evt_000002"),
            ("game-m3", 3, "evt_000003"),
        ]
    finally:
        engine.dispose()

    _upgrade(db_url)  # 再次 upgrade：数据仍保留
    engine = _engine(db_url)
    try:
        with engine.connect() as connection:
            count = connection.execute(text("SELECT COUNT(*) FROM events")).scalar()
        assert count == 3
        assert tuple(inspect(engine).get_pk_constraint("events")["constrained_columns"] or ()) == (
            "game_id",
            "seq",
        )
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# 迁移建出的库可被仓储/引擎直接使用（单存档 + 多存档 + 审计）
# ---------------------------------------------------------------------------
def test_migrated_schema_supports_engine_roundtrip(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    _upgrade(db_url)
    engine = _engine(db_url)
    try:
        repo = SqliteRepository(engine)
        game_engine = GameEngine(repo)
        created = game_engine.create_game(save_seed="save-m3-alembic", game_id="game-alembic-01")
        assert created.ok, created.message

        record = repo.load("game-alembic-01")
        assert record is not None
        assert (record.version, record.last_seq) == (1, 1)

        initial = build_initial_state(save_seed="save-m3-alembic")
        assert initial.characters[PLAYER_ID].location == ROOM_ENTRY
        submitted = game_engine.submit(
            game_id="game-alembic-01",
            expected_version=record.version,
            drafts=[
                Event(
                    turn_id=2,
                    actor_id=PLAYER_ID,
                    method="take_item",
                    event_type=EventType.ITEM_TAKEN,
                    params={"item_id": ITEM_TORCH, "from_room": ROOM_ENTRY},
                    result={"inventory_size": 1},
                )
            ],
        )
        assert submitted.ok, submitted.message
        after = repo.load("game-alembic-01")
        assert after is not None
        assert (after.version, after.last_seq) == (2, 2)
        # 迁移库上也可写 M3 审计行（只增表）
        repo.save_audit(
            [
                AuditRecord(
                    game_id="game-alembic-01",
                    turn_id=2,
                    attempt=1,
                    proposal_id="p_000002_001",
                    resolution_id="r_000002_001",
                    actor_id=PLAYER_ID,
                    tool="take_item",
                    args={"item_id": ITEM_TORCH},
                    reason="player_input",
                    priority=100,
                    source="player",
                    verdict="applied",
                    verdict_reason="",
                    status="committed",
                )
            ]
        )
        assert len(repo.read_audit("game-alembic-01", turn_id=2)) == 1
    finally:
        engine.dispose()


def test_migrated_schema_supports_multiple_saves(tmp_path: Path) -> None:
    """迁移 head 库：同库两个存档各自 seq 从 1 起、互相隔离。"""
    db_url = _db_url(tmp_path)
    _upgrade(db_url)
    engine = _engine(db_url)
    try:
        repo = SqliteRepository(engine)
        game_engine = GameEngine(repo)
        assert game_engine.create_game(save_seed="save-a", game_id="game-a").ok
        assert game_engine.create_game(save_seed="save-b", game_id="game-b").ok
        for game_id in ("game-a", "game-b"):
            record = repo.load(game_id)
            assert record is not None
            assert (record.version, record.last_seq) == (1, 1)
            events = repo.read_events(game_id)
            assert [event.seq for event in events] == [1]
            assert [event.id for event in events] == ["evt_000001"]
        # 各自继续提交，seq/版本互不影响
        assert game_engine.submit(
            game_id="game-a",
            expected_version=1,
            drafts=[
                Event(
                    turn_id=2,
                    actor_id=PLAYER_ID,
                    method="take_item",
                    event_type=EventType.ITEM_TAKEN,
                    params={"item_id": ITEM_TORCH, "from_room": ROOM_ENTRY},
                    result={"inventory_size": 1},
                )
            ],
        ).ok
        assert [event.seq for event in repo.read_events("game-a")] == [1, 2]
        assert [event.seq for event in repo.read_events("game-b")] == [1]
        record_a = repo.load("game-a")
        record_b = repo.load("game-b")
        assert record_a is not None and record_b is not None
        assert record_a.version == 2
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# 配置 / ORM metadata
# ---------------------------------------------------------------------------
def test_alembic_ini_is_ascii_only() -> None:
    """alembic.ini 必须纯 ASCII：Alembic 在 Windows 用 locale(gbk) 读 ini，UTF-8 会崩。"""
    payload = (_REPO_ROOT / "alembic.ini").read_bytes()
    payload.decode("ascii")


def test_orm_metadata_contains_all_baseline_tables() -> None:
    """tables.py 的 metadata 与迁移 head 目标表/主键/唯一约束/索引一致。"""
    assert set(Base.metadata.tables) == _EXPECTED_TABLES
    assert "version" in Base.metadata.tables["snapshots"].columns
    events = Base.metadata.tables["events"]
    assert [column.name for column in events.primary_key.columns] == ["game_id", "seq"]
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in events.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("game_id", "event_uid") in unique_columns
    assert "ix_events_game_turn_seq" in {index.name for index in events.indexes}
    assert "uq_events_game_event_uid" in {constraint.name for constraint in events.constraints}
