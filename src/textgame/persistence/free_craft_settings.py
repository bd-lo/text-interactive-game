"""自由制造玩家偏好的本机持久化（``data/free_craft.json``）。

只保存非敏感开关状态；不含 API Key。写入使用临时文件 + ``os.replace`` 原子替换，
避免进程中断留下半截 JSON。文件缺失/损坏时返回 ``None``，由组合根回退到 env 默认值
（安全方向：不因为坏文件而意外开启）。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_FREE_CRAFT_SETTINGS_PATH",
    "FreeCraftPreference",
    "FreeCraftSettingsStore",
]

_LOGGER = logging.getLogger(__name__)

DEFAULT_FREE_CRAFT_SETTINGS_PATH = Path("data/free_craft.json")


@dataclass(frozen=True)
class FreeCraftPreference:
    """持久化的自由制造偏好。"""

    enabled: bool
    risk_accepted: bool
    updated_at: str | None = None


class FreeCraftSettingsStore:
    """读写本机 ``free_craft.json``（无数据库、无密钥、单进程/单玩家口径）。"""

    def __init__(self, path: Path | None = None) -> None:
        """``path=None`` 时使用 ``data/free_craft.json``。"""
        self.path = DEFAULT_FREE_CRAFT_SETTINGS_PATH if path is None else Path(path)

    def load(self) -> FreeCraftPreference | None:
        """读取偏好；文件不存在返回 ``None``；损坏/类型错误记录警告并返回 ``None``。"""
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except (OSError, UnicodeDecodeError):
            _LOGGER.warning("无法读取自由制造设置 %s，按默认关闭处理", self.path)
            return None
        try:
            payload: Any = json.loads(raw)
        except json.JSONDecodeError:
            _LOGGER.warning("自由制造设置文件损坏 %s，按默认关闭处理", self.path)
            return None
        if (
            not isinstance(payload, dict)
            or not isinstance(payload.get("enabled"), bool)
            or not isinstance(payload.get("risk_accepted"), bool)
        ):
            _LOGGER.warning("自由制造设置字段不合法 %s，按默认关闭处理", self.path)
            return None
        updated_at = payload.get("updated_at")
        if updated_at is not None and not isinstance(updated_at, str):
            updated_at = None
        return FreeCraftPreference(
            enabled=bool(payload["enabled"]),
            risk_accepted=bool(payload["risk_accepted"]),
            updated_at=updated_at,
        )

    def save(self, *, enabled: bool, risk_accepted: bool) -> FreeCraftPreference:
        """原子写入偏好并返回写入值；``OSError`` 原样抛给接入层映射 500。"""
        updated_at = datetime.now(timezone.utc).isoformat()
        payload = {
            "enabled": bool(enabled),
            "risk_accepted": bool(risk_accepted),
            "updated_at": updated_at,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except Exception:
            with suppress(OSError):
                os.unlink(temporary)
            raise
        return FreeCraftPreference(
            enabled=bool(enabled), risk_accepted=bool(risk_accepted), updated_at=updated_at
        )
