"""剧情场景 ``scenario="story"``「余烬哨站」：固定 ID 的初始状态 + 读时目标/结局求值（纯函数）。

本模块只服务玩家首玩的垂直切片（`.work/team-player-2026-11-10/spec-scenario-story.md`）：
6 房间、7 个物品实例、1 名 NPC、3 步目标链、胜负双结局。**刻意不新增任何事件类型、不改
``reducers``/``action_service``/既有场景 builder**：目标与结局都由 :func:`story_goal` /
:func:`story_ending` 从当前 :class:`GameState` 的既有事实（所在房间、行囊、NPC 的
``dialogue_state``、HP）**读时纯求值**，因此对 m2/m3/m4 的 golden 回放零影响。

硬约束（规格 §0/§5）：
- 纯函数、零 IO、零随机、零时钟：只 import ``textgame.domain.models``（与标准库 typing）；
- 全部 ``room_``/``item_``/``npc_`` ID 逐字固化（26 位 ULID，字符集 ``[0-9A-HJKMNP-TV-Z]``），
  同一 ``save_seed`` 必得同一状态，``state_hash`` 可复算；
- 玩家角色 ID 必须与 ``domain.scenario.PLAYER_ID`` 一致：查询/叙事/编排全链路按该常量取玩家，
  故此处以私有字面量固化并由单测钉住（规格 §0 的「只 import models/types」不允许直接 import）。

制造衔接：目标 2 复用既有配方 ``recipe.signal_torch``（火把×1 + 铜线×2），产物实例的
``tags[0]`` 由 ``instantiate_craft_result`` 规范为模板键 ``item.signal_torch``，因此本模块
用 ``tags[0] == STORY_SIGNAL_TORCH_TEMPLATE`` 判定「已造出信号火把」。
"""

from __future__ import annotations

from typing import Any, Final

from textgame.domain.models import NPC, Character, GameState, Item, RngState, Room
from textgame.domain.types import Direction

# ---------------------------------------------------------------------------
# 场景元信息与固定 ID（26 位 ULID：公共前缀 25 位 + 1 位区分符）
# ---------------------------------------------------------------------------

STORY_SCENARIO: Final[str] = "story"
STORY_TITLE: Final[str] = "余烬哨站"

_ID_PREFIX: Final[str] = "01J9Z3K4Q7X8Y9Z0ABCDEFGHK"

#: 房间 ID（规格 §1：断桥 K2 / 入口 K3 / 地窖 K4 / 铁匠铺 K5 / 瞭望塔 K6 / 枯井 K7）。
_ROOM_BRIDGE: Final[str] = f"room_{_ID_PREFIX}2"
_ROOM_HUB: Final[str] = f"room_{_ID_PREFIX}3"
_ROOM_CELLAR: Final[str] = f"room_{_ID_PREFIX}4"
_ROOM_FORGE: Final[str] = f"room_{_ID_PREFIX}5"
_ROOM_TOWER: Final[str] = f"room_{_ID_PREFIX}6"
_ROOM_WELL: Final[str] = f"room_{_ID_PREFIX}7"

#: 物品实例 ID（火把 K8 / 火石 K9 / 药水 KA / 铜线×2 KB、KC / 锻锤 KD / 烈酒 KE）。
_ITEM_TORCH: Final[str] = f"item_{_ID_PREFIX}8"
_ITEM_FLINT: Final[str] = f"item_{_ID_PREFIX}9"
_ITEM_POTION: Final[str] = f"item_{_ID_PREFIX}A"
_ITEM_COPPER_A: Final[str] = f"item_{_ID_PREFIX}B"
_ITEM_COPPER_B: Final[str] = f"item_{_ID_PREFIX}C"
_ITEM_HAMMER: Final[str] = f"item_{_ID_PREFIX}D"
_ITEM_LIQUOR: Final[str] = f"item_{_ID_PREFIX}E"

#: 唯一 NPC（守夜人老兵）。
_NPC_WATCHMAN: Final[str] = f"npc_{_ID_PREFIX}F"

