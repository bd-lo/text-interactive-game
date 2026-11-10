"""``LiteLLMClient``：M5-019 治理 + 020 模型分级/缓存 + 024 回退链/熔断（第 12 章 12.2/12.3/12.4/12.7/12.8/12.9）。

九项职责落地：整链 deadline、tenacity 单一重试（≤3 次，仅超时/429/5xx）、进程内 RPM
令牌桶、幂等键复用与在飞判定、结构化日志（不落原文/密钥）、token 计量、``LLMCallRecord``；
**020** 按 ``LLMRequest.model_tier`` 选择 JSON 配置的 provider/model/api_base 与进程内
``TTLCache``；**024** 主供应商重试耗尽后按 ``tiers[*].fallbacks`` 顺序回退，并对每个
供应商做进程内熔断（60s 窗口 / 错误率>50% / 连续 5 次失败 → open 60s → half-open 单次
探测；参数见 ``config.circuit``），全链耗尽返回 ``LLM_UNAVAILABLE``（调用方确定性兜底，
绝不抛裸供应商异常）。成本预算（022）按用户决定不做；批量/休眠（023）按切片推进。

边界（D-M5-2，2026-11-10 定案）：Schema 校验与一次修复仍由 ``validation.py`` 承担，
本客户端只返回**原始文本**（``complete_json``/``complete_text`` 契约保持向后兼容的
``{"content","repaired","usage"}`` 键，019 增补 ``call``）。

SDK 边界：本模块不 import litellm/openai；默认 transport 是 ``providers.chat_completion``，
供应商异常已在 ``providers`` 内翻译为 ``ProviderCallError``（第 12 章 12.1、第 16 章 16.2）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any, Protocol, cast

from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from textgame.domain.results import ErrorCode, Result
from textgame.llm.cache import ResponseCache, cache_key
from textgame.llm.client import LLMRequest, ModelTier, compose_idempotency_key
from textgame.llm.config import CircuitSpec, LLMClientConfig, LLMConfig
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
#: 非重试类供应商错误中按「调用方输入问题」映射为 INVALID_INPUT 的状态码（第 12 章 12.9）。
_INVALID_INPUT_STATUS_CODES: frozenset[int] = frozenset({400, 422})


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


class _CircuitBreaker:
    """单供应商进程内熔断器（第 12 章 §12.8；多进程各自独立、不共享状态）。

    状态机 ``closed → open → half_open → closed/open``：
    - closed：正常放行并记录结果；窗口内 ≥4 次调用且错误率 > 阈值，或连续失败达到阈值 → open；
    - open：拒绝调用 ``open_s`` 秒；计时到点后转 half_open；
    - half_open：最多放行 ``half_open_probes`` 次探测；探测成功 → closed 清零，失败 → 重新 open。
    """

    _MIN_RATE_SAMPLES = 4
    _CLOSED = "closed"
    _OPEN = "open"
    _HALF_OPEN = "half_open"

    def __init__(self, spec: CircuitSpec) -> None:
        """按配置初始化（closed 起步，空窗口与零连续失败）。"""
        self._spec = spec
        self._outcomes: deque[tuple[float, bool]] = deque()
        self._consecutive_failures = 0
        self._state = self._CLOSED
        self._open_until = 0.0
        self._probes_used = 0

    @property
    def state(self) -> str:
        """当前状态（``closed``/``open``/``half_open``；供日志与测试只读断言）。"""
        return self._state

    def allow(self, now: float) -> bool:
        """本次是否允许调用；half-open 期每放行一次即消耗一个探测名额。"""
        if self._state == self._CLOSED:
            self._prune(now)
            return True
        if self._state == self._OPEN:
            if now < self._open_until:
                return False
            self._state = self._HALF_OPEN
            self._probes_used = 0
        if self._probes_used >= self._spec.half_open_probes:
            return False
        self._probes_used += 1
        return True

    def record_success(self, now: float) -> None:
        """成功：half-open 探测成功则关闭并清零；closed 下只记窗口成功并清零连续失败。"""
        if self._state == self._HALF_OPEN:
            self._reset()
            return
        self._prune(now)
        self._outcomes.append((now, True))
        self._consecutive_failures = 0

    def record_failure(self, now: float) -> None:
        """失败：half-open 探测失败立即重新 open；closed 下累计并评估两条熔断条件。"""
        if self._state == self._HALF_OPEN:
            self._trip(now)
            return
        self._prune(now)
        self._outcomes.append((now, False))
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._spec.consecutive_failures:
            self._trip(now)
            return
        failures = sum(1 for _, ok in self._outcomes if not ok)
        total = len(self._outcomes)
        if total >= self._MIN_RATE_SAMPLES and failures / total > self._spec.error_rate_threshold:
            self._trip(now)

    def _trip(self, now: float) -> None:
        """进入 open 并清空计数窗口（重新关闭后从干净状态重新累计）。"""
        self._state = self._OPEN
        self._open_until = now + self._spec.open_s
        self._probes_used = 0
        self._consecutive_failures = 0
        self._outcomes.clear()

    def _reset(self) -> None:
        """回到 closed 并清空计数。"""
        self._state = self._CLOSED
        self._open_until = 0.0
        self._probes_used = 0
        self._consecutive_failures = 0
        self._outcomes.clear()

    def _prune(self, now: float) -> None:
        """剔除滑动窗口外的历史结果。"""
        while self._outcomes and now - self._outcomes[0][0] > self._spec.window_s:
            self._outcomes.popleft()


class LiteLLMClient:
    """同步 LLM 客户端：治理（deadline/重试/限流/幂等/日志/计量）+ 024 回退/熔断。

    所有外部依赖可注入（``transport``/``calls``/``clock``/``sleep``/``logger``），
    单测零网络、零真实 sleep、可精确断言重试次数、回退顺序与熔断窗口。
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
        llm_config: LLMConfig | None = None,
    ) -> None:
        """``config`` 提供超时/重试/RPM/密钥；``llm_config`` 提供分级、缓存与熔断（020/024）。

        ``llm_config=None`` 时完全保持 019 行为（所有分级使用 ``config`` 的单模型、无回退）；
        传入时按 ``LLMRequest.model_tier`` 选择 JSON 配置的尝试链，并按 ``circuit`` 熔断。
        """
        self._config = config
        self._llm_config = llm_config
        self._transport: LLMTransport = transport or default_transport
        self._calls: LLMCallLog = calls or InMemoryLLMCallLog()
        self._clock = clock
        self._sleep = sleep
        self._logger = logger or _LOGGER
        self._cache = (
            ResponseCache(llm_config.cache, clock=clock) if llm_config is not None else None
        )
        self._bucket = TokenBucket(
            config.rpm_limit,
            refill_per_s=config.rpm_limit / 60.0,
            clock=clock,
        )
        self._circuit_spec = llm_config.circuit if llm_config is not None else CircuitSpec()
        self._breakers: dict[str, _CircuitBreaker] = {}
        self._breakers_lock = threading.Lock()

    # ------------------------------------------------------------------
    # 后端解析 / 熔断状态
    # ------------------------------------------------------------------
    def _resolve_backend(self, tier: ModelTier) -> tuple[str, str, str]:
        """返回该模型分级的**主** ``(provider, model, api_base)``。

        未注入 ``LLMConfig``（019 兼容路径）时统一回退到 ``LLMClientConfig`` 的单模型；
        注入时按 JSON 分级选择主供应商，配置错误已在 ``LLMConfig`` 构造期 fail-fast。
        """
        if self._llm_config is not None:
            return self._llm_config.backend_for(tier)
        return self._config.provider, self._config.model, self._config.api_base

    def _resolve_backends(self, tier: ModelTier) -> tuple[tuple[str, str, str], ...]:
        """返回该分级的完整尝试链（主供应商 + 回退链，按配置顺序）；无回退时只有主供应商。"""
        if self._llm_config is not None:
            return self._llm_config.backends_for(tier)
        provider, model, api_base = self._resolve_backend(tier)
        return ((provider, model, api_base),)

    def _breaker_for(self, provider: str) -> _CircuitBreaker:
        """按 provider 取熔断器（惰性创建；进程内单实例，线程安全）。"""
        with self._breakers_lock:
            breaker = self._breakers.get(provider)
            if breaker is None:
                breaker = _CircuitBreaker(self._circuit_spec)
                self._breakers[provider] = breaker
            return breaker

    def _lookup_cache(self, req: LLMRequest) -> LLMCallEntry | None:
        """命中条件：缓存句柄存在 + ``cache_ttl_s>0`` + task 在 allowlist；否则 ``None``。"""
        if self._cache is None or not self._cache.is_cacheable(
            task=req.task, cache_ttl_s=req.cache_ttl_s
        ):
            return None
        return self._cache.get(cache_key(req))

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
    # 内部：缓存/幂等 → 尝试链（重试/回退/熔断）→ 记录/日志
    # ------------------------------------------------------------------
    def _execute(self, req: LLMRequest) -> Result:
        """完整调用链：缓存命中/幂等复用/在飞判定 → 按尝试链执行 → 记录与日志。"""
        key = compose_idempotency_key(req)
        primary_provider, primary_model, _ = self._resolve_backend(req.model_tier)
        cached = self._lookup_cache(req)
        if cached is not None:
            return self._finish_cache_hit(
                req, cached, provider=primary_provider, default_model=primary_model
            )
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
        attempts = [0]
        last_provider, last_model = primary_provider, primary_model
        attempted_any = False
        try:
            for provider, model, api_base in self._resolve_backends(req.model_tier):
                last_provider, last_model = provider, model
                breaker = self._breaker_for(provider)
                if not breaker.allow(self._clock()):
                    # 熔断 open（或 half-open 探测名额用尽）：跳过该供应商，直接尝试下一家
                    continue
                attempted_any = True
                try:
                    outcome = self._call_backend(
                        req=req,
                        provider=provider,
                        model=model,
                        api_base=api_base,
                        deadline=deadline,
                        attempts=attempts,
                    )
                except _DeadlineExceededError:
                    return self._finish_failure(
                        req,
                        key,
                        started,
                        attempts[0],
                        ErrorCode.LLM_UNAVAILABLE,
                        "LLM 调用整体 deadline 耗尽（不再发起剩余尝试）",
                        provider=provider,
                        default_model=model,
                    )
                except _RateLimitedError:
                    return self._finish_failure(
                        req,
                        key,
                        started,
                        attempts[0],
                        ErrorCode.LLM_UNAVAILABLE,
                        "进程内 RPM 限流拒绝（第 12 章 12.2；TPM/跨进程限流归 020/022）",
                        provider=provider,
                        default_model=model,
                    )
                except ProviderCallError as exc:
                    if exc.status_code in _INVALID_INPUT_STATUS_CODES:
                        return self._finish_failure(
                            req,
                            key,
                            started,
                            attempts[0],
                            ErrorCode.INVALID_INPUT,
                            str(exc),
                            provider=provider,
                            default_model=model,
                        )
                    if not exc.retryable:
                        return self._finish_failure(
                            req,
                            key,
                            started,
                            attempts[0],
                            ErrorCode.LLM_UNAVAILABLE,
                            str(exc),
                            provider=provider,
                            default_model=model,
                        )
                    # 可重试失败且本供应商重试已耗尽 → 记熔断失败并回退下一供应商
                    breaker.record_failure(self._clock())
                    continue
                except Exception as exc:
                    return self._finish_failure(
                        req,
                        key,
                        started,
                        attempts[0],
                        ErrorCode.INTERNAL,
                        f"internal llm error: {type(exc).__name__}",
                        provider=provider,
                        default_model=model,
                    )
                breaker.record_success(self._clock())
                if not isinstance(outcome, dict) or "content" not in outcome:
                    return self._finish_failure(
                        req,
                        key,
                        started,
                        attempts[0],
                        ErrorCode.INTERNAL,
                        "malformed transport outcome: missing content",
                        provider=provider,
                        default_model=model,
                    )
                return self._finish_success(
                    req,
                    key,
                    started,
                    attempts[0],
                    cast(ChatCompletionResult, outcome),
                    provider=provider,
                    default_model=model,
                )
            message = (
                "所有供应商均不可用（尝试链全部失败，第 12 章 12.9）"
                if attempted_any
                else "所有供应商熔断打开或探测名额用尽（第 12 章 12.8）"
            )
            return self._finish_failure(
                req,
                key,
                started,
                attempts[0],
                ErrorCode.LLM_UNAVAILABLE,
                message,
                provider=last_provider,
                default_model=last_model,
            )
        finally:
            self._calls.end(key)

    def _call_backend(
        self,
        *,
        req: LLMRequest,
        provider: str,
        model: str,
        api_base: str,
        deadline: float,
        attempts: list[int],
    ) -> object:
        """对单个供应商执行 tenacity 重试（≤3 次、共享整链 deadline）；失败原样抛出。"""
        messages = [message.model_dump() for message in req.messages]

        def attempt() -> ChatCompletionResult:
            remaining = deadline - self._clock()
            if remaining <= _MIN_REMAINING_S:
                raise _DeadlineExceededError
            if not self._bucket.try_acquire():
                raise _RateLimitedError
            attempts[0] += 1
            attempt_timeout = min(self._config.timeout_s, remaining)
            try:
                return self._transport(
                    provider=provider,
                    model=model,
                    api_key=self._config.api_key.get_secret_value(),
                    api_base=api_base,
                    messages=list(messages),
                    timeout_s=attempt_timeout,
                )
            except ProviderCallError:
                raise
            except Exception as exc:
                raise ProviderCallError(
                    f"transport error: {type(exc).__name__}", retryable=False
                ) from exc

        retrying = Retrying(
            retry=retry_if_exception(_is_retryable),
            stop=stop_after_attempt(self._config.max_attempts),
            wait=wait_exponential_jitter(initial=1.0, max=8.0),
            sleep=self._sleep,
            reraise=True,
        )
        raw_outcome: object = retrying(attempt)
        return raw_outcome

    def _finish_cache_hit(
        self,
        req: LLMRequest,
        entry: LLMCallEntry,
        *,
        provider: str,
        default_model: str,
    ) -> Result:
        """缓存命中路径：不调 transport，按本次请求重建 ``cache_hit=true`` 的记录并记日志。"""
        prompt_tokens = int(entry.usage.get("prompt_tokens", 0) or 0)
        completion_tokens = int(entry.usage.get("completion_tokens", 0) or 0)
        record = self._record(
            req,
            attempts=1,
            started=self._clock(),
            provider=provider,
            default_model=default_model,
            reported_model="",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            error_code=None,
            cache_hit=True,
        )
        self._log(
            req,
            record=record,
            attempts=1,
            latency_ms=0,
            cache_hit=True,
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

    def _finish_success(
        self,
        req: LLMRequest,
        key: str,
        started: float,
        attempts: int,
        outcome: ChatCompletionResult,
        *,
        provider: str,
        default_model: str,
    ) -> Result:
        """成功路径：构造 record/entry、保存幂等缓存、结构化日志、返回 `Result`。"""
        prompt_tokens = int(outcome.get("prompt_tokens", 0) or 0)
        completion_tokens = int(outcome.get("completion_tokens", 0) or 0)
        record = self._record(
            req,
            attempts=attempts,
            started=started,
            provider=provider,
            default_model=default_model,
            reported_model=str(outcome.get("model", "") or ""),
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
        if self._cache is not None and self._cache.is_cacheable(
            task=req.task, cache_ttl_s=req.cache_ttl_s
        ):
            self._cache.put(cache_key(req), entry, ttl_s=req.cache_ttl_s)
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
        *,
        provider: str,
        default_model: str,
    ) -> Result:
        """失败路径：写失败记录（best-effort，含失败也落库口径）、日志、返回四类错误码。"""
        record = self._record(
            req,
            attempts=max(attempts, 1),
            started=started,
            provider=provider,
            default_model=default_model,
            reported_model="",
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
        provider: str,
        default_model: str,
        reported_model: str,
        prompt_tokens: int,
        completion_tokens: int,
        error_code: ErrorCode | None,
        cache_hit: bool = False,
    ) -> LLMCallRecord:
        """组装一次调用的 ``LLMCallRecord``（``provider``/``model`` 记录实际使用的后端）。

        ``cost_usd`` 按用户 2026-11-10 决定不实现成本记录，恒 0；``model`` 传供应商响应
        回报的模型名（空串时回退到本次尝试的后端配置模型，保持记录与配置可比）。
        """
        key = compose_idempotency_key(req)
        resolved_model = reported_model or default_model
        elapsed_ms = max(0, int((self._clock() - started) * 1000))
        return LLMCallRecord(
            request_id=f"{key[4:24]}:{attempts}"[:128],
            task=req.task,
            agent_id=req.agent_id,
            model_tier=req.model_tier,
            provider=provider,
            model=resolved_model,
            prompt_version=req.prompt_version,
            attempt=max(attempts, 1),
            cache_hit=cache_hit,
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
