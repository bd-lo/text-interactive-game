"""制造服务（M4-016/017）：确定性配方路径 + 自由制造校验路径 + 只读配方列表。

服务层只做「解析 ctx → 前置校验 → 调领域纯函数 → 组装草稿事件」，不写库、不 commit、
不调 GameEngine（第 13 章 §13.5）：提交由编排层交给唯一写入者。自由制造（CraftMode.FREE）
按第 13 章 §13.4 执行：生产默认关闭（D-M4-6），关闭或未注入生成器时返回 NOT_ALLOWED；
开启时由注入的 CraftGenerator（M4 用 FakeLLM 覆盖，M5 接真实 LLM）产出 CraftDraft，
服务层做 Schema + 状态一致性检查，再用同一 calculate_craft_result 重算数值，最终组装
CRAFT_GENERATED 草稿事件。任何失败都返回 Result(ok=False) 且零事件（失败零写入）。
"""

from __future__ import annotations

import hashlib

from pydantic import ValidationError

from textgame.domain.crafting import (
    CraftDraft,
    CraftItemView,
    CraftMode,
    CraftRequest,
    CraftResult,
    CraftStateSlice,
    CraftView,
    Recipe,
    build_craft_state_slice,
    calculate_craft_result,
    instantiate_craft_result,
    item_template_key,
    recipe_from_draft,
)
from textgame.domain.events import Event, EventType
from textgame.domain.hashing import params_hash
from textgame.domain.models import GameState, Item
from textgame.domain.results import ErrorCode, Result
from textgame.domain.rules.dice import DerivedRandom
from textgame.services.free_craft import FreeCraftSwitch
from textgame.services.ports import CraftGenerator
from textgame.services.recipe_catalog import RecipeCatalog
from textgame.services.tool_context import ToolContext

__all__ = ["CraftService"]

_CRAFT_METHOD = "craft_item"
_PROMPT_VERSION_DEFAULT = "craft@v1"


