"""LiteLLM 供应商适配：全仓唯一 import LiteLLM 的位置（第 12 章 12.1、第 16 章 16.2）。

职责（第 12 章 12.3）：只做**协议适配 + 异常分类**，不做重试（``num_retries=0``，重试
唯一计数者是 ``litellm_client`` 内的 tenacity）、不做 Schema 校验、不写状态。供应商异常
统一翻译为 :class:`textgame.llm.errors.ProviderCallError`，上层只按 ``retryable`` 决策。

``sanitize_no_proxy_env()`` 必须在 LiteLLM 构造 httpx 客户端前执行（本机 NO_PROXY 含
httpx 0.28 无法解析的方括号 IPv6 条目，会抛 ``InvalidURL``）。

**离线成本表（M5 出口 CI 修复）**：``import litellm`` 时 LiteLLM 默认从
``raw.githubusercontent.com`` 拉取模型成本表；本项目成本归集使用自建价格表（022），
不依赖远端成本表。因此模块导入时用 ``LITELLM_LOCAL_MODEL_COST_MAP`` 默认切到包内备份表，
避免离线/CI 环境在 import 阶段发起连接（Q9 禁网守卫会抛 ``RuntimeError``，而 LiteLLM
只捕获 httpx 异常，会导致 import 失败）；用户显式设为 ``False`` 时仍尊重用户选择。
"""

from __future__ import annotations

import os
from typing import Any, TypedDict

from textgame.llm.errors import ProviderCallError, classify_status_code

# 在导入 LiteLLM 之前设定（SDK 是函数内延迟 import）：默认离线、尊重用户显式配置。
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

__all__ = [
    "ChatCompletionResult",
    "chat_completion",
    "classify_exception",
    "litellm_model_id",
    "sanitize_no_proxy_env",
]

#: 异常类名 → 可重试判定（类名匹配避免在模块顶层 import 供应商异常类型；
#: 状态码可用时优先按状态码判定，见 ``classify_exception``）。
_RETRYABLE_EXCEPTION_NAMES: frozenset[str] = frozenset(
    {
        "Timeout",
        "APITimeoutError",
        "APIConnectionError",
        "RateLimitError",
        "ServiceUnavailableError",
        "InternalServerError",
        "MidStreamFallbackError",
    }
)
_PERMANENT_EXCEPTION_NAMES: frozenset[str] = frozenset(
    {
        "AuthenticationError",
        "BadRequestError",
        "NotFoundError",
        "PermissionDeniedError",
        "UnprocessableEntityError",
        "ContextWindowExceededError",
    }
)


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


def classify_exception(exc: BaseException) -> ProviderCallError:
    """把供应商 SDK 异常翻译为 ``ProviderCallError``（不泄漏密钥/响应体原文）。

    判定优先级：HTTP 状态码（如有）> 异常类名白名单 > 默认不可重试（避免反复计费）。
    """
    status_code: int | None = None
    for attr in ("status_code", "status", "http_status"):
        raw = getattr(exc, attr, None)
        if isinstance(raw, int):
            status_code = raw
            break
    retryable = classify_status_code(status_code)
    if status_code is None:
        name = type(exc).__name__
        if name in _RETRYABLE_EXCEPTION_NAMES:
            retryable = True
        elif name in _PERMANENT_EXCEPTION_NAMES:
            retryable = False
        elif isinstance(exc, (TimeoutError, ConnectionError)):
            retryable = True
    message = f"{type(exc).__name__}: {str(exc)[:200]}"
    return ProviderCallError(message, retryable=retryable, status_code=status_code)


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
    """发一次非流式 chat completion 并返回业务字段；失败抛 ``ProviderCallError``。"""
    sanitize_no_proxy_env()
    # 延迟导入：先修复 NO_PROXY 再让 LiteLLM 构造 httpx 客户端；同时保持全仓唯一
    # import 点（第 16 章 16.2、第 12 章 12.1）。
    from litellm import completion

    try:
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
    except ProviderCallError:
        raise
    except Exception as exc:
        raise classify_exception(exc) from exc
    try:
        choice = response.choices[0]
        content = choice.message.content or ""
    except (AttributeError, IndexError, TypeError) as exc:
        raise ProviderCallError(
            f"malformed provider response: {type(exc).__name__}", retryable=False
        ) from exc
    usage = getattr(response, "usage", None)
    return ChatCompletionResult(
        content=str(content),
        model=str(getattr(response, "model", "") or ""),
        prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
        completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
    )
