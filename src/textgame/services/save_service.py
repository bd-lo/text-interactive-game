"""N3-048 存档导出/导入服务（单文件 JSON + 回放哈希校验 + 幂等导入，后端优先）。

设计要点（第 17 章 §17.9.2 N3-048、第 14 章 14.4/14.6、第 6 章 §6.7）：

- **导出包**是玩家手动保存/分享的单文件 JSON（``export_version="1"``）：只含一局游戏的
  全部已盖章事件 + 当前头信息 + 状态哈希；不读 ``Settings``/凭据/日志，因此包内天然没有
  API Key（事件只存领域数据与叙事文本）。
- **导入**先在写库前做纯校验（包结构/字段白名单/checksum/事件结构/完整回放一致性），
  全部通过后才走唯一写入者 ``GameEngine``：``create_game``（用包内首事件的
  ``initial_state``）+ 按回合 ``submit`` 逐字重放后续事件；新 ``game_id``、版本链与快照
  节奏与导出端一致，因此导入后的 ``state_hash`` 与导出端的声明值一致。
- **失败零写入**：任何输入校验失败都在写库前抛 :class:`SaveServiceError`（事件被篡改时
  checksum 或「回放得到的 ``state_hash``」对不上，直接拒绝）。写库阶段只剩基础设施级
  异常（磁盘满/进程崩溃），不在校验失败范畴。
- **幂等**：同 ``Idempotency-Key`` 同包 → 回放首次 201 响应；同键异包 → 409 ``CONFLICT``
  （与卡库/回合写端点同口径）。非法包不写幂等表，重复提交仍得到同一结构化错误。

包结构（``export_version="1"``）::

    {"export_version": "1", "exported_at": <UTC ISO 字符串>,
     "game":   {"game_id", "turn", "version", "snapshot_version", "save_seed",
                "scenario", "created_at", "event_count"},
     "events": [{"id", "seq", "turn_id", "actor_id", "method", "event_type",
                 "params", "result", "rng_seed", "ts"}, ...],
     "snapshot": {"state_hash", "version", "last_event_seq", "turn", "snapshot_version"},
     "checksum": "<sha256(canonical_json(包去掉 checksum))>"}

本模块只做校验/编解码/读写编排，不定义 HTTP 错误映射（接入层 ``api/routes/saves.py``
按 :class:`SaveServiceError` 统一转第 6 章失败信封）。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from textgame.domain.events import Event, EventLog, EventType
from textgame.domain.hashing import canonical_json, params_hash, state_hash
from textgame.domain.models import GameState
from textgame.domain.reducers import apply_events
from textgame.domain.results import ErrorCode
from textgame.services.game_engine import GameEngine
from textgame.services.idempotency_locks import idempotency_lock
from textgame.services.ports import GameRepository, IdempotencyRecord
from textgame.services.query_service import scenario_name_of_state

#: 导出包格式版本（未来格式升级只增不改；未知版本导入直接拒绝）。
EXPORT_VERSION = "1"

#: 单个存档包大小上限（导出与导入同口径：8MB；HTTP 层对该路径单独放行到 8MB+64KB）。
MAX_SAVE_PACKAGE_BYTES = 8 * 1024 * 1024

#: 单个存档包事件条数上限（防畸形包拖慢回放）。
MAX_SAVE_EVENTS = 20_000

#: 导入幂等存储键前缀（与卡库/开局/回合前缀互不干扰）。
_IMPORT_KEY_PREFIX = "save:import:"

_CHECKSUM_RE = re.compile(r"^[0-9a-f]{64}$")
_STATE_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_SAVE_SEED_MAX_CHARS = 64


class SaveServiceError(Exception):
    """存档导出/导入失败：携带稳定错误码、可读消息与可选字段级 violations。

    接入层按 :attr:`error_code` 映射第 6 章 HTTP 状态码（``status_override`` 用于
    「错误码相同但状态码不同」的特例，如请求体过大 413）。
    """

    def __init__(
        self,
        error_code: ErrorCode,
        message: str,
        *,
        violations: list[dict[str, Any]] | None = None,
        http_status: int | None = None,
    ) -> None:
        """保存错误码/消息/violations/可选 HTTP 状态覆盖值。"""
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.violations = violations
        self.http_status = http_status


# ---------------------------------------------------------------------------
# 导出包 Pydantic DTO（字段白名单：extra="forbid"）
# ---------------------------------------------------------------------------
class SaveEventEnvelope(BaseModel):
    """导出包内单条已盖章事件（与 ``domain.events.Event`` 字段一一对应）。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=32)
    seq: int = Field(ge=1)
    turn_id: int = Field(ge=1)
    actor_id: str = Field(min_length=1, max_length=64)
    method: str = Field(min_length=1, max_length=48)
    event_type: str = Field(min_length=1, max_length=48)
    params: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    rng_seed: int = Field(ge=0, le=0x7FFF_FFFF_FFFF_FFFF)
    ts: str = Field(min_length=1, max_length=64)


