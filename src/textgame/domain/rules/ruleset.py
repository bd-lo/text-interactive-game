"""M2 判定规则数据 (第 10 章 10.1 L1, 第 16 章 16.1 ``ruleset.py``)。

只含数据常量与校验, 零 IO: ``RuleDefinition`` 描述单条规则的固定 DC 与默认修正来源,
``Ruleset`` 汇总规则并携带 ``ruleset_version`` (第 10 章 10.3 约束 3: 事件记录规则版本,
规则修改后旧存档按旧版本重放; 第 14 章 14.2 事件不写 event_schema 版本时的最小替代)。
"""

from __future__ import annotations

from typing import Annotated, Final

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .checks import validate_dc
from .types import Modifier

RULESET_VERSION: Final[str] = "m2-1"
_RULE_ID_PATTERN = r"^[a-z][a-z0-9_.]{1,63}$"
_RULESET_VERSION_PATTERN = r"^m2-[0-9]+$"


class RuleDefinition(BaseModel):
    """一条确定性判定规则: 固定 DC + 默认修正来源 (第 10 章 10.1 L1)。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule_id: Annotated[str, Field(pattern=_RULE_ID_PATTERN)]
    dc: int = Field(ge=5, le=25)
    modifiers: list[Modifier] = Field(default_factory=list, max_length=8)
    description: str = Field(min_length=1, max_length=200)

    @field_validator("dc")
    @classmethod
    def _dc_must_be_fixed_tier(cls, value: int) -> int:
        """DC 必须落在 5/10/15/20/25 固定档位 (第 10 章 10.2)。"""
        return validate_dc(value)


class Ruleset(BaseModel):
    """规则集数据 (无 IO): 规则按 ``rule_id`` 索引, 版本号供事件/回放记录。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ruleset_version: str = Field(default=RULESET_VERSION, pattern=_RULESET_VERSION_PATTERN)
    rules: dict[str, RuleDefinition] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _keys_match_rule_ids(self) -> Ruleset:
        for key, rule in self.rules.items():
            if key != rule.rule_id:
                raise ValueError(f"rules key {key!r} must equal rule_id {rule.rule_id!r}")
        return self

    def get(self, rule_id: str) -> RuleDefinition | None:
        """按 ``rule_id`` 取规则; 不存在返回 ``None`` (只读查询)。"""
        return self.rules.get(rule_id)

    def require(self, rule_id: str) -> RuleDefinition:
        """按 ``rule_id`` 取规则; 未登记 → ``ValueError`` (判定请求了规则集外的规则)。"""
        rule = self.rules.get(rule_id)
        if rule is None:
            raise ValueError(f"unknown rule_id: {rule_id!r} (ruleset {self.ruleset_version})")
        return rule


# 内置 M2 规则集: 数据常量, 零 IO (第 10 章 10.1 L1)
M2_RULESET: Final[Ruleset] = Ruleset(
    rules={
        "perception.track_v1": RuleDefinition(
            rule_id="perception.track_v1",
            dc=10,
            modifiers=[Modifier(source="perception", value=3)],
            description="追踪痕迹/脚印: 感知判定, 取第 10 章固定档位中的简单档 DC 10",
        ),
        "stealth.sneak_v1": RuleDefinition(
            rule_id="stealth.sneak_v1",
            dc=15,
            modifiers=[Modifier(source="dexterity", value=3)],
            description="潜行绕过守卫: 敏捷判定, 取第 10 章 10.2 典型场景 DC 15",
        ),
    }
)
