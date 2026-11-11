"""进程内按 ``store_key`` 的分段锁（R-04）：创建/导入/回合的幂等临界区共用。

背景：``TurnOrchestrator.run()`` 已有 D-M4-15/G-1 的 per-key 锁，但 ``create_game`` 与
Card/WorldBook/Scenario 的导入仍是 check-then-act；同键并发会各自创建实体。本模块提供
固定 64 段的进程级锁，所有写端点共用同一注册表（键前缀不同，互不干扰）。

部署假设与 D-M4-15 一致：单进程 uvicorn（默认）。多进程部署时仍需数据库唯一键抢占，
本模块不声称解决跨进程竞态；docstring 在调用方明确该限制。
"""

from __future__ import annotations

import threading
import zlib
from collections.abc import Iterator
from contextlib import contextmanager

__all__ = ["LOCK_STRIPES", "idempotency_lock", "lock_index", "snapshot_lock"]

#: 分段数：固定值避免锁字典随请求键增长（哈希碰撞只造成无关键偶发串行）。
LOCK_STRIPES = 64

_LOCKS: tuple[threading.Lock, ...] = tuple(threading.Lock() for _ in range(LOCK_STRIPES))
#: R-10：快照写锁独立于幂等锁池，避免「run 持有 turn 分段 → 再取 snapshot」与
#: 「create 持有 game 分段 → 再取 snapshot」在相同分段上形成嵌套等待。
_SNAPSHOT_LOCKS: tuple[threading.Lock, ...] = tuple(threading.Lock() for _ in range(LOCK_STRIPES))


def lock_index(store_key: str) -> int:
    """返回 ``store_key`` 的稳定分段下标（CRC32，跨进程一致，便于测试断言）。"""
    return zlib.crc32(store_key.encode("utf-8")) % LOCK_STRIPES


@contextmanager
def idempotency_lock(store_key: str) -> Iterator[None]:
    """按 ``store_key`` 串行执行临界区；异常路径也保证释放。"""
    lock = _LOCKS[lock_index(store_key)]
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


@contextmanager
def snapshot_lock(game_id: str) -> Iterator[None]:
    """按 ``game_id`` 串行化快照「读最新版本 → 计算下一版本 → 写入」（R-10）。"""
    lock = _SNAPSHOT_LOCKS[lock_index(f"snapshot:{game_id}")]
    lock.acquire()
    try:
        yield
    finally:
        lock.release()
