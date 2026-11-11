"""R-10：快照表增加 ``(game_id, snapshot_version)`` 唯一约束。

背景：``SnapshotRow`` 只有自增主键；并发提交可能算出同一个 ``snapshot_version`` 并插入
两行，读取端按版本/id 取一条，重建时可能拿到与事件流不一致的快照。本 revision 先删除
历史重复行（保留 id 最大的一行），再用 SQLite batch 模式重建表以加唯一约束。

``downgrade`` 用同样的 batch 模式移除约束，可逆。
"""

from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

_UNIQUE_NAME = "uq_snapshots_game_version"


def upgrade() -> None:
    """去重后加 ``(game_id, snapshot_version)`` 唯一约束（R-10）。"""
    op.execute(
        "DELETE FROM snapshots WHERE id NOT IN ("
        "SELECT MAX(id) FROM snapshots GROUP BY game_id, snapshot_version)"
    )
    with op.batch_alter_table("snapshots", recreate="always") as batch_op:
        batch_op.create_unique_constraint(_UNIQUE_NAME, ["game_id", "snapshot_version"])


def downgrade() -> None:
    """移除快照唯一约束（保留数据）。"""
    with op.batch_alter_table("snapshots", recreate="always") as batch_op:
        batch_op.drop_constraint(_UNIQUE_NAME, type_="unique")
