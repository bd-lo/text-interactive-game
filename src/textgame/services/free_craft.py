"""自由制造运行期开关（玩家在网页设置页开启/关闭，无需重启）。

R-19（2026-11）：自由制造草稿已迁移到**编排层 pre-queue**——在进入 ``Resolver`` 之前生成
一次，``Resolver``/CAS 重试只执行已生成草稿、不重调模型（原「Resolver 内调 LLM、重试可能
重复计费」技术风险已消除）。``risk_accepted`` 与 ``/api/v1/settings/free-craft`` 的
``accept_resolver_llm_risk`` 保留为**历史兼容的风险确认**：网页/无头开启仍需显式确认，
但技术风险已不存在，M5 后评估是否简化必填逻辑。``CraftService`` 与
``DeterministicIntentParser`` 共享同一实例，因此切换对“解析意图”和“执行制造”同时生效。
"""

from __future__ import annotations

import threading

__all__ = ["FreeCraftSwitch"]


class FreeCraftSwitch:
    """线程安全的自由制造开关（进程内单例，由组合根注入共享）。

    ``risk_accepted`` 仅记录历史兼容的风险确认；R-19 已迁移 pre-queue，开关语义不变。
    """

    def __init__(self, *, enabled: bool = False, risk_accepted: bool = False) -> None:
        """按初始状态构造；``risk_accepted`` 记录历史兼容的风险确认（保留语义，不加严）。"""
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
        """玩家/运维是否已显式确认历史风险（R-19 已迁 pre-queue；保留兼容）。"""
        with self._lock:
            return self._risk_accepted

    def set_enabled(self, enabled: bool, *, risk_accepted: bool | None = None) -> None:
        """切换开关；``risk_accepted=None`` 时保留既有确认状态。"""
        with self._lock:
            self._enabled = bool(enabled)
            if risk_accepted is not None:
                self._risk_accepted = bool(risk_accepted)
