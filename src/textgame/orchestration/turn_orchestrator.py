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

import re
import threading
import zlib
from collections.abc import Mapping, Sequence
from typing import Any, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field

from textgame.agents.dto import ParseOutcome
from textgame.agents.judge_agent import JudgeAgentLike
from textgame.config.rng import save_seed_from_settings
from textgame.domain.events import Event, EventType
from textgame.domain.hashing import params_hash
from textgame.domain.judge import JudgeOutput
from textgame.domain.models import (
    GameState,
    PerceptionSlice,
    Proposal,
    Resolution,
    ResolutionPolicy,
    SideEffect,
    ToolSpec,
)
from textgame.domain.results import ErrorCode, Result
from textgame.domain.scenario import DIRECTION_NAMES, PLAYER_ID
from textgame.domain.scenario_runtime import ScenarioRuntime
from textgame.domain.scenario_story import is_story_state, story_ending, story_room_damage
from textgame.domain.suggestions import failure_feedback, suggest_actions
from textgame.domain.world_book import (
    WorldBook,
    WorldBookProvider,
    activate_entries,
    build_activation_context,
    format_lore_entries,
)
from textgame.orchestration.direct_gate import ToolGateLike
from textgame.orchestration.dto import TurnRequest
from textgame.orchestration.proposal_queue import (
    ProposalQueue,
    ProposalSource,
    QueueEntry,
)
from textgame.services.action_service import ActionService
from textgame.services.actor_directory import ActorDirectory
from textgame.services.card_service import build_card_initial_state
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
#: 单回合裁判调用上限（第 8 章 §8.3 "仅规则空白时 1 次"、第 12 章 §12.8 最多 3 次含裁判）。
_MAX_JUDGE_CALLS_PER_TURN = 1
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
_STORY_ENDED_TEXT = "故事已经结束了。回到首页可以开新的一局。"
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


class AgentSchedulerLike(Protocol):
    """NPC 调度端口（M5-023 批量合并 + 休眠唤醒 + 调用审计）。

    一次返回本回合所有 NPC 提案（可为空）；默认 ``None`` 表示保留原逐 Agent 循环
    （M2–M4 行为不变）。实现见 ``orchestration.batch_sleep.BatchSleepScheduler``。
    """

    def collect(
        self,
        state: GameState,
        events: Sequence[Event],
        *,
        turn_id: int,
        save_seed: str,
        player_action: ParseOutcome | None,
        character_agents: Mapping[str, CharacterAgentLike],
    ) -> list[Proposal]: ...


class AuditRecorderLike(Protocol):
    """审计写入端口（``AuditService.save``；审计失败不得阻塞回合）。"""

    def save(self, records: Sequence[AuditRecord]) -> None: ...


