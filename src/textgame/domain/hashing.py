"""状态与参数的规范化哈希：快照与固定种子回放的一致性判据。

第 14 章 14.4 / 第 18 章 18.3：``state_hash = sha256(canonical_json(GameState))``。
同一 ``GameState`` 必得同一哈希，与时间、进程、随机调用顺序无关。JSON 规范化固定为
``sort_keys=True`` + 紧凑分隔符 + ``ensure_ascii=False`` + ``allow_nan=False``
(NaN/Infinity 属第 5 章 5.8 的 SCHEMA_VIOLATION 口径，必须显式拒绝而不是静默转 null)。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .models import GameState


def canonical_json(payload: Any) -> str:
    """按固定规则序列化为 JSON 文本；NaN/Infinity 直接拒绝。"""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def state_hash(state: GameState) -> str:
    """``GameState`` 的规范哈希 (第 18 章 18.3 的 ``state_hash`` 口径)。"""
    return _sha256(canonical_json(state.model_dump(mode="json")))


def params_hash(payload: Any) -> str:
    """任意请求/参数载荷的规范哈希；用于幂等「同键异参」判定 (第 6 章 6.7)。"""
    return _sha256(canonical_json(payload))
