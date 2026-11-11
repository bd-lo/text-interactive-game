"""SQLAlchemy 2.0 ORM 表：events / snapshots / game_state_current / idempotency / audit_events
/ character_cards / world_books / world_book_entries / scenarios。

M2 契约 §6、M3 契约 §3.5/D-M3-6/D-M3-7、M4 前 D-008-3 与第 14 章 14.3：SQLAlchemy 2.0
同步 ORM + SQLite，新库建表走 ``Base.metadata.create_all``；迁移走 Alembic
（``alembic/versions/0001_m3_baseline.py`` 空库 5 表，``0002_composite_event_key.py``
把 events 改为 ``(game_id, seq)`` 复合主键以支持同库多存档，
``0003_character_cards.py`` 增 M6 人物卡库表 ``character_cards``，
``0004_world_books.py`` 增 H4 世界书两表 ``world_books``/``world_book_entries``，
``0005_user_scenarios.py`` 增玩家自上传场景表 ``scenarios``）。

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
    Boolean,
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
    """派生快照表：``last_seq`` 为快照覆盖到的全局事件序号（第 14 章 14.4）。

    R-10：``(game_id, snapshot_version)`` 唯一，防止并发提交插入同版本快照。
    """

    __tablename__ = "snapshots"
    __table_args__ = (
        UniqueConstraint("game_id", "snapshot_version", name="uq_snapshots_game_version"),
    )

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


class CharacterCardRow(Base):
    """M6 人物卡库表（第 5 章 §5.7、第 6 章 §6.7、D-M6-5）：**不参与事件溯源**。

    人物卡是独立于游戏世界状态的资料库：写入由 ``CardService`` 校验后落本表，只有创建
    存档（``POST /games``）才把卡映射为 ``Character`` 并交给唯一写入者 ``GameEngine``。
    ``card_json`` 存整张卡的规范 JSON（``CharacterCard.model_dump(mode="json")``）；
    ``name``/``source``/``generated_by`` 冗余为列供列表查询与展示，``deleted_at`` 为软删除
    时间戳（``NULL`` = 未删除），删除不物理删行，便于审计与幂等回放。
    """

    __tablename__ = "character_cards"
    __table_args__ = (Index("ix_character_cards_updated_at", "updated_at"),)

    card_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(8), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    card_json: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    generated_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorldBookRow(Base):
    """H4 世界书表：一份导入/手工创建的世界设定资料（**不参与事件溯源**）。

    世界书是独立于游戏世界状态的资料库：``POST /world-books/import`` 由
    ``WorldBookService`` 规范化后落本表，``POST /games`` 只把 ``world_book_id`` 记录进
    ``game_created.params``（事件基线字段不扩展）；对局内每回合由纯函数
    ``domain.world_book.activate_entries`` 决定激活条目，**不写事件、不改 GameState**。

    - ``entry_count``：规范化后条目数（enabled+disabled 全量，供列表展示）；
    - ``raw_json``：原始导入 payload 的规范 JSON（审计/回放/前向兼容）；
    - ``deleted_at``：软删除时间戳（``NULL`` = 未删除），删除不物理删行。
    """

    __tablename__ = "world_books"
    __table_args__ = (Index("ix_world_books_updated_at", "updated_at"),)

    world_book_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    entry_count: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorldBookEntryRow(Base):
    """H4 世界书条目表：一条触发条目（SillyTavern ``world_info.entries`` 的规范化投影）。

    字段语义见 ``services.world_book_service`` 与 ``domain.world_book.WorldBookEntry``：
    ``keys_json``/``secondary_keys_json`` 为规范化 JSON 字符串数组；``raw_json`` 保留原始
    entry（含未映射字段，前向兼容）。唯一约束 ``(world_book_id, entry_uid)``：同书内
    ``entry_uid``（来自格式 uid，缺失时取数组下标）不得重复。
    """

    __tablename__ = "world_book_entries"
    __table_args__ = (
        UniqueConstraint("world_book_id", "entry_uid", name="uq_world_book_entries_book_uid"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    world_book_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    entry_uid: Mapped[int] = mapped_column(Integer, nullable=False)
    keys_json: Mapped[str] = mapped_column(Text, nullable=False)
    secondary_keys_json: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    constant: Mapped[bool] = mapped_column(Boolean, nullable=False)
    selective: Mapped[bool] = mapped_column(Boolean, nullable=False)
    selective_logic: Mapped[int] = mapped_column(Integer, nullable=False)
    order: Mapped[int] = mapped_column(Integer, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    depth: Mapped[int] = mapped_column(Integer, nullable=False)
    case_sensitive: Mapped[bool] = mapped_column(Boolean, nullable=False)
    match_whole_words: Mapped[bool] = mapped_column(Boolean, nullable=False)
    exclude_recursion: Mapped[bool] = mapped_column(Boolean, nullable=False)
    prevent_recursion: Mapped[bool] = mapped_column(Boolean, nullable=False)
    raw_json: Mapped[str] = mapped_column(Text, nullable=False)


class ScenarioRow(Base):
    """玩家自上传场景表（``docs/design/user-scenario-modules.md`` §3.3）：**不参与事件溯源**。

    场景是独立于 ``GameState`` 的资料库：``POST /scenarios`` 由 ``ScenarioService``
    校验后把定义原文与统计/告警落本表；建局时才把定义物化为初始 ``GameState``
    （玩家槽位仍是全局 ``PLAYER_ID``，事件基线字段不扩展）。

    - ``definition_json``：原始定义 JSON 原文（规范 JSON）——回放/导出/``reload`` 的唯一来源；
    - ``summary_json``/``warnings_json``：导入时算好的统计（6 个计数）与告警
      ``[{path,message}]``，列表/详情直接读，避免每次重解析；
    - ``deleted_at``：软删除时间戳（``NULL`` = 未删除），删除不物理删行——已开局存档
      的 runtime 索引仍需保留（``list_for_runtime`` 含已删记录）。
    """

    __tablename__ = "scenarios"
    __table_args__ = (Index("ix_scenarios_created_at", "created_at"),)

    scenario_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    slug: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(48), nullable=False)
    author: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str] = mapped_column(String(200), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    definition_json: Mapped[str] = mapped_column(Text, nullable=False)
    summary_json: Mapped[str] = mapped_column(Text, nullable=False)
    warnings_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
