"""用户自上传场景服务：导入 / 校验 / 列表 / 详情 / 软删 / 建局载荷（设计契约 §3.3/§3.4）。

职责边界：

- **格式与语义在领域层**（``textgame.domain.user_scenario``）：本模块只消费
  ``parse_definition``（结构化 errors/warnings/summary）、``derive_ids``、
  ``build_initial_state`` 与 ``UserScenarioHooks``，不重复实现条件 DSL/ID 派生；
- **存储只经注入的 ``ScenarioRepository`` 端口**（实现见
  ``persistence.scenario_repository.SqliteScenarioRepository``），服务层不 import 具体实现；
- **不参与事件溯源**：场景是独立资料库，导入/删除不产生 ``Event``、不改 ``GameState``；
  只有建局时由 :meth:`ScenarioService.build_game_payload` 把定义物化成初始状态
  （``GameState`` 不新增字段，``game_created.params`` 的加法由编排层负责）；
- **幂等**：``import_scenario`` 必带 ``Idempotency-Key``：同键同参回放首次响应、同键异参
  409 ``CONFLICT``；复用既有 ``idempotency`` 表/端口（同 ``CardService``/``WorldBookService``
  口径），不新增第二套幂等语义；
- **软删除**：``delete_scenario`` 只置 ``deleted_at``，列表/详情/新建局不可见；重复删除
  ``NOT_FOUND``。已开局存档的 runtime 索引必须保留，因此 :meth:`reload` 读
  ``list_for_runtime``（含已软删记录）重建索引。

运行时索引（``ScenarioRuntime``）：``import`` 后立即注册，进程启动时由组合根调用
:meth:`ScenarioService.reload` 从库重建；``delete`` 不摘除索引（旧存档仍可读/回放）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Final, Protocol

from textgame.domain.hashing import canonical_json, params_hash
from textgame.domain.results import ErrorCode
from textgame.domain.scenario import PLAYER_ID
from textgame.domain.scenario_runtime import ScenarioRuntime
from textgame.domain.user_scenario import (
    MAX_DEFINITION_BYTES,
    ParsedDefinition,
    ScenarioDefinition,
    ScenarioIds,
    ScenarioIssue,
    UserScenarioHooks,
    build_initial_state,
    derive_ids,
    parse_definition,
)
from textgame.services.idempotency_locks import idempotency_lock
from textgame.services.ports import IdempotencyRecord

#: 列表分页默认/上限（设计契约 §3.4：``limit``≤100/``offset``/``q`` 搜索）。
LIST_DEFAULT_LIMIT: Final[int] = 20
LIST_MAX_LIMIT: Final[int] = 100
#: 列表搜索词长度上限（防止畸形查询拖慢 LIKE）。
MAX_QUERY_LENGTH: Final[int] = 100
#: summary 键固定集合（前端契约测试逐键断言，缺一不可）。
SUMMARY_KEYS: Final[tuple[str, ...]] = (
    "rooms",
    "items",
    "npcs",
    "objectives",
    "endings",
    "suggestions",
)

#: 场景 ID：``scn_`` + 26 位 Crockford Base32（ULID 字符集；导入时生成、不可变）。
_SCENARIO_ID_PATTERN: Final[str] = r"^scn_[0-9A-HJKMNP-TV-Z]{26}$"
_SCENARIO_ID_RE: Final[re.Pattern[str]] = re.compile(_SCENARIO_ID_PATTERN)
_CROCKFORD_BASE32: Final[str] = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class ScenarioServiceError(Exception):
    """场景库业务失败：接入层按 ``error_code`` 映射统一失败信封。

    ``violations`` 为字段级错误列表（场景定义为 ``[{path,message}]``，与 errors/warnings
    同构）；``http_status`` 用于契约特例（导入超 256KB → 413，其余按错误码映射表）。
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


class ScenarioValidationError(ScenarioServiceError):
    """场景定义非法（422 ``INVALID_INPUT``；导入超限时携带 413）；``errors/warnings`` 带 JSON 路径。"""

    def __init__(
        self,
        message: str,
        *,
        errors: Sequence[dict[str, str]],
        warnings: Sequence[dict[str, str]] = (),
        http_status: int | None = None,
    ) -> None:
        """``errors`` 进失败信封的 ``violations``（保持 ``[{path,message}]`` 形状）。"""
        normalized_errors = [
            {"path": str(item.get("path", "")), "message": str(item.get("message", ""))}
            for item in errors
        ]
        self.errors = normalized_errors
        self.warnings = [
            {"path": str(item.get("path", "")), "message": str(item.get("message", ""))}
            for item in warnings
        ]
        super().__init__(
            ErrorCode.INVALID_INPUT,
            message,
            violations=normalized_errors,
            http_status=http_status,
        )


