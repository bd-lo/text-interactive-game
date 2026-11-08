"""工具上下文 (第 6 章 6.2 定稿六字段、契约 §4.1)：工具层透传、服务层消费。

``ToolContext`` 只是权限与随机种子等运行期信息的只读载体：不含服务/仓储引用，
因此可以被工具、服务、编排层自由传递而不泄漏可变状态（第 7 章 7.2）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

#: 调用来源；``resolver`` 为 M3 并发裁决预留，M2 只使用 ``player``。
Caller = Literal["player", "npc_agent", "parser", "system", "resolver"]


class ToolContext(BaseModel):
    """一次工具调用的上下文；六字段与第 6 章 6.2 逐字对齐。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor_id: str
    actor_tags: frozenset[str] = frozenset({"player"})
    turn_id: int
    save_seed: str
    idempotency_key: str
    caller: Caller
