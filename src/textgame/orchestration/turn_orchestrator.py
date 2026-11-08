"""回合编排 ``TurnOrchestrator`` (第 9 章 9.2 的 M2 裁剪版，契约 §4.5)。

依赖全部由构造注入（``repo/engine/query/action/parser/gate/narrator/rules``），006 可把
``parser`` 换成 ``IntentParseAgent``、``gate`` 换成 ``ToolGate``，007 可注入
``TemplateNarrator``；本类只负责串流程、幂等与错误码映射，不做规则计算、不写状态。

M2 流程（跳过项以 TODO 标注）：
1. 幂等表命中 → 同参回放首次 ``TurnResult``（零写入）/ 异参 ``CONFLICT``；
2. 载入存档 → ``NOT_FOUND``；CAS 版本不符 → ``CONFLICT``；
3. 构造 ``ToolContext`` 与感知切片；4. 解析失败 → ``INVALID_INPUT``（零写入）；
5. TODO(006) 规则引擎前置判定（``rules`` 预留）；
6. 工具门 → ActionService 草稿事件；7. 叙事 → 合并进事件 ``result``；
8. 无事件（READ_ONLY）→ 直接成功；有事件 → ``GameEngine.submit`` 原子提交；
9. 成功结果写幂等表；任何异常 → ``INTERNAL``（零写入）。

叙事器缺席（005 默认）时用模块内 ``_fallback_text`` 生成 80–150 字确定性文本；007 的
``TemplateNarrator`` 完成后由组合根注入 ``narrator`` 即可替换。
"""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from textgame.agents.dto import ParseOutcome
from textgame.config.rng import save_seed_from_settings
from textgame.domain.events import Event, EventType
from textgame.domain.hashing import params_hash
from textgame.domain.models import GameState, PerceptionSlice
from textgame.domain.results import ErrorCode, Result
from textgame.domain.scenario import DIRECTION_NAMES, PLAYER_ID
from textgame.orchestration.direct_gate import ToolGateLike
from textgame.orchestration.dto import TurnRequest
from textgame.services.action_service import ActionService
from textgame.services.dto import TurnResult
from textgame.services.game_engine import GameEngine
from textgame.services.ports import GameRecord, GameRepository, IdempotencyRecord
from textgame.services.query_service import QueryService
from textgame.services.tool_context import ToolContext

_MIN_TEXT_CHARS = 80
_MAX_TEXT_CHARS = 150
_CONFIDENCE_FLOOR = 0.6
_DEFAULT_SAVE_SEED = "save-42"

_UNKNOWN_ACTION_TEXT = (
    "你试着比划了一下，却没弄明白自己想做什么。"
    "也许可以换个说法：环顾四周、朝某个方向走、拿起地上的东西，或者和身边的人说说话。"
)
_IDEMPOTENCY_CONFLICT_TEXT = "这个幂等键已经用在不同参数的回合请求上，请换一个新的键重试。"
_VERSION_CONFLICT_TEXT = "你的对局版本已经过期，请刷新对局视图后重试。"
_GAME_NOT_FOUND_TEXT = "存档不存在，请先创建一个新的游戏。"
_ACTION_REJECTED_TEXT = "这个动作没有生效，你停下了脚步。"
_INTERNAL_ERROR_TEXT = "回合处理发生内部错误，本回合没有写入任何状态。"

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
    """007 ``RuleEngine`` 的最小前置判定接口（M2 默认 ``None``，跳过）。"""

    def evaluate_preconditions(
        self, ctx: ToolContext, tool: str, args: dict[str, Any], state: GameState
    ) -> Result:
        """确定性前置判定；失败返回 ``Result.fail(...)``。"""
        ...


class CreateGameRequest(BaseModel):
    """``POST /api/v1/games`` 的编排层请求（``seed`` 可省略，缺省用组合根默认种子）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    seed: int | None = None
    idempotency_key: str = Field(min_length=8, max_length=128)


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


class TurnOrchestrator:
    """单回合编排器；依赖全部构造注入，便于 006/007 替换解析器与叙事器。"""

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
    ) -> None:
        """装配依赖；``narrator=None`` 时使用内置确定性模板叙事。"""
        self._repo = repo
        self._engine = engine
        self._query = query
        self._action = action
        self._parser = parser
        self._gate = gate
        self._narrator = narrator
        self._rules = rules
        self._save_seed_default = save_seed_default

    # ------------------------------------------------------------------
    # 开局
    # ------------------------------------------------------------------
    def create_game(self, request: CreateGameRequest) -> GameView:
        """创建存档；同一幂等键同参回放首次结果（零写入），异参抛 ``CreateGameError(CONFLICT)``。"""
        store_key = f"game:{request.idempotency_key}"
        request_hash = params_hash({"seed": request.seed})
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
            save_seed=save_seed, idempotency_key=request.idempotency_key
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
        """执行一个回合；业务失败返回 ``TurnResult(ok=False)``（HTTP 仍 200，契约 §5.4）。"""
        store_key = _turn_store_key(request)
        request_hash = _turn_params_hash(request)
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
            return self._run_loaded(request, record, store_key, request_hash)
        except Exception:
            current = self._repo.load(request.game_id)
            return TurnResult.failed(
                ErrorCode.INTERNAL,
                version=current.version if current is not None else 0,
                text=_INTERNAL_ERROR_TEXT,
            )

    def _run_loaded(
        self,
        request: TurnRequest,
        record: GameRecord,
        store_key: str,
        request_hash: str,
    ) -> TurnResult:
        """已通过幂等/版本检查后的单回合主流程（契约 §4.5 步骤 4–10）。"""
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

        # TODO(007): rules 非 None 时先做确定性前置判定（M2 默认跳过，由服务层 L0 自检兜底）。
        drafts = list(gate_result.events)
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
    "CreateGameError",
    "CreateGameRequest",
    "GameView",
    "IntentParserLike",
    "NarratorLike",
    "RuleEngineLike",
    "TurnOrchestrator",
]