class CreateGameRequest(BaseModel):
    """``POST /api/v1/games`` 的编排层请求（``seed`` 可省略，缺省用组合根默认种子）。

    ``scenario`` 支持 ``m2``/``m3``/``m4``（M4 制造场景，D-M4-11）与 ``story``
    （剧情场景「余烬哨站」，D1/T10；旧场景行为/事件逐字不变），缺省 ``m2``
    保持 M2 契约与固定种子回放不变。D-M6-2 加法：``card_id`` + ``card``（人物卡 9 字段
    JSON，接入层已按卡库校验/加载）指定按卡建局；与 ``seed``/``scenario`` 互斥，
    同时提供 → ``INVALID_INPUT``。不提供 ``card_id`` 时行为与 M2–M4 完全一致。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    seed: int | None = None
    idempotency_key: str = Field(min_length=8, max_length=128)
    scenario: Literal["m2", "m3", "m4", "story", "custom"] = "m2"
    card_id: str | None = Field(default=None, min_length=1, max_length=64)
    card: dict[str, Any] | None = None
    #: 用户上传场景（``scenario="custom"``）：场景 ID 与物化好的初始状态（由场景服务提供）。
    #: 缺省 ``None`` 时请求哈希/创建参数/事件与旧版本逐字一致（旧场景零影响）。
    scenario_id: str | None = Field(default=None, min_length=1, max_length=64)
    initial_state: dict[str, Any] | None = None
    #: H4 加法：可选世界书 id（存在性由编排层通过 ``world_book_provider`` 校验，未知 404）。
    #: 缺省 ``None`` 时请求哈希/创建参数/事件与旧版本逐字一致（旧场景零影响）。
    world_book_id: str | None = Field(default=None, min_length=1, max_length=64)


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


#: 玩家可见失败原因的保守过滤：只展示中文（含中文标点），ASCII 字母/数字/下划线一律视为
#: 内部诊断（工具名、ULID、英文规则消息）不展示，避免把实现细节漏到玩家屏幕。
_FORBIDDEN_REASON_RE = re.compile(r"[A-Za-z0-9_]")
_MAX_REASON_CHARS = 60


def _safe_player_reason(reason: str | None) -> str | None:
    """把服务层拒绝原因收敛为可展示给玩家的中文短句；不安全/过长/为空返回 ``None``。"""
    if not isinstance(reason, str):
        return None
    text = " ".join(reason.split())
    if not text or len(text) > _MAX_REASON_CHARS:
        return None
    if _FORBIDDEN_REASON_RE.search(text):
        return None
    return text


def _scene_failure_feedback(
    state: GameState, code: ErrorCode | None, reason: str | None
) -> tuple[str, list[str]]:
    """场景化失败反馈：把已过滤的具体原因交给 ``failure_feedback``（R2 加法参数）。

    ``reason`` 在本层再做一次保守过滤后传入；算法侧 ``failure_feedback`` 负责把它与
    当前房间/行囊合成「为什么 + 下一步」。
    """
    return failure_feedback(state, code, reason=_safe_player_reason(reason))


def _context_reason(state: GameState, action: ParseOutcome) -> str | None:
    """从「当前状态 + 被拒绝的意图」推一条玩家可读的中文失败原因（D12）。

    Resolver/规则层的 ``reason`` 多为英文内部诊断（含工具名/物品 ID），不能直接展示；
    这里对最常见的世界内失败做确定性重述，宁可不说也不泄漏实现细节。返回 ``None`` 时由
    编排层走通用文案。
    """
    character = state.characters.get(PLAYER_ID)
    if character is None:
        return None
    room = state.rooms.get(character.location)
    if room is None:
        return None
    tool = action.tool
    args = action.args
    if tool == "move_to":
        direction = args.get("direction")
        if isinstance(direction, str) and direction not in room.exits:
            return f"这里没有往{_direction_name(direction)}的路。"
        return None
    if tool == "take_item":
        item_id = args.get("item_id")
        if not isinstance(item_id, str):
            return None
        item = state.items.get(item_id)
        if item is not None and item_id not in room.item_ids:
            return f"这里没有{item.name}可以拿。"
        return None
    if tool == "talk_to":
        npc_id = args.get("npc_id")
        npc = state.npcs.get(npc_id) if isinstance(npc_id, str) else None
        if npc is None or npc.room_id != character.location:
            return "这里没有可以说话的人。"
        return None
    if tool == "use_item":
        item_id = args.get("item_id")
        if not isinstance(item_id, str):
            return None
        item = state.items.get(item_id)
        if item_id in character.inventory:
            return None
        # 同名物品歧义（玩家实测）：解析可能匹配到「地上的铜线」而不是行囊里的，
        # 提示必须说真话，不能笼统说「行囊里没有」。
        same_name_held = any(
            isinstance(held, str)
            and (state.items.get(held) is not None)
            and state.items[held].name == (item.name if item is not None else "")
            for held in character.inventory
        )
        if item is not None and same_name_held:
            return f"行囊里的{item.name}不能这样直接用。"
        if item is not None:
            return f"地上的{item.name}得先拿起来才能用。"
        return "行囊里没有这件东西。"
    return None


def _with_failure_hints(
    result: TurnResult,
    state: GameState | None = None,
    reason: str | None = None,
) -> TurnResult:
    """给失败 ``TurnResult`` 填 ``effects``/``suggested_actions``（纯加法，D7/D12）。

    - 协议级失败（幂等冲突/事件 schema 等）沿用 ``_FAILURE_HINTS`` 固定文案：玩家在客户端
      层面即可处理；
    - 世界内失败（解析失败/门禁拒绝/前置不满足等）：``reason`` 为服务层给出的具体原因
      （仅中文短句会被展示，英文诊断/内部 ID 被过滤），由
      ``domain.suggestions.failure_feedback`` 合成「为什么 + 下一步」；
    - 不改 ``text`` 原文、不产生事件、不写状态；已填过 ``effects`` 的结果原样返回（幂等）。
    """
    if result.effects:
        return result
    code = result.error_code
    hint, actions = (
        _FAILURE_HINTS.get(code, _DEFAULT_FAILURE_HINT)
        if code is not None
        else _DEFAULT_FAILURE_HINT
    )
    # 协议级错误码保留固定文案（与既有测试/玩家预期一致）：只有世界内失败用场景化反馈。
    if (
        state is not None
        and code is not None
        and code not in {ErrorCode.CONFLICT, ErrorCode.SCHEMA_VIOLATION}
    ):
        scene_hint, scene_actions = _scene_failure_feedback(state, code, reason)
        if scene_hint:
            hint = scene_hint
        # 场景反馈的 actions 是权威值（结局后为空列表 = 不再引导行动）
        actions = tuple(scene_actions)
    return result.model_copy(
        update={"effects": [{"hint": hint}], "suggested_actions": list(actions)}
    )


def _success_suggestions(state: GameState) -> list[str]:
    """成功回合的「下一步」建议（D7）：当前状态可直接提交的中文指令，≤4 条、确定性。"""
    return suggest_actions(state, limit=4)


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
        agent_scheduler: AgentSchedulerLike | None = None,
        judge: JudgeAgentLike | None = None,
        world_book_provider: WorldBookProvider | None = None,
        scenario_runtime: ScenarioRuntime | None = None,
    ) -> None:
        """装配依赖；``narrator=None`` 时使用内置确定性模板叙事。

        注入 ``registry``/``resolver``/``actors`` 三者后启用 M3 多角色链路；缺省保持 M2
        单角色链路（默认装配行为与 M2 完全一致，M2 测试不回归）。``agent_scheduler``
        为 M5-023 可选的批量/休眠 NPC 调度器，默认 ``None`` = 原逐 Agent 循环。
        ``judge`` 为 M5E-2 可选裁判 Agent（默认 ``None``：不构建裁判候选、零模型调用）。
        ``world_book_provider`` 为 H4 可选世界书提供者（``None`` = 不启用世界书：旧场景
        回合/建局/查询行为逐字不变）。
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
        # M5-023：可选 NPC 调度器（批量合并 + 休眠唤醒 + 审计）；None = 原逐 Agent 循环。
        self._agent_scheduler = agent_scheduler
        # M5E-2：可选裁判 Agent（None = 不构建 judgements、零模型调用；默认离线逐字等价）。
        self._judge = judge
        # H4：可选世界书提供者（None = 不启用世界书；旧场景零计算、零注入）。
        self._world_book_provider = world_book_provider
        # 用户上传场景：可选钩子注册表（None = 只有内置 4 场景，行为逐字不变）。
        self._scenario_runtime = scenario_runtime
        # D-M4-15/G-1：同 Idempotency-Key 的回合在进程内串行，避免两个线程同时未命中
        # 幂等缓存、后到者拿到 CONFLICT/INVALID_INPUT 而非首结果；固定分段避免锁增长。
        self._idempotency_locks = tuple(threading.Lock() for _ in range(_IDEMPOTENCY_LOCK_STRIPES))

    @property
    def save_seed_default(self) -> str:
        """服务端默认存档种子（``--seed`` 未给时的派生 seed）。

        用户上传场景建局时接入层需要先物化初始状态（其 ``rng_state.save_seed`` 必须与
        引擎实际使用的 seed 一致），因此把组合根注入的默认值暴露为只读属性。
        """
        return self._save_seed_default

    @property
    def multi_actor_enabled(self) -> bool:
        """是否启用 M3 多角色链路（三个 M3 依赖都注入时）。"""
        return (
            self._registry is not None and self._resolver is not None and self._actors is not None
        )

    # ------------------------------------------------------------------
    # H4 世界书 / L1 环境伤害（纯加法：无 provider、非 story 或未命中时零影响）
    # ------------------------------------------------------------------
    def _first_event(self, game_id: str) -> Event | None:
        """存档首事件（``game_created``）；仓储未提供 ``read_first_event`` 时回退全量读。"""
        getter = getattr(self._repo, "read_first_event", None)
        if callable(getter):
            return cast(Event | None, getter(game_id))
        events = self._repo.read_events(game_id)
        return events[0] if events else None

    def _world_book_id_of(self, game_id: str) -> str | None:
        """``game_created.params.world_book_id``（旧场景/未绑定 → ``None``）。"""
        event = self._first_event(game_id)
        if event is None:
            return None
        params = event.params
        world_book_id = params.get("world_book_id") if isinstance(params, dict) else None
        return world_book_id if isinstance(world_book_id, str) and world_book_id else None

    def _lookup_world_book(self, world_book_id: str) -> WorldBook | None:
        """经 provider 查世界书；未装配 provider / provider 异常 → ``None``。

        世界书是可选增强：provider 故障不得中断对局；建局路径由调用方把 ``None`` 映射 404。
        """
        if self._world_book_provider is None:
            return None
        try:
            return self._world_book_provider.get(world_book_id)
        except Exception:
            return None

    def _world_book_of_game(self, game_id: str) -> WorldBook | None:
        """该存档绑定的世界书（未绑定/已删除 → ``None``）。"""
        world_book_id = self._world_book_id_of(game_id)
        if world_book_id is None:
            return None
        return self._lookup_world_book(world_book_id)

    def _lore_entries_for(self, game_id: str, state: GameState, player_input: str) -> list[str]:
        """本回合激活的世界设定句（``【世界设定】…``）。

        上下文 = 最近叙事 + 玩家输入 + 当前房间名/描述 + 可见物品/NPC 名（
        ``domain.world_book.build_activation_context`` 统一口径）；旧场景/未绑定世界书 →
        空列表（调用方不向 narrator 注入任何字段，文本逐字不变）。
        """
        book = self._world_book_of_game(game_id)
        if book is None:
            return []
        try:
            events = self._repo.read_events(game_id)
        except Exception:
            events = []
        narratives: list[str] = []
        for event in events:
            narrative = event.result.get("narrative")
            if isinstance(narrative, str) and narrative:
                narratives.append(narrative)
        context = build_activation_context(
            state, narrative_texts=narratives, player_input=player_input
        )
        active = activate_entries(
            book,
            context_texts=context,
            turn_id=state.turn + 1,
            save_seed=state.rng_state.save_seed,
        )
        return format_lore_entries(active)

    def _ending_of(self, state: GameState) -> dict[str, Any] | None:
        """结局视图：用户场景钩子优先，缺省回退内置 story 判定（旧场景行为不变）。"""
        if self._scenario_runtime is not None:
            ending = self._scenario_runtime.ending(state)
            if ending is not None:
                return cast(dict[str, Any], ending)
        if not is_story_state(state):
            return None
        return story_ending(state)

    def _environment_damage_draft(
        self, *, state: GameState, drafts: Sequence[Event], turn_id: int
    ) -> Event | None:
        """L1：进入受伤房间后，生成同回合同批提交的 ``ENVIRONMENT_DAMAGE`` 草稿。

        两个来源（互斥，均确定性）：

        - **用户上传场景**：``ScenarioRuntime`` 按房间 ID 查 ``rooms[].on_enter.hp_delta``
          （``scenario_runtime=None`` 或非用户场景时不命中）；
        - **内置 story**：``story_room_damage``（``is_story_state`` 恒假 → 旧三场景 golden 零影响）。

        只在「本回合有 ``CHARACTER_MOVED`` 且目标房间有伤害定义」时生成。
        """
        target_room: str | None = None
        for draft in drafts:
            if draft.event_type is not EventType.CHARACTER_MOVED:
                continue
            to_room = draft.result.get("to")
            if not isinstance(to_room, str) or not to_room:
                to_room = draft.params.get("to_room")
            if isinstance(to_room, str) and to_room:
                target_room = to_room
                break
        if target_room is None:
            return None
        user_hooks = (
            self._scenario_runtime.resolve(state) if self._scenario_runtime is not None else None
        )
        if user_hooks is not None:
            user_delta = user_hooks.room_damage(target_room)
            if user_delta is None:
                return None
            user_result: dict[str, Any] = {"hp_delta": user_delta}
            hint = user_hooks.room_damage_hint(target_room)
            if hint:
                user_result["hint"] = hint
            return Event(
                turn_id=turn_id,
                actor_id=PLAYER_ID,
                method="environment_damage",
                event_type=EventType.ENVIRONMENT_DAMAGE,
                params={"room_id": target_room},
                result=user_result,
            )
        if not is_story_state(state):
            return None
        hp_delta = story_room_damage(target_room)
        if hp_delta is None:
            return None
        return Event(
            turn_id=turn_id,
            actor_id=PLAYER_ID,
            method="environment_damage",
            event_type=EventType.ENVIRONMENT_DAMAGE,
            params={"room_id": target_room},
            result={"hp_delta": hp_delta},
        )

    @staticmethod
    def _environment_payload(state: GameState, draft: Event) -> dict[str, Any]:
        """H4/L1：给 narrator 的环境伤害结算上下文（``hp_after`` 用于 S1/S2/S3 分档）。

        纯整数运算（clamp ``0..max_hp``），确定性；只进 ``result_data``，不落事件/状态。
        """
        character = state.characters.get(PLAYER_ID)
        hp = character.hp if character is not None else 0
        raw_max = character.attributes.get("max_hp") if character is not None else None
        max_hp = int(raw_max) if isinstance(raw_max, int) else hp
        hp_delta = draft.result.get("hp_delta")
        delta = hp_delta if isinstance(hp_delta, int) and not isinstance(hp_delta, bool) else 0
        return {
            "room_id": draft.params.get("room_id", ""),
            "hp_delta": delta,
            "hp_after": max(0, min(max_hp, hp + delta)),
            "max_hp": max_hp,
        }

    # ------------------------------------------------------------------
    # 开局
    # ------------------------------------------------------------------
    def create_game(self, request: CreateGameRequest) -> GameView:
        """创建存档；同一幂等键同参回放首次结果（零写入），异参抛 ``CreateGameError(CONFLICT)``。

        D-M6-2 加法：提供 ``card_id`` 时按人物卡映射初始状态（``build_card_initial_state``，
        玩家槽位仍是 ``PLAYER_ID``、场景取默认 m2），与 ``seed``/``scenario`` 互斥（422）；
        不提供卡时请求哈希与 M2–M4 完全一致（零回归）。
        """
        store_key = f"game:{request.idempotency_key}"
        hash_payload: dict[str, Any] = {"seed": request.seed, "scenario": request.scenario}
        if request.card_id is not None:
            hash_payload["card_id"] = request.card_id
        if request.scenario_id is not None:
            # 用户上传场景：场景 ID 进入请求哈希（同键不同场景 = 异参 409）。
            hash_payload["scenario_id"] = request.scenario_id
        if request.world_book_id is not None:
            # H4 加法：仅在显式绑定世界书时进入请求哈希（旧请求哈希逐字不变）。
            hash_payload["world_book_id"] = request.world_book_id
        request_hash = params_hash(hash_payload)
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
        card_initial_state: dict[str, Any] | None = None
        if request.card_id is not None:
            if request.seed is not None or request.scenario != "m2":
                raise CreateGameError(
                    ErrorCode.INVALID_INPUT,
                    "card_id 不能与 seed/scenario 同时提供",
                )
            if request.card is None:
                raise CreateGameError(ErrorCode.INTERNAL, "创建存档缺少人物卡数据")
            try:
                card_initial_state = build_card_initial_state(
                    request.card, card_id=request.card_id, save_seed=save_seed
                )
            except ValueError as exc:
                raise CreateGameError(ErrorCode.SCHEMA_VIOLATION, f"人物卡数据非法: {exc}") from exc
        world_book_name: str | None = None
        if request.world_book_id is not None:
            # H4：建局前校验世界书存在（未知/已软删 → 404）；名称随 game_created.params 落库。
            book = self._lookup_world_book(request.world_book_id)
            if book is None:
                raise CreateGameError(ErrorCode.NOT_FOUND, "世界书不存在")
            world_book_name = book.name
        custom_initial_state: dict[str, Any] | None = None
        if request.scenario == "custom":
            # 用户上传场景：初始状态由场景服务物化（本层不解析 JSON、不做 IO）。
            if request.card_id is not None:
                raise CreateGameError(
                    ErrorCode.INVALID_INPUT, "scenario_id 不能与 card_id 同时提供"
                )
            if request.scenario_id is None or request.initial_state is None:
                raise CreateGameError(ErrorCode.INVALID_INPUT, "用户场景建局缺少场景初始状态")
            custom_initial_state = request.initial_state
        engine_kwargs: dict[str, Any] = {
            "save_seed": save_seed,
            "idempotency_key": request.idempotency_key,
            "scenario": request.scenario,
            "initial_state": card_initial_state
            if card_initial_state is not None
            else custom_initial_state,
            "scenario_id": request.scenario_id,
            "card_id": request.card_id,
        }
        if request.world_book_id is not None:
            engine_kwargs["world_book_id"] = request.world_book_id
            engine_kwargs["world_book_name"] = world_book_name
        engine_result = self._engine.create_game(**engine_kwargs)
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
            state = None
            try:
                record = self._repo.load(request.game_id)
            except Exception:  # 读状态失败按「无场景信息」降级，不掩盖原始业务失败
                record = None
            if record is not None:
                state = record.state
            return _with_failure_hints(result, state)
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
        # D3：剧情/用户场景已到结局（读时求值，不写状态）后拒绝新回合；旧三场景恒不触发。
        ended = self._ending_of(record.state)
        if ended is not None:
            return TurnResult.failed(
                ErrorCode.INVALID_INPUT,
                version=record.version,
                turn_id=record.state.turn,
                text=_STORY_ENDED_TEXT,
            )
        try:
            # H4：本回合世界设定句在进入具体链路前统一计算（旧场景/未绑定 → 空列表）。
            lore_entries = self._lore_entries_for(request.game_id, record.state, request.text)
            if self.multi_actor_enabled:
                return self._run_multi_actor(request, record, store_key, request_hash, lore_entries)
            return self._run_single_actor(request, record, store_key, request_hash, lore_entries)
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
        lore_entries: Sequence[str] = (),
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
            return _with_failure_hints(
                TurnResult.failed(
                    gate_result.error_code or ErrorCode.INTERNAL,
                    version=record.version,
                    turn_id=state.turn,
                    text=gate_result.message or _ACTION_REJECTED_TEXT,
                ),
                state,
                _safe_player_reason(gate_result.message) or _context_reason(state, outcome),
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
                return _with_failure_hints(
                    TurnResult.failed(
                        preconditions.error_code or ErrorCode.INTERNAL,
                        version=record.version,
                        turn_id=state.turn,
                        text=preconditions.message or _ACTION_REJECTED_TEXT,
                    ),
                    state,
                    _safe_player_reason(preconditions.message) or _context_reason(state, outcome),
                )
        # H4/L1：story 存档进入受伤房间（如枯井寒气）→ 同回合追加一条 ENVIRONMENT_DAMAGE 草稿。
        damage_draft = self._environment_damage_draft(state=state, drafts=drafts, turn_id=turn_id)
        if damage_draft is not None:
            drafts.append(damage_draft)
        result_data = dict(gate_result.data or {})
        if damage_draft is not None:
            result_data["environment"] = self._environment_payload(state, damage_draft)
        if lore_entries:
            result_data["lore_entries"] = list(lore_entries)
        text, narrative_source = self._render_with_source(
            action=outcome, events=drafts, state=state, result_data=result_data
        )

        if not drafts:
            # H4-L3 口径：零事件只读回合没有可展示的叙事来源（前端空 → 「来源未知」），
            # 只有写事件回合才带 template/llm（事件 result.narrative_source 始终记录实际来源）。
            response = TurnResult.success(
                turn_id=state.turn,
                version=record.version,
                text=text,
                suggested_actions=_success_suggestions(state),
                narrative_source=None,
            )
            self._save_idempotent(store_key, request_hash, response.model_dump(mode="json"))
            return response

        narrated = [
            draft.model_copy(
                update={
                    "result": {
                        **draft.result,
                        "narrative": text,
                        "narrative_source": narrative_source,
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
            return _with_failure_hints(
                TurnResult.failed(
                    engine_result.error_code or ErrorCode.INTERNAL,
                    version=record.version,
                    turn_id=state.turn,
                    text=engine_result.message or _ACTION_REJECTED_TEXT,
                ),
                state,
                _safe_player_reason(engine_result.message) or _context_reason(state, outcome),
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
        state_after = state.apply(narrated)
        response = TurnResult.success(
            turn_id=committed_turn,
            version=committed_version,
            text=text,
            events=engine_result.events,
            suggested_actions=_success_suggestions(state_after),
            narrative_source=narrative_source,
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
    def _entry_needs_judge(self, entry: QueueEntry) -> bool:
        """该提案工具是否需要裁判（``resolution_policy=agent_assisted``；未知工具为 False）。"""
        if self._registry is None:
            return False
        try:
            spec = self._registry.get(entry.proposal.tool)
        except KeyError:
            return False
        return spec.resolution_policy is ResolutionPolicy.AGENT_ASSISTED

    def _collect_judgements(
        self,
        entries: Sequence[QueueEntry],
        state: GameState,
        *,
        turn_id: int,
        save_seed: str,
        events: Sequence[Event],
    ) -> list[JudgeOutput | None] | None:
        """构建与 ``entries`` 同序等长的裁判证据槽位（M5E-2，第 9 章 §9.2 步骤 6）。

        - ``judge=None``（默认）或无需裁判 → 返回 ``None``：**不构建感知切片、零模型调用**；
        - 仅对 ``agent_assisted`` 提案调用裁判，且每回合最多 ``_MAX_JUDGE_CALLS_PER_TURN`` 次
          （第 8 章 §8.3 / 第 12 章 §12.8）；未获裁判的槽位保持 ``None`` → Resolver 走确定性
          兜底（保守拒绝）；
        - 裁判调用在 CAS 重试循环外只做一次（第 11 章 §11.3 "重试只重裁决、不重调模型"）；
        - 裁判 Agent 自身失败返回 ``None``；这里再包一层异常保护，裁判绝不影响回合。
        """
        if self._judge is None or self._registry is None or not entries:
            return None
        slots: list[JudgeOutput | None] = [None] * len(entries)
        judged = 0
        for index, entry in enumerate(entries):
            if judged >= _MAX_JUDGE_CALLS_PER_TURN:
                break
            if not self._entry_needs_judge(entry):
                continue
            perception = self._query.build_perception_slice(
                state,
                entry.proposal.actor_id,
                turn_id=turn_id,
                save_seed=save_seed,
                events=events,
            )
            try:
                slots[index] = self._judge.evaluate(
                    perception,
                    entry.proposal,
                    turn_id=turn_id,
                    save_seed=save_seed,
                )
            except Exception:  # 裁判异常不得中断回合（确定性兜底）
                slots[index] = None
            judged += 1
        return slots

    def _run_multi_actor(
        self,
        request: TurnRequest,
        record: GameRecord,
        store_key: str,
        request_hash: str,
        lore_entries: Sequence[str] = (),
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
        if lore_entries:
            # H4：世界设定句通过 result_data 传给叙事器（不改 NarratorLike 签名）；
            # 只读动作由 TemplateNarrator 追加 1 句设定，LLM 叙事器读同一字段做 prompt 注入。
            read_only_data = {**read_only_data, "lore_entries": list(lore_entries)}

        queue = ProposalQueue(turn_id=turn_id)
        player_entry: QueueEntry | None = None
        if player_proposal is not None:
            player_entry = queue.enqueue(player_proposal, source=ProposalSource.PLAYER)
        if self._agent_scheduler is not None:
            # M5-023 接线：调度器一次返回本回合全部 NPC 提案（批量/休眠/审计由其负责）；
            # 默认 None 时走下方原逐 Agent 循环，M2–M4 行为与 golden 逐字不变。
            scheduled = self._agent_scheduler.collect(
                state,
                events_history,
                turn_id=turn_id,
                save_seed=save_seed,
                player_action=outcome,
                character_agents=self._character_agents,
            )
            for npc_proposal in scheduled:
                if npc_proposal.turn_id != turn_id:
                    continue
                queue.enqueue(npc_proposal, source=ProposalSource.NPC)
        else:
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
                    npc_proposals = agent.propose(
                        npc_perception, turn_id=turn_id, save_seed=save_seed
                    )
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
        # M5E-2 步骤 6：裁判证据在 CAS 重试循环外构建一次（重试只重裁决、不重调模型）。
        # judge=None（默认）→ None：不构建感知切片、零模型调用；无 agent_assisted 提案亦然。
        judgements = self._collect_judgements(
            drained,
            state,
            turn_id=turn_id,
            save_seed=save_seed,
            events=events_history,
        )

        current = record
        text = ""
        attempt = 0
        for attempt in range(1, _MAX_SUBMIT_ATTEMPTS + 1):
            resolutions = self._resolver.resolve(drained, current.state, judgements)
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
                # 对外文案保持中文（2G 文字风格）；裁决理由（可能为规则层英文诊断）只进审计；
                # 其中 ActionService 给出的中文具体原因（如「「北」边没有出口」）经
                # ``_safe_player_reason`` 过滤后作为失败 hint 展示，玩家能看到「为什么」。
                rejected_reason = (
                    _safe_player_reason(player_resolution.reason)
                    if player_resolution is not None
                    else None
                ) or _context_reason(state, outcome)
                return _with_failure_hints(
                    TurnResult.failed(
                        error_code,
                        version=current.version,
                        turn_id=state.turn,
                        text=_ACTION_REJECTED_TEXT,
                    ),
                    state,
                    rejected_reason,
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
            # H4/L1：story 存档进入受伤房间 → 同回合给玩家批次追加一条 ENVIRONMENT_DAMAGE。
            damage_draft = self._environment_damage_draft(
                state=state, drafts=player_drafts, turn_id=turn_id
            )
            if damage_draft is not None:
                player_drafts.append(damage_draft)
                read_only_data = {
                    **read_only_data,
                    "environment": self._environment_payload(state, damage_draft),
                }
            text, narrative_source = self._render_multi_actor(
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
                # H4-L3：多角色链路的零事件（只读/全部零草稿）回合同样不带叙事来源。
                response = TurnResult.success(
                    turn_id=state.turn,
                    version=current.version,
                    text=text,
                    suggested_actions=_success_suggestions(state),
                    narrative_source=None,
                )
                self._save_idempotent(store_key, request_hash, response.model_dump(mode="json"))
                return response

            narrated = [
                draft.model_copy(
                    update={
                        "result": {
                            **draft.result,
                            "narrative": text,
                            "narrative_source": narrative_source,
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
                state_after = current.state.apply(narrated)
                response = TurnResult.success(
                    turn_id=committed_turn,
                    version=committed_version,
                    text=text,
                    events=engine_result.events,
                    suggested_actions=_success_suggestions(state_after),
                    narrative_source=narrative_source,
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
                return _with_failure_hints(
                    TurnResult.failed(
                        engine_result.error_code or ErrorCode.INTERNAL,
                        version=current.version,
                        turn_id=state.turn,
                        text=engine_result.message or _ACTION_REJECTED_TEXT,
                    ),
                    state,
                    _safe_player_reason(engine_result.message) or _context_reason(state, outcome),
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
    ) -> tuple[str, str]:
        """玩家事件用叙事器渲染（返回 ``(text, narrative_source)``），NPC 事件拼确定性短句。"""
        base, narrative_source = self._render_with_source(
            action=action, events=player_drafts, state=state, result_data=read_only_data
        )
        clause = _npc_event_clause(state, npc_drafts)
        return _compose_text(base, clause), narrative_source

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
        return self._render_with_source(
            action=action, events=events, state=state, result_data=result_data
        )[0]

    def _render_with_source(
        self,
        *,
        action: ParseOutcome,
        events: list[Event],
        state: GameState,
        result_data: dict[str, Any],
    ) -> tuple[str, str]:
        """返回 ``(text, narrative_source)``：LLM 叙事器带来源元数据，其余一律 ``template``。

        ``narrative_source`` 按 D-007-2 只有 ``llm``/``template`` 两值：LLM 叙事失败回退时
        由 Agent 自报 ``template``（``render_with_meta``），默认/模板路径恒为 ``template``。
        """
        if self._narrator is not None:
            render_with_meta = getattr(self._narrator, "render_with_meta", None)
            if callable(render_with_meta):
                text, source = render_with_meta(
                    action=action, events=events, state=state, result_data=result_data
                )
                return text, source
            return (
                self._narrator.render(
                    action=action, events=events, state=state, result_data=result_data
                ),
                "template",
            )
        return (
            _fallback_text(action=action, events=events, state=state, result_data=result_data),
            "template",
        )

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
    "AgentSchedulerLike",
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
