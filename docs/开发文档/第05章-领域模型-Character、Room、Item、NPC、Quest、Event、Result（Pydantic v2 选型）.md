## 第 5 章 领域模型：Character、Room、Item、NPC、Quest、Event、Result（Pydantic v2 选型）

### 本章检索关键词
- `pydantic v2 domain model validation`、`pydantic 2.14 requires-python 3.10`、`pydantic-core archived moved into monorepo`、`sqlalchemy 2.1 requires-python 3.11`、`SQLModel thin layer pydantic sqlalchemy 0.0.x`、`msgspec fast serialization validation`、`python eventsourcing aggregate @event`、`pypi pydantic sqlmodel msgspec eventsourcing requires-python`

### 实际打开确认的仓库列表
- `pydantic/pydantic`（https://github.com/pydantic/pydantic）— 打开确认：许可证 MIT、最近提交 2026-11（打开时 pushed 为 2026-11-07/08 两次观测）、star 约 29.4k、Python 3.10 兼容（PyPI 2.14.3 `requires-python=>=3.10`；仓库 main 分支的 `pyproject.toml` 开发版已抬到 ≥3.11，升级大版本前必须重核）、确认要点（README：V2 是 ground-up rewrite 且有 breaking change；`pydantic-core` 作为编译核心被固定版本依赖）。
- `pydantic/pydantic-core`（https://github.com/pydantic/pydantic-core）— 打开确认：许可证 MIT、最近提交 2026-01、**仓库已归档**（README 首段明示“已迁入 `pydantic/pydantic` 主仓”）、star 约 1.8k、Python 3.10 兼容（PyPI `pydantic-core` 2.50.4 `requires-python=>=3.10`）、确认要点（`pydantic` 2.14.3 的依赖锁定为 `pydantic-core==2.50.4`；本仓库不单独声明依赖）。
- `sqlalchemy/sqlalchemy`（https://github.com/sqlalchemy/sqlalchemy）— 打开确认：许可证 MIT、最近提交 2026-11、star 约 12.3k、Python 3.10 兼容需锁系列（PyPI 2.1.8 `requires-python=>=3.11`，2.0.57 `requires-python=>=3.7`）、确认要点（README 的 ORM / Unit of Work / Data Mapper 定位；两代发布系列的 requires-python 差异决定了 3.10 下限）。
- `fastapi/sqlmodel`（https://github.com/fastapi/sqlmodel）— 打开确认：许可证 MIT、最近提交 2026-11、star 约 18.6k、Python 3.10 兼容（PyPI 0.0.48 `requires-python=>=3.10`）、确认要点（README 自述是 Pydantic + SQLAlchemy 的“thin layer”；版本号仍为 0.0.x）。
- `msgspec/msgspec`（https://github.com/msgspec/msgspec）— 打开确认：许可证 BSD-3-Clause、最近提交 2026-11、star 约 4.2k、Python 3.10 兼容（PyPI 0.22.0 `requires-python=>=3.10`）、确认要点（README：JSON/MessagePack/YAML/TOML 高性能编解码 + 基于类型注解的零成本 Schema 校验）。
- `pyeventsourcing/eventsourcing`（https://github.com/pyeventsourcing/eventsourcing）— 打开确认：许可证 BSD-3-Clause、最近提交 2026-10、star 约 1.7k、Python 3.10 兼容需锁系列（PyPI 9.5.6 `requires-python=>=3.11`，9.4.6 `requires-python=>=3.9.2`）、确认要点（README：`Aggregate` + `@event` 装饰器的 Python 事件溯源实现）。

### 5.1 选型结论（先给答案）

1. **领域模型（Domain Model）只用 Pydantic v2 `BaseModel` 实现，锁定为 `pydantic>=2.14,<3`**。理由：一份类型注解同时得到运行时校验、`model_json_schema()`（工具层 JSON Schema 直接复用）、`model_dump(mode="json")`（事件与 API 序列化）、`model_validate_json()`（回放，Replay）四件事；换成 dataclass + 手写校验等于把四份代码维护一遍。
2. **SQLAlchemy 只允许在 `persistence/` 被 import，锁 `SQLAlchemy>=2.0.57,<2.1`**；`2.1.8` 已要求 Python ≥3.11，与 3.10 下限冲突（第 4 章已记录该约束，本章再次确认）。
3. **不采用 SQLModel 作为领域模型**：它把 ORM 表模式与校验模式合成一个类，等于让 `domain/` 依赖 SQLAlchemy，破坏“domain 零外部依赖”的依赖方向；且版本仍为 0.0.x（README 自述为薄层），不宜作为基线。
4. **不可变边界**：`Event`、`Result`、`Proposal`、`Resolution`、`ToolSpec`、`PerceptionSlice` 用 `frozen=True`；世界实体（`Character`/`Room`/`Item`/`NPC`/`Quest`）同样 `frozen=True`，所有变更经 `GameState.apply(events)` 做纯投影（实现见第 14 章），任何服务方法不得原地改状态。
5. **模型版本号不塞进 `Event` 基线字段**：事件 payload 的版本登记在 `EventLog` 侧的 upcaster 注册表，快照（Snapshot）版本随快照包装记录（方案见 5.9；第 14 章已采纳方案 B）。
6. **v0.2 新增 `CharacterCard` 人物卡 DTO（5.7）**：前端上传 / LLM 生成 / 手填共用同一份 JSON 契约（`extra="forbid"`），由 `build_character_card_schema()` 产出与前端共享的 JSON Schema；服务端在开局时把它映射为 `Character`（id 服务端生成、location 由开局场景决定、inventory 由 `starting_items` 实例化），人物卡本身不进入 `GameState` 与事件链。

### 5.2 基础类型、Event 与 Result

