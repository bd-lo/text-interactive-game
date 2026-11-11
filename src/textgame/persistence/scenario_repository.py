"""``SqliteScenarioRepository``：玩家自上传场景库的 SQLite 实现（设计契约 §3.3）。

职责边界：只做 ``scenarios`` 表与幂等记录的读写，不承载校验/解析/建局（那些在
``domain.user_scenario`` 与 ``services.scenario_service``）。场景**不参与事件溯源**：
本模块只碰 ``scenarios`` 与既有 ``idempotency`` 表，不读写 events/snapshots/current 行。

幂等复用**同一张** ``idempotency`` 表与同一 JSON 形状（``{"response": ..., "events": [...]}``，
见 ``SqliteRepository``），键由服务层拼装（``scenario:import:<Idempotency-Key>``），
不新增第二套幂等语义。

软删除：``soft_delete`` 只置 ``deleted_at``，不物理删行；列表/详情默认排除已删除场景，
``list_for_runtime`` 则**包含已删除**（已开局存档仍需要 runtime 索引）。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import Engine, func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from textgame.domain.events import Event
from textgame.domain.hashing import canonical_json
from textgame.services.ports import IdempotencyRecord
from textgame.services.scenario_service import StoredScenario

from .tables import IdempotencyRow, ScenarioRow


class SqliteScenarioRepository:
    """``ScenarioRepository`` 的 SQLite 实现；每次调用独立 Session + 独立事务。"""

    def __init__(self, engine: Engine) -> None:
        """绑定 Engine；新库建表由 ``init_schema(engine)`` / Alembic 0005 完成。"""
        self._engine = engine
        self._session_factory: sessionmaker[Session] = sessionmaker(
            bind=engine, expire_on_commit=False
        )

    # ------------------------------------------------------------------ 列表/读取
    def list_scenarios(
        self, *, query: str | None, limit: int, offset: int
    ) -> tuple[list[StoredScenario], int]:
        """按 ``created_at`` 倒序返回未删除场景窗口与过滤后总数。

        ``query`` 是对 ``title``/``author``/``slug`` 的转义子串匹配（``autoescape=True``，
        用户输入不含通配符）；``total`` 是过滤后、分页前的总数，供前端分页展示。
        """
        statement = select(ScenarioRow).where(ScenarioRow.deleted_at.is_(None))
        if query:
            statement = statement.where(
                or_(
                    ScenarioRow.title.contains(query, autoescape=True),
                    ScenarioRow.author.contains(query, autoescape=True),
                    ScenarioRow.slug.contains(query, autoescape=True),
                )
            )
        with self._session_factory() as session:
            total = session.scalar(select(func.count()).select_from(statement.subquery()))
            rows = session.scalars(
                statement.order_by(ScenarioRow.created_at.desc(), ScenarioRow.scenario_id.desc())
                .limit(limit)
                .offset(offset)
            ).all()
        return [self._stored(row) for row in rows], int(total or 0)

    def get(self, scenario_id: str, *, include_deleted: bool = False) -> StoredScenario | None:
        """按 id 取场景；非 ``include_deleted`` 时已软删记录视为不存在。"""
        with self._session_factory() as session:
            row = session.get(ScenarioRow, scenario_id)
        if row is None:
            return None
        if row.deleted_at is not None and not include_deleted:
            return None
        return self._stored(row)

    def list_for_runtime(self) -> list[StoredScenario]:
        """返回**全部**场景（含已软删，按 ``created_at`` 升序；runtime 索引重建用）。"""
        with self._session_factory() as session:
            rows = session.scalars(
                select(ScenarioRow).order_by(
                    ScenarioRow.created_at.asc(), ScenarioRow.scenario_id.asc()
                )
            ).all()
        return [self._stored(row) for row in rows]

    # ------------------------------------------------------------------ 写入
    def insert(self, scenario: StoredScenario) -> None:
        """写入一条新场景（``scenario_id`` 由服务层生成；同 id 不覆盖，靠主键冲突暴露）。"""
        with self._session_factory() as session, session.begin():
            session.add(
                ScenarioRow(
                    scenario_id=scenario.scenario_id,
                    slug=scenario.slug,
                    title=scenario.title,
                    author=scenario.author,
                    description=scenario.description,
                    schema_version=scenario.schema_version,
                    definition_json=canonical_json(scenario.definition),
                    summary_json=canonical_json(dict(scenario.summary)),
                    warnings_json=canonical_json([dict(item) for item in scenario.warnings]),
                    created_at=scenario.created_at,
                    deleted_at=scenario.deleted_at,
                )
            )

    def soft_delete(self, scenario_id: str, *, deleted_at: datetime) -> bool:
        """软删除（置 ``deleted_at``）；场景不存在或已删除返回 ``False``（零写入）。"""
        with self._session_factory() as session, session.begin():
            row = session.get(ScenarioRow, scenario_id)
            if row is None or row.deleted_at is not None:
                return False
            row.deleted_at = deleted_at
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
    @classmethod
    def _stored(cls, row: ScenarioRow) -> StoredScenario:
        """ORM 行 → ``StoredScenario``（JSON 列反序列化为 dict/list）。"""
        return StoredScenario(
            scenario_id=row.scenario_id,
            slug=row.slug,
            title=row.title,
            author=row.author,
            description=row.description,
            schema_version=row.schema_version,
            definition=cls._load_json_object(row.definition_json),
            summary=cls._load_summary(row.summary_json),
            warnings=cls._load_warnings(row.warnings_json),
            created_at=cls._as_utc(row.created_at),
            deleted_at=None if row.deleted_at is None else cls._as_utc(row.deleted_at),
        )

    @staticmethod
    def _load_json_object(value: str) -> dict[str, object]:
        """``definition_json`` 文本 → dict；损坏/非对象返回空 dict（定义损坏时服务层降级）。"""
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _load_summary(value: str) -> dict[str, int]:
        """``summary_json`` 文本 → ``{key: int}``（非整数项跳过；缺失键由服务层补 0）。"""
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            return {}
        if not isinstance(payload, dict):
            return {}
        summary: dict[str, int] = {}
        for key, count in payload.items():
            if isinstance(key, str) and isinstance(count, int) and not isinstance(count, bool):
                summary[key] = count
        return summary

    @staticmethod
    def _load_warnings(value: str) -> tuple[dict[str, str], ...]:
        """``warnings_json`` 文本 → ``(({path,message}), ...)``（非对象/非字符串项跳过）。"""
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            return ()
        if not isinstance(payload, list):
            return ()
        warnings: list[dict[str, str]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            path = item.get("path")
            message = item.get("message")
            if isinstance(path, str) and isinstance(message, str):
                warnings.append({"path": path, "message": message})
        return tuple(warnings)

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        """SQLite 的 ``DATETIME`` 不保留时区；落库均为 UTC，读出时补 UTC 时区。"""
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


__all__ = ["SqliteScenarioRepository"]
