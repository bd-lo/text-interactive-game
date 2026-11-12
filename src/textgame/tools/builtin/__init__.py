"""工具包：M2 七工具 + M4 craft_item + R7 行动自由度四工具的完整注册表构造入口。"""

from .crafting import CRAFT_TOOL_NAME, build_game_tool_registry, craft_item
from .freedom import (
    FREEDOM_TOOL_NAMES,
    FREEDOM_TOOLS,
    attack,
    drop_item,
    give_item,
    improvise,
)

__all__ = [
    "CRAFT_TOOL_NAME",
    "FREEDOM_TOOLS",
    "FREEDOM_TOOL_NAMES",
    "attack",
    "build_game_tool_registry",
    "craft_item",
    "drop_item",
    "give_item",
    "improvise",
]
