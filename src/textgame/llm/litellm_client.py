"""``LiteLLMClient``：M5-019 的 LLM 客户端治理实现（第 12 章 12.2/12.3/12.7/12.9）。

九项职责中本切片落地：整链 deadline、tenacity 单一重试（≤3 次，仅超时/429/5xx）、
进程内 RPM 令牌桶、幂等键复用与在飞判定、结构化日志（不落原文/密钥）、token 计量、
``LLMCallRecord``；模型分级/缓存（020）、成本预算（022）、批量/休眠（023）、
供应商回退（024）按切片推进。

边界（D-M5-2，2026-11-10 定案）：Schema 校验与一次修复仍由 ``validation.py`` 承担，
本客户端只返回**原始文本**（``complete_json``/``complete_text`` 契约保持向后兼容的
``{"content","repaired","usage"}`` 键，019 增补 ``call``）；021 再评估将修复链迁入客户端。

SDK 边界：本模块不 import litellm/openai；默认 transport 是 ``providers.chat_completion``，
供应商异常已在 ``providers`` 内翻译为 ``ProviderCallError``（第 12 章 12.1、第 16 章 16.2）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from typing import Any, Protocol, cast

from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from textgame.domain.results import ErrorCode, Result
from textgame.llm.client import LLMRequest, compose_idempotency_key
from textgame.llm.config import LLMClientConfig
from textgame.llm.errors import ProviderCallError
from textgame.llm.providers import ChatCompletionResult, chat_completion
from textgame.llm.ratelimit import TokenBucket
from textgame.llm.records import (
    InMemoryLLMCallLog,
    LLMCallEntry,
    LLMCallLog,
    LLMCallRecord,
)

__all__ = ["LLMTransport", "LiteLLMClient", "default_transport"]

_LOGGER = logging.getLogger("textgame.llm")
_MIN_REMAINING_S = 0.05
_MAX_MESSAGE_CHARS = 200


class LLMTransport(Protocol):
    """一次供应商调用的可注入端口（生产= ``providers.chat_completion``；测试=FakeTransport）。"""

    def __call__(
        self,
        *,
        provider: str,
        model: str,
        api_key: str,
        api_base: str,
        messages: list[dict[str, str]],
        timeout_s: float,
    ) -> ChatCompletionResult: ...


def default_transport(
    *,
    provider: str,
    model: str,
    api_key: str,
    api_base: str,
    messages: list[dict[str, str]],
    timeout_s: float,
) -> ChatCompletionResult:
    """生产默认 transport：委托 ``providers.chat_completion``（唯一 SDK 边界）。"""
    return chat_completion(
        provider=provider,
        model=model,
        api_key=api_key,
        api_base=api_base,
        messages=messages,
        timeout_s=timeout_s,
    )


class _DeadlineExceededError(Exception):
    """整链 deadline 已耗尽（不可重试，立即返回 LLM_UNAVAILABLE）。"""


class _RateLimitedError(Exception):
    """进程内 RPM 令牌桶额度不足（不可重试，立即返回 LLM_UNAVAILABLE）。"""


def _is_retryable(exc: BaseException) -> bool:
    """tenacity 重试谓词：只重试供应商标记为可重试的 ``ProviderCallError``。"""
    return isinstance(exc, ProviderCallError) and exc.retryable


class LiteLLMClient:
    """同步 LLM 客户端：治理（deadline/重试/限流/幂等/日志/计量）+ 原始文本返回。

    所有外部依赖可注入（``transport``/``calls``/``clock``/``sleep``/``logger``），
    单测零网络、零真实 sleep、可精确断言重试次数与 deadline 收缩。
    """

    def __init__(
        self,
        config: LLMClientConfig,
        *,
        transport: LLMTransport | None = None,
        calls: LLMCallLog | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        logger: logging.Logger | None = None,
    ) -> None:
        """``config`` 提供供应商/模型/重试/超时/RPM；其余依赖缺省用生产实现。"""
        self._config = config
        self._transport: LLMTransport = transport or default_transport
        self._calls: LLMCallLog = calls or InMemoryLLMCallLog()
        self._clock = clock
        self._sleep = sleep
        self._logger = logger or _LOGGER
        self._bucket = TokenBucket(
            config.rpm_limit,
            refill_per_s=config.rpm_limit / 60.0,
            clock=clock,
        )

    # ------------------------------------------------------------------
    # 公共入口（第 12 章 12.2 权威签名：同步；D-M5-1）
    # ------------------------------------------------------------------
    def complete_json(self, req: LLMRequest) -> Result:
        """结构化任务：要求 ``output_schema`` 非空；返回原始文本，校验/修复在 021 前留 Agent。"""
        if req.output_schema is None:
            return Result.fail(
                ErrorCode.INVALID_INPUT,
                "complete_json requires output_schema（第 12 章 12.2）",
            )
        return self._execute(req)

    def complete_text(self, req: LLMRequest) -> Result:
        """纯文本任务：不允许携带 ``output_schema``（防止误用结构化路径）。"""
        if req.output_schema is not None:
            return Result.fail(
                ErrorCode.INVALID_INPUT,
                "complete_text does not accept output_schema（第 12 章 12.2）",
            )
        return self._execute(req)

    # ------------------------------------------------------------------
    # 内部：幂等 → 限流/重试/deadline → 记录/日志
    # ------------------------------------------------------------------
    def _execute(self, req: LLMRequest) -> Result:
        """完整调用链：幂等复用/在飞判定 → 重试执行 → 记录与日志。"""
        key = compose_idempotency_key(req)
        reuse = self._calls.find_success(key)
        if reuse is not None:
            self._log(
                req,
                record=reuse.record,
                attempts=reuse.record.attempt,
                latency_ms=0,
                cache_hit=True,
                error_code=None,
            )
            return Result.success(
                data={
                    "content": reuse.content,
                    "repaired": False,
                    "usage": dict(reuse.usage),
                    "call": reuse.record,
                }
            )
        if not self._calls.try_begin(key):
            return Result.fail(
                ErrorCode.LLM_UNAVAILABLE,
                "同幂等键的 LLM 调用正在执行中（不重复扣费，第 12 章 12.7）",
            )

        started = self._clock()
        deadline = started + req.timeout_s
        attempts = 0
        try:
            retrying = Retrying(
                retry=retry_if_exception(_is_retryable),
                stop=stop_after_attempt(self._config.max_attempts),
                wait=wait_exponential_jitter(initial=1.0, max=8.0),
                sleep=self._sleep,
                reraise=True,
            )

            def attempt() -> ChatCompletionResult:
                nonlocal attempts
                remaining = deadline - self._clock()
                if remaining <= _MIN_REMAINING_S:
                    raise _DeadlineExceededError
                if not self._bucket.try_acquire():
                    raise _RateLimitedError
                attempts += 1
                attempt_timeout = min(self._config.timeout_s, remaining)
                try:
                    return self._transport(
                        provider=self._config.provider,
                        model=self._config.model,
                        api_key=self._config.api_key.get_secret_value(),
                        api_base=self._config.api_base,
                        messages=[message.model_dump() for message in req.messages],
                        timeout_s=attempt_timeout,
                    )
                except ProviderCallError:
                    raise
                except Exception as exc:
                    raise ProviderCallError(
                        f"transport error: {type(exc).__name__}", retryable=False
                    ) from exc

            raw_outcome: object = retrying(attempt)
        except _DeadlineExceededError:
            return self._finish_failure(
                req,
                key,
                started,
                attempts,
                ErrorCode.LLM_UNAVAILABLE,
                "LLM 调用整体 deadline 耗尽（不再发起剩余尝试）",
            )
        except _RateLimitedError:
            return self._finish_failure(
                req,
                key,
                started,
                attempts,
                ErrorCode.LLM_UNAVAILABLE,
                "进程内 RPM 限流拒绝（第 12 章 12.2；TPM/跨进程限流归 020/022）",
            )
        except ProviderCallError as exc:
            code = (
                ErrorCode.INVALID_INPUT
                if exc.status_code in (400, 422)
                else ErrorCode.LLM_UNAVAILABLE
            )
            return self._finish_failure(req, key, started, attempts, code, str(exc))
        except Exception as exc:
            return self._finish_failure(
                req,
                key,
                started,
                attempts,
                ErrorCode.INTERNAL,
                f"internal llm error: {type(exc).__name__}",
            )
        finally:
            self._calls.end(key)

        if not isinstance(raw_outcome, dict) or "content" not in raw_outcome:
            return self._finish_failure(
                req,
                key,
                started,
                attempts,
                ErrorCode.INTERNAL,
                "malformed transport outcome: missing content",
            )
        return self._finish_success(
            req, key, started, attempts, cast(ChatCompletionResult, raw_outcome)
        )

    def _finish_success(
        self,
        req: LLMRequest,
        key: str,
        started: float,
        attempts: int,
        outcome: ChatCompletionResult,
    ) -> Result:
        """成功路径：构造 record/entry、保存幂等缓存、结构化日志、返回 `Result`。"""
        prompt_tokens = int(outcome.get("prompt_tokens", 0) or 0)
        completion_tokens = int(outcome.get("completion_tokens", 0) or 0)
        record = self._record(
            req,
            attempts=attempts,
            started=started,
            model=str(outcome.get("model", "") or self._config.model),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            error_code=None,
        )
        entry = LLMCallEntry(
            idempotency_key=key,
            content=str(outcome.get("content", "")),
            usage={"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
            record=record,
        )
        try:
            self._calls.save_success(entry)
        except Exception:
            self._logger.warning("llm_call_save_failed request_id=%s", record.request_id)
        self._log(
            req,
            record=record,
            attempts=attempts,
            latency_ms=record.latency_ms,
            cache_hit=False,
            error_code=None,
        )
        return Result.success(
            data={
                "content": entry.content,
                "repaired": False,
                "usage": dict(entry.usage),
                "call": record,
            }
        )

    def _finish_failure(
        self,
        req: LLMRequest,
        key: str,
        started: float,
        attempts: int,
        code: ErrorCode,
        message: str,
    ) -> Result:
        """失败路径：写失败记录（best-effort，含失败也落库口径）、日志、返回四类错误码。"""
        record = self._record(
            req,
            attempts=max(attempts, 1),
            started=started,
            model=self._config.model,
            prompt_tokens=0,
            completion_tokens=0,
            error_code=code,
        )
        try:
            self._calls.append_failure(record)
        except Exception:
            self._logger.warning("llm_failure_record_failed request_id=%s", record.request_id)
        self._log(
            req,
            record=record,
            attempts=attempts,
            latency_ms=record.latency_ms,
            cache_hit=False,
            error_code=code,
        )
        return Result.fail(code, message[:_MAX_MESSAGE_CHARS])

    def _record(
        self,
        req: LLMRequest,
        *,
        attempts: int,
        started: float,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        error_code: ErrorCode | None,
    ) -> LLMCallRecord:
        """组装一次调用的 ``LLMCallRecord``（``cost_usd`` 019 恒 0，022 填真实成本）。"""
        key = compose_idempotency_key(req)
        elapsed_ms = max(0, int((self._clock() - started) * 1000))
        return LLMCallRecord(
            request_id=f"{key[4:24]}:{attempts}"[:128],
            task=req.task,
            agent_id=req.agent_id,
            model_tier=req.model_tier,
            provider=self._config.provider,
            model=model,
            prompt_version=req.prompt_version,
            attempt=max(attempts, 1),
            cache_hit=False,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=0.0,
            latency_ms=elapsed_ms,
            error_code=error_code,
        )

    def _log(
        self,
        req: LLMRequest,
        *,
        record: LLMCallRecord,
        attempts: int,
        latency_ms: int,
        cache_hit: bool,
        error_code: ErrorCode | None,
    ) -> None:
        """结构化日志：只落 prompt 哈希/长度，**不落 Prompt 原文、密钥、响应原文**。"""
        prompt_json = json.dumps(
            [message.model_dump() for message in req.messages],
            sort_keys=True,
            ensure_ascii=False,
        )
        fields: dict[str, Any] = {
            "request_id": record.request_id,
            "task": req.task,
            "agent_id": req.agent_id,
            "model_tier": req.model_tier.value,
            "provider": record.provider,
            "model": record.model,
            "prompt_version": req.prompt_version,
            "attempts": attempts,
            "cache_hit": cache_hit,
            "prompt_sha256": hashlib.sha256(prompt_json.encode()).hexdigest(),
            "prompt_chars": len(prompt_json),
            "prompt_tokens": record.prompt_tokens,
            "completion_tokens": record.completion_tokens,
            "latency_ms": latency_ms,
            "error_code": error_code.value if error_code is not None else None,
        }
        self._logger.info("llm_call %s", json.dumps(fields, sort_keys=True, ensure_ascii=False))
