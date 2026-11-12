"""用户自上传场景 JSON 的领域层（设计契约：``docs/design/user-scenario-modules.md`` §2/§3.1/§3.2；
v2 扩展见 ``docs/design/scenario-dsl-v2.md``，ADR ``D-N1-1``）。

本模块只做四件事，全部是**纯函数/纯数据**（不读库、不读配置、不联网、不写状态）：

1. **定义模型**：``ScenarioDefinition`` 及其子模型（房间/物品/NPC/目标/结局/触发/对话/flags），
   ``extra="forbid"``：未知字段直接报错，错误信息带 JSON 路径（``rooms[2].exits.east``）。
2. **校验**：``parse_definition(raw)`` 把一段 JSON 解析为 ``ParsedDefinition``（errors/warnings/
   summary），导入接口只需判断 ``ok``；错误不抛异常，便于接入层原样返回给玩家。
   v1（``schema_version=1``）逐字兼容；v2（``schema_version=2``）在 v1 之上加法扩展
   flag/目标进度/物品/NPC/时间条件与 ``flags`` 派生命题，仍白名单求值、无 eval。
3. **确定性 ID 与建局**：``derive_ids`` 用 ``sha256(scenario_id|kind|key)`` + Crockford base32
   派生 26 位 ULID 形态 ID（回放/``state_hash`` 可复算）；``build_initial_state`` 把定义物化成
   既有 ``GameState``（**玩家槽位固定全局 ``PLAYER_ID``**，所有层按该常量取玩家）。
4. **运行时钩子**：``UserScenarioHooks`` 按定义实现目标/结局/进入伤害/NPC 回复四个钩子，
   与内置场景钩子同接口；``scenario_runtime.ScenarioRuntime`` 负责按房间/NPC ID 索引与解析。

条件求值只读既有派生状态（``GameState`` 的 turn/world_time/characters/rooms/items/npcs/
quests/rng_state）与目标进度，不写状态、不抽随机、不读时钟。

约束（与开发文档一致）：不新增 ``EventType``、不给 ``GameState`` 加字段（否则旧 ``state_hash``
漂移）、不执行任何用户提供的表达式/脚本（条件 DSL 只认白名单原子谓词）。内置 4 个场景完全
不经过本模块，旧 golden / demo / console 逐字不变。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from textgame.domain.models import NPC, Character, GameState, Item, RngState, Room
from textgame.domain.scenario import PLAYER_ID
from textgame.domain.types import Direction

__all__ = [
    "MAX_DEFINITION_BYTES",
    "MIN_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "ParsedDefinition",
    "ScenarioDefinition",
    "ScenarioIds",
    "ScenarioIssue",
    "UserScenarioHooks",
    "build_initial_state",
    "derive_ids",
    "entity_id",
    "evaluate_condition",
    "parse_definition",
]

#: 场景定义 schema 版本（v2 为当前最高版本；v1 逐字兼容读取，更高版本给明确错误而不是静默降级）。
SCHEMA_VERSION = 2

#: 仍兼容读取的最低 schema 版本（v1 文件语义逐字不变；旧读取器读 v2 会按上限拒绝）。
MIN_SCHEMA_VERSION = 1

#: 导入 dry-run 用的固定 scenario_id / save_seed（只派生合法 ID，不影响校验语义）。
_DRY_RUN_SCENARIO_ID = "scn_00000000000000000000000000"
_DRY_RUN_SAVE_SEED = "import-dry-run"

#: 导入上限（与人物卡导入同量级；避免单份定义把事件/内存撑爆）。
MAX_DEFINITION_BYTES = 256 * 1024

#: 单个错误列表上限（防止超大 JSON 生成淹没前端）。
_MAX_ERRORS = 50

_KEY_PATTERN = r"^[a-z0-9_]{1,32}$"
_SLUG_PATTERN = r"^[a-z0-9-]{2,32}$"

#: Crockford Base32 字符集（与 ULID 一致，保证派生 ID 通过领域 ID 正则）。
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

_DIRECTIONS: frozenset[str] = frozenset(
    {"north", "south", "east", "west", "up", "down", "in", "out"}
)

#: 玩家属性允许的键（与 ``Character._known_attribute_keys`` 同口径；R-02）。
#: ``max_hp`` 由 ``PlayerSpec.max_hp`` 单独声明并在建局时覆盖，这里允许出现但不以其为准。
_PLAYER_ATTRIBUTE_KEYS: frozenset[str] = frozenset(
    {"str", "dex", "con", "int", "wis", "cha", "max_hp"}
)

#: ``Condition`` 上全部谓词/组合子字段名：**恰好一个非空**（顺序仅用于提示与遍历）。
#: v1 十个键语义冻结；其后是 v2 加法（需 ``schema_version=2``）。
_CONDITION_FIELDS: tuple[str, ...] = (
    # v1（逐字兼容，语义不变）
    "in_room",
    "has_item",
    "has_item_tag",
    "npc_state",
    "hp_lte",
    "hp_gte",
    # v2 加法（白名单谓词，只读既有派生状态）
    "flag",
    "objective_done",
    "has_item_tag_count",
    "item_in_room",
    "npc_in_room",
    "turn_gte",
    "turn_lte",
    "world_time_gte",
    "world_time_lte",
    # 组合子（v1/v2 共用）
    "all",
    "any",
    "negate",
    "always",
)

#: 仅 ``schema_version=2`` 允许的条件字段（v1 声明中出现 → 结构化错误，不静默降级）。
_V2_ONLY_CONDITION_FIELDS: tuple[str, ...] = (
    "flag",
    "objective_done",
    "has_item_tag_count",
    "item_in_room",
    "npc_in_room",
    "turn_gte",
    "turn_lte",
    "world_time_gte",
    "world_time_lte",
)

#: 条件里数量/阈值类字段的上限（整数比较用，防止畸形大数进入求值/提示）。
_CONDITION_COUNT_LIMIT = 1_000_000


# ---------------------------------------------------------------------------
# 条件 DSL
# ---------------------------------------------------------------------------


class NpcStateCondition(BaseModel):
    """``{"npc_state": {"npc": key, "state": dialogue_state}}`` 的载荷。"""

    model_config = ConfigDict(extra="forbid")

    npc: str = Field(pattern=_KEY_PATTERN)
    state: str = Field(min_length=1, max_length=32)


class ItemTagCountCondition(BaseModel):
    """``{"has_item_tag_count": {"tag": t, "at_least": n}}`` 的载荷（收集类目标）。"""

    model_config = ConfigDict(extra="forbid")

    tag: str = Field(min_length=1, max_length=32)
    at_least: int = Field(ge=1, le=64)


class ItemRoomCondition(BaseModel):
    """``{"item_in_room": {"item": key, "room": key}}`` 的载荷（物品仍在某房间）。"""

    model_config = ConfigDict(extra="forbid")

    item: str = Field(pattern=_KEY_PATTERN)
    room: str = Field(pattern=_KEY_PATTERN)


class NpcRoomCondition(BaseModel):
    """``{"npc_in_room": {"npc": key, "room": key}}`` 的载荷（NPC 当前所在房间）。"""

    model_config = ConfigDict(extra="forbid")

    npc: str = Field(pattern=_KEY_PATTERN)
    room: str = Field(pattern=_KEY_PATTERN)


class Condition(BaseModel):
    """条件 DSL 的一个节点：**恰好**一个键（原子谓词或组合子）。

    ``when`` 缺失时语义由调用方决定：目标/结局按「不满足」，建议按「恒真」。
    v1 十个键（in_room/.../always）语义冻结；v2 加法键只在 ``schema_version=2`` 下合法。
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    # v1 原子谓词（语义冻结）
    in_room: str | None = Field(default=None, pattern=_KEY_PATTERN)
    has_item: str | None = Field(default=None, pattern=_KEY_PATTERN)
    has_item_tag: str | None = Field(default=None, min_length=1, max_length=32)
    npc_state: NpcStateCondition | None = None
    hp_lte: int | None = None
    hp_gte: int | None = None
    # v2 原子谓词（只读既有派生状态；flags 为顶层 `flags` 声明的派生命题）
    flag: str | None = Field(default=None, pattern=_KEY_PATTERN)
    objective_done: str | None = Field(default=None, pattern=_KEY_PATTERN)
    has_item_tag_count: ItemTagCountCondition | None = None
    item_in_room: ItemRoomCondition | None = None
    npc_in_room: NpcRoomCondition | None = None
    turn_gte: int | None = Field(default=None, ge=1, le=_CONDITION_COUNT_LIMIT)
    turn_lte: int | None = Field(default=None, ge=1, le=_CONDITION_COUNT_LIMIT)
    world_time_gte: int | None = Field(default=None, ge=0, le=_CONDITION_COUNT_LIMIT)
    world_time_lte: int | None = Field(default=None, ge=0, le=_CONDITION_COUNT_LIMIT)
    # 组合子（v1/v2 共用）
    all: tuple[Condition, ...] | None = Field(default=None, min_length=1, max_length=16)
    any: tuple[Condition, ...] | None = Field(default=None, min_length=1, max_length=16)
    negate: Condition | None = Field(default=None, alias="not")
    always: bool | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> Condition:
        """必须且只能有一个谓词/组合子（防止 ``{"in_room":..,"hp_lte":..}`` 的歧义）。"""
        present = [name for name in _CONDITION_FIELDS if getattr(self, name) is not None]
        if len(present) != 1:
            raise ValueError(
                "条件必须且只能包含一个键（v1：in_room/has_item/has_item_tag/npc_state/"
                "hp_lte/hp_gte；v2：flag/objective_done/has_item_tag_count/item_in_room/"
                "npc_in_room/turn_gte/turn_lte/world_time_gte/world_time_lte；"
                "组合子：all/any/not/always）"
            )
        return self


