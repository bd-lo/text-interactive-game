"""世界书（World Book / Lorebook）领域层：格式解析 + 确定性触发引擎（纯函数，零 IO）。

H4-MVP 契约（`.work/team-player-2026-11-10/H4-CONTRACT.md` §3/§4）：

- 兼容 SillyTavern ``world_info``（``entries`` 为 uid map 或数组，也接受纯 map）、Character Card V2
  ``data.character_book`` / ``character_book`` 与项目简化 schema；只借鉴**数据结构与触发语义**；
- 触发语义为 SillyTavern 子集：``keys``（大小写不敏感、可选整词）、``secondary_keys`` +
  ``selective``/``selective_logic``（0 AND_ANY / 1 NOT_ALL / 2 NOT_ANY / 3 AND_ALL）、``constant``、
  ``order`` 排序、``scan_depth`` 上下文窗口、确定性概率、递归 ≤2 轮（``exclude_recursion`` /
  ``prevent_recursion``）、字符预算截断、最多 ``max_entries`` 条；
- 全模块纯函数、零 IO、零随机（概率用 ``sha256`` 派生，可回放）、不 import 框架；
- 旧场景无 world book → 编排/查询层不调用本模块，行为与 golden 逐字不变。

本模块同时定义 ``WorldBookProvider`` 端口：仓储/服务层（backend）实现
``get_world_book(world_book_id) -> WorldBook | None``，编排层（``TurnOrchestrator``）与查询层
（``QueryService``）通过构造注入消费，避免二者直接依赖持久化实现。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

from textgame.domain.models import GameState
from textgame.domain.scenario import PLAYER_ID

__all__ = [
    "WorldBook",
    "WorldBookEntry",
    "WorldBookProvider",
    "activate_entries",
    "build_activation_context",
    "format_lore_entries",
    "parse_import_payload",
]

#: 简化 schema / 角色卡缺省世界书名。
_DEFAULT_BOOK_NAME: Final[str] = "未命名世界书"
#: 默认上下文窗口条数（SillyTavern ``scanDepth`` 缺省口径）。
_DEFAULT_SCAN_DEPTH: Final[int] = 4
#: 默认条目字符预算（chars；LLM 侧 chars/2.5 近似 token 的口径见文档）。
_DEFAULT_CHAR_BUDGET: Final[int] = 800
#: 默认最多注入条数。
_DEFAULT_MAX_ENTRIES: Final[int] = 8
#: 递归扫描最大轮数（首轮 + 1 轮递归）。
_MAX_RECURSION_ROUNDS: Final[int] = 2
#: ``selective_logic`` 数字 → 枚举名（与 SillyTavern 同值）。
_SELECTIVE_LOGIC_NAMES: Final[dict[int, str]] = {
    0: "AND_ANY",
    1: "NOT_ALL",
    2: "NOT_ANY",
    3: "AND_ALL",
}
#: ``selective_logic`` 枚举名 → 数字（大小写不敏感；兼容常见别名）。
_SELECTIVE_LOGIC_ALIASES: Final[dict[str, int]] = {
    "AND_ANY": 0,
    "NOT_ALL": 1,
    "NOT_ANY": 2,
    "AND_ALL": 3,
}
#: 关键词分隔符（英文逗号 / 中文逗号 / 顿号 / 分号）。
_KEY_SEPARATORS = re.compile(r"[,，、;；]")
#: 整词匹配时视为「词字符」的 ASCII 集合（供正则边界判定）。
_ASCII_WORD_CHAR = re.compile(r"[0-9A-Za-z_]")


class WorldBookProvider(Protocol):
    """世界书提供者端口（backend 的仓储/服务实现；编排层与查询层只消费本协议）。"""

    def get(self, world_book_id: str) -> WorldBook | None:
        """按 id 取世界书；不存在/已软删返回 ``None``（接入层映射 404）。"""
        ...


@dataclass(frozen=True)
class WorldBookEntry:
    """规范化后的世界书条目（字段全量对齐 H4-CONTRACT §3，缺省值 = SillyTavern 缺省口径）。"""

    uid: int
    keys: tuple[str, ...]
    secondary_keys: tuple[str, ...]
    content: str
    enabled: bool = True
    constant: bool = False
    selective: bool = True
    selective_logic: int = 0
    order: int = 100
    position: int = 0
    depth: int = 4
    probability: int = 100
    use_probability: bool = False
    group: str = ""
    scan_depth: int | None = None
    case_sensitive: bool = False
    match_whole_words: bool = False
    exclude_recursion: bool = False
    prevent_recursion: bool = False


@dataclass(frozen=True)
class WorldBook:
    """规范化后的世界书；``entries`` 按 ``uid`` 升序（确定性，供回放/审计）。"""

    name: str
    entries: tuple[WorldBookEntry, ...]
    source: str
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 格式解析（SillyTavern world_info / Character Card V2 / 简化 schema）
# ---------------------------------------------------------------------------
def _as_str(value: Any, default: str = "") -> str:
    """任意值 → 字符串（仅 str 保留；其它类型走缺省，避免把对象/数字当文案）。"""
    return value if isinstance(value, str) else default


def _as_int(value: Any, default: int) -> int:
    """任意值 → 整数（bool 视作非法；数字字符串可解析；失败走缺省）。"""
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        try:
            return int(stripped)
        except ValueError:
            return default
    return default


def _as_bool(value: Any, default: bool = False) -> bool:
    """任意值 → 布尔（兼容 ``"true"/"false"`` 字符串与 0/1）。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off", ""}:
            return False
    return default