#: 玩家角色 ID：与 ``domain.scenario.PLAYER_ID`` 逐字一致（单测钉住，改常量必须同步测试）。
_PLAYER_CHARACTER_ID: Final[str] = "char_01J9Z3K4Q7X8Y9Z0ABCDEFGHJN"

#: 对外接口常量（规格 §3，签名/取值不可改；H4/L1 加法：枯井环境伤害）。
STORY_ENTRY_ROOM_ID: Final[str] = _ROOM_BRIDGE
STORY_WATCHMAN_ID: Final[str] = _NPC_WATCHMAN
STORY_SIGNAL_TORCH_TEMPLATE: Final[str] = "item.signal_torch"
STORY_FLINT_ID: Final[str] = _ITEM_FLINT
STORY_TOWER_ROOM_ID: Final[str] = _ROOM_TOWER
STORY_WELL_ROOM_ID: Final[str] = _ROOM_WELL

#: H4/L1：可重复的环境伤害（每次进入该房间扣 HP；确定性、不随机）。
#: 枯井寒气 -6/次：初始 12 HP → 连续两次进入枯井即可进入「倒下」结局，同时药水/烈酒
#: 仍能把玩家救回来（药水 +8、烈酒 -4），失败结局因此可达但仍可选择规避。
_STORY_ROOM_DAMAGE: Final[dict[str, int]] = {
    _ROOM_WELL: -6,
}

#: 铜线模板键（两件实例共用；``tags[0]`` 必须是模板键，否则制造材料聚合失败）。
_TEMPLATE_COPPER_WIRE: Final[str] = "item.copper_wire"

# ---------------------------------------------------------------------------
# 文案（简体中文、2G 文字 MUD 冷峻笔触；不得出现内部 ID/emoji/英文技术词）
# ---------------------------------------------------------------------------

_ROOM_BRIDGE_NAME: Final[str] = "哨站外·断桥"
_ROOM_HUB_NAME: Final[str] = "废弃哨站·入口"
_ROOM_CELLAR_NAME: Final[str] = "哨站地窖"
_ROOM_FORGE_NAME: Final[str] = "废弃铁匠铺"
_ROOM_TOWER_NAME: Final[str] = "哨站瞭望塔"
_ROOM_WELL_NAME: Final[str] = "枯井"

_ROOM_BRIDGE_DESC: Final[str] = (
    "断桥横在封冻的河面上，桥板缺了三块，风从缺口里直灌上来。你在雪里走了半夜，"
    "旧伤在肋下隐隐作痛。哨站就在桥东头，信号灯灭了两夜——天亮前若点不亮它，"
    "雪线那头的人就找不到这条路。"
)
_ROOM_HUB_DESC: Final[str] = (
    "哨站的门塌了一半，雪从破口堆进来。石阶往北通向地窖，东边是铁匠铺，"
    "头顶的梯子通往瞭望塔，南边枯井被雪埋了半截。守夜人老兵坐在火盆边，没有抬头，"
    "像在等谁先开口。"
)
_ROOM_CELLAR_DESC: Final[str] = (
    "地窖里潮气很重，石缝结着薄霜。墙角压着一块黑色的火石，旁边立着半瓶治疗药水。"
    "石阶往南能回地面，风从瞭望塔的方向灌下来，带着铁锈味。"
)
_ROOM_FORGE_DESC: Final[str] = (
    "铁匠铺的屋顶塌了半边，炉膛里积着冷灰。工作台上摊着两卷铜线，锤柄上还留着上一个人的指印。"
    "风从破窗灌进来，吹得灰烬打旋。"
)
_ROOM_TOWER_DESC: Final[str] = (
    "瞭望塔只有一圈窄梯，爬到顶时风几乎把人推下去。灯座空着，灯芯是干的，铁架里积着雪。"
    "从这里能看见整条封冻的河谷——灯一亮，雪线那头的人就看得见。得有一支信号火把和一块火石，"
    "才点得起来。"
)
_ROOM_WELL_DESC: Final[str] = (
    "枯井在入口背后，井沿结着霜。井底比外头暖和，靠壁坐着一个没了气息的旅人，"
    "身边放着一瓶结霜的烈酒。你移开视线，又忍不住多看了一眼。"
)

