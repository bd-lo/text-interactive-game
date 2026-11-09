"""LLM 客户端工厂：组合根唯一构造点（D-M5-6，2026-11-10 定案；020 增补分层配置）。

- ``TEXTGAME_LLM_ENABLED=false``（默认）：返回 ``DisabledLLMClient``——零联网、恒
  ``LLM_UNAVAILABLE``，**不读取 JSON 配置**，行为与 019 字节不变。
- ``TEXTGAME_LLM_ENABLED=true``：先按 ``Settings`` 校验密钥（缺 key 构造期 fail-fast），
  再加载 ``LLMConfig``（包内 ``llm/config/llm.default.json`` → ``TEXTGAME_LLM_CONFIG``
  路径覆盖；文件/JSON/字段非法同样构造期失败），最后构造 ``LiteLLMClient`` 并注入
  模型分级与进程内缓存参数（M5-020）。

真实 LLM 的 Agent 接线属 M5 出口附加项（第 17 章 §17.2）；本工厂在 019/020 落地后，
生产组合根已具备「一处开关」的切换点，且默认离线路径被集成测试覆盖。
"""

from __future__ import annotations

import time
from collections.abc import Callable

from textgame.config.settings import Settings
from textgame.llm.client import DisabledLLMClient, LLMClient
from textgame.llm.config import LLMClientConfig, load_llm_config
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
    """按配置构造 LLM 客户端；默认离线（不读 JSON），启用时配置非法立即抛 ``RuntimeError``。"""
    if not settings.llm_enabled:
        return DisabledLLMClient()
    config = LLMClientConfig.from_settings(settings)
    llm_config = load_llm_config(settings.llm_config_path)
    return LiteLLMClient(
        config,
        transport=transport,
        calls=calls,
        clock=clock,
        sleep=sleep,
        llm_config=llm_config,
    )
