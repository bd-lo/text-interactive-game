"""模板叙事器 ``TemplateNarrator`` (第 9 章 9.2 步骤 10、契约 §4.5 步骤 8)。

M2 采用方案 A (第 3 章 §3.6): 叙事默认确定性。``TemplateNarrator`` 按事件类型/动作挑固定
中文模板并插入 ``GameState`` 中的实体名, 由编排层把文本写回草稿事件
``result["narrative"]`` / ``result["narrative_source"]="template"`` (第 14 章 14.7、契约 §3.3)。

硬约束 (第 2 章 §2.3):
- 每屏 80–150 字 (中文按 ``len`` 计), 由 :func:`_fit_text` 双保险收敛;
- 一屏只推进一个动作结果, 并留一个可行动的钩子;
- 不读时钟、不用随机、不渲染 LLM 内容; 同一输入两次渲染逐字一致 (可回放);
- 实体名只从 ``GameState``/内置场景常量取, 玩家原文最多引用 12 字 (防止长文本灌入)。

M2 判定结果暂不写进事件 (007 Phase 1 已交付 ``RuleEngine``, 编排层只做前置检查),
因此本叙事器只渲染成功动作; 失败动作由编排层直接返回 ``TurnResult(ok=False)``。
"""

from __future__ import annotations

from typing import Any, Protocol

from textgame.agents.dto import ParseOutcome
from textgame.domain.events import Event, EventType
from textgame.domain.models import Character, GameState, Room
from textgame.domain.scenario import DIRECTION_NAMES, PLAYER_ID

_MIN_TEXT_CHARS = 80
_MAX_TEXT_CHARS = 150
_MAX_TOPIC_CHARS = 12

_HOOKS: tuple[str, ...] = (
    "你放轻呼吸，留意着四周的动静。",
    "远处隐约传来水滴落下的回声。",
    "空气里浮着灰尘与旧木头的气味。",
)

_GENERIC_TEXT = (
    "你试着动了动，四周的景物没有太大的变化，只有风从废墟的缝隙里穿过，卷起一小片灰尘。"
    "你重新站定，留意着周围的动静，在心里把接下来的打算又过了一遍，等待下一个可以行动的时机。"
)


class Narrator(Protocol):
    """叙事器端口 (与 ``turn_orchestrator.NarratorLike`` 同签名)。"""

    def render(
        self,
        *,
        action: ParseOutcome,
        events: list[Event],
        state: GameState,
        result_data: dict[str, Any],
    ) -> str:
        """把本回合动作/事件渲染成 80–150 字中文屏文本 (确定性优先)。"""
        ...


def _fit_text(text: str) -> str:
    """把文本确定性收敛到 80–150 字: 超长回退到句末标点截断, 不足补固定钩子句。"""
    stripped = text.strip()
    if len(stripped) > _MAX_TEXT_CHARS:
        cut = stripped[:_MAX_TEXT_CHARS]
        for index in range(len(cut) - 1, _MIN_TEXT_CHARS - 1, -1):
            if cut[index] in "。！？；":
                cut = cut[: index + 1]
                break
        stripped = cut
    while len(stripped) < _MIN_TEXT_CHARS:
        stripped += _HOOKS[len(stripped) % len(_HOOKS)]
    return stripped[:_MAX_TEXT_CHARS]


#: 句末标点（句级去重/取末句用；省略号是单字符 ``…``）。
_SENTENCE_ENDS = "。！？；…"


def _split_sentences(text: str) -> list[str]:
    """按中文句末标点切句（保留标点；不跨句重组）；确定性、零 IO。"""
    sentences: list[str] = []
    start = 0
    for index, char in enumerate(text):
        if char in _SENTENCE_ENDS:
            sentences.append(text[start : index + 1])
            start = index + 1
    if start < len(text):
        sentences.append(text[start:])
    return sentences


def _last_sentence(text: str) -> str:
    """取文本最后一句（空文本返回空串）；供补句去重判断。"""
    sentences = _split_sentences(text)
    return sentences[-1] if sentences else ""


def _dedupe_adjacent_sentences(text: str) -> str:
    """合并**相邻完全相同**的句子（inspect/对话文案去重专用，不改非该路径输出）。

    只合并紧邻的相同句，不做跨句去重：既修「空气里浮着灰尘与旧木头的气味。」连续
    两遍的问题，又不会误删场景里正常重复的意象句。
    """
    merged: list[str] = []
    for sentence in _split_sentences(text):
        if not merged or merged[-1] != sentence:
            merged.append(sentence)
    return "".join(merged)