_ITEM_TORCH_NAME: Final[str] = "火把"
_ITEM_FLINT_NAME: Final[str] = "火石"
_ITEM_POTION_NAME: Final[str] = "治疗药水"
_ITEM_COPPER_NAME: Final[str] = "铜线"
_ITEM_HAMMER_NAME: Final[str] = "锻锤"
_ITEM_LIQUOR_NAME: Final[str] = "结霜的烈酒"

_ITEM_TORCH_DESC: Final[str] = "一支受潮的火把，布头上还留着松脂的气味。"
_ITEM_FLINT_DESC: Final[str] = "一块黑色的火石，棱角被磨得发亮，敲两下就能迸出火星。"
_ITEM_POTION_DESC: Final[str] = "小瓶装的红色药水，木塞封口，液体在瓶里轻轻晃。"
_ITEM_COPPER_DESC: Final[str] = "一截剥了皮的铜线，在光下泛着暗红。"
_ITEM_HAMMER_DESC: Final[str] = "一柄趁手的锻锤，锤面被多年的敲打磨得发亮。"
_ITEM_LIQUOR_DESC: Final[str] = "一瓶结了霜的烈酒，喝下去能压住寒气，也会烧得人眼前发黑。"

_PLAYER_NAME: Final[str] = "旅人"
_WATCHMAN_NAME: Final[str] = "守夜人老兵"

_GOAL_TITLE: Final[str] = "让信号灯亮起来"
_GOAL_DESC: Final[str] = (
    "风雪今夜就要封路，哨站的信号灯已经灭了两夜。天亮前把它点亮，"
    "雪线那头的人才会循着灯光找到哨站，你今晚才有活路。"
)
_OBJECTIVE_LISTEN_TEXT: Final[str] = "问问守夜人老兵：信号灯怎么点"
_OBJECTIVE_CRAFT_TEXT: Final[str] = "找齐火把和两卷铜线，打造一支信号火把"
_OBJECTIVE_LIGHT_TEXT: Final[str] = "带上信号火把和火石，登上瞭望塔"

_BRIEF_KEYWORDS: Final[tuple[str, ...]] = (
    "信号",
    "灯",
    "怎么",
    "帮忙",
    "任务",
    "该做",
    # 玩家最自然的第一句往往就是「和守夜人老兵说话」/「问问情况」：这些泛对话词
    # 也应当触发任务说明，否则建议动作会让玩家点出一个永远完成不了的目标。
    "说话",
    "聊",
    "问",
    "交谈",
    "你好",
)
_REPLY_BRIEFED: Final[str] = (
    "老兵抬起眼：『灯座空着，得有一支信号火把。火把就在这屋里地上，铜线在铁匠铺，要两卷；"
    "凑齐了就能打造出来。做好之后带上地窖那块火石，爬上瞭望塔点亮——别空手上去，风大。』"
)
_REPLY_TALKED: Final[str] = (
    "老兵往火盆里添了块炭：『雪停之前别往河边去，桥板底下是空的。』他顿了顿，又补一句："
    "『你要是来问灯的事，就直说。』"
)

_ENDING_VICTORY_ID: Final[str] = "ending_lantern"
_ENDING_VICTORY_TITLE: Final[str] = "灯亮了"
_ENDING_VICTORY_TEXT: Final[str] = (
    "你把信号火把插进灯座，火石在铁架上敲了两下。火星先是一小点，接着整支火把烧起来，"
    "暖光扫过封冻的河谷。雪线那头有人在回应——一点、两点，正朝哨站移动。今夜这条路，又有活人了。"
)
_ENDING_FALLEN_ID: Final[str] = "ending_fallen"
_ENDING_FALLEN_TITLE: Final[str] = "你倒下了"
_ENDING_FALLEN_TEXT: Final[str] = (
    "寒意从脚底漫到胸口，你靠着墙坐下，听见风把门板吹得一下一下响。灯还灭着，雪还在下。"
    "你想起老兵那句话，可手指已经抬不起来了。"
)

