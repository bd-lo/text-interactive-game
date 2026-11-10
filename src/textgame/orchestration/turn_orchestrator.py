"""回合编排 ``TurnOrchestrator``（第 9 章 §9.2：M2 单角色裁剪版 + M3 多角色版）。

依赖全部由构造注入（``repo/engine/query/action/parser/gate/narrator/rules``）；M3 再注入
``registry/resolver/actors/character_agents/audit`` 后自动切换到「提案 → 队列 → 裁决 →
CAS 提交 → 审计」的多角色链路（第 11 章 §11.1–§11.3、契约 ``.work/m3-contract.md`` §4）。

- **M2 兼容路径**：未注入 M3 依赖（``resolver``/``actors``/``registry`` 其一为 ``None``）时，
  完全走 M2 单角色流程，行为/事件序列/幂等与 M2 契约一致（M2 测试不回归）。
- **M3 多角色路径**：玩家破坏性动作与确定性角色 Agent 的提案一起进入 ``ProposalQueue``，
  由纯函数 ``Resolver`` 统一裁决；只有 ``applied`` 裁决的草稿事件才交给唯一写入者
  ``GameEngine.submit``（CAS，最多 3 次尝试；重试只重裁决、不重解析/不重叙事）。
- 本类只负责串流程、幂等、错误码映射与审计落库，不做规则计算、不写状态、不调模型。
"""

from __future__ import annotations

import threading
import zlib
from collections.abc import Mapping, Sequence
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from textgame.agents.dto import ParseOutcome
from textgame.config.rng import save_seed_from_settings
from textgame.domain.events import Event, EventType
from textgame.domain.hashing import params_hash
from textgame.domain.models import (
    GameState,
    PerceptionSlice,
    Proposal,
    Resolution,
    SideEffect,
    ToolSpec,
)
from textgame.domain.results import ErrorCode, Result
from textgame.domain.scenario import DIRECTION_NAMES, PLAYER_ID
from textgame.orchestration.direct_gate import ToolGateLike
from textgame.orchestration.dto import TurnRequest
from textgame.orchestration.proposal_queue import (
    ProposalQueue,
    ProposalSource,
    QueueEntry,
)
from textgame.services.action_service import ActionService
from textgame.services.actor_directory import ActorDirectory
from textgame.services.dto import TurnResult
from textgame.services.game_engine import GameEngine
from textgame.services.ports import AuditRecord, GameRecord, GameRepository, IdempotencyRecord
from textgame.services.query_service import QueryService
from textgame.services.tool_context import ToolContext

_MIN_TEXT_CHARS = 80
_MAX_TEXT_CHARS = 150
_CONFIDENCE_FLOOR = 0.6
_DEFAULT_SAVE_SEED = "save-42"
_MAX_SUBMIT_ATTEMPTS = 3
# 幂等键分段锁条数（D-M4-15/G-1）：同键请求串行、不同键基本并行；固定条数避免
# 锁字典随请求键增长（不回收、无泄漏），哈希碰撞只造成无关键偶发串行。
_IDEMPOTENCY_LOCK_STRIPES = 64

_UNKNOWN_ACTION_TEXT = (
    "你试着比划了一下，却没弄明白自己想做什么。"
    "也许可以换个说法：环顾四周、朝某个方向走、拿起地上的东西，或者和身边的人说说话。"
)
_IDEMPOTENCY_CONFLICT_TEXT = "这个幂等键已经用在不同参数的回合请求上，请换一个新的键重试。"
_VERSION_CONFLICT_TEXT = "你的对局版本已经过期，请刷新对局视图后重试。"
_GAME_NOT_FOUND_TEXT = "存档不存在，请先创建一个新的游戏。"
_ACTION_REJECTED_TEXT = "这个动作没有生效，你停下了脚步。"
_INTERNAL_ERROR_TEXT = "回合处理发生内部错误，本回合没有写入任何状态。"
_SUBMIT_CONFLICT_TEXT = "对局状态被并发修改，本回合未能提交，请刷新后重试。"

_FILLER: tuple[str, ...] = (
    "风从断裂的门楣间穿过，卷起细碎的石屑。",
    "你放轻呼吸，留意着四周的动静。",
    "远处隐约传来水滴落下的回声。",
    "空气里浮着灰尘与旧木头的气味。",
)


class NarratorLike(Protocol):
    """叙事器端口（007 的 ``TemplateNarrator``/叙事 Agent 对齐此签名）。"""

    def render(
        self,
        *,
        action: ParseOutcome,
        events: list[Event],
        state: GameState,
        result_data: dict[str, Any],
    ) -> str:
        """把本回合事件渲染成 80–150 字中文屏文本（确定性优先）。"""
        ...


class IntentParserLike(Protocol):
    """解析器端口；确定性关键词解析与 006 的 ``IntentParseAgent`` 共用此签名。"""

    def parse(self, text: str, *, perception: PerceptionSlice, ctx: ToolContext) -> ParseOutcome:
        """把玩家输入解析为工具 + 参数 + 置信度（``ctx`` 供 LLM 请求携带回合上下文）。"""
        ...


