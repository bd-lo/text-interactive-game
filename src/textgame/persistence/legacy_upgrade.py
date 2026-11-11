"""M2/M3 旧库升级脚本（PROGRESS R5 + D-008-3）：把历史 SQLite 库对齐到 Alembic head。

背景：M2 用 ``Base.metadata.create_all`` 建表，没有 ``alembic_version``、也没有
``snapshots.version`` 列与 ``audit_events`` 表；M3 起提供 Alembic baseline（0001），
M4 前 D-008-3 又用 0002 把 ``events`` 改为 ``(game_id, seq)`` 复合主键；场景库 v1 的
0005 增 ``scenarios`` 表。本模块提供**显式**升级入口（组合根启动仍走
``init_schema``/``create_all``，不自动迁移）：

- 已是 head（``alembic_version=0005``）→ no-op；
- 已 stamp 0001（M3 schema）→ 直接 ``alembic upgrade head``（备份文件后执行 0002/0003/0004/0005）；
- 无 ``alembic_version`` 的旧库：
  - 旧 M2 schema（``events`` 为 ``seq`` 单主键、``snapshots`` 缺 ``version``、无
    ``audit_events``）→ 备份文件 → ``ALTER TABLE snapshots ADD COLUMN version`` →
    按当前 metadata 建 ``audit_events`` → ``alembic stamp 0001`` → ``alembic upgrade head``；
  - M3 ``create_all`` schema（已有 ``version``/``audit_events`` 但事件仍是 ``seq`` 单主键）
    → 备份后补 ``stamp 0001`` 再 upgrade head；
  - 当前 ``create_all`` schema（事件已是复合主键、含 M6 人物卡表、H4 世界书两表与
    场景库 ``scenarios`` 表）→ 仅 ``stamp head``（不重建表）；缺世界书两表/场景表时分别
    补 stamp 0003/0004 再 upgrade，由 0004/0005 建缺失的表；
  - 空库/不存在的文件 → ``alembic upgrade head`` 初始化。
- 任何失败：恢复备份文件（含清理 ``-wal``/``-shm`` 残留）并抛
  :class:`LegacyUpgradeError`，消息含失败步骤与备份路径。

CLI::

    uv run python -m textgame.persistence.legacy_upgrade --db-url sqlite:///data/textgame.db
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, inspect, text

from .tables import Base

_HEAD_REVISION = "0006"
_BASELINE_REVISION = "0001"
_LEGACY_TABLES = frozenset({"events", "snapshots", "game_state_current", "idempotency"})
_COMPOSITE_EVENT_PK = ("game_id", "seq")
_BASELINE_EVENT_PK = ("seq",)
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _resolve_migration_paths() -> tuple[Path, Path]:
    """定位 Alembic 配置与迁移目录：源码 checkout 优先，其次 wheel 内嵌副本（R-05）。"""
    candidates = (
        Path(__file__).resolve().parents[3],  # 仓库根（源码运行/editable）
        Path(__file__).resolve().parents[1],  # 包内 ``textgame/alembic*``（wheel）
    )
    for root in candidates:
        ini = root / "alembic.ini"
        directory = root / "alembic"
        if ini.is_file() and directory.is_dir():
            return ini, directory
    return _REPO_ROOT / "alembic.ini", _REPO_ROOT / "alembic"


_ALEMBIC_INI, _ALEMBIC_DIR = _resolve_migration_paths()

UpgradeAction = Literal[
    "noop",  # 已是 head
    "upgraded_m3",  # 0001 → head（0002/0003/0004/0005）
    "upgraded_legacy",  # M2 旧库补齐后 stamp 0001 → head
    "stamped_head",  # create_all 建的 head 形状库，仅补 alembic_version（旧形状先 stamp 0003/0004）
    "initialized",  # 空库/新文件 upgrade head
]


class LegacyUpgradeError(RuntimeError):
    """旧库升级失败；``backup_path`` 非空表示失败前已写入备份，失败后已恢复。"""

    def __init__(self, message: str, *, backup_path: Path | None = None) -> None:
        """记录失败原因与（可选）备份路径。"""
        super().__init__(message)
        self.backup_path = backup_path


@dataclass(frozen=True)
class LegacyUpgradeReport:
    """一次旧库升级的结果；``action`` 区分 no-op / M3 / M2 旧库 / create_all / 空库。"""

    db_url: str
    action: UpgradeAction
    from_revision: str | None
    to_revision: str = _HEAD_REVISION
    backup_path: str | None = None
    steps: tuple[str, ...] = field(default_factory=tuple)

    @property
    def changed(self) -> bool:
        """是否实际改动了数据库（``noop`` 以外均为 True）。"""
        return self.action != "noop"

    def as_dict(self) -> dict[str, Any]:
        """JSON 友好字典（供 CLI 输出/测试断言）。"""
        return {
            "db_url": self.db_url,
            "action": self.action,
            "from_revision": self.from_revision,
            "to_revision": self.to_revision,
            "backup_path": self.backup_path,
            "changed": self.changed,
            "steps": list(self.steps),
        }


@dataclass(frozen=True)
class _SchemaState:
    """旧库结构探测结果（只读一次，避免升级过程中反复反射）。"""

    revision: str | None
    tables: frozenset[str]
    events_pk: tuple[str, ...]
    has_snapshots_version: bool
    has_audit_events: bool


# ---------------------------------------------------------------------------
# 结构探测 / 校验
# ---------------------------------------------------------------------------
def _last_revision(engine: Engine) -> str | None:
    """读 ``alembic_version.version_num``；无表返回 ``None``，多行/非字符串视为损坏。"""
    with engine.connect() as connection:
        rows = connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
    if not rows:
        return None
    if len(rows) > 1:
        raise LegacyUpgradeError("alembic_version 表存在多行版本号，数据库状态不明确")
    value = rows[0]
    if value is None or not isinstance(value, str):
        raise LegacyUpgradeError(f"alembic_version 值非法：{value!r}")
    return value


def _inspect_schema(engine: Engine) -> _SchemaState:
    """反射当前库的版本号/表集合/events 主键/关键列与表是否存在。"""
    inspector = inspect(engine)
    tables = frozenset(inspector.get_table_names())
    revision: str | None = None
    if "alembic_version" in tables:
        revision = _last_revision(engine)
        if revision is None:
            raise LegacyUpgradeError("alembic_version 表存在但为空，数据库状态不明确")
    events_pk: tuple[str, ...] = ()
    if "events" in tables:
        pk = inspector.get_pk_constraint("events").get("constrained_columns") or []
        events_pk = tuple(str(column) for column in pk)
    snapshot_columns = (
        {str(column["name"]) for column in inspector.get_columns("snapshots")}
        if "snapshots" in tables
        else set()
    )
    return _SchemaState(
        revision=revision,
        tables=tables,
        events_pk=events_pk,
        has_snapshots_version="version" in snapshot_columns,
        has_audit_events="audit_events" in tables,
    )


def _verify_head(engine: Engine) -> None:
    """升级后校验 head 结构（版本号 + 复合主键 + 复合唯一约束 + 索引 + 审计表）。"""
    state = _inspect_schema(engine)
    if state.revision != _HEAD_REVISION:
        raise LegacyUpgradeError(
            f"升级后 alembic_version={state.revision!r}，期望 {_HEAD_REVISION!r}"
        )
    if state.events_pk != _COMPOSITE_EVENT_PK:
        raise LegacyUpgradeError(f"升级后 events 主键={state.events_pk!r}，期望复合主键")
    if not state.has_snapshots_version:
        raise LegacyUpgradeError("升级后 snapshots 仍缺少 version 列")
    if not state.has_audit_events:
        raise LegacyUpgradeError("升级后仍缺少 audit_events 表")
    snapshot_unique = {
        tuple(str(column) for column in constraint["column_names"])
        for constraint in inspect(engine).get_unique_constraints("snapshots")
    }
    if ("game_id", "snapshot_version") not in snapshot_unique:
        raise LegacyUpgradeError("升级后 snapshots 缺少 (game_id, snapshot_version) 唯一约束")
    inspector = inspect(engine)
    missing_world_books = {"world_books", "world_book_entries"} - set(inspector.get_table_names())
    if missing_world_books:
        raise LegacyUpgradeError(f"升级后仍缺少 H4 世界书表：{sorted(missing_world_books)}")
    if "scenarios" not in inspector.get_table_names():
        raise LegacyUpgradeError("升级后仍缺少玩家自上传场景表 scenarios")
    unique_columns = {
        tuple(str(column) for column in constraint["column_names"])
        for constraint in inspector.get_unique_constraints("events")
    }
    if ("game_id", "event_uid") not in unique_columns:
        raise LegacyUpgradeError(
            f"升级后 events 缺少 (game_id, event_uid) 唯一约束：{sorted(unique_columns)!r}"
        )
    index_names = {str(index["name"]) for index in inspector.get_indexes("events")}
    if "ix_events_game_turn_seq" not in index_names:
        raise LegacyUpgradeError(f"升级后 events 缺少 ix_events_game_turn_seq 索引：{index_names}")


# ---------------------------------------------------------------------------
# 备份 / 恢复 / Alembic 配置
# ---------------------------------------------------------------------------
def _alembic_config(db_url: str) -> Config:
    """构造程序化 Alembic 配置；URL 走 ``config.attributes`` 优先于环境变量。"""
    if not _ALEMBIC_INI.exists() or not _ALEMBIC_DIR.exists():
        raise LegacyUpgradeError(
            f"找不到 Alembic 配置（{_ALEMBIC_INI} / {_ALEMBIC_DIR}），请在仓库根目录运行"
        )
    config = Config(str(_ALEMBIC_INI))
    config.set_main_option("script_location", str(_ALEMBIC_DIR))
    config.set_main_option("sqlalchemy.url", db_url)
    config.attributes["sqlalchemy.url"] = db_url
    return config


def _backup_sqlite(db_path: Path) -> Path:
    """把 SQLite 文件复制为同目录 ``*.bak-YYYYmmddHHMMSS``（重名时追加序号）。"""
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    backup = db_path.with_name(f"{db_path.name}.bak-{timestamp}")
    suffix = 1
    while backup.exists():
        backup = db_path.with_name(f"{db_path.name}.bak-{timestamp}-{suffix}")
        suffix += 1
    shutil.copy2(db_path, backup)
    return backup


def _restore_backup(backup: Path, db_path: Path) -> None:
    """用备份覆盖回数据库文件，并清掉失败过程中可能残留的 SQLite 边车文件。"""
    for sidecar in (f"{db_path}-wal", f"{db_path}-shm", f"{db_path}-journal"):
        sidecar_path = Path(sidecar)
        if sidecar_path.exists():
            sidecar_path.unlink()
    shutil.copy2(backup, db_path)


def _sqlite_path(db_url: str) -> Path:
    """从 sqlite URL 解析文件路径（拒绝内存库；相对路径按当前目录解析）。"""
    prefix = "sqlite:///"
    if not db_url.startswith(prefix):
        raise LegacyUpgradeError(f"仅支持 sqlite:/// 文件库，收到 {db_url!r}")
    raw_path = db_url[len(prefix) :]
    if raw_path in {"", ":memory:"}:
        raise LegacyUpgradeError("内存数据库无法备份/升级，请传入 sqlite 文件路径")
    return Path(raw_path)


# ---------------------------------------------------------------------------
# 升级步骤
# ---------------------------------------------------------------------------
def _apply_legacy_schema_fixes(engine: Engine) -> tuple[str, ...]:
    """给旧 M2 库补 ``snapshots.version`` 列与 ``audit_events`` 表（幂等）。"""
    steps: list[str] = []
    inspector = inspect(engine)
    snapshot_columns = {str(column["name"]) for column in inspector.get_columns("snapshots")}
    if "version" not in snapshot_columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE snapshots ADD COLUMN version INTEGER NOT NULL DEFAULT 0")
            )
        steps.append("ALTER TABLE snapshots ADD COLUMN version")
    if "audit_events" not in inspect(engine).get_table_names():
        Base.metadata.create_all(bind=engine, tables=[Base.metadata.tables["audit_events"]])
        steps.append("CREATE TABLE audit_events")
    return tuple(steps)


def _verify_baseline_shape(state: _SchemaState) -> None:
    """旧库必须同时具备 M2 的 4 张表，否则不是可识别的 M2/M3 存档库。"""
    missing = _LEGACY_TABLES - state.tables
    if missing:
        raise LegacyUpgradeError(
            f"无法识别的旧库结构：缺少表 {sorted(missing)}；请确认这是 textgame 的 SQLite 存档库"
        )
    if state.events_pk != _BASELINE_EVENT_PK:
        raise LegacyUpgradeError(f"旧库 events 主键={state.events_pk!r}，期望 ('seq',)")


def _run_migrations(db_url: str, *, stamp: str | None, upgrade: bool) -> None:
    """执行（可选）stamp 与 upgrade head。"""
    config = _alembic_config(db_url)
    if stamp is not None:
        command.stamp(config, stamp)
    if upgrade:
        command.upgrade(config, "head")


def _upgrade_with_backup(
    db_url: str,
    db_path: Path,
    *,
    action: UpgradeAction,
    from_revision: str | None,
    legacy_fix: bool,
    stamp: str | None,
    steps: tuple[str, ...],
) -> LegacyUpgradeReport:
    """备份 → 升级（含旧库补齐）→ 校验 head；任何失败恢复备份并抛错。"""
    backup = _backup_sqlite(db_path)
    steps = (*steps, f"backup={backup.name}")
    engine: Engine | None = None
    try:
        if legacy_fix:
            engine = create_engine(db_url, connect_args={"check_same_thread": False})
            try:
                fix_steps = _apply_legacy_schema_fixes(engine)
            finally:
                engine.dispose()
                engine = None
            steps = (*steps, *fix_steps)
        _run_migrations(db_url, stamp=stamp, upgrade=True)
        engine = create_engine(db_url, connect_args={"check_same_thread": False})
        try:
            _verify_head(engine)
        finally:
            engine.dispose()
            engine = None
    except Exception as exc:  # 失败统一恢复备份（SQLite DDL 非事务，文件级回滚最可靠）
        if engine is not None:
            engine.dispose()
        _restore_backup(backup, db_path)
        raise LegacyUpgradeError(
            f"旧库升级失败（已恢复备份 {backup.name}）：{exc}", backup_path=backup
        ) from exc
    return LegacyUpgradeReport(
        db_url=db_url,
        action=action,
        from_revision=from_revision,
        backup_path=str(backup),
        steps=steps,
    )


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------
def upgrade_legacy_database(db_url: str) -> LegacyUpgradeReport:
    """把旧 SQLite 存档库升级到 Alembic head（0002 复合主键 + 0003 人物卡 + 0004 世界书两表
    + 0005 玩家自上传场景表 + 0006 快照唯一约束），返回升级报告。

    幂等：head 库直接 no-op；失败时恢复备份并抛 :class:`LegacyUpgradeError`。
    """
    db_path = _sqlite_path(db_url)
    if not db_path.exists():
        # 新文件：直接 upgrade head 初始化（Alembic 会创建空库文件）
        _run_migrations(db_url, stamp=None, upgrade=True)
        engine = create_engine(db_url, connect_args={"check_same_thread": False})
        try:
            _verify_head(engine)
        finally:
            engine.dispose()
        return LegacyUpgradeReport(
            db_url=db_url,
            action="initialized",
            from_revision=None,
            steps=("alembic upgrade head",),
        )

    # 先只读探测结构，随后立即释放连接：迁移/备份在无本进程句柄时执行（Windows 友好）。
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    try:
        state = _inspect_schema(engine)
    finally:
        engine.dispose()

    if state.revision == _HEAD_REVISION:
        return LegacyUpgradeReport(
            db_url=db_url,
            action="noop",
            from_revision=_HEAD_REVISION,
            steps=("already at head",),
        )
    if state.revision == _BASELINE_REVISION:
        return _upgrade_with_backup(
            db_url,
            db_path,
            action="upgraded_m3",
            from_revision=_BASELINE_REVISION,
            legacy_fix=False,
            stamp=None,
            steps=("alembic upgrade head",),
        )
    if state.revision is not None:
        raise LegacyUpgradeError(
            f"未知 Alembic 版本 {state.revision!r}（仅支持 0001–0006），请人工确认数据库来源"
        )
    if not state.tables:
        raise LegacyUpgradeError("SQLite 文件存在但没有任何表，无法判断是否为存档库")
    if state.events_pk == _COMPOSITE_EVENT_PK:
        # create_all 建出的 head 形状库（M4/M6/H4/场景库）：结构足够时仅补 alembic_version。
        if not (state.has_snapshots_version and state.has_audit_events):
            raise LegacyUpgradeError(
                "events 已是复合主键但缺少 snapshots.version 或 audit_events，结构不完整"
            )
        # 缺表时补 stamp 到对应版本，再由后续 revision 建表：
        # - 缺世界书两表 → 0003 形状，stamp 0003（0004 建世界书、0005 建场景表）；
        # - 缺场景表 → 0004 形状，stamp 0004（0005 建场景表）。
        stamp_revision: str | None = None
        if not {"world_books", "world_book_entries"} <= state.tables:
            stamp_revision = "0003"
        elif "scenarios" not in state.tables:
            stamp_revision = "0004"
        else:
            snapshot_unique = {
                tuple(str(column) for column in constraint["column_names"])
                for constraint in inspect(engine).get_unique_constraints("snapshots")
            }
            if ("game_id", "snapshot_version") not in snapshot_unique:
                # R-10：旧 create_all 库缺唯一约束 → 先 stamp 0005，再由 0006 重建表。
                stamp_revision = "0005"
        if stamp_revision is not None:
            return _upgrade_with_backup(
                db_url,
                db_path,
                action="stamped_head",
                from_revision=None,
                legacy_fix=False,
                stamp=stamp_revision,
                steps=(f"alembic stamp {stamp_revision}", "alembic upgrade head"),
            )
        config = _alembic_config(db_url)
        command.stamp(config, "head")
        engine = create_engine(db_url, connect_args={"check_same_thread": False})
        try:
            _verify_head(engine)
        finally:
            engine.dispose()
        return LegacyUpgradeReport(
            db_url=db_url,
            action="stamped_head",
            from_revision=None,
            steps=("alembic stamp head",),
        )
    _verify_baseline_shape(state)
    return _upgrade_with_backup(
        db_url,
        db_path,
        action="upgraded_legacy",
        from_revision=None,
        legacy_fix=True,
        stamp=_BASELINE_REVISION,
        steps=(
            "alembic stamp 0001"
            if state.has_snapshots_version and state.has_audit_events
            else "legacy schema fixes + alembic stamp 0001",
        ),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    """CLI 入口：``python -m textgame.persistence.legacy_upgrade --db-url ...``。"""
    parser = argparse.ArgumentParser(
        prog="python -m textgame.persistence.legacy_upgrade",
        description="把 textgame 的旧 SQLite 存档库升级到 Alembic head（0002 复合主键 + 0003 人物卡 + 0004 世界书表 + 0005 场景表 + 0006 快照唯一约束）",
    )
    parser.add_argument(
        "--db-url",
        required=True,
        help="SQLite 文件库 URL，例如 sqlite:///data/textgame.db（不支持内存库）",
    )
    args = parser.parse_args(argv)
    try:
        report = upgrade_legacy_database(args.db_url)
    except LegacyUpgradeError as exc:
        print(f"旧库升级失败：{exc}", file=sys.stderr)
        if exc.backup_path is not None:
            print(f"已恢复备份：{exc.backup_path}", file=sys.stderr)
        return 1
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    return 0


__all__ = [
    "LegacyUpgradeError",
    "LegacyUpgradeReport",
    "main",
    "upgrade_legacy_database",
]


if __name__ == "__main__":
    raise SystemExit(main())
