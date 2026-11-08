"""只读查询服务 (第 6 章 6.3 的 ``QueryService``，契约 §4.2)。

所有方法只读 ``GameState``/仓储：零事件、不写库、不调模型。房间/背包/感知切片供
解析与工具门使用；``get_game_view`` 供 ``GET /api/v1/games/{game_id}`` 刷新/重连恢复。
"""

from __future__ import annotations

from typing import Any, Literal

from textgame.domain.models import Character, EntityRef, GameState, PerceptionSlice
from textgame.services.ports import GameRepository

#: 感知切片实体种类（与 ``EntityRef.kind`` 的 Literal 对齐）。
EntityKind = Literal["character", "room", "item", "npc", "quest"]

#: 感知切片里 ``local_events`` 的 M2 上限（契约 §4.2 的裁剪：不读全局事件，先留空）。
_LOCAL_EVENTS_LIMIT = 10


class GameNotFoundError(LookupError):
    """``get_game_view`` 找不到存档；接入层映射为 404 ``NOT_FOUND`` 信封。"""

    def __init__(self, game_id: str) -> None:
        """记录缺失的 ``game_id``，便于接入层构造错误消息。"""
        super().__init__(f"game not found: {game_id}")
        self.game_id = game_id


class QueryService:
    """只读查询 API；构造注入 ``GameRepository``，不持有任何可写状态。"""

    def __init__(self, repo: GameRepository) -> None:
        """``repo`` 只用于 ``get_game_view`` 读取当前状态与最近事件。"""
        self._repo = repo

    def get_room_view(self, state: GameState, actor_id: str) -> dict[str, Any]:
        """返回 ``{room_id, title, description, exits, items[], npcs[]}``（契约 §4.2）。

        角色不存在时抛 ``ValueError``（内部调用方须保证 actor 合法）。
        """
        character = self._require_character(state, actor_id)
        room = state.rooms[character.location]
        items = [
            {"item_id": item.id, "name": item.name}
            for item_id in room.item_ids
            if (item := state.items.get(item_id)) is not None
        ]
        npcs = [
            {"npc_id": npc.id, "name": npc.name}
            for npc_id in room.npc_ids
            if (npc := state.npcs.get(npc_id)) is not None
        ]
        return {
            "room_id": room.id,
            "title": room.name,
            "description": room.description,
            "exits": dict(room.exits),
            "items": items,
            "npcs": npcs,
        }

    def get_inventory(self, state: GameState, actor_id: str) -> dict[str, Any]:
        """返回 ``{"items": [{"item_id", "name"}]}``；未知物品引用直接跳过。"""
        character = self._require_character(state, actor_id)
        items = [
            {"item_id": item_id, "name": state.items[item_id].name}
            for item_id in character.inventory
            if item_id in state.items
        ]
        return {"items": items}

    def build_perception_slice(
        self,
        state: GameState,
        actor_id: str,
        *,
        turn_id: int,
        save_seed: str,
    ) -> PerceptionSlice:
        """构造角色的私有感知切片（第 8 章；M2 只含当前可见实体）。

        ``turn_id``/``save_seed`` 为 006 解析 Agent 的预留参数：M2 切片不携带随机种子，
        也不读全局事件（``local_events`` 留空，避免角色越权看到他人事件）。
        """
        character = self._require_character(state, actor_id)
        room = state.rooms[character.location]
        entities: list[EntityRef] = [
            EntityRef(kind="room", id=room.id, name=room.name),
        ]
        seen: set[tuple[str, str]] = {("room", room.id)}

        def add(kind: EntityKind, entity_id: str, name: str) -> None:
            key = (kind, entity_id)
            if key in seen:
                return
            seen.add(key)
            entities.append(EntityRef(kind=kind, id=entity_id, name=name))

        for item_id in room.item_ids:
            item = state.items.get(item_id)
            if item is not None:
                add("item", item.id, item.name)
        for npc_id in room.npc_ids:
            npc = state.npcs.get(npc_id)
            if npc is not None:
                add("npc", npc.id, npc.name)
        for npc in state.npcs.values():
            if npc.room_id == room.id:
                add("npc", npc.id, npc.name)
        for other in state.characters.values():
            if other.id != actor_id and other.location == room.id:
                add("character", other.id, other.name)
        # 背包内物品也算“可见实体”，供“使用/喝”类解析匹配（契约 §3.5）。
        for item_id in character.inventory:
            item = state.items.get(item_id)
            if item is not None:
                add("item", item.id, item.name)

        private_memory = [character.private_memory_ref] if character.private_memory_ref else []
        return PerceptionSlice(
            actor_id=actor_id,
            visible_entities=entities,
            local_events=[],
            private_memory=private_memory,
            plan_hint=None,
        )

    def get_game_view(self, game_id: str) -> dict[str, Any]:
        """返回对局视图 ``{game_id, turn, version, snapshot_version, status, last_text}``。

        ``last_text`` 取最近一条事件 ``result["narrative"]``（M2 模板叙事落库口径）；
        存档不存在抛 ``GameNotFoundError``，接入层映射 404。
        """
        record = self._repo.load(game_id)
        if record is None:
            raise GameNotFoundError(game_id)
        last_text: str | None = None
        for event in reversed(self._repo.read_events(game_id)):
            narrative = event.result.get("narrative")
            if isinstance(narrative, str) and narrative:
                last_text = narrative
                break
        snapshot = self._repo.latest_snapshot(game_id)
        return {
            "game_id": game_id,
            "turn": record.state.turn,
            "version": record.version,
            "snapshot_version": snapshot.snapshot_version if snapshot is not None else 0,
            "status": "active",
            "last_text": last_text,
        }

    @staticmethod
    def _require_character(state: GameState, actor_id: str) -> Character:
        """取角色；不存在时抛 ``ValueError``（内部调用方保证 actor 存在）。"""
        character = state.characters.get(actor_id)
        if character is None:
            raise ValueError(f"actor {actor_id} is not present in state")
        return character
