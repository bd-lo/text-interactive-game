"""单元测试：剧情场景 ``scenario="story"``「余烬哨站」（`.work/.../spec-scenario-story.md`）。

覆盖规格 §5 的 11 项要求：builder 合法性与确定性、ID 正则与跨场景无交集、出口双向可达、
``is_story_state`` 判别、目标三阶段读时求值、胜利/失败结局与优先级、NPC 对话三态、
纯函数可重复，以及本场景面向玩家的文案自检（字数/无内部 ID/无英文技术词）。

全部用例只读纯函数与不可变 ``GameState``：不建库、不联网、不写文件、不使用随机/时钟。
"""

from __future__ import annotations

import re

from textgame.domain.crafting import CraftMode, CraftRequest, build_craft_state_slice
from textgame.domain.hashing import state_hash
from textgame.domain.models import GameState, Item
from textgame.domain.scenario import (
    PLAYER_ID,
    build_initial_state,
    build_m3_state,
    build_m4_state,
)
from textgame.domain.scenario_story import (
    STORY_ENTRY_ROOM_ID,
    STORY_FLINT_ID,
    STORY_SCENARIO,
    STORY_SIGNAL_TORCH_TEMPLATE,
    STORY_TITLE,
    STORY_TOWER_ROOM_ID,
    STORY_WATCHMAN_ID,
    build_story_state,
    is_story_state,
    story_ending,
    story_goal,
    story_reply,
)
from textgame.persistence.memory import InMemoryRepository
from textgame.services.craft_service import CraftService
from textgame.services.query_service import QueryService
from textgame.services.recipe_catalog import RecipeCatalog
from textgame.services.tool_context import ToolContext

_SAVE_SEED = "save-42"
_ID_PREFIX = "01J9Z3K4Q7X8Y9Z0ABCDEFGHK"
_SIGNAL_TORCH_ID = f"item_{_ID_PREFIX}T"
_ULID = r"[0-9A-HJKMNP-TV-Z]{26}"
_ROOM_ID_RE = re.compile(rf"^room_{_ULID}$")
_ITEM_ID_RE = re.compile(rf"^item_{_ULID}$")
_NPC_ID_RE = re.compile(rf"^npc_{_ULID}$")
_INTERNAL_ID_RE = re.compile(rf"(?:room|item|npc|char|game)_(?:{_ULID}|[0-9a-f]{{8}})")
_ASCII_LETTER_RE = re.compile(r"[A-Za-z]")
_OPPOSITE = {
    "north": "south",
    "south": "north",
    "east": "west",
    "west": "east",
    "up": "down",
    "down": "up",
}


# ---------------------------------------------------------------------------
# 状态构造辅助（只用不可变 model_copy，不产生 IO/随机）
# ---------------------------------------------------------------------------


def _state() -> GameState:
    """构造初始 story 状态。"""
    return build_story_state(save_seed=_SAVE_SEED)


def _replace_player(state: GameState, **updates: object) -> GameState:
    """替换玩家角色的若干字段（``model_copy`` 不触发校验，测试构造用）。"""
    player = state.characters[PLAYER_ID]
    updated = player.model_copy(update=dict(updates))
    return state.model_copy(update={"characters": {**state.characters, updated.id: updated}})


def _with_inventory(state: GameState, *item_ids: str) -> GameState:
    """在玩家行囊末尾追加物品 id（物品定义须已存在于 ``state.items``）。"""
    player = state.characters[PLAYER_ID]
    return _replace_player(state, inventory=[*player.inventory, *item_ids])


def _with_signal_torch(state: GameState) -> GameState:
    """把一支已制造的信号火把放进玩家行囊（``tags[0]`` 为模板键，模拟制造产物）。"""
    torch = Item(
        id=_SIGNAL_TORCH_ID,
        name="信号火把",
        description="用铜线缠过的火把，点燃时火星更亮，也更容易被远处看见。",
        tags=[STORY_SIGNAL_TORCH_TEMPLATE, "light", "crafted"],
    )
    items = {**state.items, torch.id: torch}
    return _with_inventory(state.model_copy(update={"items": items}), torch.id)


