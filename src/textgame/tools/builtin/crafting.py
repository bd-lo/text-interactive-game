"""M4 内置工具声明：craft_item（第 7 章 §7.3 矩阵、第 13 章制造）。

本模块只承载 ToolSpec/JSON Schema/actor_scope/side_effect：工具函数不持有 GameState、
不直接执行服务调用（第 7 章 §7.1 不变量 5）；执行由 tools/gate.py::ToolGate 校验后路由到
CraftService（第 13 章 §13.5）。build_game_tool_registry() = M2 六工具 + craft_item，
不改 build_m2_tool_registry()（旧安全用例不回归）。
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from textgame.domain.models import ResolutionPolicy, SideEffect
from textgame.domain.results import ErrorCode, Result
from textgame.services.tool_context import ToolContext
from textgame.tools.builtin.m2 import build_m2_tool_registry
from textgame.tools.decorators import tool
from textgame.tools.registry import ToolRegistry

CRAFT_TOOL_NAME: str = "craft_item"

#: 第 7 章 §7.3 矩阵：craft_item 对 player/npc:blacksmith/system 可见，默认 npc 不可见。
_CRAFT_ITEM_SCOPE: frozenset[str] = frozenset({"player", "system", "npc:blacksmith"})
_DECLARATIVE_ONLY = "M4 工具仅声明 spec；执行必须经 ToolGate.dispatch 路由到 CraftService"
_RecipeIdArg = Annotated[str, Field(min_length=1, max_length=80)]
_FreeDescriptionArg = Annotated[str, Field(min_length=1, max_length=200)]


def _declarative(reason: str = _DECLARATIVE_ONLY) -> Result:
    """占位实现：直接调用注册表不会执行（gate 才是唯一执行路由）。"""
    return Result.fail(ErrorCode.NOT_ALLOWED, reason)


@tool(
    actor_scope=_CRAFT_ITEM_SCOPE,
    side_effect=SideEffect.DESTRUCTIVE,
    resolution_policy=ResolutionPolicy.DETERMINISTIC,
)
def craft_item(
    ctx: ToolContext,
    mode: Literal["recipe", "free"] = "recipe",
    recipe_id: _RecipeIdArg | None = None,
    free_description: _FreeDescriptionArg | None = None,
) -> Result:
    """按配方制造物品（M4 确定性路径；自由制造在 M5 接线）。"""
    return _declarative()


def build_game_tool_registry() -> ToolRegistry:
    """游戏完整工具注册表：M2 六工具 + M4 craft_item（M2 注册表本身不变）。"""
    registry = build_m2_tool_registry()
    registry.register(craft_item)
    return registry


__all__ = ["CRAFT_TOOL_NAME", "build_game_tool_registry", "craft_item"]