class RuleEngineLike(Protocol):
    """007 ``RuleEngine`` 的前置判定接口（签名与 ``domain.rules.engine.RuleEngine`` 一致）。"""

    def evaluate_preconditions(
        self,
        *,
        tool: str,
        args: dict[str, Any],
        actor_id: str,
        turn_id: int,
        save_seed: str,
        state: GameState,
    ) -> Result:
        """确定性前置判定；失败返回 ``Result.fail(...)``，成功 ``data={"allowed": True}``。"""
        ...


class ToolSpecsLike(Protocol):
    """工具声明查询端口（``ToolRegistry`` 的最小子集）；未知工具抛 ``KeyError``。"""

    def get(self, name: str) -> ToolSpec: ...

    def is_visible(self, name: str, ctx: ToolContext) -> bool: ...

    def validate_args(self, name: str, args: dict[str, Any]) -> Result: ...


class ResolverLike(Protocol):
    """013 ``Resolver`` 的纯函数裁决端口（第 11 章 §11.2、第 21 章 §21.2.2）。"""

    def resolve(
        self,
        entries: Sequence[QueueEntry],
        snapshot: GameState,
        judgements: Sequence[Any] | None = None,
    ) -> list[Resolution]: ...


class CharacterAgentLike(Protocol):
    """确定性角色 Agent 端口（第 8 章 CharacterAgent 的 M3 裁剪版）。"""

    def propose(
        self, perception: PerceptionSlice, *, turn_id: int, save_seed: str
    ) -> list[Proposal]: ...


class AuditRecorderLike(Protocol):
    """审计写入端口（``AuditService.save``；审计失败不得阻塞回合）。"""

    def save(self, records: Sequence[AuditRecord]) -> None: ...


class CreateGameRequest(BaseModel):
    """``POST /api/v1/games`` 的编排层请求（``seed`` 可省略，缺省用组合根默认种子）。

    ``scenario`` 支持 ``m2``/``m3``/``m4``（M4 制造场景，D-M4-11），缺省 ``m2``
    保持 M2 契约与固定种子回放不变。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    seed: int | None = None
    idempotency_key: str = Field(min_length=8, max_length=128)
    scenario: Literal["m2", "m3", "m4"] = "m2"


class GameView(BaseModel):
    """开局视图；``POST /games`` 返回（``save_seed``）与幂等回放共用。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    game_id: str
    turn: int
    version: int
    snapshot_version: int
    status: Literal["active", "ended"] = "active"
    last_text: str | None = None
    save_seed: str | None = None


class CreateGameError(Exception):
    """开局失败（如幂等键异参 409）；接入层映射为错误信封。"""

    def __init__(self, error_code: ErrorCode, message: str) -> None:
        """携带稳定错误码与可读消息，供接入层直接映射 HTTP 状态码。"""
        super().__init__(message)
        self.error_code = error_code
        self.message = message


def _direction_name(direction: str) -> str:
    """Direction 英文值 → 中文方向词。"""
    return DIRECTION_NAMES.get(direction, direction)


def _room_title(state: GameState, room_id: str | None) -> str:
    """房间 id → 展示用房间名；未知房间回退为「这里」。"""
    if room_id is None:
        return "这里"
    room = state.rooms.get(room_id)
    return room.name if room is not None else "这里"


def _entity_name(state: GameState, item_id: str) -> str:
    """物品 id → 展示用名称；未知物品回退为「那件物品」。"""
    item = state.items.get(item_id)
    return item.name if item is not None else "那件物品"


def _npc_name(state: GameState, npc_id: str) -> str:
    """NPC id → 展示用名称；未知 NPC 回退为「那个人」。"""
    npc = state.npcs.get(npc_id)
    return npc.name if npc is not None else "那个人"


def _actor_name(state: GameState, actor_id: str) -> str:
    """任意 actor（Character 或 NPC）id → 展示用名称。"""
    character = state.characters.get(actor_id)
    if character is not None:
        return character.name
    npc = state.npcs.get(actor_id)
    if npc is not None:
        return npc.name
    return "有人"


def _fit_text(text: str) -> str:
    """把文本确定性收敛到 80–150 字（不足补固定短句，超出截断）。"""
    if len(text) > _MAX_TEXT_CHARS:
        return text[:_MAX_TEXT_CHARS]
    result = text
    index = 0
    while len(result) < _MIN_TEXT_CHARS:
        result += _FILLER[index % len(_FILLER)]
        index += 1
    return result[:_MAX_TEXT_CHARS]


