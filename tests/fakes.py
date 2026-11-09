"""测试替身：``FakeLLM`` 实现 ``LLMClient`` Protocol，``FakeTransport`` 实现 LLM transport。

``FakeLLM`` 用于 Agent/编排层测试（脚本化为「客户端层」响应，保留 M2–M4 既有语义）；
``FakeTransport``（M5-019）用于 ``LiteLLMClient`` 的治理测试（脚本化为「供应商层」响应/
异常，可断言重试次数、每次尝试的 ``timeout_s``、透传 messages）。两者都零网络、零 sleep。

用法 (每个用例独立实例，状态不共享)::

    llm = FakeLLM(
        [
            {"action": "move_to", "confidence": 0.9},   # 第一次：dict 会被序列化成 JSON 文本
            "not-json",                                  # 第二次：模拟修复失败
            RuntimeError("timeout"),                     # 第三次：模拟 LLM_UNAVAILABLE
        ]
    )

    transport = FakeTransport(["{\"ok\": 1}", ProviderCallError("429", retryable=True)])

``complete_json`` 每次弹出一条脚本响应并记录 ``calls`` (元素为 ``LLMRequest``)；
脚本耗尽后返回 ``LLM_UNAVAILABLE``，用于测试“兜底路径”。响应不得联网、不得读环境变量。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from textgame.domain.results import ErrorCode, Result
from textgame.llm.client import LLMRequest, LLMResponse
from textgame.llm.providers import ChatCompletionResult

FakeResponse = str | dict[str, Any] | LLMResponse | Exception
FakeTransportResponse = str | dict[str, Any] | Exception

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

    def reset(self, responses: Sequence[FakeResponse] | None = None) -> None:
        """重置脚本队列并清空调用记录（供一次装配多次注入用例；不改变原有 API）。"""
        self._responses = list(responses or [])
        self._cursor = 0
        self.calls.clear()

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


class FakeTransport:
    """可编排的供应商层 transport 替身（M5-019 ``LiteLLMClient`` 测试专用）。

    ``responses`` 按调用顺序消费：``str``/``dict`` → 成功响应（dict 序列化为 JSON 文本）；
    ``Exception`` → 原样抛出（测试可抛 ``ProviderCallError`` 控制 retryable）。
    记录每次调用的 kwargs（含 ``timeout_s``/``messages``），便于断言 deadline 收缩与透传。
    """

    def __init__(
        self,
        responses: Sequence[FakeTransportResponse] | None = None,
        *,
        prompt_tokens: int = 48,
        completion_tokens: int = 16,
        model: str = "fake-model",
    ) -> None:
        """``responses`` 为脚本响应；token 用量与模型名可覆盖（计量断言用）。"""
        self._responses: list[FakeTransportResponse] = list(responses or [])
        self._prompt_tokens = prompt_tokens
        self._completion_tokens = completion_tokens
        self._model = model
        self._cursor = 0
        self.calls: list[dict[str, Any]] = []

    @property
    def remaining(self) -> int:
        """剩余未消费的脚本响应数。"""
        return len(self._responses) - self._cursor

    def reset(self, responses: Sequence[FakeTransportResponse] | None = None) -> None:
        """重置脚本队列与调用记录。"""
        self._responses = list(responses or [])
        self._cursor = 0
        self.calls.clear()

    def __call__(
        self,
        *,
        provider: str,
        model: str,
        api_key: str,
        api_base: str,
        messages: list[dict[str, str]],
        timeout_s: float,
    ) -> ChatCompletionResult:
        """消费下一条脚本响应；脚本耗尽抛不可重试 ``ProviderCallError``。"""
        from textgame.llm.errors import ProviderCallError

        self.calls.append(
            {
                "provider": provider,
                "model": model,
                "api_key": api_key,
                "api_base": api_base,
                "messages": messages,
                "timeout_s": timeout_s,
            }
        )
        if self._cursor >= len(self._responses):
            raise ProviderCallError("FakeTransport: 脚本响应已耗尽", retryable=False)
        item = self._responses[self._cursor]
        self._cursor += 1
        if isinstance(item, Exception):
            raise item
        content = json.dumps(item, ensure_ascii=False) if isinstance(item, dict) else item
        return ChatCompletionResult(
            content=content,
            model=self._model,
            prompt_tokens=self._prompt_tokens,
            completion_tokens=self._completion_tokens,
        )
