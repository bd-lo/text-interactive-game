"""场景运行时：把「一个状态属于哪个场景」映射到钩子实现，供通用层按需分发。

设计契约：``docs/design/user-scenario-modules.md`` §3.2。

分工：

- **内置 4 个场景**（m2/m3/m4/story）继续走各自模块的既有函数（``story_goal``/``story_ending``/
  ``story_room_damage``/``story_reply`` 等），本模块**不接管**它们；
- 本模块只维护「用户上传场景」的索引：``room_id → 钩子``、``npc_id → 钩子``，
  以及 ``resolve(state) → 钩子``（任一州房 ID 命中即属于该场景）；
- 未注册任何用户场景时，所有查询方法返回 ``None``/``False``，调用方保持**旧行为逐字不变**
  （这是旧 golden / demo / console 零回归的关键）。

索引可变性：导入/删除场景时组合根调用 :meth:`ScenarioRuntime.register` 重建索引；读路径无锁
读取一个不可变快照（构造期整体替换），因此并发回合不会读到半更新索引。
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from textgame.domain.models import GameState
from textgame.domain.user_scenario import UserScenarioHooks

__all__ = ["ScenarioHooks", "ScenarioRuntime"]


@runtime_checkable
class ScenarioHooks(Protocol):
    """场景钩子协议（内置场景函数与 ``UserScenarioHooks`` 都满足）。"""

    def goal(self, state: GameState) -> dict[str, object] | None:
        """目标面板视图（``/view.goal``）；无目标返回 ``None``。"""
        ...

    def ending(self, state: GameState) -> dict[str, object] | None:
        """结局视图；未结束返回 ``None``。"""
        ...

    def room_damage(self, room_id: str) -> int | None:
        """某房间的进入伤害；无伤害返回 ``None``。"""
        ...

    def npc_reply(self, npc_id: str, topic: str) -> dict[str, object] | None:
        """NPC 对话回复；未知 NPC/无定义返回 ``None``。"""
        ...


class ScenarioRuntime:
    """用户场景钩子的进程内注册表与解析器（默认空表 = 旧行为）。

    只保存 ``UserScenarioHooks``；内置场景**不注册**，由调用方在 ``None`` 分支继续走
    既有函数，从而保证内置 4 场景的事件/文案/golden 完全不变。
    """

    def __init__(self, hooks: Iterable[UserScenarioHooks] = ()) -> None:
        """可选初始注册（组合根从场景仓储加载全部定义后一次性注册）。"""
        self._lock = threading.Lock()
        self._hooks: tuple[UserScenarioHooks, ...] = ()
        self._room_index: dict[str, UserScenarioHooks] = {}
        self._npc_index: dict[str, UserScenarioHooks] = {}
        if hooks:
            self.register_many(hooks)

    # ------------------------------------------------------------------
    # 注册/查询
    # ------------------------------------------------------------------

    def register_many(self, hooks: Iterable[UserScenarioHooks]) -> None:
        """整体替换索引（导入/删除场景后由组合根调用；幂等）。"""
        items = tuple(hooks)
        room_index: dict[str, UserScenarioHooks] = {}
        npc_index: dict[str, UserScenarioHooks] = {}
        for item in items:
            for room_id in item.room_ids:
                room_index[room_id] = item
            for npc_id in item.npc_ids:
                npc_index[npc_id] = item
        with self._lock:
            self._hooks = items
            self._room_index = room_index
            self._npc_index = npc_index

    def register(self, hooks: UserScenarioHooks) -> None:
        """追加注册一个场景（重复注册同一 ``scenario_id`` 时以新对象为准）。"""
        current = [item for item in self._hooks if item.scenario_id != hooks.scenario_id]
        current.append(hooks)
        self.register_many(current)

    def clear(self) -> None:
        """清空索引（测试隔离用；生产不从请求路径调用）。"""
        self.register_many(())

    def __len__(self) -> int:
        """已注册用户场景数量（启动诊断/测试断言用）。"""
        return len(self._hooks)

    def resolve(self, state: GameState) -> UserScenarioHooks | None:
        """状态属于哪个用户场景：状态里任一房间 ID 命中索引即返回该场景。"""
        room_index = self._room_index
        for room_id in state.rooms:
            hooks = room_index.get(room_id)
            if hooks is not None:
                return hooks
        return None

    def hooks_for_room(self, room_id: str) -> UserScenarioHooks | None:
        """按房间 ID 找场景（进入伤害分发用；``None`` = 内置场景或未注册）。"""
        return self._room_index.get(room_id)

    def hooks_for_npc(self, npc_id: str) -> UserScenarioHooks | None:
        """按 NPC ID 找场景（对话分发用）。"""
        return self._npc_index.get(npc_id)

    # ------------------------------------------------------------------
    # 便捷分发（None = 交回内置逻辑）
    # ------------------------------------------------------------------

    def goal(self, state: GameState) -> dict[str, object] | None:
        """用户场景目标视图；状态不属于用户场景返回 ``None``（调用方走 story_goal）。"""
        hooks = self.resolve(state)
        return hooks.goal(state) if hooks is not None else None

    def ending(self, state: GameState) -> dict[str, object] | None:
        """用户场景结局视图；状态不属于用户场景返回 ``None``。"""
        hooks = self.resolve(state)
        return hooks.ending(state) if hooks is not None else None

    def room_damage(self, room_id: str) -> int | None:
        """用户场景的进入伤害（按房间 ID 直接分发，不需要状态）。"""
        hooks = self.hooks_for_room(room_id)
        return hooks.room_damage(room_id) if hooks is not None else None

    def room_damage_hint(self, room_id: str) -> str | None:
        """用户场景的进入伤害提示（无则 ``None``）。"""
        hooks = self.hooks_for_room(room_id)
        return hooks.room_damage_hint(room_id) if hooks is not None else None

    def npc_reply(self, npc_id: str, topic: str) -> dict[str, object] | None:
        """用户场景 NPC 回复（按 NPC ID 直接分发）。"""
        hooks = self.hooks_for_npc(npc_id)
        return hooks.npc_reply(npc_id, topic) if hooks is not None else None

    def scenario_id(self, state: GameState) -> str | None:
        """状态所属用户场景 ID；内置场景返回 ``None``（/view 加法字段用）。"""
        hooks = self.resolve(state)
        return hooks.scenario_id if hooks is not None else None
