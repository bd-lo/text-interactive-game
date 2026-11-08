"""M2 内置工具声明（第 7 章 §7.3 矩阵六工具；``use_item`` 为 M2 新增动作）。

本模块**只承载** ``ToolSpec``/JSON Schema/``actor_scope``/``side_effect``：工具函数不持有
``GameState``、不直接执行服务调用 (第 7 章 §7.1 不变量 5)；执行由
``tools/gate.py::ToolGate.dispatch`` 校验后路由到 ``ActionService``/``QueryService``
(契约 §4.7 的 M2 裁剪：不经 ProposalQueue/Resolver)。

可见性逐工具对齐第 7 章 §7.3 矩阵（M3-011 硬化；此前六工具统一 ``{player, system}``，
NPC 一律不可见，与文档矩阵的 npc/npc:blacksmith 列不一致）：

- ``get_room_view`` / ``get_inventory`` / ``move_to``：文档矩阵 player/npc/npc:blacksmith/system
  均为 ✅ → ``{player, npc, system}``；``npc:blacksmith`` 通过 ``npc`` 通用标签继承
  （文档中黑匠的差异化权限只体现在本期未注册的 ``transfer_item``/``craft_item``，
  故不预置无用细标签，保持最小授权）。
- ``take_item``：文档矩阵对默认 ``npc``/``npc:blacksmith`` 为 ❌（保持）→ ``{player, system}``
  基础上仅加 ``npc:scavenger`` 细标签（M3「拾荒者抢物品」冲突演示，Lead 裁决的 M3 矩阵扩展，
  按第 7 章 §7.3 的差异化标签机制新增；默认 ``npc`` 标签不因此获得 ``take_item``）。
- ``talk_to``：文档矩阵对 npc 与 npc:blacksmith 均为 ❌ → ``{player, system}``。
- ``use_item``：未出现在第 7 章初始矩阵（M2 新增动作）→ 沿用 ``{player, system}``，不放宽；
  需在进度文档登记矩阵增列。

``actor_scope`` 只由服务端 ``ToolContext`` 推导，工具/提案参数里的任何 actor/scope 字样
都不参与授权判定 (第 15 章 §15.4)。
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
#: 第 7 章 §7.3 矩阵：只读类 + ``move_to`` 对 npc/npc:blacksmith 可见 (文档矩阵 ✅)。
#: ``npc:blacksmith`` 不单列：它同时持有 ``npc`` 通用标签 (第 7 章 §7.3 标签模型)，
#: 六工具上黑匠列与 npc 列取值相同，最小授权下不预置无工具使用的细标签。
_NPC_VISIBLE_SCOPE: frozenset[str] = frozenset({"player", "npc", "system"})
#: ``take_item``：文档矩阵对默认 ``npc``/``npc:blacksmith`` 为 ❌ (保持)；M3 冲突演示需要
#: 「拾荒者」NPC 抢同一物品，按第 7 章 §7.3「只有需要差异化的 NPC 才加细粒度标签」新增
#: ``npc:scavenger`` 细标签 (Lead 裁决的 M3 矩阵扩展，由 Lead 登记第 7 章活文档回写)。
_TAKE_ITEM_SCOPE: frozenset[str] = frozenset({"player", "system", "npc:scavenger"})
#: ``use_item``/``talk_to``：文档矩阵对 npc 与 npc:blacksmith 为 ❌；``use_item`` 文档未列
#: (M2 新增)，且 NPC 模型无背包 → 保守沿用 player/system，不扩细标签，不放宽。
_PLAYER_ONLY_WRITE_SCOPE: frozenset[str] = frozenset({"player", "system"})
_DECLARATIVE_ONLY = "M2 工具仅声明 spec；执行必须经 ToolGate.dispatch 路由到服务层"
_IdArg = Annotated[str, Field(min_length=1, max_length=64)]


def _declarative(reason: str = _DECLARATIVE_ONLY) -> Result:
    """占位实现：直接调用注册表不会执行（gate 才是唯一执行路由）。"""
    return Result.fail(ErrorCode.NOT_ALLOWED, reason)


@tool(actor_scope=_NPC_VISIBLE_SCOPE, side_effect=SideEffect.READ_ONLY)
def get_room_view(ctx: ToolContext) -> Result:
    """查看当前所在房间的可见信息。"""
    return _declarative()


@tool(actor_scope=_NPC_VISIBLE_SCOPE, side_effect=SideEffect.READ_ONLY)
def get_inventory(ctx: ToolContext) -> Result:
    """查看当前角色背包。"""
    return _declarative()


@tool(actor_scope=_NPC_VISIBLE_SCOPE, side_effect=SideEffect.DESTRUCTIVE)
def move_to(ctx: ToolContext, direction: Direction) -> Result:
    """向一个方向移动。"""
    return _declarative()


@tool(actor_scope=_TAKE_ITEM_SCOPE, side_effect=SideEffect.DESTRUCTIVE)
def take_item(ctx: ToolContext, item_id: _IdArg) -> Result:
    """从当前房间拿起物品。"""
    return _declarative()


@tool(actor_scope=_PLAYER_ONLY_WRITE_SCOPE, side_effect=SideEffect.DESTRUCTIVE)
def use_item(ctx: ToolContext, item_id: _IdArg) -> Result:
    """使用背包中的物品（M2 新增动作）。"""
    return _declarative()


@tool(actor_scope=_PLAYER_ONLY_WRITE_SCOPE, side_effect=SideEffect.DESTRUCTIVE)
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
