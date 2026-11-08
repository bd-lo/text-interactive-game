"""Alembic 迁移环境（M3 契约 §1 D-M3-6 / 任务 5 的最小 baseline）。

- 迁移目标 metadata 直接引用 ``textgame.persistence.tables.Base.metadata``，避免在迁移
  目录重复维护一份表定义；baseline 迁移（``alembic/versions/0001_m3_baseline.py``）用
  显式 ``op.create_table`` 冻结当前 5 张表结构。
- URL 解析优先级：``config.attributes["sqlalchemy.url"]``（测试/程序化）
  → ``ALEMBIC_DB_URL`` 环境变量 → ``alembic.ini`` 的 ``sqlalchemy.url``。
- 支持 ``config.attributes["connection"]`` 传入既有连接（测试复用同一内存库连接），
  也支持离线模式（``alembic upgrade head --sql``）只输出 SQL。
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, engine_from_config, pool

from textgame.persistence.tables import Base

config = context.config
if config.config_file_name is not None:
    # 不关闭既有 logger：测试进程内 alembic 配置不应禁用 pytest 采集的日志
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    """按约定优先级解析迁移数据库 URL（返回非空字符串）。"""
    override = config.attributes.get("sqlalchemy.url")
    if isinstance(override, str) and override:
        return override
    from_env = os.environ.get("ALEMBIC_DB_URL")
    if from_env:
        return from_env
    url = config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError("alembic sqlalchemy.url is not configured")
    return url


def run_migrations_offline() -> None:
    """离线模式：把迁移渲染为 SQL 文本（不连接数据库）。"""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_with_connection(connection: Connection) -> None:
    """在给定连接上执行迁移（连接生命周期由调用方管理，Alembic 负责事务）。"""
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：优先复用外部传入连接，否则按 URL 建临时 Engine（NullPool）。"""
    external = config.attributes.get("connection")
    if isinstance(external, Connection):
        _run_with_connection(external)
        return
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    engine = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            _run_with_connection(connection)
            connection.commit()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