def _rebuild_condition() -> None:
    """重建递归模型（前向引用在类体内定义，模块加载后一次性 resolve）。"""
    Condition.model_rebuild()


_rebuild_condition()


@dataclass(frozen=True)
class _ConditionEnv:
    """一次求值所需的定义内查表（v2 flag / 目标进度；v1 定义为空表）。"""

    flags: Mapping[str, Condition]
    objectives: Mapping[str, Condition | None]


_EMPTY_CONDITION_ENV = _ConditionEnv(flags={}, objectives={})

#: 条件依赖节点：``("flag", key)`` / ``("objective", key)``（环检测与求值缓存共用）。
_ConditionNode = tuple[str, str]


def _condition_env(definition: ScenarioDefinition | None) -> _ConditionEnv:
    """从定义构建 flag/目标进度查表（一次性；与状态无关，可在钩子构造期缓存）。"""
    if definition is None:
        return _EMPTY_CONDITION_ENV
    flags = {flag.key: flag.when for flag in definition.flags}
    goal = definition.goal
    objectives = {objective.key: objective.when for objective in goal.objectives} if goal else {}
    return _ConditionEnv(flags=flags, objectives=objectives)


def evaluate_condition(
    condition: Condition,
    state: GameState,
    ids: ScenarioIds,
    *,
    definition: ScenarioDefinition | None = None,
) -> bool:
    """在给定状态上求值一个条件（纯函数；只读，不产生任何副作用）。

    ``ids`` 提供用户 key → 派生领域 ID 的映射；所有实体查找都走派生 ID，
    因此求值结果只依赖「定义 + 状态」，回放可复算。

    ``definition`` 是 v2 的 flag/目标进度上下文（v1 条件不需要；缺省时 v2 的
    ``flag``/``objective_done`` 恒为 ``False``——正常导入的定义不会走到该分支，
    因为未知引用在校验期已被拒绝）。
    """
    return _evaluate_condition(condition, state, ids, _condition_env(definition))


def _evaluate_condition(
    condition: Condition,
    state: GameState,
    ids: ScenarioIds,
    env: _ConditionEnv,
) -> bool:
    """``evaluate_condition`` 的求值实现：带 memo 与环防御（每个 flag/目标最多求值一次）。"""
    return _evaluate(condition, state, ids, env, {}, frozenset())


def _evaluate_named(
    *,
    kind: str,
    key: str,
    catalog: Mapping[str, Condition | None],
    state: GameState,
    ids: ScenarioIds,
    env: _ConditionEnv,
    memo: dict[_ConditionNode, bool],
    visiting: frozenset[_ConditionNode],
) -> bool:
    """求值一个具名派生节点（flag / 目标进度）：带缓存、环上返回 ``False``（fail-closed）。"""
    node = (kind, key)
    cached = memo.get(node)
    if cached is not None:
        return cached
    if node in visiting:
        return False
    condition = catalog.get(key)
    if condition is None:
        return False
    value = _evaluate(condition, state, ids, env, memo, visiting | {node})
    memo[node] = value
    return value


