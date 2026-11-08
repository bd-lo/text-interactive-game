"""M3 baseline：空库建全部表（契约 §3.5/D-M3-6/D-M3-7、任务 5）。

冻结的迁移目标（与 ``textgame.persistence.tables`` 当前 ORM 定义一致）：

- ``events``（只 INSERT；``seq`` 单主键，M3 不改为多存档复合主键，见 D-008-3 登记）
- ``snapshots``（M3 增 ``version`` 列：快照对应的提交版本号）
- ``game_state_current``（CAS 版本缓存）
- ``idempotency``（同键同参回放）
- ``audit_events``（M3 只增审计表，索引 ``(game_id, turn_id)``）

说明：本 revision 仅面向**空库**；M2 旧库已由 ``Base.metadata.create_all`` 建表且无
``alembic_version``，不在 baseline upgrade 范围内（旧库迁移/重建策略由 Lead 登记）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建 M3 全部 5 张表与索引/唯一约束。"""
    op.create_table(
        "events",
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
    )
    op.create_index("ix_events_game_id", "events", ["game_id"])
    op.create_index("ix_events_turn_id", "events", ["turn_id"])

    op.create_table(
        "snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("game_id", sa.String(length=64), nullable=False),
        sa.Column("snapshot_version", sa.Integer(), nullable=False),
        sa.Column("turn_id", sa.Integer(), nullable=False),
        sa.Column("last_seq", sa.BigInteger(), nullable=False),
        sa.Column("state_json", sa.Text(), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_snapshots_game_id", "snapshots", ["game_id"])

    op.create_table(
        "game_state_current",
        sa.Column("game_id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("state_json", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("last_seq", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "idempotency",
        sa.Column("store_key", sa.String(length=192), primary_key=True, nullable=False),
        sa.Column("params_hash", sa.String(length=64), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "audit_events",
        sa.Column("audit_uid", sa.String(length=192), primary_key=True, nullable=False),
        sa.Column("game_id", sa.String(length=64), nullable=False),
        sa.Column("turn_id", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("proposal_id", sa.String(length=64), nullable=False),
        sa.Column("resolution_id", sa.String(length=64), nullable=True),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("tool", sa.String(length=64), nullable=False),
        sa.Column("args_json", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("verdict", sa.String(length=32), nullable=True),
        sa.Column("verdict_reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_events_game_turn", "audit_events", ["game_id", "turn_id"])


def downgrade() -> None:
    """回滚：按依赖无关顺序删除全部表（M3 baseline 反操作，仅测试/开发使用）。"""
    op.drop_index("ix_audit_events_game_turn", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_table("idempotency")
    op.drop_table("game_state_current")
    op.drop_index("ix_snapshots_game_id", table_name="snapshots")
    op.drop_table("snapshots")
    op.drop_index("ix_events_turn_id", table_name="events")
    op.drop_index("ix_events_game_id", table_name="events")
    op.drop_table("events")
