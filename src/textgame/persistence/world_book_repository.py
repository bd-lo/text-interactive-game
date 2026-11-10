"""``SqliteWorldBookRepository``：世界书库的 SQLite 实现（H4-CONTRACT §1）。

职责边界：只做 ``world_books`` / ``world_book_entries`` 两表与幂等记录的读写，不承载格式
解析/触发语义/内容安全（那些在 ``domain.world_book`` 与 ``services.world_book_service``）。
世界书**不参与事件溯源**：本模块只碰这两张表与既有 ``idempotency`` 表，不读写
events/snapshots/current 行。

幂等复用**同一张** ``idempotency`` 表与同一 JSON 形状（``{"response": ..., "events": [...]}``，
见 ``SqliteRepository``），键由服务层拼装（``world_book:import|delete:<Idempotency-Key>``），
不新增第二套幂等语义。软删除：``delete_world_book`` 只置 ``deleted_at``，不物理删行；读路径
默认排除已删除世界书（``include_deleted=True`` 仅删除服务判幂等用）。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timezone

from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from textgame.domain.events import Event
from textgame.domain.hashing import canonical_json
from textgame.services.ports import IdempotencyRecord
from textgame.services.world_book_service import StoredWorldBook, StoredWorldBookEntry

from .tables import IdempotencyRow, WorldBookEntryRow, WorldBookRow


class SqliteWorldBookRepository:
    """``WorldBookRepository`` 的 SQLite 实现；每次调用独立 Session + 独立事务。"""

    def __init__(self, engine: Engine) -> None:
        """绑定 Engine；新库建表由 ``init_schema(engine)`` / Alembic 0004 完成。"""
        self._engine = engine
        self._session_factory: sessionmaker[Session] = sessionmaker(
            bind=engine, expire_on_commit=False
        )

    # ------------------------------------------------------------------ 列表/读取
    def list_world_books(
        self, *, query: str | None, limit: int, offset: int
    ) -> tuple[list[StoredWorldBook], int]:
        """按 ``updated_at`` 倒序返回未删除世界书窗口与过滤后总数。

        ``query`` 是对 ``name`` 的转义子串匹配（``autoescape=True``，用户输入不含通配符）；
        ``total`` 是过滤后、分页前的总数，供前端分页展示。
        """
        statement = select(WorldBookRow).where(WorldBookRow.deleted_at.is_(None))
        if query:
            statement = statement.where(WorldBookRow.name.contains(query, autoescape=True))
        with self._session_factory() as session:
            total = session.scalar(select(func.count()).select_from(statement.subquery()))
            rows = session.scalars(
                statement.order_by(
                    WorldBookRow.updated_at.desc(), WorldBookRow.world_book_id.desc()
                )
                .limit(limit)
                .offset(offset)
            ).all()
        return [self._stored_book(row) for row in rows], int(total or 0)

    def get_world_book(
        self, world_book_id: str, *, include_deleted: bool = False
    ) -> StoredWorldBook | None:
        """按 id 取世界书；非 ``include_deleted`` 时已软删记录视为不存在。"""
        with self._session_factory() as session:
            row = session.get(WorldBookRow, world_book_id)
        if row is None:
            return None
        if row.deleted_at is not None and not include_deleted:
            return None
        return self._stored_book(row)

    def get_entries(self, world_book_id: str) -> list[StoredWorldBookEntry]:
        """返回该书全部条目（按 ``entry_uid`` 升序；无条目返回空列表）。"""
        with self._session_factory() as session:
            rows = session.scalars(
                select(WorldBookEntryRow)
                .where(WorldBookEntryRow.world_book_id == world_book_id)
                .order_by(WorldBookEntryRow.entry_uid.asc())
            ).all()
        return [self._stored_entry(row) for row in rows]

    # ------------------------------------------------------------------ 写入
    def save_world_book(
        self, book: StoredWorldBook, entries: Sequence[StoredWorldBookEntry]
    ) -> None:
        """新增或覆盖一本书（同 id 覆盖；条目整批重写；``created_at`` 保留首次值）。"""
        raw_json = canonical_json(book.raw)
        rows = [self._entry_row(book.world_book_id, entry) for entry in entries]
        with self._session_factory() as session, session.begin():
            row = session.get(WorldBookRow, book.world_book_id)
            if row is None:
                session.add(
                    WorldBookRow(
                        world_book_id=book.world_book_id,
                        name=book.name,
                        description=book.description,
                        source=book.source,
                        entry_count=book.entry_count,
                        raw_json=raw_json,
                        created_at=book.created_at,
                        updated_at=book.updated_at,
                        deleted_at=None,
                    )
                )
            else:
                row.name = book.name
                row.description = book.description
                row.source = book.source
                row.entry_count = book.entry_count
                row.raw_json = raw_json
                row.updated_at = book.updated_at
                row.deleted_at = None
            session.execute(
                delete(WorldBookEntryRow).where(
                    WorldBookEntryRow.world_book_id == book.world_book_id
                )
            )
            if rows:
                session.add_all(rows)

    def delete_world_book(self, world_book_id: str, *, deleted_at: datetime) -> bool:
        """软删除（置 ``deleted_at``）；世界书不存在或已删除返回 ``False``（零写入）。"""
        with self._session_factory() as session, session.begin():
            row = session.get(WorldBookRow, world_book_id)
            if row is None or row.deleted_at is not None:
                return False
            row.deleted_at = deleted_at
            row.updated_at = deleted_at
        return True

    # ------------------------------------------------------------------ 幂等
    def get_idempotent(self, key: str) -> IdempotencyRecord | None:
        """按存储键取幂等记录（与 ``SqliteRepository`` 同表同形状）。"""
        with self._session_factory() as session:
            row = session.get(IdempotencyRow, key)
        if row is None:
            return None
        payload = json.loads(row.result_json)
        events = tuple(Event.model_validate(item) for item in payload.get("events", []))
        response = payload.get("response")
        if not isinstance(response, dict):
            return None
        return IdempotencyRecord(
            key=row.store_key,
            params_hash=row.params_hash,
            payload=response,
            events=events,
        )

    def save_idempotent(self, record: IdempotencyRecord) -> None:
        """保存幂等记录；同键已存在时覆盖为最新（同键异参判定由服务层完成）。"""
        payload = {
            "response": record.payload,
            "events": [event.model_dump(mode="json") for event in record.events],
        }
        result_json = canonical_json(payload)
        now = datetime.now(timezone.utc)
        with self._session_factory() as session, session.begin():
            row = session.get(IdempotencyRow, record.key)
            if row is None:
                session.add(
                    IdempotencyRow(
                        store_key=record.key,
                        params_hash=record.params_hash,
                        result_json=result_json,
                        created_at=now,
                    )
                )
            else:
                row.params_hash = record.params_hash
                row.result_json = result_json
                row.created_at = now

    # ------------------------------------------------------------------ 行映射
    @staticmethod
    def _entry_row(world_book_id: str, entry: StoredWorldBookEntry) -> WorldBookEntryRow:
        """服务层条目 → ORM 行（JSON 列用 ``canonical_json`` 序列化）。"""
        return WorldBookEntryRow(
            world_book_id=world_book_id,
            entry_uid=entry.entry_uid,
            keys_json=canonical_json(list(entry.keys)),
            secondary_keys_json=canonical_json(list(entry.secondary_keys)),
            content=entry.content,
            enabled=entry.enabled,
            constant=entry.constant,
            selective=entry.selective,
            selective_logic=entry.selective_logic,
            order=entry.order,
            position=entry.position,
            depth=entry.depth,
            case_sensitive=entry.case_sensitive,
            match_whole_words=entry.match_whole_words,
            exclude_recursion=entry.exclude_recursion,
            prevent_recursion=entry.prevent_recursion,
            raw_json=canonical_json(entry.raw or {}),
        )

    @classmethod
    def _stored_book(cls, row: WorldBookRow) -> StoredWorldBook:
        """ORM 行 → ``StoredWorldBook``（``raw_json`` 反序列化为 dict）。"""
        return StoredWorldBook(
            world_book_id=row.world_book_id,
            name=row.name,
            description=row.description,
            source=row.source,
            entry_count=row.entry_count,
            raw=cls._load_json_object(row.raw_json),
            created_at=cls._as_utc(row.created_at),
            updated_at=cls._as_utc(row.updated_at),
            deleted_at=None if row.deleted_at is None else cls._as_utc(row.deleted_at),
        )

    @classmethod
    def _stored_entry(cls, row: WorldBookEntryRow) -> StoredWorldBookEntry:
        """ORM 行 → ``StoredWorldBookEntry``（JSON 数组列反序列化为 tuple）。"""
        return StoredWorldBookEntry(
            entry_uid=row.entry_uid,
            keys=cls._load_str_tuple(row.keys_json),
            secondary_keys=cls._load_str_tuple(row.secondary_keys_json),
            content=row.content,
            enabled=row.enabled,
            constant=row.constant,
            selective=row.selective,
            selective_logic=row.selective_logic,
            order=row.order,
            position=row.position,
            depth=row.depth,
            case_sensitive=row.case_sensitive,
            match_whole_words=row.match_whole_words,
            exclude_recursion=row.exclude_recursion,
            prevent_recursion=row.prevent_recursion,
            raw=cls._load_json_object(row.raw_json),
        )

    @staticmethod
    def _load_json_object(value: str) -> dict[str, object]:
        """``raw_json`` 文本 → dict；损坏/非对象返回空 dict（诊断数据不应阻塞读取）。"""
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _load_str_tuple(value: str) -> tuple[str, ...]:
        """``keys_json`` 文本 → 字符串 tuple；非数组/非字符串项静默跳过。"""
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            return ()
        if not isinstance(payload, list):
            return ()
        return tuple(item for item in payload if isinstance(item, str))

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        """SQLite 的 ``DATETIME`` 不保留时区；落库均为 UTC，读出时补 UTC 时区。"""
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


__all__ = ["SqliteWorldBookRepository"]