def _evaluate(
    condition: Condition,
    state: GameState,
    ids: ScenarioIds,
    env: _ConditionEnv,
    memo: dict[_ConditionNode, bool],
    visiting: frozenset[_ConditionNode],
) -> bool:
    """条件树递归求值（白名单谓词；组合子短路；不读取/修改任何全局状态）。"""
    if condition.always is not None:
        return bool(condition.always)
    player = state.characters.get(PLAYER_ID)
    if condition.in_room is not None:
        return player is not None and player.location == ids.rooms.get(condition.in_room)
    if condition.has_item is not None:
        item_id = ids.items.get(condition.has_item)
        return player is not None and item_id is not None and item_id in player.inventory
    if condition.has_item_tag is not None:
        if player is None:
            return False
        tag = condition.has_item_tag
        return any(
            item is not None and tag in item.tags
            for item in (state.items.get(inv_id) for inv_id in player.inventory)
        )
    if condition.npc_state is not None:
        npc_id = ids.npcs.get(condition.npc_state.npc)
        if npc_id is None:
            return False
        npc = state.npcs.get(npc_id)
        return npc is not None and npc.dialogue_state == condition.npc_state.state
    if condition.hp_lte is not None:
        return player is not None and player.hp <= condition.hp_lte
    if condition.hp_gte is not None:
        return player is not None and player.hp >= condition.hp_gte
    if condition.flag is not None:
        return _evaluate_named(
            kind="flag",
            key=condition.flag,
            catalog=env.flags,
            state=state,
            ids=ids,
            env=env,
            memo=memo,
            visiting=visiting,
        )
    if condition.objective_done is not None:
        return _evaluate_named(
            kind="objective",
            key=condition.objective_done,
            catalog=env.objectives,
            state=state,
            ids=ids,
            env=env,
            memo=memo,
            visiting=visiting,
        )
    if condition.has_item_tag_count is not None:
        if player is None:
            return False
        tag = condition.has_item_tag_count.tag
        count = sum(
            1
            for inv_id in player.inventory
            if (item := state.items.get(inv_id)) is not None and tag in item.tags
        )
        return count >= condition.has_item_tag_count.at_least
    if condition.item_in_room is not None:
        item_id = ids.items.get(condition.item_in_room.item)
        room_id = ids.rooms.get(condition.item_in_room.room)
        if item_id is None or room_id is None:
            return False
        room = state.rooms.get(room_id)
        return room is not None and item_id in room.item_ids
    if condition.npc_in_room is not None:
        npc_id = ids.npcs.get(condition.npc_in_room.npc)
        room_id = ids.rooms.get(condition.npc_in_room.room)
        if npc_id is None or room_id is None:
            return False
        npc = state.npcs.get(npc_id)
        return npc is not None and npc.room_id == room_id
    if condition.turn_gte is not None:
        return state.turn >= condition.turn_gte
    if condition.turn_lte is not None:
        return state.turn <= condition.turn_lte
    if condition.world_time_gte is not None:
        return state.world_time >= condition.world_time_gte
    if condition.world_time_lte is not None:
        return state.world_time <= condition.world_time_lte
    if condition.all is not None:
        return all(_evaluate(item, state, ids, env, memo, visiting) for item in condition.all)
    if condition.any is not None:
        return any(_evaluate(item, state, ids, env, memo, visiting) for item in condition.any)
    if condition.negate is not None:
        return not _evaluate(condition.negate, state, ids, env, memo, visiting)
    return False


# ---------------------------------------------------------------------------
# 定义模型
# ---------------------------------------------------------------------------


class ScenarioMeta(BaseModel):
    """场景展示信息（列表/详情 UI 用；不进入 GameState）。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=48)
    author: str = Field(default="", max_length=32)
    description: str = Field(default="", max_length=200)
    tags: list[str] = Field(default_factory=list, max_length=8)


class OnEnterSpec(BaseModel):
    """进入房间触发（v1 只支持环境伤害 + 提示文案）。"""

    model_config = ConfigDict(extra="forbid")

    hp_delta: int = Field(default=0, ge=-50, le=0)
    hint: str | None = Field(default=None, max_length=120)


class RoomSpec(BaseModel):
    """一个房间的定义（``key`` 是作者可读标识，物化时映射为派生 ``room_`` ID）。"""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=_KEY_PATTERN)
    name: str = Field(min_length=1, max_length=32)
    description: str = Field(min_length=1, max_length=500)
    exits: dict[Direction, str] = Field(default_factory=dict)
    items: list[str] = Field(default_factory=list, max_length=32)
    npcs: list[str] = Field(default_factory=list, max_length=16)
    ambient_tags: list[str] = Field(default_factory=list, max_length=8)
    on_enter: OnEnterSpec | None = None


class ItemSpec(BaseModel):
    """物品定义；``tags``/``stats`` 复用既有 ``Item`` 语义（heal/hp_delta/quest/weapon/...）。"""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=_KEY_PATTERN)
    name: str = Field(min_length=1, max_length=32)
    description: str = Field(default="", max_length=500)
    tags: list[str] = Field(default_factory=list, max_length=8)
    stats: dict[str, int | float] = Field(default_factory=dict)
    rarity: Literal["common", "uncommon", "rare", "epic", "legendary"] = "common"
    stackable: bool = False


class DialogueTopicSpec(BaseModel):
    """NPC 话题分支：任一 ``keywords`` 命中玩家输入即返回 ``reply``。"""

    model_config = ConfigDict(extra="forbid")

    keywords: list[str] = Field(min_length=1, max_length=8)
    reply: str = Field(min_length=1, max_length=500)
    dialogue_state: str | None = Field(default=None, min_length=1, max_length=32)


class DialogueSpec(BaseModel):
    """NPC 对话定义；缺省时 ``talk_to`` 走通用文案（与旧场景一致）。"""

    model_config = ConfigDict(extra="forbid")

    default: str = Field(default="", max_length=500)
    topics: list[DialogueTopicSpec] = Field(default_factory=list, max_length=8)


class NpcSpec(BaseModel):
    """NPC 定义（``room`` 决定初始位置；``dialogue`` 可选）。"""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=_KEY_PATTERN)
    name: str = Field(min_length=1, max_length=24)
    room: str = Field(pattern=_KEY_PATTERN)
    description: str = Field(default="", max_length=500)
    dialogue: DialogueSpec | None = None


class PlayerSpec(BaseModel):
    """玩家初始属性；``location``/``inventory`` 用作者 key 引用房间/物品。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="旅人", min_length=1, max_length=24)
    hp: int = Field(default=20, ge=0, le=999)
    max_hp: int = Field(default=20, ge=1, le=999)
    attributes: dict[str, int] = Field(
        default_factory=lambda: {
            "str": 10,
            "dex": 10,
            "con": 10,
            "int": 10,
            "wis": 10,
            "cha": 10,
        }
    )
    location: str = Field(pattern=_KEY_PATTERN)
    inventory: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("attributes")
    @classmethod
    def _attributes_sane(cls, value: dict[str, int]) -> dict[str, int]:
        """R-02：属性键/值域必须与 ``Character`` 一致，否则导入成功但建局 500。"""
        unknown = set(value) - _PLAYER_ATTRIBUTE_KEYS
        if unknown:
            raise ValueError(
                f"未知属性键 {sorted(unknown)}（只允许 str/dex/con/int/wis/cha/max_hp）"
            )
        if any(not (-10 <= int(number) <= 100) for number in value.values()):
            raise ValueError("属性值必须在 [-10, 100] 之间")
        return value

    @model_validator(mode="after")
    def _hp_within_max(self) -> PlayerSpec:
        """``hp`` 不得超过 ``max_hp``（超了直接报错，避免开局即异常状态）。"""
        if self.hp > self.max_hp:
            raise ValueError("player.hp 不能大于 player.max_hp")
        return self


class ObjectiveSpec(BaseModel):
    """目标链的一步：``when`` 满足即 ``done=true``（缺失视为未完成）。"""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=_KEY_PATTERN)
    text: str = Field(min_length=1, max_length=120)
    when: Condition | None = None


