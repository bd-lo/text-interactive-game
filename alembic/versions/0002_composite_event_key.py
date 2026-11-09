"""M4 前决策 D-008-3：``events`` 改复合主键 ``(game_id, seq)``（同库多存档）。

背景：``Event.id = f"evt_{seq:06d}"`` 是**每存档**编号，M2/M3 的 ``events`` 用 ``seq``
单主键 + ``event_uid`` 全局唯一，导致同一 SQLite 库只能承载一个存档。本 revision 在
SQLite 上用 ``op.batch_alter_table(..., recreate="always")`` 重建 ``events``：

- 主键：``seq`` → ``(game_id, seq)``（同库不同存档的 ``seq`` 各自从 1 起）；
- 唯一约束：``event_uid`` 全局唯一 → ``(game_id, event_uid)`` 复合唯一；
- 索引：删除旧单列索引 ``ix_events_game_id`` / ``ix_events_turn_id``（前者由复合主键
  前缀覆盖），新增 ``ix_events_game_turn_seq (game_id, turn_id, seq)``；
- 全部既有行按列名原样保留（batch copy-and-move，不丢数据）。

实现要点：SQLite 不支持直接 ALTER 主键；batch ``recreate="always"`` 会「建临时表 →
``INSERT ... SELECT`` 复制数据 → 删旧表 → 改名」。旧 ``seq`` 单主键/列级唯一约束在
SQLite 里没有可引用的约束名，无法用 ``drop_constraint`` 改，因此这里把**目标表定义**
（``_head_events_table``）作为 ``copy_from`` 传给 batch：batch 以它为准建新表并复制
同名数据列，从而一次完成主键/唯一约束/索引替换。显式给出表定义也让迁移在
``--sql`` 离线模式可生成 SQL（无需反射）。

downgrade 同理用 ``_baseline_events_table`` 作为目标表定义恢复旧 ``seq`` 单主键 +
全局唯一 ``event_uid``；仅对**单存档数据**安全（多存档数据中不同存档的
``evt_000001`` 会在全局唯一约束上冲突，downgrade 会失败而不是静默丢数据）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def _baseline_events_table() -> sa.Table:
    """0001（M3 baseline）的 events 表定义：``seq`` 单主键 + ``event_uid`` 全局唯一。"""
    metadata = sa.MetaData()
    return sa.Table(
        "events",
        metadata,
        sa.Column("seq", sa.BigInteger(), primary_key=True, autoincrement=False, nullable=False),
        sa.Column("event_uid", sa.String(length=16), nullable=False, unique=True),
        sa.Column("game_id", sa.String(length=64), nullable=False),
        sa.Column("turn_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=True),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("method", sa.String(length=64), nullable=False),
        sa.Column("params", sa.Text(), nullable=False),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("rng_seed", sa.BigInteger(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Index("ix_events_game_id", "game_id"),
        sa.Index("ix_events_turn_id", "turn_id"),
    )


def _head_events_table() -> sa.Table:
    """head 的 events 表定义：``(game_id, seq)`` 复合主键（与 tables.py 一致）。"""
    metadata = sa.MetaData()
    return sa.Table(
        "events",
        metadata,
        sa.Column("game_id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("event_uid", sa.String(length=16), nullable=False),
        sa.Column("seq", sa.BigInteger(), primary_key=True, autoincrement=False, nullable=False),
        sa.Column("turn_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=True),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("method", sa.String(length=64), nullable=False),
        sa.Column("params", sa.Text(), nullable=False),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("rng_seed", sa.BigInteger(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("game_id", "event_uid", name="uq_events_game_event_uid"),
        sa.Index("ix_events_game_turn_seq", "game_id", "turn_id", "seq"),
    )


def upgrade() -> None:
    """SQLite batch 重建 events：复合主键 + 复合唯一约束 + 新索引，保留全部数据。"""
    with op.batch_alter_table(
        "events",
        recreate="always",
        copy_from=_head_events_table(),
    ):
        pass


def downgrade() -> None:
    """恢复旧 ``seq`` 单主键 + 全局唯一 ``event_uid``（仅单存档数据安全）。"""
    with op.batch_alter_table(
        "events",
        recreate="always",
        copy_from=_baseline_events_table(),
    ):
        pass