class SaveGameMeta(BaseModel):
    """导出包的存档头信息（只用于一致性校验/展示，导入时游戏 ID 一律重新分配）。"""

    model_config = ConfigDict(extra="forbid")

    game_id: str = Field(min_length=1, max_length=64)
    turn: int = Field(ge=1)
    version: int = Field(ge=1)
    snapshot_version: int = Field(ge=0)
    save_seed: str = Field(min_length=1, max_length=_SAVE_SEED_MAX_CHARS)
    scenario: str = Field(min_length=1, max_length=32)
    created_at: str | None = Field(default=None, max_length=64)
    event_count: int = Field(ge=1)


class SaveSnapshotHead(BaseModel):
    """导出端当前提交头（全量事件回放后必须与之一致）。"""

    model_config = ConfigDict(extra="forbid")

    state_hash: str = Field(min_length=64, max_length=64)
    version: int = Field(ge=1)
    last_event_seq: int = Field(ge=1)
    turn: int = Field(ge=1)
    snapshot_version: int = Field(ge=0)


class SavePackage(BaseModel):
    """单文件存档导出包（导入端结构白名单；语义一致性另经回放校验）。"""

    model_config = ConfigDict(extra="forbid")

    export_version: str = Field(min_length=1, max_length=16)
    exported_at: str = Field(min_length=1, max_length=64)
    game: SaveGameMeta
    events: list[SaveEventEnvelope] = Field(min_length=1, max_length=MAX_SAVE_EVENTS)
    snapshot: SaveSnapshotHead
    checksum: str = Field(min_length=64, max_length=64)


# ---------------------------------------------------------------------------
# 纯函数：规范化校验和与序列化
# ---------------------------------------------------------------------------
def package_checksum(package: Mapping[str, Any]) -> str:
    """对「包去掉 ``checksum`` 字段」做规范 JSON 哈希（导出写入/导入验算共用同一实现）。

    规范化口径复用 ``domain.hashing.params_hash``（sort_keys + 紧凑分隔符 +
    ``ensure_ascii=False`` + ``allow_nan=False``）；NaN/Infinity 会抛 ``ValueError``，
    由调用方映射为 422，不静默通过。
    """
    return params_hash({key: value for key, value in package.items() if key != "checksum"})


def package_byte_size(package: Mapping[str, Any]) -> int:
    """包规范 JSON 的 UTF-8 字节数（大小上限判据；NaN/Infinity 抛 ``ValueError``）。"""
    return len(canonical_json(dict(package)).encode("utf-8"))


def _utc_now_iso() -> str:
    """当前 UTC 时间的 ISO 文本（导出包 ``exported_at``）。"""
    return datetime.now(timezone.utc).isoformat()


def _violation(loc: Sequence[str | int], kind: str, message: str) -> dict[str, Any]:
    """构造与第 6 章失败信封同构的字段级 violation（``loc`` 一律转字符串）。"""
    return {"loc": [str(part) for part in loc], "type": kind, "msg": message}


def _pydantic_violations(exc: ValidationError) -> list[dict[str, Any]]:
    """把 Pydantic 校验错误裁剪成可 JSON 序列化的 ``{loc, type, msg}``（与 api.errors 同构）。"""
    return [
        _violation(error.get("loc", ()), str(error.get("type", "")), str(error.get("msg", "")))
        for error in exc.errors()
    ]


def _event_to_payload(event: Event) -> dict[str, Any]:
    """已盖章 ``Event`` → 导出包内的事件 JSON（未完全盖章属存储层内部错误）。"""
    if (
        event.id is None
        or event.seq is None
        or event.rng_seed is None
        or event.ts is None
        or event.event_type is None
    ):
        raise SaveServiceError(ErrorCode.INTERNAL, "存档事件未完全盖章，无法导出")
    return {
        "id": event.id,
        "seq": event.seq,
        "turn_id": event.turn_id,
        "actor_id": event.actor_id,
        "method": event.method,
        "event_type": event.event_type.value,
        "params": event.params,
        "result": event.result,
        "rng_seed": event.rng_seed,
        "ts": event.ts.isoformat(),
    }