def _split_keys(value: Any) -> tuple[str, ...]:
    """关键词规范化：逗号/顿号/分号分隔字符串或数组 → 去空白、去空、去重（保序）。"""
    if value is None:
        return ()
    raw_items: list[Any] = []
    if isinstance(value, str):
        raw_items = _KEY_SEPARATORS.split(value)
    elif isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, str):
                raw_items.extend(_KEY_SEPARATORS.split(item))
            elif isinstance(item, int) and not isinstance(item, bool):
                raw_items.append(str(item))
    normalized: list[str] = []
    for item in raw_items:
        keyword = item.strip() if isinstance(item, str) else ""
        if keyword and keyword not in normalized:
            normalized.append(keyword)
    return tuple(normalized)


def _coerce_selective_logic(value: Any) -> int:
    """``selectiveLogic`` 兼容数字（0..3 或数字串）与枚举名（大小写不敏感）。"""
    if value is None:
        return 0
    if isinstance(value, bool):
        return 3 if value else 0
    if isinstance(value, int):
        return value if value in _SELECTIVE_LOGIC_NAMES else 0
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            numeric = int(stripped)
            return numeric if numeric in _SELECTIVE_LOGIC_NAMES else 0
        return _SELECTIVE_LOGIC_ALIASES.get(stripped.upper().replace(" ", "_"), 0)
    return 0


def _coerce_position(value: Any) -> int:
    """条目插入位置：数字原样；字符卡字符串（before_char/after_char）→ 0/1。"""
    if isinstance(value, str):
        mapped = {"before_char": 0, "after_char": 1}.get(value.strip().lower())
        return mapped if mapped is not None else 0
    return _as_int(value, 0)


def _first_non_empty_str(*values: Any) -> str:
    """取第一个非空字符串（用于书名兜底链）；全部为空 → 缺省书名。"""
    for value in values:
        text = _as_str(value).strip()
        if text:
            return text
    return _DEFAULT_BOOK_NAME


def _entry_uid(entry: dict[str, Any], fallback: int) -> int:
    """条目 uid：优先 ``uid`` 字段，其次 ``id``，最后数组下标 / map 键。"""
    for key in ("uid", "id"):
        value = entry.get(key)
        if value is not None:
            return _as_int(value, fallback)
    return fallback


def _looks_like_tavern_entry(entry: Any) -> bool:
    """SillyTavern 条目标记：``key``/``keysecondary``/``selectiveLogic``/``insertion_order`` 等。"""
    if not isinstance(entry, dict):
        return False
    markers = ("key", "keysecondary", "selectiveLogic", "insertion_order", "disable", "comment")
    return any(marker in entry for marker in markers)


