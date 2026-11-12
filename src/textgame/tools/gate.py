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

from pydantic import ValidationError

from textgame.domain.crafting import CraftRequest
from textgame.domain.models import GameState, SideEffect
from textgame.domain.results import ErrorCode, Result
from textgame.domain.types import Direction, ItemId, NpcId
from textgame.services.tool_context import ToolContext
from textgame.tools.registry import ToolRegistry


class QueryServicePort(Protocol):
    """gate 用到的只读查询方法 (契约 §4.2 子集)；零事件、不写库。"""

    def get_inventory(self, state: GameState, actor_id: str) -> dict[str, Any]: ...


class ActionServicePort(Protocol):
    """gate 用到的服务方法 (契约 §4.3)：``look``/``inspect`` 只读；其余写方法只产草稿 ``Event``。"""

    def look(self, ctx: ToolContext, state: GameState) -> Result: ...

    def inspect(
        self,
        ctx: ToolContext,
        state: GameState,
        *,
        item_id: str | None = None,
        npc_id: str | None = None,
    ) -> Result: ...

    def move_to(self, ctx: ToolContext, state: GameState, *, direction: Direction) -> Result: ...

    def take_item(self, ctx: ToolContext, state: GameState, *, item_id: ItemId) -> Result: ...

    def use_item(self, ctx: ToolContext, state: GameState, *, item_id: ItemId) -> Result: ...

    def talk_to(
        self, ctx: ToolContext, state: GameState, *, npc_id: NpcId, topic: str
    ) -> Result: ...

    # R7 行动自由度动作包（契约 §1/§3）：均只产草稿 ``Event``，不提交、不写库。
    def drop_item(self, ctx: ToolContext, state: GameState, *, item_id: ItemId) -> Result: ...

    def give_item(
        self, ctx: ToolContext, state: GameState, *, item_id: ItemId, npc_id: NpcId
    ) -> Result: ...

    def attack(self, ctx: ToolContext, state: GameState, *, npc_id: NpcId) -> Result: ...

    def improvise(self, ctx: ToolContext, state: GameState, *, action_text: str) -> Result: ...


class CraftServicePort(Protocol):
    """gate 用到的制造服务方法（第 13 章 §13.5）：只产草稿 ``Event``，不提交。

    R-19 加法：``prepared_craft`` 为编排层 pre-queue 载荷（进入 Resolver 前生成一次），
    提供时 FREE 路径纯执行、零 LLM；``prepare_free_craft`` / ``free_craft_ready`` 由
    pre-queue 端口调用（开关关闭/未注入生成器时 ``ready=False``，编排层保持旧路径）。
    """

    @property
    def free_craft_ready(self) -> bool: ...

    def prepare_free_craft(
        self,
        ctx: ToolContext,
        state: GameState,
        req: CraftRequest,
    ) -> Result: ...

    def craft(
        self,
        ctx: ToolContext,
        state: GameState,
        req: CraftRequest,
        *,
        idempotency_key: str,
        prepared_craft: dict[str, Any] | None = None,
    ) -> Result: ...