class GoalSpec(BaseModel):
    """目标面板视图（``/view.goal`` 的数据源）。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=48)
    description: str = Field(default="", max_length=300)
    objectives: list[ObjectiveSpec] = Field(min_length=1, max_length=12)


class EndingSpec(BaseModel):
    """结局：按 ``priority`` 降序、同权按声明顺序取第一个满足项。"""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=_KEY_PATTERN)
    kind: Literal["win", "lose"] = "win"
    title: str = Field(min_length=1, max_length=48)
    text: str = Field(min_length=1, max_length=800)
    priority: int = Field(default=0, ge=-100, le=100)
    when: Condition | None = None


class SuggestionSpec(BaseModel):
    """建议文案（v1 只用于 /view.suggested_actions 的补充，不改变解析结果）。"""

    model_config = ConfigDict(extra="forbid")

    when: Condition | None = None
    text: str = Field(min_length=1, max_length=120)


class FlagSpec(BaseModel):
    """v2 派生命题：``{"key": k, "when": cond}``；``{"flag": k}`` 求值其 ``when``。

    flag **不是持久化变量**，而是读时求值的命名条件（别名/复用）；历史语义（曾经发生）
    不在 v2 表达范围内（见 ``docs/design/scenario-dsl-v2.md`` §7）。
    """

    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=_KEY_PATTERN)
    when: Condition


class ScenarioDefinition(BaseModel):
    """一份用户场景定义的完整模型（``schema_version`` 为 1 或 2）。

    - ``schema_version=1``：v1 文件逐字兼容读取（不接受 v2 字段/谓词，声明即错误）；
    - ``schema_version=2``：v1 全部字段 + ``flags`` 派生命题 + v2 条件谓词。
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(ge=MIN_SCHEMA_VERSION, le=SCHEMA_VERSION)
    slug: str = Field(pattern=_SLUG_PATTERN)
    meta: ScenarioMeta
    player: PlayerSpec
    rooms: list[RoomSpec] = Field(min_length=1, max_length=40)
    items: list[ItemSpec] = Field(default_factory=list, max_length=80)
    npcs: list[NpcSpec] = Field(default_factory=list, max_length=20)
    flags: list[FlagSpec] = Field(default_factory=list, max_length=16)
    goal: GoalSpec | None = None
    endings: list[EndingSpec] = Field(default_factory=list, max_length=6)
    suggestions: list[SuggestionSpec] = Field(default_factory=list, max_length=16)


# ---------------------------------------------------------------------------
# 解析/校验（结构化错误，不抛异常）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScenarioIssue:
    """一条校验问题：``path`` 为 JSON 路径（如 ``rooms[2].exits.east``），``message`` 中文。"""

    path: str
    message: str

    def as_dict(self) -> dict[str, str]:
        """转成可直接 JSON 序列化的形状（接入层原样返回）。"""
        return {"path": self.path, "message": self.message}


@dataclass(frozen=True)
class ParsedDefinition:
    """解析结果：``ok`` 为真时 ``definition`` 非空，``summary`` 供列表/详情展示。"""

    definition: ScenarioDefinition | None
    errors: tuple[ScenarioIssue, ...]
    warnings: tuple[ScenarioIssue, ...]
    summary: dict[str, int]

    @property
    def ok(self) -> bool:
        """是否可导入（无阻断性错误）。"""
        return self.definition is not None and not self.errors


def _format_loc(loc: tuple[Any, ...]) -> str:
    """Pydantic error loc → JSON 路径（``('rooms', 2, 'exits', 'east')`` → ``rooms[2].exits.east``）。"""
    rendered = ""
    for part in loc:
        if isinstance(part, int):
            rendered += f"[{part}]"
        else:
            rendered += f".{part}" if rendered else str(part)
    return rendered or "$"


def _reject_non_finite(value: str) -> Any:
    """``json.loads(parse_constant=...)``：拒绝 NaN/Infinity（R-11，避免后置 500）。"""
    raise ValueError(f"JSON 不允许非有限数值: {value}")


def _parse_json_body(raw: str | bytes) -> tuple[Any, ScenarioIssue | None]:
    """读取并解析 JSON；返回 ``(payload, issue)``（issue 非空 = 阻断错误）。"""
    data = raw.encode("utf-8") if isinstance(raw, str) else raw
    if len(data) > MAX_DEFINITION_BYTES:
        return None, ScenarioIssue(
            "$", f"文件过大：{len(data)} 字节（上限 {MAX_DEFINITION_BYTES} 字节）"
        )
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        return None, ScenarioIssue("$", f"文件不是合法 UTF-8 编码：{exc}")
    try:
        payload = json.loads(text, parse_constant=_reject_non_finite)
    except json.JSONDecodeError as exc:
        return None, ScenarioIssue(
            "$", f"不是合法 JSON：第 {exc.lineno} 行第 {exc.colno} 列 {exc.msg}"
        )
    except ValueError as exc:
        return None, ScenarioIssue("$", f"场景 JSON 含非法数值：{exc}")
    if not isinstance(payload, dict):
        return None, ScenarioIssue("$", "顶层必须是 JSON 对象（不以 [ 或字符串开头）")
    return payload, None


def _schema_version_issue(payload: dict[str, Any]) -> ScenarioIssue | None:
    """预检 ``schema_version``：只接受 1/2（整数字面量），错误路径固定 ``schema_version``。

    缺字段交给 Pydantic 的 required 错误（路径同样精确）；``true``（bool）等非整数字面量
    在这里拒绝，避免隐式转成 1。
    """
    if "schema_version" not in payload:
        return None
    value = payload["schema_version"]
    if type(value) is int and value in (MIN_SCHEMA_VERSION, SCHEMA_VERSION):
        return None
    return ScenarioIssue(
        "schema_version",
        f"不支持的 schema_version={value!r}（本版本只支持 {MIN_SCHEMA_VERSION} 或 {SCHEMA_VERSION}）",
    )


def _issue_of_validation_error(error: Mapping[str, Any]) -> ScenarioIssue:
    """Pydantic 单条错误 → 中文 ``ScenarioIssue``（保留 JSON 路径；未知谓词给出白名单提示）。"""
    loc = tuple(error.get("loc", ()))
    error_type = str(error.get("type", ""))
    message = str(error.get("msg", "字段非法"))
    if error_type == "extra_forbidden" and loc:
        unknown = str(loc[-1])
        parent = loc[-2] if len(loc) >= 2 else None
        if parent == "when":
            message = (
                f'未知谓词或组合子 "{unknown}"（只允许白名单键；v2 新谓词需 schema_version=2，'
                "见 docs/design/scenario-dsl-v2.md §3）"
            )
        else:
            message = f'未知字段 "{unknown}"（不允许多余字段；v2 新字段需 schema_version=2）'
    elif loc == ("schema_version",) and error_type != "missing":
        message = f"不支持的 schema_version（只支持 {MIN_SCHEMA_VERSION} 或 {SCHEMA_VERSION}）"
    return ScenarioIssue(_format_loc(loc), message)


