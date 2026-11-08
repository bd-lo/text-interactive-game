"""LLM 供应商适配：全仓唯一 import LiteLLM 的位置（第 12 章 12.1、第 16 章 16.2）。

M1 阶段只需要「配置可用性自检」的最小非流式文本补全；完整 LLMClient
（整链 deadline、tenacity 重试、限流、幂等键、token 计量、Schema 修复、
供应商回退、熔断）在 M5 切片 019-llmclient 实现，见第 12 章 12.2、
12.3、12.9。业务代码不得在本模块之外 import 任何供应商 SDK。
"""

from __future__ import annotations

import os
from typing import Any, TypedDict


class ChatCompletionResult(TypedDict):
    """一次 chat completion 中业务关心的结果字段（不向上暴露供应商原始对象）。"""

    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int


def sanitize_no_proxy_env() -> None:
    """剔除 NO_PROXY 中 httpx 无法解析的方括号 IPv6 条目。

    本机环境把 ``[::1]`` 写进 NO_PROXY，httpx 0.28 构造客户端时会抛
    ``InvalidURL: Invalid port: ':1]'``，导致所有出站 HTTP 调用（含 LiteLLM）
    失败。这里只剔除 ``[...]`` 形式的条目（localhost / 127.0.0.1 / ::1 仍在），
    其余代理配置保持不变，不改变外部环境的代理意图。
    """
    for name in ("NO_PROXY", "no_proxy"):
        raw = os.environ.get(name)
        if not raw:
            continue
        entries = [entry.strip() for entry in raw.split(",") if entry.strip()]
        kept = [entry for entry in entries if not entry.startswith("[")]
        if kept != entries:
            os.environ[name] = ",".join(kept)


def litellm_model_id(provider: str, model: str) -> str:
    """把 provider/model 配置拼成 LiteLLM 模型标识（如 ``deepseek/deepseek-flash``）。"""
    return model if "/" in model else f"{provider}/{model}"


def chat_completion(
    *,
    provider: str,
    model: str,
    api_key: str,
    api_base: str,
    messages: list[dict[str, str]],
    timeout_s: float = 20.0,
    max_tokens: int | None = None,
) -> ChatCompletionResult:
    """发一次非流式 chat completion 并返回业务字段；异常向上抛给调用方处理。"""
    sanitize_no_proxy_env()
    # 延迟导入：先修复 NO_PROXY 再让 LiteLLM 构造 httpx 客户端；同时保持全仓唯一
    # import 点（第 16 章 16.2、第 12 章 12.1）。
    from litellm import completion

    response: Any = completion(
        model=litellm_model_id(provider, model),
        messages=messages,
        api_key=api_key,
        api_base=api_base,
        timeout=timeout_s,
        # 网络重试所有权归 tenacity（第 12 章 12.3），LiteLLM 只做协议适配，必须关闭
        num_retries=0,
        max_tokens=max_tokens,
    )
    usage = getattr(response, "usage", None)
    choice = response.choices[0]
    return ChatCompletionResult(
        content=choice.message.content or "",
        model=str(getattr(response, "model", "") or ""),
        prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
        completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
    )