```python
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_ULID = r"[0-9A-HJKMNP-TV-Z]{26}"
CharacterId = Annotated[str, Field(pattern=rf"^char_{_ULID}$")]
RoomId = Annotated[str, Field(pattern=rf"^room_{_ULID}$")]
ItemId = Annotated[str, Field(pattern=rf"^item_{_ULID}$")]
NpcId = Annotated[str, Field(pattern=rf"^npc_{_ULID}$")]
QuestId = Annotated[str, Field(pattern=rf"^quest_{_ULID}$")]
EventId = Annotated[str, Field(pattern=r"^evt_[0-9]{6,}$")]
TurnId = Annotated[int, Field(ge=1)]
Direction = Literal["north", "south", "east", "west", "up", "down", "in", "out"]
Verdict = Literal["applied", "rejected", "conflict", "deferred"]


class ErrorCode(str, Enum):
    INVALID_INPUT = "INVALID_INPUT"
    NOT_FOUND = "NOT_FOUND"
    NOT_ALLOWED = "NOT_ALLOWED"
    OUT_OF_RANGE = "OUT_OF_RANGE"
    CONFLICT = "CONFLICT"
    SCHEMA_VIOLATION = "SCHEMA_VIOLATION"
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
    INTERNAL = "INTERNAL"


class Event(BaseModel):
    """一次写操作的不可变记录。草稿态 → GameEngine/EventLog 盖章后为完成态。"""
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: EventId | None = None                                  # 盖章时生成：f"evt_{seq:06d}"
    turn_id: TurnId
    seq: int | None = Field(default=None, ge=1)                # 存档内全局单调递增，跨回合连续，从 1 起
    actor_id: str = Field(min_length=1, max_length=64)
    method: Annotated[str, Field(pattern=r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$", max_length=48)]
    params: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    rng_seed: int | None = Field(default=None, ge=0, le=0x7FFF_FFFF_FFFF_FFFF)  # 有符号 64 位，与第 10 章掩码一致
    ts: datetime | None = None

    @model_validator(mode="after")
    def _stamped_all_or_none(self) -> "Event":
        flags = (self.id is not None, self.seq is not None,
                 self.rng_seed is not None, self.ts is not None)
        if any(flags) and not all(flags):
            raise ValueError("event must be fully stamped or fully draft")
        return self

    @field_validator("ts")
    @classmethod
    def _ts_must_be_utc(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return None
        if v.tzinfo is None:
            raise ValueError("ts must be timezone-aware")
        return v.astimezone(timezone.utc)

    @property
    def is_stamped(self) -> bool:
        return self.id is not None


class Result(BaseModel):
    """所有写方法的统一返回；失败必须带 error_code 且不得携带事件。"""
    model_config = ConfigDict(frozen=True, extra="forbid")

    ok: bool
    data: dict[str, Any] | None = None
    error_code: ErrorCode | None = None
    message: str | None = None
    events: list[Event] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> "Result":
        if self.ok and self.error_code is not None:
            raise ValueError("ok=True cannot carry error_code")
        if not self.ok:
            if self.error_code is None:
                raise ValueError("ok=False requires error_code")
            if self.events:
                raise ValueError("failed Result must not carry events (atomicity)")
        return self

    @classmethod
    def success(cls, data: dict[str, Any] | None = None,
                events: list[Event] | None = None) -> "Result":
        return cls(ok=True, data=data, events=events or [])

    @classmethod
    def fail(cls, error_code: ErrorCode, message: str | None = None) -> "Result":
        return cls(ok=False, error_code=error_code, message=message)
```

ID 统一用“前缀 + ULID”字符串（`char_...`/`room_...`/`evt_000041`），不暴露自增主键：事件日志可读、跨库合并无冲突、Agent Prompt 里也不会出现可猜的数字 ID。数据库里再叠加自己的 BIGINT 主键，领域模型不感知。

### 5.3 Event：草稿态与盖章态

`Event` 同时服务两个阶段：服务层产出**草稿态**（无 `id`/`seq`/`ts`/`rng_seed`），`GameEngine` 提交前由 `EventLog.stamp()` 补全为**盖章态**。用“全有或全无”校验避免出现半盖章事件；`Result.events` 类型因此无需分裂成两种模型。一次攻击产生的两个事件之一形如：

```json
{
  "id": "evt_000412", "turn_id": 42, "seq": 412, "actor_id": "player_1",
  "method": "attack_target",
  "params": {"target_id": "npc_goblin_01", "weapon_id": "item_rusty_sword", "proposal_id": "p_000042_001"},
  "result": {"hit": true, "damage": 5, "target_hp_after": 7, "dice": [{"sides": 20, "rolls": [14], "kept": [14]}]},
  "rng_seed": 7917514595543340859,
  "ts": "2026-11-08T12:00:03Z"
}
```

`seq` 是**存档内全局单调递增**的序号（从 1 起、跨回合连续），`id = f"evt_{seq:06d}"`；同一回合内的事件按 `seq` 升序排列，`turn_id` 只表示所属回合，与 `seq` 不要求相等（一回合可产生多个事件）。

注意 `result` 里同时保留 `dice` 与 `rng_seed`：回放时不需要重跑战斗，只需校验重算出的骰面与记录一致（第 10 章的验收标准）。**`rng_seed` 必须是有符号 64 位整数**（`0 <= rng_seed <= 0x7FFF_FFFF_FFFF_FFFF`，即 `2**63-1`）：第 10 章的 `derive_seed()` 用 blake2b 取 8 字节转整数时会落在 `[0, 2**64-1]`，落库前必须掩码 `& 0x7FFF_FFFF_FFFF_FFFF`（与第 10 章 10.3 的写法一致），否则超过 `BIGINT` 上界（Postgres/SQLite 均如此）会写库失败——这是第 10 章实现与第 14 章事件表之间的硬约束。


字段与基线的对应：`turn_id`、`seq`、`actor_id`、`method`（写操作的工具名，如 `attack_target`）、`params`（工具参数 + `proposal_id`）、`result`（判定摘要与数值结果）、`rng_seed`（第 10 章 `RandomSource.derive_seed()` 派生的 64 位种子）、`ts`。回放契约：同一存档种子重放时，`params`/`result`/`rng_seed` 的哈希必须逐字节一致。

### 5.4 世界实体

