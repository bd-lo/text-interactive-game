"""ToolGate：白名单 + actor_scope + JSON Schema 校验 + 读/写路由 (契约 §4.7、第 7 章 7.4)。

错误码口径 (第 7 章 §7.5)：工具名不存在 → ``NOT_FOUND``；``actor_scope`` 越权 →
``NOT_ALLOWED``；参数不符合 JSON Schema → ``INVALID_INPUT``；服务层异常 → ``INTERNAL``。
上述失败全部零事件、零状态写入。

M2 单角色无并发，**不经 ProposalQueue/Resolver** (M3 引入)，但写操作仍必须过 gate：
READ_ONLY 工具路由到只读服务 (``get_room_view`` → ``ActionService.look`` 返回一屏房间文本；
``get_inventory`` → ``QueryService.get_inventory``)；DESTRUCTIVE 工具调 ``ActionService``
只拿草稿事件 (不提交、不写库)。提交仍由编排层交给唯一写入者 ``GameEngine``。

本模块不 import ``GameEngine``、不 import 任何具体实现；构造注入结构化 Protocol，
005 的 ``QueryService``/``ActionService`` 按契约 §4.2/§4.3 实现即可直接注入。
"""

from __future__ import annotations

from typing import Any, Protocol, cast

from textgame.domain.models import GameState, SideEffect
from textgame.domain.results import ErrorCode, Result
from textgame.domain.types import Direction, ItemId, NpcId
from textgame.services.tool_context import ToolContext
from textgame.tools.registry import ToolRegistry


class QueryServicePort(Protocol):
    """gate 用到的只读查询方法 (契约 §4.2 子集)；零事件、不写库。"""

    def get_inventory(self, state: GameState, actor_id: str) -> dict[str, Any]: ...


class ActionServicePort(Protocol):
    """gate 用到的服务方法 (契约 §4.3)：``look`` 只读；其余写方法只产草稿 ``Event``。"""

    def look(self, ctx: ToolContext, state: GameState) -> Result: ...

    def move_to(self, ctx: ToolContext, state: GameState, *, direction: Direction) -> Result: ...

    def take_item(self, ctx: ToolContext, state: GameState, *, item_id: ItemId) -> Result: ...

    def use_item(self, ctx: ToolContext, state: GameState, *, item_id: ItemId) -> Result: ...

    def talk_to(
        self, ctx: ToolContext, state: GameState, *, npc_id: NpcId, topic: str
    ) -> Result: ...


class ToolGate:
    """工具门：先校验存在性/权限/Schema，再按 ``side_effect`` 路由到服务层。"""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        query: QueryServicePort,
        action: ActionServicePort,
    ) -> None:
        """注入工具注册表与只读/写服务端口；不持有仓储与引擎。"""
        self._registry = registry
        self._query = query
        self._action = action

    def dispatch(
        self,
        *,
        tool: str,
        args: dict[str, Any],
        ctx: ToolContext,
        state: GameState,
    ) -> Result:
        """校验并路由一次工具调用；任何失败都返回 ``Result.fail`` 且零事件。"""
        try:
            spec = self._registry.get(tool)
        except KeyError:
            return Result.fail(ErrorCode.NOT_FOUND, f"unknown tool: {tool}")
        if not self._registry.is_visible(tool, ctx):
            return Result.fail(ErrorCode.NOT_ALLOWED, "actor_scope violation")

        validated = self._registry.validate_args(tool, args)
        if not validated.ok or not validated.data:
            return validated
        params = cast(dict[str, Any], validated.data["args"])
        if spec.side_effect is SideEffect.READ_ONLY:
            return self._dispatch_read(tool, ctx, state)
        return self._dispatch_write(tool, params, ctx, state)

    def _dispatch_read(self, tool: str, ctx: ToolContext, state: GameState) -> Result:
        """只读路由：返回 ``Result.success(data=...)``、零事件。

        ``get_room_view`` 走 ``ActionService.look`` 拿一屏房间文本（``data={"text": ...}``，
        编排层只读叙事可直接使用）；``get_inventory`` 走 ``QueryService.get_inventory``。
        """
        try:
            if tool == "get_room_view":
                return self._action.look(ctx, state)
            if tool == "get_inventory":
                return Result.success(data=self._query.get_inventory(state, ctx.actor_id))
        except Exception:  # 不向调用方暴露内部异常细节 (第 7 章 7.5)
            return Result.fail(ErrorCode.INTERNAL, "tool dispatch failed")
        return Result.fail(ErrorCode.NOT_ALLOWED, f"unsupported read tool: {tool}")

    def _dispatch_write(
        self,
        tool: str,
        params: dict[str, Any],
        ctx: ToolContext,
        state: GameState,
    ) -> Result:
        """写路由：调 ``ActionService`` 拿草稿事件；提交由编排层负责。"""
        try:
            if tool == "move_to":
                return self._action.move_to(
                    ctx, state, direction=cast(Direction, params["direction"])
                )
            if tool == "take_item":
                return self._action.take_item(ctx, state, item_id=cast(ItemId, params["item_id"]))
            if tool == "use_item":
                return self._action.use_item(ctx, state, item_id=cast(ItemId, params["item_id"]))
            if tool == "talk_to":
                return self._action.talk_to(
                    ctx,
                    state,
                    npc_id=cast(NpcId, params["npc_id"]),
                    topic=cast(str, params["topic"]),
                )
        except Exception:  # 服务层异常统一转 INTERNAL，零事件
            return Result.fail(ErrorCode.INTERNAL, "tool dispatch failed")
        return Result.fail(ErrorCode.NOT_ALLOWED, f"unsupported destructive tool: {tool}")