def _normalize_entry(entry: dict[str, Any], *, uid: int) -> WorldBookEntry:
    """SillyTavern / Character Card V2 / 简化 schema 条目 → 规范化 ``WorldBookEntry``。

    兼容差异：``keys``/``key``、``secondary_keys``/``keysecondary``、``order``/``insertion_order``、
    ``enabled``/``disable``（取反）、``probability``/``useProbability`` 等。
    """
    keys = _split_keys(entry.get("key", entry.get("keys")))
    secondary = _split_keys(entry.get("keysecondary", entry.get("secondary_keys")))
    content = _as_str(entry.get("content", entry.get("text", entry.get("entry"))))
    enabled = _as_bool(entry.get("enabled"), True) and not _as_bool(entry.get("disable"), False)
    order = _as_int(entry.get("order", entry.get("insertion_order")), 100)
    depth = _as_int(entry.get("depth"), 4)
    probability = _as_int(entry.get("probability"), 100)
    probability = min(max(probability, 0), 100)
    scan_depth_raw = entry.get("scanDepth", entry.get("scan_depth"))
    scan_depth = None if scan_depth_raw is None else max(_as_int(scan_depth_raw, 4), 0)
    return WorldBookEntry(
        uid=uid,
        keys=keys,
        secondary_keys=secondary,
        content=content,
        enabled=enabled,
        constant=_as_bool(entry.get("constant"), False),
        selective=_as_bool(entry.get("selective"), True),
        selective_logic=_coerce_selective_logic(entry.get("selectiveLogic")),
        order=order,
        position=_coerce_position(entry.get("position")),
        depth=depth,
        probability=probability,
        use_probability=_as_bool(entry.get("useProbability", entry.get("use_probability")), False),
        group=_as_str(entry.get("group")).strip(),
        scan_depth=scan_depth,
        case_sensitive=_as_bool(entry.get("caseSensitive", entry.get("case_sensitive")), False),
        match_whole_words=_as_bool(
            entry.get("matchWholeWords", entry.get("match_whole_words")), False
        ),
        exclude_recursion=_as_bool(
            entry.get("excludeRecursion", entry.get("exclude_recursion")), False
        ),
        prevent_recursion=_as_bool(
            entry.get("preventRecursion", entry.get("prevent_recursion")), False
        ),
    )


def _normalize_entries(entries_raw: Any) -> tuple[WorldBookEntry, ...]:
    """条目集合（uid map / 数组）→ 按 uid 升序的规范化元组；非法结构抛 ``ValueError``。"""
    if isinstance(entries_raw, dict):
        pairs: list[tuple[int, dict[str, Any]]] = []
        for index, (map_key, value) in enumerate(entries_raw.items()):
            if not isinstance(value, dict):
                continue
            fallback = _as_int(map_key, index) if isinstance(map_key, str) else index
            pairs.append((_entry_uid(value, fallback), value))
    elif isinstance(entries_raw, list):
        pairs = [
            (_entry_uid(value, index), value)
            for index, value in enumerate(entries_raw)
            if isinstance(value, dict)
        ]
    else:
        raise ValueError("世界书 entries 必须是对象映射或数组")
    if not pairs and entries_raw:
        raise ValueError("世界书 entries 中没有任何合法条目")
    entries: list[WorldBookEntry] = []
    seen_uids: set[int] = set()
    for uid, entry in pairs:
        if uid in seen_uids:
            raise ValueError(f"世界书条目 uid 重复: {uid}")
        seen_uids.add(uid)
        entries.append(_normalize_entry(entry, uid=uid))
    entries.sort(key=lambda item: item.uid)
    return tuple(entries)


def _detect_character_book(payload: dict[str, Any]) -> tuple[dict[str, Any], str] | None:
    """Character Card V2 检测：``data.character_book`` 优先，其次顶层 ``character_book``。"""
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("character_book"), dict):
        name = _first_non_empty_str(
            data["character_book"].get("name"), data.get("name"), payload.get("name")
        )
        return data["character_book"], name
    if isinstance(payload.get("character_book"), dict):
        book = payload["character_book"]
        return book, _first_non_empty_str(book.get("name"), payload.get("name"))
    return None