```python
class Status(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: Annotated[str, Field(pattern=r"^status_[a-z0-9_]{2,32}$")]
    name: str = Field(min_length=1, max_length=24)
    duration_turns: int = Field(ge=-1, description="-1 表示永续，0 表示本回合结束即移除")
    stacks: int = Field(default=1, ge=1, le=99)


class Character(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: CharacterId
    name: str = Field(min_length=1, max_length=24)
    attributes: dict[str, int] = Field(min_length=1)
    inventory: list[ItemId] = Field(default_factory=list, max_length=64)
    location: RoomId
    hp: int = Field(ge=0)
    status: list[Status] = Field(default_factory=list, max_length=16)
    private_memory_ref: str | None = Field(default=None, max_length=64)
    created_turn: TurnId

    @field_validator("attributes")
    @classmethod
    def _known_attribute_keys(cls, v: dict[str, int]) -> dict[str, int]:
        allowed = {"str", "dex", "con", "int", "wis", "cha", "max_hp"}
        unknown = set(v) - allowed
        if unknown:
            raise ValueError(f"unknown attribute keys: {sorted(unknown)}")
        if any(not (-10 <= n <= 100) for n in v.values()):
            raise ValueError("attribute values must be within [-10, 100]")
        return v

    @model_validator(mode="after")
    def _hp_within_max(self) -> "Character":
        # max_hp 挂在 attributes 里，不新增基线字段
        max_hp = self.attributes.get("max_hp")
        if max_hp is not None and self.hp > max_hp:
            raise ValueError(f"hp {self.hp} exceeds max_hp {max_hp}")
        return self


class Room(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: RoomId
    name: str = Field(min_length=1, max_length=32)
    description: str = Field(min_length=1, max_length=500)
    exits: dict[Direction, RoomId] = Field(default_factory=dict)
    item_ids: list[ItemId] = Field(default_factory=list)
    npc_ids: list[NpcId] = Field(default_factory=list)
    ambient_tags: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def _exits_sane(self) -> "Room":
        if self.id in self.exits.values():
            raise ValueError("room cannot exit to itself")
        return self


class Item(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: ItemId
    name: str = Field(min_length=1, max_length=32)
    description: str = Field(max_length=500)
    tags: list[str] = Field(default_factory=list, max_length=8)
    stats: dict[str, int | float] = Field(default_factory=dict)
    rarity: Literal["common", "uncommon", "rare", "epic", "legendary"] = "common"
    stackable: bool = False


class ScheduleSlot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    from_world_minute: int = Field(ge=0, le=1439)
    to_world_minute: int = Field(ge=0, le=1439)
    room_id: RoomId

    @model_validator(mode="after")
    def _window(self) -> "ScheduleSlot":
        if self.from_world_minute == self.to_world_minute:
            raise ValueError("empty schedule window")
        return self


class NPC(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: NpcId
    name: str = Field(min_length=1, max_length=24)
    room_id: RoomId
    dialogue_state: str = Field(default="idle", max_length=32)
    faction: str | None = Field(default=None, max_length=32)
    memory_ref: str = Field(min_length=1, max_length=64)
    schedule: list[ScheduleSlot] = Field(default_factory=list, max_length=32)


class Stage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: Annotated[str, Field(pattern=r"^stage_[0-9]{1,3}$")]
    description: str = Field(max_length=200)
    objective: dict[str, Any] = Field(default_factory=dict)
    completed: bool = False


class Reward(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    item_id: ItemId
    qty: int = Field(ge=1, le=999)


class QuestState(str, Enum):
    NOT_STARTED = "not_started"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"


class Quest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: QuestId
    title: str = Field(min_length=1, max_length=48)
    stages: list[Stage] = Field(min_length=1, max_length=20)
    rewards: list[Reward] = Field(default_factory=list)
    state: QuestState = QuestState.NOT_STARTED

    @model_validator(mode="after")
    def _stages_contiguous(self) -> "Quest":
        # completed 必须是从头开始的前缀；Skipped 中间阶段的 bug 在模型层即失败
        flags = [s.completed for s in self.stages]
        if flags != sorted(flags, reverse=True):
            raise ValueError("completed stages must form a prefix")
        return self
```

### 5.5 GameState 与其余模型

本节定义唯一写入者（Single Writer）持有的世界状态 `GameState`，以及事件之源上的四个协作对象：提案（Proposal）、裁决（Resolution）、动作意图（Action Intent）、感知切片（Perception Slice）与工具规格（Tool Spec）。

