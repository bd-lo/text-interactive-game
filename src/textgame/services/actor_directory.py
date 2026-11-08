"""``ActorDirectory``: 服务端 actor 标签与调用来源推导 (第 7 章 §7.3、第 8 章 §8.2)。

工具层的 ``actor_scope`` 使用**授权标签**而不是角色 ID；标签必须由服务端按存档状态与
静态场景配置推导，**绝不读取模型输出或客户端字段**（第 11 章 §11.1 authority 红线）。

本类不持有可变状态、不写库、不调模型：`tags_of`/`caller_of` 是从 ``GameState`` 与构造时
注入的静态 ``nonplayer_tags`` 映射出发的纯计算，所以可在 Resolver（纯函数裁决）与编排层
共用。``nonplayer_tags`` 只描述「非玩家角色」的细粒度标签（如 ``npc:scavenger``），玩家
恒为 ``{"player"}``；未登记的 Character 回退为默认 ``{"npc"}``。
"""

from __future__ import annotations

from collections.abc import Mapping

from textgame.domain.models import GameState
from textgame.domain.scenario import PLAYER_ID
from textgame.services.tool_context import Caller

PLAYER_TAGS: frozenset[str] = frozenset({"player"})
DEFAULT_NPC_TAGS: frozenset[str] = frozenset({"npc"})


class ActorDirectory:
    """从 ``GameState`` 解析 actor 标签与调用来源；构造时注入静态细标签映射。"""

    def __init__(self, *, nonplayer_tags: Mapping[str, frozenset[str]] | None = None) -> None:
        """``nonplayer_tags`` 是服务端静态配置（如场景模块的 ``M3_NONPLAYER_TAGS``）。"""
        self._nonplayer_tags: dict[str, frozenset[str]] = {
            actor_id: frozenset(tags) for actor_id, tags in (nonplayer_tags or {}).items()
        }

    def exists(self, state: GameState, actor_id: str) -> bool:
        """actor 是否为存档中的角色（Character）或 NPC 实体。"""
        return actor_id in state.characters or actor_id in state.npcs

    def tags_of(self, state: GameState, actor_id: str) -> frozenset[str]:
        """返回 actor 的授权标签；不存在返回空集合（调用方据此拒绝）。

        玩家 → ``{"player"}``；其他 Character → 静态映射，缺省 ``{"npc"}``；
        NPC 实体 → ``{"npc"}`` 并追加 ``npc:<faction>``（faction 为空则不加）。
        """
        if actor_id == PLAYER_ID and actor_id in state.characters:
            return PLAYER_TAGS
        if actor_id in state.characters:
            return self._nonplayer_tags.get(actor_id, DEFAULT_NPC_TAGS)
        npc = state.npcs.get(actor_id)
        if npc is not None:
            if npc.faction:
                return DEFAULT_NPC_TAGS | {f"npc:{npc.faction}"}
            return DEFAULT_NPC_TAGS
        return frozenset()

    def caller_of(self, state: GameState, actor_id: str) -> Caller:
        """推导 ``ToolContext.caller``：玩家 / NPC Agent / 解析层兜底。"""
        if actor_id == PLAYER_ID and actor_id in state.characters:
            return "player"
        if self.exists(state, actor_id):
            return "npc_agent"
        return "resolver"


__all__ = ["DEFAULT_NPC_TAGS", "PLAYER_TAGS", "ActorDirectory"]
