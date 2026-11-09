"""LLM 供应商错误分类（第 12 章 12.3 / 12.9 的失败降级决策树）。

供应商 SDK 的异常只在 ``providers.py`` 内翻译为本模块的 :class:`ProviderCallError`；
``llm_client``/业务层只按 ``retryable`` 布尔值决策，不 import 任何 SDK 异常类型，
保证「业务代码不得直调 SDK」（第 12 章 12.1、第 16 章 16.2 分层契约）。

重试所有权（第 12 章 12.3）：本模块只做分类，不重试；唯一重试计数者是
``litellm_client`` 内的 tenacity（≤3 次），LiteLLM 自身保持 ``num_retries=0``。
"""

from __future__ import annotations

__all__ = ["ProviderCallError", "classify_status_code"]

#: HTTP 状态码中允许 tenacity 重试的集合（第 12 章 12.3：超时 / 429 / 5xx）。
RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({408, 429, 500, 502, 503, 504})


class ProviderCallError(RuntimeError):
    """一次供应商调用失败；``retryable`` 决定 tenacity 是否再次尝试。

    ``status_code`` 为供应商 HTTP 状态码（可用时），仅用于日志与测试断言，
    不向上暴露原始响应体（第 12 章 12.2 脱敏口径）。
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        status_code: int | None = None,
    ) -> None:
        """记录失败原因、是否可重试与（可选的）HTTP 状态码。"""
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


def classify_status_code(status_code: int | None) -> bool:
    """按 HTTP 状态码判断是否可重试：429/5xx（含 408）可重试，其余不可重试。"""
    if status_code is None:
        return False
    return status_code in RETRYABLE_STATUS_CODES
