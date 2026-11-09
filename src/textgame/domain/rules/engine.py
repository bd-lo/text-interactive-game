"""``RuleEngine``: L0 前置检查 + d20 判定 (第 10 章 10.1/10.2/10.3)。

纯函数、零 IO、不读时钟、不用全局随机:
- ``evaluate_preconditions`` 覆盖 L0 硬约束 (可见性/容量/位置), 不产生事件;
- ``check`` 覆盖 L1/L2 判定: d20 + 修正求和, 种子经 ``RandomSource.derive_seed``
  派生 (已掩码, 禁止重复掩码), 结果按第 5 章 5.6 的 ``CheckResult`` 值对象返回;
- ``check_rule`` 从 ``Ruleset`` 取规则条目后调用 ``check`` (M3 ``RuleService`` 接线入口)。

M2 裁剪: 判定结果暂不写入事件、不接编排链路 (第 9 章 9.2 步骤 6 的 M2 简化, 由进度
文档登记); ``deterministic_fallback`` 依赖 M3 的 ``Resolver``/``ToolContext`` 契约,
M2 不实现。随机只允许经 ``RandomSource``; 全仓 ``import random`` 的唯一落点是
``rules/dice.py`` (第 10 章 10.3 约束 1)。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Final

from ..models import GameState
from ..results import ErrorCode, Result
from .checks import D20_SIDES, outcome_for, sum_modifiers, validate_dc
from .dice import DerivedRandom
from .ruleset import M2_RULESET, RuleDefinition, Ruleset
from .types import CheckResult, DiceRoll, Modifier, RandomSource

# L0 工具集合 (M2 六工具子集): 只读工具恒允许; 写工具按第 10 章 10.1 L0 检查。
READ_ONLY_TOOLS: Final[frozenset[str]] = frozenset({"look"})
PRECONDITION_TOOLS: Final[frozenset[str]] = frozenset(
    {"move_to", "take_item", "use_item", "talk_to", "craft_item"}
)
BACKPACK_CAPACITY: Final[int] = 64


def _allowed() -> Result:
    """L0 通过的统一返回 (第 10 章 10.1: ``data`` 固定为 allowed/reason)。"""
    return Result.success(data={"allowed": True, "reason": ""})


class RuleEngine:
    """确定性判定入口: L0 前置检查 (零事件) + L1/L2 d20 判定 (纯计算)。"""

    def __init__(self, ruleset: Ruleset | None = None) -> None:
        """``ruleset`` 缺省使用内置 M2 规则集; 构造不读文件、不联网。"""
        self._ruleset: Ruleset = M2_RULESET if ruleset is None else ruleset

    @property
    def ruleset(self) -> Ruleset:
        """当前规则集 (只读数据, 供审计/回放记录 ``ruleset_version``)。"""
        return self._ruleset

    def evaluate_preconditions(
        self,
        *,
        tool: str,
        args: dict[str, Any],
        actor_id: str,
        turn_id: int,
        save_seed: str,
        state: GameState,
    ) -> Result:
        """L0 硬约束 (第 10 章 10.1): 不满足直接 ``Result.fail``, 零事件。

        规则 (M2 六工具 + M4 ``craft_item``): ``look`` 恒允许; ``move_to`` 方向合法且
        出口存在; ``take_item`` 物品在当前房间且背包未满 (<64); ``use_item`` 物品在背包;
        ``talk_to`` NPC 与 actor 同房间; ``craft_item`` 只做最低形状检查（actor 存在、
        mode 合法、recipe 模式 recipe_id 非空；材料/工位等深度校验留 ``CraftService``）；
        未知工具 → ``NOT_ALLOWED``。
        成功返回 ``data={"allowed": True, "reason": ""}``; 失败 ``events=[]``。
        ``turn_id``/``save_seed`` 为确定性判定上下文预留 (M3 Judge/派生需要), L0 不使用。
        """
        if tool in READ_ONLY_TOOLS:
            return _allowed()
        if tool not in PRECONDITION_TOOLS:
            return Result.fail(ErrorCode.NOT_ALLOWED, f"tool {tool!r} is not allowed")
        actor = state.characters.get(actor_id)
        if actor is None:
            return Result.fail(ErrorCode.NOT_FOUND, f"actor {actor_id!r} not found")
        room = state.rooms.get(actor.location)
        if room is None:  # GameState 引用完整性保证不会发生, 防御性兜底
            return Result.fail(ErrorCode.NOT_FOUND, f"room {actor.location!r} not found")
        if tool == "craft_item":
            mode = args.get("mode", "recipe")
            if mode not in {"recipe", "free"}:
                return Result.fail(ErrorCode.NOT_ALLOWED, f"unknown craft mode {mode!r}")
            if mode == "recipe":
                recipe_id = args.get("recipe_id")
                if not isinstance(recipe_id, str) or not recipe_id:
                    return Result.fail(
                        ErrorCode.INVALID_INPUT,
                        "recipe_id is required when craft mode=recipe",
                    )
            return _allowed()
        if tool == "move_to":
            direction = args.get("direction")
            if not isinstance(direction, str) or direction not in room.exits:
                return Result.fail(ErrorCode.NOT_FOUND, f"no exit to {direction!r} from {room.id}")
            return _allowed()
        if tool == "take_item":
            item_id = args.get("item_id")
            if not isinstance(item_id, str) or item_id not in room.item_ids:
                return Result.fail(ErrorCode.NOT_FOUND, f"item {item_id!r} is not in {room.id}")
            if len(actor.inventory) >= BACKPACK_CAPACITY:
                return Result.fail(
                    ErrorCode.OUT_OF_RANGE,
                    f"backpack is full ({BACKPACK_CAPACITY} slots)",
                )
            return _allowed()
        if tool == "use_item":
            item_id = args.get("item_id")
            if not isinstance(item_id, str) or item_id not in actor.inventory:
                return Result.fail(ErrorCode.NOT_FOUND, f"item {item_id!r} is not in inventory")
            return _allowed()
        # tool == "talk_to"
        npc_id = args.get("npc_id")
        npc = state.npcs.get(npc_id) if isinstance(npc_id, str) else None
        if npc is None or npc.room_id != actor.location:
            return Result.fail(ErrorCode.NOT_FOUND, f"npc {npc_id!r} is not here")
        return _allowed()

    def check(
        self,
        *,
        rule_id: str,
        dc: int,
        modifiers: list[Modifier],
        save_seed: str,
        turn_id: int,
        actor_id: str,
        seq: int,
        rng: RandomSource | None = None,
        target: str | None = None,
    ) -> CheckResult:
        """L1/L2 判定: d20 + 修正求和 → 第 5 章 5.6 的 ``CheckResult``。

        派生: ``seed = DerivedRandom.derive_seed(save_seed, turn_id, actor_id, rule_id, seq)``
        (派生键的 method 维度取 ``rule_id``; 返回值已掩码到 ``0..2**63-1``, 不得重复掩码),
        再用 ``DerivedRandom(seed)`` 掷 d20。自然 20/自然 1 按第 10 章 10.2 单独判大成功/
        大失败, 其余按 ``total >= dc`` 判成功/失败。``dc`` 必须是 5/10/15/20/25 固定档,
        否则 ``ValueError``。``rng`` 仅供单测注入固定骰面 (生产调用省略, 由派生种子构造,
        否则记录的 ``rng_seed`` 与实际骰面可能不一致)。
        """
        validate_dc(dc)
        seed = DerivedRandom.derive_seed(save_seed, turn_id, actor_id, rule_id, seq)
        source: RandomSource = DerivedRandom(seed) if rng is None else rng
        natural = source.randint(1, D20_SIDES)
        total = natural + sum_modifiers(modifiers)
        return CheckResult(
            rule_id=rule_id,
            actor_id=actor_id,
            target=target,
            dc=dc,
            dice=[DiceRoll(sides=D20_SIDES, rolls=[natural], kept=[natural])],
            modifiers=list(modifiers),
            total=total,
            outcome=outcome_for(natural=natural, total=total, dc=dc),
            rng_seed=seed,
        )

    def check_rule(
        self,
        *,
        rule_id: str,
        save_seed: str,
        turn_id: int,
        actor_id: str,
        seq: int,
        rng: RandomSource | None = None,
        target: str | None = None,
        additional_modifiers: Sequence[Modifier] = (),
    ) -> CheckResult:
        """按 ``Ruleset`` 取规则 (固定 DC + 默认修正) 后调用 :meth:`check` (L1 入口)。

        规则未登记 → ``ValueError``; ``additional_modifiers`` 用于叠加环境/技能临时修正
        (顺序在规则默认修正之后, 回放时按事件记录即可复算)。
        """
        rule: RuleDefinition = self._ruleset.require(rule_id)
        modifiers = [*rule.modifiers, *additional_modifiers]
        return self.check(
            rule_id=rule_id,
            dc=rule.dc,
            modifiers=modifiers,
            save_seed=save_seed,
            turn_id=turn_id,
            actor_id=actor_id,
            seq=seq,
            rng=rng,
            target=target,
        )
