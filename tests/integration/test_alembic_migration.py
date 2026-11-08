"""M3-013 集成测试：Alembic 最小 baseline 迁移（契约 §1 D-M3-6、任务 5）。

覆盖：

- ``alembic upgrade head`` 在临时 SQLite 文件库上建出 M3 全部 5 张表
  （events / snapshots / game_state_current / idempotency / audit_events）并记录
  ``alembic_version=0001``；
- 迁移建出的 schema 与 ORM ``Base.metadata.create_all`` 的**表/列/主键/索引/唯一约束**
  一致（防止 baseline 与 tables.py 漂移）；
- 重复 ``upgrade head`` 幂等；
- ``downgrade base`` 删除全部 5 张表后可再次 upgrade；
- 迁移建出的库可直接被 ``SqliteRepository`` + ``GameEngine`` 使用（建游戏 + 提交回合）。

测试只写 ``tmp_path``，不触碰仓库 ``data/``；用程序化 ``alembic.config.Config``
指定绝对 ``script_location``，不依赖当前工作目录。
"""

from __future__ import annotations

from pathlib import Path

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


def _db_url(tmp_path: Path, name: str = "alembic.db") -> str:
    """tmp 目录下的 SQLite 文件库 URL（统一正斜杠，Windows 亦可）。"""
    return f"sqlite:///{tmp_path.joinpath(name).as_posix()}"


def _alembic_config(db_url: str) -> Config:
    """按仓库根目录的 ``alembic.ini`` 构造程序化配置（绝对 script_location）。"""
    config = Config(str(_REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_REPO_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", db_url)
    return config


def _upgrade_head(db_url: str) -> None:
    """执行 ``alembic upgrade head``。"""
    command.upgrade(_alembic_config(db_url), "head")


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
            "columns": {(column["name"], bool(column["nullable"])) for column in columns},
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


# ---------------------------------------------------------------------------
# upgrade head：建表 + alembic_version
# ---------------------------------------------------------------------------
def test_upgrade_head_creates_all_m3_tables(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    _upgrade_head(db_url)
    engine = _engine(db_url)
    try:
        tables = set(inspect(engine).get_table_names())
        assert tables >= _EXPECTED_TABLES
        assert "alembic_version" in tables

        with engine.connect() as connection:
            version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert version == "0001"

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
        index_names = {index["name"] for index in inspect(engine).get_indexes("audit_events")}
        assert "ix_audit_events_game_turn" in index_names
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# baseline 与 ORM metadata 一致性
# ---------------------------------------------------------------------------
def test_baseline_schema_matches_orm_metadata(tmp_path: Path) -> None:
    """迁移库 vs ``Base.metadata.create_all`` 库：表/列/主键/索引/唯一约束一致。"""
    migrated_url = _db_url(tmp_path, "migrated.db")
    _upgrade_head(migrated_url)
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
    _upgrade_head(db_url)
    _upgrade_head(db_url)  # 已在 head：再次 upgrade 为空操作

    engine = _engine(db_url)
    try:
        with engine.connect() as connection:
            count = connection.execute(text("SELECT COUNT(*) FROM alembic_version")).scalar()
        assert count == 1
    finally:
        engine.dispose()


def test_downgrade_base_drops_all_tables_then_reupgrade(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    _upgrade_head(db_url)
    command.downgrade(_alembic_config(db_url), "base")
    engine = _engine(db_url)
    try:
        assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
    finally:
        engine.dispose()

    _upgrade_head(db_url)  # 可再次 upgrade 回 head
    engine = _engine(db_url)
    try:
        assert set(inspect(engine).get_table_names()) >= _EXPECTED_TABLES
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# 迁移建出的库可被仓储/引擎直接使用
# ---------------------------------------------------------------------------
def test_migrated_schema_supports_engine_roundtrip(tmp_path: Path) -> None:
    db_url = _db_url(tmp_path)
    _upgrade_head(db_url)
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


def test_alembic_ini_is_ascii_only() -> None:
    """alembic.ini 必须纯 ASCII：Alembic 在 Windows 用 locale(gbk) 读 ini，UTF-8 会崩。"""
    payload = (_REPO_ROOT / "alembic.ini").read_bytes()
    payload.decode("ascii")


def test_orm_metadata_contains_all_baseline_tables() -> None:
    """tables.py 的 metadata 与 baseline 迁移目标表集合一致。"""
    assert set(Base.metadata.tables) == _EXPECTED_TABLES
    assert "version" in Base.metadata.tables["snapshots"].columns
