"""制造服务（M4-016）：确定性配方路径 + 只读配方列表（第 6 章 §6.3、第 13 章 §13.2-§13.6）。

服务层只做「解析 ctx → 前置校验 → 调领域纯函数 → 组装草稿事件」，不写库、不 commit、
不调 GameEngine（第 13 章 §13.5）：提交由编排层交给唯一写入者。材料/工位/工具校验在
本层完成；数值（成功率/质量/消耗/副产物）唯一由 domain/crafting.py 的纯函数计算。
自由制造（CraftMode.FREE）在 M4 返回 NOT_ALLOWED（D-M4-3，017 接入生成路径）。
"""

from __future__ import annotations

from typing import Any

from textgame.domain.crafting import (
    CraftMode,
    CraftRequest,
    CraftStateSlice,
    Recipe,
    build_craft_state_slice,
    calculate_craft_result,
    instantiate_craft_result,
)
from textgame.domain.events import Event, EventType
from textgame.domain.models import GameState
from textgame.domain.results import ErrorCode, Result
from textgame.domain.rules.dice import DerivedRandom
from textgame.services.recipe_catalog import RecipeCatalog
from textgame.services.tool_context import ToolContext

__all__ = ["CraftService"]

_CRAFT_METHOD = "craft_item"


class CraftService:
    """制造服务：RecipeCatalog 注入；generator/free_craft_enabled 为 017 自由制造预留。"""

    def __init__(
        self,
        recipes: RecipeCatalog,
        *,
        generator: Any = None,
        free_craft_enabled: bool = False,
    ) -> None:
        """注入配方目录与（M5/017 才使用的）生成器/开关；构造不读库、不联网。"""
        self._recipes = recipes
        self._generator = generator
        self._free_craft_enabled = free_craft_enabled

    def list_recipes(self, ctx: ToolContext) -> Result:
        """只读配方摘要（零事件）：data={"recipes": [...]}，按 recipe_id 升序。"""
        recipes = [
            {
                "recipe_id": recipe.recipe_id,
                "name": recipe.name,
                "version": recipe.version,
                "output_template_id": recipe.output_template_id,
                "station": recipe.station,
            }
            for recipe in self._recipes.all()
        ]
        return Result.success(data={"recipes": recipes})

    def craft(
        self,
        ctx: ToolContext,
        state: GameState,
        req: CraftRequest,
        *,
        idempotency_key: str,
    ) -> Result:
        """制造一次（RECIPE 确定性路径）：返回 Result(ok=True, data={"craft": ...}, events=[草稿])。

        失败（未知配方/actor 不存在/工位不符/材料或工具不足）返回 Result(ok=False) 且
        events 为空（失败零状态变更）；自由制造 M4 返回 NOT_ALLOWED（D-M4-3）。
        """
        if req.mode is CraftMode.FREE:
            return Result.fail(
                ErrorCode.NOT_ALLOWED,
                "free craft is disabled in M4 (D-M4-3; 017/M5 接入生成路径)",
            )
        if req.recipe_id is None:  # CraftRequest 已交叉校验，防御性兜底
            return Result.fail(ErrorCode.INVALID_INPUT, "recipe_id is required for recipe craft")
        try:
            recipe = self._recipes.require(req.recipe_id)
        except KeyError:
            return Result.fail(ErrorCode.NOT_FOUND, f"unknown recipe: {req.recipe_id!r}")
        if ctx.actor_id not in state.characters:
            return Result.fail(ErrorCode.NOT_FOUND, f"actor {ctx.actor_id!r} not found")
        try:
            state_slice = build_craft_state_slice(state, ctx.actor_id)
        except ValueError as exc:
            return Result.fail(ErrorCode.INVALID_INPUT, str(exc))
        rejected = self._check_recipe_preconditions(recipe, state_slice)
        if rejected is not None:
            return rejected

        rng_seed = DerivedRandom.derive_seed(
            ctx.save_seed,
            ctx.turn_id,
            ctx.actor_id,
            _CRAFT_METHOD,
            state_slice.next_seq,
        )
        result = calculate_craft_result(state_slice, recipe, ctx.actor_id, rng_seed)
        result, produced_items = instantiate_craft_result(
            result,
            recipe,
            save_seed=ctx.save_seed,
            turn_id=ctx.turn_id,
            actor_id=ctx.actor_id,
            craft_kind=EventType.CRAFT_DETERMINISTIC.value,
            event_seq=state_slice.next_seq,
        )
        draft_event = Event(
            turn_id=ctx.turn_id,
            actor_id=ctx.actor_id,
            method=_CRAFT_METHOD,
            event_type=EventType.CRAFT_DETERMINISTIC,
            params={
                "mode": CraftMode.RECIPE.value,
                "recipe_id": recipe.recipe_id,
                "recipe_version": recipe.version,
            },
            result={
                "craft": result.model_dump(mode="json"),
                "produced_items": [item.model_dump(mode="json") for item in produced_items],
            },
        )
        # 引用 idempotency_key 仅作服务层签名契约（幂等由编排层 turn 键与 events 复合主键保证）。
        del idempotency_key
        return Result.success(data={"craft": result}, events=[draft_event])

    @staticmethod
    def _check_recipe_preconditions(recipe: Recipe, state: CraftStateSlice) -> Result | None:
        """材料/工具/工位前置校验（第 13 章 §13.4；技能校验 M4 后移，D-M4-2）。

        返回 None 表示通过；否则返回带稳定错误码的 Result.fail：
        工位不符/缺工具 → NOT_ALLOWED；材料不在背包 → NOT_FOUND；数量不足 → OUT_OF_RANGE。
        """
        station = recipe.station
        if station is not None and station != state.station_id:
            return Result.fail(
                ErrorCode.NOT_ALLOWED,
                f"craft station mismatch: need {station!r} (current {state.station_id!r})",
            )
        for item in recipe.inputs:
            available = state.inventory.get(item.template_id, 0)
            if item.role == "tool":
                if available < item.qty:
                    return Result.fail(
                        ErrorCode.NOT_ALLOWED,
                        f"missing tool: {item.template_id!r}",
                    )
                continue
            if available == 0:
                return Result.fail(
                    ErrorCode.NOT_FOUND,
                    f"material {item.template_id!r} is not in inventory",
                )
            if available < item.qty:
                return Result.fail(
                    ErrorCode.OUT_OF_RANGE,
                    f"insufficient material {item.template_id!r}: "
                    f"need {item.qty}, have {available}",
                )
        return None