def _condition_nodes(condition: Condition | None) -> list[Condition]:
    """深度优先展开条件树（供引用校验用）。"""
    if condition is None:
        return []
    nodes = [condition]
    for group in (condition.all, condition.any):
        if group is not None:
            for item in group:
                nodes.extend(_condition_nodes(item))
    if condition.negate is not None:
        nodes.extend(_condition_nodes(condition.negate))
    return nodes


def _condition_slots(definition: ScenarioDefinition) -> list[tuple[str, Condition | None]]:
    """全部 ``when`` 槽位（路径, 条件）：目标/结局/建议/flags（v2），供统一静态校验。"""
    slots: list[tuple[str, Condition | None]] = []
    if definition.goal is not None:
        for index, objective in enumerate(definition.goal.objectives):
            slots.append((f"goal.objectives[{index}].when", objective.when))
    for index, ending in enumerate(definition.endings):
        slots.append((f"endings[{index}].when", ending.when))
    for index, suggestion in enumerate(definition.suggestions):
        slots.append((f"suggestions[{index}].when", suggestion.when))
    for index, flag in enumerate(definition.flags):
        slots.append((f"flags[{index}].when", flag.when))
    return slots


def _version_gate_errors(definition: ScenarioDefinition) -> list[ScenarioIssue]:
    """v1 声明不得使用 v2 字段/谓词（``schema_version`` 是对特性级别的承诺，不静默升级）。"""
    if definition.schema_version != MIN_SCHEMA_VERSION:
        return []
    errors: list[ScenarioIssue] = []
    if definition.flags:
        errors.append(
            ScenarioIssue(
                "flags",
                "schema_version=1 不支持 flags（v2 字段）；如需使用请把 schema_version 改为 2",
            )
        )
    for path, condition in _condition_slots(definition):
        for node in _condition_nodes(condition):
            for field_name in _V2_ONLY_CONDITION_FIELDS:
                if getattr(node, field_name) is not None:
                    errors.append(
                        ScenarioIssue(
                            f"{path}.{field_name}",
                            f'谓词 "{field_name}" 需要 schema_version=2（当前为 1）',
                        )
                    )
    return errors[:_MAX_ERRORS]


def _condition_references(condition: Condition) -> list[_ConditionNode]:
    """条件树内引用的 flag / 目标进度节点（环检测用；不展开被引用节点本身）。"""
    refs: list[_ConditionNode] = []
    for node in _condition_nodes(condition):
        if node.flag is not None:
            refs.append(("flag", node.flag))
        if node.objective_done is not None:
            refs.append(("objective", node.objective_done))
    return refs


def _find_dependency_cycle(
    graph: Mapping[_ConditionNode, list[_ConditionNode]],
) -> list[_ConditionNode] | None:
    """在 flag/目标进度依赖图中找一条环；无环返回 ``None``（DFS 三色，规模 ≤28 节点）。"""
    color: dict[_ConditionNode, int] = dict.fromkeys(graph, 0)
    stack: list[_ConditionNode] = []

    def _visit(node: _ConditionNode) -> list[_ConditionNode] | None:
        color[node] = 1
        stack.append(node)
        for dep in graph.get(node, []):
            if dep not in graph:
                continue
            if color[dep] == 1:
                return [*stack[stack.index(dep) :], dep]
            if color[dep] == 0:
                found = _visit(dep)
                if found is not None:
                    return found
        stack.pop()
        color[node] = 2
        return None

    for node in graph:
        if color[node] == 0:
            found = _visit(node)
            if found is not None:
                return found
    return None


def _check_conditions(
    *,
    definition: ScenarioDefinition,
    room_keys: set[str],
    item_keys: set[str],
    npc_keys: set[str],
    flag_keys: set[str],
    objective_keys: set[str],
) -> list[ScenarioIssue]:
    """校验条件里的全部 key 引用（错误定位到 ``when`` 所在路径，含 v2 新谓词）。"""
    errors: list[ScenarioIssue] = []
    for path, condition in _condition_slots(definition):
        for node in _condition_nodes(condition):
            if node.in_room is not None and node.in_room not in room_keys:
                errors.append(ScenarioIssue(f"{path}.in_room", f'未知房间 key "{node.in_room}"'))
            if node.has_item is not None and node.has_item not in item_keys:
                errors.append(ScenarioIssue(f"{path}.has_item", f'未知物品 key "{node.has_item}"'))
            if node.npc_state is not None and node.npc_state.npc not in npc_keys:
                errors.append(
                    ScenarioIssue(f"{path}.npc_state.npc", f'未知 NPC key "{node.npc_state.npc}"')
                )
            if node.flag is not None and node.flag not in flag_keys:
                errors.append(
                    ScenarioIssue(
                        f"{path}.flag",
                        f'未知 flag key "{node.flag}"（必须先在顶层 flags 声明）',
                    )
                )
            if node.objective_done is not None and node.objective_done not in objective_keys:
                errors.append(
                    ScenarioIssue(
                        f"{path}.objective_done",
                        f'未知目标 key "{node.objective_done}"（必须是 goal.objectives 里的 key）',
                    )
                )
            if node.item_in_room is not None:
                if node.item_in_room.item not in item_keys:
                    errors.append(
                        ScenarioIssue(
                            f"{path}.item_in_room.item",
                            f'未知物品 key "{node.item_in_room.item}"',
                        )
                    )
                if node.item_in_room.room not in room_keys:
                    errors.append(
                        ScenarioIssue(
                            f"{path}.item_in_room.room",
                            f'未知房间 key "{node.item_in_room.room}"',
                        )
                    )
            if node.npc_in_room is not None:
                if node.npc_in_room.npc not in npc_keys:
                    errors.append(
                        ScenarioIssue(
                            f"{path}.npc_in_room.npc", f'未知 NPC key "{node.npc_in_room.npc}"'
                        )
                    )
                if node.npc_in_room.room not in room_keys:
                    errors.append(
                        ScenarioIssue(
                            f"{path}.npc_in_room.room", f'未知房间 key "{node.npc_in_room.room}"'
                        )
                    )
    return errors


def _check_dependency_cycles(definition: ScenarioDefinition) -> list[ScenarioIssue]:
    """flag/目标进度互相引用的环检测（跨两类节点统一依赖图；环 = 无法静态求值，必须拒绝）。"""
    graph: dict[_ConditionNode, list[_ConditionNode]] = {}
    node_paths: dict[_ConditionNode, str] = {}
    for index, flag in enumerate(definition.flags):
        node = ("flag", flag.key)
        graph[node] = _condition_references(flag.when)
        node_paths[node] = f"flags[{index}].when"
    if definition.goal is not None:
        for index, objective in enumerate(definition.goal.objectives):
            node = ("objective", objective.key)
            graph[node] = _condition_references(objective.when) if objective.when else []
            node_paths[node] = f"goal.objectives[{index}].when"
    cycle = _find_dependency_cycle(graph)
    if cycle is None:
        return []
    rendered = " → ".join(f"{kind}:{key}" for kind, key in cycle)
    path = node_paths.get(cycle[0], "flags")
    return [ScenarioIssue(path, f"条件存在循环引用：{rendered}（flag / 目标进度不能互相依赖成环）")]


