"""LLM 客户端工厂：组合根唯一构造点（D-M5-6，2026-11-10 定案）。

- ``TEXTGAME_LLM_ENABLED=false``（默认）：返回 ``DisabledLLMClient``——零联网、恒
  ``LLM_UNAVAILABLE``，各 Agent 走确定性兜底，M2–M4 golden 与默认装配行为字节不变。
- ``TEXTGAME_LLM_ENABLED=true``：构造 ``LiteLLMClient``；缺少密钥/配置非法在**构造期**
  失败（fail-fast），不做「看着开了其实全兜底」的静默降级。

真实 LLM 的 Agent 接线属 M5 出口附加项（第 17 章 §17.2）；本工厂在 019 落地后，
生产组合根已具备「一处开关」的切换点，且默认离线路径被集成测试覆盖。
"""

from __future__ import annotations

import time
from collections.abc import Callable

from textgame.config.settings import Settings
from textgame.llm.client import DisabledLLMClient, LLMClient
from textgame.llm.config import LLMClientConfig
from textgame.llm.litellm_client import LiteLLMClient, LLMTransport
from textgame.llm.records import LLMCallLog

__all__ = ["build_llm_client"]


def build_llm_client(
    settings: Settings,
    *,
    transport: LLMTransport | None = None,
    calls: LLMCallLog | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> LLMClient:
    """按配置构造 LLM 客户端；默认离线，启用时配置非法立即抛 ``RuntimeError``。"""
    if not settings.llm_enabled:
        return DisabledLLMClient()
    config = LLMClientConfig.from_settings(settings)
    return LiteLLMClient(
        config,
        transport=transport,
        calls=calls,
        clock=clock,
        sleep=sleep,
    )