def _fallback_text(
    *,
    action: ParseOutcome,
    events: list[Event],
    state: GameState,
    result_data: dict[str, Any],
) -> str:
    """确定性模板叙事（005 默认实现）：按事件类型拼一段 80–150 字中文文本。"""
    if events:
        event = events[0]
        event_type = event.event_type
        if event_type is EventType.CHARACTER_MOVED:
            direction = str(event.params.get("direction", ""))
            to_room = event.result.get("to", event.params.get("to_room"))
            text = (
                f"你沿着{_direction_name(direction)}边的小路动身，脚步落在潮湿的石板上。"
                f"不多时，{_room_title(state, to_room if isinstance(to_room, str) else None)}"
                "出现在眼前。"
            )
        elif event_type is EventType.ITEM_TAKEN:
            item_id = str(event.params.get("item_id", ""))
            item_name = _entity_name(state, item_id)
            text = (
                f"你弯下腰，把{item_name}从地上拿了起来，指尖沾了一层薄灰。"
                f"{item_name}现在稳稳躺在你的行囊里，随身带着总有用处。"
            )
        elif event_type is EventType.ITEM_CONSUMED:
            item_id = str(event.params.get("item_id", ""))
            item_name = _entity_name(state, item_id)
            text = (
                f"你取出{item_name}，按照记忆里的用法将它用掉。"
                "一股温热从喉咙滑下去，先前的疲惫被压住了一些。"
                "你活动了一下手腕，重新留意四周的动静。"
            )
        elif event_type is EventType.DIALOGUE_EXCHANGED:
            npc_id = str(event.params.get("npc_id", ""))
            npc_name = _npc_name(state, npc_id)
            topic = str(event.params.get("topic", ""))
            text = (
                f"你转向{npc_name}，把心里的话说了出来：「{topic}」。"
                f"{npc_name}沉默片刻，抬眼打量你，随后缓缓点了点头，"
                "似乎在斟酌该如何回应。"
            )
        else:
            text = "你完成了这个动作，四周的景物没有太大的变化，只有火把的光轻轻晃了晃。"
        return _fit_text(text)

    # READ_ONLY：look 的 ``data["text"]`` 已是房间描述，直接补足长度。
    base = str(result_data.get("text") or "")
    if not base:
        character = state.characters.get(PLAYER_ID)
        if character is not None:
            room = state.rooms.get(character.location)
            if room is not None:
                base = f"你环顾四周，{room.name}的光线有些昏暗。{room.description}"
    if not base:
        base = "你环顾四周，四周静悄悄的，只有风穿过废墟的缝隙。"
    return _fit_text(base)


def _npc_event_clause(state: GameState, events: Sequence[Event]) -> str:
    """把非玩家角色的 applied 事件渲染成一句确定性中文短句（M3 多角色叙事）。"""
    if not events:
        return ""
    parts: list[str] = []
    for event in events:
        name = _actor_name(state, event.actor_id)
        if event.event_type is EventType.CHARACTER_MOVED:
            direction = str(event.params.get("direction", ""))
            parts.append(f"{name}也朝{_direction_name(direction)}边挪了过去")
        elif event.event_type is EventType.ITEM_TAKEN:
            item_id = str(event.params.get("item_id", ""))
            parts.append(f"{name}伸手把{_entity_name(state, item_id)}拿走了")
        elif event.event_type is EventType.ITEM_CONSUMED:
            item_id = str(event.params.get("item_id", ""))
            parts.append(f"{name}用掉了{_entity_name(state, item_id)}")
        elif event.event_type is EventType.DIALOGUE_EXCHANGED:
            parts.append(f"{name}低声说了几句")
        else:
            parts.append(f"{name}也动了一下")
    return "与此同时，" + "；".join(parts) + "。"


def _compose_text(base: str, clause: str) -> str:
    """把玩家叙事与 NPC 短句合成 80–150 字文本（先给 NPC 句留位，再补足/截断）。"""
    if not clause:
        return _fit_text(base)
    max_base = max(_MIN_TEXT_CHARS - len(clause), 0)
    trimmed = base[:max_base] if len(base) > max_base else base
    return _fit_text(trimmed + clause)


def _turn_store_key(request: TurnRequest) -> str:
    """回合幂等存储键（契约 §5.2：``turn:{game_id}:{key}``）。"""
    return f"turn:{request.game_id}:{request.idempotency_key}"


def _turn_params_hash(request: TurnRequest) -> str:
    """同键异参判定的参数哈希（契约 §4.5：game_id + input + expected_version）。"""
    return params_hash(
        {
            "game_id": request.game_id,
            "input": request.text,
            "expected_version": request.expected_version,
        }
    )


def _idempotency_lock_index(store_key: str) -> int:
    """幂等存储键 → 分段锁下标（crc32 稳定哈希；同键必同下标，跨进程不影响正确性）。"""
    return zlib.crc32(store_key.encode("utf-8")) % _IDEMPOTENCY_LOCK_STRIPES


#: 失败回合的结构化提示（M6 加法字段 effects/suggested_actions；**不改 ``text`` 原文**）。
_FAILURE_HINTS: dict[ErrorCode, tuple[str, tuple[str, ...]]] = {
    ErrorCode.INVALID_INPUT: (
        "这个动作没有被理解或当前无法执行，可以换个说法再试。",
        ("环顾四周", "查看背包", "拿起火把", "对老哨兵问好"),
    ),
    ErrorCode.CONFLICT: (
        "对局状态已经变化（版本过期或同一幂等键参数不同），请刷新对局视图后重试。",
        ("环顾四周", "查看背包", "刷新对局视图"),
    ),
    ErrorCode.NOT_FOUND: ("存档不存在，请先创建新的游戏。", ("环顾四周", "拿起火把", "查看背包")),
    ErrorCode.NOT_ALLOWED: ("当前角色无权执行这个动作。", ("环顾四周", "查看背包", "对老哨兵问好")),
    ErrorCode.OUT_OF_RANGE: ("目标不在可达范围内。", ("环顾四周", "查看背包", "向北走进地窖")),
}
_DEFAULT_FAILURE_HINT: tuple[str, tuple[str, ...]] = (
    "本回合没有生效，可以换一种说法或先观察四周。",
    ("环顾四周", "查看背包", "对老哨兵问好"),
)


