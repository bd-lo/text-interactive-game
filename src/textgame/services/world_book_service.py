"""H4 世界书服务：导入 / 校验 / 列表 / 详情 / 软删 + ``WorldBookProvider`` 实现。

职责边界（H4-CONTRACT §1/§2/§3）：

- **格式解析与触发语义在 domain 层**（``textgame.domain.world_book``，algorithm 写）：本模块只
  消费 ``parse_import_payload``，不重复实现 SillyTavern / 角色卡格式识别；
- **存储只经注入的 ``WorldBookRepository`` 端口**（实现见
  ``persistence.world_book_repository.SqliteWorldBookRepository``），服务层不 import 具体实现；
- **不参与事件溯源**：世界书是独立资料库，导入/删除不产生 ``Event``、不改 ``GameState``；
  对局只通过 ``game_created.params.world_book_id``（仅建局传入时）与 ``WorldBookProvider``
  消费（本服务实现 ``get_world_book``，供编排层/查询层构造注入）；
- 写操作（import/delete）必带 ``Idempotency-Key``：同键同参回放首次响应、同键异参 409，
  复用既有 ``idempotency`` 表与 ``IdempotencyRecord``（同 ``CardService`` 口径）；
- 软删除：``delete_world_book`` 只置 ``deleted_at``，读路径排除已删除世界书；重复删除返回
  ``{"deleted": True}``（幂等），从未存在 → 404。

安全：上传 ≤256KB、UTF-8 严格解码、拒绝重复 JSON 键、嵌套深度 ≤8、根必须为对象；
名称/描述/条目内容禁止控制字符；未知格式 422（由 domain 解析器抛 ``ValueError``）。
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Final, Protocol

from textgame.domain.hashing import params_hash
from textgame.domain.results import ErrorCode
from textgame.domain.world_book import (
    WorldBook,
    WorldBookEntry,
    parse_entry_extras,
    parse_import_payload,
)
from textgame.services.idempotency_locks import idempotency_lock
from textgame.services.ports import IdempotencyRecord

#: 上传文件大小上限（H4-CONTRACT §2：JSON ≤256KB；超限 HTTP 413）。
MAX_UPLOAD_BYTES: Final[int] = 256 * 1024
#: JSON 嵌套深度上限（与人物卡导入同口径，防畸形 payload 拖慢/撑爆解析）。
MAX_JSON_DEPTH: Final[int] = 8
#: 列表分页默认/上限（H4-CONTRACT §2：``?limit=20&offset=0&query=``，limit ≤100）。
LIST_DEFAULT_LIMIT: Final[int] = 20
LIST_MAX_LIMIT: Final[int] = 100
#: 列表搜索词长度上限（防止畸形查询拖慢 name LIKE）。
MAX_QUERY_LENGTH: Final[int] = 100
#: 世界书名/描述/来源列长度（与 ``persistence.tables`` 的列宽一致）。
MAX_NAME_CHARS: Final[int] = 64
MAX_DESCRIPTION_CHARS: Final[int] = 500

#: 世界书 ID：``wb_`` + 30 位小写 [a-z0-9]（与人物卡 ``card_`` 前缀区分，H4-CONTRACT §1）。
_WORLD_BOOK_ID_RE: Final[re.Pattern[str]] = re.compile(r"^wb_[a-z0-9]{30}$")
#: 允许的导入来源（由 domain 解析器检测决定）。
_ALLOWED_SOURCES: Final[frozenset[str]] = frozenset({"tavern", "character_card", "manual"})
#: 控制字符（不含换行/制表符）：出现在玩家可见文本里属畸形数据。
_CONTROL_CHAR_RE: Final[re.Pattern[str]] = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
#: 角色卡内嵌 character_book 提取时的告警码。
WARNING_CHARACTER_BOOK_EXTRACTED: Final[str] = "CHARACTER_BOOK_EXTRACTED"
#: 空世界书（0 条目）导入告警码。
WARNING_EMPTY_WORLD_BOOK: Final[str] = "EMPTY_WORLD_BOOK"


class WorldBookServiceError(Exception):
    """世界书库业务失败：接入层按 ``error_code`` 映射统一失败信封。

    ``http_status`` 用于契约特例（文件过大 → 413）；``violations`` 为字段级错误列表。
    """

    def __init__(
        self,
        error_code: ErrorCode,
        message: str,
        *,
        violations: list[dict[str, Any]] | None = None,
        http_status: int | None = None,
    ) -> None:
        """携带稳定错误码、可读消息、可选 violations/状态码覆盖。"""
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.violations = violations
        self.http_status = http_status


@dataclass(frozen=True)
class StoredWorldBook:
    """世界书库中的一条世界书（软删除前的记录）；``raw`` 为原始导入 payload。"""

    world_book_id: str
    name: str
    description: str
    source: str
    entry_count: int
    raw: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


@dataclass(frozen=True)
class StoredWorldBookEntry:
    """世界书库中的一条规范化条目（触发语义字段与 ``domain.world_book.WorldBookEntry`` 对齐）。"""

    entry_uid: int
    keys: tuple[str, ...]
    secondary_keys: tuple[str, ...]
    content: str
    enabled: bool = True
    constant: bool = False
    selective: bool = True
    selective_logic: int = 0
    order: int = 100
    position: int = 0
    depth: int = 4
    case_sensitive: bool = False
    match_whole_words: bool = False
    exclude_recursion: bool = False
    prevent_recursion: bool = False
    raw: dict[str, Any] | None = None


class WorldBookRepository(Protocol):
    """世界书仓储端口（实现见 ``persistence.world_book_repository``）。"""

    def list_world_books(
        self, *, query: str | None, limit: int, offset: int
    ) -> tuple[list[StoredWorldBook], int]:
        """返回未删除世界书窗口与过滤后总数（按 ``updated_at`` 倒序）。"""
        ...

    def get_world_book(
        self, world_book_id: str, *, include_deleted: bool = False
    ) -> StoredWorldBook | None:
        """按 id 取世界书；``include_deleted=True`` 时含已软删记录。"""
        ...

    def get_entries(self, world_book_id: str) -> list[StoredWorldBookEntry]:
        """返回该书全部条目（按 ``entry_uid`` 升序）。"""
        ...

    def save_world_book(
        self, book: StoredWorldBook, entries: Sequence[StoredWorldBookEntry]
    ) -> None:
        """新增或覆盖一本书（同 id 覆盖；条目整批重写；``created_at`` 保留首次值）。"""
        ...

    def delete_world_book(self, world_book_id: str, *, deleted_at: datetime) -> bool:
        """软删除（置 ``deleted_at``）；不存在或已删除返回 ``False``（零写入）。"""
        ...

    def get_idempotent(self, key: str) -> IdempotencyRecord | None:
        """按存储键取幂等记录（复用既有 ``idempotency`` 表）。"""
        ...

    def save_idempotent(self, record: IdempotencyRecord) -> None:
        """保存幂等记录（同键覆盖；同键异参判定由服务层完成）。"""
        ...


def _utc_now() -> datetime:
    """当前 UTC 时间（世界书时间戳统一 UTC，落库读回补时区）。"""
    return datetime.now(timezone.utc)


def new_world_book_id() -> str:
    """生成服务端世界书 ID：``wb_`` + 30 位小写 [a-z0-9]（uuid4 hex 子集，不含大写）。"""
    return f"wb_{uuid.uuid4().hex[:30]}"


def _schema_issue(loc: list[str], issue_type: str, msg: str) -> dict[str, Any]:
    """统一构造 ``{loc, type, msg}`` 字段级问题项（接入层/前端可直接展示）。"""
    return {"loc": loc, "type": issue_type, "msg": msg}


class _DuplicateJsonKeyError(ValueError):
    """JSON 对象出现重复键（第 6 章 §6.7/第 15 章防注入：重复键直接拒绝）。"""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """``json.loads(object_pairs_hook=...)``：重复键抛 :class:`_DuplicateJsonKeyError`。"""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKeyError(key)
        result[key] = value
    return result


def _reject_non_finite(value: str) -> Any:
    """``json.loads(parse_constant=...)``：拒绝 NaN/Infinity（R-11）。"""
    raise ValueError(f"JSON 不允许非有限数值: {value}")


def _json_depth(value: Any) -> int:
    """JSON 值嵌套深度（标量 = 1，容器 = 1 + 子项最大深度）。"""
    if isinstance(value, dict):
        return 1 + max((_json_depth(item) for item in value.values()), default=0)
    if isinstance(value, list):
        return 1 + max((_json_depth(item) for item in value), default=0)
    return 1


def _truncate(value: str, limit: int) -> str:
    """按字符数截断（防列溢出；导入数据可能带超长名称/描述）。"""
    return value[:limit]


def _best_effort_description(payload: dict[str, Any]) -> str:
    """世界书描述：优先顶层/角色卡 ``description`` 字段，缺失/非字符串 → 空串。"""
    candidates = [payload.get("description")]
    data = payload.get("data")
    if isinstance(data, dict):
        candidates.append(data.get("description"))
        book = data.get("character_book")
        if isinstance(book, dict):
            candidates.append(book.get("description"))
    book = payload.get("character_book")
    if isinstance(book, dict):
        candidates.append(book.get("description"))
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return _truncate(" ".join(candidate.split()), MAX_DESCRIPTION_CHARS)
    return ""


def _entry_containers(payload: dict[str, Any]) -> list[Any]:
    """尽力收集 payload 里的「原始 entry 容器」（仅用于保留 raw_json，不参与解析决策）。"""
    containers: list[Any] = []
    book = payload.get("character_book")
    if isinstance(book, dict):
        containers.append(book.get("entries"))
    data = payload.get("data")
    if isinstance(data, dict):
        character_book = data.get("character_book")
        if isinstance(character_book, dict):
            containers.append(character_book.get("entries"))
    if "entries" in payload:
        containers.append(payload.get("entries"))
    if not containers and payload and all(isinstance(value, dict) for value in payload.values()):
        containers.append(payload)
    return [container for container in containers if isinstance(container, (dict, list))]


def _raw_entry_best_effort(payload: dict[str, Any], entry: WorldBookEntry) -> dict[str, Any]:
    """尽力从原始 payload 找回该条目的原始 JSON（含未映射字段）；找不到返回规范化字段。

    ``domain.world_book.WorldBook`` 只保留规范化条目，不保留每条原始 dict（契约 §1 的
    ``raw_json`` 用于审计/前向兼容）。这里按 uid 在原始条目容器里定位：数组形态用 uid
    （``uid``/``id`` 字段或下标）匹配，map 形态用 map 键或 uid 匹配；定位失败时退回
    ``StoredWorldBookEntry`` 的规范化字段 JSON（不阻塞导入）。
    """
    for container in _entry_containers(payload):
        candidates: list[dict[str, Any]] = []
        if isinstance(container, dict):
            for map_key, value in container.items():
                if isinstance(value, dict):
                    candidates.append({"_map_key": map_key, **value})
        else:
            candidates = [value for value in container if isinstance(value, dict)]
        for index, candidate in enumerate(candidates):
            raw_uid = candidate.get("uid", candidate.get("id"))
            uid = index
            if isinstance(raw_uid, int) and not isinstance(raw_uid, bool):
                uid = raw_uid
            elif isinstance(raw_uid, str) and raw_uid.strip().isdigit():
                uid = int(raw_uid.strip())
            elif isinstance(candidate.get("_map_key"), str) and candidate["_map_key"].isdigit():
                uid = int(candidate["_map_key"])
            if uid == entry.uid:
                return {key: value for key, value in candidate.items() if key != "_map_key"}
    return _entry_to_raw(entry)


def _entry_to_raw(entry: WorldBookEntry) -> dict[str, Any]:
    """规范化条目 → 原始 JSON 兜底形态（字段名与 SillyTavern 常用键对齐）。"""
    return {
        "uid": entry.uid,
        "key": list(entry.keys),
        "keysecondary": list(entry.secondary_keys),
        "content": entry.content,
        "enabled": entry.enabled,
        "constant": entry.constant,
        "selective": entry.selective,
        "selectiveLogic": entry.selective_logic,
        "order": entry.order,
        "position": entry.position,
        "depth": entry.depth,
        "probability": entry.probability,
        "useProbability": entry.use_probability,
        "group": entry.group,
        "groupWeight": entry.group_weight,
        "groupOverride": entry.group_override,
        "useGroupScoring": entry.group_scoring,
        "scanDepth": entry.scan_depth,
        "caseSensitive": entry.case_sensitive,
        "matchWholeWords": entry.match_whole_words,
        "excludeRecursion": entry.exclude_recursion,
        "preventRecursion": entry.prevent_recursion,
        "sticky": entry.sticky,
        "cooldown": entry.cooldown,
        "delay": entry.delay,
    }


def _stored_entry(entry: WorldBookEntry, raw: dict[str, Any]) -> StoredWorldBookEntry:
    """domain 条目 + 原始 JSON → 仓储行数据。"""
    return StoredWorldBookEntry(
        entry_uid=entry.uid,
        keys=entry.keys,
        secondary_keys=entry.secondary_keys,
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
        raw=raw,
    )


def _domain_entry(entry: StoredWorldBookEntry) -> WorldBookEntry:
    """仓储行数据 → domain 条目（供触发引擎/Provider 使用）。

    仓储只落库 H4 契约 §3 的固定列；``probability``/``use_probability``/``group``/
    ``scan_depth`` 与 P1 新增的定时效果/包含组字段从 ``raw_json``（``entry.raw``）恢复，
    不改表、不加迁移；旧数据只要 raw 里保留了原字段即可自动生效。
    """
    extras = parse_entry_extras(entry.raw or {})
    return WorldBookEntry(
        uid=entry.entry_uid,
        keys=entry.keys,
        secondary_keys=entry.secondary_keys,
        content=entry.content,
        enabled=entry.enabled,
        constant=entry.constant,
        selective=entry.selective,
        selective_logic=entry.selective_logic,
        order=entry.order,
        position=entry.position,
        depth=entry.depth,
        probability=extras.probability,
        use_probability=extras.use_probability,
        group=extras.group,
        scan_depth=extras.scan_depth,
        case_sensitive=entry.case_sensitive,
        match_whole_words=entry.match_whole_words,
        exclude_recursion=entry.exclude_recursion,
        prevent_recursion=entry.prevent_recursion,
        sticky=extras.sticky,
        cooldown=extras.cooldown,
        delay=extras.delay,
        group_weight=extras.group_weight,
        group_override=extras.group_override,
        group_scoring=extras.group_scoring,
    )


def _entry_detail(entry: StoredWorldBookEntry) -> dict[str, Any]:
    """仓储行 → 详情响应条目（固定列 + ``raw_json`` 恢复的触发语义字段）。

    详情/预览要能看到玩家导入的完整触发语义（P1 D-WB-3 可见性缺口）：``probability``/
    ``use_probability``/``group``/``group_weight``/``group_override``/``group_scoring``/
    ``scan_depth`` 以及定时效果 ``sticky``/``cooldown``/``delay`` 只存在于 ``raw_json``，
    这里用 ``parse_entry_extras`` 还原后随响应返回（只读展示，不参与触发决策；触发仍走
    ``_domain_entry`` 的同一解析结果，两者保证一致）。
    """
    extras = parse_entry_extras(entry.raw or {})
    return {
        "entry_uid": entry.entry_uid,
        "keys": list(entry.keys),
        "secondary_keys": list(entry.secondary_keys),
        "content": entry.content,
        "enabled": entry.enabled,
        "constant": entry.constant,
        "order": entry.order,
        "position": entry.position,
        "depth": entry.depth,
        "selective": entry.selective,
        "selective_logic": entry.selective_logic,
        "case_sensitive": entry.case_sensitive,
        "match_whole_words": entry.match_whole_words,
        "exclude_recursion": entry.exclude_recursion,
        "prevent_recursion": entry.prevent_recursion,
        "probability": extras.probability,
        "use_probability": extras.use_probability,
        "group": extras.group,
        "group_weight": extras.group_weight,
        "group_override": extras.group_override,
        "group_scoring": extras.group_scoring,
        "scan_depth": extras.scan_depth,
        "sticky": extras.sticky,
        "cooldown": extras.cooldown,
        "delay": extras.delay,
    }


def _normalized_entry_payloads(book: WorldBook) -> list[tuple[WorldBookEntry, dict[str, Any]]]:
    """规范化条目 + 尽力找回的原始 JSON 配对（用于落库）。"""
    return [(entry, _raw_entry_best_effort(book.raw, entry)) for entry in book.entries]


def _content_control_char_issue(text: str, loc: list[str]) -> dict[str, Any] | None:
    """玩家可见文本含控制字符 → 字段级问题项（None = 合法）。"""
    if _CONTROL_CHAR_RE.search(text):
        return _schema_issue(loc, "content_not_allowed", "文本包含不允许的控制字符")
    return None


class WorldBookService:
    """世界书业务服务：导入/校验/列表/详情/软删 + ``WorldBookProvider`` 实现。"""

    def __init__(self, repo: WorldBookRepository) -> None:
        """绑定仓储端口；所有写操作经 ``Idempotency-Key`` 幂等，读操作零副作用。"""
        self._repo = repo

    # ------------------------------------------------------------------
    # Provider（编排层/查询层构造注入）
    # ------------------------------------------------------------------
    def get_world_book(self, world_book_id: str) -> WorldBook | None:
        """按 id 取可用（未软删）世界书 → domain ``WorldBook``；未知/已删除返回 ``None``。

        这是 ``domain.world_book.WorldBookProvider`` 协议要求的方法（编排层与查询层注入）。
        """
        if not isinstance(world_book_id, str) or not _WORLD_BOOK_ID_RE.match(world_book_id):
            return None
        stored = self._repo.get_world_book(world_book_id)
        if stored is None:
            return None
        entries = self._repo.get_entries(world_book_id)
        return WorldBook(
            name=stored.name,
            entries=tuple(_domain_entry(entry) for entry in entries),
            source=stored.source,
            raw=stored.raw,
        )

    def get(self, world_book_id: str) -> WorldBook | None:
        """``get_world_book`` 的短别名（duck typing 兼容不同注入点命名）。"""
        return self.get_world_book(world_book_id)

    # ------------------------------------------------------------------
    # 只读：列表 / 详情
    # ------------------------------------------------------------------
    def list_world_books(
        self,
        *,
        query: str | None = None,
        limit: int = LIST_DEFAULT_LIMIT,
        offset: int = 0,
    ) -> dict[str, Any]:
        """返回 ``{world_books, total}``（按最近更新倒序；``query`` 匹配 name）。"""
        if limit < 1 or limit > LIST_MAX_LIMIT:
            raise WorldBookServiceError(
                ErrorCode.INVALID_INPUT, f"limit 必须在 1..{LIST_MAX_LIMIT} 之间"
            )
        if offset < 0:
            raise WorldBookServiceError(ErrorCode.INVALID_INPUT, "offset 不能为负数")
        keyword = query.strip() if isinstance(query, str) else ""
        if len(keyword) > MAX_QUERY_LENGTH:
            raise WorldBookServiceError(
                ErrorCode.INVALID_INPUT, f"query 长度不能超过 {MAX_QUERY_LENGTH}"
            )
        books, total = self._repo.list_world_books(
            query=keyword or None, limit=limit, offset=offset
        )
        return {
            "world_books": [
                {
                    "world_book_id": stored.world_book_id,
                    "name": stored.name,
                    "description": stored.description,
                    "source": stored.source,
                    "entry_count": stored.entry_count,
                    "updated_at": stored.updated_at,
                }
                for stored in books
            ],
            "total": total,
        }

    def get_detail(self, world_book_id: str) -> dict[str, Any]:
        """返回 ``{world_book, entries}``；未知/已删除 → 404 ``NOT_FOUND``。"""
        stored = self._require_stored(world_book_id)
        entries = self._repo.get_entries(world_book_id)
        return {
            "world_book": {
                "world_book_id": stored.world_book_id,
                "name": stored.name,
                "description": stored.description,
                "source": stored.source,
                "entry_count": stored.entry_count,
                "updated_at": stored.updated_at,
            },
            "entries": [_entry_detail(entry) for entry in entries],
        }

    # ------------------------------------------------------------------
    # 校验（只读、无副作用）
    # ------------------------------------------------------------------
    def validate_payload(self, payload: Any) -> dict[str, Any]:
        """查询式校验任意 JSON payload：返回 ``{valid, errors, warnings, preview}``。

        校验失败仍 200（只表达结果）；格式不可识别/结构非法时 ``valid=False``，
        ``preview.detected_format`` 为 ``"unknown"``。只读、零写入。
        """
        errors: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        preview = {"name": "", "entry_count": 0, "detected_format": "unknown"}
        try:
            # R-11：validate 的 payload 已由 FastAPI 解析（可能含 NaN/Infinity）；这里统一拒绝。
            encoded_payload = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            errors.append(_schema_issue(["payload"], "value_error", f"payload 含非法数值：{exc}"))
            return {"valid": False, "errors": errors, "warnings": warnings, "preview": preview}
        if len(encoded_payload.encode("utf-8")) > MAX_UPLOAD_BYTES:
            errors.append(
                _schema_issue(
                    ["payload"],
                    "value_error",
                    f"payload 超过 {MAX_UPLOAD_BYTES // 1024}KB 上限",
                )
            )
            return {"valid": False, "errors": errors, "warnings": warnings, "preview": preview}
        if not isinstance(payload, dict):
            errors.append(_schema_issue(["payload"], "value_error", "世界书 JSON 根必须是对象"))
            return {"valid": False, "errors": errors, "warnings": warnings, "preview": preview}
        try:
            book = parse_import_payload(payload)
        except ValueError as exc:
            errors.append(_schema_issue([], "invalid_format", str(exc)))
            return {"valid": False, "errors": errors, "warnings": warnings, "preview": preview}
        warnings.extend(self._format_warnings(book))
        errors.extend(self._content_issues(book, payload))
        preview = {
            "name": _truncate(book.name, MAX_NAME_CHARS),
            "entry_count": len(book.entries),
            "detected_format": book.source,
        }
        return {
            "valid": not errors,
            "errors": errors,
            "warnings": warnings,
            "preview": preview,
        }

    # ------------------------------------------------------------------
    # 写入（必带 Idempotency-Key；同键同参回放、同键异参 409）
    # ------------------------------------------------------------------
    def import_book(
        self,
        *,
        filename: str | None,
        content_type: str | None,
        content: bytes,
        name: str | None,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """上传 JSON 并入库 → ``{world_book_id, name, entry_count, source, warnings}``。"""
        _ensure_import_metadata(filename, content_type)
        self._ensure_size(content)
        store_key = f"world_book:import:{idempotency_key}"
        request_hash = params_hash(
            {
                "op": "import",
                "filename": filename or "",
                "sha256": hashlib.sha256(content).hexdigest(),
                "name": (name or "").strip(),
            }
        )

        def _run() -> dict[str, Any]:
            payload = self._parse_json_object(content)
            book = self._parse_book(payload)
            warnings = self._format_warnings(book)
            issues = self._content_issues(book, payload)
            if issues:
                raise WorldBookServiceError(
                    ErrorCode.INVALID_INPUT,
                    "世界书内容包含不允许的字符",
                    violations=issues,
                )
            world_book_name = (name or "").strip() or book.name
            world_book_name = (
                _truncate(" ".join(world_book_name.split()), MAX_NAME_CHARS) or "未命名世界书"
            )
            if len(book.entries) == 0:
                warnings.append(
                    {
                        "code": WARNING_EMPTY_WORLD_BOOK,
                        "message": "世界书不含任何条目，导入后不会触发任何设定",
                    }
                )
            stored_entries = [
                _stored_entry(entry, raw) for entry, raw in _normalized_entry_payloads(book)
            ]
            now = _utc_now()
            world_book_id = new_world_book_id()
            self._repo.save_world_book(
                StoredWorldBook(
                    world_book_id=world_book_id,
                    name=world_book_name,
                    description=_best_effort_description(payload),
                    source=book.source,
                    entry_count=len(stored_entries),
                    raw=payload,
                    created_at=now,
                    updated_at=now,
                ),
                stored_entries,
            )
            return {
                "world_book_id": world_book_id,
                "name": world_book_name,
                "entry_count": len(stored_entries),
                "source": book.source,
                "warnings": warnings,
            }

        return self._idempotent(store_key, request_hash, _run)

    def delete_book(self, *, world_book_id: str, idempotency_key: str) -> dict[str, Any]:
        """软删除世界书 → ``{world_book_id, deleted: True}``；从未存在 404，重复删幂等成功。"""
        store_key = f"world_book:delete:{idempotency_key}"
        request_hash = params_hash({"op": "delete", "world_book_id": world_book_id})

        def _run() -> dict[str, Any]:
            stored = self._repo.get_world_book(world_book_id, include_deleted=True)
            if stored is None:
                raise WorldBookServiceError(ErrorCode.NOT_FOUND, f"世界书不存在: {world_book_id}")
            if stored.deleted_at is None:
                deleted = self._repo.delete_world_book(world_book_id, deleted_at=_utc_now())
                if not deleted:  # 并发下已被删：按幂等成功处理
                    stored = self._repo.get_world_book(world_book_id, include_deleted=True)
                    if stored is None:
                        raise WorldBookServiceError(
                            ErrorCode.NOT_FOUND, f"世界书不存在: {world_book_id}"
                        )
            return {"world_book_id": world_book_id, "deleted": True}

        return self._idempotent(store_key, request_hash, _run)

    # ------------------------------------------------------------------
    # 解析 / 安全
    # ------------------------------------------------------------------
    def _ensure_size(self, content: bytes) -> None:
        """上传体大小上限：超过 256KB → ``INVALID_INPUT`` + HTTP 413（H4-CONTRACT §2）。"""
        if len(content) > MAX_UPLOAD_BYTES:
            raise WorldBookServiceError(
                ErrorCode.INVALID_INPUT,
                f"世界书 JSON 超过 {MAX_UPLOAD_BYTES // 1024}KB 上限",
                http_status=413,
            )

    def _parse_json_object(self, content: bytes) -> dict[str, Any]:
        """字节 → JSON 对象：UTF-8 严格解码、拒绝重复键、深度 ≤8、根必须为对象。"""
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WorldBookServiceError(
                ErrorCode.INVALID_INPUT, "世界书 JSON 必须为 UTF-8 编码"
            ) from exc
        try:
            payload = json.loads(
                text,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_non_finite,
            )
        except _DuplicateJsonKeyError as exc:
            raise WorldBookServiceError(ErrorCode.INVALID_INPUT, f"JSON 含重复键: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise WorldBookServiceError(
                ErrorCode.INVALID_INPUT,
                f"JSON 语法错误: {exc.msg} (line {exc.lineno} column {exc.colno})",
            ) from exc
        except ValueError as exc:
            raise WorldBookServiceError(ErrorCode.INVALID_INPUT, str(exc)) from exc
        except RecursionError as exc:
            raise WorldBookServiceError(ErrorCode.INVALID_INPUT, "JSON 嵌套过深") from exc
        try:
            depth = _json_depth(payload)
        except RecursionError as exc:
            raise WorldBookServiceError(ErrorCode.INVALID_INPUT, "JSON 嵌套过深") from exc
        if depth > MAX_JSON_DEPTH:
            raise WorldBookServiceError(
                ErrorCode.INVALID_INPUT, f"JSON 嵌套深度超过 {MAX_JSON_DEPTH} 层"
            )
        if not isinstance(payload, dict):
            raise WorldBookServiceError(ErrorCode.INVALID_INPUT, "世界书 JSON 根必须是对象")
        return payload

    @staticmethod
    def _parse_book(payload: dict[str, Any]) -> WorldBook:
        """调用 domain 解析器；不可识别格式 → 422 ``INVALID_INPUT``（含原因）。"""
        try:
            return parse_import_payload(payload)
        except ValueError as exc:
            raise WorldBookServiceError(
                ErrorCode.INVALID_INPUT, f"不支持的世世界书格式或内容非法: {exc}"
            ) from exc

    @staticmethod
    def _format_warnings(book: WorldBook) -> list[dict[str, Any]]:
        """格式相关告警（角色卡提取/空书/解析期诊断；不阻塞导入）。

        形状保持既有 ``[{code, message}]``（前端 ``WorldBookWarningResponse`` 未变），
        只增条目：domain 解析期产出的正则键非法、定时效果越界、未支持字段等诊断在此透传。
        """
        warnings: list[dict[str, Any]] = []
        if book.source == "character_card":
            warnings.append(
                {
                    "code": WARNING_CHARACTER_BOOK_EXTRACTED,
                    "message": "已从角色卡 data.character_book 提取世界书",
                }
            )
        if book.source not in _ALLOWED_SOURCES:  # pragma: no cover - 解析器只返回三种 source
            warnings.append({"code": "UNKNOWN_SOURCE", "message": f"未知世界书来源: {book.source}"})
        warnings.extend({"code": item.code, "message": item.message} for item in book.warnings)
        return warnings

    @staticmethod
    def _content_issues(book: WorldBook, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """玩家可见文本安全：书名/描述/条目内容不得含控制字符（返回字段级问题项）。"""
        issues: list[dict[str, Any]] = []
        name_issue = _content_control_char_issue(book.name, ["name"])
        if name_issue is not None:
            issues.append(name_issue)
        description_issue = _content_control_char_issue(_best_effort_description(payload), [])
        if description_issue is not None:
            description_issue["loc"] = ["description"]
            issues.append(description_issue)
        for entry, raw in _normalized_entry_payloads(book):
            issue = _content_control_char_issue(entry.content, ["entries", str(entry.uid)])
            if issue is not None:
                issues.append(issue)
            del raw  # 仅用于 raw_json 落库；安全扫描只看规范化内容
        return issues

    def _require_stored(self, world_book_id: str) -> StoredWorldBook:
        """取未删除世界书；不存在/已删除/格式非法 → 404 ``NOT_FOUND``。"""
        if not _WORLD_BOOK_ID_RE.match(world_book_id):
            raise WorldBookServiceError(ErrorCode.NOT_FOUND, f"世界书不存在: {world_book_id}")
        stored = self._repo.get_world_book(world_book_id)
        if stored is None:
            raise WorldBookServiceError(ErrorCode.NOT_FOUND, f"世界书不存在: {world_book_id}")
        return stored

    # ------------------------------------------------------------------
    # 幂等（复用既有 idempotency 表/端口，与 CardService 同口径）
    # ------------------------------------------------------------------
    def _idempotent(
        self,
        store_key: str,
        request_hash: str,
        operation: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        """R-04：同 ``Idempotency-Key`` 的世界书写请求在进程内串行（单进程部署）。"""
        with idempotency_lock(store_key):
            return self._idempotent_unlocked(store_key, request_hash, operation)

    def _idempotent_unlocked(
        self,
        store_key: str,
        request_hash: str,
        operation: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        """同键同参回放首次响应（含失败响应）；同键异参 → 409 ``CONFLICT``。"""
        cached = self._repo.get_idempotent(store_key)
        if cached is not None:
            if cached.params_hash != request_hash:
                raise WorldBookServiceError(
                    ErrorCode.CONFLICT,
                    "该 Idempotency-Key 已经用在不同参数的世界书请求上",
                )
            error = cached.payload.get("__error__")
            if isinstance(error, dict):
                code = ErrorCode(str(error.get("code", ErrorCode.INTERNAL.value)))
                status = error.get("http_status")
                raise WorldBookServiceError(
                    code,
                    str(error.get("message", "世界书请求失败")),
                    violations=error.get("violations"),
                    http_status=int(status) if isinstance(status, int) else None,
                )
            return dict(cached.payload)
        try:
            result = operation()
        except WorldBookServiceError as exc:
            self._save_error_idempotent(store_key, request_hash, exc)
            raise
        self._repo.save_idempotent(
            IdempotencyRecord(key=store_key, params_hash=request_hash, payload=result)
        )
        return result

    def _save_error_idempotent(
        self, store_key: str, request_hash: str, exc: WorldBookServiceError
    ) -> None:
        """缓存可重放的业务失败；R-20：全部 4xx 拒绝不缓存（修正输入后同键可重试）。"""
        if exc.http_status is not None and 400 <= exc.http_status < 500:
            return
        if exc.error_code in {
            ErrorCode.INVALID_INPUT,
            ErrorCode.SCHEMA_VIOLATION,
            ErrorCode.NOT_ALLOWED,
        }:
            return
        payload = {
            "__error__": {
                "code": exc.error_code.value,
                "message": exc.message,
                "violations": exc.violations,
                "http_status": exc.http_status,
            }
        }
        self._repo.save_idempotent(
            IdempotencyRecord(key=store_key, params_hash=request_hash, payload=payload)
        )


def _ensure_import_metadata(filename: str | None, content_type: str | None) -> None:
    """上传文件元数据：只接受 ``.json`` 文件名与 JSON 类 Content-Type。"""
    if filename is None or not filename.lower().endswith(".json"):
        raise WorldBookServiceError(ErrorCode.INVALID_INPUT, "只接受 .json 文件")
    allowed_types = {None, "", "application/json", "application/octet-stream"}
    if content_type is not None:
        normalized = content_type.split(";", 1)[0].strip().lower()
        if normalized not in allowed_types:
            raise WorldBookServiceError(
                ErrorCode.INVALID_INPUT, f"只接受 application/json，收到 {content_type!r}"
            )


__all__ = [
    "LIST_DEFAULT_LIMIT",
    "LIST_MAX_LIMIT",
    "MAX_JSON_DEPTH",
    "MAX_QUERY_LENGTH",
    "MAX_UPLOAD_BYTES",
    "WARNING_CHARACTER_BOOK_EXTRACTED",
    "WARNING_EMPTY_WORLD_BOOK",
    "StoredWorldBook",
    "StoredWorldBookEntry",
    "WorldBookRepository",
    "WorldBookService",
    "WorldBookServiceError",
    "new_world_book_id",
]