__all__ = [
    "STORY_ENTRY_ROOM_ID",
    "STORY_FLINT_ID",
    "STORY_SCENARIO",
    "STORY_SIGNAL_TORCH_TEMPLATE",
    "STORY_TITLE",
    "STORY_TOWER_ROOM_ID",
    "STORY_WATCHMAN_ID",
    "STORY_WELL_ROOM_ID",
    "build_story_state",
    "is_story_state",
    "story_ending",
    "story_goal",
    "story_reply",
    "story_room_damage",
]


# ---------------------------------------------------------------------------
# 初始状态构造（纯函数：固定 ID、零随机、零 IO）
# ---------------------------------------------------------------------------


def _room(
    *,
    room_id: str,
    name: str,
    description: str,
    exits: dict[Direction, str],
    item_ids: list[str] | None = None,
    npc_ids: list[str] | None = None,
    ambient_tags: list[str] | None = None,
) -> Room:
    """构造一个房间（固定字段顺序，保证 ``model_dump`` 可复算）。"""
    return Room(
        id=room_id,
        name=name,
        description=description,
        exits=exits,
        item_ids=list(item_ids or []),
        npc_ids=list(npc_ids or []),
        ambient_tags=list(ambient_tags or []),
    )


def build_story_state(*, save_seed: str) -> GameState:
    """构造「余烬哨站」初始 ``GameState``；``turn=1``、``world_time=0``、``last_seq=0``。

    出生点是断桥（唯一入口房），玩家身上没有物品；开局 HP 为 **12/20**（雪夜赶路、旧伤在身），
    这样治疗药水的 ``heal=8`` 在玩家侧可观测（满血用药水等于假道具）。目标/结局不写进状态
    （``quests={}``），由 :func:`story_goal` / :func:`story_ending` 读时求值。纯函数：同一
    ``save_seed`` 必得同一状态，``state_hash`` 可复算。
    """
    bridge = _room(
        room_id=_ROOM_BRIDGE,
        name=_ROOM_BRIDGE_NAME,
        description=_ROOM_BRIDGE_DESC,
        exits={"east": _ROOM_HUB},
        ambient_tags=["snow", "ruined"],
    )
    hub = _room(
        room_id=_ROOM_HUB,
        name=_ROOM_HUB_NAME,
        description=_ROOM_HUB_DESC,
        exits={
            "west": _ROOM_BRIDGE,
            "north": _ROOM_CELLAR,
            "east": _ROOM_FORGE,
            "up": _ROOM_TOWER,
            "south": _ROOM_WELL,
        },
        item_ids=[_ITEM_TORCH],
        npc_ids=[_NPC_WATCHMAN],
        ambient_tags=["ruined", "shelter"],
    )
    cellar = _room(
        room_id=_ROOM_CELLAR,
        name=_ROOM_CELLAR_NAME,
        description=_ROOM_CELLAR_DESC,
        exits={"south": _ROOM_HUB},
        item_ids=[_ITEM_FLINT, _ITEM_POTION],
        ambient_tags=["dark", "damp"],
    )
    forge = _room(
        room_id=_ROOM_FORGE,
        name=_ROOM_FORGE_NAME,
        description=_ROOM_FORGE_DESC,
        exits={"west": _ROOM_HUB},
        item_ids=[_ITEM_COPPER_A, _ITEM_COPPER_B, _ITEM_HAMMER],
        ambient_tags=["station.forge"],
    )
    tower = _room(
        room_id=_ROOM_TOWER,
        name=_ROOM_TOWER_NAME,
        description=_ROOM_TOWER_DESC,
        exits={"down": _ROOM_HUB},
        ambient_tags=["signal", "wind"],
    )
    well = _room(
        room_id=_ROOM_WELL,
        name=_ROOM_WELL_NAME,
        description=_ROOM_WELL_DESC,
        exits={"north": _ROOM_HUB},
        item_ids=[_ITEM_LIQUOR],
        ambient_tags=["cold", "quiet"],
    )
    items = {
        _ITEM_TORCH: Item(
            id=_ITEM_TORCH,
            name=_ITEM_TORCH_NAME,
            description=_ITEM_TORCH_DESC,
            # tags[0] 必须是模板键：``recipe.signal_torch`` 按 ``tags[0]=="item.torch"``
            # 聚合材料（D-M4-1），否则目标 2「打造信号火把」永远缺火把。
            # ``quest`` 标签让「使用火把」被前置拒绝（D-R2-5 剧情/材料保护，防主线材料
            # 被误消耗造成永久软锁）；不影响材料聚合（第 1 位仍是模板键）。
            tags=["item.torch", "quest", "light"],
        ),
        _ITEM_FLINT: Item(
            id=_ITEM_FLINT,
            name=_ITEM_FLINT_NAME,
            description=_ITEM_FLINT_DESC,
            tags=["quest", "material"],
        ),
        _ITEM_POTION: Item(
            id=_ITEM_POTION,
            name=_ITEM_POTION_NAME,
            description=_ITEM_POTION_DESC,
            tags=["consumable"],
            stats={"heal": 8},
        ),
        _ITEM_COPPER_A: Item(
            id=_ITEM_COPPER_A,
            name=_ITEM_COPPER_NAME,
            description=_ITEM_COPPER_DESC,
            # ``quest`` 标签防止主线材料被「使用」误消耗（D-R2-5）；第 1 位仍是模板键。
            tags=[_TEMPLATE_COPPER_WIRE, "material", "quest"],
            stackable=True,
        ),
        _ITEM_COPPER_B: Item(
            id=_ITEM_COPPER_B,
            name=_ITEM_COPPER_NAME,
            description=_ITEM_COPPER_DESC,
            tags=[_TEMPLATE_COPPER_WIRE, "material", "quest"],
            stackable=True,
        ),
        _ITEM_HAMMER: Item(
            id=_ITEM_HAMMER,
            name=_ITEM_HAMMER_NAME,
            description=_ITEM_HAMMER_DESC,
            tags=["tool"],
        ),
        _ITEM_LIQUOR: Item(
            id=_ITEM_LIQUOR,
            name=_ITEM_LIQUOR_NAME,
            description=_ITEM_LIQUOR_DESC,
            tags=["consumable"],
            stats={"hp_delta": -4},
        ),
    }
    player = Character(
        id=_PLAYER_CHARACTER_ID,
        name=_PLAYER_NAME,
        attributes={
            "str": 12,
            "dex": 14,
            "con": 12,
            "int": 10,
            "wis": 11,
            "cha": 9,
            "max_hp": 20,
        },
        inventory=[],
        location=_ROOM_BRIDGE,
        hp=12,
        created_turn=1,
    )
    watchman = NPC(
        id=_NPC_WATCHMAN,
        name=_WATCHMAN_NAME,
        room_id=_ROOM_HUB,
        dialogue_state="idle",
        faction="哨站",
        memory_ref="npc_story_watchman",
    )
    return GameState(
        turn=1,
        world_time=0,
        characters={player.id: player},
        rooms={
            bridge.id: bridge,
            hub.id: hub,
            cellar.id: cellar,
            forge.id: forge,
            tower.id: tower,
            well.id: well,
        },
        items=items,
        npcs={watchman.id: watchman},
        quests={},
        rng_state=RngState(save_seed=save_seed, last_seq=0),
    )


