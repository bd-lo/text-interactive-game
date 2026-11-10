"""手工合成 cassette 的离线回放器（M5 出口接线测试专用，不参与生产装配）。

cassette 为 ``tests/fixtures/cassettes/*.json``（vcrpy 风格 ``interactions`` 结构）：
**手工编写、非真实 API 录制**，只含合成内容，不含密钥/玩家隐私。本模块把
``interactions[].response.body`` 回放成 ``LLMTransport`` 结果，供
``LiteLLMClient`` 在 ``--record-mode=none`` 语义下零网络执行。

注意：cassette 是"证据/夹具"，不是生产代码；生产联网路径仍只经
``providers.chat_completion``（第 12 章 §12.1 边界）。
"""

from __future__ import annotations

import json
from pathlib import Path

from textgame.llm.errors import ProviderCallError
from textgame.llm.providers import ChatCompletionResult

CASSETTE_DIR = Path(__file__).resolve().parent / "fixtures" / "cassettes"

__all__ = ["CASSETTE_DIR", "CassetteTransport"]


class CassetteTransport:
    """按顺序回放手工 cassette；耗尽后抛不可重试错误（等价于真实网络不可用）。"""

    def __init__(self, name: str) -> None:
        """加载 ``tests/fixtures/cassettes/<name>`` 并校验为手工合成标记。"""
        self.name = name
        self.path = CASSETTE_DIR / name
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if payload.get("recorded_with") != "synthetic-handwritten":
            raise ValueError(
                f"cassette 必须是手工合成（recorded_with=synthetic-handwritten）: {name}"
            )
        self._interactions: list[dict[str, object]] = list(payload["interactions"])
        self.calls = 0

    @property
    def remaining(self) -> int:
        """剩余未消费的交互条数。"""
        return len(self._interactions) - self.calls

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
        """回放下一条交互（不记录 api_key/messages 原文，避免密钥/隐私进入证据）。"""
        _ = (provider, api_key, api_base, messages, timeout_s)
        if self.calls >= len(self._interactions):
            raise ProviderCallError(f"cassette exhausted: {self.name}", retryable=False)
        interaction = self._interactions[self.calls]
        self.calls += 1
        response = interaction["response"]
        assert isinstance(response, dict)  # cassette 结构固定（手工夹具）
        body = response["body"]
        assert isinstance(body, dict)
        usage = body.get("usage", {})
        assert isinstance(usage, dict)
        return ChatCompletionResult(
            content=str(body["content"]),
            model=str(body.get("model", model)),
            prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage.get("completion_tokens", 0) or 0),
        )
