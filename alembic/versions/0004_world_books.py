"""H4 世界书：新增 ``world_books`` / ``world_book_entries`` 两表（H4-CONTRACT §1）。

背景：H4「世界书 + 酒馆格式兼容」MVP 需要一张**独立于事件溯源**的资料库（世界书不产生
``Event``、不进入 ``GameState``；只有 ``POST /games`` 把 ``world_book_id`` 记进
``game_created.params``，对局内每回合由纯函数激活条目）。本 revision 只做加法：
建两表 + 索引 + ``(world_book_id, entry_uid)`` 唯一约束；不改既有 6 张表、不迁移数据。
``downgrade`` 按依赖顺序删除两表与其索引/约束，可逆。

列口径（与 ``textgame.persistence.tables.WorldBookRow``/``WorldBookEntryRow`` 逐列一致）：

- ``world_books``：``world_book_id``（服务端生成 ``wb_`` + 30 位 [a-z0-9]）主键；
  ``name``/``description``/``source`` 冗余展示列；``entry_count`` 规范化后条目数；
  ``raw_json`` 原始导入 payload 规范 JSON；``created_at``/``updated_at``/``deleted_at``。
- ``world_book_entries``：自增 ``id`` 主键；``world_book_id`` 索引 + ``entry_uid`` 唯一；
  ``keys_json``/``secondary_keys_json`` 规范化 JSON 数组；触发语义字段（enabled/constant/
  selective/selective_logic/order/position/depth/大小写/整词/递归）+ ``raw_json``。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建 ``world_books``/``world_book_entries`` 两表、索引与唯一约束（纯加法）。"""
    op.create_table(
        "world_books",
        sa.Column("world_book_id", sa.String(length=32), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("entry_count", sa.Integer(), nullable=False),
        sa.Column("raw_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_world_books_updated_at", "world_books", ["updated_at"])

    op.create_table(
        "world_book_entries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("world_book_id", sa.String(length=32), nullable=False),
        sa.Column("entry_uid", sa.Integer(), nullable=False),
        sa.Column("keys_json", sa.Text(), nullable=False),
        sa.Column("secondary_keys_json", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("constant", sa.Boolean(), nullable=False),
        sa.Column("selective", sa.Boolean(), nullable=False),
        sa.Column("selective_logic", sa.Integer(), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("case_sensitive", sa.Boolean(), nullable=False),
        sa.Column("match_whole_words", sa.Boolean(), nullable=False),
        sa.Column("exclude_recursion", sa.Boolean(), nullable=False),
        sa.Column("prevent_recursion", sa.Boolean(), nullable=False),
        sa.Column("raw_json", sa.Text(), nullable=False),
        sa.UniqueConstraint("world_book_id", "entry_uid", name="uq_world_book_entries_book_uid"),
    )
    op.create_index("ix_world_book_entries_world_book_id", "world_book_entries", ["world_book_id"])


def downgrade() -> None:
    """回滚：先删条目表（含索引/唯一约束）再删世界书表；不触碰其余 6 张表。"""
    op.drop_index("ix_world_book_entries_world_book_id", table_name="world_book_entries")
    op.drop_table("world_book_entries")
    op.drop_index("ix_world_books_updated_at", table_name="world_books")
    op.drop_table("world_books")
