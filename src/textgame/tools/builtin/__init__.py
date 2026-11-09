"""M4 工具包：M2 六工具 + craft_item 的完整注册表构造入口。"""

from .crafting import CRAFT_TOOL_NAME, build_game_tool_registry, craft_item

__all__ = ["CRAFT_TOOL_NAME", "build_game_tool_registry", "craft_item"]
