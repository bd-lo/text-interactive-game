"""M3 ``ActorDirectory`` 单元测试：服务端标签/调用来源推导 (第 7 章 §7.3、第 8 章 §8.2)。"""

from __future__ import annotations

from textgame.domain.models import NPC
from textgame.domain.scenario import (
    M3_NONPLAYER_TAGS,
    M3_NPC_SCAVENGER_A,
    M3_NPC_SCAVENGER_B,
    NPC_SENTINEL,
    PLAYER_ID,
    build_initial_state,
    build_m3_state,
)
from textgame.services.actor_directory import ActorDirectory


def _m3_directory() -> ActorDirectory:
    """按组合根口径构造 M3 ActorDirectory（静态细标签映射来自场景模块）。"""
    return ActorDirectory(nonplayer_tags=M3_NONPLAYER_TAGS)


def test_player_tags_and_caller() -> None:
    directory = ActorDirectory()
    state = build_initial_state(save_seed="save-1")
    assert directory.tags_of(state, PLAYER_ID) == frozenset({"player"})
    assert directory.caller_of(state, PLAYER_ID) == "player"
    assert directory.exists(state, PLAYER_ID) is True


def test_default_npc_entity_tags() -> None:
    directory = ActorDirectory()
    state = build_initial_state(save_seed="save-1")
    assert directory.tags_of(state, NPC_SENTINEL) == frozenset({"npc"})
    assert directory.caller_of(state, NPC_SENTINEL) == "npc_agent"
    assert directory.exists(state, NPC_SENTINEL) is True


def test_npc_faction_tag_is_server_derived() -> None:
    directory = ActorDirectory()
    state = build_initial_state(save_seed="save-1")
    sentinel = state.npcs[NPC_SENTINEL]
    faction_npc = NPC(
        id="npc_01J9Z3K4Q7X8Y9Z0ABCDEFGHJX",
        name="铁匠",
        room_id=sentinel.room_id,
        faction="blacksmith",
        memory_ref="npc_blacksmith",
    )
    state = state.model_copy(update={"npcs": {**state.npcs, faction_npc.id: faction_npc}})
    assert directory.tags_of(state, faction_npc.id) == frozenset({"npc", "npc:blacksmith"})


def test_nonplayer_character_tags_from_static_mapping() -> None:
    directory = _m3_directory()
    state = build_m3_state(save_seed="save-1")
    for actor_id in (M3_NPC_SCAVENGER_A, M3_NPC_SCAVENGER_B):
        assert directory.tags_of(state, actor_id) == frozenset({"npc", "npc:scavenger"})
        assert directory.caller_of(state, actor_id) == "npc_agent"
        assert directory.exists(state, actor_id) is True


def test_nonplayer_character_without_mapping_defaults_to_npc() -> None:
    directory = ActorDirectory()
    state = build_m3_state(save_seed="save-1")
    assert directory.tags_of(state, M3_NPC_SCAVENGER_A) == frozenset({"npc"})


def test_unknown_actor_has_no_tags_and_resolver_caller() -> None:
    directory = _m3_directory()
    state = build_m3_state(save_seed="save-1")
    unknown = "char_01J9Z3K4Q7X8Y9Z0ABCDEFGHJZ"
    assert directory.exists(state, unknown) is False
    assert directory.tags_of(state, unknown) == frozenset()
    assert directory.caller_of(state, unknown) == "resolver"


def test_tags_are_frozensets_and_not_shared() -> None:
    directory = _m3_directory()
    state = build_m3_state(save_seed="save-1")
    tags = directory.tags_of(state, M3_NPC_SCAVENGER_A)
    assert isinstance(tags, frozenset)
    assert "npc:scavenger" in tags
    # 修改返回集合不应影响后续推导（frozenset 天然不可变，这里验证新集合可安全比较）。
    assert directory.tags_of(state, M3_NPC_SCAVENGER_A) == tags