```python
class RngState(BaseModel):
    """第 10 章方案 A：派生式无状态随机，只存存档种子与最后序号。"""
    model_config = ConfigDict(frozen=True, extra="forbid")
    mode: Literal["derived", "state"] = "derived"
    save_seed: str = Field(min_length=1, max_length=64)
    last_seq: int = Field(default=0, ge=0)


class GameState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    turn: TurnId
    world_time: int = Field(ge=0, description="游戏内分钟计数，0 = 第 1 天 00:00")
    characters: dict[CharacterId, Character] = Field(default_factory=dict)
    rooms: dict[RoomId, Room] = Field(default_factory=dict)
    items: dict[ItemId, Item] = Field(default_factory=dict)
    npcs: dict[NpcId, NPC] = Field(default_factory=dict)
    quests: dict[QuestId, Quest] = Field(default_factory=dict)
    rng_state: RngState

    @model_validator(mode="after")
    def _referential_integrity(self) -> "GameState":
        for cid, ch in self.characters.items():
            if cid != ch.id:
                raise ValueError(f"character key mismatch: {cid}")
            if ch.location not in self.rooms:
                raise ValueError(f"character {cid} in unknown room {ch.location}")
            missing = [i for i in ch.inventory if i not in self.items]
            if missing:
                raise ValueError(f"character {cid} holds unknown items {missing}")
        for rid, room in self.rooms.items():
            if rid != room.id:
                raise ValueError(f"room key mismatch: {rid}")
            unknown_exits = [d for d in room.exits.values() if d not in self.rooms]
            if unknown_exits:
                raise ValueError(f"room {rid} has unknown exits {unknown_exits}")
        for nid, npc in self.npcs.items():
            if nid != npc.id or npc.room_id not in self.rooms:
                raise ValueError(f"npc {nid} has invalid id or room")
        return self

    def apply(self, events: list[Event]) -> "GameState":
        """纯投影：返回新状态，不写库、不改 self。实现见第 14 章。"""
        raise NotImplementedError  # 由 persistence/replay 模块提供投影


class Proposal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    turn_id: TurnId
    actor_id: str = Field(min_length=1, max_length=64)
    tool: Annotated[str, Field(pattern=r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$", max_length=48)]
    args: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=1, max_length=500)
    priority: int = Field(default=50, ge=0, le=100)


class Resolution(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    proposal_id: str = Field(min_length=1, max_length=64)
    verdict: Verdict
    reason: str = Field(default="", max_length=1000)
    applied_events: list[Event] = Field(default_factory=list)

    @model_validator(mode="after")
    def _applied_requires_drafts(self) -> "Resolution":
        if self.verdict == "applied" and not self.applied_events:
            raise ValueError("applied resolution must carry at least one draft event")
        if self.verdict != "applied" and self.applied_events:
            raise ValueError("non-applied resolution must not carry events")
        if any(e.is_stamped for e in self.applied_events):
            raise ValueError("resolution events must be drafts; stamping is GameEngine's job")
        return self


class ActionIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Annotated[str, Field(min_length=1, max_length=32, pattern=r"^[a-z][a-z0-9_]*$")]
    target: str | None = Field(default=None, max_length=64)
    params: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0)


class EntityRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: Literal["character", "room", "item", "npc", "quest"]
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(max_length=48)


class PerceptionSlice(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    actor_id: str = Field(min_length=1, max_length=64)
    visible_entities: list[EntityRef] = Field(default_factory=list)
    local_events: list[Event] = Field(default_factory=list)
    private_memory: list[str] = Field(default_factory=list)
    plan_hint: str | None = Field(default=None, max_length=200)


class SideEffect(str, Enum):
    READ_ONLY = "READ_ONLY"
    DESTRUCTIVE = "DESTRUCTIVE"
    NETWORK = "NETWORK"


class ResolutionPolicy(str, Enum):
    DETERMINISTIC = "deterministic"
    AGENT_ASSISTED = "agent_assisted"
    MANUAL = "manual"


class ToolSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$", max_length=48)]
    description: str = Field(min_length=1, max_length=200)
    json_schema: dict[str, Any]
    actor_scope: frozenset[str] = Field(default_factory=frozenset)
    side_effect: SideEffect = SideEffect.READ_ONLY
    resolution_policy: ResolutionPolicy = ResolutionPolicy.DETERMINISTIC

    @model_validator(mode="after")
    def _policy_invariants(self) -> "ToolSpec":
        if self.side_effect is SideEffect.NETWORK:
            raise ValueError("network tools are not allowed in the core tool layer")
        if self.json_schema.get("type") != "object":
            raise ValueError("tool json_schema root must be an object")
        if not self.description.strip():
            raise ValueError("description is required (used as tool prompt)")
        return self
```

`ToolSpec` 严格保持第 5 章 5.5 定义的六字段（第 21、22 章的进度文档模板也按六字段对照），不额外加 `schema_hash` 等派生列；需要哈希时在注册表里现算。

### 5.6 跨章类型归属与校验责任

除本章定义的领域模型外，第 8 章（智能体输出契约）与第 10 章（判定值对象）还会产出若干类型。它们**不属于** `domain/models.py`，但会被服务层、工具层引用，本节统一交代归属与校验责任，避免全队出现“未定义类型”。总原则：**Agent 输出契约只在 `agents/` 与 `llm/` 内流动，绝不进 `domain/`；判定值对象属于 `domain/rules/`；服务层只看见 `Result`、草稿 `Event` 和纯数据 `dict`。**

| 类型 | 定义章节 | 字段摘要 | 归属包 | 是否进入 `GameState`/`Event` | 校验责任与失败错误码 |
| --- | --- | --- | --- | --- | --- |
| `ParseOutput` | 第 8 章 §8.2 | `intent: ActionIntent`、`tool: ToolCall \| None`、`candidate_narrative: str \| None`（≤200 字）、`clarify_question: str \| None`（≤80 字） | `agents/schemas.py`（回合内 DTO） | 不进入；只有 `intent` 经第 7 章转成 `Proposal` 后进队列 | 第 12 章 LLMClient Schema 校验 + 第 8 章语义校验；原始文本不可解析 → `SCHEMA_VIOLATION`；工具参数不符 → `INVALID_INPUT`；越权 → `NOT_ALLOWED` |
| `JudgeOutput` | 第 8 章 §8.2 | `verdict`、`reason`、`evidence_refs`、`confidence`；不产出数值 | `agents/schemas.py` | 不进入；只进 `Resolution.reason` 与审计 | Schema + 第 10 章置信度阈值（<0.7 强制保守裁决）；失败 → `SCHEMA_VIOLATION`，降级为确定性兜底 |
| `NarrativeOutput` | 第 8 章 §8.2 | `narrative`（80～150 字）、`tone` | `agents/schemas.py` | 不进入 `GameState`；文本进 `TurnResult`，来源记 `narrative_source ∈ {llm, template}` | 第 8 章字数/字符校验 + Schema；失败 → 模板兜底，不改状态 |
| `MemoryOutput` | 第 8 章 §8.2 | `ops: list[MemoryOp]`、`summary` | `agents/schemas.py`；`MemoryOp` 由 `MemoryStore` 应用（接口见第 8 章记忆 Agent） | 不进入 `GameState`；写入按 `actor_id` 隔离的私有记忆库 | Schema + 命名空间归属校验；失败 → `SCHEMA_VIOLATION`，丢弃本次记忆（不阻塞回合） |
| `CharacterOutput` | 第 8 章 §8.2 | `proposals: list[Proposal]`（≤2）、`dialogue`、`memory_ops` | `agents/schemas.py` | 不直接进入；提案进 `ProposalQueue`（第 11 章） | Schema + 第 7 章工具门 + 第 11 章 Resolver；失败 → `SCHEMA_VIOLATION`/`NOT_ALLOWED`，本回合不行动 |
| `CraftOutput` | 第 8 章 §8.2 | `item_name`、`slots`、`materials`、`steps`；**无任何数值字段** | `agents/schemas.py`，第 13 章规范化为 `CraftProposal` | 不进入；产物经 `CraftService` 校验后转 `Proposal` | Schema + 第 13 章状态一致性检查；失败 → `SCHEMA_VIOLATION`/`NOT_ALLOWED`，退回确定性路径 |
| `ToolCall` | 第 8 章（`ParseOutput.tool` 引用） | `name: str`、`args: dict`（字段以第 8 章为准） | 契约类型，不持久化 | 不进入；由第 7 章 `build_proposal` 消费 | 第 7 章参数 Schema；失败 → `INVALID_INPUT`/`NOT_ALLOWED` |
| `CheckResult`/`DiceRoll`/`Modifier` | 本章 5.6 定稿（第 10 章 §10.2 使用） | 见下方代码块 | `domain/rules/types.py`（判定值对象，纯确定性） | 整对象不落库；`dice` 与 `rng_seed` 以摘要形式进 `Event.result` | 第 10 章 `RuleEngine` 计算 + Pydantic 校验，禁止 LLM 生成或修改；构造失败 → `INTERNAL`（代码 bug）；参数越界 → `INVALID_INPUT`/`OUT_OF_RANGE` |
| `RandomSource` | 本章 5.6 定稿接口（第 10 章实现） | `derive_seed(save_seed, turn_id, actor_id, method, seq) -> int`、`mask_seed(raw) -> int` | 接口 `domain/rules/types.py`；实现 `domain/rules/dice.py`（`DerivedRandom`） | 不进入；`save_seed`/`last_seq` 进 `GameState.rng_state` | 实现必须掩码到 `0..2^63-1`，否则 → `INTERNAL`；第 10/14 章按类方法调用 |

