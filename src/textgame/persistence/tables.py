"""SQLAlchemy 2.0 ORM 表：events / snapshots / game_state_current / idempotency / audit_events。

M2 契约 §6、M3 契约 §3.5/D-M3-6/D-M3-7、M4 前 D-008-3 与第 14 章 14.3：SQLAlchemy 2.0
同步 ORM + SQLite，新库建表走 ``Base.metadata.create_all``；迁移走 Alembic
（``alembic/versions/0001_m3_baseline.py`` 空库 5 表，``0002_composite_event_key.py``
把 events 改为 ``(game_id, seq)`` 复合主键以支持同库多存档）。

``events.seq`` 是**每存档内**全局单调序号、由 ``EventLog`` 分配（**不使用数据库自增**），
``id = f"evt_{seq:06d}"`` 即领域 ``Event.id``——因此 ``event_uid`` 只在存档内唯一，
数据库约束必须是 ``(game_id, event_uid)``，禁止再做成全局唯一（D-008-3）。事件表只
INSERT，不 UPDATE/DELETE（第 14 章 14.6 写入口禁令）；M3 新增的 ``audit_events`` 同为
只增表（审计只 INSERT，不 UPDATE/DELETE）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """所有持久化表的声明基类；``init_schema`` 以它的 metadata 建表。"""


class EventRow(Base):
    """事件表：一行 = 一条盖章事件（``params``/``result`` 为 JSON 文本）。

    主键 ``(game_id, seq)``：``seq`` 只在一个存档内单调，同库多存档各自从 1 起
    （D-008-3）；``event_uid`` 唯一约束为 ``(game_id, event_uid)``，``(game_id, seq)``
    复合主键前缀同时覆盖按存档取事件/取最大 seq 的查询，``(game_id, turn_id, seq)``
    索引覆盖按回合回放/审计关联查询。
    """

    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("game_id", "event_uid", name="uq_events_game_event_uid"),
        Index("ix_events_game_turn_seq", "game_id", "turn_id", "seq"),
    )

    game_id: Mapped[str] = mapped_column(String(64), primary_key=True, nullable=False)
    # = f"evt_{seq:06d}"，即领域 Event.id；供 Resolution.applied_events 与审计引用
    event_uid: Mapped[str] = mapped_column(String(16), nullable=False)
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    turn_id: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    method: Mapped[str] = mapped_column(String(64), nullable=False)
    params: Mapped[str] = mapped_column(Text, nullable=False)
    result: Mapped[str] = mapped_column(Text, nullable=False)
    # 有符号 64 位：0 <= rng_seed <= 2**63-1（第 10 章 10.3，禁止重复掩码）
    rng_seed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SnapshotRow(Base):
    """派生快照表：``last_seq`` 为快照覆盖到的全局事件序号（第 14 章 14.4）。"""

    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    snapshot_version: Mapped[int] = mapped_column(Integer, nullable=False)
    turn_id: Mapped[int] = mapped_column(Integer, nullable=False)
    last_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state_json: Mapped[str] = mapped_column(Text, nullable=False)
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # M3 D-M3-6：快照对应的提交版本号；旧 M2 快照/未显式传值时为 0，读取端按
    # 「last_seq + 快照后 distinct turn 数」兜底（见 persistence.versioning）。
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GameStateCurrentRow(Base):
    """当前状态缓存行：CAS 版本号 + 快照/事件之上的最新投影（可随时重建）。"""

    __tablename__ = "game_state_current"

    game_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    state_json: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    last_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IdempotencyRow(Base):
    """幂等表：``store_key`` 由编排层拼装；``result_json`` 保存 TurnResult + 事件。"""

    __tablename__ = "idempotency"

    store_key: Mapped[str] = mapped_column(String(192), primary_key=True)
    params_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditEventRow(Base):
    """M3 审计只增表（契约 §3.5/D-M3-7）：每回合每提案一行，只 INSERT。

    ``audit_uid`` 为主键，由 ``(game_id, turn_id, attempt, proposal_id)`` 确定性生成，
    重复投递同一批审计行时按「已存在则不覆盖、不删除」处理（INSERT OR IGNORE）；
    ``args_json`` 存提案参数的规范 JSON 文本；``status`` 固定
    ``{committed, conflicted, aborted}``，与 ``verdict`` 分离（禁止混写）。
    """

    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_game_turn", "game_id", "turn_id"),)

    audit_uid: Mapped[str] = mapped_column(String(192), primary_key=True)
    game_id: Mapped[str] = mapped_column(String(64), nullable=False)
    turn_id: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    proposal_id: Mapped[str] = mapped_column(String(64), nullable=False)
    resolution_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tool: Mapped[str] = mapped_column(String(64), nullable=False)
    args_json: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    verdict: Mapped[str | None] = mapped_column(String(32), nullable=True)
    verdict_reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
