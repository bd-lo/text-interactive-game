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

from sqlalchemy import Engine, create_engine
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