def _with_dialogue(state: GameState, dialogue_state: str) -> GameState:
    """把老兵 ``dialogue_state`` 置为指定值。"""
    watchman = state.npcs[STORY_WATCHMAN_ID].model_copy(update={"dialogue_state": dialogue_state})
    return state.model_copy(update={"npcs": {**state.npcs, watchman.id: watchman}})


def _victory_state(*, hp: int = 20) -> GameState:
    """构造胜利态：人在瞭望塔 + 行囊有信号火把与火石。"""
    state = _with_signal_torch(_state())
    state = _with_inventory(state, STORY_FLINT_ID)
    return _replace_player(state, location=STORY_TOWER_ROOM_ID, hp=hp)


# ---------------------------------------------------------------------------
# 1) builder：合法、确定、不污染既有场景
# ---------------------------------------------------------------------------


def test_build_story_state_is_valid_and_deterministic() -> None:
    first = _state()
    second = _state()

    assert first == second
    assert state_hash(first) == state_hash(second)
    assert first.turn == 1
    assert first.world_time == 0
    assert first.rng_state.save_seed == _SAVE_SEED
    assert first.rng_state.last_seq == 0
    assert first.quests == {}
    assert set(first.characters) == {PLAYER_ID}


def test_build_story_state_player_starts_on_bridge() -> None:
    state = _state()
    assert PLAYER_ID in state.characters
    player = state.characters[PLAYER_ID]

    assert player.name == "旅人"
    assert player.location == STORY_ENTRY_ROOM_ID
    assert player.inventory == []
    assert player.hp == 12
    assert player.attributes["max_hp"] == 20
    # 开局不满血：治疗药水 heal=8 才能在玩家侧可观测（满血用药水=假道具）。
    assert player.hp < player.attributes["max_hp"]
    assert player.created_turn == 1


def test_build_story_state_does_not_touch_m2_initial_state() -> None:
    before = build_initial_state(save_seed=_SAVE_SEED).model_dump(mode="json")

    build_story_state(save_seed=_SAVE_SEED)

    after = build_initial_state(save_seed=_SAVE_SEED).model_dump(mode="json")
    assert after == before


# ---------------------------------------------------------------------------
# 2) 固定 ID：正则合法 + 与 m2/m3/m4 的房间/物品/NPC 无交集
# ---------------------------------------------------------------------------


def test_story_ids_match_domain_patterns() -> None:
    state = _state()

    assert set(state.rooms) and all(_ROOM_ID_RE.fullmatch(room_id) for room_id in state.rooms)
    assert set(state.items) and all(_ITEM_ID_RE.fullmatch(item_id) for item_id in state.items)
    assert set(state.npcs) and all(_NPC_ID_RE.fullmatch(npc_id) for npc_id in state.npcs)
    assert STORY_WATCHMAN_ID in state.npcs
    assert STORY_FLINT_ID in state.items
    assert STORY_TOWER_ROOM_ID in state.rooms
    assert STORY_SCENARIO == "story"
    assert STORY_TITLE == "余烬哨站"


def test_story_ids_are_disjoint_from_m2_m3_m4() -> None:
    story = _state()
    others = (
        build_initial_state(save_seed=_SAVE_SEED),
        build_m3_state(save_seed=_SAVE_SEED),
        build_m4_state(save_seed=_SAVE_SEED),
    )
    other_rooms = {room_id for other in others for room_id in other.rooms}
    other_items = {item_id for other in others for item_id in other.items}
    other_npcs = {npc_id for other in others for npc_id in other.npcs}

    assert not set(story.rooms) & other_rooms
    assert not set(story.items) & other_items
    assert not set(story.npcs) & other_npcs


# ---------------------------------------------------------------------------
# 3) 拓扑与内容结构（规格 §2）
# ---------------------------------------------------------------------------


