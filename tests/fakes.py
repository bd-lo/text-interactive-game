"""测试替身：``FakeLLM`` 实现 ``LLMClient`` Protocol (第 18 章：集成测试禁止真实 LLM/网络)。

用法 (每个用例独立实例，状态不共享)::

    llm = FakeLLM(
        [
            {"action": "move_to", "confidence": 0.9},   # 第一次：dict 会被序列化成 JSON 文本
            "not-json",                                  # 第二次：模拟修复失败
            RuntimeError("timeout"),                     # 第三次：模拟 LLM_UNAVAILABLE
        ]
    )

``complete_json`` 每次弹出一条脚本响应并记录 ``calls`` (元素为 ``LLMRequest``)；
脚本耗尽后返回 ``LLM_UNAVAILABLE``，用于测试“兜底路径”。响应不得联网、不得读环境变量。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from textgame.domain.results import ErrorCode, Result
from textgame.llm.client import LLMRequest, LLMResponse

FakeResponse = str | dict[str, Any] | LLMResponse | Exception

_DEFAULT_USAGE: dict[str, Any] = {"prompt_tokens": 48, "completion_tokens": 16}


class FakeLLM:
    """可编排响应序列的 LLM 客户端替身；同时用于"修复重试"与"兜底"分支。"""

    def __init__(
        self,
        responses: Sequence[FakeResponse] | None = None,
        *,
        usage: dict[str, Any] | None = None,
    ) -> None:
        """``responses`` 为按调用顺序消费的脚本响应；``usage`` 可覆盖默认 token 用量。"""
        self._responses: list[FakeResponse] = list(responses or [])
        self._usage: dict[str, Any] = dict(usage or _DEFAULT_USAGE)
        self._cursor = 0
        self.calls: list[LLMRequest] = []

    @property
    def remaining(self) -> int:
        """剩余未消费的脚本响应数 (便于断言“只修复一次”)。"""
        return len(self._responses) - self._cursor

    def complete_json(self, req: LLMRequest) -> Result:
        """消费下一条脚本响应并返回 ``Result``；耗尽或异常一律映射为 ``LLM_UNAVAILABLE``。"""
        self.calls.append(req)
        if self._cursor >= len(self._responses):
            return Result.fail(ErrorCode.LLM_UNAVAILABLE, "FakeLLM: 脚本响应已耗尽")
        item = self._responses[self._cursor]
        self._cursor += 1
        if isinstance(item, Exception):
            return Result.fail(ErrorCode.LLM_UNAVAILABLE, f"{type(item).__name__}: {item}")
        if isinstance(item, LLMResponse):
            response = item
        elif isinstance(item, dict):
            response = LLMResponse(
                content=json.dumps(item, ensure_ascii=False), usage=dict(self._usage)
            )
        else:
            response = LLMResponse(content=item, usage=dict(self._usage))
        return Result.success(
            data={
                "content": response.content,
                "repaired": response.repaired,
                "usage": dict(response.usage),
            }
        )