def _with_failure_hints(result: TurnResult) -> TurnResult:
    """给失败 ``TurnResult`` 填 ``effects``/``suggested_actions``（纯加法）。

    只读取已有 ``error_code`` 映射固定中文提示，不拼接内部错误消息、不改 ``text``、
    不产生事件，因此对 golden/回放零影响（字段原本恒空、回放不比对）。
    """
    code = result.error_code
    hint, actions = (
        _FAILURE_HINTS.get(code, _DEFAULT_FAILURE_HINT)
        if code is not None
        else _DEFAULT_FAILURE_HINT
    )
    return result.model_copy(
        update={"effects": [{"hint": hint}], "suggested_actions": list(actions)}
    )


class TurnOrchestrator:
    """单回合编排器；依赖全部构造注入，M2 依赖与 M3 依赖可分别装配。"""

    def __init__(
        self,
        *,
        repo: GameRepository,
        engine: GameEngine,
        query: QueryService,
        action: ActionService,
        parser: IntentParserLike,
        gate: ToolGateLike,
        narrator: NarratorLike | None = None,
        rules: RuleEngineLike | None = None,
        save_seed_default: str = _DEFAULT_SAVE_SEED,
        registry: ToolSpecsLike | None = None,
        resolver: ResolverLike | None = None,
        actors: ActorDirectory | None = None,
        character_agents: Mapping[str, CharacterAgentLike] | None = None,
        audit: AuditRecorderLike | None = None,
    ) -> None:
        """装配依赖；``narrator=None`` 时使用内置确定性模板叙事。

        注入 ``registry``/``resolver``/``actors`` 三者后启用 M3 多角色链路；缺省保持 M2
        单角色链路（默认装配行为与 M2 完全一致，M2 测试不回归）。
        """
        self._repo = repo
        self._engine = engine
        self._query = query
        self._action = action
        self._parser = parser
        self._gate = gate
        self._narrator = narrator
        self._rules = rules
        self._save_seed_default = save_seed_default
        self._registry = registry
        self._resolver = resolver
        self._actors = actors
        self._character_agents: dict[str, CharacterAgentLike] = dict(character_agents or {})
        self._audit = audit
        # D-M4-15/G-1：同 Idempotency-Key 的回合在进程内串行，避免两个线程同时未命中
        # 幂等缓存、后到者拿到 CONFLICT/INVALID_INPUT 而非首结果；固定分段避免锁增长。
        self._idempotency_locks = tuple(threading.Lock() for _ in range(_IDEMPOTENCY_LOCK_STRIPES))

    @property
    def multi_actor_enabled(self) -> bool:
        """是否启用 M3 多角色链路（三个 M3 依赖都注入时）。"""
        return (
            self._registry is not None and self._resolver is not None and self._actors is not None
        )

    # ------------------------------------------------------------------
    # 开局
    # ------------------------------------------------------------------
    def create_game(self, request: CreateGameRequest) -> GameView:
        """创建存档；同一幂等键同参回放首次结果（零写入），异参抛 ``CreateGameError(CONFLICT)``。"""
        store_key = f"game:{request.idempotency_key}"
        request_hash = params_hash({"seed": request.seed, "scenario": request.scenario})
        cached = self._repo.get_idempotent(store_key)
        if cached is not None:
            if cached.params_hash != request_hash:
                raise CreateGameError(
                    ErrorCode.CONFLICT,
                    "该 Idempotency-Key 已经用在不同参数的创建请求上",
                )
            return GameView.model_validate(cached.payload)

        save_seed = (
            save_seed_from_settings(request.seed)
            if request.seed is not None
            else self._save_seed_default
        )
        engine_result = self._engine.create_game(
            save_seed=save_seed,
            idempotency_key=request.idempotency_key,
            scenario=request.scenario,
        )
        if not engine_result.ok:
            raise CreateGameError(
                engine_result.error_code or ErrorCode.INTERNAL,
                engine_result.message or "创建存档失败",
            )
        data = engine_result.data or {}
        try:
            view = GameView(
                game_id=str(data["game_id"]),
                turn=int(data["turn"]),
                version=int(data["version"]),
                snapshot_version=int(data["snapshot_version"]),
                save_seed=str(data["save_seed"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CreateGameError(ErrorCode.INTERNAL, f"创建存档结果不完整: {exc}") from exc
        self._save_idempotent(store_key, request_hash, view.model_dump(mode="json"))
        return view

    # ------------------------------------------------------------------
    # 回合
    # ------------------------------------------------------------------
    def run(self, request: TurnRequest) -> TurnResult:
        """执行一个回合；业务失败返回 ``TurnResult(ok=False)``（HTTP 仍 200，契约 §5.4）。

        D-M4-15/G-1：同 ``Idempotency-Key`` 的请求在进程内按存储键串行（固定分段锁），
        后到者进入时能读到首请求写入的幂等记录并原样重放；失败路径再由
        ``_replay_idempotent`` 重查一次幂等表，覆盖「另一进程在本次检查后写入首结果」
        的窗口（单进程部署 Q6=A 下由分段锁保证；多进程时退化为显式失败 + 零状态写入）。
        最终失败结果由 ``_with_failure_hints`` 追加 ``effects``/``suggested_actions``
        （M6 加法字段，不改 ``text``）。
        """
        store_key = _turn_store_key(request)
        request_hash = _turn_params_hash(request)
        lock = self._idempotency_locks[_idempotency_lock_index(store_key)]
        lock.acquire()
        try:
            result = self._run_once(request, store_key, request_hash)
            if result.ok:
                return result
            replayed = self._replay_idempotent(store_key, request_hash, result)
            if replayed.ok:
                return replayed
            return _with_failure_hints(result)
        finally:
            lock.release()

    def _run_once(self, request: TurnRequest, store_key: str, request_hash: str) -> TurnResult:
        """单次回合处理（原有逻辑）；同键串行化与失败后重查由 ``run`` 负责。"""
        cached = self._repo.get_idempotent(store_key)
        if cached is not None:
            if cached.params_hash == request_hash:
                return TurnResult.model_validate(cached.payload)
            record = self._repo.load(request.game_id)
            return TurnResult.failed(
                ErrorCode.CONFLICT,
                version=record.version if record is not None else 0,
                text=_IDEMPOTENCY_CONFLICT_TEXT,
            )

        record = self._repo.load(request.game_id)
        if record is None:
            return TurnResult.failed(ErrorCode.NOT_FOUND, version=0, text=_GAME_NOT_FOUND_TEXT)
        if record.version != request.expected_version:
            return TurnResult.failed(
                ErrorCode.CONFLICT, version=record.version, text=_VERSION_CONFLICT_TEXT
            )
        try:
            if self.multi_actor_enabled:
                return self._run_multi_actor(request, record, store_key, request_hash)
            return self._run_single_actor(request, record, store_key, request_hash)
        except Exception:
            current = self._repo.load(request.game_id)
            return TurnResult.failed(
                ErrorCode.INTERNAL,
                version=current.version if current is not None else 0,
                text=_INTERNAL_ERROR_TEXT,
            )

    # ------------------------------------------------------------------
    # M2 单角色链路（未注入 M3 依赖时；与 M2 契约逐字一致）
    # ------------------------------------------------------------------
    def _run_single_actor(
        self,
        request: TurnRequest,
        record: GameRecord,
        store_key: str,
        request_hash: str,
    ) -> TurnResult:
        """M2 单角色主流程（契约 §4.5 步骤 4–10），保持 M2 行为不回归。"""
        state = record.state
        turn_id = state.turn + 1
        ctx = ToolContext(
            actor_id=PLAYER_ID,
            turn_id=turn_id,
            save_seed=state.rng_state.save_seed,
            idempotency_key=request.idempotency_key,
            caller="player",
        )
        perception = self._query.build_perception_slice(
            state,
            PLAYER_ID,
            turn_id=turn_id,
            save_seed=ctx.save_seed,
        )
        outcome = self._parser.parse(request.text, perception=perception, ctx=ctx)
        if outcome.tool is None or outcome.confidence < _CONFIDENCE_FLOOR:
            return TurnResult.failed(
                ErrorCode.INVALID_INPUT,
                version=record.version,
                turn_id=state.turn,
                text=_UNKNOWN_ACTION_TEXT,
            )

        gate_result = self._gate.dispatch(
            tool=outcome.tool, args=outcome.args, ctx=ctx, state=state
        )
        if not gate_result.ok:
            return TurnResult.failed(
                gate_result.error_code or ErrorCode.INTERNAL,
                version=record.version,
                turn_id=state.turn,
                text=gate_result.message or _ACTION_REJECTED_TEXT,
            )

        drafts = list(gate_result.events)
        if self._rules is not None and drafts:
            preconditions = self._rules.evaluate_preconditions(
                tool=outcome.tool,
                args=outcome.args,
                actor_id=ctx.actor_id,
                turn_id=ctx.turn_id,
                save_seed=ctx.save_seed,
                state=state,
            )
            if not preconditions.ok:
                return TurnResult.failed(
                    preconditions.error_code or ErrorCode.INTERNAL,
                    version=record.version,
                    turn_id=state.turn,
                    text=preconditions.message or _ACTION_REJECTED_TEXT,
                )
        result_data = dict(gate_result.data or {})
        text = self._render(action=outcome, events=drafts, state=state, result_data=result_data)

        if not drafts:
            response = TurnResult.success(turn_id=state.turn, version=record.version, text=text)
            self._save_idempotent(store_key, request_hash, response.model_dump(mode="json"))
            return response

        narrated = [
            draft.model_copy(
                update={
                    "result": {
                        **draft.result,
                        "narrative": text,
                        "narrative_source": "template",
                    }
                }
            )
            for draft in drafts
        ]
        engine_result = self._engine.submit(
            game_id=request.game_id,
            expected_version=record.version,
            drafts=narrated,
            idempotency_key=request.idempotency_key,
        )
        if not engine_result.ok:
            return TurnResult.failed(
                engine_result.error_code or ErrorCode.INTERNAL,
                version=record.version,
                turn_id=state.turn,
                text=engine_result.message or _ACTION_REJECTED_TEXT,
            )
        data = engine_result.data or {}
        committed_turn = data.get("turn_id")
        committed_version = data.get("version")
        if not isinstance(committed_turn, int) or not isinstance(committed_version, int):
            return TurnResult.failed(
                ErrorCode.INTERNAL,
                version=record.version,
                turn_id=state.turn,
                text=_INTERNAL_ERROR_TEXT,
            )
        response = TurnResult.success(
            turn_id=committed_turn,
            version=committed_version,
            text=text,
            events=engine_result.events,
        )
        self._save_idempotent(
            store_key,
            request_hash,
            response.model_dump(mode="json"),
            events=tuple(engine_result.events),
        )
        return response

    # ------------------------------------------------------------------
    # M3 多角色链路
    # ------------------------------------------------------------------
    def _run_multi_actor(
        self,
        request: TurnRequest,
        record: GameRecord,
        store_key: str,
        request_hash: str,
    ) -> TurnResult:
        """M3 多角色主流程：玩家提案 + 角色 Agent 提案 → 队列 → 裁决 → CAS 提交 → 审计。"""
        assert self._registry is not None  # multi_actor_enabled 已保证
        assert self._resolver is not None
        assert self._actors is not None
        state = record.state
        turn_id = state.turn + 1
        save_seed = state.rng_state.save_seed
        player_ctx = ToolContext(
            actor_id=PLAYER_ID,
            actor_tags=self._actors.tags_of(state, PLAYER_ID),
            turn_id=turn_id,
            save_seed=save_seed,
            idempotency_key=request.idempotency_key,
            caller="player",
        )
        events_history = self._repo.read_events(request.game_id)
        perception = self._query.build_perception_slice(
            state,
            PLAYER_ID,
            turn_id=turn_id,
            save_seed=save_seed,
            events=events_history,
        )
        outcome = self._parser.parse(request.text, perception=perception, ctx=player_ctx)
        if outcome.tool is None or outcome.confidence < _CONFIDENCE_FLOOR:
            return TurnResult.failed(
                ErrorCode.INVALID_INPUT,
                version=record.version,
                turn_id=state.turn,
                text=_UNKNOWN_ACTION_TEXT,
            )

        player_proposal: Proposal | None = None
        read_only_data: dict[str, Any] = {}
        try:
            spec = self._registry.get(outcome.tool)
        except KeyError:
            spec = None
        if spec is None:
            return TurnResult.failed(
                ErrorCode.NOT_FOUND,
                version=record.version,
                turn_id=state.turn,
                text=f"未知工具: {outcome.tool}",
            )
        if spec.side_effect is SideEffect.READ_ONLY:
            read_only = self._gate.dispatch(
                tool=outcome.tool, args=outcome.args, ctx=player_ctx, state=state
            )
            if not read_only.ok:
                return TurnResult.failed(
                    read_only.error_code or ErrorCode.INTERNAL,
                    version=record.version,
                    turn_id=state.turn,
                    text=read_only.message or _ACTION_REJECTED_TEXT,
                )
            read_only_data = dict(read_only.data or {})
        else:
            if not self._registry.is_visible(outcome.tool, player_ctx):
                return TurnResult.failed(
                    ErrorCode.NOT_ALLOWED,
                    version=record.version,
                    turn_id=state.turn,
                    text="当前角色无权执行这个动作。",
                )
            validated = self._registry.validate_args(outcome.tool, outcome.args)
            if not validated.ok or not validated.data:
                return TurnResult.failed(
                    validated.error_code or ErrorCode.INVALID_INPUT,
                    version=record.version,
                    turn_id=state.turn,
                    text=validated.message or _ACTION_REJECTED_TEXT,
                )
            player_proposal = Proposal(
                turn_id=turn_id,
                actor_id=PLAYER_ID,
                tool=outcome.tool,
                args=dict(validated.data["args"]),
                reason="玩家明确动作",
                priority=100,
            )

        queue = ProposalQueue(turn_id=turn_id)
        player_entry: QueueEntry | None = None
        if player_proposal is not None:
            player_entry = queue.enqueue(player_proposal, source=ProposalSource.PLAYER)
        for actor_id in sorted(self._character_agents):
            agent = self._character_agents[actor_id]
            if not self._actors.exists(state, actor_id):
                continue
            npc_perception = self._query.build_perception_slice(
                state,
                actor_id,
                turn_id=turn_id,
                save_seed=save_seed,
                events=events_history,
            )
            try:
                npc_proposals = agent.propose(npc_perception, turn_id=turn_id, save_seed=save_seed)
            except Exception:  # 单个角色 Agent 失败不影响其余（第 9 章 §9.2 步骤 8）
                continue
            for npc_proposal in npc_proposals:
                if npc_proposal.turn_id != turn_id:
                    continue
                queue.enqueue(npc_proposal, source=ProposalSource.NPC)
        drained = queue.drain()
        expired = queue.expire_pending()
        entries: list[QueueEntry] = [*drained, *expired]
        # 同一 turn_id 可能被多次回合尝试复用（上一次是只读/全部被拒的零写入回合，
        # GameState.turn 未推进）；审计行以 (game, turn, attempt, proposal) 去重，故本回合
        # 的 attempt 从「该 turn 已有审计的最大 attempt + 1」起算，保证只增审计不互相覆盖。
        audit_base = self._audit_next_attempt(request.game_id, turn_id)

        current = record
        text = ""
        attempt = 0
        for attempt in range(1, _MAX_SUBMIT_ATTEMPTS + 1):
            resolutions = self._resolver.resolve(drained, current.state)
            by_proposal = {resolution.proposal_id: resolution for resolution in resolutions}
            player_resolution = (
                by_proposal.get(player_entry.proposal_id) if player_entry is not None else None
            )
            if player_entry is not None and (
                player_resolution is None or player_resolution.verdict != "applied"
            ):
                # 对外文案保持中文（2G 文字风格）；裁决理由（可能为规则层英文诊断）只进审计。
                error_code = (
                    ErrorCode.CONFLICT
                    if player_resolution is not None and player_resolution.verdict == "conflict"
                    else ErrorCode.INVALID_INPUT
                )
                self._save_audit(
                    self._audit_rows(
                        game_id=request.game_id,
                        entries=entries,
                        resolutions=resolutions,
                        status="aborted",
                        attempt=audit_base + attempt - 1,
                    )
                )
                return TurnResult.failed(
                    error_code,
                    version=current.version,
                    turn_id=state.turn,
                    text=_ACTION_REJECTED_TEXT,
                )

            entry_by_id = {entry.proposal_id: entry for entry in entries}
            player_drafts: list[Event] = []
            npc_drafts: list[Event] = []
            for resolution in resolutions:
                if resolution.verdict != "applied":
                    continue
                source = entry_by_id[resolution.proposal_id].source
                for draft in resolution.applied_events:
                    if source is ProposalSource.PLAYER:
                        player_drafts.append(draft)
                    else:
                        npc_drafts.append(draft)
            text = self._render_multi_actor(
                action=outcome,
                state=state,
                player_drafts=player_drafts,
                npc_drafts=npc_drafts,
                read_only_data=read_only_data,
            )
            drafts = [*player_drafts, *npc_drafts]
            if not drafts:
                self._save_audit(
                    self._audit_rows(
                        game_id=request.game_id,
                        entries=entries,
                        resolutions=resolutions,
                        status="committed",
                        attempt=audit_base + attempt - 1,
                    )
                )
                response = TurnResult.success(
                    turn_id=state.turn, version=current.version, text=text
                )
                self._save_idempotent(store_key, request_hash, response.model_dump(mode="json"))
                return response

            narrated = [
                draft.model_copy(
                    update={
                        "result": {
                            **draft.result,
                            "narrative": text,
                            "narrative_source": "template",
                        }
                    }
                )
                for draft in drafts
            ]
            engine_result = self._engine.submit(
                game_id=request.game_id,
                expected_version=current.version,
                drafts=narrated,
                idempotency_key=request.idempotency_key,
            )
            if engine_result.ok:
                self._save_audit(
                    self._audit_rows(
                        game_id=request.game_id,
                        entries=entries,
                        resolutions=resolutions,
                        status="committed",
                        attempt=audit_base + attempt - 1,
                    )
                )
                data = engine_result.data or {}
                committed_turn = data.get("turn_id")
                committed_version = data.get("version")
                if not isinstance(committed_turn, int) or not isinstance(committed_version, int):
                    return TurnResult.failed(
                        ErrorCode.INTERNAL,
                        version=current.version,
                        turn_id=state.turn,
                        text=_INTERNAL_ERROR_TEXT,
                    )
                response = TurnResult.success(
                    turn_id=committed_turn,
                    version=committed_version,
                    text=text,
                    events=engine_result.events,
                )
                self._save_idempotent(
                    store_key,
                    request_hash,
                    response.model_dump(mode="json"),
                    events=tuple(engine_result.events),
                )
                return response

            self._save_audit(
                self._audit_rows(
                    game_id=request.game_id,
                    entries=entries,
                    resolutions=resolutions,
                    status="conflicted"
                    if engine_result.error_code is ErrorCode.CONFLICT
                    else "aborted",
                    attempt=audit_base + attempt - 1,
                )
            )
            if engine_result.error_code is not ErrorCode.CONFLICT:
                return TurnResult.failed(
                    engine_result.error_code or ErrorCode.INTERNAL,
                    version=current.version,
                    turn_id=state.turn,
                    text=engine_result.message or _ACTION_REJECTED_TEXT,
                )
            reloaded = self._repo.load(request.game_id)
            if reloaded is None:
                return TurnResult.failed(ErrorCode.NOT_FOUND, version=0, text=_GAME_NOT_FOUND_TEXT)
            current = reloaded
            state = current.state

        return TurnResult.failed(
            ErrorCode.CONFLICT,
            version=current.version,
            turn_id=current.state.turn - 1 if current.state.turn > 1 else current.state.turn,
            text=_SUBMIT_CONFLICT_TEXT,
        )

    def _render_multi_actor(
        self,
        *,
        action: ParseOutcome,
        state: GameState,
        player_drafts: list[Event],
        npc_drafts: list[Event],
        read_only_data: dict[str, Any],
    ) -> str:
        """玩家事件用叙事器渲染，NPC 事件拼一句确定性短句，最终收敛到 80–150 字。"""
        base = self._render(
            action=action, events=player_drafts, state=state, result_data=read_only_data
        )
        clause = _npc_event_clause(state, npc_drafts)
        return _compose_text(base, clause)

    def _audit_next_attempt(self, game_id: str, turn_id: int) -> int:
        """计算该回合下一批审计行的 attempt 起始值（同 turn 零写入重试时不覆盖旧行）。

        审计表以 ``(game_id, turn_id, attempt, proposal_id)`` 去重且只增不改；同一个
        ``turn_id`` 可能因上一次回合零写入（只读/全部被拒，``GameState.turn`` 未推进）被
        再次使用，故从已有最大 ``attempt + 1`` 起算，保证两次尝试的审计行都保留。
        """
        if self._audit is None:
            return 1
        try:
            rows = self._repo.read_audit(game_id, turn_id=turn_id)
        except Exception:
            return 1
        return max((row.attempt for row in rows), default=0) + 1

    def _audit_rows(
        self,
        *,
        game_id: str,
        entries: Sequence[QueueEntry],
        resolutions: Sequence[Resolution],
        status: Literal["committed", "conflicted", "aborted"],
        attempt: int,
    ) -> list[AuditRecord]:
        """把本尝试的队列条目 + 裁决结果映射为审计行（每提案一行，只增不改）。"""
        by_proposal = {resolution.proposal_id: resolution for resolution in resolutions}
        rows: list[AuditRecord] = []
        for index, entry in enumerate(entries, start=1):
            resolution = by_proposal.get(entry.proposal_id)
            turn_id = entry.proposal.turn_id
            resolution_id = f"r_{turn_id:06d}_{index:03d}" if resolution is not None else None
            rows.append(
                AuditRecord(
                    game_id=game_id,
                    turn_id=turn_id,
                    attempt=attempt,
                    proposal_id=entry.proposal_id,
                    resolution_id=resolution_id,
                    actor_id=entry.proposal.actor_id,
                    tool=entry.proposal.tool,
                    args=dict(entry.proposal.args),
                    reason=entry.proposal.reason,
                    priority=entry.proposal.priority,
                    source=str(entry.source.value),
                    verdict=resolution.verdict if resolution is not None else None,
                    verdict_reason=resolution.reason if resolution is not None else "expired",
                    status=status,
                )
            )
        return rows

    def _save_audit(self, records: Sequence[AuditRecord]) -> None:
        """写入审计行；审计失败不阻塞回合（第 11 章 §11.4 / 第 14 章 §14.6）。"""
        if self._audit is None or not records:
            return
        try:
            self._audit.save(records)
        except Exception:
            return

    # ------------------------------------------------------------------
    # 渲染
    # ------------------------------------------------------------------
    def _render(
        self,
        *,
        action: ParseOutcome,
        events: list[Event],
        state: GameState,
        result_data: dict[str, Any],
    ) -> str:
        """优先用注入的叙事器；M2 默认走确定性模板 ``_fallback_text``。"""
        if self._narrator is not None:
            return self._narrator.render(
                action=action, events=events, state=state, result_data=result_data
            )
        return _fallback_text(action=action, events=events, state=state, result_data=result_data)

    def _replay_idempotent(
        self, store_key: str, request_hash: str, failure: TurnResult
    ) -> TurnResult:
        """失败后重查幂等表：若首请求已写入同参成功结果则原样重放（竞态兜底）。

        无记录、参数不同或读取异常都保留本次失败结果（不掩盖真实业务失败）。成功结果
        按 D-M5-7 语义复用，避免并发同键请求把首结果覆盖成瞬时 CONFLICT/INVALID_INPUT。
        """
        try:
            cached = self._repo.get_idempotent(store_key)
            if cached is None or cached.params_hash != request_hash:
                return failure
            return TurnResult.model_validate(cached.payload)
        except Exception:
            return failure

    def _save_idempotent(
        self,
        key: str,
        request_hash: str,
        payload: dict[str, Any],
        *,
        events: tuple[Event, ...] = (),
    ) -> None:
        """把成功响应写入幂等表（失败不写，避免“失败结果被永久缓存”）。"""
        self._repo.save_idempotent(
            IdempotencyRecord(
                key=key,
                params_hash=request_hash,
                payload=payload,
                events=events,
            )
        )


__all__ = [
    "AuditRecorderLike",
    "CharacterAgentLike",
    "CreateGameError",
    "CreateGameRequest",
    "GameView",
    "IntentParserLike",
    "NarratorLike",
    "ResolverLike",
    "RuleEngineLike",
    "ToolSpecsLike",
    "TurnOrchestrator",
]
