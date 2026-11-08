"""解析 Agent 的输出契约 (第 8 章 ParseOutput 的 M2 最小投影)。

本模块放在 ``agents`` 层而不是 ``orchestration``，因为 ``ParseOutput`` 是智能体产物
(第 8 章 §8.1)：编排层可以 import ``agents``，``agents`` 不得反向 import ``orchestration``
(第 16 章 §16.2 的 import-linter 分层契约)。确定性关键词解析与 LLM 解析共用该类型，
``source`` 记录实际生效的解析来源，供回放与审计核对。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from textgame.domain.models import ActionIntent

_TOOL_NAME = r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$"


class ParseOutcome(BaseModel):
    """解析统一输出：工具名 + 参数 + 候选叙事 + 置信度 + 来源。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    intent: ActionIntent | None = None
    tool: Annotated[str | None, Field(default=None, pattern=_TOOL_NAME)] = None
    args: dict[str, Any] = Field(default_factory=dict)
    candidate_narrative: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    source: Literal["keyword", "llm", "fallback"] = "keyword"
