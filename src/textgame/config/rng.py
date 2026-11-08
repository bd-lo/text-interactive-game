"""种子派生策略的配置侧入口 (第 10 章 10.3; 落点见第 16 章 16.1 ``config/rng.py``)。

MVP 采用派生式无状态随机: 不维护全局可变 RNG, 每条事件用
``(save_seed, turn_id, actor_id, method, seq)`` 派生子种子并掩码到 ``0..2**63-1``
(SQLite/PostgreSQL ``BIGINT`` 上界)。派生与掩码算法唯一实现在
``textgame.domain.rules.types.RandomSource``; 本模块只提供配置默认值与轻量委托,
不得使用 ``secrets``/``random`` 生成种子 (第 10 章 10.3 约束 1)。
"""

from textgame.domain.rules.dice import DerivedRandom

__all__ = ["DEFAULT_SEED_MASK", "derive_event_seed", "save_seed_from_settings"]

DEFAULT_SEED_MASK = 0x7FFF_FFFF_FFFF_FFFF


def save_seed_from_settings(seed: int) -> str:
    """把 ``Settings.seed`` 转成存档种子字符串 (确定性 ``save-<seed>``, 用于测试/开局)。"""
    return f"save-{seed}"


def derive_event_seed(
    save_seed: str,
    turn_id: int,
    actor_id: str,
    method: str,
    seq: int,
) -> int:
    """派生事件子种子; 返回值已掩码, 调用方不得重复掩码 (第 10 章 10.3)。"""
    return DerivedRandom.derive_seed(save_seed, turn_id, actor_id, method, seq)