def _check_references(definition: ScenarioDefinition) -> list[ScenarioIssue]:
    """校验跨数组引用、key 唯一性、位置一致性（导入门槛）。"""
    errors: list[ScenarioIssue] = []

    def _dupes(keys: list[str], path: str) -> None:
        seen: set[str] = set()
        for key in keys:
            if key in seen:
                errors.append(ScenarioIssue(path, f'重复的 key "{key}"'))
                return
            seen.add(key)

    room_keys = [room.key for room in definition.rooms]
    item_keys = [item.key for item in definition.items]
    npc_keys = [npc.key for npc in definition.npcs]
    flag_keys = [flag.key for flag in definition.flags]
    objective_keys = (
        [objective.key for objective in definition.goal.objectives]
        if definition.goal is not None
        else []
    )
    _dupes(room_keys, "rooms.key")
    _dupes(item_keys, "items.key")
    _dupes(npc_keys, "npcs.key")
    _dupes(flag_keys, "flags.key")
    _dupes(definition.player.inventory, "player.inventory")
    if definition.goal is not None:
        _dupes(objective_keys, "goal.objectives.key")
    _dupes([ending.key for ending in definition.endings], "endings.key")

    rooms = set(room_keys)
    items = set(item_keys)
    npcs = set(npc_keys)

    if definition.player.location not in rooms:
        errors.append(
            ScenarioIssue(
                "player.location",
                f'未知房间 key "{definition.player.location}"（必须是 rooms 里的 key）',
            )
        )
    for index, key in enumerate(definition.player.inventory):
        if key not in items:
            errors.append(ScenarioIssue(f"player.inventory[{index}]", f'未知物品 key "{key}"'))

    placed: dict[str, str] = {}
    inventory_keys = set(definition.player.inventory)
    for room_index, room in enumerate(definition.rooms):
        for direction, target in room.exits.items():
            if direction not in _DIRECTIONS:
                errors.append(
                    ScenarioIssue(
                        f"rooms[{room_index}].exits.{direction}",
                        f'方向 "{direction}" 非法（只允许 north/south/east/west/up/down/in/out）',
                    )
                )
            if target not in rooms:
                errors.append(
                    ScenarioIssue(
                        f"rooms[{room_index}].exits.{direction}", f'未知房间 key "{target}"'
                    )
                )
            elif target == room.key:
                # R-02：Room 模型禁止自指出口，必须在导入阶段报错而不是建局时 500。
                errors.append(
                    ScenarioIssue(
                        f"rooms[{room_index}].exits.{direction}",
                        f'房间 "{room.key}" 不能把出口指向自己',
                    )
                )
        for item_index, key in enumerate(room.items):
            if key not in items:
                errors.append(
                    ScenarioIssue(
                        f"rooms[{room_index}].items[{item_index}]", f'未知物品 key "{key}"'
                    )
                )
            elif key in inventory_keys:
                # R-02：同一 item 同时放背包和房间会让同一个 item_id 出现两处，拿取后造重复实例。
                errors.append(
                    ScenarioIssue(
                        f"rooms[{room_index}].items[{item_index}]",
                        f'物品 "{key}" 已经在玩家行囊里，不能再放到房间',
                    )
                )
            elif key in placed:
                errors.append(
                    ScenarioIssue(
                        f"rooms[{room_index}].items[{item_index}]",
                        f'物品 "{key}" 已经放在 rooms.{placed[key]}，同一件物品只能放一处',
                    )
                )
            else:
                placed[key] = room.key
        for npc_index, key in enumerate(room.npcs):
            if key not in npcs:
                errors.append(
                    ScenarioIssue(f"rooms[{room_index}].npcs[{npc_index}]", f'未知 NPC key "{key}"')
                )
            else:
                declared = next(npc for npc in definition.npcs if npc.key == key)
                if declared.room != room.key:
                    errors.append(
                        ScenarioIssue(
                            f"rooms[{room_index}].npcs[{npc_index}]",
                            f'NPC "{key}" 的 room 是 "{declared.room}"，与所在房间 "{room.key}" 不一致',
                        )
                    )

    for npc_index, npc in enumerate(definition.npcs):
        if npc.room not in rooms:
            errors.append(ScenarioIssue(f"npcs[{npc_index}].room", f'未知房间 key "{npc.room}"'))

    # ``_check_conditions`` 的错误路径已经带 ``when`` 前缀，这里统一追加。
    errors.extend(
        _check_conditions(
            definition=definition,
            room_keys=rooms,
            item_keys=items,
            npc_keys=npcs,
            flag_keys=set(flag_keys),
            objective_keys=set(objective_keys),
        )
    )
    errors.extend(_check_dependency_cycles(definition))
    return errors[:_MAX_ERRORS]


def _collect_warnings(definition: ScenarioDefinition, *, placed: set[str]) -> list[ScenarioIssue]:
    """非阻断提示：物品没放任何地方、NPC 没对话、没有目标/结局、未使用的 flag。"""
    warnings: list[ScenarioIssue] = []
    inventory_keys = set(definition.player.inventory)
    for index, item in enumerate(definition.items):
        if item.key not in placed and item.key not in inventory_keys:
            warnings.append(
                ScenarioIssue(
                    f"items[{index}]",
                    f'物品 "{item.key}" 既不在任何房间也不在玩家行囊里，玩家永远拿不到',
                )
            )
    for index, npc in enumerate(definition.npcs):
        if npc.dialogue is None:
            warnings.append(
                ScenarioIssue(f"npcs[{index}]", f'NPC "{npc.key}" 没有 dialogue，对话将走通用文案')
            )
    if definition.goal is None:
        warnings.append(ScenarioIssue("goal", "未定义 goal：/view 不会显示目标面板"))
    if not definition.endings:
        warnings.append(
            ScenarioIssue(
                "endings", "未定义 endings：将使用内置兜底结局（HP≤0 失败 / 目标全完成胜利）"
            )
        )
    referenced_flags = {
        node.flag
        for _, condition in _condition_slots(definition)
        for node in _condition_nodes(condition)
        if node.flag is not None
    }
    for index, flag in enumerate(definition.flags):
        if flag.key not in referenced_flags:
            warnings.append(
                ScenarioIssue(
                    f"flags[{index}]",
                    f'flag "{flag.key}" 未被任何条件引用（不影响玩法，可删或留给编辑器预览）',
                )
            )
    return warnings


def _summary_of(definition: ScenarioDefinition) -> dict[str, int]:
    """统计信息（列表/详情 UI 用；键固定，前端契约测试会断言）。"""
    return {
        "rooms": len(definition.rooms),
        "items": len(definition.items),
        "npcs": len(definition.npcs),
        "objectives": len(definition.goal.objectives) if definition.goal is not None else 0,
        "endings": len(definition.endings),
        "suggestions": len(definition.suggestions),
    }


