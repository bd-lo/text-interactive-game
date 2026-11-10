"""``SqliteCardRepository``：人物卡库的 SQLite 实现（第 5 章 §5.7、第 6 章 §6.7）。

职责边界：只做 ``character_cards`` 行与幂等记录的读写，不承载校验/规范化/内容安全等业务
（那些在 ``services.card_service.CardService``）。人物卡**不参与事件溯源**：本模块只碰
``character_cards`` 与既有 ``idempotency`` 表，不读写 events/snapshots/current 行。

幂等复用**同一张** ``idempotency`` 表与同一 JSON 形状（``{"response": ..., "events": [...]}``，
见 ``SqliteRepository``），键由服务层拼装（``card:import|save|delete:<Idempotency-Key>``），
不新增第二套幂等语义（第 6 章 §6.7）。

软删除：``delete_card`` 只置 ``deleted_at``，不物理删行；读路径一律排除已删除卡。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from textgame.domain.character_card import CharacterCard
from textgame.domain.events import Event
from textgame.domain.hashing import canonical_json
from textgame.services.card_service import StoredCard
from textgame.services.ports import IdempotencyRecord

from .tables import CharacterCardRow, IdempotencyRow


class SqliteCardRepository:
    """``CardRepository`` 的 SQLite 实现；每次调用使用独立 Session + 独立事务。"""

    def __init__(self, engine: Engine) -> None:
        """绑定 Engine；新库建表由 ``init_schema(engine)`` / Alembic 0003 完成。"""
        self._engine = engine
        self._session_factory: sessionmaker[Session] = sessionmaker(
            bind=engine, expire_on_commit=False
        )

    # ------------------------------------------------------------------ 列表/读取
    def list_cards(
        self, *, query: str | None, limit: int, offset: int
    ) -> tuple[list[StoredCard], int]:
        """按 ``updated_at`` 倒序返回未删除卡片窗口与过滤后的总数。

        ``query`` 是对 ``name`` 的转义子串匹配（``autoescape=True``，用户输入不含通配符）；
        ``total`` 是过滤后、分页前的总数，供前端分页展示。
        """
        statement = select(CharacterCardRow).where(CharacterCardRow.deleted_at.is_(None))
        if query:
            statement = statement.where(CharacterCardRow.name.contains(query, autoescape=True))
        with self._session_factory() as session:
            total = session.scalar(select(func.count()).select_from(statement.subquery()))
            rows = session.scalars(
                statement.order_by(
                    CharacterCardRow.updated_at.desc(), CharacterCardRow.card_id.desc()
                )
                .limit(limit)
                .offset(offset)
            ).all()
        return [self._stored(row) for row in rows], int(total or 0)

    def get_card(self, card_id: str) -> StoredCard | None:
        """按 ``card_id`` 取未删除卡片；不存在/已软删除返回 ``None``。"""
        with self._session_factory() as session:
            row = session.get(CharacterCardRow, card_id)
        if row is None or row.deleted_at is not None:
            return None
        return self._stored(row)

    # ------------------------------------------------------------------ 写入
    def save_card(self, card: StoredCard) -> None:
        """新增或覆盖一张卡（同 ``card_id`` 覆盖；``created_at`` 保留首次值）。"""
        card_json = canonical_json(card.card.model_dump(mode="json"))
        with self._session_factory() as session, session.begin():
            row = session.get(CharacterCardRow, card.card_id)
            if row is None:
                session.add(
                    CharacterCardRow(
                        card_id=card.card_id,
                        schema_version=card.card.schema_version,
                        name=card.card.name,
                        card_json=card_json,
                        source=card.card.source,
                        generated_by=card.card.generated_by,
                        created_at=card.created_at,
                        updated_at=card.updated_at,
                        deleted_at=None,
                    )
                )
            else:
                row.schema_version = card.card.schema_version
                row.name = card.card.name
                row.card_json = card_json
                row.source = card.card.source
                row.generated_by = card.card.generated_by
                row.updated_at = card.updated_at

    def delete_card(self, card_id: str, *, deleted_at: datetime) -> bool:
        """软删除（置 ``deleted_at``）；卡不存在或已删除返回 ``False``（零写入）。"""
        with self._session_factory() as session, session.begin():
            row = session.get(CharacterCardRow, card_id)
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
    def _stored(row: CharacterCardRow) -> StoredCard:
        """ORM 行 → ``StoredCard``（``card_json`` 反序列化为权威 ``CharacterCard``）。"""
        return StoredCard(
            card_id=row.card_id,
            card=CharacterCard.model_validate_json(row.card_json),
            created_at=SqliteCardRepository._as_utc(row.created_at),
            updated_at=SqliteCardRepository._as_utc(row.updated_at),
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        """SQLite 的 ``DATETIME`` 不保留时区；落库均为 UTC，读出时补 UTC 时区。"""
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


__all__ = ["SqliteCardRepository"]
