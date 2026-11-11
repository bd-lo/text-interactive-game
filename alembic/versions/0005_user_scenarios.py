"""玩家自上传场景（Scenario Module v1）：新增 ``scenarios`` 表（设计契约 §3.3）。

背景：P0「玩家自己上传一份场景 JSON 就能开新局」需要一个**独立于事件溯源**的场景库
（场景不产生 ``Event``、不进入 ``GameState``；只有建局时把定义物化为初始状态）。
本 revision 只做加法：建 ``scenarios`` 表 + ``created_at`` 索引；不改既有 8 张表、
不迁移数据（场景定义原文存在 ``definition_json``，导入时即校验）。
``downgrade`` 删索引与表，可逆。

列口径（与 ``textgame.persistence.tables.ScenarioRow`` 逐列一致）：

- ``scenario_id``（服务端生成 ``scn_`` + 26 位 ULID）主键；
- ``slug``/``title``/``author``/``description`` 冗余展示列（slug 不唯一，同 slug 可重复导入）；
- ``schema_version`` 定义版本（本期恒为 1）；
- ``definition_json`` 原始定义 JSON（规范文本，回放/导出/``reload`` 的唯一来源）；
- ``summary_json`` 统计（rooms/items/npcs/objectives/endings/suggestions 六个计数）；
- ``warnings_json`` 非阻断告警 ``[{path,message}]``；
- ``created_at`` 导入时间（列表按它倒序）；``deleted_at`` 软删除时间戳（``NULL`` = 未删除，
  删除不物理删行，已开局存档的 runtime 索引仍需保留）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建 ``scenarios`` 表与 ``created_at`` 索引（纯加法）。"""
    op.create_table(
        "scenarios",
        sa.Column("scenario_id", sa.String(length=32), primary_key=True, nullable=False),
        sa.Column("slug", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=48), nullable=False),
        sa.Column("author", sa.String(length=32), nullable=False),
        sa.Column("description", sa.String(length=200), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("definition_json", sa.Text(), nullable=False),
        sa.Column("summary_json", sa.Text(), nullable=False),
        sa.Column("warnings_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_scenarios_created_at", "scenarios", ["created_at"])


def downgrade() -> None:
    """回滚：删 ``scenarios`` 表及其索引；不触碰其余 8 张表。"""
    op.drop_index("ix_scenarios_created_at", table_name="scenarios")
    op.drop_table("scenarios")
