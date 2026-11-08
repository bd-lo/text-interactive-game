"""``RandomSource`` 的派生式实现 ``DerivedRandom`` (第 10 章 10.3)。

**本模块是全仓唯一允许 import ``random`` 的模块**: 游戏逻辑禁止直接调用
``random``/``secrets``/``numpy.random``, 必须先经 ``RandomSource.derive_seed``
派生并掩码子种子, 再由本类实例掷骰, 保证固定种子回放可复算 (第 10 章约束 1、
第 14 章 replay)。``config.rng`` 只做派生委托, 不得 import ``random``。
"""

import random
from collections.abc import Sequence
from typing import TypeVar

from textgame.domain.rules.types import RandomSource

__all__ = ["DerivedRandom"]

_T = TypeVar("_T")


class DerivedRandom(RandomSource):
    """派生式随机实例: 一个已掩码子种子对应一条独立、可复现的掷骰序列。

    ``mask_seed``/``derive_seed`` 继承自 ``RandomSource`` (staticmethod/classmethod),
    可直接类级调用, 如 ``DerivedRandom.derive_seed(...)`` (第 5 章 5.6、第 18 章 18.2)。
    """

    def __init__(self, seed: int) -> None:
        """用已派生并掩码到 ``0..2**63-1`` 的子种子初始化独立的 ``random.Random``。"""
        self._rng = random.Random(seed)

    def randint(self, a: int, b: int) -> int:
        """返回 ``[a, b]`` 闭区间内的整数 (含两端); 语义同 ``random.Random.randint``。"""
        return self._rng.randint(a, b)

    def choice(self, seq: Sequence[_T]) -> _T:
        """从非空候选 ``seq`` 中等概率返回一个元素; 空序列抛 ``IndexError``。"""
        return self._rng.choice(seq)