def _pad_text_no_repeat(text: str) -> str:
    """把不足 80 字的文本补钩子句到 80 字，**跳过与上一句相同的钩子**（确定性轮转）。

    背景（AC-35）：``_fit_text`` 的补句用 ``_HOOKS[len % 3]``，三个钩子中两个长 15 字
    （≡0 mod 3），当 ``len % 3 == 2`` 时会连续追加同一句（inspect 短描述尤甚）。
    本函数只用于 inspect/对话 reply 等**无 golden 钉死**的路径；补完仍由 ``_fit_text``
    统一收口（此时长度已 ≥80，不会再加钩子，只做 150 字截断）。
    """
    padded = text
    index = 0
    while len(padded) < _MIN_TEXT_CHARS:
        last = _last_sentence(padded)
        for offset in range(len(_HOOKS)):
            candidate = _HOOKS[(index + offset) % len(_HOOKS)]
            if candidate != last:
                padded += candidate
                index = (index + offset + 1) % len(_HOOKS)
                break
        else:  # pragma: no cover - _HOOKS 三句互不相同，循环必有候选
            break
    return padded


def _direction_name(direction: Any) -> str:
    """Direction 英文值 → 中文方向词; 非字符串回退为「前方」。"""
    if not isinstance(direction, str):
        return "前方"
    return DIRECTION_NAMES.get(direction, direction)


def _room_name(state: GameState, room_id: Any) -> str:
    """房间 id → 展示名; 未知房间回退为「前方」。"""
    if isinstance(room_id, str):
        room = state.rooms.get(room_id)
        if room is not None:
            return room.name
    return "前方"


def _item_name(state: GameState, item_id: Any) -> str:
    """物品 id → 展示名; 未知物品回退为「那件物品」。"""
    if isinstance(item_id, str):
        item = state.items.get(item_id)
        if item is not None:
            return item.name
    return "那件物品"


def _npc_name(state: GameState, npc_id: Any) -> str:
    """NPC id → 展示名; 未知 NPC 回退为「那个人」。"""
    if isinstance(npc_id, str):
        npc = state.npcs.get(npc_id)
        if npc is not None:
            return npc.name
    return "那个人"


def _actor(state: GameState) -> Character | None:
    """取玩家角色 (M2 单角色; 缺省再退到任意一个角色, 保持确定性)。"""
    character = state.characters.get(PLAYER_ID)
    if character is not None:
        return character
    return next(iter(state.characters.values()), None)


def _current_room(state: GameState) -> Room | None:
    """玩家当前房间; 状态缺角色/房间时返回 ``None`` (叙事走通用兜底)。"""
    character = _actor(state)
    if character is None:
        return None
    return state.rooms.get(character.location)


def _topic_excerpt(event: Event) -> str:
    """取对话主题的短摘录 (最多 12 字), 避免把长玩家原文灌进一屏。"""
    topic = str(event.params.get("topic", "")).replace("\n", " ").strip()
    if not topic:
        return "你好"
    return topic[:_MAX_TOPIC_CHARS]


# ---------------------------------------------------------------------------
# 模板: 写动作 (按 event_type)
# ---------------------------------------------------------------------------


def _moved_text(event: Event, state: GameState) -> str:
    """``CHARACTER_MOVED``: 方向 + 目标房间名 (契约 §3.3 的 params/result)。

    P2-5（R3 玩家体验）：``up``/``down`` 走「爬梯/下梯」专项文案，避免「你沿着上边的
    通道动身」这类生硬方向词；其余四个方向（north/south/east/west）**逐字保持原模板**
    （m2/m3/m4 golden 只涉及这四个方向，text_len 钉死，不得改动）。
    """
    raw_direction = event.params.get("direction", "")
    to_room = event.result.get("to", event.params.get("to_room"))
    room_name = _room_name(state, to_room)
    if raw_direction == "up":
        return (
            "你顺着窄梯往上走，木档在脚下吱呀发响，越往上风越紧。"
            f"走了不多时，{room_name}的气息扑面而来，光线也跟着亮了一截。"
            "你放慢脚步站定，打量着这里的环境。"
        )
    if raw_direction == "down":
        return (
            "你沿着窄梯往下走，石壁上的霜被衣袖蹭落，脚下每一级都踩得很实。"
            f"走了不多时，{room_name}的气息扑面而来，光线也跟着暗了一截。"
            "你放慢脚步站定，打量着这里的环境。"
        )
    direction = _direction_name(raw_direction)
    return (
        f"你沿着{direction}边的通道动身，脚底的石板缝隙里积着陈年的灰，石阶上浮着一层薄薄的潮气。"
        f"走了不多时，{room_name}的气息扑面而来，光线也跟着暗了一截。"
        "你放慢脚步站定，打量着这里的环境。"
    )


def _taken_text(event: Event, state: GameState) -> str:
    """``ITEM_TAKEN``: 物品名 + 入行囊。"""
    item_name = _item_name(state, event.params.get("item_id"))
    return (
        f"你弯下腰，把{item_name}从地上拾起来，掸掉表面的一层浮尘。"
        f"{item_name}的分量比看上去更趁手，你把它收进行囊，行囊里多了一点踏实的感觉。"
        "你重新直起身，拍了拍衣角，留意着四周的动静。"
    )