def test_room_topology_and_contents_match_spec() -> None:
    state = _state()

    assert len(state.rooms) == 6
    bridge = state.rooms[STORY_ENTRY_ROOM_ID]
    hub = state.rooms[bridge.exits["east"]]
    assert bridge.item_ids == [] and bridge.npc_ids == []

    assert set(hub.exits) == {"west", "north", "east", "up", "south"}
    assert hub.npc_ids == [STORY_WATCHMAN_ID]
    assert state.npcs[STORY_WATCHMAN_ID].room_id == hub.id
    assert state.npcs[STORY_WATCHMAN_ID].dialogue_state == "idle"
    assert state.npcs[STORY_WATCHMAN_ID].faction == "哨站"
    assert state.npcs[STORY_WATCHMAN_ID].memory_ref == "npc_story_watchman"
    assert len(hub.item_ids) == 1

    forge = state.rooms[hub.exits["east"]]
    assert "station.forge" in forge.ambient_tags
    assert len(forge.item_ids) == 3

    cellar = state.rooms[hub.exits["north"]]
    assert STORY_FLINT_ID in cellar.item_ids
    assert len(cellar.item_ids) == 2

    tower = state.rooms[hub.exits["up"]]
    assert tower.id == STORY_TOWER_ROOM_ID
    assert tower.item_ids == [] and tower.npc_ids == []

    well = state.rooms[hub.exits["south"]]
    assert len(well.item_ids) == 1


def test_all_exits_are_bidirectionally_reachable() -> None:
    state = _state()

    for room in state.rooms.values():
        for direction, target_id in room.exits.items():
            back = _OPPOSITE[direction]
            assert state.rooms[target_id].exits.get(back) == room.id, (room.id, direction)


def test_item_catalog_matches_spec() -> None:
    state = _state()
    items = list(state.items.values())
    by_name = {item.name: item for item in items}

    assert {"火把", "火石", "治疗药水", "铜线", "锻锤", "结霜的烈酒"} <= set(by_name)
    # R-01：tags[0] 统一为模板键，语义标签后置（否则背包合并/制造聚合会出错）。
    assert by_name["火把"].tags == ["item.torch", "quest", "light"]
    assert by_name["火石"].tags == ["item.flint", "quest", "material"]
    assert by_name["治疗药水"].tags == ["item.potion", "consumable"]
    assert by_name["治疗药水"].stats == {"heal": 8}
    assert by_name["结霜的烈酒"].tags == ["item.liquor", "consumable"]
    assert by_name["结霜的烈酒"].stats == {"hp_delta": -4}
    assert by_name["锻锤"].tags == ["item.forge_hammer", "tool"]

    copper = [item for item in items if item.name == "铜线"]
    assert len(copper) == 2
    assert all(item.tags[0] == "item.copper_wire" for item in copper)
    assert len({item.id for item in copper}) == 2


# ---------------------------------------------------------------------------
# 4) 文案自检（规格 §4）
# ---------------------------------------------------------------------------


def test_player_facing_copy_lengths_and_hooks() -> None:
    state = _state()

    for room in state.rooms.values():
        assert 50 <= len(room.description) <= 110, (room.name, len(room.description))
        assert not _ASCII_LETTER_RE.search(room.description), room.name
    hub = state.rooms[state.rooms[STORY_ENTRY_ROOM_ID].exits["east"]]
    assert "老兵" in hub.description
    assert "地窖" in hub.description and "铁匠铺" in hub.description
    bridge = state.rooms[STORY_ENTRY_ROOM_ID]
    assert "灯" in bridge.description
    # 首屏交代玩家「雪夜赶路、旧伤在身」，让开局 HP 12/20 有叙事解释（而非数字裸奔）。
    assert "旧伤" in bridge.description

    goal = story_goal(state)
    assert goal is not None
    assert goal["title"] == "让信号灯亮起来"
    assert 40 <= len(goal["description"]) <= 80
    assert not _ASCII_LETTER_RE.search(goal["description"])


