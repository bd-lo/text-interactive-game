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

import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .tables import Base

#: 启动期自动升级的跨进程文件锁后缀：由 DB 文件路径派生（如 ``data/textgame.db.migrate.lock``）。
MIGRATION_LOCK_SUFFIX = ".migrate.lock"

#: 等待另一个进程完成升级的最长时间（秒）；超时 fail-fast 提示手动升级。
MIGRATION_LOCK_TIMEOUT_S = 10.0

#: 锁轮询间隔（秒）。
_MIGRATION_LOCK_POLL_S = 0.05


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


def migration_lock_path(db_url: str) -> Path | None:
    """由 SQLite 文件库 URL 派生迁移锁文件路径（``<db 文件名>.migrate.lock``）。

    内存库/非 SQLite 返回 ``None``：内存库天然单进程；非 SQLite 已由 ``prepare_db`` 拒绝。
    """
    try:
        url = make_url(db_url)
    except Exception:
        # 非法 URL 交给建库/升级路径报错，锁这里不抢戏（返回 None = 不加锁）。
        return None
    if url.get_backend_name() != "sqlite":
        return None
    database = url.database
    if not database or database == ":memory:":
        return None
    return Path(database + MIGRATION_LOCK_SUFFIX)


def _lock_windows(handle: BinaryIO) -> None:
    """Windows 字节锁：从文件位置 0 非阻塞锁住 1 字节（``msvcrt.locking``）。"""
    if sys.platform == "win32":
        import msvcrt

        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)


def _unlock_windows(handle: BinaryIO) -> None:
    """释放 Windows 字节锁（必须与加锁的字节区间/句柄一致）。"""
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def _lock_posix(handle: BinaryIO) -> None:
    """POSIX ``fcntl.flock`` 排它锁（非阻塞；Windows 上不执行）。"""
    if sys.platform != "win32":
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_posix(handle: BinaryIO) -> None:
    """释放 POSIX ``flock``（Windows 上不执行）。"""
    if sys.platform != "win32":
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class _MigrationFileLock:
    """跨进程文件锁（启动期自动迁移互斥）：Windows ``msvcrt`` / POSIX ``flock``。

    锁文件不主动删除（删除会与其它等待者形成竞态）；内容只有 1 个占位字节。拿不到锁时
    按 ``_MIGRATION_LOCK_POLL_S`` 重试到 ``timeout_s``，超时抛 ``RuntimeError``（fail-fast，
    提示关闭其它实例或手动运行升级脚本）。
    """

    def __init__(self, path: Path, *, timeout_s: float = MIGRATION_LOCK_TIMEOUT_S) -> None:
        """保存锁文件路径与等待上限。"""
        self.path = path
        self.timeout_s = timeout_s
        self._handle: BinaryIO | None = None

    def acquire(self) -> None:
        """获取排它锁；超时/系统错误 → ``RuntimeError``（消息含手动升级提示）。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_s
        last_error: OSError | None = None
        while True:
            handle: BinaryIO | None = None
            try:
                handle = self.path.open("a+b")
                if os.name == "nt":
                    _lock_windows(handle)
                else:
                    _lock_posix(handle)
                self._handle = handle
                return
            except OSError as exc:
                last_error = exc
                if handle is not None:
                    handle.close()
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        f"等待存档库自动迁移锁超时（{self.path}）：可能另一个实例正在升级或"
                        "运行。请关闭其它实例后重试，或先手动升级："
                        "`python -m textgame.persistence.legacy_upgrade --db-url <数据库URL>`。"
                    ) from last_error
                time.sleep(_MIGRATION_LOCK_POLL_S)

    def release(self) -> None:
        """释放锁并关闭句柄（幂等：未持有时 no-op）。"""
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            if os.name == "nt":
                _unlock_windows(handle)
            else:
                _unlock_posix(handle)
        finally:
            handle.close()


@contextmanager
def migration_lock(db_url: str, *, timeout_s: float = MIGRATION_LOCK_TIMEOUT_S) -> Iterator[None]:
    """持有启动期自动升级的跨进程锁；内存库/非 SQLite 为 no-op。

    用法：自动升级前 ``with migration_lock(db_url): ``（拿到锁后应重新检测一次 schema，
    另一进程可能已升级完成，避免重复迁移/重复备份）。
    """
    path = migration_lock_path(db_url)
    if path is None:
        yield
        return
    lock = _MigrationFileLock(path, timeout_s=timeout_s)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


_COMPOSITE_EVENT_PK = ("game_id", "seq")


def _needs_upgrade(engine: Engine) -> bool:
    """检测旧结构：``snapshots.version`` 缺失、``events`` 非复合主键、Alembic 落后等。

    只读 inspector/``alembic_version``，不做任何写操作；供 ``ensure_schema`` 在加锁前后
    各判一次（加锁后重判可避免另一个实例已完成升级时的重复迁移/重复备份）。
    """
    from .legacy_upgrade import _HEAD_REVISION

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if not tables:
        return False
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
    return (
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


def ensure_schema(
    engine: Engine,
    *,
    db_url: str | None = None,
    lock_timeout_s: float = MIGRATION_LOCK_TIMEOUT_S,
) -> None:
    """启动期 schema 守卫（R-05）：自动补齐旧库结构，避免升级用户建局/读视图 500。

    流程：
    1. 内存库/空文件库 → ``init_schema``（新库路径，不跑 Alembic）；
    2. 先检测旧结构（``snapshots.version`` 缺失、``events`` 非 ``(game_id, seq)`` 复合主键、
       或 ``alembic_version`` 落后于 head）；健康库只 ``init_schema`` 补齐新表，不跑 Alembic；
    3. 需要升级时**先获取跨进程迁移锁**（见 :func:`migration_lock`），拿到锁后重新检测一次
       （另一实例可能已完成升级），再释放连接调用 ``upgrade_legacy_database``；
    4. 锁等待超时/升级失败抛 ``RuntimeError`` 并给出手动升级命令（fail-fast）。

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
    if not _needs_upgrade(engine):
        # 健康库：只补 create_all 能建的新表（不会补列；旧列形状上面已判为需要升级）。
        init_schema(engine)
        return
    from .legacy_upgrade import LegacyUpgradeError, upgrade_legacy_database

    with migration_lock(resolved_url, timeout_s=lock_timeout_s):
        # 加锁后重判：另一个进程可能已在我们等待期间完成升级（避免重复迁移/重复备份）。
        if not _needs_upgrade(engine):
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
