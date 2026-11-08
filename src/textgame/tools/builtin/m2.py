"""M2 内置工具声明（第 7 章 §7.3 矩阵六工具；``use_item`` 为 M2 新增动作）。

本模块**只承载** ``ToolSpec``/JSON Schema/``actor_scope``/``side_effect``：工具函数不持有
``GameState``、不直接执行服务调用 (第 7 章 §7.1 不变量 5)；执行由
``tools/gate.py::ToolGate.dispatch`` 校验后路由到 ``ActionService``/``QueryService``
(契约 §4.7 的 M2 裁剪：不经 ProposalQueue/Resolver)。

可见性 (第 7 章 §7.3 子集)：玩家与系统可见；NPC 标签在 M2 不获得这些工具。
``use_item`` 未出现在第 7 章初始矩阵，属 M2 新增动作，需在进度文档登记。
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from textgame.domain.models import SideEffect
from textgame.domain.results import ErrorCode, Result
from textgame.domain.types import Direction
from textgame.services.tool_context import ToolContext
from textgame.tools.decorators import tool
from textgame.tools.registry import ToolRegistry

M2_TOOL_NAMES: tuple[str, ...] = (
    "get_room_view",
    "get_inventory",
    "move_to",
    "take_item",
    "use_item",
    "talk_to",
)
_M2_ACTOR_SCOPE: frozenset[str] = frozenset({"player", "system"})
_DECLARATIVE_ONLY = "M2 工具仅声明 spec；执行必须经 ToolGate.dispatch 路由到服务层"
_IdArg = Annotated[str, Field(min_length=1, max_length=64)]


def _declarative(reason: str = _DECLARATIVE_ONLY) -> Result:
    """占位实现：直接调用注册表不会执行（gate 才是唯一执行路由）。"""
    return Result.fail(ErrorCode.NOT_ALLOWED, reason)


@tool(actor_scope=_M2_ACTOR_SCOPE, side_effect=SideEffect.READ_ONLY)
def get_room_view(ctx: ToolContext) -> Result:
    """查看当前所在房间的可见信息。"""
    return _declarative()


@tool(actor_scope=_M2_ACTOR_SCOPE, side_effect=SideEffect.READ_ONLY)
def get_inventory(ctx: ToolContext) -> Result:
    """查看当前角色背包。"""
    return _declarative()


@tool(actor_scope=_M2_ACTOR_SCOPE, side_effect=SideEffect.DESTRUCTIVE)
def move_to(ctx: ToolContext, direction: Direction) -> Result:
    """向一个方向移动。"""
    return _declarative()


@tool(actor_scope=_M2_ACTOR_SCOPE, side_effect=SideEffect.DESTRUCTIVE)
def take_item(ctx: ToolContext, item_id: _IdArg) -> Result:
    """从当前房间拿起物品。"""
    return _declarative()


@tool(actor_scope=_M2_ACTOR_SCOPE, side_effect=SideEffect.DESTRUCTIVE)
def use_item(ctx: ToolContext, item_id: _IdArg) -> Result:
    """使用背包中的物品（M2 新增动作）。"""
    return _declarative()


@tool(actor_scope=_M2_ACTOR_SCOPE, side_effect=SideEffect.DESTRUCTIVE)
def talk_to(
    ctx: ToolContext,
    npc_id: _IdArg,
    topic: Annotated[str, Field(min_length=1, max_length=500)],
) -> Result:
    """与同房间 NPC 交谈。"""
    return _declarative()


_M2_TOOLS = (get_room_view, get_inventory, move_to, take_item, use_item, talk_to)


def build_m2_tool_registry() -> ToolRegistry:
    """构建并返回注册了 M2 六工具声明的 ``ToolRegistry`` (执行由 ``ToolGate`` 路由)。"""
    registry = ToolRegistry()
    for fn in _M2_TOOLS:
        registry.register(fn)
    return registry


__all__ = ["M2_TOOL_NAMES", "build_m2_tool_registry"]