其余跨章类型只引用、不重定义：`JudgeOutput`/`NarrativeOutput`/`MemoryOutput`/`MemoryOp`/`MemoryStore` 见第 8 章（智能体设计）；`CraftRequest`/`CraftMode`/`CraftDraft`/`CraftProposal`/`CraftStateSlice`/`CraftResult`/`QualityBand`/`Byproduct` 见第 13 章（制造系统）；`TurnInput`/`TurnResult` 见第 9 章（单回合调用链）。第 6/7 章引用这些类型时不再重复定义字段。

第 10 章的判定值对象在第 5 章定稿如下（`domain/rules/types.py`；`rng_seed` 约束与本章 `Event` 一致）：

```python
class DiceRoll(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    sides: int = Field(ge=2, le=100)
    rolls: list[int] = Field(min_length=1, max_length=16)
    kept: list[int] = Field(min_length=1, max_length=16)


class Modifier(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    source: str = Field(min_length=1, max_length=48)
    value: int = Field(ge=-100, le=100)


class CheckResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    rule_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.]{1,63}$")]
    actor_id: str = Field(min_length=1, max_length=64)
    target: str | None = Field(default=None, max_length=64)
    dc: int = Field(ge=1, le=100)
    dice: list[DiceRoll] = Field(min_length=1, max_length=4)
    modifiers: list[Modifier] = Field(default_factory=list, max_length=8)
    total: int = Field(ge=-100, le=400)
    outcome: Literal["critical_success", "success", "failure", "critical_failure"]
    rng_seed: int = Field(ge=0, le=0x7FFF_FFFF_FFFF_FFFF)
```

**随机源接口（RandomSource）**。第 10 章的派生式随机与第 14 章的回放都按这两个类方法调用，接口在本章定稿：

```python
from typing import Protocol


class RandomSource(Protocol):
    """派生式随机（接口定稿；实现与完整算法见第 10 章 10.3 的 DerivedRandom）。"""

    @staticmethod
    def mask_seed(raw: int) -> int:
        """raw & 0x7FFF_FFFF_FFFF_FFFF；把任意整数收敛到有符号 64 位范围。"""
        ...

    @classmethod
    def derive_seed(cls, save_seed: str, turn_id: int, actor_id: str,
                    method: str, seq: int) -> int:
        """同一 (save_seed, turn_id, actor_id, method, seq) 必得同一已掩码子种子
        （blake2b → int → cls.mask_seed），返回 0..0x7FFF_FFFF_FFFF_FFFF。"""
        ...
```

`GameState.rng_state` 只保存 `{"mode": "derived", "save_seed": str, "last_seq": int}`（第 10 章方案 A、第 14 章快照格式），不保存 Python 的 `random.getstate()`；`EventLog.stamp()`、`RuleEngine/RuleService`、`CraftService` 内所有随机都必须经这两个类方法，禁止直接调用 `random`。第 10 章 10.3 的 `DerivedRandom` 实现该协议并另提供实例掷骰接口 `randint`（示例已实现）；`choice` 由第 10 章协议声明、实现按需补齐（不属于 seed 契约，见第 10 章 10.3）。

依赖方向落地：`domain/` 不 import `agents/`、不 import `llm/`；`agents/` 可以 import `domain/`（值对象与 `ActionIntent`/`Proposal`）与 `llm/`；`services/` 只 import `domain/` 与仓储接口，永远看不到 `ParseOutput` 这类 LLM 契约。跨层传递一律走 `Result.data` 里的纯 `dict`（第 6 章），不传 Pydantic 契约对象。

### 5.7 CharacterCard DTO：人物卡导入/生成的前端 JSON 契约（v0.2）

`CharacterCard` 是 v0.2 前端（Vue 3 SPA，第 23 章）与后端共享的**人物卡 JSON 契约**（character card DTO），不是领域实体：它只在 API 边界存在，先由服务端校验，再在开局时映射为本章 `Character` 并写入新存档。字段在本节定稿，与第 23 章前端契约完全一致；第 6 章 6.7 的接口、第 21 章示例、第 23 章前端 Ajv 校验都引用本节，**不得增删字段**。

