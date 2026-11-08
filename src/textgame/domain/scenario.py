"""内置单场景 (M2 005 切片): 固定 ULID 的「废弃哨站」初始状态构造 (纯函数)。

场景数据只服务 M2 单场景 MVP 与固定种子回放 (契约 `.work/m2-contract.md` §3.4)：
所有 ``room_``/``char_``/``npc_``/``item_`` ID 逐字固化，禁止随机生成，否则 golden
回放与 ``state_hash`` 会漂移。本模块只依赖 ``domain.models``，不读库、不读配置、不碰 IO
(第 5 章 5.4/5.5、第 14 章 14.7)。

方向词 → ``Direction`` 的口径同 §3.4（北/南/东/西/上/下），供 ``orchestration/intent.py``
的确定性解析复用；反向映射（``Direction`` → 中文）供模板叙事复用。
"""

from __future__ import annotations

from typing import Final

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
