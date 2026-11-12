"""动作服务 (第 6 章 6.3、契约 §4.3)：只产草稿 ``Event``，不提交、不写库。

写方法失败返回 ``Result.fail(...)`` 且 ``events=[]``（原子性）；L0 预条件在本层自检
（出口存在、物品在房间/背包、NPC 同房间），不依赖工具层或编排层兜底。草稿事件字段
``turn_id/actor_id/method/event_type/params/result`` 由本层填好，``id/seq/rng_seed/ts``
留给 ``GameEngine`` 盖章（第 5 章 5.3）。

R7 动作包（D-N6，用户批准新增 EventType）：``drop_item``（放下）、``give_item``（给予）、
``attack``（确定性攻击判定）、``improvise``（裁判确认后的临场动作）四方法均为**纯加法**：
只由 ``build_game_tool_registry()`` 中的 freedom 工具经 ``ToolGate.dispatch`` 路由调用，
旧 m2/m3/m4 场景不注册这些工具 → 旧事件流/golden 逐字不变。``attack`` 的 d20 与修正
全部来自 ``DerivedRandom`` 派生种子（禁直接 ``random``），同 seed 同回合可复算。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from textgame.domain.events import Event, EventType
from textgame.domain.models import NPC, GameState
from textgame.domain.results import ErrorCode, Result
from textgame.domain.rules.dice import DerivedRandom
from textgame.domain.scenario import DIRECTION_NAMES
from textgame.domain.scenario_runtime import ScenarioRuntime
from textgame.domain.scenario_story import story_reply
from textgame.domain.types import Direction, ItemId, NpcId
from textgame.services.query_service import QueryService
from textgame.services.tool_context import ToolContext

#: 背包上限与 ``Character.inventory`` 的模型约束保持一致（第 5 章 5.4）。
_INVENTORY_LIMIT = 64

#: 剧情/任务类物品标签前缀（R1 玩家实测 P0-3 安全子集）：带这些标签的物品不可被
#: 「使用」消耗，否则任务物品会被永久销毁。场景数据里「古物」的 tags 为 ``["quest"]``
#: （``domain/scenario.py``）；``quest_item``/``quest.*``/``story.*`` 等同前缀均视为剧情类。
#: 普通消耗品（如 ``consumable``）与一般物品标签（``light``/``weapon``/``item.*``）不受影响。
_STORY_TAG_PREFIXES: tuple[str, ...] = ("quest", "story")

#: D5：武器/工具标签的物品不可被「使用」消耗（world-internal 说法见 :func:`_use_reject_text`）。
#: 第 7 章工具矩阵语义内的安全前置：`weapon`/`tool` 是「拿在手里使」的东西，不是消耗品；
#: m2/m3/m4 golden 的 use_item 输入（m2 治疗药水 consumable、m3 火把 light）均不受影响。
_NON_CONSUMABLE_TAGS: tuple[str, ...] = ("weapon", "tool")

#: D5 世界内拒绝文案（不出现标签名/内部 ID）；按命中标签给不同说法。
_USE_REJECT_TEXT: dict[str, str] = {
    "weapon": "这东西要握在手里才有用，不能这样用掉。",
    "tool": "这工具要拿在手里使，不能这样用掉。",
}
_USE_REJECT_DEFAULT = "这件东西不能这样用掉。"


def _is_story_item(tags: Sequence[str]) -> bool:
    """物品是否带剧情/任务标签（前缀匹配；仅供 ``use_item`` 的前置保护使用）。"""
    for tag in tags:
        for prefix in _STORY_TAG_PREFIXES:
            if tag == prefix or tag.startswith(f"{prefix}_") or tag.startswith(f"{prefix}."):
                return True
    return False


def _non_consumable_tag(tags: Sequence[str]) -> str | None:
    """返回命中的武器/工具标签（``weapon``/``tool`` 或带前缀变体）；未命中返回 ``None``。"""
    for tag in tags:
        for blocked in _NON_CONSUMABLE_TAGS:
            if tag == blocked or tag.startswith(f"{blocked}.") or tag.startswith(f"{blocked}_"):
                return blocked
    return None


def _direction_name(direction: str) -> str:
    """方向英文枚举值 → 中文方向词（叙事与失败消息用）。"""
    return DIRECTION_NAMES.get(direction, direction)


def _room_text(view: dict[str, Any]) -> str:
    """把房间视图拼成一段确定性中文描述（``look`` 的 ``data["text"]``）。"""
    parts: list[str] = [f"{view['title']}。", f"{view['description']}"]
    items = view.get("items") or []
    npcs = view.get("npcs") or []
    exits = view.get("exits") or {}
    if items:
        parts.append("地上有" + "、".join(str(item["name"]) for item in items) + "。")
    if npcs:
        parts.append("这里站着" + "、".join(str(npc["name"]) for npc in npcs) + "。")
    if exits:
        parts.append("可以往" + "、".join(_direction_name(str(key)) for key in exits) + "走。")
    else:
        parts.append("四下没有明显的出口。")
    return "".join(parts)


def _npc_inspect_text(npc: NPC) -> str:
    """NPC 模型无描述字段：按固定规则拼一段确定性「细看」描述（不出现 ID/英文）。"""
    parts = [f"{npc.name}就在近前，你能看清对方脸上被风霜磨出的纹路。"]
    if npc.faction:
        faction = "".join(ch for ch in npc.faction if ch.isprintable())[:12] or "旧日"
        parts.append(f"衣领上留着{faction}的旧记号。")
    parts.append("你没有出声，只把这份印象记在心里。")
    return "".join(parts)


class ActionService:
    """动作写方法；构造注入只读查询服务，``look`` 复用房间视图。"""

    def __init__(
        self,
        query: QueryService,
        *,
        scenario_runtime: ScenarioRuntime | None = None,
    ) -> None:
        """``query`` 用于 ``look`` 读取房间视图（只读，不产生状态）。

        ``scenario_runtime``（用户上传场景）：可选场景钩子注册表；提供时 ``talk_to`` 优先
        走用户场景对话定义，未命中再回退 ``story_reply``（内置场景行为逐字不变）。
        """
        self._query = query
        self._scenario_runtime = scenario_runtime

    def _npc_reply(self, npc_id: NpcId, topic: str) -> dict[str, Any] | None:
        """NPC 回复：用户场景钩子优先，缺省回退内置 story 对话（旧场景不变）。"""
        if self._scenario_runtime is not None:
            reply = self._scenario_runtime.npc_reply(npc_id, topic)
            if reply is not None:
                return reply
        return story_reply(npc_id, topic)

    def look(self, ctx: ToolContext, state: GameState) -> Result:
        """``READ_ONLY``：返回房间描述文本，不产生事件。"""
        view = self._query.get_room_view(state, ctx.actor_id)
        return Result.success(data={"text": _room_text(view)})

    def move_to(self, ctx: ToolContext, state: GameState, *, direction: Direction) -> Result:
        """移动到相邻房间；出口不存在 → ``NOT_FOUND``（L0 预条件）。"""
        character = state.characters.get(ctx.actor_id)
        if character is None:
            return Result.fail(ErrorCode.NOT_FOUND, f"角色不存在: {ctx.actor_id}")
        room = state.rooms.get(character.location)
        if room is None:
            return Result.fail(ErrorCode.NOT_FOUND, f"角色所在地点不存在: {character.location}")
        target_room = room.exits.get(direction)
        if target_room is None:
            return Result.fail(ErrorCode.NOT_FOUND, f"「{_direction_name(direction)}」边没有出口")
        draft = Event(
            turn_id=ctx.turn_id,
            actor_id=ctx.actor_id,
            method="move_to",
            event_type=EventType.CHARACTER_MOVED,
            params={"direction": direction, "from_room": room.id, "to_room": target_room},
            result={"from": room.id, "to": target_room},
        )
        return Result.success(
            data={"from_room": room.id, "to_room": target_room, "direction": direction},
            events=[draft],
        )

    def take_item(self, ctx: ToolContext, state: GameState, *, item_id: ItemId) -> Result:
        """从当前房间拿起物品；不在房间 → ``NOT_FOUND``，背包满 → ``OUT_OF_RANGE``。"""
        character = state.characters.get(ctx.actor_id)
        if character is None:
            return Result.fail(ErrorCode.NOT_FOUND, f"角色不存在: {ctx.actor_id}")
        room = state.rooms.get(character.location)
        if room is None:
            return Result.fail(ErrorCode.NOT_FOUND, f"角色所在地点不存在: {character.location}")
        item = state.items.get(item_id)
        if item is None or item_id not in room.item_ids:
            return Result.fail(ErrorCode.NOT_FOUND, "这里没有这件物品")
        if len(character.inventory) >= _INVENTORY_LIMIT:
            return Result.fail(ErrorCode.OUT_OF_RANGE, "背包已满，无法再拿起物品")
        inventory_size = len(character.inventory) + 1
        draft = Event(
            turn_id=ctx.turn_id,
            actor_id=ctx.actor_id,
            method="take_item",
            event_type=EventType.ITEM_TAKEN,
            params={"item_id": item_id, "from_room": room.id},
            result={"inventory_size": inventory_size},
        )
        return Result.success(
            data={"item_id": item_id, "item_name": item.name, "inventory_size": inventory_size},
            events=[draft],
        )

    def use_item(self, ctx: ToolContext, state: GameState, *, item_id: ItemId) -> Result:
        """使用背包内物品；不在背包 → ``NOT_FOUND``，剧情/任务物品 → ``INVALID_INPUT``。

        剧情/任务物品禁止被「使用」销毁（R1 实测：古物曾可被 use 永久消耗）：
        前置拒绝且零事件、背包不变；普通消耗品的既有行为与事件结构完全不变。
        D5：``weapon``/``tool`` 标签同样拒绝（世界内说法），避免把武器/工具当消耗品烧掉；
        治疗药水（``consumable``）与 m3 golden 的火把（``light``）路径不变。
        """
        character = state.characters.get(ctx.actor_id)
        if character is None:
            return Result.fail(ErrorCode.NOT_FOUND, f"角色不存在: {ctx.actor_id}")
        item = state.items.get(item_id)
        if item is None or item_id not in character.inventory:
            return Result.fail(ErrorCode.NOT_FOUND, "背包里没有这件物品")
        if _is_story_item(item.tags):
            return Result.fail(ErrorCode.INVALID_INPUT, "这件东西不能这样用")
        blocked = _non_consumable_tag(item.tags)
        if blocked is not None:
            return Result.fail(
                ErrorCode.INVALID_INPUT,
                _USE_REJECT_TEXT.get(blocked, _USE_REJECT_DEFAULT),
            )
        draft = Event(
            turn_id=ctx.turn_id,
            actor_id=ctx.actor_id,
            method="use_item",
            event_type=EventType.ITEM_CONSUMED,
            params={"item_id": item_id},
            result={"consumed": True},
        )
        return Result.success(
            data={"item_id": item_id, "item_name": item.name},
            events=[draft],
        )

    def talk_to(self, ctx: ToolContext, state: GameState, *, npc_id: NpcId, topic: str) -> Result:
        """与同房间 NPC 对话；NPC 不存在或不同房间 → ``NOT_FOUND``。

        D8：story 的守夜人老兵由 ``domain.scenario_story.story_reply`` 给确定性回话，
        事件 ``result`` 增 ``reply`` 与剧情化 ``dialogue_state``（``briefed``/``talked``）；
        其它场景/其它 NPC 的 ``story_reply`` 返回 ``None``，事件结构与旧场景逐字不变。
        """
        character = state.characters.get(ctx.actor_id)
        if character is None:
            return Result.fail(ErrorCode.NOT_FOUND, f"角色不存在: {ctx.actor_id}")
        npc = state.npcs.get(npc_id)
        if npc is None or npc.room_id != character.location:
            return Result.fail(ErrorCode.NOT_FOUND, "这里没有可以交谈的人")
        reply = self._npc_reply(npc_id, topic)
        if reply is not None:
            raw_state = reply.get("dialogue_state")
            dialogue_state = raw_state if isinstance(raw_state, str) and raw_state else "greeted"
            reply_text = str(reply.get("reply") or "")
            if dialogue_state != "briefed" and npc.dialogue_state != "briefed":
                # 玩家第一次和守夜人搭话（且 LLM 解析出的 topic 没命中任务关键词）时，
                # 仍给任务说明：否则「和守夜人老兵说话」这类最自然的开局会永远完不成目标 1。
                # 用户上传场景同理：第一次对话若未命中话题关键词，补一次「任务」关键词探测。
                directed = self._npc_reply(npc_id, "任务")
                if directed is not None and directed.get("dialogue_state") == "briefed":
                    reply_text = str(directed.get("reply") or reply_text)
                    dialogue_state = "briefed"
            draft = Event(
                turn_id=ctx.turn_id,
                actor_id=ctx.actor_id,
                method="talk_to",
                event_type=EventType.DIALOGUE_EXCHANGED,
                params={"npc_id": npc_id, "topic": topic},
                result={"dialogue_state": dialogue_state, "reply": reply_text},
            )
            return Result.success(
                data={
                    "npc_id": npc_id,
                    "npc_name": npc.name,
                    "dialogue_state": dialogue_state,
                    "reply": reply_text,
                },
                events=[draft],
            )
        draft = Event(
            turn_id=ctx.turn_id,
            actor_id=ctx.actor_id,
            method="talk_to",
            event_type=EventType.DIALOGUE_EXCHANGED,
            params={"npc_id": npc_id, "topic": topic},
            result={"dialogue_state": "greeted"},
        )
        return Result.success(
            data={"npc_id": npc_id, "npc_name": npc.name, "dialogue_state": "greeted"},
            events=[draft],
        )

    def drop_item(self, ctx: ToolContext, state: GameState, *, item_id: ItemId) -> Result:
        """R7：把背包里的一件物品放到当前房间地面（``ITEM_DROPPED``）。

        L0 自检：actor/房间存在、物品在背包、物品定义存在、当前房间地面无该物品；
        任一不满足 → ``NOT_FOUND``/``INVALID_INPUT`` 且零事件。
        """
        character = state.characters.get(ctx.actor_id)
        if character is None:
            return Result.fail(ErrorCode.NOT_FOUND, f"角色不存在: {ctx.actor_id}")
        room = state.rooms.get(character.location)
        if room is None:
            return Result.fail(ErrorCode.NOT_FOUND, f"角色所在地点不存在: {character.location}")
        item = state.items.get(item_id)
        if item is None or item_id not in character.inventory:
            return Result.fail(ErrorCode.NOT_FOUND, "背包里没有这件物品")
        if item_id in room.item_ids:
            return Result.fail(ErrorCode.INVALID_INPUT, "这件物品已经在地上了")
        draft = Event(
            turn_id=ctx.turn_id,
            actor_id=ctx.actor_id,
            method="drop_item",
            event_type=EventType.ITEM_DROPPED,
            params={"item_id": item_id, "from_room": room.id},
            result={"to_room": room.id, "item_name": item.name},
        )
        return Result.success(
            data={"item_id": item_id, "item_name": item.name, "to_room": room.id},
            events=[draft],
        )

    def give_item(
        self, ctx: ToolContext, state: GameState, *, item_id: ItemId, npc_id: NpcId
    ) -> Result:
        """R7：把背包里的一件物品交给同房间的 NPC（``ITEM_GIVEN``）。

        NPC 模型没有背包字段：物品只离开玩家持有（``state.items`` 保留定义，文档注明）；
        L0 自检：actor 存在、物品在背包、NPC 存在且与 actor 同房间，否则零事件。
        """
        character = state.characters.get(ctx.actor_id)
        if character is None:
            return Result.fail(ErrorCode.NOT_FOUND, f"角色不存在: {ctx.actor_id}")
        item = state.items.get(item_id)
        if item is None or item_id not in character.inventory:
            return Result.fail(ErrorCode.NOT_FOUND, "背包里没有这件物品")
        npc = state.npcs.get(npc_id)
        if npc is None or npc.room_id != character.location:
            return Result.fail(ErrorCode.NOT_FOUND, "这里没有可以交出物品的人")
        draft = Event(
            turn_id=ctx.turn_id,
            actor_id=ctx.actor_id,
            method="give_item",
            event_type=EventType.ITEM_GIVEN,
            params={"item_id": item_id, "npc_id": npc_id},
            result={"npc_name": npc.name},
        )
        return Result.success(
            data={
                "item_id": item_id,
                "item_name": item.name,
                "npc_id": npc_id,
                "npc_name": npc.name,
            },
            events=[draft],
        )

    def attack(self, ctx: ToolContext, state: GameState, *, npc_id: NpcId) -> Result:
        """R7：对同房间目标做一次确定性攻击判定（``COMBAT_RESOLVED``，恒定一条事件）。

        判定公式（契约 §3，禁其他随机源）::

            seed = DerivedRandom.derive_seed(ctx.save_seed, ctx.turn_id, ctx.actor_id,
                                             "attack", 0)
            roll = DerivedRandom(seed).randint(1, 20)
            mod  = (actor.attributes["str"] - 10) // 2
            dc   = 10; total = roll + mod
            hit  = roll == 20 or (roll != 1 and total >= dc)
            damage = 0 if not hit else max(1, 3 + mod)

        目标不存在/不同房间 → ``NOT_FOUND`` 零事件；命中时由 reducer 让目标退出场景。
        """
        character = state.characters.get(ctx.actor_id)
        if character is None:
            return Result.fail(ErrorCode.NOT_FOUND, f"角色不存在: {ctx.actor_id}")
        npc = state.npcs.get(npc_id)
        if npc is None or npc.room_id != character.location:
            return Result.fail(ErrorCode.NOT_FOUND, "这里没有可以攻击的目标")
        seed = DerivedRandom.derive_seed(ctx.save_seed, ctx.turn_id, ctx.actor_id, "attack", 0)
        roll = DerivedRandom(seed).randint(1, 20)
        mod = (character.attributes.get("str", 10) - 10) // 2
        dc = 10
        total = roll + mod
        hit = roll == 20 or (roll != 1 and total >= dc)
        damage = 0 if not hit else max(1, 3 + mod)
        draft = Event(
            turn_id=ctx.turn_id,
            actor_id=ctx.actor_id,
            method="attack",
            event_type=EventType.COMBAT_RESOLVED,
            params={"npc_id": npc_id, "room_id": character.location},
            result={"hit": hit, "roll": roll, "total": total, "dc": dc, "damage": damage},
        )
        return Result.success(
            data={
                "npc_id": npc_id,
                "npc_name": npc.name,
                "hit": hit,
                "roll": roll,
                "total": total,
                "dc": dc,
                "damage": damage,
            },
            events=[draft],
        )

    def improvise(self, ctx: ToolContext, state: GameState, *, action_text: str) -> Result:
        """R7：产 ``IMPROVISED_ACTION`` 草稿（裁判 allow 后由编排层提交）。

        只做形状自检（actor 存在、1..200 字非空文本），不产生数值/物品；``result`` 只带
        ``narrative_hint=action_text[:120]``（契约 §3）。失败 → ``INVALID_INPUT``/``NOT_FOUND``
        零事件；是否需要裁判由 ``RuleEngine`` 的 needs_judge 分支决定，不在本层。
        """
        character = state.characters.get(ctx.actor_id)
        if character is None:
            return Result.fail(ErrorCode.NOT_FOUND, f"角色不存在: {ctx.actor_id}")
        if not isinstance(action_text, str) or not action_text.strip() or len(action_text) > 200:
            return Result.fail(ErrorCode.INVALID_INPUT, "临场动作必须是一段 1..200 字的文本")
        narrative_hint = action_text[:120]
        draft = Event(
            turn_id=ctx.turn_id,
            actor_id=ctx.actor_id,
            method="improvise",
            event_type=EventType.IMPROVISED_ACTION,
            params={"action_text": action_text},
            result={"narrative_hint": narrative_hint},
        )
        return Result.success(data={"narrative_hint": narrative_hint}, events=[draft])

    def inspect(
        self,
        ctx: ToolContext,
        state: GameState,
        *,
        item_id: str | None = None,
        npc_id: str | None = None,
    ) -> Result:
        """细看当前环境里的一件物品或一个人（``READ_ONLY``，零事件、不改状态）。

        - 参数只允许 ``item_id`` / ``npc_id`` 二选一（同时/都缺 → ``INVALID_INPUT``）；
        - 物品必须在玩家背包或当前房间可见，NPC 必须与玩家同房间，否则 ``NOT_FOUND``；
        - 返回 ``data={"kind": "item"|"npc", "name": str, "description": str}`` 供叙事器
          渲染一屏「细看」文本；不产生事件、不触发任何写路径。
        """
        if (item_id is None) == (npc_id is None):
            return Result.fail(ErrorCode.INVALID_INPUT, "要细看什么呢？可以指定一件物品或一个人。")
        character = state.characters.get(ctx.actor_id)
        if character is None:
            return Result.fail(ErrorCode.NOT_FOUND, f"角色不存在: {ctx.actor_id}")
        room = state.rooms.get(character.location)
        if room is None:
            return Result.fail(ErrorCode.NOT_FOUND, f"角色所在地点不存在: {character.location}")
        if item_id is not None:
            item = state.items.get(item_id)
            visible = item_id in room.item_ids or item_id in character.inventory
            if item is None or not visible:
                return Result.fail(ErrorCode.NOT_FOUND, "这里没有可以细看的东西")
            return Result.success(
                data={"kind": "item", "name": item.name, "description": item.description}
            )
        npc = state.npcs.get(npc_id) if npc_id is not None else None
        if npc is None or npc.room_id != character.location:
            return Result.fail(ErrorCode.NOT_FOUND, "这里没有可以细看的人")
        return Result.success(
            data={"kind": "npc", "name": npc.name, "description": _npc_inspect_text(npc)}
        )


__all__ = ["ActionService", "_room_text"]
