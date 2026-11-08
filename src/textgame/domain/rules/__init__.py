"""判定规则子包: M1 交付判定值对象与可复现随机基座 (第 5 章 5.6、第 10 章 10.3)。

M1 阶段只包含两类纯逻辑模块:
- ``types``: ``DiceRoll``/``Modifier``/``CheckResult`` 不可变判定值对象, 以及
  派生式随机契约 ``RandomSource`` (blake2b 派生 + 63 位掩码);
- ``dice``: ``RandomSource`` 的实例实现 ``DerivedRandom`` (全仓唯一允许 import
  ``random`` 的模块)。

M2 起本包再补齐 ``engine.py`` (RuleEngine 判定入口)、``ruleset.py`` (规则数据与
版本) 与 ``checks.py`` (命中/伤害/状态判定纯函数); 本包只允许依赖标准库与
pydantic, 不做任何 IO 或 LLM 调用 (第 4 章 4.2、第 16 章 16.1)。
"""