def parse_definition(raw: str | bytes) -> ParsedDefinition:
    """解析并校验一段场景 JSON；返回结构化 errors/warnings/summary（不抛异常）。

    错误来源四类：JSON 语法/编码/大小、``schema_version`` 版本、Pydantic 字段错误（带路径）、
    语义引用/环错误。只有 ``ok`` 为真时才应写入存储并允许建局。

    ``schema_version=1`` 逐字兼容：v1 文件不接受 v2 字段/谓词（版本门禁先于引用校验，避免
    冗余错误）；``schema_version=2`` 在 v1 全部字段之上加法启用 v2。
    """
    payload, issue = _parse_json_body(raw)
    if issue is not None:
        return ParsedDefinition(definition=None, errors=(issue,), warnings=(), summary={})
    assert payload is not None
    version_issue = _schema_version_issue(payload)
    if version_issue is not None:
        return ParsedDefinition(definition=None, errors=(version_issue,), warnings=(), summary={})
    try:
        definition = ScenarioDefinition.model_validate(payload)
    except ValidationError as exc:
        errors = tuple(_issue_of_validation_error(item) for item in exc.errors())[:_MAX_ERRORS]
        return ParsedDefinition(definition=None, errors=errors, warnings=(), summary={})
    gate_errors = tuple(_version_gate_errors(definition))
    if gate_errors:
        return ParsedDefinition(
            definition=None, errors=gate_errors, warnings=(), summary=_summary_of(definition)
        )
    errors = tuple(_check_references(definition))
    if errors:
        return ParsedDefinition(
            definition=None, errors=errors, warnings=(), summary=_summary_of(definition)
        )
    # R-02：导入阶段就做一次建局 dry-run，把 Character/Room 等模型约束提前暴露为 422。
    build_errors = _build_dry_run_issues(definition)
    if build_errors:
        return ParsedDefinition(
            definition=None,
            errors=tuple(build_errors),
            warnings=(),
            summary=_summary_of(definition),
        )
    placed = {key for room in definition.rooms for key in room.items}
    warnings = tuple(_collect_warnings(definition, placed=placed))
    return ParsedDefinition(
        definition=definition,
        errors=(),
        warnings=warnings,
        summary=_summary_of(definition),
    )


# ---------------------------------------------------------------------------
# 确定性 ID 派生与建局
# ---------------------------------------------------------------------------


def _derive_ulid(*parts: str) -> str:
    """``sha256(parts)`` 前 130 bit → 26 位 Crockford base32（ULID 形态、纯函数）。"""
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).digest()
    value = int.from_bytes(digest[:17], "big") >> 6  # 136 bit → 130 bit
    chars: list[str] = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def entity_id(kind: Literal["room", "item", "npc"], scenario_id: str, key: str) -> str:
    """派生一个合法的领域 ID（``room_<ULID>`` 等）；同 ``scenario_id`` + ``key`` 恒得同一 ID。"""
    if kind not in ("room", "item", "npc"):
        raise ValueError(f"unsupported entity kind: {kind!r}")
    return f"{kind}_{_derive_ulid(scenario_id, kind, key)}"


@dataclass(frozen=True)
class ScenarioIds:
    """某份场景定义的 key → 派生 ID 映射（求值/钩子都靠它，避免字符串拼接分散）。"""

    scenario_id: str
    rooms: dict[str, str]
    items: dict[str, str]
    npcs: dict[str, str]

    def room(self, key: str) -> str:
        """房间 key → 派生 ``room_`` ID（调用方保证 key 已校验存在）。"""
        return self.rooms[key]

    def item(self, key: str) -> str:
        """物品 key → 派生 ``item_`` ID。"""
        return self.items[key]

    def npc(self, key: str) -> str:
        """NPC key → 派生 ``npc_`` ID。"""
        return self.npcs[key]


def derive_ids(scenario_id: str, definition: ScenarioDefinition) -> ScenarioIds:
    """为一份定义派生全部实体 ID（确定性；回放/state_hash 可复算）。"""
    return ScenarioIds(
        scenario_id=scenario_id,
        rooms={room.key: entity_id("room", scenario_id, room.key) for room in definition.rooms},
        items={item.key: entity_id("item", scenario_id, item.key) for item in definition.items},
        npcs={npc.key: entity_id("npc", scenario_id, npc.key) for npc in definition.npcs},
    )


def build_initial_state(
    definition: ScenarioDefinition,
    ids: ScenarioIds,
    *,
    save_seed: str,
) -> GameState:
    """把定义物化成初始 ``GameState``（纯函数：同定义 + 同 seed 恒得同一状态）。

    玩家槽位固定使用全局 ``PLAYER_ID``；``turn=1``、``world_time=0``、``last_seq=0``；
    目标/结局不进状态（读时求值），与 story 场景口径一致。
    """
    npcs_by_room: dict[str, list[str]] = {}
    for npc in definition.npcs:
        npcs_by_room.setdefault(npc.room, []).append(npc.key)

    rooms: dict[str, Room] = {}
    for spec in definition.rooms:
        rooms[ids.rooms[spec.key]] = Room(
            id=ids.rooms[spec.key],
            name=spec.name,
            description=spec.description,
            exits={direction: ids.rooms[target] for direction, target in spec.exits.items()},
            item_ids=[ids.items[key] for key in spec.items],
            npc_ids=[ids.npcs[key] for key in npcs_by_room.get(spec.key, [])],
            ambient_tags=list(spec.ambient_tags),
        )

    items: dict[str, Item] = {
        ids.items[spec.key]: Item(
            id=ids.items[spec.key],
            name=spec.name,
            description=spec.description,
            tags=list(spec.tags),
            stats=dict(spec.stats),
            rarity=spec.rarity,
            stackable=spec.stackable,
        )
        for spec in definition.items
    }

    npcs: dict[str, NPC] = {
        ids.npcs[spec.key]: NPC(
            id=ids.npcs[spec.key],
            name=spec.name,
            room_id=ids.rooms[spec.room],
            memory_ref=f"scn:{ids.scenario_id[-24:]}:{spec.key}"[:64],
        )
        for spec in definition.npcs
    }

    attributes = {name: int(value) for name, value in definition.player.attributes.items()}
    attributes["max_hp"] = definition.player.max_hp
    player = Character(
        id=PLAYER_ID,
        name=definition.player.name,
        attributes=attributes,
        inventory=[ids.items[key] for key in definition.player.inventory],
        location=ids.rooms[definition.player.location],
        hp=definition.player.hp,
        created_turn=1,
    )

    return GameState(
        turn=1,
        world_time=0,
        characters={PLAYER_ID: player},
        rooms=rooms,
        items=items,
        npcs=npcs,
        quests={},
        rng_state=RngState(save_seed=save_seed, last_seq=0),
    )