def _used_text(event: Event, state: GameState) -> str:
    """``ITEM_CONSUMED``: 物品名 + 使用后的余韵；烈酒（``hp_delta`` 负）走专属分支。"""
    item_name = _item_name(state, event.params.get("item_id"))
    if _has_negative_hp_delta(event, state):
        return (
            f"你拧开{item_name}的瓶塞，烈酒呛得喉咙发烫，寒气被逼退了一些，"
            "眼前却晃了一下，脚步不得不钉在原地。"
            "你把瓶子收好，缓了几口气，重新留意四周的动静。"
        )
    return (
        f"你取出{item_name}，按着记忆里的用法将它用掉。"
        "药水里带着一点草根的苦味，很快被温热带了下去，先前的疲惫也被压住了一些。"
        "你活动了一下手腕，把空瓶收好，重新留意四周的动静。"
    )


def _has_negative_hp_delta(event: Event, state: GameState) -> bool:
    """物品 ``stats.hp_delta`` 为负（如 story 的结霜烈酒）→ 用专属扣血文案。"""
    item_id = event.params.get("item_id")
    item = state.items.get(item_id) if isinstance(item_id, str) else None
    if item is None:
        return False
    delta = item.stats.get("hp_delta")
    return isinstance(delta, (int, float)) and not isinstance(delta, bool) and delta < 0


_QUALITY_WORDS: dict[str, str] = {
    "poor": "粗糙",
    "common": "趁手",
    "fine": "精良",
    "masterwork": "传世",
}


def _crafted_text(event: Event, state: GameState) -> str:
    """CRAFT_DETERMINISTIC/CRAFT_GENERATED：成功/掷骰失败两分支（长度由 _fit_text 收敛）。"""
    craft = event.result.get("craft")
    success = isinstance(craft, dict) and craft.get("success") is True
    if not success:
        return (
            "你按着记熟的工序把材料摆开，生火、预热、敲打，可最后一步的火候还是偏了一点。"
            "手底下的材料没能成形，你把还能用的部分收拢起来，吹灭炉边的火星。"
            "你拍了拍衣角上的灰，盘算着是再试一次，还是先去寻些更合适的材料。"
        )
    names: list[str] = []
    produced = event.result.get("produced_items")
    if isinstance(produced, list):
        for produced_item in produced:
            if isinstance(produced_item, dict):
                name = produced_item.get("name")
                if isinstance(name, str) and name:
                    names.append(name)
    item_name = names[0] if names else "成品"
    quality = craft.get("quality") if isinstance(craft, dict) else None
    quality_word = _QUALITY_WORDS.get(quality, "趁手") if isinstance(quality, str) else "趁手"
    return (
        f"你按着记熟的工序把材料一样样摆开，生火、加热、敲打，手上的动作渐渐连成一片。"
        f"等最后一道工序收住，{item_name}已经成形，{quality_word}的成色在炉火里泛出一点冷光。"
        "你把它拿在手里掂了掂，分量正好，于是收进行囊，又环顾四周盘算下一步。"
    )


def _talked_text(event: Event, state: GameState) -> str:
    """``DIALOGUE_EXCHANGED``: NPC 名 + 对话摘录 + 等待回应。

    D8：story 的 NPC 事件带 ``result.reply`` 时渲染 NPC 真回话（旧场景无 reply → 旧文案逐字不变）。
    AC-35（R3）：reply 分支删除固定框架句「{npc}沉默片刻，抬眼看你。」——story 回话自身
    常以「老兵抬起眼：」起头，叠加后出现重复叙述；旧场景 else 分支保持逐字不变（m2 golden
    的 ``对老哨兵问好`` text_len=83 不受影响）。
    """
    npc_name = _npc_name(state, event.params.get("npc_id"))
    reply = event.result.get("reply")
    if isinstance(reply, str) and reply.strip():
        # 玩家最需要读到的是 NPC 回话本身：不再重复玩家原文，也不在回话外再包一层「」，
        # 避免与场景文案自带的引号嵌套（reply 本身可能包含『』）。回话较长时优先保留
        # 回话正文（尾句用短句），首尾由 _dedupe/_pad 保证 80–150 字与无相邻重复句。
        base = f"你转向{npc_name}，把心里的话说了出去。{reply.strip()}你把这些话记在心里。"
        return _pad_text_no_repeat(_dedupe_adjacent_sentences(base))
    return (
        f"你转向{npc_name}，把心里的话说了出去：「{_topic_excerpt(event)}」。"
        f"{npc_name}沉默片刻，抬眼打量你，随后缓缓点了点头，似乎在斟酌该如何回应。"
        "你等着对方开口，没有急着追问，只是安静地站着。"
    )


