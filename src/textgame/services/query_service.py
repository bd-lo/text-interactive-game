"""只读查询服务 (第 6 章 6.3 的 ``QueryService``，契约 §4.2)。

所有方法只读 ``GameState``/仓储：零事件、不写库、不调模型。房间/背包/感知切片供
解析与工具门使用；``get_game_view`` 供 ``GET /api/v1/games/{game_id}`` 刷新/重连恢复；
``get_player_view`` 供 ``GET /api/v1/games/{game_id}/view`` 提供结构化状态面板数据。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from textgame.domain.events import Event
from textgame.domain.models import Character, EntityRef, GameState, PerceptionSlice
from textgame.domain.perception import filter_local_events
from textgame.domain.scenario import (
    M3_NPC_SCAVENGER_A,
    M3_NPC_SCAVENGER_B,
    M4_ROOM_FORGE,
    PLAYER_ID,
)
from textgame.domain.scenario_story import (
    STORY_SCENARIO,
    is_story_state,
    story_ending,
    story_goal,
)
from textgame.domain.suggestions import suggest_actions
from textgame.services.ports import GameRepository

#: 感知切片实体种类（与 ``EntityRef.kind`` 的 Literal 对齐）。
EntityKind = Literal["character", "room", "item", "npc", "quest"]


def scenario_name_of_state(state: GameState) -> str:
    """从状态事实推导场景名（不新增 GameState 字段、不改事件/快照）。

    判定顺序：story 独有房间 → m4 铁匠铺房间 → m3 拾荒者角色槽位 → 缺省 m2。
    「按卡建局」等自定义初始状态没有场景标记，按缺省 ``m2`` 处理（与建局契约一致）。
    """
    if is_story_state(state):
        return STORY_SCENARIO
    if M4_ROOM_FORGE in state.rooms:
        return "m4"
    if M3_NPC_SCAVENGER_A in state.characters or M3_NPC_SCAVENGER_B in state.characters:
        return "m3"
    return "m2"


def game_ending_of_state(state: GameState) -> dict[str, Any] | None:
    """当前状态的结局视图（仅 story 场景；未结束/旧场景返回 ``None``）。"""
    return story_ending(state)


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
        """返回 ``{room_id, title, description, exits, exit_names, items[], npcs[]}``。

        ``exit_names``（加法字段）：方向 → 目标房间展示名，供前端/终端把「北 → 地窖」
        渲染成玩家可读的出口，而不是内部房间 ID；``exits`` 保留 ID 映射不变（契约兼容）。

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
            "exit_names": {
                direction: state.rooms[target].name if target in state.rooms else direction
                for direction, target in room.exits.items()
            },
            "items": items,
            "npcs": npcs,
        }

    def get_inventory(self, state: GameState, actor_id: str) -> dict[str, Any]:
        """返回 ``{"items": [{"item_id", "name", "qty"}]}``；同模板实例聚合计数（加法字段）。

        模板键与 ``CraftService`` 背包聚合口径一致（D-M4-1）：有 tags 时取 ``item.tags[0]``，
        无 tags 时用物品自身 id。``item_id``/``name`` 取该模板首个实例；``qty`` 为该模板
        实例数。M2 场景物品 tags 为语义标签（light/consumable…）互不相同，故 qty 恒 1；
        制造产物同模板实例会正确合并。未知物品引用直接跳过。
        """
        character = self._require_character(state, actor_id)
        grouped: dict[str, dict[str, Any]] = {}
        for item_id in character.inventory:
            item = state.items.get(item_id)
            if item is None:
                continue
            template_key = item.tags[0] if item.tags else item.id
            entry = grouped.get(template_key)
            if entry is None:
                grouped[template_key] = {"item_id": item.id, "name": item.name, "qty": 1}
            else:
                entry["qty"] += 1
        return {"items": list(grouped.values())}

    def build_perception_slice(
        self,
        state: GameState,
        actor_id: str,
        *,
        turn_id: int,
        save_seed: str,
        plan_hint: str | None = None,
        events: Sequence[Event] = (),
    ) -> PerceptionSlice:
        """构造任意角色（玩家 ``Character`` 与 ``NPC``）的私有感知切片（第 8 章 §8.2）。

        这是结构性防泄露的唯一组装点（第 8 章 §8.2、第 15 章 §15.4）：只按 actor 视角
        裁剪当前房间可见实体、自己的背包物品、自己的事件与自己的私有记忆，绝不返回
        ``GameState``/仓储句柄或其他角色的私有数据。玩家与 NPC 使用同一套可见性规则。

        - ``actor_id`` 命中 ``state.characters`` 时用角色的 ``location``，命中
          ``state.npcs`` 时用 NPC 的 ``room_id``；两者都不命中 → ``ValueError``
          （契约 §4.2：内部调用方须保证 actor 合法）。
        - ``visible_entities`` 只含“其他”实体：room + 房内 items/npcs/characters +
          自己背包 items；actor 自身不作为实体引用返回（NPC 不返回自身 npc 引用，
          角色不返回自身 character 引用），可见实体只描述 actor 之外的场景对象。
        - ``private_memory`` 按 actor 隔离：角色取 ``private_memory_ref``，NPC 取
          ``memory_ref``，两者都只放该 actor 自己的引用。
        - ``plan_hint`` 只能由调用方显式注入（第 8 章 §8.2 构造规则 4：调度器注入，
          不来自其他 Agent 输出），本方法不从 ``state`` 推导。
        - ``local_events`` 只含 ``event.actor_id == actor_id`` 的最近 10 条（按 ``seq``
          升序），过滤实现见 ``domain.perception.filter_local_events``；不接受全局流水。
        - ``turn_id``/``save_seed`` 保留给解析 Agent 的调用签名（M2 契约 §4.2），
          切片本身不携带随机种子。
        """
        character = state.characters.get(actor_id)
        if character is not None:
            room_id = character.location
            own_inventory: Sequence[str] = character.inventory
            private_memory = [character.private_memory_ref] if character.private_memory_ref else []
        else:
            npc = state.npcs.get(actor_id)
            if npc is None:
                raise ValueError(f"actor {actor_id} is not present in state")
            room_id = npc.room_id
            own_inventory = ()
            private_memory = [npc.memory_ref]

        room = state.rooms[room_id]
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

        # 房间内物品（``room.item_ids`` 为唯一权威列表；悬空引用防御性跳过）。
        for item_id in room.item_ids:
            item = state.items.get(item_id)
            if item is not None:
                add("item", item.id, item.name)
        # 房间内 NPC：``room.npc_ids`` + 全量扫描兜底（防房间列表漏登记，M2 既有口径）；
        # actor 自己是 NPC 时不得作为可见实体返回（可见实体只描述 actor 之外的场景对象）。
        for npc_id in room.npc_ids:
            room_npc = state.npcs.get(npc_id)
            if room_npc is not None and room_npc.id != actor_id:
                add("npc", room_npc.id, room_npc.name)
        for other_npc in state.npcs.values():
            if other_npc.room_id == room.id and other_npc.id != actor_id:
                add("npc", other_npc.id, other_npc.name)
        # 同房间其他角色（不含 actor 自己；其他房间角色一律不可见）。
        for other in state.characters.values():
            if other.id != actor_id and other.location == room.id:
                add("character", other.id, other.name)
        # actor 自己背包内的物品也算“可见实体”，供“使用/喝”类解析匹配（契约 §3.5）；
        # 其他角色的背包物品绝不进入本切片。
        for item_id in own_inventory:
            item = state.items.get(item_id)
            if item is not None:
                add("item", item.id, item.name)

        return PerceptionSlice(
            actor_id=actor_id,
            visible_entities=entities,
            local_events=filter_local_events(events, actor_id),
            private_memory=private_memory,
            plan_hint=plan_hint,
            # R2 加法（D6 离线 NLU 房间名导航）：「去地窖」类输入靠相邻房间名 → 方向。
            # 编码契约：kind="room"、id=方向英文值、name=目标房间展示名（不含内部 ID）。
            exit_refs=[
                EntityRef(
                    kind="room",
                    id=direction,
                    name=state.rooms[target].name if target in state.rooms else direction,
                )
                for direction, target in room.exits.items()
            ],
        )

    def get_game_view(self, game_id: str) -> dict[str, Any]:
        """返回对局视图 ``{game_id, turn, version, snapshot_version, status, last_text,
        scenario, ending_text}``。

        ``last_text`` 取最近一条事件 ``result["narrative"]``（M2 模板叙事落库口径）；
        ``status`` 由 ``story_ending`` 读时纯求值（story 胜利/倒下 → ``ended``，旧场景恒
        ``active``）；``ending_text`` 为结局正文（未结束为 ``None``）。只读、零写入。
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
        ending = game_ending_of_state(record.state)
        return {
            "game_id": game_id,
            "turn": record.state.turn,
            "version": record.version,
            "snapshot_version": snapshot.snapshot_version if snapshot is not None else 0,
            "status": "ended" if ending is not None else "active",
            "last_text": last_text,
            "scenario": scenario_name_of_state(record.state),
            "ending_text": str(ending["text"]) if ending is not None else None,
        }

    def get_player_view(self, game_id: str) -> dict[str, Any]:
        """返回玩家视角对局视图（只读、零事件、不写库、不调模型）。

        契约（M6 前端 StatusPanel / 终端客户端共用）：

        ``{game_id, turn, version, snapshot_version, status, scenario, hp, max_hp,
        inventory:[{item_id,name,qty}], room:{room_id,title,description,exits,exit_names,
        items,npcs}, goal, ending_text, suggested_actions}``。

        ``turn``/``version``/``snapshot_version``/``status`` 与 ``get_game_view`` 同口径
        （``version`` 仍是下一次提交的 ``expected_version``；本方法不产生任何事件）；
        ``hp``/``max_hp``/``inventory`` 取玩家角色（``PLAYER_ID``）当前状态，
        ``room`` 复用 :meth:`get_room_view` 的可见性口径；``goal``/``ending_text`` 仅
        story 场景有值（旧场景 ``None``），``suggested_actions`` 为当前可执行的下一步
        指令（成功回合也给，D7）。存档不存在抛 :class:`GameNotFoundError`。
        """
        record = self._repo.load(game_id)
        if record is None:
            raise GameNotFoundError(game_id)
        state = record.state
        character = self._require_character(state, PLAYER_ID)
        max_hp = character.attributes.get("max_hp")
        snapshot = self._repo.latest_snapshot(game_id)
        ending = game_ending_of_state(state)
        return {
            "game_id": game_id,
            "turn": state.turn,
            "version": record.version,
            "snapshot_version": snapshot.snapshot_version if snapshot is not None else 0,
            "status": "ended" if ending is not None else "active",
            "scenario": scenario_name_of_state(state),
            "hp": character.hp,
            "max_hp": max_hp if max_hp is not None else character.hp,
            "inventory": self.get_inventory(state, PLAYER_ID)["items"],
            "room": self.get_room_view(state, PLAYER_ID),
            "goal": story_goal(state),
            "ending_text": str(ending["text"]) if ending is not None else None,
            "suggested_actions": suggest_actions(state),
        }

    def list_games(self, *, limit: int = 10, offset: int = 0) -> dict[str, Any]:
        """返回存档列表 ``{games:[...], total}``（只读、零写入；M6 续玩 UI 用）。

        列表项：``{game_id, turn, version, snapshot_version, status, scenario, last_text,
        updated_at}``，按最近提交时间倒序；``total`` 为存档总数。``limit`` 由调用方校验
        （1..50）。不存在的列表为空。
        """
        records = self._repo.list_games(limit=limit, offset=offset)
        games: list[dict[str, Any]] = []
        for record in records:
            last_text: str | None = None
            for event in reversed(self._repo.read_events(record.game_id)):
                narrative = event.result.get("narrative")
                if isinstance(narrative, str) and narrative:
                    last_text = narrative
                    break
            snapshot = self._repo.latest_snapshot(record.game_id)
            ending = game_ending_of_state(record.state)
            games.append(
                {
                    "game_id": record.game_id,
                    "turn": record.state.turn,
                    "version": record.version,
                    "snapshot_version": snapshot.snapshot_version if snapshot is not None else 0,
                    "status": "ended" if ending is not None else "active",
                    "scenario": scenario_name_of_state(record.state),
                    "last_text": last_text,
                    "updated_at": record.updated_at,
                }
            )
        return {"games": games, "total": self._repo.count_games()}

    def read_events(self, game_id: str) -> list[Event]:
        """按 ``seq`` 升序返回存档全部已盖章事件（只读、零写入；M6 回放端点用）。

        未知存档抛 :class:`GameNotFoundError`（接入层映射 404 ``NOT_FOUND`` 信封）。
        ``GameRepository.read_events`` 本身只读，本方法不产生事件/快照/幂等写入。
        """
        record = self._repo.load(game_id)
        if record is None:
            raise GameNotFoundError(game_id)
        return self._repo.read_events(game_id)

    @staticmethod
    def _require_character(state: GameState, actor_id: str) -> Character:
        """取角色；不存在时抛 ``ValueError``（内部调用方保证 actor 存在）。"""
        character = state.characters.get(actor_id)
        if character is None:
            raise ValueError(f"actor {actor_id} is not present in state")
        return character
