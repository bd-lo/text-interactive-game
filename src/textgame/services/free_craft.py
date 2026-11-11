"""自由制造运行期开关（玩家在网页设置页开启/关闭，无需重启）。

R-19：自由制造会在 Resolver 裁决链路内调用 LLM；开关本身不隐藏该风险，开启前由
``/api/v1/settings/free-craft`` 要求前端显式确认 ``accept_resolver_llm_risk``。
``CraftService`` 与 ``DeterministicIntentParser`` 共享同一实例，因此切换对“解析意图”
和“执行制造”同时生效。
"""

from __future__ import annotations

import threading

__all__ = ["FreeCraftSwitch"]


class FreeCraftSwitch:
    """线程安全的自由制造开关（进程内单例，由组合根注入共享）。"""

    def __init__(self, *, enabled: bool = False, risk_accepted: bool = False) -> None:
        """按初始状态构造；``risk_accepted`` 记录玩家是否已确认 Resolver LLM 风险。"""
        self._lock = threading.Lock()
        self._enabled = bool(enabled)
        self._risk_accepted = bool(risk_accepted)

    @property
    def enabled(self) -> bool:
        """当前是否允许自由制造（解析与执行路径实时读取）。"""
        with self._lock:
            return self._enabled

    @property
    def risk_accepted(self) -> bool:
        """玩家/运维是否已显式接受“Resolver 内调 LLM”的风险。"""
        with self._lock:
            return self._risk_accepted

    def set_enabled(self, enabled: bool, *, risk_accepted: bool | None = None) -> None:
        """切换开关；``risk_accepted=None`` 时保留既有确认状态。"""
        with self._lock:
            self._enabled = bool(enabled)
            if risk_accepted is not None:
                self._risk_accepted = bool(risk_accepted)