def _sha256_hex(text: str) -> str:
    """自由制造事件只落玩家原话的 sha256（不落原文，防注入/隐私，D-M4-5）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _validation_summary(exc: ValidationError) -> str:
    """把 pydantic 错误收敛为字段路径摘要（不回传完整 payload，防泄露）。"""
    parts = [".".join(str(part) for part in error["loc"]) or "<root>" for error in exc.errors()[:5]]
    return "; ".join(parts)


class CraftService:
    """制造服务：RecipeCatalog 注入；generator/free_craft 开关控制自由制造路径。"""

    def __init__(
        self,
        recipes: RecipeCatalog,
        *,
        generator: CraftGenerator | None = None,
        free_craft_enabled: bool = False,
        free_craft_switch: FreeCraftSwitch | None = None,
    ) -> None:
        """注入配方目录与自由制造生成器/开关；构造不读库、不联网。

        ``free_craft_switch`` 存在时优先使用共享运行期开关（网页设置页热切换）；
        否则用 ``free_craft_enabled`` 创建仅本服务可见的内部开关（保持旧测试/装配兼容）。
        """
        self._recipes = recipes
        self._generator = generator
        self._free_craft_switch = free_craft_switch or FreeCraftSwitch(enabled=free_craft_enabled)

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
        """制造一次：RECIPE 确定性路径 / FREE 生成路径；只返回草稿事件，不提交。

        失败（未知配方/前置校验失败/自由草案未过 Schema 或一致性检查/LLM 不可用）
        返回 Result(ok=False) 且 events 为空（失败零状态变更）。
        """
        if req.mode is CraftMode.FREE:
            return self._craft_free(ctx, state, req)
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
        rejected = self._check_recipe_preconditions(
            recipe, state_slice, self._template_display_names(state)
        )
        if rejected is not None:
            return rejected

        rng_seed = self._derive_rng_seed(ctx, state_slice)
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
        draft_event = self._build_event(
            ctx,
            event_type=EventType.CRAFT_DETERMINISTIC,
            params={
                "mode": CraftMode.RECIPE.value,
                "recipe_id": recipe.recipe_id,
                "recipe_version": recipe.version,
            },
            result=result,
            produced_items=produced_items,
        )
        # 幂等由编排层 turn 键与 events 复合主键保证；服务层签名保留 idempotency_key。
        del idempotency_key
        return Result.success(data={"craft": result}, events=[draft_event])

    # ------------------------------------------------------------------
    # 自由制造（017，第 13 章 §13.4）
    # ------------------------------------------------------------------
    def _craft_free(self, ctx: ToolContext, state: GameState, req: CraftRequest) -> Result:
        """自由制造校验路径：Schema → 状态一致性 → 折算临时 Recipe → 同一纯函数重算。"""
        if not self._free_craft_switch.enabled or self._generator is None:
            return Result.fail(
                ErrorCode.NOT_ALLOWED,
                "free craft is disabled (TEXTGAME_FREE_CRAFT_ENABLED=false or web Settings; D-M4-6)",
            )
        description = req.free_description
        if description is None:  # CraftRequest 已交叉校验，防御性兜底
            return Result.fail(
                ErrorCode.INVALID_INPUT, "free_description is required when mode=free"
            )
        if ctx.actor_id not in state.characters:
            return Result.fail(ErrorCode.NOT_FOUND, f"actor {ctx.actor_id!r} not found")
        try:
            state_slice = build_craft_state_slice(state, ctx.actor_id)
            view = self._build_craft_view(state, ctx.actor_id, state_slice, description)
        except ValueError as exc:
            return Result.fail(ErrorCode.INVALID_INPUT, str(exc))

        generated = self._generator.generate(
            view,
            turn_id=ctx.turn_id,
            save_seed=ctx.save_seed,
            idempotency_key=ctx.idempotency_key,
        )
        if not generated.ok:
            return generated  # SCHEMA_VIOLATION / LLM_UNAVAILABLE 原样透传，零事件
        generated_data = generated.data or {}
        parsed = generated_data.get("parsed")
        if not isinstance(parsed, dict):
            return Result.fail(ErrorCode.SCHEMA_VIOLATION, "craft generator returned no draft")
        try:
            draft = CraftDraft.model_validate(parsed)  # extra=forbid：LLM 数值字段直接拒绝
        except ValidationError as exc:
            return Result.fail(
                ErrorCode.SCHEMA_VIOLATION,
                f"craft draft failed schema validation: {_validation_summary(exc)}",
            )
        violations = self._validate_draft(draft, view, state_slice)
        if violations:
            return Result.fail(
                ErrorCode.NOT_ALLOWED,
                "free craft draft violates world state: " + "; ".join(violations),
            )

        recipe = recipe_from_draft(
            draft,
            view,
            save_seed=ctx.save_seed,
            turn_id=ctx.turn_id,
            actor_id=ctx.actor_id,
        )
        rng_seed = self._derive_rng_seed(ctx, state_slice)
        result = calculate_craft_result(
            state_slice, recipe, ctx.actor_id, rng_seed
        )  # 数值由框架重算
        result, produced_items = instantiate_craft_result(
            result,
            recipe,
            save_seed=ctx.save_seed,
            turn_id=ctx.turn_id,
            actor_id=ctx.actor_id,
            craft_kind=EventType.CRAFT_GENERATED.value,
            event_seq=state_slice.next_seq,
        )
        prompt_version = generated_data.get("prompt_version")
        draft_event = self._build_event(
            ctx,
            event_type=EventType.CRAFT_GENERATED,
            params={
                "mode": CraftMode.FREE.value,
                "free_description_sha256": _sha256_hex(description),
                "proposal_hash": params_hash(
                    {
                        "draft": draft.model_dump(mode="json"),
                        "free_description": description,
                    }
                ),
                "prompt_version": (
                    prompt_version
                    if isinstance(prompt_version, str) and prompt_version
                    else _PROMPT_VERSION_DEFAULT
                ),
            },
            result=result,
            produced_items=produced_items,
        )
        return Result.success(data={"craft": result}, events=[draft_event])

    def _build_craft_view(
        self,
        state: GameState,
        actor_id: str,
        state_slice: CraftStateSlice,
        free_description: str,
    ) -> CraftView:
        """从 GameState 投影 CraftAgent 的私有视图（背包/工位/已知配方/玩家原话）。"""
        character = state.characters[actor_id]
        grouped: dict[str, CraftItemView] = {}
        for item_id in character.inventory:
            item = state.items.get(item_id)
            if item is None:
                raise ValueError(f"inventory item {item_id!r} has no definition in state.items")
            template_id = item_template_key(item)
            if template_id is None:
                # 语义标签物品不是可制造材料（R-01），不出现在生成器私有视图里。
                continue
            existing = grouped.get(template_id)
            if existing is None:
                grouped[template_id] = CraftItemView(
                    item_id=item_id,
                    template_id=template_id,
                    name=item.name,
                    qty=1,
                )
            else:
                grouped[template_id] = existing.model_copy(update={"qty": existing.qty + 1})
        return CraftView(
            actor_id=state_slice.actor_id,
            inventory=list(grouped.values()),
            station_id=state_slice.station_id,
            known_recipe_ids=[recipe.recipe_id for recipe in self._recipes.all()],
            free_description=free_description,
        )

    @staticmethod
    def _validate_draft(
        draft: CraftDraft,
        view: CraftView,
        state_slice: CraftStateSlice,
    ) -> list[str]:
        """自由草案的状态一致性检查（第 13 章 §13.4 步骤 4、D-M4-7 最小集）。

        只允许引用玩家背包内的模板且数量足够（tool 存在即可，不消耗）；工位必须与当前
        房间工位匹配（draft.station=None 视为无需工位）；名称/步骤长度与数值字段由 Schema
        负责（extra=forbid）。返回违规说明列表；空列表表示通过。
        """
        violations: list[str] = []
        available = {entry.template_id: entry.qty for entry in view.inventory}
        requested: dict[str, int] = {}
        for item in draft.inputs:
            if item.template_id not in available:
                violations.append(f"input template {item.template_id!r} is not in inventory")
                continue
            requested[item.template_id] = requested.get(item.template_id, 0) + item.qty
        for template_id, qty in requested.items():
            if available[template_id] < qty:
                violations.append(
                    f"insufficient {template_id!r}: need {qty}, have {available[template_id]}"
                )
        if draft.station is not None and draft.station != state_slice.station_id:
            violations.append(
                f"station mismatch: need {draft.station!r}, current {state_slice.station_id!r}"
            )
        return violations

    # ------------------------------------------------------------------
    # 公共辅助
    # ------------------------------------------------------------------
    @staticmethod
    def _derive_rng_seed(ctx: ToolContext, state_slice: CraftStateSlice) -> int:
        """制造种子（D-M4-4）：derive_seed(save_seed, turn_id, actor_id, craft_item, next_seq)。"""
        return DerivedRandom.derive_seed(
            ctx.save_seed,
            ctx.turn_id,
            ctx.actor_id,
            _CRAFT_METHOD,
            state_slice.next_seq,
        )

    @staticmethod
    def _build_event(
        ctx: ToolContext,
        *,
        event_type: EventType,
        params: dict[str, object],
        result: CraftResult,
        produced_items: list[Item],
    ) -> Event:
        """组装制造草稿事件（result.craft + produced_items，第 14 章 §14.7 载荷口径）。"""
        return Event(
            turn_id=ctx.turn_id,
            actor_id=ctx.actor_id,
            method=_CRAFT_METHOD,
            event_type=event_type,
            params=dict(params),
            result={
                "craft": result.model_dump(mode="json"),
                "produced_items": [item.model_dump(mode="json") for item in produced_items],
            },
        )

    @staticmethod
    def _template_display_names(state: GameState) -> dict[str, str]:
        """模板键 → 玩家可读名称（失败提示用：缺材料/缺工具时必须点名，不能只给内部 ID）。

        优先取场景里同模板实例的 ``name``；场景未提供实例时用常见材料兜底名（story/m4 的
        火把、铜线、铁锭等）。仍未知时由调用方回退为「材料/工具」。
        """
        names: dict[str, str] = {
            "item.torch": "火把",
            "item.copper_wire": "铜线",
            "item.iron_ingot": "铁锭",
            "item.oak_handle": "橡木柄",
            "item.forge_hammer": "锻锤",
        }
        for item in state.items.values():
            if item.tags and item.name:
                names.setdefault(item.tags[0], item.name)
        return names

    @staticmethod
    def _check_recipe_preconditions(
        recipe: Recipe,
        state: CraftStateSlice,
        display_names: dict[str, str] | None = None,
    ) -> Result | None:
        """材料/工具/工位前置校验（第 13 章 §13.4；技能校验 M4 后移，D-M4-2）。

        返回 None 表示通过；否则返回带稳定错误码的 Result.fail：
        工位不符/缺工具 → NOT_ALLOWED；材料不在背包 → NOT_FOUND；数量不足 → OUT_OF_RANGE。
        失败文案面向玩家（中文、点名缺什么/还差几个），不含内部模板 ID；详细诊断仍进审计。
        """
        names = display_names or {}
        station = recipe.station
        if station is not None and station != state.station_id:
            station_name = "铁匠炉" if station == "station.forge" else "合适的工位"
            return Result.fail(
                ErrorCode.NOT_ALLOWED,
                f"这里不是合适的制作地点（需要{station_name}）。",
            )
        for item in recipe.inputs:
            available = state.inventory.get(item.template_id, 0)
            item_name = names.get(item.template_id)
            if item.role == "tool":
                if available < item.qty:
                    return Result.fail(
                        ErrorCode.NOT_ALLOWED,
                        f"缺少工具：{item_name or '合适的工具'}。",
                    )
                continue
            if available == 0:
                return Result.fail(
                    ErrorCode.NOT_FOUND,
                    f"缺少材料：{item_name or '一样材料'}（先把它拿进行囊）。",
                )
            if available < item.qty:
                return Result.fail(
                    ErrorCode.OUT_OF_RANGE,
                    f"材料不够：{item_name or '材料'}还差 {item.qty - available} 个。",
                )
        return None
