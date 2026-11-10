"""M6 人物卡库：新增 ``character_cards`` 表（第 5 章 §5.7、第 6 章 §6.7、D-M6-5）。

背景：M6 后端前置的 character-cards 契约需要一张**独立于事件溯源**的资料库表（人物卡
不产生 ``Event``、不进入 ``GameState``；只有 ``POST /games`` 才把卡映射为新存档的初始
``Character``）。本 revision 只做加法：建 ``character_cards`` 表与 ``updated_at`` 索引，
不改既有 5 张表、不迁移任何数据；``downgrade`` 删除该表与索引，可逆。

列口径（与 ``textgame.persistence.tables.CharacterCardRow`` 逐列一致）：

- ``card_id``：服务端生成 ``card_<26 位 ULID>``，主键；
- ``card_json``：整张卡的规范 JSON（``CharacterCard.model_dump(mode="json")``）；
- ``name``/``source``/``generated_by``：冗余展示/筛选列；
- ``deleted_at``：软删除时间戳（``NULL`` = 未删除），删除不物理删行；
- ``created_at``/``updated_at``：UTC 时间戳。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建 ``character_cards`` 表与 ``updated_at`` 索引（纯加法，不动既有表）。"""
    op.create_table(
        "character_cards",
        sa.Column("card_id", sa.String(length=32), primary_key=True, nullable=False),
        sa.Column("schema_version", sa.String(length=8), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("card_json", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("generated_by", sa.String(length=120), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_character_cards_updated_at", "character_cards", ["updated_at"])


def downgrade() -> None:
    """回滚：删除索引与 ``character_cards`` 表（不触碰其余 5 张表）。"""
    op.drop_index("ix_character_cards_updated_at", table_name="character_cards")
    op.drop_table("character_cards")