```python
from typing import Any, Callable

# v0.2 新增的 API 层 ID：由服务端生成，不接受前端指定
CardId = Annotated[str, Field(pattern=rf"^card_{_ULID}$")]
GameId = Annotated[str, Field(pattern=rf"^game_{_ULID}$")]
ItemTemplateId = Annotated[str, Field(pattern=r"^item[._][a-z][a-z0-9_.]{1,63}$")]  # 模板 ID（两种写法）

AttributeKey = Literal["str", "dex", "con", "int", "wis", "cha", "max_hp"]
CardSource = Literal["upload", "llm", "manual"]
# max_hp 是生命上限，必须 ≥1（否则 hp>=0 与 hp<=max_hp 无解、卡片永不可能合法）；其余六键 -10..100
_ATTRIBUTE_BOUNDS: dict[str, tuple[int, int]] = {
    "str": (-10, 100), "dex": (-10, 100), "con": (-10, 100), "int": (-10, 100),
    "wis": (-10, 100), "cha": (-10, 100), "max_hp": (1, 100),
}
_ATTRIBUTE_KEYS = frozenset(_ATTRIBUTE_BOUNDS)


class CharacterCard(BaseModel):
    """人物卡 DTO（v0.2）：上传 / LLM 生成 / 手填共用同一份 JSON 契约；不进入 GameState。"""
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"]
    name: str = Field(min_length=1, max_length=24)
    attributes: dict[AttributeKey, int]
    hp: int = Field(ge=0)
    starting_items: list[ItemTemplateId] = Field(max_length=8)
    background: str = Field(max_length=2000)
    appearance: str | None = Field(default=None, max_length=500)
    source: CardSource
    generated_by: str | None = Field(default=None, max_length=120)

    @field_validator("name", "background")
    @classmethod
    def _trim(cls, v: str) -> str:
        return v.strip()

    @field_validator("attributes")
    @classmethod
    def _exact_keys_and_range(cls, v: dict[AttributeKey, int]) -> dict[AttributeKey, int]:
        if set(v) != _ATTRIBUTE_KEYS:
            missing = sorted(_ATTRIBUTE_KEYS - set(v))
            extra = sorted(set(v) - _ATTRIBUTE_KEYS)
            raise ValueError(f"attributes keys must be exactly 7 known keys; missing={missing} extra={extra}")
        for key, value in v.items():
            low, high = _ATTRIBUTE_BOUNDS[key]
            if not (low <= value <= high):
                raise ValueError(f"attribute {key} must be within [{low}, {high}]")
        return v

    @model_validator(mode="after")
    def _hp_within_max(self) -> "CharacterCard":
        if self.hp > self.attributes["max_hp"]:
            raise ValueError("hp must be <= attributes.max_hp")
        return self


def build_character_card_schema() -> dict[str, Any]:
    """生成与前端共享的 JSON Schema（draft 2020-12；前端 Ajv2020 直接复用同一份）。"""
    schema = CharacterCard.model_json_schema(mode="validation", ref_template="#/$defs/{model}")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://textgame.local/schemas/character-card-1.0.json"
    # attributes 是 dict[Literal, int]，Pydantic 只给 propertyNames + additionalProperties；
    # 这里显式展开为逐键 properties，让前端 Ajv 也能校验每个键的取值范围（max_hp minimum:1）
    attrs = schema["properties"]["attributes"]
    attrs.pop("additionalProperties", None)
    attrs.pop("propertyNames", None)
    attrs["properties"] = {
        key: {"type": "integer", "minimum": low, "maximum": high}
        for key, (low, high) in _ATTRIBUTE_BOUNDS.items()
    }
    attrs["required"] = sorted(_ATTRIBUTE_KEYS)
    attrs["additionalProperties"] = False
    return schema


CHARACTER_CARD_SCHEMA: dict[str, Any] = build_character_card_schema()
```

`build_character_card_schema()` 是唯一的 Schema 生成入口：`GET /api/v1/character-cards/schema` 直接返回 `CHARACTER_CARD_SCHEMA`，`web/src/schemas/character-card.schema.json` 与之做契约测试（第 18 章）。生成物中 `additionalProperties: false`、`starting_items.items.pattern`，以及显式展开的 `attributes` 逐键 `properties`（含 `max_hp` 的 `minimum: 1`、其余键 `-10..100`）与 `required`，让**前端 Ajv 预校验与服务端 Pydantic 校验等价**；前端校验只改善体验，服务端为最终权威。

校验规则与错误码（错误码定义见 5.2，API 入口见第 6 章 6.7）：

| 字段 | 规则 | 失败码 |
| --- | --- | --- |
| `schema_version` | 必须为 `"1.0"` | `SCHEMA_VIOLATION` |
| `name` | trim 后 1–24 字符 | `SCHEMA_VIOLATION` |
| `attributes` | 恰好 7 键（`str/dex/con/int/wis/cha/max_hp`）；前六键 -10..100，**`max_hp` 必须 1..100**（否则 `hp>=0` 与 `hp<=max_hp` 无解） | `SCHEMA_VIOLATION` |
| `hp` | `0 <= hp <= attributes.max_hp`（`max_hp>=1` 保证该区间至少含 `hp=0`） | `SCHEMA_VIOLATION` |
| `starting_items` | ≤8 项，元素为物品模板 ID（`item.<name>` 或 `item_<name>`，如 `item.rusty_sword`/`item_torch`；`item_<26位ULID>` 是实例 ID，不接受），可为空数组；重复模板合并并记 warning | `SCHEMA_VIOLATION`；模板不存在 → `NOT_FOUND` |
| `background` | ≤2000 字符（可为空字符串） | `SCHEMA_VIOLATION` |
| `appearance` | `null` 或 ≤500 字符 | `SCHEMA_VIOLATION` |
| `source` | `upload` / `llm` / `manual` | `SCHEMA_VIOLATION` |
| `generated_by` | `null` 或 ≤120 字符 | `SCHEMA_VIOLATION` |
| 多余字段 | `extra="forbid"` 直接拒绝 | `SCHEMA_VIOLATION` |
| JSON 语法 / 文件大小 | 非 JSON、或上传文件 >64KB | `INVALID_INPUT` |
| 内容安全 | HTML/URL/base64/提示注入等 | `NOT_ALLOWED`（第 15 章 GuardPipeline） |

**`CharacterCard → Character` 映射规则**（服务端在开局事务内执行，前端不参与）：