# ---------------------------------------------------------------------------
# 模板: 只读动作 (无事件)
# ---------------------------------------------------------------------------


def _look_text(result_data: dict[str, Any], state: GameState) -> str:
    """``get_room_view``: 复用房间描述文本, 补一屏钩子。"""
    base = result_data.get("text")
    if isinstance(base, str) and base:
        return f"{base}你把眼前的景象在心里过了一遍，留意着可能变化的角落。"
    room = _current_room(state)
    if room is not None:
        return (
            f"你环顾四周，{room.name}的光线有些昏暗。{room.description}"
            "你把这些细节记在心里，留意着可能变化的角落。"
        )
    return "你环顾四周，四周静悄悄的，只有风从废墟的缝隙里穿过。你把这些细节记在心里，留意着可能变化的角落。"


def _inventory_text(result_data: dict[str, Any]) -> str:
    """``get_inventory``: 行囊物品名列表 (最多 4 件), 不输出数量。"""
    raw_items = result_data.get("items")
    names: list[str] = []
    if isinstance(raw_items, list):
        for item in raw_items:
            if isinstance(item, dict):
                name = item.get("name")
                if isinstance(name, str) and name:
                    names.append(name)
    if names:
        joined = "、".join(names[:4])
        return (
            f"你低头翻开行囊，{joined}都还在，边角被一路的尘土磨得发亮。"
            "你把它们重新理了理，合上行囊，盘算着下一步该往哪里走，先做什么、后做什么。"
        )
    return (
        "你低头翻开行囊，里面空空如也，只有几粒灰尘落在袋底。"
        "你把袋口重新扎紧，指尖在粗糙的布面上按了按。"
        "总得先找到些能用的东西，你重新打量四周，盘算着下一步该往哪里走。"
    )


def _inspect_text(result_data: dict[str, Any]) -> str | None:
    """``inspect_item`` 只读结果 → 一屏「细看」文本；缺字段返回 ``None`` 走兜底。

    AC-35（R3）：短描述经外层 ``_fit_text`` 补钩子句时会出现相邻重复句（「空气里浮着
    灰尘与旧木头的气味。」连两遍，铜线甚至连三遍）。这里做**句级去重 + 补句去重**；
    只影响 inspect 路径，``_fit_text`` 本体与其他模板零改动（m2/m3/m4 golden 零漂移）。
    """
    name = result_data.get("name")
    description = result_data.get("description")
    if not isinstance(name, str) or not name:
        return None
    if not isinstance(description, str) or not description:
        return None
    kind = result_data.get("kind")
    subject = "这个人" if kind == "npc" else "这件东西"
    base = f"你凑近细看{name}。{description}你把{subject}的细节记在心里，才慢慢移开视线。"
    return _pad_text_no_repeat(_dedupe_adjacent_sentences(base))


class TemplateNarrator:
    """确定性模板叙事器: 按事件类型/动作选模板, 无 random/时钟/LLM 依赖。"""

    def render(
        self,
        *,
        action: ParseOutcome,
        events: list[Event],
        state: GameState,
        result_data: dict[str, Any],
    ) -> str:
        """渲染一屏中文叙事; 未知动作/事件类型走通用兜底 (长度仍达标)。"""
        if events:
            return _fit_text(self._render_event(events[0], state))
        return _fit_text(self._render_read_only(action, result_data, state))

    @staticmethod
    def _render_event(event: Event, state: GameState) -> str:
        """按 ``event_type`` 选写动作模板; 未知类型走通用兜底。"""
        event_type = event.event_type
        if event_type is EventType.CHARACTER_MOVED:
            return _moved_text(event, state)
        if event_type is EventType.ITEM_TAKEN:
            return _taken_text(event, state)
        if event_type is EventType.ITEM_CONSUMED:
            return _used_text(event, state)
        if event_type is EventType.DIALOGUE_EXCHANGED:
            return _talked_text(event, state)
        if event_type in (EventType.CRAFT_DETERMINISTIC, EventType.CRAFT_GENERATED):
            return _crafted_text(event, state)
        return _GENERIC_TEXT

    @staticmethod
    def _render_read_only(
        action: ParseOutcome, result_data: dict[str, Any], state: GameState
    ) -> str:
        """只读动作: ``inspect_item`` 细看描述、``get_inventory`` 列行囊、房间查看复用描述。"""
        if action.tool == "inspect_item":
            inspected = _inspect_text(result_data)
            if inspected is not None:
                return inspected
            return _GENERIC_TEXT
        if action.tool == "get_inventory":
            return _inventory_text(result_data)
        if action.tool in {"get_room_view", "look"}:
            return _look_text(result_data, state)
        return _GENERIC_TEXT


__all__ = ["Narrator", "TemplateNarrator"]
