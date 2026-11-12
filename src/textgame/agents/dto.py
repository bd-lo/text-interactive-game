"""解析 Agent 的输出契约 (第 8 章 ParseOutput 的 M2 最小投影)。

本模块放在 ``agents`` 层而不是 ``orchestration``，因为 ``ParseOutput`` 是智能体产物
(第 8 章 §8.1)：编排层可以 import ``agents``，``agents`` 不得反向 import ``orchestration``
(第 16 章 §16.2 的 import-linter 分层契约)。确定性关键词解析与 LLM 解析共用该类型，
``source`` 记录实际生效的解析来源，供回放与审计核对。

R7「行动自由度」加法（契约 §5.1，全部带默认值，旧调用方零改动）：

- ``ParseStep``：多步解析的单步（工具名 + 参数），steps 只放 2–3 步的后续动作，
  一步的输入仍只填 ``tool``/``args``（``steps`` 留空）；
- ``ParseOutcome.steps``/``candidate_tool``/``candidate_args``：多步计划与低置信候选
  （候选只供编排层生成澄清建议，**绝不执行**）；
- ``CONFIDENCE_FLOORS``/``confidence_floor_for``：按工具分级的置信阈值（R7 之前是
  全局 0.6；只读动作可放宽、制造仍从严），未知工具回退 0.6。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from textgame.domain.models import ActionIntent

_TOOL_NAME = r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$"

#: R7 分工具置信阈值（契约 §5.1）。
#: 阈值越低越容易放行：只读/简单动作 0.45–0.5，攻击 0.55，制造 0.6（与旧全局阈值一致）。
CONFIDENCE_FLOORS: dict[str, float] = {
    "get_room_view": 0.45,
    "get_inventory": 0.45,
    "move_to": 0.45,
    "inspect_item": 0.5,
    "take_item": 0.5,
    "use_item": 0.5,
    "talk_to": 0.5,
    "craft_item": 0.6,
    "attack": 0.55,
    "drop_item": 0.5,
    "give_item": 0.5,
    "improvise": 0.55,
}

#: 未知/None 工具的回退阈值（= R7 之前的全局阈值，保持旧行为）。
_DEFAULT_CONFIDENCE_FLOOR = 0.6


def confidence_floor_for(tool: str | None) -> float:
    """返回该工具的置信阈值；未知工具/``None`` 回退 0.6（R7 之前全局口径）。"""
    if tool is None:
        return _DEFAULT_CONFIDENCE_FLOOR
    return CONFIDENCE_FLOORS.get(tool, _DEFAULT_CONFIDENCE_FLOOR)


class ParseStep(BaseModel):
    """多步解析中的一步（契约 §5.1）：只携带工具名 + 标量参数，不含数值/私有数据。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool: Annotated[str, Field(min_length=1, max_length=48, pattern=_TOOL_NAME)]
    args: dict[str, Any] = Field(default_factory=dict)

    @field_validator("args")
    @classmethod
    def _args_are_scalar(cls, args: dict[str, Any]) -> dict[str, Any]:
        """步骤参数只允许标量（JSON 可序列化），拒绝嵌套结构（注入防御）。"""
        for value in args.values():
            if value is not None and not isinstance(value, (str, int, float, bool)):
                raise ValueError("step args must be scalar (no nested objects/lists)")
        return args


class ParseOutcome(BaseModel):
    """解析统一输出：工具名 + 参数 + 候选叙事 + 置信度 + 来源（+ R7 多步/候选）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    intent: ActionIntent | None = None
    tool: Annotated[str | None, Field(default=None, pattern=_TOOL_NAME)] = None
    args: dict[str, Any] = Field(default_factory=dict)
    candidate_narrative: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    source: Literal["keyword", "llm", "fallback"] = "keyword"
    #: R7 多步计划：0 或 2–3 步（1 步请只填 tool/args，steps 留空）。
    steps: tuple[ParseStep, ...] = Field(default=(), max_length=3)
    #: R7 低置信候选：模型给出的候选工具/参数（不执行，仅供编排层生成澄清建议）。
    candidate_tool: Annotated[str | None, Field(default=None, pattern=_TOOL_NAME)] = None
    candidate_args: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _steps_need_two_entries(self) -> ParseOutcome:
        """``steps`` 语义为「2–3 步的计划」：1 步是非法状态（契约 §5.1）。"""
        if len(self.steps) == 1:
            raise ValueError("steps must be empty or contain 2..3 entries")
        return self