```python
def to_character(card: CharacterCard, *, location: RoomId, created_turn: int,
                 new_ulid: Callable[[], str],
                 instantiate_item: Callable[[ItemTemplateId, ItemId], Item]) -> Character:
    """把校验通过的 CharacterCard 映射为开局 Character；Item 实例同时写入 GameState.items。"""
    instances = [instantiate_item(tid, f"item_{new_ulid()}") for tid in card.starting_items]
    return Character(
        id=f"char_{new_ulid()}",
        name=card.name,
        attributes=dict(card.attributes),
        inventory=[item.id for item in instances],
        location=location,
        hp=card.hp,
        status=[],
        private_memory_ref=None,
        created_turn=created_turn,
    )
```

| `CharacterCard` 字段 | 目标 | 规则 |
| --- | --- | --- |
| `name` / `attributes`（含 `max_hp`）/ `hp` | `Character.name` / `attributes` / `hp` | 原样映射；`max_hp` 已校验 1..100、`hp ≤ max_hp` 已在模型层校验（`to_character` 不重复计算） |
| `starting_items` | `Character.inventory` + `GameState.items` | 按物品模板实例化为 `Item`（`id = item_<ULID>`），最多 8 件；重复模板合并数量 |
| （DTO 无 id） | `Character.id` | 服务端生成 `char_<ULID>`，前端不得指定 |
| （DTO 无 location） | `Character.location` | 由开局场景决定（第 6 章 6.7 的 `POST /games`，默认新手房间），不接受前端传坐标 |
| `background` / `appearance` | 不入 `Character` | 存人物卡记录（展示用），可选写入该角色初始私有记忆（第 8 章） |
| `source` / `generated_by` | 审计字段 | 存人物卡记录，仅展示与审计，不参与判定 |
| `schema_version` | 仅校验 | 不进入任何领域模型 |

边界：① `CharacterCard` **不参与事件溯源**——它不产生 `Event`、不进入 `GameState`，只有开局后的 `Character`/`GameState`/`Event` 进入事件链；② 该 DTO **不是 `ToolSpec`**、不注册进工具层（第 7 章 7.3 边界）；③ 模型层只做长度/类型/键约束，HTML/URL/base64 等内容安全由第 15 章 GuardPipeline 在 `CharacterCardService` 内执行。

### 5.8 校验规则清单与校验边界

| 校验类别 | 实例 | 失败处理 |
| --- | --- | --- |
| 结构/类型 | 字段类型、枚举、ID 正则、`extra="forbid"` | 来自 LLM 原始输出 → `SCHEMA_VIOLATION`；来自 API 请求体/工具参数 → `INVALID_INPUT`；来自内部构造 → 视为 bug，`INTERNAL` |
| 取值范围 | `hp>=0`、`0<=confidence<=1`、`priority∈[0,100]`、`stacks∈[1,99]` | 同上；服务层业务范围（如伤害上限）另行 `OUT_OF_RANGE` |
| 跨字段一致 | `hp<=max_hp`、`ok` 与 `error_code` 互斥、`Resolution.applied ≠ []` | 同上 |
| 引用完整性 | `location`/`exits`/`inventory` 指向的实体存在 | 加载快照时 `SCHEMA_VIOLATION`（不进入回合，Turn） |
| 业务前置条件 | 出口是否存在、目标是否同房间、材料是否足够 | **不在模型层**：`RuleService`（内部 `RuleEngine`）/服务层返回 `NOT_FOUND`/`NOT_ALLOWED`/`OUT_OF_RANGE` |

边界原则：**模型层只保证“结构上不可能非法”，业务合法性一律下沉到服务层（`RuleService`）与 `domain/rules/` 的 `RuleEngine`**；否则每加一条玩法规则都要改模型、加字段版本。模型层也不做 JSON 可序列化深校验（`params`/`result` 的序列化校验放在 `EventLog.stamp()` 里做一次 `json.dumps(..., allow_nan=False)`，失败返回 `SCHEMA_VIOLATION`），避免每个事件都跑一遍递归。

### 5.9 序列化、模型版本与迁移策略

- 对外/入库统一 `model_dump(mode="json")`；回放统一 `model_validate_json()`。禁止 pickle、禁止 `model_dump()` 直接入库（会留下 `datetime` 等非 JSON 类型，SQLite/Postgres JSON 列行为不一致）。
- `extra="forbid"` 全开：字段多了要显式报错，而不是静默吞掉；前向兼容靠 upcaster，不靠 `extra="allow"`。
- 不可变性说明：`frozen=True` 只防字段赋值，不防 `list`/`dict` 容器内部变更。纪律是**服务层不向 Agent/工具返回内部容器引用**（返回 `model_copy(deep=True)` 或 DTO）；第 18 章增加一条“快照不可变”测试：提交后对上一版本快照做深比较必须完全不变。若之后要强不可变，M2 后评估把 `list` 换成 `tuple`（JSON 序列化结果不变）。

**模型版本与迁移（已定稿：方案 B）**。版本号落在两处：① `GameState` 快照的版本随第 14 章的 `Snapshot` 包装记录；② 事件 payload 的版本按 `(method, payload_version)` 登记在 `EventLog` 侧的 upcaster 注册表。三个候选方案与取舍如下：

| 方案 | 做法 | 取舍 |
| --- | --- | --- |
| A | `Event` 增加 `schema_version: int = 1` 字段 | 最直观，但改动第 5 章 5.2 的 `Event` 基线字段、且与第 9 章事件 JSON 示例不一致，全队都要同步 |
| B（已采纳） | `Event` 不动；`EventLog` 侧表 `event_schema(method, version, upcaster_ref)` 登记，读取时先 upcast 再 `model_validate` | 不改基线、迁移逻辑集中、旧事件可读；代价是多一张小表和一次注册 |
| C | 只在快照层版本化，事件不版本化 | 最省事，但旧存档的旧 payload 回放会直接炸，不满足“事件溯源（Event Sourcing）可回放”的硬要求 |