def test_ending_and_reply_copy_lengths_and_no_internal_ids() -> None:
    victory = story_ending(_victory_state())
    fallen = story_ending(_replace_player(_state(), hp=0))
    briefed = story_reply(STORY_WATCHMAN_ID, "信号灯怎么了")
    talked = story_reply(STORY_WATCHMAN_ID, "你叫什么名字")

    assert victory is not None and fallen is not None
    assert briefed is not None and talked is not None
    assert len(victory["text"]) >= 80
    assert len(fallen["text"]) >= 60
    assert len(briefed["reply"]) >= 40
    assert "火把" in briefed["reply"] and "铜线" in briefed["reply"]
    assert "火石" in briefed["reply"] and "瞭望塔" in briefed["reply"]
    assert talked["reply"] != ""

    for text in (
        victory["text"],
        fallen["text"],
        victory["title"],
        fallen["title"],
        briefed["reply"],
        talked["reply"],
    ):
        assert not _ASCII_LETTER_RE.search(text), text
        assert not _INTERNAL_ID_RE.search(text), text


# ---------------------------------------------------------------------------
# 5) 目标读时求值（规格 §3）
# ---------------------------------------------------------------------------


def test_initial_goal_has_three_pending_objectives() -> None:
    state = _state()
    goal = story_goal(state)

    assert goal is not None
    objectives = goal["objectives"]
    assert [objective["id"] for objective in objectives] == ["listen", "craft", "light"]
    assert all(objective["done"] is False for objective in objectives)
    assert story_ending(state) is None


def test_briefed_dialogue_completes_listen_objective() -> None:
    goal = story_goal(_with_dialogue(_state(), "briefed"))

    assert goal is not None
    done = {objective["id"]: objective["done"] for objective in goal["objectives"]}
    assert done == {"listen": True, "craft": False, "light": False}


def test_crafted_signal_torch_completes_craft_objective() -> None:
    goal = story_goal(_with_signal_torch(_state()))

    assert goal is not None
    done = {objective["id"]: objective["done"] for objective in goal["objectives"]}
    assert done == {"listen": False, "craft": True, "light": False}


def test_non_story_states_have_no_goal_or_ending() -> None:
    for other in (
        build_initial_state(save_seed=_SAVE_SEED),
        build_m3_state(save_seed=_SAVE_SEED),
        build_m4_state(save_seed=_SAVE_SEED),
    ):
        assert story_goal(other) is None
        assert story_ending(other) is None


# ---------------------------------------------------------------------------
# 6) 结局判定（规格 §3/§5.8/§5.9）
# ---------------------------------------------------------------------------


def test_victory_ending_requires_tower_torch_and_flint() -> None:
    victory = story_ending(_victory_state())

    assert victory is not None
    assert victory["ending_id"] == "ending_lantern"
    assert victory["title"]
    assert "灯" in victory["text"]

    tower_only = _replace_player(_state(), location=STORY_TOWER_ROOM_ID)
    torch_only = _with_signal_torch(_state())
    flint_only = _with_inventory(_state(), STORY_FLINT_ID)
    assert story_ending(tower_only) is None
    assert story_ending(torch_only) is None
    assert story_ending(flint_only) is None


def test_fallen_ending_when_hp_reaches_zero() -> None:
    fallen = story_ending(_replace_player(_state(), hp=0))

    assert fallen is not None
    assert fallen["ending_id"] == "ending_fallen"
    assert fallen["title"] == "你倒下了"
    assert story_ending(_replace_player(_state(), hp=1)) is None


def test_victory_takes_priority_over_fallen() -> None:
    ending = story_ending(_victory_state(hp=0))

    assert ending is not None
    assert ending["ending_id"] == "ending_lantern"
    goal = story_goal(_victory_state(hp=0))
    assert goal is not None
    assert goal["objectives"][2]["done"] is True


# ---------------------------------------------------------------------------
# 7) NPC 对话三态 + 纯函数可重复
# ---------------------------------------------------------------------------


