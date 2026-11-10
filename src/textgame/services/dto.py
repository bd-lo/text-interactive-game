"""服务层与编排层共用的回合结果 DTO (第 9 章 9.1 的 ``TurnResult``)。

``TurnResult`` 是 ``TurnOrchestrator.run`` 的返回值、HTTP ``POST /api/v1/turns`` 的响应体，
也是仓储幂等表原样保存的快照：同一幂等键重试时直接回放首次结果 (第 6 章 6.7)。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from textgame.domain.events import Event
from textgame.domain.results import ErrorCode


class TurnResult(BaseModel):
    """一次回合的对外结果；失败时不得携带已提交事件 (原子性)。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ok: bool
    turn_id: int | None = None
    version: int = Field(ge=0, description="下一次提交的 expected_version；失败时为当前版本")
    text: str = ""
    events: list[Event] = Field(default_factory=list)
    error_code: ErrorCode | None = None
    effects: list[dict[str, Any]] = Field(default_factory=list)
    suggested_actions: list[str] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)
    #: L3 加法（H4）：本回合屏文本的来源（``llm``/``template``；失败或未产出文本为 ``None``）。
    #: 由编排层按叙事器返回值填写，前端据此显示来源徽章（无事件回合也能显示）。
    narrative_source: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> TurnResult:
        if self.ok and self.error_code is not None:
            raise ValueError("ok=True cannot carry error_code")
        if not self.ok:
            if self.error_code is None:
                raise ValueError("ok=False requires error_code")
            if self.events:
                raise ValueError("failed TurnResult must not carry events (atomicity)")
        return self

    @classmethod
    def success(
        cls,
        *,
        version: int,
        turn_id: int | None = None,
        text: str = "",
        events: list[Event] | None = None,
        effects: list[dict[str, Any]] | None = None,
        suggested_actions: list[str] | None = None,
        usage: dict[str, Any] | None = None,
        narrative_source: str | None = None,
    ) -> TurnResult:
        """构造成功结果；``version`` 为下一次提交的 expected_version。

        ``narrative_source``（L3 加法）记录本回合叙事来源（``llm``/``template``），
        缺省 ``None``；旧调用方不传时行为逐字不变。
        """
        return cls(
            ok=True,
            turn_id=turn_id,
            version=version,
            text=text,
            events=events or [],
            effects=effects or [],
            suggested_actions=suggested_actions or [],
            usage=usage or {},
            narrative_source=narrative_source,
        )

    @classmethod
    def failed(
        cls,
        error_code: ErrorCode,
        *,
        version: int,
        text: str = "",
        turn_id: int | None = None,
    ) -> TurnResult:
        """构造失败结果；零事件，版本保持调用方当前版本。"""
        return cls(ok=False, error_code=error_code, version=version, text=text, turn_id=turn_id)
