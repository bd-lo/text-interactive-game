"""单元测试：M1-002 领域模型（第 5 章 5.2/5.4/5.5、第 14 章 14.7）。

重点不是 happy path，而是钉死模型层的不变量与失败分支：ID 正则、frozen、
extra=forbid、Character 属性白名单/范围/hp<=max_hp、Room 自引用出口、Quest
阶段前缀、GameState 引用完整性、Result 的 ok/error_code 互斥与失败不带事件、
Event 草稿/盖章 all-or-none 与 UTC ts、ToolSpec 六字段不变量。

合法 ULID 统一用下面两个 26 位字面量（字符集不含 I/L/O/U）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from textgame.domain.events import Event, EventType
from textgame.domain.models import (
    NPC,
    ActionIntent,
    Character,
    EntityRef,
    GameState,
    Item,
    PerceptionSlice,
    Proposal,
    Quest,
    QuestState,
    Resolution,
    ResolutionPolicy,
    Reward,
    RngState,
    Room,
    ScheduleSlot,
    SideEffect,
    Stage,
    Status,
    ToolSpec,
)
from textgame.domain.results import ErrorCode, Result
from textgame.domain.types import (
    CharacterId,
    Direction,
    EventId,
    ItemId,
    NpcId,
    QuestId,
    RoomId,
    TurnId,
    Verdict,
)

_ULID = "01J0M3Z8Q9R5T7V1X2Y3Z4A5B6"
_OTHER_ULID = "01J0M3Z8Q9R5T7V1X2Y3Z4A5B7"
CHAR_ID = f"char_{_ULID}"
OTHER_CHAR_ID = f"char_{_OTHER_ULID}"
ROOM_ID = f"room_{_ULID}"
OTHER_ROOM_ID = f"room_{_OTHER_ULID}"
ITEM_ID = f"item_{_ULID}"
OTHER_ITEM_ID = f"item_{_OTHER_ULID}"
NPC_ID = f"npc_{_ULID}"
OTHER_NPC_ID = f"npc_{_OTHER_ULID}"
QUEST_ID = f"quest_{_ULID}"
_TS_UTC = datetime(2026, 11, 8, 12, 0, 3, tzinfo=timezone.utc)
_ATTRIBUTES = {"str": 10, "dex": 10, "con": 10, "int": 10, "wis": 10, "cha": 10, "max_hp": 20}


# ---------------------------------------------------------------------------
# 构造辅助（全部返回文档基线合法值，tests 只对关注字段做 overrides）
# ---------------------------------------------------------------------------
def _draft_event(**overrides: Any) -> Event:
    data: dict[str, Any] = {"turn_id": 1, "actor_id": "player_1", "method": "move_to"}
    data.update(overrides)
    return Event(**data)


def _stamped_event(**overrides: Any) -> Event:
    data: dict[str, Any] = {
        "id": "evt_000001",
        "turn_id": 1,
        "seq": 1,
        "actor_id": "player_1",
        "method": "move_to",
        "params": {"direction": "north"},
        "result": {"from": ROOM_ID, "to": OTHER_ROOM_ID},
        "rng_seed": 0,
        "ts": _TS_UTC,
    }
    data.update(overrides)
    return Event(**data)


def _character(**overrides: Any) -> Character:
    data: dict[str, Any] = {
        "id": CHAR_ID,
        "name": "旅人",
        "attributes": dict(_ATTRIBUTES),
        "inventory": [ITEM_ID],
        "location": ROOM_ID,
        "hp": 20,
        "created_turn": 1,
    }
    data.update(overrides)
    return Character(**data)


def _room(**overrides: Any) -> Room:
    data: dict[str, Any] = {
        "id": ROOM_ID,
        "name": "石室",
        "description": "一间阴冷的石室，墙壁渗着水。",
    }
    data.update(overrides)
    return Room(**data)


def _item(**overrides: Any) -> Item:
    data: dict[str, Any] = {"id": ITEM_ID, "name": "火把", "description": "照亮四周。"}
    data.update(overrides)
    return Item(**data)


def _npc(**overrides: Any) -> NPC:
    data: dict[str, Any] = {
        "id": NPC_ID,
        "name": "守卫",
        "room_id": ROOM_ID,
        "memory_ref": "mem_npc_guard",
    }
    data.update(overrides)
    return NPC(**data)


def _stage(stage_no: int = 1, **overrides: Any) -> Stage:
    data: dict[str, Any] = {"id": f"stage_{stage_no}", "description": f"第 {stage_no} 步"}
    data.update(overrides)
    return Stage(**data)


def _quest(stages: list[Stage] | None = None, **overrides: Any) -> Quest:
    data: dict[str, Any] = {
        "id": QUEST_ID,
        "title": "初入石室",
        "stages": [_stage()] if stages is None else stages,
    }
    data.update(overrides)
    return Quest(**data)


def _state_kwargs() -> dict[str, Any]:
    """返回一份引用完整、可通过校验的 GameState 原始 dict（供失败分支改字段）。"""
    state = GameState(
        turn=1,
        world_time=0,
        characters={CHAR_ID: _character()},
        rooms={ROOM_ID: _room()},
        items={ITEM_ID: _item()},
        npcs={NPC_ID: _npc()},
        quests={QUEST_ID: _quest()},
        rng_state=RngState(save_seed="save-42"),
    )
    kwargs: dict[str, Any] = state.model_dump()
    return kwargs


# ---------------------------------------------------------------------------
# 5.2 基础类型：ID 正则、枚举字面量
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("alias", "value"),
    [
        (CharacterId, CHAR_ID),
        (RoomId, ROOM_ID),
        (ItemId, ITEM_ID),
        (NpcId, NPC_ID),
        (QuestId, QUEST_ID),
    ],
)
def test_id_aliases_accept_prefix_plus_ulid(alias: Any, value: str) -> None:
    """5.2：<前缀>_<26 位 ULID> 合法。"""
    assert TypeAdapter(alias).validate_python(value) == value


@pytest.mark.parametrize(
    "bad",
    [
        f"room_{_ULID}",  # 前缀错误
        f"char_{_ULID[:-1]}",  # 25 位
        f"char_{_ULID}6",  # 27 位
        f"char_{_ULID[:-1]}I",  # ULID 字符集不含 I
        f"char_{_ULID[:-1]}L",  # 不含 L
        f"char_{_ULID[:-1]}O",  # 不含 O
        f"char_{_ULID[:-1]}U",  # 不含 U
        f"char_{_ULID.lower()}",  # 小写不合法
        "char_",
        "",
    ],
)
def test_character_id_rejects_invalid_values(bad: str) -> None:
    """5.2：前缀、长度、ULID 字符集任一项不符都必须拒绝。"""
    with pytest.raises(ValidationError):
        TypeAdapter(CharacterId).validate_python(bad)


@pytest.mark.parametrize("bad", ["evt_00001", "evt_", "evt_abcdef", "evt_ 000001"])
def test_event_id_rejects_invalid_values(bad: str) -> None:
    """5.2：EventId 为 evt_ + 至少 6 位数字。"""
    with pytest.raises(ValidationError):
        TypeAdapter(EventId).validate_python(bad)


def test_event_id_accepts_six_or_more_digits() -> None:
    adapter = TypeAdapter(EventId)
    assert adapter.validate_python("evt_000001") == "evt_000001"
    assert adapter.validate_python("evt_0010234") == "evt_0010234"


def test_turn_id_must_be_positive() -> None:
    """5.2：TurnId = int ge=1。"""
    adapter = TypeAdapter(TurnId)
    assert adapter.validate_python(1) == 1
    for bad in (0, -1):
        with pytest.raises(ValidationError):
            adapter.validate_python(bad)


@pytest.mark.parametrize("direction", ["north", "south", "east", "west", "up", "down", "in", "out"])
def test_direction_accepts_documented_directions(direction: str) -> None:
    assert TypeAdapter(Direction).validate_python(direction) == direction


def test_direction_rejects_unknown_direction() -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(Direction).validate_python("northeast")


@pytest.mark.parametrize("verdict", ["applied", "rejected", "conflict", "deferred"])
def test_verdict_accepts_documented_verdicts(verdict: str) -> None:
    assert TypeAdapter(Verdict).validate_python(verdict) == verdict


def test_verdict_rejects_unknown_verdict() -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(Verdict).validate_python("maybe")


# ---------------------------------------------------------------------------
# 5.2 ErrorCode / Result
# ---------------------------------------------------------------------------
def test_error_code_has_eight_stable_codes() -> None:
    assert len(ErrorCode) == 8
    assert {code.name for code in ErrorCode} == {
        "INVALID_INPUT",
        "NOT_FOUND",
        "NOT_ALLOWED",
        "OUT_OF_RANGE",
        "CONFLICT",
        "SCHEMA_VIOLATION",
        "LLM_UNAVAILABLE",
        "INTERNAL",
    }
    assert ErrorCode.INVALID_INPUT.value == "INVALID_INPUT"
    assert isinstance(ErrorCode.INVALID_INPUT, str)


def test_result_success_constructor() -> None:
    result = Result.success()
    assert result.ok is True
    assert result.error_code is None
    assert result.events == []
    with_events = Result.success(data={"n": 1}, events=[_draft_event()])
    assert with_events.data == {"n": 1}
    assert len(with_events.events) == 1


def test_result_fail_constructor() -> None:
    result = Result.fail(ErrorCode.NOT_FOUND, "目标不存在")
    assert result.ok is False
    assert result.error_code is ErrorCode.NOT_FOUND
    assert result.message == "目标不存在"
    assert result.events == []


def test_result_rejects_ok_with_error_code() -> None:
    """5.2：ok=True 不得携带 error_code。"""
    with pytest.raises(ValidationError, match="cannot carry error_code"):
        Result(ok=True, error_code=ErrorCode.INTERNAL)


def test_result_rejects_failure_without_error_code() -> None:
    """5.2：ok=False 必须带 error_code。"""
    with pytest.raises(ValidationError, match="requires error_code"):
        Result(ok=False)


def test_result_rejects_failure_with_events() -> None:
    """5.2：失败 Result 不得携带事件（原子性）。"""
    with pytest.raises(ValidationError, match="must not carry events"):
        Result(ok=False, error_code=ErrorCode.CONFLICT, events=[_draft_event()])


def test_result_is_frozen() -> None:
    result = Result.success()
    with pytest.raises(ValidationError, match="frozen"):
        result.ok = False


# ---------------------------------------------------------------------------
# 5.2/5.3 Event：草稿态/盖章态、UTC、范围
# ---------------------------------------------------------------------------
def test_event_draft_is_valid_and_unstamped() -> None:
    event = _draft_event()
    assert event.is_stamped is False
    assert event.id is None
    assert event.seq is None
    assert event.rng_seed is None
    assert event.ts is None
    assert event.params == {}
    assert event.result == {}


def test_event_fully_stamped_is_stamped() -> None:
    event = _stamped_event()
    assert event.is_stamped is True
    assert event.id == "evt_000001"
    assert event.seq == 1
    assert isinstance(event.params["direction"], str)


@pytest.mark.parametrize(
    "partial",
    [
        {"id": "evt_000001"},
        {"seq": 1},
        {"rng_seed": 1},
        {"ts": _TS_UTC},
        {"id": "evt_000001", "seq": 1},
        {"id": "evt_000001", "rng_seed": 1},
        {"seq": 1, "ts": _TS_UTC},
        {"id": "evt_000001", "seq": 1, "rng_seed": 1},
    ],
)
def test_event_rejects_partially_stamped(partial: dict[str, Any]) -> None:
    """5.3：id/seq/rng_seed/ts 必须全有或全无，半盖章事件直接失败。"""
    with pytest.raises(ValidationError, match="fully stamped or fully draft"):
        _draft_event(**partial)


def test_event_rejects_naive_ts() -> None:
    """5.2：ts 必须带时区。"""
    with pytest.raises(ValidationError, match="timezone-aware"):
        _stamped_event(ts=datetime(2026, 11, 8, 12, 0, 3))


def test_event_normalizes_ts_to_utc() -> None:
    """5.2：带偏移的 ts 统一 astimezone(UTC)。"""
    event = _stamped_event(ts=datetime(2026, 11, 8, 20, 0, 3, tzinfo=timezone(timedelta(hours=8))))
    assert event.ts is not None
    assert event.ts == _TS_UTC
    assert event.ts.utcoffset() == timedelta(0)


@pytest.mark.parametrize("rng_seed", [0, 0x7FFF_FFFF_FFFF_FFFF])
def test_event_rng_seed_accepts_signed_64bit_range(rng_seed: int) -> None:
    assert _stamped_event(rng_seed=rng_seed).rng_seed == rng_seed


@pytest.mark.parametrize("rng_seed", [-1, 0x8000_0000_0000_0000])
def test_event_rng_seed_rejects_out_of_range(rng_seed: int) -> None:
    """5.3：rng_seed 必须落在 [0, 2**63-1]。"""
    with pytest.raises(ValidationError):
        _stamped_event(rng_seed=rng_seed)


def test_event_seq_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        _stamped_event(seq=0)


@pytest.mark.parametrize("bad_id", ["evt_1", "evt_00001", "000001"])
def test_event_rejects_bad_event_id(bad_id: str) -> None:
    with pytest.raises(ValidationError):
        _stamped_event(id=bad_id)


@pytest.mark.parametrize("bad_method", ["Attack", "_move", "move-to", "move__to", "move_", "1move"])
def test_event_rejects_bad_method(bad_method: str) -> None:
    """5.2：method 必须是 snake_case 工具名。"""
    with pytest.raises(ValidationError):
        _draft_event(method=bad_method)


def test_event_actor_id_boundaries() -> None:
    assert _draft_event(actor_id="p").actor_id == "p"
    with pytest.raises(ValidationError):
        _draft_event(actor_id="")
    with pytest.raises(ValidationError):
        _draft_event(actor_id="x" * 65)


def test_event_is_frozen() -> None:
    event = _draft_event()
    with pytest.raises(ValidationError, match="frozen"):
        event.actor_id = "player_2"


def test_event_type_values_match_chapter_14_7() -> None:
    """14.7 核心事件类型 + M2 扩展 + H4/L1 环境伤害：共 10 个，game_created 为小写字面名。

    M2 按第 6 章 §6.4 动作表扩展 ``DIALOGUE_EXCHANGED``（talk_to -> dialogue_exchanged）；
    第 14 章 §14.7 核心表未列该成员，已登记为 M2 契约偏离（见 .work/m2-contract.md §3.1）。
    H4/L1 新增 ``ENVIRONMENT_DAMAGE``（story 枯井寒气等可重复环境伤害；旧场景不产生）。
    """
    assert {member.value for member in EventType} == {
        "game_created",
        "CHARACTER_MOVED",
        "ITEM_TAKEN",
        "ITEM_CONSUMED",
        "DIALOGUE_EXCHANGED",
        "CRAFT_DETERMINISTIC",
        "CRAFT_GENERATED",
        "CRAFT_REJECTED",
        "ADMIN_CORRECTION",
        "ENVIRONMENT_DAMAGE",
    }
    assert EventType.GAME_CREATED.value == "game_created"


# ---------------------------------------------------------------------------
# 5.4 世界实体
# ---------------------------------------------------------------------------
def test_status_accepts_boundaries() -> None:
    assert Status(id="status_poisoned", name="中毒", duration_turns=-1).stacks == 1
    assert Status(id="status_poisoned", name="中毒", duration_turns=0, stacks=99).stacks == 99


@pytest.mark.parametrize(
    "overrides",
    [
        {"id": "status_a"},
        {"id": "status_AbC"},
        {"id": "poisoned"},
        {"duration_turns": -2},
        {"stacks": 0},
        {"stacks": 100},
    ],
)
def test_status_rejects_invalid_fields(overrides: dict[str, Any]) -> None:
    data: dict[str, Any] = {"id": "status_poisoned", "name": "中毒", "duration_turns": 1}
    data.update(overrides)
    with pytest.raises(ValidationError):
        Status(**data)


def test_status_is_frozen() -> None:
    status = Status(id="status_poisoned", name="中毒", duration_turns=1)
    with pytest.raises(ValidationError, match="frozen"):
        status.stacks = 2


def test_character_accepts_documented_baseline() -> None:
    character = _character()
    assert character.attributes["max_hp"] == 20
    assert character.inventory == [ITEM_ID]
    assert character.status == []
    assert character.private_memory_ref is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"attributes": {"luck": 10}},  # 未知属性名
        {"attributes": {"str": -11}},  # 下界外
        {"attributes": {"str": 101}},  # 上界外
        {"attributes": {}},  # 至少一个属性
        {"hp": -1},
        {"created_turn": 0},
        {"name": ""},
        {"name": "x" * 25},
        {"location": "room_x"},
        {"inventory": ["not_an_item"]},
    ],
)
def test_character_rejects_invalid_fields(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _character(**overrides)


def test_character_attribute_value_boundaries() -> None:
    assert _character(attributes={"str": -10}).attributes == {"str": -10}
    assert _character(attributes={"str": 100}).attributes == {"str": 100}


def test_character_rejects_unknown_attribute_key_message() -> None:
    with pytest.raises(ValidationError, match="unknown attribute keys"):
        _character(attributes={"str": 10, "luck": 1})


def test_character_hp_must_not_exceed_max_hp() -> None:
    """5.4：max_hp 挂在 attributes 里；hp > max_hp 在模型层失败。"""
    with pytest.raises(ValidationError, match="exceeds max_hp"):
        _character(attributes={**_ATTRIBUTES, "max_hp": 19}, hp=20)


def test_character_hp_equal_max_hp_allowed() -> None:
    assert _character().hp == 20


def test_character_without_max_hp_has_no_upper_hp_check() -> None:
    """5.4 原文：max_hp 缺省时只校验 hp>=0（不新增基线字段）。"""
    assert _character(attributes={"str": 10}, hp=999).hp == 999


def test_character_rejects_too_many_statuses_and_inventory() -> None:
    status = Status(id="status_x1", name="状态", duration_turns=1)
    with pytest.raises(ValidationError):
        _character(status=[status] * 17)
    with pytest.raises(ValidationError):
        _character(inventory=[ITEM_ID] * 65)


def test_character_is_frozen() -> None:
    character = _character()
    with pytest.raises(ValidationError, match="frozen"):
        character.hp = 1


def test_room_accepts_exit_to_other_room() -> None:
    room = _room(exits={"north": OTHER_ROOM_ID})
    assert room.exits == {"north": OTHER_ROOM_ID}
    assert room.item_ids == []
    assert room.npc_ids == []


def test_room_rejects_self_exit() -> None:
    """5.4：room cannot exit to itself。"""
    with pytest.raises(ValidationError, match="cannot exit to itself"):
        _room(exits={"north": ROOM_ID})


def test_room_rejects_unknown_direction_key() -> None:
    with pytest.raises(ValidationError):
        _room(exits={"northeast": OTHER_ROOM_ID})


@pytest.mark.parametrize(
    "overrides",
    [{"name": ""}, {"name": "x" * 33}, {"description": ""}, {"description": "x" * 501}],
)
def test_room_rejects_invalid_text_bounds(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _room(**overrides)


def test_room_rejects_too_many_ambient_tags() -> None:
    with pytest.raises(ValidationError):
        _room(ambient_tags=[f"tag{i}" for i in range(9)])


def test_room_is_frozen() -> None:
    room = _room()
    with pytest.raises(ValidationError, match="frozen"):
        room.name = "别处"


def test_item_accepts_stats_and_rarity() -> None:
    item = _item(stats={"atk": 3, "weight": 1.5}, rarity="rare", tags=["武器"])
    assert item.stats["atk"] == 3
    assert item.rarity == "rare"
    assert item.stackable is False


def test_item_rejects_unknown_rarity() -> None:
    with pytest.raises(ValidationError):
        _item(rarity="mythic")


def test_item_rejects_too_many_tags() -> None:
    with pytest.raises(ValidationError):
        _item(tags=[f"tag{i}" for i in range(9)])


def test_item_is_frozen() -> None:
    item = _item()
    with pytest.raises(ValidationError, match="frozen"):
        item.stackable = True


def test_schedule_slot_accepts_window() -> None:
    slot = ScheduleSlot(from_world_minute=480, to_world_minute=1020, room_id=ROOM_ID)
    assert slot.from_world_minute == 480


@pytest.mark.parametrize(
    "overrides",
    [
        {"from_world_minute": 600, "to_world_minute": 600},  # 空窗口
        {"from_world_minute": -1},
        {"to_world_minute": 1440},
    ],
)
def test_schedule_slot_rejects_invalid_window(overrides: dict[str, Any]) -> None:
    data: dict[str, Any] = {
        "from_world_minute": 480,
        "to_world_minute": 1020,
        "room_id": ROOM_ID,
    }
    data.update(overrides)
    with pytest.raises(ValidationError):
        ScheduleSlot(**data)


def test_schedule_slot_is_frozen() -> None:
    slot = ScheduleSlot(from_world_minute=480, to_world_minute=1020, room_id=ROOM_ID)
    with pytest.raises(ValidationError, match="frozen"):
        slot.room_id = OTHER_ROOM_ID


def test_npc_accepts_defaults_and_schedule() -> None:
    npc = _npc(
        schedule=[ScheduleSlot(from_world_minute=480, to_world_minute=1020, room_id=ROOM_ID)]
    )
    assert npc.dialogue_state == "idle"
    assert npc.faction is None
    assert len(npc.schedule) == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"room_id": "room_x"},
        {"memory_ref": ""},
        {"memory_ref": "x" * 65},
        {"name": "x" * 25},
        {"schedule": [{"from_world_minute": 1, "to_world_minute": 1, "room_id": ROOM_ID}]},
    ],
)
def test_npc_rejects_invalid_fields(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _npc(**overrides)


def test_npc_rejects_too_many_schedule_slots() -> None:
    slot = ScheduleSlot(from_world_minute=1, to_world_minute=2, room_id=ROOM_ID)
    with pytest.raises(ValidationError):
        _npc(schedule=[slot] * 33)


@pytest.mark.parametrize("stage_id", ["stage_1", "stage_999"])
def test_stage_id_pattern_accepts(stage_id: str) -> None:
    assert Stage(id=stage_id, description="一步").id == stage_id


@pytest.mark.parametrize("stage_id", ["stage_", "stage_1000", "stage01", "stage_a"])
def test_stage_id_pattern_rejects(stage_id: str) -> None:
    with pytest.raises(ValidationError):
        Stage(id=stage_id, description="一步")


def test_quest_defaults_to_not_started() -> None:
    quest = _quest()
    assert quest.state is QuestState.NOT_STARTED
    assert quest.rewards == []


@pytest.mark.parametrize(
    "flags",
    [[False], [True], [True, False], [True, True, False], [True, True, True]],
)
def test_quest_accepts_completed_prefix(flags: list[bool]) -> None:
    """5.4：completed 必须是从头开始的前缀。"""
    stages = [_stage(i + 1, completed=flag) for i, flag in enumerate(flags)]
    assert _quest(stages=stages).stages[-1].completed is flags[-1]


@pytest.mark.parametrize(
    "flags",
    [[False, True], [True, False, True], [False, False, True]],
)
def test_quest_rejects_non_prefix_completion(flags: list[bool]) -> None:
    stages = [_stage(i + 1, completed=flag) for i, flag in enumerate(flags)]
    with pytest.raises(ValidationError, match="must form a prefix"):
        _quest(stages=stages)


def test_quest_requires_at_least_one_stage() -> None:
    with pytest.raises(ValidationError):
        _quest(stages=[])


def test_quest_rejects_unknown_state_and_short_title() -> None:
    with pytest.raises(ValidationError):
        _quest(state="archived")
    with pytest.raises(ValidationError):
        _quest(title="")


def test_quest_is_frozen() -> None:
    quest = _quest()
    with pytest.raises(ValidationError, match="frozen"):
        quest.state = QuestState.ACTIVE


def test_reward_quantity_bounds() -> None:
    assert Reward(item_id=ITEM_ID, qty=1).qty == 1
    assert Reward(item_id=ITEM_ID, qty=999).qty == 999
    for bad_qty in (0, 1000):
        with pytest.raises(ValidationError):
            Reward(item_id=ITEM_ID, qty=bad_qty)


def test_reward_rejects_invalid_item_id() -> None:
    with pytest.raises(ValidationError):
        Reward(item_id="not_an_item", qty=1)


def test_quest_state_values() -> None:
    assert {state.value for state in QuestState} == {
        "not_started",
        "active",
        "completed",
        "failed",
    }


# ---------------------------------------------------------------------------
# 5.5 RngState / GameState
# ---------------------------------------------------------------------------
def test_rng_state_defaults() -> None:
    rng = RngState(save_seed="save-42")
    assert rng.mode == "derived"
    assert rng.last_seq == 0


@pytest.mark.parametrize(
    "overrides",
    [{"mode": "python"}, {"save_seed": ""}, {"save_seed": "x" * 65}, {"last_seq": -1}],
)
def test_rng_state_rejects_invalid_fields(overrides: dict[str, Any]) -> None:
    data: dict[str, Any] = {"save_seed": "save-42"}
    data.update(overrides)
    with pytest.raises(ValidationError):
        RngState(**data)


def test_gamestate_accepts_consistent_state() -> None:
    state = GameState(**_state_kwargs())
    assert state.turn == 1
    assert CHAR_ID in state.characters
    assert state.rng_state.save_seed == "save-42"


def test_gamestate_minimal_collections_default_empty() -> None:
    state = GameState(turn=1, world_time=0, rng_state=RngState(save_seed="save-42"))
    assert state.characters == {}
    assert state.rooms == {}


def test_gamestate_accepts_connected_rooms() -> None:
    """两个房间互相连通且角色/物品/NPC 引用完整时合法。"""
    kwargs = _state_kwargs()
    kwargs["rooms"] = {
        ROOM_ID: {**_room(exits={"north": OTHER_ROOM_ID}).model_dump()},
        OTHER_ROOM_ID: {
            "id": OTHER_ROOM_ID,
            "name": "走廊",
            "description": "一条向北延伸的走廊。",
            "exits": {"south": ROOM_ID},
        },
    }
    state = GameState(**kwargs)
    assert state.rooms[ROOM_ID].exits == {"north": OTHER_ROOM_ID}


def test_gamestate_rejects_character_in_unknown_room() -> None:
    """5.5：角色 location 必须指向已存在房间。"""
    kwargs = _state_kwargs()
    kwargs["characters"][CHAR_ID]["location"] = OTHER_ROOM_ID
    with pytest.raises(ValidationError, match="in unknown room"):
        GameState(**kwargs)


def test_gamestate_rejects_character_holding_unknown_item() -> None:
    """5.5：inventory 必须指向已存在物品。"""
    kwargs = _state_kwargs()
    kwargs["characters"][CHAR_ID]["inventory"] = [OTHER_ITEM_ID]
    with pytest.raises(ValidationError, match="holds unknown items"):
        GameState(**kwargs)


def test_gamestate_rejects_character_key_mismatch() -> None:
    kwargs = _state_kwargs()
    kwargs["characters"] = {OTHER_CHAR_ID: kwargs["characters"][CHAR_ID]}
    with pytest.raises(ValidationError, match="character key mismatch"):
        GameState(**kwargs)


def test_gamestate_rejects_room_key_mismatch() -> None:
    kwargs = _state_kwargs()
    kwargs["characters"] = {}
    kwargs["npcs"] = {}
    kwargs["rooms"] = {OTHER_ROOM_ID: kwargs["rooms"][ROOM_ID]}
    with pytest.raises(ValidationError, match="room key mismatch"):
        GameState(**kwargs)


def test_gamestate_rejects_unknown_exit() -> None:
    """5.5：房间出口必须指向已存在房间。"""
    kwargs = _state_kwargs()
    kwargs["rooms"][ROOM_ID]["exits"] = {"north": OTHER_ROOM_ID}
    with pytest.raises(ValidationError, match="unknown exits"):
        GameState(**kwargs)


def test_gamestate_rejects_npc_in_unknown_room() -> None:
    kwargs = _state_kwargs()
    kwargs["npcs"][NPC_ID]["room_id"] = OTHER_ROOM_ID
    with pytest.raises(ValidationError, match="invalid id or room"):
        GameState(**kwargs)


def test_gamestate_rejects_npc_key_mismatch() -> None:
    kwargs = _state_kwargs()
    kwargs["npcs"] = {OTHER_NPC_ID: kwargs["npcs"][NPC_ID]}
    with pytest.raises(ValidationError, match="invalid id or room"):
        GameState(**kwargs)


def test_gamestate_rejects_zero_turn_and_negative_world_time() -> None:
    kwargs = _state_kwargs()
    kwargs["turn"] = 0
    with pytest.raises(ValidationError):
        GameState(**kwargs)
    kwargs = _state_kwargs()
    kwargs["world_time"] = -1
    with pytest.raises(ValidationError):
        GameState(**kwargs)


def test_gamestate_requires_rng_state() -> None:
    kwargs = _state_kwargs()
    del kwargs["rng_state"]
    with pytest.raises(ValidationError):
        GameState(**kwargs)


def test_gamestate_is_frozen() -> None:
    state = GameState(**_state_kwargs())
    with pytest.raises(ValidationError, match="frozen"):
        state.turn = 2


def test_gamestate_apply_delegates_to_reducers() -> None:
    """M2-005：apply 委托 ``domain.reducers.apply_events``（M1 的 NotImplementedError 已演进）。

    第 14 章 14.7 的 reducer 落地后 apply 是纯投影：正常事件可投影、缺失 event_type
    抛 ValueError、且不修改原状态（frozen）。完整 reducer 分支见 tests/unit/test_reducers.py。
    """
    state = GameState(**_state_kwargs())
    event = _draft_event(
        event_type=EventType.DIALOGUE_EXCHANGED,
        params={"npc_id": NPC_ID, "topic": "你好"},
        result={"dialogue_state": "greeted"},
    )

    projected = state.apply([event])

    assert projected.npcs[NPC_ID].dialogue_state == "greeted"
    assert state.npcs[NPC_ID].dialogue_state == "idle"  # frozen：原状态不被修改
    with pytest.raises(ValueError, match="event_type"):
        state.apply([_draft_event()])


# ---------------------------------------------------------------------------
# 5.5 Proposal / Resolution / ActionIntent / EntityRef / PerceptionSlice
# ---------------------------------------------------------------------------
def test_proposal_defaults_and_priority_bounds() -> None:
    proposal = Proposal(turn_id=1, actor_id="player_1", tool="move_to", reason="向北走")
    assert proposal.args == {}
    assert proposal.priority == 50
    assert (
        Proposal(
            turn_id=1, actor_id="player_1", tool="move_to", reason="向北走", priority=0
        ).priority
        == 0
    )
    assert (
        Proposal(
            turn_id=1, actor_id="player_1", tool="move_to", reason="向北走", priority=100
        ).priority
        == 100
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"tool": "Move"},
        {"tool": "move-to"},
        {"reason": ""},
        {"priority": -1},
        {"priority": 101},
    ],
)
def test_proposal_rejects_invalid_fields(overrides: dict[str, Any]) -> None:
    data: dict[str, Any] = {"turn_id": 1, "actor_id": "player_1", "tool": "move_to", "reason": "走"}
    data.update(overrides)
    with pytest.raises(ValidationError):
        Proposal(**data)


def test_proposal_is_frozen() -> None:
    proposal = Proposal(turn_id=1, actor_id="player_1", tool="move_to", reason="走")
    with pytest.raises(ValidationError, match="frozen"):
        proposal.priority = 1


def test_resolution_applied_requires_draft_events() -> None:
    resolution = Resolution(
        proposal_id="p_000042_001", verdict="applied", applied_events=[_draft_event()]
    )
    assert resolution.reason == ""
    assert resolution.applied_events[0].is_stamped is False


def test_resolution_rejects_applied_without_events() -> None:
    """5.5：verdict=applied 必须至少携带一条草稿事件。"""
    with pytest.raises(ValidationError, match="at least one draft event"):
        Resolution(proposal_id="p_000042_001", verdict="applied")


def test_resolution_rejects_non_applied_with_events() -> None:
    for verdict in ("rejected", "conflict", "deferred"):
        with pytest.raises(ValidationError, match="must not carry events"):
            Resolution(proposal_id="p_000042_001", verdict=verdict, applied_events=[_draft_event()])


def test_resolution_rejects_stamped_events() -> None:
    """5.5：盖章是 GameEngine 的职责，Resolution 只允许草稿事件。"""
    with pytest.raises(ValidationError, match="must be drafts"):
        Resolution(proposal_id="p_000042_001", verdict="applied", applied_events=[_stamped_event()])


def test_resolution_rejects_unknown_verdict() -> None:
    with pytest.raises(ValidationError):
        Resolution(proposal_id="p_000042_001", verdict="ignored")


def test_resolution_is_frozen() -> None:
    resolution = Resolution(proposal_id="p_000042_001", verdict="rejected")
    with pytest.raises(ValidationError, match="frozen"):
        resolution.reason = "改"


def test_action_intent_accepts_and_is_mutable() -> None:
    """5.5：ActionIntent 是回合内 DTO，唯一未冻结的模型（只 extra=forbid）。"""
    intent = ActionIntent(action="move_to", target="north", confidence=0.5)
    assert intent.params == {}
    intent.confidence = 0.9
    assert intent.confidence == 0.9


@pytest.mark.parametrize(
    "overrides",
    [
        {"action": "Move"},
        {"action": "2move"},
        {"action": ""},
        {"confidence": -0.1},
        {"confidence": 1.1},
        {"target": "x" * 65},
    ],
)
def test_action_intent_rejects_invalid_fields(overrides: dict[str, Any]) -> None:
    data: dict[str, Any] = {"action": "move_to", "confidence": 0.5}
    data.update(overrides)
    with pytest.raises(ValidationError):
        ActionIntent(**data)


def test_action_intent_confidence_boundaries() -> None:
    assert ActionIntent(action="move_to", confidence=0.0).confidence == 0.0
    assert ActionIntent(action="move_to", confidence=1.0).confidence == 1.0


@pytest.mark.parametrize("kind", ["character", "room", "item", "npc", "quest"])
def test_entity_ref_accepts_documented_kinds(kind: str) -> None:
    ref = EntityRef(kind=kind, id=CHAR_ID, name="旅人")
    assert ref.kind == kind
    assert ref.name == "旅人"


def test_entity_ref_rejects_unknown_kind_and_empty_id() -> None:
    with pytest.raises(ValidationError):
        EntityRef(kind="player", id=CHAR_ID, name="旅人")
    with pytest.raises(ValidationError):
        EntityRef(kind="character", id="", name="旅人")


def test_entity_ref_is_frozen() -> None:
    ref = EntityRef(kind="character", id=CHAR_ID, name="旅人")
    with pytest.raises(ValidationError, match="frozen"):
        ref.name = "别人"


def test_perception_slice_defaults_and_nested_validation() -> None:
    slice_ = PerceptionSlice(
        actor_id="player_1",
        visible_entities=[EntityRef(kind="room", id=ROOM_ID, name="石室")],
        local_events=[_draft_event()],
        private_memory=["记得那扇门"],
    )
    assert slice_.plan_hint is None
    assert len(slice_.local_events) == 1


def test_perception_slice_rejects_invalid_nested_ref() -> None:
    with pytest.raises(ValidationError):
        PerceptionSlice(
            actor_id="player_1",
            visible_entities=[{"kind": "player", "id": CHAR_ID, "name": "x"}],
        )


def test_perception_slice_plan_hint_bound_and_frozen() -> None:
    with pytest.raises(ValidationError):
        PerceptionSlice(actor_id="player_1", plan_hint="x" * 201)
    slice_ = PerceptionSlice(actor_id="player_1")
    with pytest.raises(ValidationError, match="frozen"):
        slice_.actor_id = "player_2"


# ---------------------------------------------------------------------------
# 5.5 SideEffect / ResolutionPolicy / ToolSpec
# ---------------------------------------------------------------------------
def test_side_effect_values() -> None:
    assert {effect.value for effect in SideEffect} == {"READ_ONLY", "DESTRUCTIVE", "NETWORK"}


def test_resolution_policy_values() -> None:
    assert {policy.value for policy in ResolutionPolicy} == {
        "deterministic",
        "agent_assisted",
        "manual",
    }


def test_tool_spec_keeps_exactly_six_fields() -> None:
    """5.5：ToolSpec 严格保持六字段，不额外加 schema_hash 等派生列。"""
    assert set(ToolSpec.model_fields) == {
        "name",
        "description",
        "json_schema",
        "actor_scope",
        "side_effect",
        "resolution_policy",
    }


def test_tool_spec_defaults() -> None:
    spec = ToolSpec(name="move_to", description="向指定方向移动", json_schema={"type": "object"})
    assert spec.actor_scope == frozenset()
    assert spec.side_effect is SideEffect.READ_ONLY
    assert spec.resolution_policy is ResolutionPolicy.DETERMINISTIC


def test_tool_spec_accepts_destructive_with_scope() -> None:
    spec = ToolSpec(
        name="take_item",
        description="拾取物品",
        json_schema={"type": "object", "properties": {"item_id": {"type": "string"}}},
        actor_scope={"character"},
        side_effect=SideEffect.DESTRUCTIVE,
        resolution_policy=ResolutionPolicy.AGENT_ASSISTED,
    )
    assert spec.actor_scope == frozenset({"character"})
    assert spec.side_effect is SideEffect.DESTRUCTIVE


def test_tool_spec_rejects_network_side_effect() -> None:
    """5.5：核心工具层不允许 NETWORK 副作用。"""
    with pytest.raises(ValidationError, match="network tools are not allowed"):
        ToolSpec(
            name="fetch_url",
            description="联网抓取",
            json_schema={"type": "object"},
            side_effect=SideEffect.NETWORK,
        )


@pytest.mark.parametrize("json_schema", [{}, {"type": "array"}, {"type": None}])
def test_tool_spec_requires_object_root_schema(json_schema: dict[str, Any]) -> None:
    """5.5：tool json_schema 根必须是 object。"""
    with pytest.raises(ValidationError, match="root must be an object"):
        ToolSpec(name="move_to", description="移动", json_schema=json_schema)


@pytest.mark.parametrize("name", ["Move", "move-to", "1move", "move__to"])
def test_tool_spec_rejects_bad_name(name: str) -> None:
    with pytest.raises(ValidationError):
        ToolSpec(name=name, description="移动", json_schema={"type": "object"})


def test_tool_spec_rejects_whitespace_only_description() -> None:
    with pytest.raises(ValidationError, match="description is required"):
        ToolSpec(name="move_to", description="   ", json_schema={"type": "object"})


def test_tool_spec_rejects_extra_derived_field() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ToolSpec(
            name="move_to",
            description="移动",
            json_schema={"type": "object"},
            schema_hash="deadbeef",
        )


def test_tool_spec_is_frozen() -> None:
    spec = ToolSpec(name="move_to", description="移动", json_schema={"type": "object"})
    with pytest.raises(ValidationError, match="frozen"):
        spec.description = "改"


# ---------------------------------------------------------------------------
# 全模型共性：extra=forbid 与 frozen（第 5 章 5.1/5.9 的不可变边界）
# ---------------------------------------------------------------------------
_EXTRA_FORBID_CASES: list[tuple[type[BaseModel], dict[str, Any]]] = [
    (Event, {"turn_id": 1, "actor_id": "player_1", "method": "move_to"}),
    (Result, {"ok": True}),
    (Status, {"id": "status_poisoned", "name": "中毒", "duration_turns": 1}),
    (
        Character,
        {
            "id": CHAR_ID,
            "name": "旅人",
            "attributes": {"str": 1},
            "location": ROOM_ID,
            "hp": 1,
            "created_turn": 1,
        },
    ),
    (Room, {"id": ROOM_ID, "name": "石室", "description": "阴冷。"}),
    (Item, {"id": ITEM_ID, "name": "火把", "description": "照亮四周。"}),
    (
        ScheduleSlot,
        {"from_world_minute": 480, "to_world_minute": 1020, "room_id": ROOM_ID},
    ),
    (NPC, {"id": NPC_ID, "name": "守卫", "room_id": ROOM_ID, "memory_ref": "mem_guard"}),
    (Stage, {"id": "stage_1", "description": "一步"}),
    (Reward, {"item_id": ITEM_ID, "qty": 1}),
    (Quest, {"id": QUEST_ID, "title": "任务", "stages": [_stage()]}),
    (RngState, {"save_seed": "save-42"}),
    (Proposal, {"turn_id": 1, "actor_id": "player_1", "tool": "move_to", "reason": "走"}),
    (Resolution, {"proposal_id": "p_000001_001", "verdict": "rejected"}),
    (ActionIntent, {"action": "move_to", "confidence": 0.5}),
    (EntityRef, {"kind": "character", "id": CHAR_ID, "name": "旅人"}),
    (PerceptionSlice, {"actor_id": "player_1"}),
    (ToolSpec, {"name": "move_to", "description": "移动", "json_schema": {"type": "object"}}),
    (GameState, _state_kwargs()),
]


@pytest.mark.parametrize(
    ("model_cls", "base_kwargs"),
    _EXTRA_FORBID_CASES,
    ids=[model_cls.__name__ for model_cls, _ in _EXTRA_FORBID_CASES],
)
def test_models_reject_extra_fields(
    model_cls: type[BaseModel], base_kwargs: dict[str, Any]
) -> None:
    """5.9：extra="forbid" 全开，未知字段必须显式报错，而不是静默吞掉。"""
    with pytest.raises(ValidationError, match="extra_forbidden"):
        model_cls(**base_kwargs, unexpected_field=1)


_FROZEN_CASES: list[tuple[BaseModel, str, Any]] = [
    (_draft_event(), "actor_id", "player_2"),
    (Result.success(), "ok", False),
    (Status(id="status_poisoned", name="中毒", duration_turns=1), "name", "剧毒"),
    (_character(), "name", "别人"),
    (_room(), "name", "别处"),
    (_item(), "name", "长剑"),
    (
        ScheduleSlot(from_world_minute=480, to_world_minute=1020, room_id=ROOM_ID),
        "to_world_minute",
        1021,
    ),
    (_npc(), "name", "路人"),
    (_stage(), "completed", True),
    (Reward(item_id=ITEM_ID, qty=1), "qty", 2),
    (_quest(), "title", "别的任务"),
    (RngState(save_seed="save-42"), "last_seq", 1),
    (
        Proposal(turn_id=1, actor_id="player_1", tool="move_to", reason="走"),
        "priority",
        1,
    ),
    (Resolution(proposal_id="p_000001_001", verdict="rejected"), "reason", "改"),
    (EntityRef(kind="character", id=CHAR_ID, name="旅人"), "name", "别人"),
    (PerceptionSlice(actor_id="player_1"), "actor_id", "player_2"),
    (
        ToolSpec(name="move_to", description="移动", json_schema={"type": "object"}),
        "description",
        "改",
    ),
    (GameState(**_state_kwargs()), "turn", 2),
]


@pytest.mark.parametrize(
    ("model", "field", "value"),
    _FROZEN_CASES,
    ids=[type(model).__name__ for model, _, _ in _FROZEN_CASES],
)
def test_frozen_models_reject_attribute_assignment(
    model: BaseModel, field: str, value: Any
) -> None:
    """5.1/5.9：不可变边界内的模型禁止原地改字段（变更只能经 GameState.apply）。"""
    with pytest.raises(ValidationError, match="frozen"):
        setattr(model, field, value)
