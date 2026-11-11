"""内置单场景 (M2 005 切片): 固定 ULID 的「废弃哨站」初始状态构造 (纯函数)。

场景数据只服务 M2 单场景 MVP 与固定种子回放 (契约 `.work/m2-contract.md` §3.4)：
所有 ``room_``/``char_``/``npc_``/``item_`` ID 逐字固化，禁止随机生成，否则 golden
回放与 ``state_hash`` 会漂移。本模块只依赖 ``domain.models``，不读库、不读配置、不碰 IO
(第 5 章 5.4/5.5、第 14 章 14.7)。

方向词 → ``Direction`` 的口径同 §3.4（北/南/东/西/上/下），供 ``orchestration/intent.py``
的确定性解析复用；反向映射（``Direction`` → 中文）供模板叙事复用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

from textgame.domain.models import NPC, Character, GameState, Item, RngState, Room
from textgame.domain.types import Direction

# ---------------------------------------------------------------------------
# 固定 ULID（26 位、字符集 [0-9A-HJKMNP-TV-Z]；契约 §3.4，逐字使用，禁止改名）
# ---------------------------------------------------------------------------
ROOM_ENTRY: Final[str] = "room_01J9Z3K4Q7X8Y9Z0ABCDEFGHJK"  # 废弃哨站·入口
ROOM_CELLAR: Final[str] = "room_01J9Z3K4Q7X8Y9Z0ABCDEFGHJM"  # 哨站地窖
PLAYER_ID: Final[str] = "char_01J9Z3K4Q7X8Y9Z0ABCDEFGHJN"  # 玩家角色
NPC_SENTINEL: Final[str] = "npc_01J9Z3K4Q7X8Y9Z0ABCDEFGHJP"  # 老哨兵
ITEM_TORCH: Final[str] = "item_01J9Z3K4Q7X8Y9Z0ABCDEFGHJQ"  # 火把
ITEM_POTION: Final[str] = "item_01J9Z3K4Q7X8Y9Z0ABCDEFGHJR"  # 治疗药水
ITEM_SWORD: Final[str] = "item_01J9Z3K4Q7X8Y9Z0ABCDEFGHJS"  # 锈剑
ITEM_RELIC: Final[str] = "item_01J9Z3K4Q7X8Y9Z0ABCDEFGHJT"  # 古物

# 名称只用于展示与确定性解析匹配（契约 §3.4）。
PLAYER_NAME: Final[str] = "旅人"
ROOM_ENTRY_NAME: Final[str] = "废弃哨站·入口"
ROOM_CELLAR_NAME: Final[str] = "哨站地窖"
NPC_SENTINEL_NAME: Final[str] = "老哨兵"
ITEM_TORCH_NAME: Final[str] = "火把"
ITEM_POTION_NAME: Final[str] = "治疗药水"
ITEM_SWORD_NAME: Final[str] = "锈剑"
ITEM_RELIC_NAME: Final[str] = "古物"

DEFAULT_SAVE_SEED: Final[str] = "save-42"

# 契约 §3.4：方向词映射（中文关键词 -> 领域 Direction）。
DIRECTION_KEYWORDS: Final[dict[str, Direction]] = {
    "北": "north",
    "南": "south",
    "东": "east",
    "西": "west",
    "上": "up",
    "下": "down",
}

# 模板叙事用的反向映射（Direction -> 中文方向词）。
DIRECTION_NAMES: Final[dict[str, str]] = {value: key for key, value in DIRECTION_KEYWORDS.items()}
# R-17：``in``/``out`` 不在中文关键词表（避免单字误配），但展示/建议必须有中文，
# 否则会出现「向in走」这类英文泄漏。
DIRECTION_NAMES.update({"in": "内", "out": "外"})

#: 用户场景/LLM 可能使用的 ``in``/``out`` 安全别名（≥2 字，避免「里」「出」误配）。
DIRECTION_ALIASES: Final[dict[str, Direction]] = {
    "里面": "in",
    "内部": "in",
    "进入": "in",
    "进去": "in",
    "进屋": "in",
    "外面": "out",
    "出去": "out",
    "走出": "out",
}


def build_initial_state(*, save_seed: str) -> GameState:
    """构造单场景初始 ``GameState``；``turn=1``、``world_time=0``、``last_seq=0``。

    纯函数：同一 ``save_seed`` 必得同一状态（``state_hash`` 可复算），不读时钟、不随机。
    """
    entry = Room(
        id=ROOM_ENTRY,
        name=ROOM_ENTRY_NAME,
        description=(
            "废弃哨站的入口塌了一半，风从断裂的门楣间穿过，卷起细碎的石屑。"
            "墙角堆着受潮的木箱，地上散落着几件被遗弃的杂物。"
        ),
        exits={"north": ROOM_CELLAR},
        item_ids=[ITEM_TORCH, ITEM_POTION],
        npc_ids=[NPC_SENTINEL],
        ambient_tags=["ruined", "dusty"],
    )
    cellar = Room(
        id=ROOM_CELLAR,
        name=ROOM_CELLAR_NAME,
        description=(
            "地窖里几乎没有光，潮湿的泥土味压得人喘不过气。"
            "几级石阶从北面通下来，墙上的凹槽里似乎塞着什么东西。"
        ),
        exits={"south": ROOM_ENTRY},
        item_ids=[ITEM_SWORD, ITEM_RELIC],
        ambient_tags=["dark", "damp"],
    )
    torch = Item(
        id=ITEM_TORCH,
        name=ITEM_TORCH_NAME,
        description="一支受潮的火把，布头上还留着松脂的气味。",
        tags=["light"],
    )
    potion = Item(
        id=ITEM_POTION,
        name=ITEM_POTION_NAME,
        description="小瓶装的红色药水，木塞封口，液体在瓶里轻轻晃。",
        tags=["consumable"],
        stats={"heal": 8},
    )
    sword = Item(
        id=ITEM_SWORD,
        name=ITEM_SWORD_NAME,
        description="一柄锈迹斑斑的短剑，刃口钝了，但分量还在。",
        tags=["weapon"],
        stats={"damage": 3},
    )
    relic = Item(
        id=ITEM_RELIC,
        name=ITEM_RELIC_NAME,
        description="看不出用途的青铜小件，表面刻着早已磨平的纹路。",
        tags=["quest"],
    )
    player = Character(
        id=PLAYER_ID,
        name=PLAYER_NAME,
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
        location=ROOM_ENTRY,
        hp=20,
        created_turn=1,
    )
    sentinel = NPC(
        id=NPC_SENTINEL,
        name=NPC_SENTINEL_NAME,
        room_id=ROOM_ENTRY,
        memory_ref="npc_sentinel",
    )
    return GameState(
        turn=1,
        world_time=0,
        characters={PLAYER_ID: player},
        rooms={ROOM_ENTRY: entry, ROOM_CELLAR: cellar},
        items={
            ITEM_TORCH: torch,
            ITEM_POTION: potion,
            ITEM_SWORD: sword,
            ITEM_RELIC: relic,
        },
        npcs={NPC_SENTINEL: sentinel},
        quests={},
        rng_state=RngState(save_seed=save_seed, last_seq=0),
    )


# ---------------------------------------------------------------------------
# M3 多角色场景（契约 .work/m3-contract.md §3.6；不改 build_initial_state，M2 golden 不回归）
# ---------------------------------------------------------------------------
#: 两名「拾荒者」非玩家角色：用户 `Character` 建模以具备背包（第 5 章 NPC 无 inventory），
#: 用于演示「同回合抢同一物品」的冲突提案与审计（D-M3-4）。
M3_NPC_SCAVENGER_A: Final[str] = "char_01J9Z3K4Q7X8Y9Z0ABCDEFGHJV"
M3_NPC_SCAVENGER_B: Final[str] = "char_01J9Z3K4Q7X8Y9Z0ABCDEFGHJW"
M3_NPC_SCAVENGER_A_NAME: Final[str] = "拾荒者甲"
M3_NPC_SCAVENGER_B_NAME: Final[str] = "拾荒者乙"

#: 非玩家角色的细粒度授权标签（服务端静态配置，禁止来自模型输出，第 7 章 §7.3）。
M3_NONPLAYER_TAGS: Final[dict[str, frozenset[str]]] = {
    M3_NPC_SCAVENGER_A: frozenset({"npc", "npc:scavenger"}),
    M3_NPC_SCAVENGER_B: frozenset({"npc", "npc:scavenger"}),
}


@dataclass(frozen=True)
class PlanStep:
    """确定性角色 Agent 的一条计划动作（第 8 章 CharacterAgent 的 M3 裁剪版）。"""

    turn_id: int
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    reason: str = "npc_plan"


#: 十回合多角色回放用的 NPC 计划表（turn_id = 玩家第 N 次输入对应的回合号）。
#: turn 2 两名拾荒者同回合抢同一瓶治疗药水 —— M3 冲突演示（Resolver 只让一个生效）。
M3_NPC_PLANS: Final[dict[str, tuple[PlanStep, ...]]] = {
    M3_NPC_SCAVENGER_A: (
        PlanStep(2, "take_item", {"item_id": ITEM_POTION}, "抢先把药水收进背包"),
        PlanStep(4, "move_to", {"direction": "north"}, "跟着往地窖去"),
        PlanStep(6, "move_to", {"direction": "south"}, "退回入口"),
        PlanStep(10, "take_item", {"item_id": ITEM_POTION}, "再摸一次那瓶药水"),
    ),
    M3_NPC_SCAVENGER_B: (
        PlanStep(2, "take_item", {"item_id": ITEM_POTION}, "也盯上了那瓶药水"),
        PlanStep(5, "move_to", {"direction": "north"}, "摸黑下到地窖"),
        PlanStep(7, "take_item", {"item_id": ITEM_TORCH}, "想捡起火把"),
        PlanStep(9, "move_to", {"direction": "south"}, "回到入口"),
    ),
}


def _scavenger(*, character_id: str, name: str) -> Character:
    """构造一名拾荒者非玩家角色（固定属性，保证 state_hash 可复算）。"""
    return Character(
        id=character_id,
        name=name,
        attributes={
            "str": 10,
            "dex": 12,
            "con": 11,
            "int": 9,
            "wis": 10,
            "cha": 8,
            "max_hp": 12,
        },
        inventory=[],
        location=ROOM_ENTRY,
        hp=12,
        created_turn=1,
    )


def build_m3_state(*, save_seed: str) -> GameState:
    """构造 M3 多角色初始状态：M2 单场景 + 两名拾荒者（纯函数、固定 ID、零 IO）。

    ``build_initial_state`` 保持不变（M2 golden 不回归）；本函数只在其结果上追加角色，
    房间/物品/NPC 与 M2 完全一致。
    """
    base = build_initial_state(save_seed=save_seed)
    characters = {
        **base.characters,
        M3_NPC_SCAVENGER_A: _scavenger(
            character_id=M3_NPC_SCAVENGER_A, name=M3_NPC_SCAVENGER_A_NAME
        ),
        M3_NPC_SCAVENGER_B: _scavenger(
            character_id=M3_NPC_SCAVENGER_B, name=M3_NPC_SCAVENGER_B_NAME
        ),
    }
    return base.model_copy(update={"characters": characters})


# ---------------------------------------------------------------------------
# M4 制造场景（D-M4-11；加法新增，不改 build_initial_state/build_m3_state，M2/M3 golden 不回归）
# ---------------------------------------------------------------------------
M4_ROOM_FORGE: Final[str] = "room_01J9Z3K4Q7X8Y9Z0ABCDEFGHJX"
M4_ITEM_IRON_INGOT: Final[str] = "item_01J9Z3K4Q7X8Y9Z0ABCDEFGHJY"
M4_ITEM_OAK_HANDLE: Final[str] = "item_01J9Z3K4Q7X8Y9Z0ABCDEFGHJZ"
M4_ITEM_FORGE_HAMMER: Final[str] = "item_01J9Z3K4Q7X8Y9Z0ABCDEFGHK0"
M4_ITEM_COPPER_WIRE: Final[str] = "item_01J9Z3K4Q7X8Y9Z0ABCDEFGHK1"

M4_ROOM_FORGE_NAME: Final[str] = "废弃铁匠铺"
M4_ITEM_IRON_INGOT_NAME: Final[str] = "铁锭"
M4_ITEM_OAK_HANDLE_NAME: Final[str] = "橡木柄"
M4_ITEM_FORGE_HAMMER_NAME: Final[str] = "锻锤"
M4_ITEM_COPPER_WIRE_NAME: Final[str] = "铜线"

#: 材料实例 tags[0] 的模板键（第 13 章 §13.2、D-M4-1；配方 JSON 用同一批模板键）。
M4_TEMPLATE_IRON_INGOT: Final[str] = "item.iron_ingot"
M4_TEMPLATE_OAK_HANDLE: Final[str] = "item.oak_handle"
M4_TEMPLATE_FORGE_HAMMER: Final[str] = "item.forge_hammer"
M4_TEMPLATE_COPPER_WIRE: Final[str] = "item.copper_wire"

#: M4 场景玩家初始背包（重复项表达数量：铁锭×2、铜线×2；覆盖两个内置配方的材料需求）。
M4_STARTING_INVENTORY: Final[tuple[str, ...]] = (
    M4_ITEM_IRON_INGOT,
    M4_ITEM_IRON_INGOT,
    M4_ITEM_OAK_HANDLE,
    M4_ITEM_FORGE_HAMMER,
    M4_ITEM_COPPER_WIRE,
    M4_ITEM_COPPER_WIRE,
)


def _with_template_tag(item: Item, template_id: str) -> Item:
    """把物品实例 tags 首项规范为模板键（M4 场景内的制造材料口径；不改 Item 模型）。"""
    tags = [template_id, *(tag for tag in item.tags if tag != template_id)][:8]
    return item.model_copy(update={"tags": tags})


def build_m4_state(*, save_seed: str) -> GameState:
    """构造 M4 制造场景初始状态：M2 单场景 + 铁匠铺 + 制造材料（纯函数、固定 ID）。

    只改本函数返回值，build_initial_state/build_m3_state 及其 golden 字节不变（D-M4-11）。
    入口房间东侧连 forge 房间（ambient_tags 含 station.forge 作为工位，D-M4-9）；玩家初始
    背包含制造材料（重复项表达数量）；全部实例 tags 首项为模板键，供 CraftService 聚合
    材料视图（D-M4-1）。固定 ID、禁止随机，state_hash 可复算。
    """
    base = build_initial_state(save_seed=save_seed)
    forge = Room(
        id=M4_ROOM_FORGE,
        name=M4_ROOM_FORGE_NAME,
        description=(
            "铁匠铺的屋顶塌了半边，炉膛里积着冷灰。墙角还立着半截铁砧，"
            "风箱的皮囊裂了几道口子，但勉强还能使。"
        ),
        exits={"west": ROOM_ENTRY},
        item_ids=[],
        npc_ids=[],
        ambient_tags=["station.forge"],
    )
    entry = base.rooms[ROOM_ENTRY]
    rooms = {
        **base.rooms,
        ROOM_ENTRY: entry.model_copy(update={"exits": {**entry.exits, "east": M4_ROOM_FORGE}}),
        M4_ROOM_FORGE: forge,
    }
    items = {
        **base.items,
        ITEM_TORCH: _with_template_tag(base.items[ITEM_TORCH], "item.torch"),
        ITEM_POTION: _with_template_tag(base.items[ITEM_POTION], "item.potion"),
        ITEM_SWORD: _with_template_tag(base.items[ITEM_SWORD], "item.sword"),
        ITEM_RELIC: _with_template_tag(base.items[ITEM_RELIC], "item.relic"),
        M4_ITEM_IRON_INGOT: Item(
            id=M4_ITEM_IRON_INGOT,
            name=M4_ITEM_IRON_INGOT_NAME,
            description="一块沉甸甸的粗铁锭，边角还带着没敲净的炉渣。",
            tags=[M4_TEMPLATE_IRON_INGOT, "material"],
            stackable=True,
        ),
        M4_ITEM_OAK_HANDLE: Item(
            id=M4_ITEM_OAK_HANDLE,
            name=M4_ITEM_OAK_HANDLE_NAME,
            description="打磨过的橡木柄，握上去还算顺手。",
            tags=[M4_TEMPLATE_OAK_HANDLE, "material"],
            stackable=True,
        ),
        M4_ITEM_FORGE_HAMMER: Item(
            id=M4_ITEM_FORGE_HAMMER,
            name=M4_ITEM_FORGE_HAMMER_NAME,
            description="一柄趁手的锻锤，锤面被多年的敲打磨得发亮。",
            tags=[M4_TEMPLATE_FORGE_HAMMER, "tool"],
        ),
        M4_ITEM_COPPER_WIRE: Item(
            id=M4_ITEM_COPPER_WIRE,
            name=M4_ITEM_COPPER_WIRE_NAME,
            description="一截剥了皮的铜线，在光下泛着暗红。",
            tags=[M4_TEMPLATE_COPPER_WIRE, "material"],
            stackable=True,
        ),
    }
    player = base.characters[PLAYER_ID]
    characters = {
        **base.characters,
        PLAYER_ID: player.model_copy(update={"inventory": list(M4_STARTING_INVENTORY)}),
    }
    return base.model_copy(update={"rooms": rooms, "items": items, "characters": characters})
