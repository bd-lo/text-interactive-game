"""判定值对象与随机源协议 (第 5 章 5.6 定稿, 第 10 章 10.2/10.3 使用)。

本模块只含纯数据与接口: ``DiceRoll``/``Modifier``/``CheckResult`` 是不可变判定值
对象, 构造时由 Pydantic 做结构与范围校验, 判定计算 (RuleEngine) 属 M2;
``RandomSource`` 定义派生式随机契约, 其种子派生算法 (blake2b → int → 63 位掩码)
按第 10 章 10.3 实现, 实例掷骰实现见 ``textgame.domain.rules.dice.DerivedRandom``。
"""

import hashlib
from collections.abc import Sequence
from typing import Annotated, Literal, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["CheckResult", "DiceRoll", "Modifier", "RandomSource"]

_T = TypeVar("_T")


class DiceRoll(BaseModel):
    """一次骰子掷出 (第 5 章 5.6): ``sides`` 面骰, 骰面记于 ``rolls``, 保留值记于 ``kept``。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sides: int = Field(ge=2, le=100)
    rolls: list[int] = Field(min_length=1, max_length=16)
    kept: list[int] = Field(min_length=1, max_length=16)


class Modifier(BaseModel):
    """判定修正项 (第 5 章 5.6): ``source`` 可审计 (属性/技能/环境), 数值限制 ±100。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str = Field(min_length=1, max_length=48)
    value: int = Field(ge=-100, le=100)


class CheckResult(BaseModel):
    """一次判定的最终结果 (第 5 章 5.6): 不可变, 整对象不落库。

    ``dice`` 与 ``rng_seed`` 以摘要形式进 ``Event.result`` (第 10 章 10.2), 供回放复算;
    任何字段均禁止 LLM 生成或修改。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.]{1,63}$")]
    actor_id: str = Field(min_length=1, max_length=64)
    target: str | None = Field(default=None, max_length=64)
    dc: int = Field(ge=1, le=100)
    dice: list[DiceRoll] = Field(min_length=1, max_length=4)
    modifiers: list[Modifier] = Field(default_factory=list, max_length=8)
    total: int = Field(ge=-100, le=400)
    outcome: Literal["critical_success", "success", "failure", "critical_failure"]
    rng_seed: int = Field(ge=0, le=0x7FFF_FFFF_FFFF_FFFF)


class RandomSource(Protocol):
    """派生式随机契约 (第 5 章 5.6、第 10 章 10.3): 类方法派生/掩码, 实例负责掷骰。

    MVP 不维护全局可变 RNG: 每个事件用 ``(save_seed, turn_id, actor_id, method, seq)``
    经 blake2b 派生子种子, 回放时按同样输入重算。``randint``/``choice`` 只是实例掷骰
    声明, 不属于种子契约, 由 ``DerivedRandom`` 实现 (第 10 章 10.3)。
    """

    @staticmethod
    def mask_seed(raw: int) -> int:
        """``raw & 0x7FFF_FFFF_FFFF_FFFF``: 收敛到 ``0..2**63-1`` (BIGINT 上界)。"""
        return raw & 0x7FFF_FFFF_FFFF_FFFF

    @classmethod
    def derive_seed(cls, save_seed: str, turn_id: int, actor_id: str, method: str, seq: int) -> int:
        """同一 ``(save_seed, turn_id, actor_id, method, seq)`` 必得同一已掩码子种子。

        构造 ``f"{save_seed}|{turn_id}|{actor_id}|{method}|{seq}"`` 的 UTF-8 字节,
        取其 blake2b ``digest_size=8`` 摘要按大端解释为整数, 再经 ``cls.mask_seed``
        掩码; 返回 ``0..0x7FFF_FFFF_FFFF_FFFF`` (第 10 章 10.3 原文)。
        """
        # ``str.encode`` 默认 UTF-8; 省略参数仅为满足 ruff UP012, 字节序列与
        # 第 10 章 10.3 的 ``.encode("utf-8")`` 完全一致 (文档 §10.2 示例值已钉死)。
        key = f"{save_seed}|{turn_id}|{actor_id}|{method}|{seq}".encode()
        raw = int.from_bytes(hashlib.blake2b(key, digest_size=8).digest(), "big")
        return cls.mask_seed(raw)

    def randint(self, a: int, b: int) -> int: ...

    def choice(self, seq: Sequence[_T]) -> _T: ...