class ScenarioNotFoundError(ScenarioServiceError):
    """场景不存在或已软删除（404 ``NOT_FOUND``）。"""

    def __init__(self, message: str) -> None:
        """构造 404 失败（消息面向玩家，不暴露内部细节）。"""
        super().__init__(ErrorCode.NOT_FOUND, message)


class ScenarioConflictError(ScenarioServiceError):
    """幂等键冲突：同键异参（409 ``CONFLICT``）。"""

    def __init__(self, message: str) -> None:
        """构造 409 失败。"""
        super().__init__(ErrorCode.CONFLICT, message)


@dataclass(frozen=True)
class StoredScenario:
    """场景库中的一条场景（软删除前的记录）；``definition`` 为原始定义 JSON 对象。"""

    scenario_id: str
    slug: str
    title: str
    author: str
    description: str
    schema_version: int
    definition: dict[str, Any]
    summary: dict[str, int]
    warnings: tuple[dict[str, str], ...]
    created_at: datetime
    deleted_at: datetime | None = None


class ScenarioRepository(Protocol):
    """场景仓储端口（实现见 ``persistence.scenario_repository``）。"""

    def insert(self, scenario: StoredScenario) -> None:
        """写入一条新场景（``scenario_id`` 由服务层生成，不可变）。"""
        ...

    def get(self, scenario_id: str, *, include_deleted: bool = False) -> StoredScenario | None:
        """按 id 取场景；非 ``include_deleted`` 时已软删记录视为不存在。"""
        ...

    def list_scenarios(
        self, *, query: str | None, limit: int, offset: int
    ) -> tuple[list[StoredScenario], int]:
        """返回未删除场景窗口与过滤后总数（按 ``created_at`` 倒序）。"""
        ...

    def soft_delete(self, scenario_id: str, *, deleted_at: datetime) -> bool:
        """软删除（置 ``deleted_at``）；不存在或已删除返回 ``False``（零写入）。"""
        ...

    def list_for_runtime(self) -> list[StoredScenario]:
        """返回**全部**场景（含已软删；runtime 索引重建用）。"""
        ...

    def get_idempotent(self, key: str) -> IdempotencyRecord | None:
        """按存储键取幂等记录（复用既有 ``idempotency`` 表）。"""
        ...

    def save_idempotent(self, record: IdempotencyRecord) -> None:
        """保存幂等记录（同键覆盖；同键异参判定由服务层完成）。"""
        ...


def _utc_now() -> datetime:
    """当前 UTC 时间（场景时间戳统一 UTC，落库读回补时区）。"""
    return datetime.now(timezone.utc)


def new_scenario_id() -> str:
    """生成服务端场景 ID：``scn_`` + 26 位 ULID 形态（时间戳 48bit + 随机 80bit）。

    导入一次生成一个（同 slug 重复导入也得到新 ID，不覆盖已开局存档）；纯服务端权威 ID，
    前端不得指定。
    """
    timestamp_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    randomness = int.from_bytes(os.urandom(10), "big") & ((1 << 80) - 1)
    value = (timestamp_ms << 80) | randomness
    chars = [_CROCKFORD_BASE32[(value >> (5 * (25 - index))) & 0x1F] for index in range(26)]
    return "scn_" + "".join(chars)


class _DuplicateJsonKeyError(ValueError):
    """JSON 对象出现重复键（与人物卡/世界书导入同口径：直接拒绝）。"""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """``json.loads(object_pairs_hook=...)``：重复键抛 :class:`_DuplicateJsonKeyError`。"""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKeyError(key)
        result[key] = value
    return result


def _duplicate_key_issue(raw: bytes) -> ScenarioIssue | None:
    """检测 JSON 重复键（``parse_definition`` 用默认 json 解析，重复键会静默后者覆盖）。

    仅在 ``parse_definition`` 已判定合法后调用：编码/语法错误已由领域层给出更精确的 issue。
    """
    try:
        json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except _DuplicateJsonKeyError as exc:
        return ScenarioIssue("$", f'JSON 含重复键 "{exc}"（同一对象内键名必须唯一）')
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):  # pragma: no cover
        return None
    return None


