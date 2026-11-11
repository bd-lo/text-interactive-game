"""M4 前 R5/D-008-3 集成测试：M2 旧库升级脚本 ``legacy_upgrade``。

覆盖：

- 旧 **M2 schema**（``events`` ``seq`` 单主键、``event_uid`` 全局唯一、``snapshots``
  无 ``version``、无 ``audit_events``）→ 备份文件 → 补 ``version``/``audit_events`` →
  ``stamp 0001`` → ``upgrade head``（0002 复合主键 + 0003 人物卡 + 0004 世界书表 + 0005 场景表）：数据/状态/哈希保留、事件 seq 连续、
  可继续提交回合、审计可写、结构 = 当前 metadata；
- 已 stamp 的 ``0001``（M3 schema）→ 直接 upgrade head，数据保留；
- M3 ``create_all`` 形状但无 ``alembic_version`` → 补 stamp 后 upgrade head；
- M4 ``create_all`` 形状（已是复合主键）→ 仅 ``stamp head``（不重建、不备份）；
- 新文件 → ``upgrade head`` 初始化；
- 幂等：head 库再次升级为 no-op 且不产生新备份；
- 失败恢复：迁移中途失败 → 抛 ``LegacyUpgradeError``、SQLite 文件字节级恢复、备份保留；
- URL 校验（内存库/非 sqlite 拒绝）与 CLI 成功/失败退出码。

构造旧库一律用**原始 SQL DDL**（不依赖 git 历史中的旧 ``create_all`` 代码）；事件/状态
复用当前仍稳定的 domain 纯函数（M2 场景与事件结构未变）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, inspect, text

from textgame.domain.events import Event, EventLog, EventType
from textgame.domain.hashing import canonical_json, state_hash
from textgame.domain.models import GameState
from textgame.domain.scenario import PLAYER_ID, ROOM_CELLAR, ROOM_ENTRY, build_initial_state
from textgame.persistence import legacy_upgrade as legacy_upgrade_module
from textgame.persistence.database import ensure_schema, init_schema, prepare_db
from textgame.persistence.legacy_upgrade import (
    LegacyUpgradeError,
    LegacyUpgradeReport,
    main,
    upgrade_legacy_database,
)
from textgame.persistence.sqlite_repository import SqliteRepository
from textgame.services.game_engine import GameEngine
from textgame.services.ports import AuditRecord

pytestmark = [pytest.mark.integration]

_GAME_ID = "game-legacy-01"
_SECOND_GAME_ID = "game-legacy-02"
_SAVE_SEED = "save-42"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_TS_FORMAT = "%Y-%m-%d %H:%M:%S.%f"


# ---------------------------------------------------------------------------
# 旧 M2 schema 原始 DDL（等价 M2 create_all 建出的 4 张表）
# ---------------------------------------------------------------------------
_M2_DDL: tuple[str, ...] = (
    """
    CREATE TABLE events (
        seq BIGINT NOT NULL,
        event_uid VARCHAR(16) NOT NULL,
        game_id VARCHAR(64) NOT NULL,
        turn_id INTEGER NOT NULL,
        event_type VARCHAR(64),
        actor_id VARCHAR(64) NOT NULL,
        method VARCHAR(64) NOT NULL,
        params TEXT NOT NULL,
        result TEXT NOT NULL,
        rng_seed BIGINT NOT NULL,
        ts DATETIME NOT NULL,
        PRIMARY KEY (seq),
        UNIQUE (event_uid)
    )
    """,
    "CREATE INDEX ix_events_game_id ON events (game_id)",
    "CREATE INDEX ix_events_turn_id ON events (turn_id)",
    """
    CREATE TABLE snapshots (
        id INTEGER NOT NULL,
        game_id VARCHAR(64) NOT NULL,
        snapshot_version INTEGER NOT NULL,
        turn_id INTEGER NOT NULL,
        last_seq BIGINT NOT NULL,
        state_json TEXT NOT NULL,
        state_hash VARCHAR(64) NOT NULL,
        schema_version INTEGER NOT NULL,
        created_at DATETIME NOT NULL,
        PRIMARY KEY (id)
    )
    """,
    "CREATE INDEX ix_snapshots_game_id ON snapshots (game_id)",
    """
    CREATE TABLE game_state_current (
        game_id VARCHAR(64) NOT NULL,
        state_json TEXT NOT NULL,
        version INTEGER NOT NULL,
        last_seq BIGINT NOT NULL,
        updated_at DATETIME NOT NULL,
        PRIMARY KEY (game_id)
    )
    """,
    """
    CREATE TABLE idempotency (
        store_key VARCHAR(192) NOT NULL,
        params_hash VARCHAR(64) NOT NULL,
        result_json TEXT NOT NULL,
        created_at DATETIME NOT NULL,
        PRIMARY KEY (store_key)
    )
    """,
)


@dataclass(frozen=True)
class _SeededGame:
    """构造旧库时写入的期望数据（升级后逐项比对）。"""

    events: tuple[Event, ...]
    state: GameState


# ---------------------------------------------------------------------------
# 辅助：构造旧库 / 校验结构
# ---------------------------------------------------------------------------
def _db_url(db_path: Path) -> str:
    return f"sqlite:///{db_path.as_posix()}"


def _alembic_config(db_url: str) -> Config:
    config = Config(str(_REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_REPO_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", db_url)
    config.attributes["sqlalchemy.url"] = db_url
    return config


def _game_created_draft(initial: GameState) -> Event:
    return Event(
        turn_id=1,
        actor_id="system",
        method="game_created",
        event_type=EventType.GAME_CREATED,
        params={
            "card_id": None,
            "starting_room_id": ROOM_ENTRY,
            "initial_character_id": PLAYER_ID,
            "save_seed": _SAVE_SEED,
            "snapshot_version": 1,
        },
        result={"initial_state": initial.model_dump(mode="json")},
    )


def _move_draft(*, turn_id: int, from_room: str, to_room: str) -> Event:
    return Event(
        turn_id=turn_id,
        actor_id=PLAYER_ID,
        method="move_to",
        event_type=EventType.CHARACTER_MOVED,
        params={"direction": "north", "from_room": from_room, "to_room": to_room},
        result={"from": from_room, "to": to_room},
    )


def _insert_event(connection: Any, event: Event, *, game_id: str = _GAME_ID) -> None:
    """按旧 ``events`` 表列把盖章事件写成原始 SQL 行。"""
    assert event.seq is not None and event.id is not None and event.ts is not None
    connection.execute(
        text(
            "INSERT INTO events (seq, event_uid, game_id, turn_id, event_type, actor_id, "
            "method, params, result, rng_seed, ts) VALUES (:seq, :uid, :game_id, :turn_id, "
            ":event_type, :actor_id, :method, :params, :result, :rng_seed, :ts)"
        ),
        {
            "seq": event.seq,
            "uid": event.id,
            "game_id": game_id,
            "turn_id": event.turn_id,
            "event_type": event.event_type.value if event.event_type else None,
            "actor_id": event.actor_id,
            "method": event.method,
            "params": canonical_json(event.params),
            "result": canonical_json(event.result),
            "rng_seed": event.rng_seed,
            "ts": event.ts.strftime(_TS_FORMAT),
        },
    )


def _write_m2_legacy_database(db_path: Path) -> _SeededGame:
    """原始 SQL 建 M2 schema 库并写 1 个存档（game_created + 2 回合事件 + 快照）。"""
    engine = create_engine(_db_url(db_path))
    try:
        initial = build_initial_state(save_seed=_SAVE_SEED)
        log = EventLog(_SAVE_SEED)
        created = tuple(log.stamp([_game_created_draft(initial)]))
        log.append(list(created))
        after_create = initial.apply(list(created))
        moves = tuple(
            log.stamp(
                [
                    _move_draft(turn_id=2, from_room=ROOM_ENTRY, to_room=ROOM_CELLAR),
                    _move_draft(turn_id=3, from_room=ROOM_CELLAR, to_room=ROOM_ENTRY),
                ]
            )
        )
        final_state = after_create.apply(list(moves))
        events = (*created, *moves)
        with engine.begin() as connection:
            for ddl in _M2_DDL:
                connection.execute(text(ddl))
            for event in events:
                _insert_event(connection, event)
            connection.execute(
                text(
                    "INSERT INTO snapshots (game_id, snapshot_version, turn_id, last_seq, "
                    "state_json, state_hash, schema_version, created_at) VALUES (:game_id, 1, 1, "
                    "1, :state_json, :state_hash, 1, :created_at)"
                ),
                {
                    "game_id": _GAME_ID,
                    "state_json": canonical_json(after_create.model_dump(mode="json")),
                    "state_hash": state_hash(after_create),
                    "created_at": events[0].ts.strftime(_TS_FORMAT),  # type: ignore[union-attr]
                },
            )
            connection.execute(
                text(
                    "INSERT INTO game_state_current (game_id, state_json, version, last_seq, "
                    "updated_at) VALUES (:game_id, :state_json, 3, 3, :updated_at)"
                ),
                {
                    "game_id": _GAME_ID,
                    "state_json": canonical_json(final_state.model_dump(mode="json")),
                    "updated_at": events[-1].ts.strftime(_TS_FORMAT),  # type: ignore[union-attr]
                },
            )
    finally:
        engine.dispose()
    return _SeededGame(events=events, state=final_state)


def _schema_signature(engine: Engine) -> dict[str, dict[str, object]]:
    """反射 schema（表 → 列/主键/索引/唯一约束），忽略 alembic_version。"""
    inspector = inspect(engine)
    signature: dict[str, dict[str, object]] = {}
    for table in sorted(inspector.get_table_names()):
        if table == "alembic_version":
            continue
        signature[table] = {
            "columns": {
                (column["name"], str(column["type"]), bool(column["nullable"]))
                for column in inspector.get_columns(table)
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
    return signature


def _assert_schema_matches_metadata(engine: Engine, tmp_path: Path) -> None:
    created_engine = create_engine(_db_url(tmp_path / "metadata-reference.db"))
    try:
        init_schema(created_engine)
        assert _schema_signature(engine) == _schema_signature(created_engine)
    finally:
        created_engine.dispose()


def _alembic_revision(engine: Engine) -> str:
    with engine.connect() as connection:
        value = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert isinstance(value, str)
    return value


def _backups(db_path: Path) -> set[Path]:
    return set(db_path.parent.glob(f"{db_path.name}.bak-*"))


def _stamp_and_seed_0001(db_url: str, *, drop_alembic_version: bool) -> _SeededGame:
    """建 0001（M3）schema 并写单存档数据；可选抹掉 alembic_version 模拟 create_all 库。"""
    initial = build_initial_state(save_seed=_SAVE_SEED)
    log = EventLog(_SAVE_SEED)
    created = tuple(log.stamp([_game_created_draft(initial)]))
    log.append(list(created))
    after_create = initial.apply(list(created))
    moves = tuple(log.stamp([_move_draft(turn_id=2, from_room=ROOM_ENTRY, to_room=ROOM_CELLAR)]))
    final_state = after_create.apply(list(moves))
    events = (*created, *moves)

    command.upgrade(_alembic_config(db_url), "0001")
    engine = create_engine(db_url)
    try:
        with engine.begin() as connection:
            if drop_alembic_version:
                connection.execute(text("DROP TABLE alembic_version"))
            for event in events:
                _insert_event(connection, event)
            connection.execute(
                text(
                    "INSERT INTO snapshots (game_id, snapshot_version, turn_id, last_seq, "
                    "state_json, state_hash, schema_version, version, created_at) VALUES "
                    "(:game_id, 1, 2, 2, :state_json, :state_hash, 1, 2, :created_at)"
                ),
                {
                    "game_id": _GAME_ID,
                    "state_json": canonical_json(final_state.model_dump(mode="json")),
                    "state_hash": state_hash(final_state),
                    "created_at": events[-1].ts.strftime(_TS_FORMAT),  # type: ignore[union-attr]
                },
            )
            connection.execute(
                text(
                    "INSERT INTO game_state_current (game_id, state_json, version, last_seq, "
                    "updated_at) VALUES (:game_id, :state_json, 2, 2, :updated_at)"
                ),
                {
                    "game_id": _GAME_ID,
                    "state_json": canonical_json(final_state.model_dump(mode="json")),
                    "updated_at": events[-1].ts.strftime(_TS_FORMAT),  # type: ignore[union-attr]
                },
            )
    finally:
        engine.dispose()
    return _SeededGame(events=events, state=final_state)


# ---------------------------------------------------------------------------
# 旧 M2 schema：完整升级链路
# ---------------------------------------------------------------------------
def test_upgrade_m2_legacy_database_end_to_end(tmp_path: Path) -> None:
    """M2 旧库：备份 → 补列/建表 → stamp 0001 → head；数据保留且可继续使用。"""
    db_path = tmp_path / "legacy-m2.db"
    seeded = _write_m2_legacy_database(db_path)
    original_bytes = db_path.read_bytes()

    report = upgrade_legacy_database(_db_url(db_path))

    assert report.action == "upgraded_legacy"
    assert report.from_revision is None
    assert report.to_revision == "0006"
    assert report.changed is True
    assert report.backup_path is not None
    backup = Path(report.backup_path)
    assert backup.exists()
    assert backup.name.startswith(f"{db_path.name}.bak-")
    assert backup.read_bytes() == original_bytes  # 备份 = 升级前原始字节
    assert any("ADD COLUMN version" in step for step in report.steps)
    assert any("audit_events" in step for step in report.steps)

    engine = create_engine(_db_url(db_path))
    try:
        assert _alembic_revision(engine) == "0006"
        _assert_schema_matches_metadata(engine, tmp_path)

        repo = SqliteRepository(engine)
        record = repo.load(_GAME_ID)
        assert record is not None
        assert (record.version, record.last_seq) == (3, 3)
        assert state_hash(record.state) == state_hash(seeded.state)
        assert repo.read_events(_GAME_ID) == list(seeded.events)
        snapshot = repo.latest_snapshot(_GAME_ID)
        assert snapshot is not None
        assert snapshot.version == 0  # 旧快照补列后为默认 0，按 M2 规则兜底

        # 升级后 current 行丢失也可重建（旧快照 version=0 走 last_seq 兜底规则）
        with engine.begin() as connection:
            connection.execute(
                text("DELETE FROM game_state_current WHERE game_id = :game_id"),
                {"game_id": _GAME_ID},
            )
        rebuilt = repo.load(_GAME_ID)
        assert rebuilt is not None
        assert (rebuilt.version, rebuilt.last_seq) == (3, 3)
        assert state_hash(rebuilt.state) == state_hash(seeded.state)

        # 升级后可继续提交回合（seq 续接、版本 +1）
        game_engine = GameEngine(repo)
        submitted = game_engine.submit(
            game_id=_GAME_ID,
            expected_version=record.version,
            drafts=[_move_draft(turn_id=4, from_room=ROOM_ENTRY, to_room=ROOM_CELLAR)],
        )
        assert submitted.ok, submitted.message
        after = repo.load(_GAME_ID)
        assert after is not None
        assert (after.version, after.last_seq) == (4, 4)
        assert [event.seq for event in repo.read_events(_GAME_ID)] == [1, 2, 3, 4]

        # 审计只增表可用
        repo.save_audit(
            [
                AuditRecord(
                    game_id=_GAME_ID,
                    turn_id=4,
                    attempt=1,
                    proposal_id="p_000004_001",
                    resolution_id="r_000004_001",
                    actor_id=PLAYER_ID,
                    tool="move_to",
                    args={"direction": "north"},
                    reason="player_input",
                    priority=100,
                    source="player",
                    verdict="applied",
                    verdict_reason="",
                    status="committed",
                )
            ]
        )
        assert len(repo.read_audit(_GAME_ID, turn_id=4)) == 1

        # 多存档：升级后的同库可再建第二个游戏，各自 seq 从 1 起、互不干扰
        second = game_engine.create_game(save_seed="save-99", game_id=_SECOND_GAME_ID)
        assert second.ok, second.message
        second_record = repo.load(_SECOND_GAME_ID)
        assert second_record is not None
        assert (second_record.version, second_record.last_seq) == (1, 1)
        assert [event.seq for event in repo.read_events(_SECOND_GAME_ID)] == [1]
        assert [event.seq for event in repo.read_events(_GAME_ID)] == [1, 2, 3, 4]
    finally:
        engine.dispose()


def test_upgrade_m2_legacy_database_is_idempotent(tmp_path: Path) -> None:
    """head 库再升级：no-op、不新建备份。"""
    db_path = tmp_path / "legacy-idempotent.db"
    _write_m2_legacy_database(db_path)
    first = upgrade_legacy_database(_db_url(db_path))
    backups_after_first = _backups(db_path)
    assert first.backup_path is not None and len(backups_after_first) == 1

    second = upgrade_legacy_database(_db_url(db_path))

    assert second.action == "noop"
    assert second.changed is False
    assert second.backup_path is None
    assert second.from_revision == "0006"
    assert _backups(db_path) == backups_after_first


# ---------------------------------------------------------------------------
# 已 stamp 0001（M3 schema）→ head（0002/0003/0004/0005）
# ---------------------------------------------------------------------------
def test_upgrade_from_stamped_0001_to_head_preserves_data(tmp_path: Path) -> None:
    """M3（0001）库直接 upgrade head：0002 重建主键、0003 人物卡、0004 世界书表、0005 场景表，数据保留。"""
    db_path = tmp_path / "m3-0001.db"
    db_url = _db_url(db_path)
    seeded = _stamp_and_seed_0001(db_url, drop_alembic_version=False)

    report = upgrade_legacy_database(db_url)

    assert report.action == "upgraded_m3"
    assert report.from_revision == "0001"
    assert report.backup_path is not None
    engine = create_engine(db_url)
    try:
        assert _alembic_revision(engine) == "0006"
        _assert_schema_matches_metadata(engine, tmp_path)
        repo = SqliteRepository(engine)
        record = repo.load(_GAME_ID)
        assert record is not None
        assert (record.version, record.last_seq) == (2, 2)
        assert state_hash(record.state) == state_hash(seeded.state)
        assert repo.read_events(_GAME_ID) == list(seeded.events)
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# M3 create_all 形状（无 alembic_version）→ 补 stamp 后升级到 head（0002/0003/0004/0005）
# ---------------------------------------------------------------------------
def test_upgrade_m3_create_all_schema_without_alembic_version(tmp_path: Path) -> None:
    """M3 create_all 形状（已有 version/审计、事件仍 seq 单主键）也能升级到 head。"""
    db_path = tmp_path / "m3-createall.db"
    db_url = _db_url(db_path)
    seeded = _stamp_and_seed_0001(db_url, drop_alembic_version=True)

    report = upgrade_legacy_database(db_url)

    assert report.action == "upgraded_legacy"
    assert any("stamp 0001" in step for step in report.steps)
    assert not any("ADD COLUMN version" in step for step in report.steps)
    engine = create_engine(db_url)
    try:
        assert _alembic_revision(engine) == "0006"
        _assert_schema_matches_metadata(engine, tmp_path)
        repo = SqliteRepository(engine)
        record = repo.load(_GAME_ID)
        assert record is not None
        assert state_hash(record.state) == state_hash(seeded.state)
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# M4 create_all 形状（复合主键、无 alembic_version）→ stamp head
# ---------------------------------------------------------------------------
def test_upgrade_m4_create_all_schema_stamps_head(tmp_path: Path) -> None:
    """当前 create_all 建出的 head 形状库：仅补 alembic_version=head，不重建表/不备份。"""
    db_path = tmp_path / "m4-createall.db"
    engine = create_engine(_db_url(db_path))
    try:
        init_schema(engine)
    finally:
        engine.dispose()

    report = upgrade_legacy_database(_db_url(db_path))

    assert report.action == "stamped_head"
    assert report.from_revision is None
    assert report.backup_path is None
    assert _backups(db_path) == set()
    engine = create_engine(_db_url(db_path))
    try:
        assert _alembic_revision(engine) == "0006"
        _assert_schema_matches_metadata(engine, tmp_path)
        # 可直接多存档写入
        repo = SqliteRepository(engine)
        game_engine = GameEngine(repo)
        assert game_engine.create_game(save_seed="save-a", game_id="game-a").ok
        assert game_engine.create_game(save_seed="save-b", game_id="game-b").ok
        assert [event.seq for event in repo.read_events("game-a")] == [1]
        assert [event.seq for event in repo.read_events("game-b")] == [1]
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# 新文件 / 参数校验 / 失败恢复 / CLI
# ---------------------------------------------------------------------------
def test_upgrade_fresh_database_initializes_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "fresh.db"
    report = upgrade_legacy_database(_db_url(db_path))

    assert report.action == "initialized"
    assert report.backup_path is None
    engine = create_engine(_db_url(db_path))
    try:
        assert _alembic_revision(engine) == "0006"
        _assert_schema_matches_metadata(engine, tmp_path)
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "db_url",
    ["sqlite:///:memory:", "sqlite://", "postgresql+psycopg://localhost/textgame"],
)
def test_upgrade_rejects_unsupported_db_urls(db_url: str) -> None:
    with pytest.raises(LegacyUpgradeError):
        upgrade_legacy_database(db_url)


def test_upgrade_failure_restores_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """迁移中途失败：文件字节级恢复、备份保留、抛明确错误。"""
    db_path = tmp_path / "legacy-failure.db"
    _write_m2_legacy_database(db_path)
    original_bytes = db_path.read_bytes()

    def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("simulated migration failure")

    monkeypatch.setattr(legacy_upgrade_module, "_run_migrations", _boom)

    with pytest.raises(LegacyUpgradeError, match="已恢复备份") as excinfo:
        upgrade_legacy_database(_db_url(db_path))

    backup = excinfo.value.backup_path
    assert backup is not None and backup.exists()
    assert backup.read_bytes() == original_bytes
    assert db_path.read_bytes() == original_bytes  # 内容回滚（补列/建表均已撤销）
    engine = create_engine(_db_url(db_path))
    try:
        tables = set(inspect(engine).get_table_names())
        assert "audit_events" not in tables
        assert "alembic_version" not in tables
        snapshot_columns = {column["name"] for column in inspect(engine).get_columns("snapshots")}
        assert "version" not in snapshot_columns
    finally:
        engine.dispose()
    # 未恢复的备份保留在侧（供人工核对）
    assert len(_backups(db_path)) == 1


def test_cli_success_and_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "cli.db"
    _write_m2_legacy_database(db_path)

    assert main(["--db-url", _db_url(db_path)]) == 0
    output = capsys.readouterr().out
    assert "upgraded_legacy" in output
    assert '"to_revision": "0006"' in output

    # 失败路径：返回 1 且 stderr 有明确错误
    def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise LegacyUpgradeError("boom for CLI")

    monkeypatch.setattr(legacy_upgrade_module, "upgrade_legacy_database", _boom)
    assert main(["--db-url", _db_url(db_path)]) == 1
    assert "旧库升级失败" in capsys.readouterr().err


def test_report_as_dict_is_json_friendly() -> None:
    report = LegacyUpgradeReport(db_url="sqlite:///x.db", action="noop", from_revision="0006")
    payload = report.as_dict()
    assert payload["changed"] is False
    assert payload["steps"] == []
    assert payload["to_revision"] == "0006"


# ---------------------------------------------------------------------------
# R-05：启动期 ensure_schema 自动升级
# ---------------------------------------------------------------------------
def test_ensure_schema_auto_upgrades_m2_database(tmp_path: Path) -> None:
    """R-05：启动守卫 detect 到 M2 旧库 → 自动升级到 head，数据/哈希保留。"""
    db_path = tmp_path / "auto-upgrade.db"
    seeded = _write_m2_legacy_database(db_path)
    db_url = _db_url(db_path)
    engine, _factory = prepare_db(db_url)
    try:
        ensure_schema(engine, db_url=db_url)

        assert _alembic_revision(engine) == "0006"
        record = SqliteRepository(engine).load(_GAME_ID)
        assert record is not None
        assert state_hash(record.state) == state_hash(seeded.state)
    finally:
        engine.dispose()


def test_ensure_schema_is_noop_for_head_database(tmp_path: Path) -> None:
    """R-05：head 形状库 ensure_schema 不备份、不改数据（幂等）。"""
    db_path = tmp_path / "healthy.db"
    db_url = _db_url(db_path)
    engine, _factory = prepare_db(db_url)
    try:
        init_schema(engine)
        ensure_schema(engine, db_url=db_url)
        assert _backups(db_path) == set()
        ensure_schema(engine, db_url=db_url)
        assert _backups(db_path) == set()
    finally:
        engine.dispose()
