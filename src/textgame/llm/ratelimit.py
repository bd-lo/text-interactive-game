"""进程内供应商限流（第 12 章 12.2 九项职责之「限流」、12.4 RPM 封顶）。

M5-019 只实现**进程内 RPM 令牌桶**：单进程单写者架构下足够；TPM 精确额度、
跨进程/多副本限流归 020/022（成本与分级切片）按配置校准。

时钟可注入（``clock: Callable[[], float]``）：生产用 ``time.monotonic``，单测用伪时钟，
保证「无真实 sleep、可精确断言额度恢复」的确定性测试（第 18 章测试纪律）。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

__all__ = ["TokenBucket"]


class TokenBucket:
    """按时间匀速补充的令牌桶；``try_acquire`` 不阻塞、不 sleep。

    ``capacity`` 为桶容量（= 每分钟允许的调用数），``refill_per_s`` 为每秒补充量。
    线程安全：内部加锁（单测/并发装配可共享同一实例）。
    """

    def __init__(
        self,
        capacity: int,
        *,
        refill_per_s: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """``capacity`` 必须 ≥1；``refill_per_s`` 必须 >0（否则额度永不恢复）。"""
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        if refill_per_s <= 0:
            raise ValueError("refill_per_s must be > 0")
        self._capacity = float(capacity)
        self._refill_per_s = refill_per_s
        self._clock = clock
        self._tokens = float(capacity)
        self._updated_at = clock()
        self._lock = threading.Lock()

    def try_acquire(self) -> bool:
        """尝试消耗一个令牌：成功返回 True，额度不足返回 False（不等待）。"""
        with self._lock:
            now = self._clock()
            elapsed = max(0.0, now - self._updated_at)
            self._tokens = min(self._capacity, self._tokens + elapsed * self._refill_per_s)
            self._updated_at = now
            if self._tokens < 1.0:
                return False
            self._tokens -= 1.0
            return True

    @property
    def tokens(self) -> float:
        """当前可用额度（只读快照，供日志/测试断言）。"""
        with self._lock:
            return self._tokens