class ToolGate:
    """工具门：先校验存在性/权限/Schema，再按 ``side_effect`` 路由到服务层。"""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        query: QueryServicePort,
        action: ActionServicePort,
        craft: CraftServicePort | None = None,
    ) -> None:
        """注入工具注册表与只读/写/制造服务端口；不持有仓储与引擎。"""
        self._registry = registry
        self._query = query
        self._action = action
        self._craft = craft

    # ------------------------------------------------------------------
    # R-19 pre-queue 端口（编排层在进入 Resolver 前调用；零事件）
    # ------------------------------------------------------------------
    @property
    def free_craft_ready(self) -> bool:
        """自由制造预生成是否可用（未装配制造服务/开关关闭/无生成器 → ``False``）。

        ``False`` 时编排层不预生成，保持 R-19 之前的旧路径（Resolver 内服务直接返回
        NOT_ALLOWED，零 LLM 调用），默认关闭/离线行为逐字不变。
        """
        if self._craft is None:
            return False
        return self._craft.free_craft_ready

    def prepare_free_craft(
        self,
        *,
        args: dict[str, Any],
        ctx: ToolContext,
        state: GameState,
    ) -> Result:
        """R-19 pre-queue：在进入 Resolver 前生成一次自由制造草稿（零事件、至多一次 LLM）。

        只由 ``TurnOrchestrator`` 在多角色链路构造玩家提案后调用；成功/失败草稿都收敛在
        ``Result.success(data={"prepared": 载荷})`` 里（失败零事件），由编排层随提案交给
        Resolver 纯执行路径。未装配制造服务/参数非法 → 稳定失败码（编排层按失败草稿处理）。
        """
        if self._craft is None:
            return Result.fail(ErrorCode.NOT_ALLOWED, "craft service is not wired")
        try:
            request = CraftRequest.model_validate(
                {
                    "mode": args.get("mode", "recipe"),
                    "recipe_id": args.get("recipe_id"),
                    "free_description": args.get("free_description"),
                }
            )
        except ValidationError:
            return Result.fail(
                ErrorCode.INVALID_INPUT, "craft args do not form a valid CraftRequest"
            )
        return self._craft.prepare_free_craft(ctx, state, request)

    def dispatch(
        self,
        *,
        tool: str,
        args: dict[str, Any],
        ctx: ToolContext,
        state: GameState,
        prepared_craft: dict[str, Any] | None = None,
    ) -> Result:
        """校验并路由一次工具调用；任何失败都返回 ``Result.fail`` 且零事件。

        ``prepared_craft``（R-19）为编排层 pre-queue 生成的自由制造载荷，仅由 Resolver
        适配器透传（默认 ``None`` = 旧行为：服务层自行决定是否调用生成器）。
        """
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
            return self._dispatch_read(tool, params, ctx, state)
        return self._dispatch_write(tool, params, ctx, state, prepared_craft)

    def _dispatch_read(
        self, tool: str, params: dict[str, Any], ctx: ToolContext, state: GameState
    ) -> Result:
        """只读路由：返回 ``Result.success(data=...)``、零事件。

        ``get_room_view`` 走 ``ActionService.look`` 拿一屏房间文本（``data={"text": ...}``，
        编排层只读叙事可直接使用）；``get_inventory`` 走 ``QueryService.get_inventory``；
        ``inspect_item`` 走 ``ActionService.inspect`` 拿物品/NPC 描述（D6，零事件）。
        """
        try:
            if tool == "get_room_view":
                return self._action.look(ctx, state)
            if tool == "get_inventory":
                return Result.success(data=self._query.get_inventory(state, ctx.actor_id))
            if tool == "inspect_item":
                return self._action.inspect(
                    ctx,
                    state,
                    item_id=cast(str | None, params.get("item_id")),
                    npc_id=cast(str | None, params.get("npc_id")),
                )
        except Exception:  # 不向调用方暴露内部异常细节 (第 7 章 7.5)
            return Result.fail(ErrorCode.INTERNAL, "tool dispatch failed")
        return Result.fail(ErrorCode.NOT_ALLOWED, f"unsupported read tool: {tool}")

    def _dispatch_write(
        self,
        tool: str,
        params: dict[str, Any],
        ctx: ToolContext,
        state: GameState,
        prepared_craft: dict[str, Any] | None = None,
    ) -> Result:
        """写路由：调 ``ActionService``/``CraftService`` 拿草稿事件；提交由编排层负责。"""
        try:
            if tool == "craft_item":
                return self._dispatch_craft(params, ctx, state, prepared_craft)
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
            # R7 行动自由度四工具（契约 §1/§3）：gate 只做路由，L0/投影由服务与 reducer 负责。
            if tool == "attack":
                return self._action.attack(ctx, state, npc_id=cast(NpcId, params["npc_id"]))
            if tool == "drop_item":
                return self._action.drop_item(ctx, state, item_id=cast(ItemId, params["item_id"]))
            if tool == "give_item":
                return self._action.give_item(
                    ctx,
                    state,
                    item_id=cast(ItemId, params["item_id"]),
                    npc_id=cast(NpcId, params["npc_id"]),
                )
            if tool == "improvise":
                return self._action.improvise(
                    ctx, state, action_text=cast(str, params["action_text"])
                )
        except Exception:  # 服务层异常统一转 INTERNAL，零事件
            return Result.fail(ErrorCode.INTERNAL, "tool dispatch failed")
        return Result.fail(ErrorCode.NOT_ALLOWED, f"unsupported destructive tool: {tool}")

    def _dispatch_craft(
        self,
        params: dict[str, Any],
        ctx: ToolContext,
        state: GameState,
        prepared_craft: dict[str, Any] | None = None,
    ) -> Result:
        """craft_item 路由：组装 ``CraftRequest``（校验失败→INVALID_INPUT）后调 CraftService。"""
        if self._craft is None:
            return Result.fail(ErrorCode.NOT_ALLOWED, "craft service is not wired")
        try:
            request = CraftRequest.model_validate(
                {
                    "mode": params.get("mode", "recipe"),
                    "recipe_id": params.get("recipe_id"),
                    "free_description": params.get("free_description"),
                }
            )
        except ValidationError:
            return Result.fail(
                ErrorCode.INVALID_INPUT, "craft args do not form a valid CraftRequest"
            )
        return self._craft.craft(
            ctx,
            state,
            request,
            idempotency_key=ctx.idempotency_key,
            prepared_craft=prepared_craft,
        )