def test_story_reply_three_states() -> None:
    brief = story_reply(STORY_WATCHMAN_ID, "信号灯怎么不亮了")
    smalltalk = story_reply(STORY_WATCHMAN_ID, "这雪要下到什么时候")
    unknown = story_reply("npc_01J9Z3K4Q7X8Y9Z0ABCDEFGHJZ", "信号")

    assert brief is not None
    assert brief["dialogue_state"] == "briefed"
    assert smalltalk is not None
    assert smalltalk["dialogue_state"] == "talked"
    assert unknown is None
    for keyword in ("信号", "灯", "怎么", "帮忙", "任务", "该做", "说话", "问", "聊"):
        reply = story_reply(STORY_WATCHMAN_ID, f"我想问问{keyword}的事")
        assert reply is not None
        assert reply["dialogue_state"] == "briefed", keyword
    # 建议模块生成的指令原文（「和守夜人老兵说话」）必须能推进目标 1。
    natural = story_reply(STORY_WATCHMAN_ID, "和守夜人老兵说话")
    assert natural is not None and natural["dialogue_state"] == "briefed"


def test_pure_functions_are_repeatable() -> None:
    state = _state()
    victory = _victory_state()

    assert build_story_state(save_seed=_SAVE_SEED) == build_story_state(save_seed=_SAVE_SEED)
    assert story_goal(state) == story_goal(state)
    assert story_goal(victory) == story_goal(victory)
    assert story_ending(victory) == story_ending(victory)
    assert story_reply(STORY_WATCHMAN_ID, "信号") == story_reply(STORY_WATCHMAN_ID, "信号")
    assert is_story_state(state) is True


# ---------------------------------------------------------------------------
# R-01：背包聚合/制造视图不把语义标签当模板键
# ---------------------------------------------------------------------------
def _ids_by_name(state: GameState) -> dict[str, list[str]]:
    """物品名 → 实例 id 列表（铜线有两件）。"""
    grouped: dict[str, list[str]] = {}
    for item in state.items.values():
        grouped.setdefault(item.name, []).append(item.id)
    return grouped


def test_inventory_does_not_merge_semantic_tags() -> None:
    """R-01：药水与烈酒都带 consumable，但不得合并成一项 qty=2。"""
    state = _state()
    ids = _ids_by_name(state)
    state = _with_inventory(state, ids["治疗药水"][0], ids["结霜的烈酒"][0])

    inventory = QueryService(InMemoryRepository()).get_inventory(state, PLAYER_ID)["items"]

    assert sorted(entry["name"] for entry in inventory) == ["治疗药水", "结霜的烈酒"]
    assert all(entry["qty"] == 1 for entry in inventory)


def test_story_craft_succeeds_with_semantic_items_in_inventory() -> None:
    """R-01：背包同时有药水/火石/锻锤时，打造信号火把仍成功（不再 ValidationError）。"""
    state = _state()
    ids = _ids_by_name(state)
    state = _with_inventory(
        state,
        ids["火把"][0],
        ids["铜线"][0],
        ids["铜线"][1],
        ids["治疗药水"][0],
        ids["火石"][0],
        ids["锻锤"][0],
    )

    state_slice = build_craft_state_slice(state, PLAYER_ID)
    assert state_slice.inventory["item.copper_wire"] == 2
    assert "item.potion" in state_slice.inventory
    assert "item.flint" in state_slice.inventory
    assert "item.forge_hammer" in state_slice.inventory

    service = CraftService(RecipeCatalog.load_builtin())
    ctx = ToolContext(
        actor_id=PLAYER_ID,
        actor_tags=frozenset({"player"}),
        turn_id=state.turn + 1,
        save_seed=state.rng_state.save_seed,
        idempotency_key="story-craft-key-01",
        caller="player",
    )
    result = service.craft(
        ctx,
        state,
        CraftRequest(mode=CraftMode.RECIPE, recipe_id="recipe.signal_torch"),
        idempotency_key=ctx.idempotency_key,
    )

    assert result.ok is True, result.message
    assert len(result.events) == 1
    produced = result.events[0].result["produced_items"]
    assert [item["name"] for item in produced] == ["信号火把"]
