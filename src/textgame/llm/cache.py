"""进程内 LLM 响应缓存（M5-020；第 12 章 12.7、ADR D-M5-4）。

设计约束（020 护栏）：

- **默认不缓存**：``CacheSpec.allowed_tasks`` 默认为空元组，只有调用方显式把任务加入
  allowlist 且请求 ``cache_ttl_s > 0`` 时才允许命中/写入（「不缓存敏感内容」）；
- **失败不缓存**：只有成功响应的 ``LLMCallEntry`` 才会写入缓存；
- **线程安全**：``cachetools.TTLCache`` 本身不保证线程安全，本模块用 ``threading.Lock``
  串行化全部读写；进程内单实例，不落盘、不进日志；
- **TTL 双保险**：``TTLCache`` 的 ``ttl_s`` 是容量/过期上界，每条记录另存
  ``expires_at = clock() + min(req.cache_ttl_s, cache.ttl_s)``，读取时用注入时钟精确判断
  （便于单测用伪时钟断言过期，不依赖真实等待）。
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable

from cachetools import TTLCache

from textgame.llm.client import LLMRequest
from textgame.llm.config import CacheSpec
from textgame.llm.records import LLMCallEntry

__all__ = ["ResponseCache", "cache_key"]


def cache_key(req: LLMRequest) -> str:
    """派生可复用缓存的键：只含「内容级」字段，跨回合/跨重试同内容可命中。

    刻意**不含** ``turn_id``/``idempotency_key``/``timeout_s``/``cache_ttl_s``：
    这些是请求上下文与治理参数，不是 Prompt 内容；同一 Agent/任务/分级/Prompt 版本与
    消息内容应得到同一缓存键（回合级幂等仍由 ``LLMCallLog`` 独立负责）。
    """
    payload = {
        "task": req.task,
        "agent_id": req.agent_id,
        "model_tier": req.model_tier.value,
        "prompt_version": req.prompt_version,
        "messages": [message.model_dump() for message in req.messages],
        "output_schema": req.output_schema_json,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    return f"llm-cache:{digest[:48]}"


class ResponseCache:
    """``TTLCache`` 的线程安全包装（仅内存；命中返回原 ``LLMCallEntry``）。"""

    def __init__(
        self,
        spec: CacheSpec,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """按 ``spec`` 初始化缓存；``clock`` 可注入伪时钟用于过期断言。"""
        self._spec = spec
        self._clock = clock
        self._lock = threading.Lock()
        self._max_entry_ttl = float(spec.ttl_s)
        # cachetools 的 ttl 是容量级过期上界；精确过期由记录内 expires_at 判断。
        self._entries: TTLCache[str, tuple[float, LLMCallEntry]] = TTLCache(
            maxsize=spec.maxsize, ttl=self._max_entry_ttl
        )

    @property
    def allowed_tasks(self) -> tuple[str, ...]:
        """允许缓存的任务 allowlist（默认空 = 不缓存）。"""
        return self._spec.allowed_tasks

    def is_cacheable(self, *, task: str, cache_ttl_s: int) -> bool:
        """``cache_ttl_s > 0`` 且任务在 allowlist 中才允许缓存（两道门缺一不可）。"""
        return cache_ttl_s > 0 and self._spec.allows(task)

    def get(self, key: str) -> LLMCallEntry | None:
        """读取缓存；不存在或已过期返回 ``None``（过期项顺手剔除）。"""
        with self._lock:
            item = self._entries.get(key)
            if item is None:
                return None
            expires_at, entry = item
            if self._clock() >= expires_at:
                self._entries.pop(key, None)
                return None
            return entry

    def put(self, key: str, entry: LLMCallEntry, *, ttl_s: int) -> None:
        """写入成功响应；单个条目的 TTL 取 ``min(req.cache_ttl_s, cache.ttl_s)``。"""
        with self._lock:
            effective_ttl = min(float(ttl_s), self._max_entry_ttl)
            self._entries[key] = (self._clock() + effective_ttl, entry)

    def __len__(self) -> int:
        """当前缓存条目数（容量/线程安全断言用）。"""
        with self._lock:
            return len(self._entries)