# ---------------------------------------------------------------------------
# 读时求值：场景识别 / 目标 / 结局 / NPC 对话
# ---------------------------------------------------------------------------


def story_room_damage(room_id: str) -> int | None:
    """H4/L1：某房间的进入伤害（``hp_delta``，负数扣血）；无伤害/非本场景房间返回 ``None``。

    纯函数（同一 ``room_id`` 恒返回同一值）：编排层在 story 存档的 ``CHARACTER_MOVED`` 后
    据此追加 ``ENVIRONMENT_DAMAGE`` 事件（同回合批次提交），旧场景永不命中 → golden 零影响。
    """
    return _STORY_ROOM_DAMAGE.get(room_id)


def is_story_state(state: GameState) -> bool:
    """是否为「余烬哨站」存档（``STORY_ENTRY_ROOM_ID`` 是新场景独有房间）。"""
    return STORY_ENTRY_ROOM_ID in state.rooms


def _player_of(state: GameState) -> Character | None:
    """取本场景玩家角色（缺失时返回 ``None``，调用方走空值分支）。"""
    return state.characters.get(_PLAYER_CHARACTER_ID)


def _has_signal_torch(state: GameState, character: Character) -> bool:
    """玩家行囊里是否存在信号火把实例（制造产物 ``tags[0]`` 为模板键）。"""
    for item_id in character.inventory:
        item: Item | None = state.items.get(item_id)
        if item is not None and item.tags and item.tags[0] == STORY_SIGNAL_TORCH_TEMPLATE:
            return True
    return False


