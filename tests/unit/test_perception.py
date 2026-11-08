"""单元测试：M3-010 角色私有感知切片（第 8 章 §8.2、第 5 章 §5.4/§5.5）。

覆盖：玩家 ``Character`` 与 ``NPC`` 两种 actor 的切片组装、结构性防泄露（其他房间
实体 / 他人背包物品 / quests / 全局事件一律不进入切片）、``private_memory`` 按
actor 隔离、``plan_hint`` 仅由调用方注入、序列化白名单（字段集合固定、可
``model_dump(mode="json")`` 且无 ``GameState``/仓储引用）、``local_events`` 过滤
（只含自己事件、按 ``seq`` 升序、上限 10），并覆盖失败分支（未知 actor、未盖章事件、
非法 limit、``plan_hint`` 超长）。

文档依据：第 8 章 §8.2「私有感知切片与私有记忆：结构性防泄露」构造规则 1–4；
第 5 章 §5.5 ``EntityRef``/``PerceptionSlice`` 字段定义；``.work/m2-contract.md``
§3.4 内置场景与 §4.2 ``QueryService`` 签名（M2 调用方向后兼容）。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from textgame.domain.events import Event
from textgame.domain.models import (
    NPC,
    Character,
    EntityRef,
    GameState,
    PerceptionSlice,
    Quest,
    Stage,
)
from textgame.domain.perception import LOCAL_EVENTS_LIMIT, filter_local_events
from textgame.domain.scenario import (
    ITEM_POTION,
    ITEM_POTION_NAME,
    ITEM_RELIC,
    ITEM_SWORD,
    ITEM_TORCH,
    ITEM_TORCH_NAME,
    NPC_SENTINEL,
    NPC_SENTINEL_NAME,
    PLAYER_ID,
    PLAYER_NAME,
    ROOM_CELLAR,
    ROOM_ENTRY,
    ROOM_ENTRY_NAME,
    build_initial_state,
)
from textgame.persistence.memory import InMemoryRepository
from textgame.services.query_service import QueryService

_TS = datetime(2026, 11, 8, 12, 0, 0, tzinfo=timezone.utc)

#: 测试专用固定 ULID（26 位，字符集与 ``domain/types.py`` 一致）。
_OTHER_CHAR = "char_01J9Z3K4Q7X8Y9Z0ABCDEFGHJW"
_OTHER_NPC = "npc_01J9Z3K4Q7X8Y9Z0ABCDEFGHJX"
_QUEST_ID = "quest_01J9Z3K4Q7X8Y9Z0ABCDEFGHJZ"
_UNKNOWN_ACTOR = "char_01J9Z3K4Q7X8Y9Z0ABCDEFGHJV"
_UNKNOWN_NPC = "npc_01J9Z3K4Q7X8Y9Z0ABCDEFGHJY"


# ---------------------------------------------------------------------------
# fixtures 与构造辅助
# ---------------------------------------------------------------------------
@pytest.fixture
def query() -> QueryService:
    """``build_perception_slice`` 不读仓储；``InMemoryRepository`` 只满足构造注入。"""
    return QueryService(InMemoryRepository())


@pytest.fixture
def state() -> GameState:
    """M2 内置单场景初始状态（契约 §3.4，固定 ULID，纯函数构造）。"""
    return build_initial_state(save_seed="save-42")


def _make_slice(
    query: QueryService,
    state: GameState,
    actor_id: str,
    **kwargs: Any,
) -> PerceptionSlice:
    """按契约 §4.2 签名构造切片；``turn_id``/``save_seed`` 用合法占位值。"""
    return query.build_perception_slice(
        state,
        actor_id,
        turn_id=state.turn + 1,
        save_seed="save-42",
        **kwargs,
    )


def _stamped(seq: int, actor_id: str = PLAYER_ID, *, method: str = "look") -> Event:
    """构造与 ``EventLog.stamp`` 同构的盖章事件（用例只关心 ``seq``/``actor_id``）。"""
    return Event(
        id=f"evt_{seq:06d}",
        turn_id=1,
        seq=seq,
        actor_id=actor_id,
        method=method,
        rng_seed=0,
        ts=_TS,
    )


def _draft(actor_id: str = PLAYER_ID, *, method: str = "look") -> Event:
    """构造草稿事件（无 ``seq``/``id``/``rng_seed``/``ts``）。"""
    return Event(turn_id=1, actor_id=actor_id, method=method)


def _refs(slice_: PerceptionSlice, kind: str) -> list[EntityRef]:
    """返回切片中指定 ``kind`` 的实体引用。"""
    return [entity for entity in slice_.visible_entities if entity.kind == kind]


def _keys(slice_: PerceptionSlice) -> set[tuple[str, str]]:
    """返回 ``(kind, id)`` 集合，便于整体断言可见性。"""
    return {(entity.kind, entity.id) for entity in slice_.visible_entities}


def _names(slice_: PerceptionSlice) -> dict[tuple[str, str], str]:
    """返回 ``(kind, id) -> name`` 映射，便于核对名称裁剪。"""
    return {(entity.kind, entity.id): entity.name for entity in slice_.visible_entities}


def _mutate(state: GameState, **updates: Any) -> GameState:
    """返回 frozen 状态的测试副本（``model_copy`` 不重跑引用完整性校验）。"""
    return state.model_copy(update=updates)


def _other_character(*, inventory: list[str], location: str = ROOM_ENTRY) -> Character:
    """构造第二个角色（用于“同房间其他角色 / 他人背包”用例）。"""
    return Character(
        id=_OTHER_CHAR,
        name="同行者",
        attributes={"str": 10, "max_hp": 10},
        inventory=inventory,
        location=location,
        hp=10,
        created_turn=1,
    )


def _quest() -> Quest:
    """构造一个 quest（用于证明 quests 不进入感知切片）。"""
    return Quest(
        id=_QUEST_ID,
        title="哨站的秘密",
        stages=[Stage(id="stage_1", description="找到古物")],
    )


def _assert_json_primitives_only(value: Any) -> None:
    """递归断言切片 dump 只含 JSON 基本类型（不存在 ``GameState``/仓储引用）。"""
    if isinstance(value, dict):
        for key, item in value.items():
            assert isinstance(key, str)
            _assert_json_primitives_only(item)
        return
    if isinstance(value, list):
        for item in value:
            _assert_json_primitives_only(item)
        return
    assert value is None or isinstance(value, (str, int, float, bool)), (
        f"perception slice leaked non-JSON value: {type(value)!r}"
    )


# ---------------------------------------------------------------------------
# 玩家切片：只含当前房间可见实体 + 自己背包物品
# ---------------------------------------------------------------------------
def test_player_slice_contains_only_current_room_entities(
    query: QueryService, state: GameState
) -> None:
    """第 8 章 §8.2 规则 1：玩家切片 = 当前房间 + 房内物品/NPC + 同房间其他角色。"""
    slice_ = _make_slice(query, state, PLAYER_ID)

    assert slice_.actor_id == PLAYER_ID
    assert _keys(slice_) == {
        ("room", ROOM_ENTRY),
        ("item", ITEM_TORCH),
        ("item", ITEM_POTION),
        ("npc", NPC_SENTINEL),
    }
    names = _names(slice_)
    assert names[("room", ROOM_ENTRY)] == ROOM_ENTRY_NAME
    assert names[("item", ITEM_TORCH)] == ITEM_TORCH_NAME
    assert names[("item", ITEM_POTION)] == ITEM_POTION_NAME
    assert names[("npc", NPC_SENTINEL)] == NPC_SENTINEL_NAME
    # 玩家自己不是“同房间其他 characters”，不出现在 character 引用里。
    assert _refs(slice_, "character") == []


def test_player_slice_hides_other_room_entities(query: QueryService, state: GameState) -> None:
    """其他房间的 room/items/NPC 不得出现（结构性防泄露，第 8 章 §8.2）。"""
    keys = _keys(_make_slice(query, state, PLAYER_ID))

    assert ("room", ROOM_CELLAR) not in keys
    assert ("item", ITEM_SWORD) not in keys
    assert ("item", ITEM_RELIC) not in keys


def test_actor_own_inventory_items_are_visible(query: QueryService, state: GameState) -> None:
    """actor 自己背包内的物品可见（供“使用/喝”类解析匹配，契约 §3.5）。"""
    player = state.characters[PLAYER_ID].model_copy(update={"inventory": [ITEM_SWORD]})
    modified = _mutate(state, characters={PLAYER_ID: player})

    assert ("item", ITEM_SWORD) in _keys(_make_slice(query, modified, PLAYER_ID))


def test_same_room_character_visible_but_their_inventory_is_not(
    query: QueryService, state: GameState
) -> None:
    """同房间其他角色只暴露 ``(id, name)``；其背包物品绝不泄露（玩家与 NPC 同规则）。"""
    modified = _mutate(
        state,
        characters={
            PLAYER_ID: state.characters[PLAYER_ID],
            _OTHER_CHAR: _other_character(inventory=[ITEM_SWORD]),
        },
    )

    player_slice = _make_slice(query, modified, PLAYER_ID)
    assert ("character", _OTHER_CHAR) in _keys(player_slice)
    assert _names(player_slice)[("character", _OTHER_CHAR)] == "同行者"
    assert ("item", ITEM_SWORD) not in _keys(player_slice)

    npc_slice = _make_slice(query, modified, NPC_SENTINEL)
    assert ("character", _OTHER_CHAR) in _keys(npc_slice)
    assert ("item", ITEM_SWORD) not in _keys(npc_slice)


def test_other_room_character_and_npc_not_visible(query: QueryService, state: GameState) -> None:
    """其他房间的 character/NPC 不得进入当前切片（含不在房间列表里的对象）。"""
    far_npc = NPC(
        id=_OTHER_NPC,
        name="地窖守卫",
        room_id=ROOM_CELLAR,
        memory_ref="npc_cellar_guard",
    )
    modified = _mutate(
        state,
        characters={
            PLAYER_ID: state.characters[PLAYER_ID],
            _OTHER_CHAR: _other_character(inventory=[], location=ROOM_CELLAR),
        },
        npcs={NPC_SENTINEL: state.npcs[NPC_SENTINEL], _OTHER_NPC: far_npc},
    )

    for actor_id in (PLAYER_ID, NPC_SENTINEL):
        keys = _keys(_make_slice(query, modified, actor_id))
        assert ("character", _OTHER_CHAR) not in keys
        assert ("npc", _OTHER_NPC) not in keys


def test_quest_entities_never_appear(query: QueryService, state: GameState) -> None:
    """quests 是全局状态、不属于房间感知范围：任何 actor 的切片都不得出现 quest 引用。"""
    modified = _mutate(state, quests={_QUEST_ID: _quest()})

    for actor_id in (PLAYER_ID, NPC_SENTINEL):
        assert _refs(_make_slice(query, modified, actor_id), "quest") == []


def test_visible_entities_have_no_duplicates(query: QueryService, state: GameState) -> None:
    """实体去重（例如房间物品同时出现在背包时只保留一份）。"""
    player = state.characters[PLAYER_ID].model_copy(update={"inventory": [ITEM_TORCH]})
    modified = _mutate(state, characters={PLAYER_ID: player})

    entities = _make_slice(query, modified, PLAYER_ID).visible_entities
    assert len(entities) == len({(entity.kind, entity.id) for entity in entities})


# ---------------------------------------------------------------------------
# NPC 切片：与玩家同一规则，但房间取 NPC.room_id
# ---------------------------------------------------------------------------
def test_npc_slice_uses_npc_room_and_sees_room_entities(
    query: QueryService, state: GameState
) -> None:
    """NPC actor：room = npc.room_id；可见房内物品/其他角色，自身不作为实体返回。"""
    slice_ = _make_slice(query, state, NPC_SENTINEL)

    assert slice_.actor_id == NPC_SENTINEL
    assert _keys(slice_) == {
        ("room", ROOM_ENTRY),
        ("item", ITEM_TORCH),
        ("item", ITEM_POTION),
        ("character", PLAYER_ID),
    }
    assert _names(slice_)[("character", PLAYER_ID)] == PLAYER_NAME


def test_actor_self_reference_is_never_visible(query: QueryService, state: GameState) -> None:
    """可见实体只描述“其他”实体：NPC 不含自身 npc 引用、玩家不含自身 character 引用。

    Lead 010 裁定：actor 自身即使出现在 ``room.npc_ids`` 或同房间扫描中也要排除。
    """
    npc_slice = _make_slice(query, state, NPC_SENTINEL)
    assert ("npc", NPC_SENTINEL) not in _keys(npc_slice)

    player_slice = _make_slice(query, state, PLAYER_ID)
    assert ("character", PLAYER_ID) not in _keys(player_slice)
    for actor_id, slice_ in ((NPC_SENTINEL, npc_slice), (PLAYER_ID, player_slice)):
        assert all(entity_id != actor_id for _, entity_id in _keys(slice_))


def test_npc_slice_follows_npc_room_id(query: QueryService, state: GameState) -> None:
    """NPC 移动到地窖后，切片跟随 ``npc.room_id`` 而不是任何角色的 location。"""
    sentinel = state.npcs[NPC_SENTINEL].model_copy(update={"room_id": ROOM_CELLAR})
    modified = _mutate(state, npcs={NPC_SENTINEL: sentinel})

    slice_ = _make_slice(query, modified, NPC_SENTINEL)
    keys = _keys(slice_)
    assert ("room", ROOM_CELLAR) in keys
    assert ("item", ITEM_SWORD) in keys
    assert ("item", ITEM_RELIC) in keys
    assert ("room", ROOM_ENTRY) not in keys
    assert ("item", ITEM_TORCH) not in keys
    assert ("character", PLAYER_ID) not in keys


# ---------------------------------------------------------------------------
# private_memory：按 actor 隔离
# ---------------------------------------------------------------------------
def test_private_memory_is_isolated_per_actor(query: QueryService, state: GameState) -> None:
    """角色取 ``private_memory_ref``、NPC 取 ``memory_ref``；互不可见（第 8 章 §8.2 规则 3）。"""
    player = state.characters[PLAYER_ID].model_copy(
        update={"private_memory_ref": "mem_player_torch"}
    )
    modified = _mutate(state, characters={PLAYER_ID: player})

    player_slice = _make_slice(query, modified, PLAYER_ID)
    npc_slice = _make_slice(query, modified, NPC_SENTINEL)

    assert player_slice.private_memory == ["mem_player_torch"]
    assert npc_slice.private_memory == ["npc_sentinel"]
    assert "npc_sentinel" not in player_slice.private_memory
    assert "mem_player_torch" not in npc_slice.private_memory


def test_character_without_memory_ref_has_empty_private_memory(
    query: QueryService, state: GameState
) -> None:
    """``private_memory_ref`` 缺省的角色（内置玩家）私有记忆为空，不得回退到他人记忆。"""
    assert _make_slice(query, state, PLAYER_ID).private_memory == []


# ---------------------------------------------------------------------------
# plan_hint：只由调用方注入（第 8 章 §8.2 规则 4）
# ---------------------------------------------------------------------------
def test_plan_hint_is_injected_from_caller(query: QueryService, state: GameState) -> None:
    hint = "先去地窖找古物，再回来问老哨兵"
    slice_ = _make_slice(query, state, NPC_SENTINEL, plan_hint=hint)

    assert slice_.plan_hint == hint


def test_plan_hint_defaults_to_none_and_is_not_derived_from_state(
    query: QueryService, state: GameState
) -> None:
    """``plan_hint`` 不得从 state（如 NPC dialogue_state）或其他 Agent 输出推导。"""
    sentinel = state.npcs[NPC_SENTINEL].model_copy(update={"dialogue_state": "greeted"})
    modified = _mutate(state, npcs={NPC_SENTINEL: sentinel})

    assert _make_slice(query, modified, NPC_SENTINEL).plan_hint is None


def test_plan_hint_over_limit_is_rejected(query: QueryService, state: GameState) -> None:
    """``plan_hint`` 受模型上限约束（第 5 章 §5.5：max_length=200）。"""
    with pytest.raises(ValidationError):
        _make_slice(query, state, PLAYER_ID, plan_hint="x" * 201)


# ---------------------------------------------------------------------------
# 序列化白名单：字段集合固定、可 JSON 序列化、不含 GameState/仓储引用
# ---------------------------------------------------------------------------
def test_perception_slice_field_whitelist_is_fixed() -> None:
    """第 5 章 §5.5 定稿字段集合固定，禁止夹带 GameState/仓储/新增派生字段。"""
    assert set(PerceptionSlice.model_fields) == {
        "actor_id",
        "visible_entities",
        "local_events",
        "private_memory",
        "plan_hint",
    }
    assert set(EntityRef.model_fields) == {"kind", "id", "name"}


def test_slice_is_json_serializable_and_round_trips(query: QueryService, state: GameState) -> None:
    """第 8 章 §8.2 规则 5：切片交付前已 JSON 序列化，且不含非 JSON 对象。"""
    slice_ = _make_slice(
        query,
        state,
        PLAYER_ID,
        plan_hint="检查火把",
        events=[_stamped(1)],
    )

    dumped = slice_.model_dump(mode="json")
    encoded = json.dumps(dumped, ensure_ascii=False)
    assert json.loads(encoded) == dumped
    assert set(dumped) == set(PerceptionSlice.model_fields)
    assert all(set(ref) == set(EntityRef.model_fields) for ref in dumped["visible_entities"])
    assert PerceptionSlice.model_validate(dumped) == slice_


def test_slice_contains_no_gamestate_or_repository_reference(
    query: QueryService, state: GameState
) -> None:
    """递归断言 dump 只含 JSON 基本类型：不存在 GameState/ORM 对象/仓储句柄。"""
    dumped = _make_slice(query, state, PLAYER_ID).model_dump(mode="json")
    _assert_json_primitives_only(dumped)
    # 切片字段带出的只有 actor/实体引用/事件数据，不存在全局状态字段。
    assert "characters" not in dumped
    assert "rooms" not in dumped


# ---------------------------------------------------------------------------
# local_events：只含自己事件、seq 升序、上限 10（第 8 章 §8.2 规则 2）
# ---------------------------------------------------------------------------
def test_local_events_include_only_actor_own_events(query: QueryService, state: GameState) -> None:
    """只返回 ``event.actor_id == actor_id`` 的事件，他人事件/系统事件不可见。"""
    events = [
        _stamped(1, PLAYER_ID),
        _stamped(2, NPC_SENTINEL),
        _stamped(3, PLAYER_ID),
        _stamped(4, "system"),
    ]

    player_slice = _make_slice(query, state, PLAYER_ID, events=events)
    assert [event.seq for event in player_slice.local_events] == [1, 3]
    assert {event.actor_id for event in player_slice.local_events} == {PLAYER_ID}

    npc_slice = _make_slice(query, state, NPC_SENTINEL, events=events)
    assert [event.seq for event in npc_slice.local_events] == [2]
    assert {event.actor_id for event in npc_slice.local_events} == {NPC_SENTINEL}


def test_local_events_exclude_other_actors_entirely(query: QueryService, state: GameState) -> None:
    """他人事件与全局流水不得伪装成局部事件返回。"""
    events = [_stamped(1, NPC_SENTINEL), _stamped(2, "system")]
    assert _make_slice(query, state, PLAYER_ID, events=events).local_events == []


def test_local_events_sorted_by_seq_regardless_of_input_order(
    query: QueryService, state: GameState
) -> None:
    """``local_events`` 按 ``seq`` 升序（第 14 章 §14.2 全局单调序号即时间序）。"""
    events = [_stamped(5), _stamped(2), _stamped(9)]
    slice_ = _make_slice(query, state, PLAYER_ID, events=events)

    assert [event.seq for event in slice_.local_events] == [2, 5, 9]


def test_local_events_keep_only_latest_ten(query: QueryService, state: GameState) -> None:
    """上限 10 条：12 条自己事件 + 1 条他人事件 → 只留最近的 seq 3..12。"""
    events = [_stamped(seq) for seq in range(1, 13)] + [_stamped(13, NPC_SENTINEL)]
    slice_ = _make_slice(query, state, PLAYER_ID, events=events)

    assert LOCAL_EVENTS_LIMIT == 10
    assert [event.seq for event in slice_.local_events] == list(range(3, 13))
    assert len(slice_.local_events) == LOCAL_EVENTS_LIMIT


def test_local_events_default_empty_for_m2_call_compatibility(
    query: QueryService, state: GameState
) -> None:
    """M2 调用方不传 ``events``（默认 ``()``）时 ``local_events`` 为空。"""
    assert _make_slice(query, state, PLAYER_ID).local_events == []


def test_m2_call_signature_remains_compatible(query: QueryService, state: GameState) -> None:
    """契约 §4.2 旧四参调用（state, actor_id, turn_id=, save_seed=）保持可用。"""
    slice_ = query.build_perception_slice(state, PLAYER_ID, turn_id=2, save_seed="save-42")

    assert slice_.actor_id == PLAYER_ID
    assert slice_.visible_entities
    assert slice_.local_events == []
    assert slice_.plan_hint is None


# ---------------------------------------------------------------------------
# 失败分支
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("unknown_id", [_UNKNOWN_ACTOR, _UNKNOWN_NPC, ""])
def test_unknown_actor_raises_value_error(
    query: QueryService, state: GameState, unknown_id: str
) -> None:
    """角色/NPC 两套 ID 都不命中 → ``ValueError``（契约 §4.2：内部调用方保证 actor 存在）。"""
    with pytest.raises(ValueError, match="not present in state"):
        query.build_perception_slice(state, unknown_id, turn_id=2, save_seed="save-42")


def test_local_events_reject_unstamped_events(query: QueryService, state: GameState) -> None:
    """未盖章事件没有 ``seq``、无法代表已提交事实，不得进入 ``local_events``。"""
    with pytest.raises(ValueError, match="stamped events"):
        _make_slice(query, state, PLAYER_ID, events=[_draft()])


def test_draft_event_from_other_actor_is_ignored(query: QueryService, state: GameState) -> None:
    """过滤先于排序：他人草稿事件不进入本 actor 切片，也不触发排序失败。"""
    assert _make_slice(query, state, PLAYER_ID, events=[_draft(NPC_SENTINEL)]).local_events == []


# ---------------------------------------------------------------------------
# 领域纯函数 filter_local_events（切片 010 要求 4：纯函数落在 domain/perception.py）
# ---------------------------------------------------------------------------
def test_filter_local_events_respects_custom_limit() -> None:
    """``limit`` 参数可显式收窄（默认 10 由 ``LOCAL_EVENTS_LIMIT`` 提供）。"""
    events = [_stamped(seq) for seq in range(1, 6)]

    assert [event.seq for event in filter_local_events(events, PLAYER_ID, limit=2)] == [4, 5]


@pytest.mark.parametrize("limit", [0, -1])
def test_filter_local_events_rejects_non_positive_limit(limit: int) -> None:
    """``limit <= 0`` 是调用方错误：抛 ``ValueError`` 而不是静默返回空列表。"""
    with pytest.raises(ValueError, match="limit must be positive"):
        filter_local_events([], PLAYER_ID, limit=limit)


def test_filter_local_events_rejects_empty_actor_id() -> None:
    """空 ``actor_id`` 无法界定“自己的事件”，直接拒绝。"""
    with pytest.raises(ValueError, match="non-empty"):
        filter_local_events([], "")