def _detect_pure_entry_map(payload: dict[str, Any]) -> dict[str, Any] | None:
    """纯 uid → entry 映射检测（SillyTavern 导出的另一种形态）。"""
    if not payload or not all(isinstance(value, dict) for value in payload.values()):
        return None
    candidates = [value for value in payload.values() if _looks_like_tavern_entry(value)]
    return payload if candidates else None


def parse_import_payload(payload: Any) -> WorldBook:
    """把导入 JSON 解析为规范化 :class:`WorldBook`（格式自动检测）。

    检测顺序（H4-CONTRACT §3）：

    1. Character Card V2：``payload["data"]["character_book"]`` 或 ``payload["character_book"]``
       → ``source="character_card"``（书名取 ``character_book.name`` / 卡名 / 顶层 name）；
    2. SillyTavern ``world_info``：``payload["entries"]`` 为 uid map 或数组，或 payload 本身是
       uid → entry 的纯映射 → ``source="tavern"``；
    3. 项目简化 schema：``{"name", "entries": [{"keys", "content", ...}]}`` → ``source="manual"``。

    非法 JSON 结构 / 不支持的格式 / uid 重复时抛 ``ValueError``（接入层映射 422）；本函数
    零 IO、不修改入参。
    """
    if not isinstance(payload, dict):
        raise ValueError("世界书 JSON 根必须是对象")
    character_book = _detect_character_book(payload)
    if character_book is not None:
        book, name = character_book
        return WorldBook(
            name=name,
            entries=_normalize_entries(book.get("entries", [])),
            source="character_card",
            raw=payload,
        )
    if "entries" in payload:
        entries_raw = payload["entries"]
        if isinstance(entries_raw, dict):
            return WorldBook(
                name=_first_non_empty_str(payload.get("name")),
                entries=_normalize_entries(entries_raw),
                source="tavern",
                raw=payload,
            )
        if isinstance(entries_raw, list):
            is_tavern = any(_looks_like_tavern_entry(entry) for entry in entries_raw)
            return WorldBook(
                name=_first_non_empty_str(payload.get("name")),
                entries=_normalize_entries(entries_raw),
                source="tavern" if is_tavern else "manual",
                raw=payload,
            )
        raise ValueError("世界书 entries 必须是对象映射或数组")
    pure_map = _detect_pure_entry_map(payload)
    if pure_map is not None:
        return WorldBook(
            name=_DEFAULT_BOOK_NAME,
            entries=_normalize_entries(pure_map),
            source="tavern",
            raw=payload,
        )
    raise ValueError(
        "无法识别的世界书格式（支持 SillyTavern world_info / 角色卡 V2 / 简化 schema）"
    )


# ---------------------------------------------------------------------------
# 上下文构造（编排层与查询层共用，保证 /view lore 与回合注入一致）
# ---------------------------------------------------------------------------
def build_activation_context(
    state: GameState,
    *,
    narrative_texts: Sequence[str] = (),
    player_input: str | None = None,
    max_narratives: int = 6,
    max_entity_names: int = 8,
) -> list[str]:
    """组装触发用上下文文本（确定性、纯函数）。

    顺序约定（``activate_entries`` 的 ``scan_depth`` 取窗口末尾 ``N`` 条，缺省 4）：

    1. 最近 ``max_narratives`` 条叙事（旧→新）；
    2. 可见物品名、在场 NPC 名；
    3. 当前房间名、房间描述；
    4. 玩家输入（最后一项）。

    默认窗口因此总能覆盖「当前房间名/描述 + 玩家输入」；历史叙事只有在条目显式放宽
    ``scan_depth`` 时才进入窗口（SillyTavern ``scanDepth`` 语义的项目化口径）。
    """
    parts: list[str] = []
    recent = [text for text in narrative_texts if isinstance(text, str) and text.strip()]
    parts.extend(recent[-max_narratives:])
    character = state.characters.get(PLAYER_ID)
    room = state.rooms.get(character.location) if character is not None else None
    if room is not None:
        item_names = [
            state.items[item_id].name for item_id in room.item_ids if item_id in state.items
        ][:max_entity_names]
        if item_names:
            parts.append("、".join(item_names))
        npc_names = [state.npcs[npc_id].name for npc_id in room.npc_ids if npc_id in state.npcs][
            :max_entity_names
        ]
        if npc_names:
            parts.append("、".join(npc_names))
        parts.append(room.name)
        parts.append(room.description)
    if isinstance(player_input, str) and player_input.strip():
        parts.append(player_input)
    return parts