def _build_dry_run_issues(definition: ScenarioDefinition) -> list[ScenarioIssue]:
    """建局 dry-run（R-02）：把 ``build_initial_state`` 的模型约束转成导入期错误。

    只做纯函数调用，不写库、不联网；固定 scenario_id/save_seed 仅用于派生合法 ID，
    不影响校验结果。错误路径优先映射回玩家可理解的 ``player.attributes``。
    """
    try:
        ids = derive_ids(_DRY_RUN_SCENARIO_ID, definition)
        build_initial_state(definition, ids, save_seed=_DRY_RUN_SAVE_SEED)
    except ValidationError as exc:
        issues: list[ScenarioIssue] = []
        for error in exc.errors():
            loc = tuple(error.get("loc", ()))
            path = "player.attributes" if "attributes" in loc else _format_loc(loc)
            issues.append(ScenarioIssue(path, str(error.get("msg", "建局状态非法"))))
        return issues[:_MAX_ERRORS]
    except ValueError as exc:
        return [ScenarioIssue("$", f"场景无法建局：{exc}")]
    return []


# ---------------------------------------------------------------------------
# 运行时钩子（与内置场景钩子同接口）
# ---------------------------------------------------------------------------

#: 兜底结局文案（作者不写 endings 时使用；不存进定义，避免污染原始 JSON）。
_FALLBACK_FALLEN_TITLE = "倒下"
_FALLBACK_FALLEN_TEXT = "你倒下了，这一程到此为止。"
_FALLBACK_COMPLETE_TITLE = "目标完成"
_FALLBACK_COMPLETE_TEXT = "你完成了这里的所有目标。"

#: 通用对话兜底文案（NPC 未定义 dialogue 时）。
_GENERIC_REPLY = "他看了你一眼，没有多说什么。"


class UserScenarioHooks:
    """用户场景的运行时钩子：目标/结局/进入伤害/NPC 回复（全部由定义数据驱动）。

    ``applies_to(state)`` 判定状态是否属于本场景（任一派生房间 ID 出现在状态里）；
    ``UserScenarioHooks`` 与内置场景函数同语义，供 ``ScenarioRuntime`` 统一分发。
    """

    def __init__(self, definition: ScenarioDefinition, ids: ScenarioIds) -> None:
        """绑定定义与派生 ID（构造期一次性建反查表与 v2 条件上下文，运行期只读）。"""
        self._definition = definition
        self._ids = ids
        # 反查：room 派生 ID → RoomSpec；npc 派生 ID → NpcSpec（运行期只读）
        self._room_specs = {ids.rooms[room.key]: room for room in definition.rooms}
        self._npc_specs = {ids.npcs[npc.key]: npc for npc in definition.npcs}
        # v2 flag / 目标进度求值上下文（v1 定义为空表；构造期缓存避免每回合重建）
        self._condition_env = _condition_env(definition)

    @property
    def scenario_id(self) -> str:
        """本场景的内部 ID（``scn_...``，导入时生成、不可变）。"""
        return self._ids.scenario_id

    @property
    def slug(self) -> str:
        """作者可读 slug（可能重复；仅展示用）。"""
        return self._definition.slug

    @property
    def title(self) -> str:
        """场景标题（列表/对局徽章展示）。"""
        return self._definition.meta.title

    @property
    def definition(self) -> ScenarioDefinition:
        """原始定义（导出/详情用；只读）。"""
        return self._definition

    @property
    def room_ids(self) -> frozenset[str]:
        """本场景全部房间的派生 ID（运行时索引/场景识别用）。"""
        return frozenset(self._room_specs)

    @property
    def npc_ids(self) -> frozenset[str]:
        """本场景全部 NPC 的派生 ID（运行时索引/对话分发用）。"""
        return frozenset(self._npc_specs)

    def applies_to(self, state: GameState) -> bool:
        """状态是否属于本场景：任一派生房间 ID 出现在状态房间表里。"""
        return any(room_id in state.rooms for room_id in self._room_specs)

    def _match(self, condition: Condition, state: GameState) -> bool:
        """在钩子绑定的定义上下文里求值（v2 flag/目标进度可用；纯只读）。"""
        return _evaluate_condition(condition, state, self._ids, self._condition_env)

    def goal(self, state: GameState) -> dict[str, Any] | None:
        """按 ``goal.objectives[].when`` 求值出目标面板（与 story_goal 同结构）。"""
        goal = self._definition.goal
        if goal is None:
            return None
        objectives = [
            {
                "id": objective.key,
                "text": objective.text,
                "done": objective.when is not None and self._match(objective.when, state),
            }
            for objective in goal.objectives
        ]
        return {
            "title": goal.title,
            "description": goal.description,
            "objectives": objectives,
        }

    def ending(self, state: GameState) -> dict[str, Any] | None:
        """显式结局优先（priority 降序），其次兜底：目标全完成=胜利、HP≤0=倒下。"""
        player = state.characters.get(PLAYER_ID)
        if player is None:
            return None
        ordered = sorted(
            enumerate(self._definition.endings),
            key=lambda pair: (-pair[1].priority, pair[0]),
        )
        for _, ending in ordered:
            if ending.when is not None and self._match(ending.when, state):
                return {
                    "ending_id": f"ending_{self._definition.slug}_{ending.key}",
                    "title": ending.title,
                    "text": ending.text,
                    "kind": ending.kind,
                }
        goal = self._definition.goal
        if goal is not None and all(
            objective.when is not None and self._match(objective.when, state)
            for objective in goal.objectives
        ):
            return {
                "ending_id": f"ending_{self._definition.slug}_complete",
                "title": _FALLBACK_COMPLETE_TITLE,
                "text": _FALLBACK_COMPLETE_TEXT,
                "kind": "win",
            }
        if player.hp <= 0:
            return {
                "ending_id": f"ending_{self._definition.slug}_fallen",
                "title": _FALLBACK_FALLEN_TITLE,
                "text": _FALLBACK_FALLEN_TEXT,
                "kind": "lose",
            }
        return None

    def room_damage(self, room_id: str) -> int | None:
        """某房间的进入伤害（负数扣血）；无伤害/未知房间返回 ``None``（旧场景口径）。"""
        room = self._room_specs.get(room_id)
        if room is None or room.on_enter is None:
            return None
        delta = room.on_enter.hp_delta
        return delta if delta < 0 else None

    def room_damage_hint(self, room_id: str) -> str | None:
        """进入伤害的提示文案（编排层写进 ``ENVIRONMENT_DAMAGE`` 事件供叙事/提示使用）。"""
        room = self._room_specs.get(room_id)
        if room is None or room.on_enter is None:
            return None
        return room.on_enter.hint

    def npc_reply(self, npc_id: str, topic: str) -> dict[str, Any] | None:
        """按 ``dialogue.topics`` 关键词匹配出回复；未命中用 default 或通用文案。

        返回 ``{"reply": str, "dialogue_state": str | None}``（与 ``story_reply`` 同结构；
        未知 NPC 返回 ``None``，调用方走既有通用分支）。
        """
        spec = self._npc_specs.get(npc_id)
        if spec is None:
            return None
        dialogue = spec.dialogue
        if dialogue is None:
            return {"reply": _GENERIC_REPLY, "dialogue_state": None}
        for item in dialogue.topics:
            if any(keyword in topic for keyword in item.keywords):
                return {"reply": item.reply, "dialogue_state": item.dialogue_state}
        if dialogue.default:
            return {"reply": dialogue.default, "dialogue_state": None}
        return {"reply": _GENERIC_REPLY, "dialogue_state": None}
