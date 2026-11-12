"""R7 行动自由度内置工具声明：``attack`` / ``drop_item`` / ``give_item`` / ``improvise``。

本模块只承载 ``ToolSpec``/JSON Schema/``actor_scope``/``side_effect``/``resolution_policy``：
工具函数不持有 ``GameState``、不直接执行服务调用（第 7 章 §7.1 不变量 5）；执行由
``tools/gate.py::ToolGate.dispatch`` 校验后路由到 ``ActionService``（契约 R7 §1/§3）。

只由 ``build_game_tool_registry()`` 注册（``build_m2_tool_registry()`` 不注册这四个工具），
因此旧 m2/m3/m4 golden 不产生新事件、逐字不变；``improvise`` 声明 ``AGENT_ASSISTED``，
由 ``ToolRegistry.agent_assisted_tools()`` 派生出 ``RuleEngine`` 的 needs_judge 生产点。
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from textgame.domain.models import ResolutionPolicy, SideEffect
from textgame.domain.results import ErrorCode, Result
from textgame.services.tool_context import ToolContext
from textgame.tools.decorators import tool

#: R7 动作包工具名（契约 §1；顺序即注册顺序）。
FREEDOM_TOOL_NAMES: tuple[str, ...] = ("attack", "drop_item", "give_item", "improvise")

#: 契约 §1：四个工具均只对 player/system 可见（NPC 不可见，不放宽既有权限）。
_FREEDOM_SCOPE: frozenset[str] = frozenset({"player", "system"})
_DECLARATIVE_ONLY = "R7 工具仅声明 spec；执行必须经 ToolGate.dispatch 路由到 ActionService"
_IdArg = Annotated[str, Field(min_length=1, max_length=64)]
_ActionTextArg = Annotated[str, Field(min_length=1, max_length=200)]


def _declarative() -> Result:
    """占位实现：直接调用注册表不会执行（gate 才是唯一执行路由）。"""
    return Result.fail(ErrorCode.NOT_ALLOWED, _DECLARATIVE_ONLY)


@tool(
    actor_scope=_FREEDOM_SCOPE,
    side_effect=SideEffect.DESTRUCTIVE,
    resolution_policy=ResolutionPolicy.DETERMINISTIC,
)
def attack(ctx: ToolContext, npc_id: _IdArg) -> Result:
    """攻击同房间的一个目标（一次判定：命中则对方退出场景）。"""
    return _declarative()


@tool(
    actor_scope=_FREEDOM_SCOPE,
    side_effect=SideEffect.DESTRUCTIVE,
    resolution_policy=ResolutionPolicy.DETERMINISTIC,
)
def drop_item(ctx: ToolContext, item_id: _IdArg) -> Result:
    """把背包里的一件物品放到当前房间地上。"""
    return _declarative()


@tool(
    actor_scope=_FREEDOM_SCOPE,
    side_effect=SideEffect.DESTRUCTIVE,
    resolution_policy=ResolutionPolicy.DETERMINISTIC,
)
def give_item(ctx: ToolContext, item_id: _IdArg, npc_id: _IdArg) -> Result:
    """把背包里的一件物品交给同房间的一个人。"""
    return _declarative()


@tool(
    actor_scope=_FREEDOM_SCOPE,
    side_effect=SideEffect.DESTRUCTIVE,
    resolution_policy=ResolutionPolicy.AGENT_ASSISTED,
)
def improvise(ctx: ToolContext, action_text: _ActionTextArg) -> Result:
    """做一个规则没有覆盖的临场动作（由裁判确认是否成立）。"""
    return _declarative()


#: 四个工具函数（``build_game_tool_registry`` 按此顺序注册；导出供测试断言）。
FREEDOM_TOOLS = (attack, drop_item, give_item, improvise)

__all__ = [
    "FREEDOM_TOOLS",
    "FREEDOM_TOOL_NAMES",
    "attack",
    "drop_item",
    "give_item",
    "improvise",
]