def _group_events_by_turn(events: Sequence[Event]) -> list[list[Event]]:
    """按 ``turn_id`` 把事件分组（同回合多角色事件保持原 ``seq`` 顺序）。"""
    groups: list[list[Event]] = []
    for event in events:
        if groups and groups[-1][0].turn_id == event.turn_id:
            groups[-1].append(event)
        else:
            groups.append([event])
    return groups


def _to_draft(event: Event) -> Event:
    """已盖章事件 → 草稿事件（去掉 ``id/seq/rng_seed/ts``，交给 ``GameEngine`` 重新盖章）。

    ``GameEngine.submit`` 只用草稿态；重新盖章时 ``rng_seed`` 仍由
    ``(save_seed, turn_id, actor_id, method, seq)`` 派生，因此与导出端逐值一致，仅 ``ts``
    变为导入时刻（时间不参与状态哈希）。
    """
    payload = event.model_dump(mode="json")
    payload.update({"id": None, "seq": None, "rng_seed": None, "ts": None})
    return Event.model_validate(payload)


class SaveService:
    """存档导出/导入编排（只读导出 + 校验后经 ``GameEngine`` 写入的导入）。"""

    def __init__(self, repo: GameRepository, engine: GameEngine) -> None:
        """注入仓储（读事件/快照/幂等）与唯一写入者（建局 + 逐回合提交）。"""
        self._repo = repo
        self._engine = engine

    # ------------------------------------------------------------------
    # 导出（只读：不改库、不写事件、不写快照）
    # ------------------------------------------------------------------
    def export_save(self, game_id: str) -> dict[str, Any]:
        """导出一局游戏为单文件 JSON 包（``export_version="1"``）。

        只读 ``GameRepository``（当前状态/事件/最新快照）；未知存档 ``NOT_FOUND``（接入层
        404）。包大小/事件数超上限时拒绝导出（防止生成无法导入的畸形包）。
        """
        record = self._repo.load(game_id)
        if record is None:
            raise SaveServiceError(ErrorCode.NOT_FOUND, f"存档不存在: {game_id}")
        events = self._repo.read_events(game_id)
        if not events:
            raise SaveServiceError(ErrorCode.INTERNAL, "存档事件日志为空，无法导出")
        if len(events) > MAX_SAVE_EVENTS:
            raise SaveServiceError(
                ErrorCode.OUT_OF_RANGE,
                f"存档事件过多（{len(events)} 条，上限 {MAX_SAVE_EVENTS}），无法导出为单文件",
            )
        latest_snapshot = self._repo.latest_snapshot(game_id)
        snapshot_version = latest_snapshot.snapshot_version if latest_snapshot is not None else 0
        state = record.state
        first = events[0]
        package: dict[str, Any] = {
            "export_version": EXPORT_VERSION,
            "exported_at": _utc_now_iso(),
            "game": {
                "game_id": game_id,
                "turn": state.turn,
                "version": record.version,
                "snapshot_version": snapshot_version,
                "save_seed": state.rng_state.save_seed,
                "scenario": scenario_name_of_state(state),
                "created_at": first.ts.isoformat() if first.ts is not None else None,
                "event_count": len(events),
            },
            "events": [_event_to_payload(event) for event in events],
            "snapshot": {
                "state_hash": state_hash(state),
                "version": record.version,
                "last_event_seq": record.last_seq,
                "turn": state.turn,
                "snapshot_version": snapshot_version,
            },
        }
        package["checksum"] = package_checksum(package)
        if package_byte_size(package) > MAX_SAVE_PACKAGE_BYTES:
            raise SaveServiceError(
                ErrorCode.OUT_OF_RANGE,
                f"存档包超过 {MAX_SAVE_PACKAGE_BYTES // (1024 * 1024)}MB 上限，"
                "无法导出为单文件；请改用同库续玩",
            )
        return package

    # ------------------------------------------------------------------
    # 导入（先校验零写入，再经唯一写入者重放写库）
    # ------------------------------------------------------------------
    def import_save(self, package: Mapping[str, Any], *, idempotency_key: str) -> dict[str, Any]:
        """把单文件存档包导入为**新的一局**（幂等键语义与既有写端点一致）。

        - 同键同包 → 回放首次响应（不重复建局）；
        - 同键异包 → ``CONFLICT``（409）；
        - 包非法/被篡改 → 422 结构化 ``violations`` 且**零写入**；
        - 成功 → 返回 ``{game_id, turn, version, snapshot_version}``（新 ``game_id``）。
        """
        try:
            request_hash = params_hash(dict(package))
        except (TypeError, ValueError) as exc:
            raise SaveServiceError(
                ErrorCode.SCHEMA_VIOLATION,
                "存档包含非法数值，无法校验",
                violations=[_violation(("checksum",), "invalid_nan", "包内含 NaN/Infinity")],
            ) from exc
        store_key = f"{_IMPORT_KEY_PREFIX}{idempotency_key}"
        with idempotency_lock(store_key):
            cached = self._repo.get_idempotent(store_key)
            if cached is not None:
                if cached.params_hash != request_hash:
                    raise SaveServiceError(
                        ErrorCode.CONFLICT,
                        "该 Idempotency-Key 已经导入过不同的存档包",
                    )
                return dict(cached.payload)
            result = self._import_validated(dict(package))
            self._repo.save_idempotent(
                IdempotencyRecord(key=store_key, params_hash=request_hash, payload=result)
            )
            return result

    def _import_validated(self, package: dict[str, Any]) -> dict[str, Any]:
        """校验包并重放写库；校验失败抛 :class:`SaveServiceError`（此时零写入）。"""
        self._verify_checksum(package)
        self._verify_limits(package)
        parsed = self._parse_package(package)
        if parsed.export_version != EXPORT_VERSION:
            raise SaveServiceError(
                ErrorCode.INVALID_INPUT,
                f"不支持的存档包版本: {parsed.export_version}（当前支持 {EXPORT_VERSION}）",
                violations=[_violation(("export_version",), "unsupported_version", "版本不受支持")],
            )
        events = self._decode_events(parsed.events)
        save_seed = parsed.game.save_seed
        self._replay_and_verify(events, parsed, save_seed)
        game_id = self._write_replayed_game(events, save_seed)
        return self._read_import_result(game_id)

    # -- 校验（全部在写库之前） -----------------------------------------
    def _verify_checksum(self, package: Mapping[str, Any]) -> None:
        """先验校验和：任何字段被改动都会与导出端哈希不一致（篡改直接拒绝）。"""
        declared = package.get("checksum")
        if not isinstance(declared, str) or _CHECKSUM_RE.fullmatch(declared) is None:
            raise SaveServiceError(
                ErrorCode.SCHEMA_VIOLATION,
                "存档包缺少合法的校验和",
                violations=[_violation(("checksum",), "missing", "checksum 必须为 64 位小写 hex")],
            )
        try:
            actual = package_checksum(package)
        except (TypeError, ValueError) as exc:
            raise SaveServiceError(
                ErrorCode.SCHEMA_VIOLATION,
                "存档包含非法数值，无法校验",
                violations=[_violation(("checksum",), "invalid_nan", "包内含 NaN/Infinity")],
            ) from exc
        if actual != declared:
            raise SaveServiceError(
                ErrorCode.SCHEMA_VIOLATION,
                "存档包校验和不匹配（内容可能被篡改）",
                violations=[_violation(("checksum",), "checksum_mismatch", "内容与校验和不一致")],
            )

    def _verify_limits(self, package: Mapping[str, Any]) -> None:
        """大小/事件条数上限（HTTP 层另有 8MB+报文头的请求体上限）。"""
        try:
            size = package_byte_size(package)
        except (TypeError, ValueError) as exc:
            raise SaveServiceError(
                ErrorCode.SCHEMA_VIOLATION,
                "存档包含非法数值，无法校验",
                violations=[_violation(("events",), "invalid_nan", "包内含 NaN/Infinity")],
            ) from exc
        if size > MAX_SAVE_PACKAGE_BYTES:
            raise SaveServiceError(
                ErrorCode.INVALID_INPUT,
                f"存档包超过 {MAX_SAVE_PACKAGE_BYTES // (1024 * 1024)}MB 上限",
                http_status=413,
                violations=[_violation(("package",), "too_large", "包体积超过上限")],
            )
        raw_events = package.get("events")
        count = len(raw_events) if isinstance(raw_events, list) else 0
        if count > MAX_SAVE_EVENTS:
            raise SaveServiceError(
                ErrorCode.INVALID_INPUT,
                f"存档包事件过多（{count} 条，上限 {MAX_SAVE_EVENTS}）",
                violations=[_violation(("events",), "too_many", "事件条数超过上限")],
            )

    def _parse_package(self, package: dict[str, Any]) -> SavePackage:
        """按字段白名单解析包结构（未知字段/类型/范围 → 422 结构化 violations）。"""
        try:
            return SavePackage.model_validate(package)
        except ValidationError as exc:
            raise SaveServiceError(
                ErrorCode.SCHEMA_VIOLATION,
                "存档包结构不合法",
                violations=_pydantic_violations(exc),
            ) from exc

    def _decode_events(self, envelopes: Sequence[SaveEventEnvelope]) -> list[Event]:
        """包内事件 → 领域 ``Event``（复用 Event 模型校验；未知事件类型直接拒绝）。"""
        events: list[Event] = []
        violations: list[dict[str, Any]] = []
        for index, envelope in enumerate(envelopes):
            try:
                event = Event.model_validate(envelope.model_dump(mode="json"))
            except ValidationError as exc:
                for item in _pydantic_violations(exc):
                    location = item["loc"]
                    item["loc"] = [str(index), *location]
                    violations.append(item)
                continue
            if event.event_type is None:
                violations.append(
                    _violation(("events", index, "event_type"), "missing", "事件缺少类型")
                )
                continue
            events.append(event)
        if violations:
            raise SaveServiceError(
                ErrorCode.SCHEMA_VIOLATION, "存档事件结构不合法", violations=violations
            )
        return events

    def _replay_and_verify(
        self, events: list[Event], parsed: SavePackage, save_seed: str
    ) -> GameState:
        """全量回放事件并校验与包内声明的头信息一致（state_hash 是最终篡改判据）。"""
        first = events[0]
        if first.event_type is not EventType.GAME_CREATED:
            raise SaveServiceError(
                ErrorCode.SCHEMA_VIOLATION,
                "存档包首条事件不是 game_created，无法回放",
                violations=[
                    _violation(("events", 0, "event_type"), "missing", "首条必须为 game_created")
                ],
            )
        verifier = EventLog(save_seed)
        try:
            verifier.append(events)
            replayed = verifier.replay()
        except ValueError as exc:
            raise SaveServiceError(
                ErrorCode.SCHEMA_VIOLATION,
                "存档事件回放校验失败（事件可能被篡改）",
                violations=[
                    _violation(("events",), "replay_mismatch", "事件序号/随机种子与存档种子不一致")
                ],
            ) from exc
        try:
            state = apply_events(None, replayed)
        except ValueError as exc:
            raise SaveServiceError(
                ErrorCode.SCHEMA_VIOLATION,
                "存档事件无法回放为合法状态（事件可能被篡改）",
                violations=[_violation(("events",), "projection_failed", "事件无法投影为游戏状态")],
            ) from exc
        try:
            actual_hash = state_hash(state)
        except ValueError as exc:
            raise SaveServiceError(
                ErrorCode.SCHEMA_VIOLATION,
                "存档状态含非法数值，无法校验",
                violations=[
                    _violation(("snapshot", "state_hash"), "invalid_nan", "状态含 NaN/Infinity")
                ],
            ) from exc
        violations: list[dict[str, Any]] = []
        if actual_hash != parsed.snapshot.state_hash:
            violations.append(
                _violation(
                    ("snapshot", "state_hash"),
                    "state_hash_mismatch",
                    "事件回放得到的状态哈希与包内声明不一致",
                )
            )
        rebuilt_version = len({event.turn_id for event in replayed})
        if parsed.snapshot.version != rebuilt_version:
            violations.append(
                _violation(("snapshot", "version"), "version_mismatch", "版本与事件回放不一致")
            )
        if parsed.game.version != rebuilt_version:
            violations.append(
                _violation(("game", "version"), "version_mismatch", "版本与事件回放不一致")
            )
        if parsed.snapshot.turn != state.turn:
            violations.append(
                _violation(("snapshot", "turn"), "turn_mismatch", "回合数与事件回放不一致")
            )
        if parsed.game.turn != state.turn:
            violations.append(_violation(("game", "turn"), "turn_mismatch", "回合数与回放不一致"))
        last_seq = replayed[-1].seq
        if parsed.snapshot.last_event_seq != last_seq:
            violations.append(
                _violation(
                    ("snapshot", "last_event_seq"), "seq_mismatch", "末事件序号与包内声明不一致"
                )
            )
        if parsed.game.event_count != len(replayed):
            violations.append(
                _violation(("game", "event_count"), "count_mismatch", "事件条数与包内声明不一致")
            )
        if parsed.game.save_seed != state.rng_state.save_seed:
            violations.append(
                _violation(("game", "save_seed"), "save_seed_mismatch", "存档种子与回放状态不一致")
            )
        if parsed.game.snapshot_version != parsed.snapshot.snapshot_version:
            violations.append(
                _violation(("game", "snapshot_version"), "snapshot_mismatch", "快照版本不一致")
            )
        violations.extend(self._turn_structure_violations(replayed))
        if violations:
            raise SaveServiceError(
                ErrorCode.SCHEMA_VIOLATION, "存档包与回放结果不一致", violations=violations
            )
        return state

    @staticmethod
    def _turn_structure_violations(events: Sequence[Event]) -> list[dict[str, Any]]:
        """回合结构校验：从 1 起连续递增、同一回合事件必须相邻。"""
        violations: list[dict[str, Any]] = []
        seen: set[int] = set()
        current: int | None = None
        expected_turn = 1
        for index, event in enumerate(events):
            if event.turn_id == current:
                continue
            if event.turn_id in seen:
                violations.append(
                    _violation(
                        ("events", index, "turn_id"), "turn_not_contiguous", "同一回合事件必须相邻"
                    )
                )
                continue
            if event.turn_id != expected_turn:
                violations.append(
                    _violation(
                        ("events", index, "turn_id"),
                        "turn_gap",
                        f"回合序号应从 1 连续递增（期望 {expected_turn}）",
                    )
                )
            seen.add(event.turn_id)
            current = event.turn_id
            expected_turn = event.turn_id + 1
        return violations

    # -- 写库（唯一写入者：create_game + 逐回合 submit 重放） --------------
    def _write_replayed_game(self, events: list[Event], save_seed: str) -> str:
        """按包内 ``initial_state`` 建局，再按回合重放后续事件（全部经 ``GameEngine``）。

        返回新分配的 ``game_id``。校验阶段已保证事件可投影、版本连续，因此这里只有
        基础设施级异常可能中断（无法回滚，属于运维问题而非输入校验失败）。
        """
        initial_state = events[0].result.get("initial_state")
        if not isinstance(initial_state, dict):
            raise SaveServiceError(
                ErrorCode.SCHEMA_VIOLATION,
                "存档包首事件缺少初始状态，无法导入",
                violations=[
                    _violation(("events", 0, "result", "initial_state"), "missing", "缺少初始状态")
                ],
            )
        created = self._engine.create_game(
            save_seed=save_seed,
            scenario="custom",
            initial_state=initial_state,
        )
        if not created.ok or created.data is None:
            raise SaveServiceError(ErrorCode.INTERNAL, "导入失败：游戏引擎没有完成新存档创建")
        game_id = created.data.get("game_id")
        if not isinstance(game_id, str) or not game_id:
            raise SaveServiceError(ErrorCode.INTERNAL, "导入失败：新存档标识无效")
        expected_version = 1
        for turn_events in _group_events_by_turn(events[1:]):
            drafts = [_to_draft(event) for event in turn_events]
            submitted = self._engine.submit(
                game_id=game_id,
                expected_version=expected_version,
                drafts=drafts,
            )
            if not submitted.ok:
                raise SaveServiceError(
                    ErrorCode.INTERNAL,
                    "导入失败：存档事件写入中断（请联系维护，勿重复导入）",
                )
            expected_version += 1
        return game_id

    def _read_import_result(self, game_id: str) -> dict[str, Any]:
        """读取导入后的存档头并组成 201 响应。"""
        record = self._repo.load(game_id)
        if record is None:  # pragma: no cover - 引擎刚提交成功，读取不应失败
            raise SaveServiceError(ErrorCode.INTERNAL, "导入失败：新存档读取不到")
        snapshot = self._repo.latest_snapshot(game_id)
        return {
            "game_id": game_id,
            "turn": record.state.turn,
            "version": record.version,
            "snapshot_version": snapshot.snapshot_version if snapshot is not None else 0,
        }


__all__ = [
    "EXPORT_VERSION",
    "MAX_SAVE_EVENTS",
    "MAX_SAVE_PACKAGE_BYTES",
    "SavePackage",
    "SaveService",
    "SaveServiceError",
    "package_byte_size",
    "package_checksum",
]
