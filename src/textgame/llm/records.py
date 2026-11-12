"""LLM 调用记录与调用日志端口（第 12 章 12.2 ``LLMCallRecord``、12.7 幂等缓存）。

职责边界（D-M5-3，2026-11-10 定案；D-M5-10 成本出范围）：
- **019**：本模块只定义 `LLMCallRecord` / `LLMCallEntry` / `LLMCallLog` 端口 +
  `InMemoryLLMCallLog` 内存实现 + 结构化日志；单进程内幂等复用与「在飞」判定。
- **022（缩范围，D-M5-10）**：仅保留并发同键幂等治理（D-M4-15/G-1，见
  `orchestration/turn_orchestrator.py`）；`llm_call_log` 表、SQL 实现与 Alembic 迁移
  **不做**——成本/预算按用户决定整体出范围（2026-11-10）。本模块保持内存实现，
  `llm/` 不得 import `persistence/`。

成功复用语义（第 12 章 12.7）：同键成功记录直接复用，不重复扣费；同键「在飞」返回
`LLM_UNAVAILABLE`（单进程立即失败，不阻塞等待）。
失败记录（含 Schema 失败）只落 `append_failure` 供审计/排障，不进入成功复用表。
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from textgame.domain.results import ErrorCode
from textgame.llm.client import ModelTier

__all__ = ["InMemoryLLMCallLog", "LLMCallEntry", "LLMCallLog", "LLMCallRecord"]


class LLMCallRecord(BaseModel):
    """一次 LLM 调用（含重试/修复）的治理元数据（第 12 章 12.2 字段）。

    ``request_id`` 由 ``客户端 + 幂等键`` 派生，只用于关联日志，不含私密内容；
    ``cost_usd`` 恒为 0（价格表/成本归集按 D-M5-10 出范围，字段保留仅为不破坏记录契约）；
    ``latency_ms`` 是整次调用（含重试与修复请求）的墙钟耗时；``streamed`` 为 N2-038
    只增字段（流式调用 True，旧非流式记录默认 False，口径不变）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str = Field(min_length=1, max_length=128)
    task: str = Field(min_length=1, max_length=64)
    agent_id: str = Field(min_length=1, max_length=32)
    model_tier: ModelTier
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    prompt_version: str = Field(default="unversioned", max_length=64)
    attempt: int = Field(ge=1)
    cache_hit: bool = False
    #: N2-038（D-N2-4）只增字段：本次调用是否走流式传输（默认 False → 旧记录/断言不变）。
    streamed: bool = False
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0)
    latency_ms: int = Field(ge=0)
    error_code: ErrorCode | None = None

    @property
    def total_tokens(self) -> int:
        """输入 + 输出 token 总数（成本/预算归集的中间量）。"""
        return self.prompt_tokens + self.completion_tokens


class LLMCallEntry(BaseModel):
    """幂等复用所需的结果载荷：原始文本 + token 用量 + 调用记录。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=160)
    content: str
    usage: dict[str, int] = Field(default_factory=dict)
    record: LLMCallRecord


class LLMCallLog(Protocol):
    """调用日志端口：本批（D-M5-10 后）只保留进程内实现。

    ``try_begin``/``end`` 用于「在飞」判定（防同键并发重复调用）；``find_success``/
    ``save_success`` 用于幂等复用；``append_failure`` 只记失败、不参与复用。
    持久化 SQL 实现（``llm_call_log`` 表）按 D-M5-10 不做。
    """

    def try_begin(self, idempotency_key: str) -> bool:
        """标记一次调用开始；同键已在飞返回 False（调用方映射 LLM_UNAVAILABLE）。"""
        ...

    def end(self, idempotency_key: str) -> None:
        """标记一次调用结束（必须与 ``try_begin`` 成对，异常路径也要调用）。"""
        ...

    def find_success(self, idempotency_key: str) -> LLMCallEntry | None:
        """同键成功记录（有则直接复用，不再发起供应商调用）。"""
        ...

    def save_success(self, entry: LLMCallEntry) -> None:
        """保存一次成功调用；同键重复保存以首次为准（幂等）。"""
        ...

    def append_failure(self, record: LLMCallRecord) -> None:
        """记录一次失败调用（含 Schema 失败），只供审计/排障，不参与复用。"""
        ...


class InMemoryLLMCallLog:
    """`LLMCallLog` 的进程内线程安全实现（D-M5-10 后为唯一实现）。

    R-21：成功复用表与失败记录均有界（LRU/上限），长期运行不会随调用次数无限增长。
    """

    _MAX_SUCCESS_ENTRIES = 1024
    _MAX_FAILURE_ENTRIES = 1024

    def __init__(self) -> None:
        """创建空日志：成功表、失败表与在飞集合均为空。"""
        self._success: OrderedDict[str, LLMCallEntry] = OrderedDict()
        self._failures: list[LLMCallRecord] = []
        self._in_flight: set[str] = set()
        self._lock = threading.Lock()

    def try_begin(self, idempotency_key: str) -> bool:
        """原子占用键；已在飞则返回 False。"""
        with self._lock:
            if idempotency_key in self._in_flight:
                return False
            self._in_flight.add(idempotency_key)
            return True

    def end(self, idempotency_key: str) -> None:
        """释放键（幂等：未占有时无副作用）。"""
        with self._lock:
            self._in_flight.discard(idempotency_key)

    def find_success(self, idempotency_key: str) -> LLMCallEntry | None:
        """返回同键成功记录副本（不存在返回 None）；命中即刷新 LRU 位置。"""
        with self._lock:
            entry = self._success.get(idempotency_key)
            if entry is not None:
                self._success.move_to_end(idempotency_key)
            return entry

    def save_success(self, entry: LLMCallEntry) -> None:
        """保存成功记录；同键已存在时保留首次（幂等复用口径），超限淘汰最旧。"""
        with self._lock:
            if entry.idempotency_key in self._success:
                return
            while len(self._success) >= self._MAX_SUCCESS_ENTRIES:
                self._success.popitem(last=False)
            self._success[entry.idempotency_key] = entry

    def append_failure(self, record: LLMCallRecord) -> None:
        """追加失败记录（有界列表，供测试/成本归集）。"""
        with self._lock:
            self._failures.append(record)
            if len(self._failures) > self._MAX_FAILURE_ENTRIES:
                del self._failures[: len(self._failures) - self._MAX_FAILURE_ENTRIES]

    @property
    def failures(self) -> list[LLMCallRecord]:
        """失败记录只读快照（测试与成本归集用）。"""
        with self._lock:
            return list(self._failures)

    @property
    def success_count(self) -> int:
        """成功记录条数（测试断言复用是否发生）。"""
        with self._lock:
            return len(self._success)


def usage_to_tokens(usage: dict[str, Any]) -> tuple[int, int]:
    """从供应商 usage 字典提取 ``(prompt_tokens, completion_tokens)``。

    缺失/类型不符按 0 处理（第 12 章 12.2：优先供应商 usage；token 估算属成本口径，
    按 D-M5-10 出范围，缺失一律记 0、不做估算）。
    """
    prompt = usage.get("prompt_tokens", 0)
    completion = usage.get("completion_tokens", 0)
    return (
        prompt if isinstance(prompt, int) and prompt >= 0 else 0,
        completion if isinstance(completion, int) and completion >= 0 else 0,
    )