def _is_victory(state: GameState, character: Character) -> bool:
    """胜利条件：人在瞭望塔 + 行囊有信号火把 + 行囊有火石（三者同时满足）。"""
    return (
        character.location == STORY_TOWER_ROOM_ID
        and _has_signal_torch(state, character)
        and STORY_FLINT_ID in character.inventory
    )


def story_goal(state: GameState) -> dict[str, Any] | None:
    """返回玩家目标视图；非「余烬哨站」存档返回 ``None``。

    结构（规格 §3）：``{"title", "description", "objectives": [{"id","text","done"}, ...]}``；
    ``objectives`` 顺序固定为 ``listen`` / ``craft`` / ``light``，完成度全部从当前状态读时计算。
    """
    if not is_story_state(state):
        return None
    character = _player_of(state)
    if character is None:
        return None
    watchman = state.npcs.get(STORY_WATCHMAN_ID)
    listened = watchman is not None and watchman.dialogue_state == "briefed"
    crafted = _has_signal_torch(state, character)
    lit = _is_victory(state, character)
    objectives: list[dict[str, Any]] = [
        {"id": "listen", "text": _OBJECTIVE_LISTEN_TEXT, "done": listened},
        {"id": "craft", "text": _OBJECTIVE_CRAFT_TEXT, "done": crafted},
        {"id": "light", "text": _OBJECTIVE_LIGHT_TEXT, "done": lit},
    ]
    return {"title": _GOAL_TITLE, "description": _GOAL_DESC, "objectives": objectives}


def story_ending(state: GameState) -> dict[str, Any] | None:
    """返回结局视图；未结束/非本场景存档返回 ``None``。

    结构：``{"ending_id", "title", "text"}``。判定顺序先胜利（灯亮）后失败（倒下），
    两者互斥且胜利优先：即使 HP 已归零，只要同时满足胜利条件也判胜利（规格 §5.9）。
    """
    if not is_story_state(state):
        return None
    character = _player_of(state)
    if character is None:
        return None
    if _is_victory(state, character):
        return {
            "ending_id": _ENDING_VICTORY_ID,
            "title": _ENDING_VICTORY_TITLE,
            "text": _ENDING_VICTORY_TEXT,
        }
    if character.hp <= 0:
        return {
            "ending_id": _ENDING_FALLEN_ID,
            "title": _ENDING_FALLEN_TITLE,
            "text": _ENDING_FALLEN_TEXT,
        }
    return None


def story_reply(npc_id: str, topic: str) -> dict[str, Any] | None:
    """确定性 NPC 对话分支：仅守夜人老兵返回 ``{"reply", "dialogue_state"}``。

    命中任务关键词（``信号``/``灯``/``怎么``/``帮忙``/``任务``/``该做``）→ ``briefed`` 并给出
    3 步任务说明；其它话题 → ``talked`` 并给氛围提示（回复恒非空）。未知 NPC 返回 ``None``。
    """
    if npc_id != STORY_WATCHMAN_ID:
        return None
    if any(keyword in topic for keyword in _BRIEF_KEYWORDS):
        return {"reply": _REPLY_BRIEFED, "dialogue_state": "briefed"}
    return {"reply": _REPLY_TALKED, "dialogue_state": "talked"}
