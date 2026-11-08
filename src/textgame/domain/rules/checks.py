"""判定纯函数 (第 10 章 10.2): DC 固定档位、修正求和与 d20 结果分级。

本模块零 IO、零随机、不读时钟: 随机由 ``RuleEngine`` 经 ``RandomSource`` 注入,
这里只做可单测的纯计算, 供 ``RuleEngine`` 与未来 ``RuleService``/``Resolver`` 复用
(第 4 章 4.2 规则/判定层、第 16 章 16.1 ``checks.py``)。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final, Literal

from .types import Modifier

Outcome = Literal["critical_success", "success", "failure", "critical_failure"]

# 第 10 章 10.2: d20 体系; 难度 DC 档位固定为 5/10/15/20/25
D20_SIDES: Final[int] = 20
NATURAL_CRIT_SUCCESS: Final[int] = 20
NATURAL_CRIT_FAILURE: Final[int] = 1
DC_TIERS: Final[frozenset[int]] = frozenset({5, 10, 15, 20, 25})
_DIFFICULTY_LABELS: Final[dict[int, str]] = {
    5: "琐碎",
    10: "简单",
    15: "普通",
    20: "困难",
    25: "极难",
}


def validate_dc(dc: int) -> int:
    """校验 ``dc`` 属于第 10 章 10.2 的固定档位并返回原值; 非法档位 → ``ValueError``。"""
    if dc not in DC_TIERS:
        raise ValueError(f"dc must be one of {sorted(DC_TIERS)}, got {dc!r}")
    return dc


def difficulty_for(dc: int) -> str:
    """DC 档位 → 中文难度名 (第 10 章 10.2 难度表); 非法档位 → ``ValueError``。"""
    validate_dc(dc)
    return _DIFFICULTY_LABELS[dc]


def sum_modifiers(modifiers: Sequence[Modifier]) -> int:
    """汇总全部修正来源的数值 (属性/技能/环境叠加, 第 10 章 10.2)。"""
    return sum(modifier.value for modifier in modifiers)


def outcome_for(*, natural: int, total: int, dc: int) -> Outcome:
    """按 d20 自然骰与总值分级 (第 10 章 10.2, ``CheckResult.outcome`` 取值)。

    自然 20 → ``critical_success``; 自然 1 → ``critical_failure``;
    其余 ``total >= dc`` → ``success``, 否则 ``failure``。``dc`` 由调用方先经
    :func:`validate_dc` 校验; 自然骰越出 1..20 属实现 bug, 直接 ``ValueError``。
    """
    if not NATURAL_CRIT_FAILURE <= natural <= NATURAL_CRIT_SUCCESS:
        raise ValueError(
            f"natural roll must be within {NATURAL_CRIT_FAILURE}..{NATURAL_CRIT_SUCCESS}, "
            f"got {natural!r}"
        )
    if natural == NATURAL_CRIT_SUCCESS:
        return "critical_success"
    if natural == NATURAL_CRIT_FAILURE:
        return "critical_failure"
    return "success" if total >= dc else "failure"