第 14 章已确认采用方案 B：事件载荷版本登记在 `event_schema(method, version, upcaster_ref)` 注册表、不写入 `Event` 基线，快照版本记录在 `Snapshot` 包装上；方案 A/C 仅作为历史备选保留。

迁移流程固定四步：**读原始 JSON → 按 `(method, version)` 串行执行 upcaster 链 → 用当前模型校验 → 校验失败终止回放并告警**（不静默跳过、不猜测默认值）。禁止在模型里写 `if version == 1` 分支，迁移逻辑只允许存在于 `persistence/migrations/`。

### 5.10 候选轮子与拼接判断

| 仓库 | 定位 | 可复用部分 | 许可证 | 活跃度 | 建议 |
| --- | --- | --- | --- | --- | --- |
| pydantic/pydantic | 领域模型与校验内核 | `BaseModel`、`field_validator`/`model_validator`、`model_json_schema()`、`model_dump(mode="json")` | MIT | 2026-11 有提交，star 约 29.4k；2.14.3 要求 ≥3.10 | pip 依赖 |
| pydantic/pydantic-core | Pydantic v2 编译核心 | 无需直接使用，由 pydantic 锁定版本（`pydantic` 2.14.3 固定 `pydantic-core==2.50.4`） | MIT | 仓库已归档（代码迁入主仓）；2.50.4 要求 ≥3.10 | pip 依赖 |
| sqlalchemy/sqlalchemy | 持久化 ORM/事务 | ORM 映射、Unit of Work、版本计数器（第 14 章做事件表与快照表） | MIT | 2026-11 有提交，star 约 12.3k；2.0.57 要求 ≥3.7，2.1.8 要求 ≥3.11 | pip 依赖 |
| fastapi/sqlmodel | Pydantic + SQLAlchemy 合并层 | 仅借鉴“单类同时承载表与校验”的取舍经验 | MIT | 2026-11 有提交，star 约 18.6k；0.0.48 要求 ≥3.10 | 放弃 |
| msgspec/msgspec | 高性能序列化 + 类型校验 | 可作回放 JSON 解析的备选加速器 | BSD-3-Clause | 2026-11 有提交，star 约 4.2k；0.22.0 要求 ≥3.10 | 放弃 |
| pyeventsourcing/eventsourcing | 事件溯源 Aggregate | 借鉴 `Aggregate`/`@event` 的“命令→事件→投影”分层与乐观并发控制（Optimistic Concurrency Control, CAS）写法 | BSD-3-Clause | 2026-10 有提交，star 约 1.7k；9.4.6 要求 ≥3.9.2、9.5.6 要求 ≥3.11 | 参考设计 |

**复用判断与拼接边界（一）：Pydantic 是唯一模型层**。`domain/` 只 import `pydantic`；`model_json_schema()` 同时供第 7 章工具层生成参数 Schema，避免“领域模型一份校验、工具参数另一份手写 Schema”的经典漂移。边界：Pydantic 不承担业务规则，也不允许在模型里调用任何服务/IO。

**复用判断与拼接边界（二）：SQLAlchemy 在 `persistence/` 内做映射，不做领域模型**。领域模型是 `frozen` 的 Pydantic 对象，ORM 实体是另一套可变的 Row 类，两者在仓储层转换；`persistence/` import `domain/`，反向不允许（第 4 章依赖方向检查）。SQLModel 被放弃的原因不是许可证（MIT、≥3.10 都合格），而是它会把 SQLAlchemy 的类型系统带进 `domain/`，并且 0.0.x 的版本策略不适合作为基线。

**复用判断与拼接边界（三）：msgspec 与 eventsourcing 只作方案储备**。msgspec 的校验语义与 Pydantic 不同（Struct/严格类型），两套校验栈会让“同一字段两种错误”难以排查，故 MVP 放弃；只有当第 14 章实测“回放 1000 回合 JSON 解析耗时超过 1 秒”时才评估替换序列化层（替换面限定在 `EventLog` 的 JSON 编解码）。eventsourcing 的 `Aggregate` 基类会把状态变更入口收进聚合根，与本项目“服务层产事件 + GameEngine 唯一写入者”冲突，只借鉴其 upcaster 与快照恢复的分层，不引入依赖（同时它的 9.5 系列要求 Python ≥3.11，也会顶到 3.10 下限）。

### 本章结论

1. 领域模型定稿为 Pydantic v2（`pydantic>=2.14,<3`）单一实现；`domain/` 零外部依赖，SQLAlchemy 只允许进 `persistence/`，且必须锁 `SQLAlchemy>=2.0.57,<2.1`（2.1 系列要求 Python ≥3.11）。
2. `Event` 采用“草稿态/盖章态 + 全有或全无”校验：服务层只产草稿事件，`id`/`seq`/`ts`/`rng_seed` 由 `GameEngine` 在提交链上补全，`Result.events` 因此不需要两种类型。
3. 不可变边界：`Event`/`Result`/`Proposal`/`Resolution`/`ToolSpec`/`PerceptionSlice` 与全部世界实体均为 `frozen=True`，任何状态变更只能经 `GameState.apply(events)`；服务层禁止返回内部容器引用。
4. 模型层只做结构校验（类型、范围、跨字段、引用完整性），业务前置条件放服务层与 `RuleEngine`；跨章类型归属已定稿（5.6）：第 8 章 Agent 输出契约不进 `domain/`，第 10 章判定值对象 `CheckResult`/`DiceRoll`/`Modifier` 放 `domain/rules/types.py`；v0.2 的 `CharacterCard` DTO（5.7）同样 `extra="forbid"` + 冻结，只存在于 API 边界；事件 `params`/`result` 的 JSON 可序列化校验在 `EventLog.stamp()` 做一次。
5. 事件版本策略方案 B 已被第 14 章采纳（`EventLog` 侧 `event_schema` upcaster 注册表，不改 `Event` 基线；快照版本记在 `Snapshot` 包装），迁移固定“读原始 JSON → upcast 链 → 当前模型校验 → 失败终止回放”四步，M1 按此实现。
6. 不建议引入 SQLModel / msgspec：前者破坏 domain 依赖方向，后者造成双校验栈；eventsourcing 只作参考设计（BSD-3-Clause，无传染性风险）。
