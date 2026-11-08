"""SQLAlchemy 2.0 ORM 表：events / snapshots / game_state_current / idempotency / audit_events。

M2 契约 §6、M3 契约 §3.5/D-M3-6/D-M3-7 与第 14 章 14.3：SQLAlchemy 2.0 同步 ORM +
SQLite，新库建表走 ``Base.metadata.create_all``；M3 起同时提供 Alembic 最小 baseline
（``alembic/versions/0001_m3_baseline.py``，仅用于新库/测试，旧 M2 库由迁移责任登记）。
SQLite 的 JSON 列统一用 ``TEXT`` 存规范 JSON 文本；``events.seq`` 是存档内全局单调
序号、由 ``EventLog`` 分配（**不使用数据库自增**），``event_uid`` 唯一且即领域
``Event.id``。事件表只 INSERT，不 UPDATE/DELETE（第 14 章 14.6 写入口禁令）；M3 新增的
``audit_events`` 同为只增表（审计只 INSERT，不 UPDATE/DELETE）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """所有持久化表的声明基类；``init_schema`` 以它的 metadata 建表。"""


class EventRow(Base):
    """事件表：一行 = 一条盖章事件（``params``/``result`` 为 JSON 文本）。"""

    __tablename__ = "events"

    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    # = f"evt_{seq:06d}"，即领域 Event.id；供 Resolution.applied_events 与审计引用
    event_uid: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    game_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    turn_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
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
    # 「last_seq + 快照后 distinct turn 数」兜底（见 persistence.versions）。
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
