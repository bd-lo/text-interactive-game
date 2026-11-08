"""第 5 章 5.2 的 ErrorCode 与 Result：所有写方法的统一返回。

``Result`` 失败必须带 ``error_code`` 且不得携带事件（原子性：失败不产生半提交状态，
第 5 章 5.2/5.8）。``ErrorCode`` 是服务层与 API 错误映射的公共词表。
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .events import Event


class ErrorCode(str, Enum):
    """第 5 章 5.2 的 8 个稳定错误码。"""

    INVALID_INPUT = "INVALID_INPUT"
    NOT_FOUND = "NOT_FOUND"
    NOT_ALLOWED = "NOT_ALLOWED"
    OUT_OF_RANGE = "OUT_OF_RANGE"
    CONFLICT = "CONFLICT"
    SCHEMA_VIOLATION = "SCHEMA_VIOLATION"
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
    INTERNAL = "INTERNAL"


class Result(BaseModel):
    """所有写方法的统一返回；失败必须带 error_code 且不得携带事件。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ok: bool
    data: dict[str, Any] | None = None
    error_code: ErrorCode | None = None
    message: str | None = None
    events: list[Event] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistent(self) -> Result:
        if self.ok and self.error_code is not None:
            raise ValueError("ok=True cannot carry error_code")
        if not self.ok:
            if self.error_code is None:
                raise ValueError("ok=False requires error_code")
            if self.events:
                raise ValueError("failed Result must not carry events (atomicity)")
        return self

    @classmethod
    def success(
        cls, data: dict[str, Any] | None = None, events: list[Event] | None = None
    ) -> Result:
        return cls(ok=True, data=data, events=events or [])

    @classmethod
    def fail(cls, error_code: ErrorCode, message: str | None = None) -> Result:
        return cls(ok=False, error_code=error_code, message=message)
