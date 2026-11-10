"""裁判证据与裁判 Agent 输出的跨层契约（M5E-2；第 8 章 §8.2、第 10 章 §10.1、第 11 章 §11.2）。

``JudgeOutput`` 是 ``JudgeAgent`` 的结构化输出契约（advisory only，不含数值/事件）；
``JudgeEvidence`` 是域层 ``RuleEngine.verify_agent_override`` 复核所用的**同构只读 DTO**。
放在 ``domain`` 层的原因：``domain/rules/engine.py`` 必须引用该类型，而第 16 章 §16.2 的
分层契约禁止 ``domain`` import ``agents``；``agents/judge_agent.py`` 再导出 ``JudgeOutput``
作为 Agent 的 ``output_schema``。

保守语义（第 10 章 §10.1、第 11 章 §11.2、第 21 章 §21.2.2 三处一致）：
- 只有 ``confidence >= JUDGE_CONFIDENCE_FLOOR`` 且 ``reason`` 非空才允许采纳；
- ``verdict != "allow"``（deny / modify）一律由 ``RuleEngine`` 落为保守拒绝——
  ``modify`` 不含可机械应用的数值/字段，无法安全合并，按"不产生有利效果"处理；
- ``evidence_refs`` 必须引用本角色感知切片内的实体（由 Agent 侧校验，见 judge_agent）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "JUDGE_CONFIDENCE_FLOOR",
    "MAX_EVIDENCE_REFS",
    "JudgeEvidence",
    "JudgeOutput",
    "JudgeVerdict",
]

#: 采纳裁判建议的最低置信度（低于该值由 Resolver 改走确定性保守兜底）。
JUDGE_CONFIDENCE_FLOOR = 0.7
#: 证据引用条数上限（与第 8 章 §8.2 JudgeOutput 契约一致）。
MAX_EVIDENCE_REFS = 5
#: 裁判倾向：允许 / 拒绝 / 需修改（advisory only；modify 在 M5E-2 保守拒绝）。
JudgeVerdict = Literal["allow", "deny", "modify"]


class JudgeEvidence(BaseModel):
    """已校验的裁判证据（域层复核 DTO；与 JudgeOutput 同构，不产生事件、不写状态）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    verdict: JudgeVerdict
    reason: str = Field(min_length=1, max_length=300)
    evidence_refs: list[str] = Field(default_factory=list, max_length=MAX_EVIDENCE_REFS)
    confidence: float = Field(ge=0.0, le=1.0)

    @property
    def allowed(self) -> bool:
        """是否倾向允许（只读派生属性；不进 JSON Schema、不可被模型覆盖）。"""
        return self.verdict == "allow"


class JudgeOutput(JudgeEvidence):
    """``JudgeAgent`` 的结构化输出（与证据 DTO 同构；Agent 的 ``output_schema`` 用本类）。"""
