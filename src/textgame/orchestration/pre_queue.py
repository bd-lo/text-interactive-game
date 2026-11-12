"""R-19 pre-queue：自由制造草稿在进入 Resolver 之前生成一次。

背景（R5 遗留 R-19「Resolver 内 LLM」）：自由制造的 ``CraftDraft`` 原先在 ``Resolver`` 的
``services.call`` 链路内由 ``CraftService._craft_free`` 调用 LLM 生成；CAS 冲突重试会重复
调用、重复计费。目标架构（本模块 + ``TurnOrchestrator``）：

1. 编排层解析出玩家 ``craft_item(mode="free")`` 意向后、**在构造提案之后、进入
   Resolver/CAS 重试循环之前**，经工具门的 pre-queue 端口预生成一次草稿（至多一次生成，
   Agent 内部至多一次 Schema 修复）；
2. 草稿「成功载荷」或「失败载荷」作为**数据**随玩家提案交给 ``Resolver``；
   ``Resolver`` → ``ToolGate`` → ``CraftService`` 执行路径只做纯校验/纯计算（零 LLM）；
3. CAS 重试复用同一载荷，不重调模型；失败载荷直接以稳定错误码拒绝，零事件零写入。

开关关闭或未注入生成器时 ``free_craft_ready`` 为 ``False``，本模块返回 ``None``：编排层
不预生成，保持 R-19 之前的旧路径（Resolver 内服务直接返回 NOT_ALLOWED、零 LLM），默认
关闭/离线行为逐字不变。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from textgame.domain.models import GameState
from textgame.domain.results import ErrorCode, Result
from textgame.services.tool_context import ToolContext

__all__ = [
    "CRAFT_TOOL_NAME",
    "FREE_CRAFT_MODE",
    "FreeCraftPreparerLike",
    "is_free_craft_proposal",
    "prepare_free_craft_draft",
]

#: 制造工具名（第 7 章 §7.3 矩阵）与自由制造模式值（第 13 章 §13.2）。
CRAFT_TOOL_NAME = "craft_item"
FREE_CRAFT_MODE = "free"


class FreeCraftPreparerLike(Protocol):
    """编排层依赖的 pre-queue 端口（``tools.gate.ToolGate`` 结构实现；未装配时零影响）。"""

    @property
    def free_craft_ready(self) -> bool:
        """自由制造是否可预生成（开关开启且注入了生成器）。"""
        ...

    def prepare_free_craft(
        self,
        *,
        args: dict[str, Any],
        ctx: ToolContext,
        state: GameState,
    ) -> Result:
        """生成一次自由制造草稿；成功/失败草稿收敛在 ``data["prepared"]``。"""
        ...


def is_free_craft_proposal(tool: str, args: Mapping[str, Any]) -> bool:
    """该提案是否为自由制造（``craft_item`` + ``mode="free"``）。"""
    return tool == CRAFT_TOOL_NAME and args.get("mode") == FREE_CRAFT_MODE


def prepare_free_craft_draft(
    *,
    gate: object,
    ctx: ToolContext,
    state: GameState,
    tool: str,
    args: Mapping[str, Any],
) -> dict[str, Any] | None:
    """在进入 Resolver 前预生成自由制造草稿；不适用时返回 ``None``（旧路径零影响）。

    返回载荷（dict，JSON 可序列化，作为数据随玩家提案传给 Resolver）：

    - 成功 ``{"ok": True, "draft": {...}, "prompt_version": "craft@v1", "repaired": bool}``；
    - 失败 ``{"ok": False, "error_code": "SCHEMA_VIOLATION"|..., "message": "..."}``；
      执行路径以同一错误码拒绝、零事件，不重试 LLM。

    仅当工具门实现 pre-queue 端口且 ``free_craft_ready`` 为真时生成；否则返回 ``None``，
    编排层走旧路径（``Resolver`` 内服务直接返回 NOT_ALLOWED，零 LLM）。
    """
    if not is_free_craft_proposal(tool, args):
        return None
    prepare = getattr(gate, "prepare_free_craft", None)
    if not callable(prepare) or not bool(getattr(gate, "free_craft_ready", False)):
        return None
    try:
        result: Result = prepare(args=dict(args), ctx=ctx, state=state)
    except Exception:  # 预生成异常不得中断回合：按失败草稿处理（零事件、走 Resolver 拒绝）
        return {
            "ok": False,
            "error_code": ErrorCode.INTERNAL.value,
            "message": "free craft preparation failed",
        }
    if not result.ok:
        code = result.error_code or ErrorCode.INTERNAL
        return {"ok": False, "error_code": code.value, "message": result.message or ""}
    prepared = (result.data or {}).get("prepared")
    if not isinstance(prepared, dict):
        return {
            "ok": False,
            "error_code": ErrorCode.INTERNAL.value,
            "message": "free craft preparation returned no draft",
        }
    return prepared