def _complete_summary(summary: Mapping[str, int]) -> dict[str, int]:
    """把 summary 补齐为固定 6 个键（校验失败时领域层可能给空 dict；前端要求键齐全）。"""
    return {key: int(summary.get(key, 0)) for key in SUMMARY_KEYS}


class ScenarioService:
    """场景库业务服务；依赖注入 ``ScenarioRepository``（不直接 import 持久化实现）。"""

    def __init__(
        self,
        repo: ScenarioRepository,
        *,
        runtime: ScenarioRuntime | None = None,
    ) -> None:
        """绑定仓储端口与运行时索引（缺省新建空 ``ScenarioRuntime`` = 旧行为不变）。

        组合根可传入与查询/编排/动作层共享的同一个 ``ScenarioRuntime``，并在启动时调用
        :meth:`reload` 把已导入场景（含已软删）注册进索引；不调用 reload 时索引为空，
        只影响查询/钩子分发，不影响列表/详情/建局载荷。
        """
        self._repo = repo
        self.runtime: ScenarioRuntime = runtime if runtime is not None else ScenarioRuntime()

    # ------------------------------------------------------------------
    # 运行时索引
    # ------------------------------------------------------------------
    def reload(self) -> int:
        """从仓储重建 runtime 索引（含已软删场景，旧存档仍可读/回放）；返回注册数量。

        只注册能重新解析成功的定义（库内定义应均合法；损坏行跳过而不是让启动失败）。
        ``register_many`` 整体替换索引，因此本方法幂等。
        """
        hooks: list[UserScenarioHooks] = []
        for stored in self._repo.list_for_runtime():
            built = self._hooks_of(stored)
            if built is not None:
                hooks.append(built[0])
        self.runtime.register_many(hooks)
        return len(hooks)

    # ------------------------------------------------------------------
    # 只读：校验 / 列表 / 详情
    # ------------------------------------------------------------------
    def validate(self, raw: bytes) -> ParsedDefinition:
        """只读预检（不写库、无副作用）：返回领域层 ``ParsedDefinition``。

        除领域层校验外，额外拒绝 JSON 重复键（与人物卡/世界书导入口径一致）。
        """
        parsed = parse_definition(raw)
        if not parsed.ok:
            return parsed
        issue = _duplicate_key_issue(raw)
        if issue is not None:
            return ParsedDefinition(
                definition=None,
                errors=(issue,),
                warnings=parsed.warnings,
                summary=parsed.summary,
            )
        return parsed

    def list_scenarios(
        self,
        *,
        query: str | None = None,
        limit: int = LIST_DEFAULT_LIMIT,
        offset: int = 0,
    ) -> dict[str, Any]:
        """返回 ``{scenarios, total}``（按 ``created_at`` 倒序；``q`` 匹配 title/author/slug）。"""
        if limit < 1 or limit > LIST_MAX_LIMIT:
            raise ScenarioValidationError(
                f"limit 必须在 1..{LIST_MAX_LIMIT} 之间",
                errors=[{"path": "limit", "message": f"limit 必须在 1..{LIST_MAX_LIMIT} 之间"}],
            )
        if offset < 0:
            raise ScenarioValidationError(
                "offset 不能为负数",
                errors=[{"path": "offset", "message": "offset 不能为负数"}],
            )
        keyword = query.strip() if isinstance(query, str) else ""
        if len(keyword) > MAX_QUERY_LENGTH:
            raise ScenarioValidationError(
                f"q 长度不能超过 {MAX_QUERY_LENGTH}",
                errors=[{"path": "q", "message": f"q 长度不能超过 {MAX_QUERY_LENGTH}"}],
            )
        stored_items, total = self._repo.list_scenarios(
            query=keyword or None, limit=limit, offset=offset
        )
        return {
            "scenarios": [self._summary_payload(stored) for stored in stored_items],
            "total": total,
        }

    def get_scenario(self, scenario_id: str) -> dict[str, Any]:
        """场景详情（含原始定义对象、summary、warnings）；未知/已软删 → 404 ``NOT_FOUND``。"""
        stored = self._require_stored(scenario_id)
        payload = self._summary_payload(stored)
        payload["definition"] = stored.definition
        return payload

    # ------------------------------------------------------------------
    # 写入：导入（幂等）/ 软删
    # ------------------------------------------------------------------
    def import_scenario(self, raw: bytes, *, idempotency_key: str) -> dict[str, Any]:
        """导入场景 JSON 原文 → ``{scenario_id, slug, title, summary, warnings}``。

        同键同参回放首次响应（零写入）、同键异参 409；定义非法 → ``ScenarioValidationError``
        （422，导入超过 256KB → 413）。导入成功后立即注册 runtime 索引。
        """
        if len(raw) > MAX_DEFINITION_BYTES:
            too_large = f"场景 JSON 超过 {MAX_DEFINITION_BYTES // 1024}KB 上限"
            raise ScenarioValidationError(
                too_large,
                errors=[{"path": "$", "message": too_large}],
                http_status=413,
            )
        store_key = f"scenario:import:{idempotency_key}"
        request_hash = params_hash({"op": "import", "sha256": hashlib.sha256(raw).hexdigest()})

        def _run() -> dict[str, Any]:
            parsed = self.validate(raw)
            if not parsed.ok or parsed.definition is None:
                raise ScenarioValidationError(
                    "场景定义校验失败",
                    errors=[issue.as_dict() for issue in parsed.errors],
                    warnings=[issue.as_dict() for issue in parsed.warnings],
                )
            definition = parsed.definition
            scenario_id = new_scenario_id()
            stored = StoredScenario(
                scenario_id=scenario_id,
                slug=definition.slug,
                title=definition.meta.title,
                author=definition.meta.author,
                description=definition.meta.description,
                schema_version=definition.schema_version,
                definition=self._definition_payload(raw),
                summary=_complete_summary(parsed.summary),
                warnings=tuple(issue.as_dict() for issue in parsed.warnings),
                created_at=_utc_now(),
            )
            self._repo.insert(stored)
            return {
                "scenario_id": scenario_id,
                "slug": definition.slug,
                "title": definition.meta.title,
                "summary": _complete_summary(parsed.summary),
                "warnings": [issue.as_dict() for issue in parsed.warnings],
            }

        result = self._idempotent(store_key, request_hash, _run)
        # 幂等重放（或进程重启后）也要把场景注册进 runtime，保证建局后钩子可用。
        self._register_scenario(str(result["scenario_id"]))
        return result

    def delete_scenario(self, scenario_id: str) -> None:
        """软删除场景（204 无 body）：列表/详情/新建局不可见；重复删除 → 404 ``NOT_FOUND``。

        不摘除 runtime 索引：已开局存档（含回放）仍需要按房间/NPC ID 解析到本场景钩子。
        """
        stored = self._repo.get(scenario_id, include_deleted=True)
        if stored is None or stored.deleted_at is not None:
            raise ScenarioNotFoundError(f"场景不存在: {scenario_id}")
        if not self._repo.soft_delete(scenario_id, deleted_at=_utc_now()):
            raise ScenarioNotFoundError(f"场景不存在: {scenario_id}")

    # ------------------------------------------------------------------
    # 建局载荷（Lead 集成用：GameEngine.create_game(initial_state=...)/orchestrator）
    # ------------------------------------------------------------------
    def build_game_payload(self, scenario_id: str, save_seed: str) -> dict[str, Any]:
        """把场景物化成建局所需数据；未知/已软删 → 404 ``NOT_FOUND``。

        返回 ``{"scenario_id", "scenario_title", "initial_state", "ids", "starting_room_id",
        "initial_character_id"}``：
        ``initial_state`` 是 ``GameState.model_dump(mode="json")``（喂给
        ``GameEngine.create_game(initial_state=...)``）；``ids`` 是 ``ScenarioIds``
        （作者 key → 派生领域 ID 的映射，求值/建议/叙事复用）；玩家槽位固定 ``PLAYER_ID``。
        """
        if not save_seed:
            raise ScenarioValidationError(
                "save_seed 不能为空",
                errors=[{"path": "save_seed", "message": "save_seed 不能为空"}],
            )
        stored = self._require_stored(scenario_id)
        built = self._hooks_of(stored)
        if built is None:  # pragma: no cover - 落库前已校验；损坏数据按不存在处理
            raise ScenarioNotFoundError(f"场景定义不可用: {scenario_id}")
        hooks, ids = built
        definition = hooks.definition
        try:
            state = build_initial_state(definition, ids, save_seed=save_seed)
        except ValueError as exc:
            # 旧库存定义可能绕过导入期 dry-run（R-02）：建局时给 422 而不是 500。
            raise ScenarioValidationError(
                "场景定义无法建局",
                errors=[{"path": "$", "message": f"场景定义无法建局：{exc}"}],
            ) from exc
        return {
            "scenario_id": stored.scenario_id,
            "scenario_title": definition.meta.title,
            "initial_state": state.model_dump(mode="json"),
            "ids": ids,
            "starting_room_id": ids.rooms[definition.player.location],
            "initial_character_id": PLAYER_ID,
        }

    # ------------------------------------------------------------------
    # 内部：行载荷 / runtime 注册 / 幂等
    # ------------------------------------------------------------------
    def _summary_payload(self, stored: StoredScenario) -> dict[str, Any]:
        """列表/详情公共字段（详情再追加 ``definition``）。"""
        return {
            "scenario_id": stored.scenario_id,
            "slug": stored.slug,
            "title": stored.title,
            "author": stored.author,
            "description": stored.description,
            "schema_version": stored.schema_version,
            "created_at": stored.created_at,
            "summary": _complete_summary(stored.summary),
            "warnings": [dict(item) for item in stored.warnings],
        }

    @staticmethod
    def _definition_payload(raw: bytes) -> dict[str, Any]:
        """把已校验的原文重新反序列化为原始 JSON 对象（存库/详情展示用）。"""
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:  # pragma: no cover
            raise ScenarioValidationError(
                "场景定义不是合法 JSON",
                errors=[{"path": "$", "message": "场景定义不是合法 JSON"}],
            ) from exc
        if not isinstance(payload, dict):  # pragma: no cover - parse_definition 已保证
            raise ScenarioValidationError(
                "场景定义顶层必须是 JSON 对象",
                errors=[{"path": "$", "message": "场景定义顶层必须是 JSON 对象"}],
            )
        return payload

    def _hooks_of(self, stored: StoredScenario) -> tuple[UserScenarioHooks, ScenarioIds] | None:
        """由存储行重建 ``(UserScenarioHooks, ScenarioIds)``；定义损坏返回 ``None``。"""
        parsed = parse_definition(canonical_json(stored.definition))
        if not parsed.ok or parsed.definition is None:
            return None
        definition: ScenarioDefinition = parsed.definition
        ids = derive_ids(stored.scenario_id, definition)
        return UserScenarioHooks(definition, ids), ids

    def _register_scenario(self, scenario_id: str) -> None:
        """把某场景注册进 runtime（未删除且定义可解析时）；重放/重启后也生效。"""
        stored = self._repo.get(scenario_id)
        if stored is None:
            return
        built = self._hooks_of(stored)
        if built is not None:
            self.runtime.register(built[0])

    def _require_stored(self, scenario_id: str) -> StoredScenario:
        """取未软删场景；ID 格式非法/未知/已删除 → 404 ``NOT_FOUND``。"""
        if not isinstance(scenario_id, str) or not _SCENARIO_ID_RE.match(scenario_id):
            raise ScenarioNotFoundError(f"场景不存在: {scenario_id}")
        stored = self._repo.get(scenario_id)
        if stored is None:
            raise ScenarioNotFoundError(f"场景不存在: {scenario_id}")
        return stored

    def _idempotent(
        self,
        store_key: str,
        request_hash: str,
        operation: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        """R-04：同 ``Idempotency-Key`` 的场景导入在进程内串行（单进程部署）。"""
        with idempotency_lock(store_key):
            return self._idempotent_unlocked(store_key, request_hash, operation)

    def _idempotent_unlocked(
        self,
        store_key: str,
        request_hash: str,
        operation: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        """同键同参回放首次响应；同键异参 → 409 ``CONFLICT``（同 CardService 口径）。

        成功响应落 ``idempotency`` 表；**校验失败不缓存**（玩家修正 JSON 后可用同一键重试，
        无需先换键），避免把一次手误固化成永久失败。
        """
        cached = self._repo.get_idempotent(store_key)
        if cached is not None:
            if cached.params_hash != request_hash:
                raise ScenarioConflictError("该 Idempotency-Key 已经用在不同参数的场景导入请求上")
            return dict(cached.payload)
        result = operation()
        self._repo.save_idempotent(
            IdempotencyRecord(key=store_key, params_hash=request_hash, payload=result)
        )
        return result


__all__ = [
    "LIST_DEFAULT_LIMIT",
    "LIST_MAX_LIMIT",
    "MAX_QUERY_LENGTH",
    "SUMMARY_KEYS",
    "ScenarioConflictError",
    "ScenarioNotFoundError",
    "ScenarioRepository",
    "ScenarioService",
    "ScenarioServiceError",
    "ScenarioValidationError",
    "StoredScenario",
    "new_scenario_id",
]
