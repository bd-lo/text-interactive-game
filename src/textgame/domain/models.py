"""第 5 章 5.4 世界实体与 5.5 GameState 及其余协作模型（逐字按文档实现）。

本节定义唯一写入者（Single Writer）持有的世界状态 ``GameState``，以及事件之源上的协作
对象：提案（Proposal）、裁决（Resolution）、动作意图（Action Intent）、感知切片
（Perception Slice）与工具规格（Tool Spec）。模型层只保证“结构上不可能非法”，业务合法性
下沉到服务层与 ``domain/rules/``（第 5 章 5.8）。

不可变边界：``Event``/``Result``/``Proposal``/``Resolution``/``ToolSpec``/``PerceptionSlice``
与全部世界实体均为 ``frozen=True``，任何状态变更只能经 ``GameState.apply(events)``（第 5 章
5.1/5.9）。
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .events import Event
from .types import CharacterId, Direction, ItemId, NpcId, QuestId, RoomId, TurnId, Verdict


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
    def _hp_within_max(self) -> Character:
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
    def _exits_sane(self) -> Room:
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
    def _window(self) -> ScheduleSlot:
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
    def _stages_contiguous(self) -> Quest:
        # completed 必须是从头开始的前缀；Skipped 中间阶段的 bug 在模型层即失败
        flags = [s.completed for s in self.stages]
        if flags != sorted(flags, reverse=True):
            raise ValueError("completed stages must form a prefix")
        return self


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
    def _referential_integrity(self) -> GameState:
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

    def apply(self, events: list[Event]) -> GameState:
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
    def _applied_requires_drafts(self) -> Resolution:
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
    def _policy_invariants(self) -> ToolSpec:
        if self.side_effect is SideEffect.NETWORK:
            raise ValueError("network tools are not allowed in the core tool layer")
        if self.json_schema.get("type") != "object":
            raise ValueError("tool json_schema root must be an object")
        if not self.description.strip():
            raise ValueError("description is required (used as tool prompt)")
        return self
