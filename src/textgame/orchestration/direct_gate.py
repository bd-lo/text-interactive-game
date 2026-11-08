"""005 的简单工具门 ``DirectGate``（002/006 的 ``tools/gate.py::ToolGate`` 接入前占位）。

接口与 006 的 ``ToolGate`` 保持一致：``dispatch(*, tool, args, ctx, state) -> Result``，
编排层只依赖 ``ToolGateLike`` Protocol，因此 006 可以原样替换本实现而无需改
``TurnOrchestrator``。M2 裁剪：不经 ProposalQueue/Resolver（单角色无并发），但写操作
仍必须过 gate → ActionService（L0 自检）→ GameEngine（契约 §4.7）。
"""

from __future__ import annotations

from typing import Any, Protocol, cast, get_args

from textgame.domain.models import GameState
from textgame.domain.results import ErrorCode, Result
from textgame.domain.types import Direction
from textgame.services.action_service import ActionService
from textgame.services.query_service import QueryService
from textgame.services.tool_context import ToolContext

_DIRECTIONS: frozenset[str] = frozenset(get_args(Direction))
_READ_ONLY_TOOLS: frozenset[str] = frozenset({"look", "get_room_view", "get_inventory"})
_WRITE_TOOLS: frozenset[str] = frozenset({"move_to", "take_item", "use_item", "talk_to"})
_ALLOWED_TOOLS: frozenset[str] = _READ_ONLY_TOOLS | _WRITE_TOOLS
_GATE_ACTOR_TAGS: frozenset[str] = frozenset({"player", "system"})


class ToolGateLike(Protocol):
    """编排层依赖的工具门端口；006 的 ``ToolGate`` 结构一致，可替换注入。"""

    def dispatch(
        self,
        *,
        tool: str,
        args: dict[str, Any],
        ctx: ToolContext,
        state: GameState,
    ) -> Result:
        """执行工具：返回 ``Result``（READ_ONLY 文本 / 写摘要 + 草稿事件）。"""
        ...


def _require_exact_keys(tool: str, args: dict[str, Any], required: frozenset[str]) -> None:
    """参数必须恰好是 ``required``：缺参/多参都按 ``INVALID_INPUT`` 处理。"""
    missing = required - set(args)
    extra = set(args) - required
    if missing:
        raise ValueError(f"工具 {tool} 缺少参数: {sorted(missing)}")
    if extra:
        raise ValueError(f"工具 {tool} 收到未知参数: {sorted(extra)}")


def _validate_args(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """按 006 的 JSON Schema 口径做一次最小参数校验（返回规范化参数）。"""
    if tool in _READ_ONLY_TOOLS:
        _require_exact_keys(tool, args, frozenset())
        return {}
    if tool == "move_to":
        _require_exact_keys(tool, args, frozenset({"direction"}))
        direction = args["direction"]
        if not isinstance(direction, str) or direction not in _DIRECTIONS:
            raise ValueError(f"工具 move_to 的方向非法: {direction!r}")
        return {"direction": direction}
    if tool in {"take_item", "use_item"}:
        _require_exact_keys(tool, args, frozenset({"item_id"}))
        item_id = args["item_id"]
        if not isinstance(item_id, str) or not item_id:
            raise ValueError(f"工具 {tool} 的 item_id 必须是非空字符串")
        return {"item_id": item_id}
    if tool == "talk_to":
        _require_exact_keys(tool, args, frozenset({"npc_id", "topic"}))
        npc_id = args["npc_id"]
        topic = args["topic"]
        if not isinstance(npc_id, str) or not npc_id:
            raise ValueError("工具 talk_to 的 npc_id 必须是非空字符串")
        if not isinstance(topic, str) or not topic.strip():
            raise ValueError("工具 talk_to 的 topic 必须是非空字符串")
        return {"npc_id": npc_id, "topic": topic}
    raise ValueError(f"未知工具: {tool}")


class DirectGate:
    """005 工具门：白名单 + actor_scope + 参数校验 + 读/写路由。"""

    def __init__(self, action: ActionService, query: QueryService) -> None:
        """``action`` 提供写方法与 look，``query`` 提供只读背包视图。"""
        self._action = action
        self._query = query

    def dispatch(
        self,
        *,
        tool: str,
        args: dict[str, Any],
        ctx: ToolContext,
        state: GameState,
    ) -> Result:
        """按工具名路由；未知工具 ``NOT_FOUND``、越权 ``NOT_ALLOWED``、参数非法 ``INVALID_INPUT``。"""
        if tool not in _ALLOWED_TOOLS:
            return Result.fail(ErrorCode.NOT_FOUND, f"未知工具: {tool}")
        if not (ctx.actor_tags & _GATE_ACTOR_TAGS):
            return Result.fail(ErrorCode.NOT_ALLOWED, "actor_scope 不允许当前角色调用该工具")
        try:
            validated = _validate_args(tool, args)
        except ValueError as exc:
            return Result.fail(ErrorCode.INVALID_INPUT, str(exc))
        if tool in {"look", "get_room_view"}:
            return self._action.look(ctx, state)
        if tool == "get_inventory":
            return Result.success(data=self._query.get_inventory(state, ctx.actor_id))
        if tool == "move_to":
            return self._action.move_to(
                ctx, state, direction=cast(Direction, validated["direction"])
            )
        if tool == "take_item":
            return self._action.take_item(ctx, state, item_id=validated["item_id"])
        if tool == "use_item":
            return self._action.use_item(ctx, state, item_id=validated["item_id"])
        return self._action.talk_to(
            ctx,
            state,
            npc_id=validated["npc_id"],
            topic=validated["topic"],
        )


__all__ = ["DirectGate", "ToolGateLike"]
