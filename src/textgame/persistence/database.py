"""SQLite engine / session 工厂（M2 契约 §6、第 16 章 16.1 的 ``persistence/`` 落点）。

组合根（``main.py``）与测试都通过本文件建库：``prepare_db`` 为文件库创建父目录、
``create_engine_and_session`` 返回 Engine + 会话工厂，``init_schema`` 用
``Base.metadata.create_all`` 建表（新库路径，M4 起 events 为 ``(game_id, seq)`` 复合主键；
Alembic 迁移见 ``alembic/``，旧库升级见 ``persistence.legacy_upgrade``，均不在本函数内
自动执行）。

SQLite 约定：``check_same_thread=False`` 允许会话跨线程使用；内存库用
``StaticPool`` 复用同一连接，否则每个连接看到的是各自的空库。M2 只支持 SQLite
（第 19 章 19.2、M2 契约 §6）：非 sqlite URL 由 ``prepare_db`` 直接拒绝。
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .tables import Base


def _is_memory_sqlite(db_url: str) -> bool:
    """判断是否为 SQLite 内存库 URL（``sqlite://`` 或 ``sqlite:///:memory:``）。"""
    if not db_url.startswith("sqlite"):
        return False
    return ":memory:" in db_url or db_url.rstrip("/") == "sqlite:"


def prepare_db(db_url: str = "sqlite:///:memory:") -> tuple[Engine, sessionmaker[Session]]:
    """准备 SQLite 数据库并返回 ``(Engine, sessionmaker)``。

    对 ``sqlite:///相对或绝对路径`` 的 URL 先创建父目录（默认配置
    ``sqlite:///data/textgame.db`` 因此能自动建出仓库 gitignore 的 ``data/``），
    内存库不落盘；M2 只支持 SQLite，其它后端直接 ``NotImplementedError``。
    建表仍由调用方显式 ``init_schema(engine)`` 完成，便于组合根控制时序。
    """
    url = make_url(db_url)
    if url.get_backend_name() != "sqlite":
        raise NotImplementedError(f"M2 only supports sqlite databases, got {db_url!r}")
    database = url.database
    if database and database != ":memory:":
        Path(database).parent.mkdir(parents=True, exist_ok=True)
    return create_engine_and_session(db_url)


def create_engine_and_session(
    db_url: str = "sqlite:///:memory:",
) -> tuple[Engine, sessionmaker[Session]]:
    """创建 Engine 与会话工厂；SQLite 内存库共享同一连接（StaticPool）。"""
    if _is_memory_sqlite(db_url):
        engine = create_engine(
            db_url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    elif db_url.startswith("sqlite"):
        engine = create_engine(db_url, connect_args={"check_same_thread": False})
    else:
        engine = create_engine(db_url)
    factory: sessionmaker[Session] = sessionmaker(bind=engine, expire_on_commit=False)
    return engine, factory


def init_schema(engine: Engine) -> None:
    """按 ``Base.metadata`` 建全部表；已存在时幂等（不执行迁移，旧库用升级脚本）。"""
    Base.metadata.create_all(engine)


_COMPOSITE_EVENT_PK = ("game_id", "seq")


def ensure_schema(engine: Engine, *, db_url: str | None = None) -> None:
    """启动期 schema 守卫（R-05）：自动补齐旧库结构，避免升级用户建局/读视图 500。

    流程：
    1. 内存库/空文件库 → ``init_schema``（新库路径，不跑 Alembic）；
    2. 先 ``init_schema`` 建缺失的新表（create_all 不会补列，但能补表）；
    3. 检测旧结构：``snapshots.version`` 缺失、``events`` 非 ``(game_id, seq)`` 复合主键、
       或 ``alembic_version`` 落后于 head → 释放连接后调用 ``upgrade_legacy_database``；
    4. 健康库不跑 Alembic（避免每次启动 stamp）；升级失败抛 ``RuntimeError`` 并给出命令。

    发行包（wheel）需要同时带上 ``alembic/`` 与 ``alembic.ini``（见 pyproject force-include），
    否则只有源码 checkout 能自动升级；缺失时 fail-fast 提示手动升级命令。
    """
    resolved_url = db_url or str(engine.url)
    if _is_memory_sqlite(resolved_url):
        init_schema(engine)
        return
    existing = set(inspect(engine).get_table_names())
    if not existing:
        init_schema(engine)
        return
    from .legacy_upgrade import _HEAD_REVISION, LegacyUpgradeError, upgrade_legacy_database

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    snapshot_columns = (
        {str(column["name"]) for column in inspector.get_columns("snapshots")}
        if "snapshots" in tables
        else set()
    )
    events_pk = (
        tuple(
            str(column)
            for column in inspector.get_pk_constraint("events").get("constrained_columns") or []
        )
        if "events" in tables
        else ()
    )
    revision: str | None = None
    if "alembic_version" in tables:
        with engine.connect() as connection:
            rows = (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
            )
        revision = str(rows[0]) if rows else None
    needs_upgrade = (
        ("snapshots" in tables and "version" not in snapshot_columns)
        or (bool(events_pk) and events_pk != _COMPOSITE_EVENT_PK)
        or (revision is not None and revision != _HEAD_REVISION)
        or (
            "snapshots" in tables
            and ("game_id", "snapshot_version")
            not in {
                tuple(str(column) for column in constraint["column_names"])
                for constraint in inspector.get_unique_constraints("snapshots")
            }
        )
    )
    if not needs_upgrade:
        # 健康库：只补 create_all 能建的新表（不会补列；旧列形状上面已判为 needs_upgrade）。
        init_schema(engine)
        return
    # 旧库必须先迁移再 create_all：Alembic 0003–0005 的 op.create_table 不能容忍表已存在。
    engine.dispose()
    try:
        upgrade_legacy_database(resolved_url)
    except LegacyUpgradeError as exc:
        raise RuntimeError(
            "旧版存档库需要升级但自动迁移失败："
            f"{exc}。请先运行 `python -m textgame.persistence.legacy_upgrade "
            f"--db-url {resolved_url}` 后重试。"
        ) from exc
