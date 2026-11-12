"""``RuleEngine``: L0 前置检查 + d20 判定 (第 10 章 10.1/10.2/10.3)。

纯函数、零 IO、不读时钟、不用全局随机:
- ``evaluate_preconditions`` 覆盖 L0 硬约束 (可见性/容量/位置), 不产生事件;
- ``check`` 覆盖 L1/L2 判定: d20 + 修正求和, 种子经 ``RandomSource.derive_seed``
  派生 (已掩码, 禁止重复掩码), 结果按第 5 章 5.6 的 ``CheckResult`` 值对象返回;
- ``check_rule`` 从 ``Ruleset`` 取规则条目后调用 ``check`` (M3 ``RuleService`` 接线入口);
- ``verify_agent_override`` / ``deterministic_fallback`` 为 M5E-2 裁判链路接口 (第 7 章
  §7.4、第 10 章 §10.1、第 11 章 §11.2): 纯计算、零事件、保守语义——证据不足/低置信/
  ``verdict != "allow"`` 一律不给有利效果。

M5E-2 的 ``needs_judge`` 生产点：``agent_assisted_tools`` 集合（由组合根用
``ToolRegistry.agent_assisted_tools()`` 注入，默认空集）内的工具在 L0 通过时返回
``data["needs_judge"]=True``；集合为空时 data 逐字不变（默认离线/既有 golden 零回归）。
随机只允许经 ``RandomSource``; 全仓 ``import random`` 的唯一落点是 ``rules/dice.py``。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any, Final

from ..judge import JUDGE_CONFIDENCE_FLOOR, JudgeEvidence
from ..models import GameState, Proposal
from ..results import ErrorCode, Result
from .checks import D20_SIDES, outcome_for, sum_modifiers, validate_dc
from .dice import DerivedRandom
from .ruleset import M2_RULESET, RuleDefinition, Ruleset
from .types import CheckResult, DiceRoll, Modifier, RandomSource

# L0 工具集合 (M2 六工具子集): 只读工具恒允许; 写工具按第 10 章 10.1 L0 检查。
READ_ONLY_TOOLS: Final[frozenset[str]] = frozenset({"look"})
PRECONDITION_TOOLS: Final[frozenset[str]] = frozenset(
    {
        "move_to",
        "take_item",
        "use_item",
        "talk_to",
        "craft_item",
        # R7 行动自由度动作包（契约 §4）：attack/drop_item/give_item 为确定性 L0，
        # improvise 另由 agent_assisted_tools 触发 needs_judge（裁判链路）。
        "attack",
        "drop_item",
        "give_item",
        "improvise",
    }
)
BACKPACK_CAPACITY: Final[int] = 64

#: M5E-2 裁判拒绝/兜底时给玩家的中文短句（不含内部 ID/英文，可被编排层 hint 过滤展示）。
_JUDGE_EVIDENCE_REJECTED = "裁判证据不足：降级为确定性保守判定。"
_JUDGE_FALLBACK_DENIED = "裁判不可用：降级为确定性保守判定。"
#: R7：裁判不可用时 improvise 的保守拒绝（明确说「世界没有确认」，不产生任何效果）。
_IMPROVISE_FALLBACK_DENIED = "裁判不可用：这个临场动作没有被世界确认。"


def _allowed() -> Result:
    """L0 通过的统一返回 (第 10 章 10.1: ``data`` 固定为 allowed/reason)。"""
    return Result.success(data={"allowed": True, "reason": ""})


class RuleEngine:
    """确定性判定入口: L0 前置检查 (零事件) + L1/L2 d20 判定 (纯计算)。"""

    def __init__(
        self,
        ruleset: Ruleset | None = None,
        *,
        agent_assisted_tools: frozenset[str] = frozenset(),
    ) -> None:
        """``ruleset`` 缺省使用内置 M2 规则集；``agent_assisted_tools`` 为 needs_judge 生产点。

        构造不读文件、不联网；``agent_assisted_tools`` 默认空集 = 旧行为逐字不变。
        """
        self._ruleset: Ruleset = M2_RULESET if ruleset is None else ruleset
        self._agent_assisted_tools: frozenset[str] = frozenset(agent_assisted_tools)

    @property
    def ruleset(self) -> Ruleset:
        """当前规则集 (只读数据, 供审计/回放记录 ``ruleset_version``)。"""
        return self._ruleset

    @property
    def agent_assisted_tools(self) -> frozenset[str]:
        """需要裁判覆盖的工具名集合（needs_judge 生产点；默认空集）。"""
        return self._agent_assisted_tools

    def _allowed_for(self, tool: str) -> Result:
        """L0 通过返回：``agent_assisted`` 工具追加 ``needs_judge=True``（其余工具逐字不变）。"""
        if tool in self._agent_assisted_tools:
            return Result.success(data={"allowed": True, "needs_judge": True, "reason": ""})
        return _allowed()

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

        规则 (M2 六工具 + M4 ``craft_item`` + R7 动作包): ``look`` 恒允许; ``move_to`` 方向
        合法且出口存在; ``take_item`` 物品在当前房间且背包未满 (<64); ``use_item`` 物品在背包;
        ``talk_to`` NPC 与 actor 同房间; ``craft_item`` 只做最低形状检查（actor 存在、
        mode 合法、recipe 模式 recipe_id 非空；材料/工位等深度校验留 ``CraftService``）；
        ``attack`` NPC 存在且与 actor 同房间; ``drop_item`` 物品在背包; ``give_item`` 物品在
        背包且 NPC 同房间; ``improvise`` actor 存在 + ``action_text`` 为 1..200 字非空文本
        （该工具在 ``agent_assisted_tools`` 中时经 ``_allowed_for`` 自动带 ``needs_judge``）；
        未知工具 → ``NOT_ALLOWED``。
        成功返回 ``data={"allowed": True, "reason": ""}``; 失败 ``events=[]``。
        ``turn_id``/``save_seed`` 为确定性判定上下文预留 (M3 Judge/派生需要), L0 不使用。
        """
        if tool in READ_ONLY_TOOLS:
            return self._allowed_for(tool)
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
            return self._allowed_for(tool)
        if tool == "move_to":
            direction = args.get("direction")
            if not isinstance(direction, str) or direction not in room.exits:
                return Result.fail(ErrorCode.NOT_FOUND, f"no exit to {direction!r} from {room.id}")
            return self._allowed_for(tool)
        if tool == "take_item":
            item_id = args.get("item_id")
            if not isinstance(item_id, str) or item_id not in room.item_ids:
                return Result.fail(ErrorCode.NOT_FOUND, f"item {item_id!r} is not in {room.id}")
            if len(actor.inventory) >= BACKPACK_CAPACITY:
                return Result.fail(
                    ErrorCode.OUT_OF_RANGE,
                    f"backpack is full ({BACKPACK_CAPACITY} slots)",
                )
            return self._allowed_for(tool)
        if tool == "use_item":
            item_id = args.get("item_id")
            if not isinstance(item_id, str) or item_id not in actor.inventory:
                return Result.fail(ErrorCode.NOT_FOUND, f"item {item_id!r} is not in inventory")
            return self._allowed_for(tool)
        # R7 动作包（契约 §4）：只有形状/可见性 L0；命中/伤害等语义在 ActionService/reducer。
        if tool == "attack":
            npc_id = args.get("npc_id")
            npc = state.npcs.get(npc_id) if isinstance(npc_id, str) else None
            if npc is None or npc.room_id != actor.location:
                return Result.fail(ErrorCode.NOT_FOUND, f"npc {npc_id!r} is not here")
            return self._allowed_for(tool)
        if tool == "drop_item":
            item_id = args.get("item_id")
            if not isinstance(item_id, str) or item_id not in actor.inventory:
                return Result.fail(ErrorCode.NOT_FOUND, f"item {item_id!r} is not in inventory")
            return self._allowed_for(tool)
        if tool == "give_item":
            item_id = args.get("item_id")
            if not isinstance(item_id, str) or item_id not in actor.inventory:
                return Result.fail(ErrorCode.NOT_FOUND, f"item {item_id!r} is not in inventory")
            npc_id = args.get("npc_id")
            npc = state.npcs.get(npc_id) if isinstance(npc_id, str) else None
            if npc is None or npc.room_id != actor.location:
                return Result.fail(ErrorCode.NOT_FOUND, f"npc {npc_id!r} is not here")
            return self._allowed_for(tool)
        if tool == "improvise":
            action_text = args.get("action_text")
            if (
                not isinstance(action_text, str)
                or not action_text.strip()
                or len(action_text) > 200
            ):
                return Result.fail(
                    ErrorCode.INVALID_INPUT,
                    "action_text must be a non-empty string of 1..200 chars",
                )
            return self._allowed_for(tool)
        # tool == "talk_to"
        npc_id = args.get("npc_id")
        npc = state.npcs.get(npc_id) if isinstance(npc_id, str) else None
        if npc is None or npc.room_id != actor.location:
            return Result.fail(ErrorCode.NOT_FOUND, f"npc {npc_id!r} is not here")
        return self._allowed_for(tool)

    # ------------------------------------------------------------------
    # M5E-2 裁判链路（第 7 章 §7.4、第 11 章 §11.2、第 21 章 §21.2.2）：
    # 两方法均为纯计算、零事件、不调模型；由 Resolver 在 needs_judge 分支调用。
    # ------------------------------------------------------------------
    def verify_agent_override(self, ctx: Any, proposal: Proposal, judge: Any) -> Result:
        """复核 ``JudgeAgent`` 的覆盖建议（只复核、不产生有利好处之外的内容）。

        仅当 ``verdict == "allow"``、``confidence`` 为**有限数值**且 ``>= JUDGE_CONFIDENCE_FLOOR``、
        ``reason`` 为**非空白字符串**时通过，返回 ``data={"allowed": True, "reason": <裁判理由>}``；
        其余（deny/modify/低置信/NaN/类型非法/理由空/非契约对象）一律**拒绝**（``NOT_ALLOWED``，
        中文保守提示），由 ``Resolver`` 落为 ``rejected``。``judge`` 标注为 ``Any`` 是刻意的
        防御性设计：生产链路由编排层只传 pydantic 校验后的 ``JudgeOutput | None``；但
        ``JudgeEvidence.model_construct`` 或任意 duck 对象也不得被 ``str()/float()`` 强转"洗白"。
        ``ctx``/``proposal`` 只作签名对齐（文档第 11 章 §11.2）；不写库、不读时钟、零事件。
        """
        del ctx, proposal  # 签名对齐文档；纯复核不使用上下文
        if not isinstance(judge, JudgeEvidence):
            return Result.fail(ErrorCode.NOT_ALLOWED, _JUDGE_EVIDENCE_REJECTED)
        verdict: Any = judge.verdict
        reason_value: Any = judge.reason
        raw_confidence: Any = judge.confidence
        # 严格类型校验（先于业务判断）：非 str reason / 非数值或 NaN confidence 一律拒绝。
        if not isinstance(reason_value, str):
            return Result.fail(ErrorCode.NOT_ALLOWED, _JUDGE_EVIDENCE_REJECTED)
        reason = reason_value.strip()
        confidence_valid = (
            isinstance(raw_confidence, (int, float))
            and not isinstance(raw_confidence, bool)
            and math.isfinite(float(raw_confidence))
        )
        if not confidence_valid or float(raw_confidence) < JUDGE_CONFIDENCE_FLOOR:
            detail = f"（裁判意见：{reason}）" if reason else ""
            return Result.fail(ErrorCode.NOT_ALLOWED, f"{_JUDGE_EVIDENCE_REJECTED}{detail}")
        if verdict != "allow" or not reason:
            # 审计需要可追溯裁判意见：消息附中文理由（无理由时退回通用保守提示）。
            detail = f"（裁判意见：{reason}）" if reason else ""
            return Result.fail(ErrorCode.NOT_ALLOWED, f"{_JUDGE_EVIDENCE_REJECTED}{detail}")
        return Result.success(data={"allowed": True, "reason": reason})

    def deterministic_fallback(self, ctx: Any, proposal: Proposal, snapshot: GameState) -> Result:
        """裁判不可用/证据缺失时的确定性兜底：保守否决（不产生有利效果）。

        纯计算、零事件；``ctx``/``proposal``/``snapshot`` 只作签名对齐（第 11 章 §11.2）。
        M5E-2 无任何 ``agent_assisted`` 工具的确定性兜底规则可查（生产该集合为空），
        因此统一返回否定 ``Result``（``NOT_ALLOWED`` + 中文保守提示），由 ``Resolver`` 落为
        ``rejected``；R7：``improvise`` 使用专属中文文案（「这个临场动作没有被世界确认。」），
        其余工具保持原文案。未来接入具体工具时可在本方法内按 ``Ruleset`` 扩展保守规则。
        """
        del ctx, snapshot  # 纯函数签名对齐；保守拒绝
        tool = getattr(proposal, "tool", None)
        if tool == "improvise":
            return Result.fail(ErrorCode.NOT_ALLOWED, _IMPROVISE_FALLBACK_DENIED)
        return Result.fail(ErrorCode.NOT_ALLOWED, _JUDGE_FALLBACK_DENIED)

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