def format_lore_entries(entries: Sequence[WorldBookEntry]) -> list[str]:
    """激活条目 → 叙事注入文本（``【世界设定】内容``；空内容条目跳过）。"""
    return [f"【世界设定】{entry.content}" for entry in entries if entry.content.strip()]


# ---------------------------------------------------------------------------
# 确定性触发引擎
# ---------------------------------------------------------------------------
def _context_hash(context_texts: Sequence[str]) -> str:
    """上下文文本的稳定摘要（用于确定性概率派生；与条目/回合无关）。"""
    joined = "\x1f".join(context_texts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def _key_in_text(text: str, key: str, *, case_sensitive: bool, whole_words: bool) -> bool:
    """单个关键词是否命中文本（默认子串/大小写不敏感；整词仅对 ASCII 键加词边界）。"""
    if not key:
        return False
    if not case_sensitive:
        text_cmp = text.casefold()
        key_cmp = key.casefold()
    else:
        text_cmp = text
        key_cmp = key
    if not whole_words or not _ASCII_WORD_CHAR.search(key_cmp):
        return key_cmp in text_cmp
    pattern = r"(?<![0-9A-Za-z_])" + re.escape(key_cmp) + r"(?![0-9A-Za-z_])"
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.search(pattern, text_cmp, flags=flags) is not None


def _entry_window(entry: WorldBookEntry, context_texts: Sequence[str]) -> list[str]:
    """条目上下文窗口：取末尾 ``scan_depth`` 条（缺省 4；0 表示空窗口）。"""
    depth = _DEFAULT_SCAN_DEPTH if entry.scan_depth is None else entry.scan_depth
    if depth <= 0:
        return []
    return list(context_texts[-depth:])


def _secondary_logic_matches(entry: WorldBookEntry, window_text: str) -> bool:
    """按 ``selective_logic`` 判定 secondary 关键词组（0/1/2/3 语义见契约 §3）。"""
    keys = entry.secondary_keys
    if not keys:
        return True
    matched = sum(
        1
        for key in keys
        if _key_in_text(
            window_text,
            key,
            case_sensitive=entry.case_sensitive,
            whole_words=entry.match_whole_words,
        )
    )
    logic = entry.selective_logic
    if logic == 1:  # NOT_ALL：并非全部 secondary 命中才激活
        return matched < len(keys)
    if logic == 2:  # NOT_ANY：一个都不命中才激活
        return matched == 0
    if logic == 3:  # AND_ALL：全部命中才激活
        return matched == len(keys)
    return matched > 0  # 0 = AND_ANY：任一命中即激活


def _probability_passes(
    entry: WorldBookEntry, *, save_seed: str, turn_id: int, ctx_hash: str
) -> bool:
    """确定性概率：``sha256(seed:turn:uid:ctx)`` 取 0..99，``< probability`` 才激活。"""
    if not entry.use_probability:
        return True
    if entry.probability >= 100:
        return True
    if entry.probability <= 0:
        return False
    digest = hashlib.sha256(f"{save_seed}:{turn_id}:{entry.uid}:{ctx_hash}".encode()).hexdigest()
    return int(digest[:8], 16) % 100 < entry.probability


def _entry_matches(
    entry: WorldBookEntry,
    window_texts: Sequence[str],
    *,
    save_seed: str,
    turn_id: int,
    ctx_hash: str,
) -> bool:
    """判定单条目是否在本轮激活（constant → 无条件；否则主关键词 + secondary + 概率）。"""
    if entry.constant:
        return True
    window_text = "\n".join(window_texts)
    primary_hit = any(
        _key_in_text(
            window_text,
            key,
            case_sensitive=entry.case_sensitive,
            whole_words=entry.match_whole_words,
        )
        for key in entry.keys
    )
    if not primary_hit:
        return False
    if (
        entry.selective
        and entry.secondary_keys
        and not _secondary_logic_matches(entry, window_text)
    ):
        return False
    return _probability_passes(entry, save_seed=save_seed, turn_id=turn_id, ctx_hash=ctx_hash)


def _apply_budget(
    entries: Sequence[WorldBookEntry], *, max_entries: int, char_budget: int
) -> list[WorldBookEntry]:
    """按 ``(order 升序, uid 升序)`` 排序后按 ``char_budget``（含 1 字节分隔符）截断。"""
    ordered = sorted(entries, key=lambda entry: (entry.order, entry.uid))
    selected: list[WorldBookEntry] = []
    used = 0
    for entry in ordered:
        if len(selected) >= max_entries:
            break
        cost = len(entry.content) + (1 if selected else 0)
        if used + cost > char_budget:
            break
        selected.append(entry)
        used += cost
    return selected


def activate_entries(
    book: WorldBook,
    *,
    context_texts: Sequence[str],
    turn_id: int,
    save_seed: str,
    max_entries: int = _DEFAULT_MAX_ENTRIES,
    char_budget: int = _DEFAULT_CHAR_BUDGET,
) -> list[WorldBookEntry]:
    """确定性计算本回合激活的世界书条目（纯函数，可回放）。

    语义（H4-CONTRACT §3）：
    1. ``enabled`` 为假永不激活；``constant`` 无条件激活（仍受预算/条数裁剪）；
    2. 主关键词：``keys`` 任一命中上下文窗口（默认子串+大小写不敏感；``match_whole_words``
       对 ASCII 键加词边界；``case_sensitive`` 区分大小写）；
    3. ``selective`` 且 ``secondary_keys`` 非空 → 按 ``selective_logic``（0 AND_ANY / 1 NOT_ALL /
       2 NOT_ANY / 3 AND_ALL）判定；
    4. ``use_probability`` → ``sha256(f"{save_seed}:{turn_id}:{uid}:{ctx_hash}")`` 映射 0..99，
       小于 ``probability`` 才激活（``ctx_hash`` 由上下文文本派生，回放一致）；
    5. 每个条目的窗口取上下文末尾 ``scan_depth`` 条（缺省 4）；
    6. 递归：首轮激活条目 content（``exclude_recursion=False``）追加进上下文再扫第 2 轮；
       ``prevent_recursion=True`` 不在递归轮被激活；最多 2 轮；
    7. 结果按 ``(order, uid)`` 升序、按 ``char_budget`` 累加长度（含分隔符）截断、最多
       ``max_entries`` 条。
    """
    if max_entries <= 0 or char_budget <= 0 or not book.entries:
        return []
    context = [text for text in context_texts if isinstance(text, str) and text]
    ctx_hash = _context_hash(context)
    enabled = [entry for entry in book.entries if entry.enabled]
    active: dict[int, WorldBookEntry] = {}
    round_context = context
    for round_index in range(_MAX_RECURSION_ROUNDS):
        matched_this_round: list[WorldBookEntry] = []
        for entry in enabled:
            if entry.uid in active:
                continue
            if round_index > 0 and entry.prevent_recursion:
                continue
            if _entry_matches(
                entry,
                _entry_window(entry, round_context),
                save_seed=save_seed,
                turn_id=turn_id,
                ctx_hash=ctx_hash,
            ):
                matched_this_round.append(entry)
        for entry in matched_this_round:
            active[entry.uid] = entry
        additions = [
            entry.content
            for entry in matched_this_round
            if entry.content and not entry.exclude_recursion
        ]
        if not additions:
            break
        round_context = [*context, *additions]
    return _apply_budget(list(active.values()), max_entries=max_entries, char_budget=char_budget)
