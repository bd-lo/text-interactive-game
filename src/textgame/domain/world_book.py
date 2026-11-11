"""世界书（World Book / Lorebook）领域层：格式解析 + 确定性触发引擎（纯函数，零 IO）。

H4-MVP 契约（`.work/team-player-2026-11-10/H4-CONTRACT.md` §3/§4）：

- 兼容 SillyTavern ``world_info``（``entries`` 为 uid map 或数组，也接受纯 map）、Character Card V2
  ``data.character_book`` / ``character_book`` 与项目简化 schema；只借鉴**数据结构与触发语义**；
- 触发语义为 SillyTavern 子集：``keys``（大小写不敏感、可选整词）、``secondary_keys`` +
  ``selective``/``selective_logic``（0 AND_ANY / 1 NOT_ALL / 2 NOT_ANY / 3 AND_ALL）、``constant``、
  ``order`` 排序、``scan_depth`` 上下文窗口、确定性概率、递归 ≤2 轮（``exclude_recursion`` /
  ``prevent_recursion``）、字符预算截断、最多 ``max_entries`` 条；
- **P1 酒馆级升级（本轮加法，全部确定性/可回放）**：

  1. 正则键：键形如 ``/pattern/flags``（仅 i/m/s）→ Python ``re.search``；导入期编译校验，
     非法/超长（pattern > 200 字符）给 warning 并按字面量匹配；正则只匹配文本前 2000 字符；
  2. 定时效果：``sticky``/``cooldown``/``delay``（0..100 回合）；由 ``activate_entries`` 的
     可选 ``history``（``Sequence[ActivationTurn]``）逐回合模拟，``history=None`` 时**逐字等价
     旧实现**（旧测试零改动）；历史可由 :func:`history_from_events` 从事件日志纯函数重建；
  3. 包含组：同 ``group`` 的命中条目每回合最多 1 条，选择顺序
     ``group_override`` 优先 → ``group_weight`` 降序 → ``order`` 降序 → ``uid`` 升序；
  4. ``position``/``depth`` 参与 lore 块拼接排序（``position`` 升序、``depth`` 降序、``order``
     升序、``uid`` 升序），不改变 LLM prompt 消息结构（cassette 不破）；
- 全模块纯函数、零 IO、零随机（概率用 ``sha256`` 派生，可回放）、不 import 框架；
- 旧场景无 world book → 编排/查询层不调用本模块，行为与 golden 逐字不变。

本模块同时定义 ``WorldBookProvider`` 端口：仓储/服务层（backend）实现
``get_world_book(world_book_id) -> WorldBook | None``，编排层（``TurnOrchestrator``）与查询层
（``QueryService``）通过构造注入消费，避免二者直接依赖持久化实现。
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Final, Protocol

from textgame.domain.events import Event
from textgame.domain.models import GameState
from textgame.domain.scenario import PLAYER_ID

__all__ = [
    "ActivationTurn",
    "WorldBook",
    "WorldBookEntry",
    "WorldBookProvider",
    "WorldBookWarning",
    "activate_entries",
    "build_activation_context",
    "format_lore_entries",
    "history_from_events",
    "parse_entry_extras",
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
#: 定时效果（sticky/cooldown/delay）回合数上限（契约：0..100；越界收敛）。
_MAX_TIMED_EFFECT_TURNS: Final[int] = 100
#: 历史回放最多包含的回合数（超出只回放最近 N 回合，保证长局成本有界）。
_MAX_HISTORY_TURNS: Final[int] = 100
#: 正则键 pattern 最大长度；超长按字面量处理（防灾难性回溯/畸形输入）。
_MAX_REGEX_KEY_CHARS: Final[int] = 200
#: 正则匹配的输入文本上限（字符）；只匹配前 N 字符（防灾难性回溯）。
_MAX_REGEX_INPUT_CHARS: Final[int] = 2000
#: 正则键支持的 flags（SillyTavern 常见 i/m/s；g/u/y 等不支持 → 按字面量处理）。
_SUPPORTED_REGEX_FLAGS: Final[frozenset[str]] = frozenset({"i", "m", "s"})
#: 正则键形状：``/pattern/flags``（pattern 至少 1 字符；flags 仅字母）。
_REGEX_KEY_SHAPE: Final[re.Pattern[str]] = re.compile(
    r"^/(?P<pattern>.+)/(?P<flags>[A-Za-z]*)$", re.DOTALL
)
#: 无效整数的哨兵值（用于区分「字段非法」与「合法 0」并给出导入告警）。
_INVALID_INT: Final[int] = -(10**9)
#: 条目级「已解析但本版不生效」的字段 → 告警说明（原始值仍保留在 ``raw_json``）。
_UNSUPPORTED_ENTRY_FIELDS: Final[dict[str, str]] = {
    "vectorized": "向量检索（本项目无 embedding/网络依赖）",
    "characterFilter": "按角色/标签过滤",
    "triggers": "生成类型过滤",
    "matchPersonaDescription": "额外匹配来源（persona 描述）",
    "matchCharacterDescription": "额外匹配来源（角色描述）",
    "matchCharacterPersonality": "额外匹配来源（角色性格）",
    "matchCharacterDepthPrompt": "额外匹配来源（角色深度提示）",
    "matchScenario": "额外匹配来源（场景）",
    "matchCreatorNotes": "额外匹配来源（作者备注）",
    "delayUntilRecursion": "仅在递归轮触发",
    "delayedUntilRecursion": "仅在递归轮触发",
    "role": "@D 插入角色",
    "outlet": "宏插入点",
    "outletName": "宏插入点",
    "automationId": "Quick Reply 自动化（安全边界：不执行导入内容中的任何命令）",
    "priority": "token 预算优先级（CCv2）",
}
#: 世界书/角色书级全局激活设置 → 告警说明（本项目固定参数，不参与运行）。
_UNSUPPORTED_BOOK_FIELDS: Final[dict[str, str]] = {
    "scan_depth": "全局扫描深度",
    "token_budget": "token 预算",
    "recursive_scanning": "递归扫描",
}
#: 导入告警码（接入层转既有 ``[{code, message}]`` 形状，只增条目）。
WARNING_REGEX_KEY_INVALID: Final[str] = "REGEX_KEY_INVALID"
WARNING_TIMED_EFFECT_CLAMPED: Final[str] = "TIMED_EFFECT_CLAMPED"
WARNING_TIMED_EFFECTS_HISTORY_ONLY: Final[str] = "TIMED_EFFECTS_HISTORY_ONLY"
WARNING_GROUP_WEIGHT_INVALID: Final[str] = "GROUP_WEIGHT_INVALID"
WARNING_GROUP_SCORING_UNSUPPORTED: Final[str] = "GROUP_SCORING_UNSUPPORTED"
WARNING_UNSUPPORTED_FIELD: Final[str] = "UNSUPPORTED_FIELD"
WARNING_BOOK_GLOBAL_SETTINGS_IGNORED: Final[str] = "BOOK_GLOBAL_SETTINGS_IGNORED"


class WorldBookProvider(Protocol):
    """世界书提供者端口（backend 的仓储/服务实现；编排层与查询层只消费本协议）。"""

    def get(self, world_book_id: str) -> WorldBook | None:
        """按 id 取世界书；不存在/已软删返回 ``None``（接入层映射 404）。"""
        ...


@dataclass(frozen=True)
class WorldBookWarning:
    """导入期诊断（不阻塞导入；接入层转成既有 ``[{code, message}]`` 告警形状）。"""

    code: str
    message: str


@dataclass(frozen=True)
class ActivationTurn:
    """定时效果逐回合回放所需的历史回合快照（只读、可回放）。

    ``context_texts`` 为该回合用于关键词匹配的上下文。事件日志没有落库玩家输入，
    编排层/查询层重建时以该回合已提交事件的 ``result["narrative"]`` 为准
    （见 :func:`history_from_events`）。
    """

    turn_id: int
    context_texts: tuple[str, ...]


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
    # P1 酒馆级：定时效果（0 = 关闭；0..100 回合）与包含组（group_weight/override/scoring）。
    sticky: int = 0
    cooldown: int = 0
    delay: int = 0
    group_weight: float = 1.0
    group_override: bool = False
    group_scoring: bool = False


@dataclass(frozen=True)
class WorldBook:
    """规范化后的世界书；``entries`` 按 ``uid`` 升序（确定性，供回放/审计）。"""

    name: str
    entries: tuple[WorldBookEntry, ...]
    source: str
    raw: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[WorldBookWarning, ...] = ()


@dataclass(frozen=True)
class EntryExtras:
    """仓储固定列之外/新增的触发字段（从原始 entry 或 ``raw_json`` 恢复）。"""

    probability: int = 100
    use_probability: bool = False
    group: str = ""
    scan_depth: int | None = None
    sticky: int = 0
    cooldown: int = 0
    delay: int = 0
    group_weight: float = 1.0
    group_override: bool = False
    group_scoring: bool = False


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


def _as_float(value: Any, default: float) -> float:
    """任意值 → 有限浮点（bool 视作非法；数字字符串可解析；NaN/Inf 走缺省）。"""
    if isinstance(value, bool):
        return default
    number: float | None = None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return default
    if number is None or not math.isfinite(number):
        return default
    return number


def _coerce_timed_turns(value: Any) -> int:
    """定时效果字段 → 0..100 回合（None/非法 → 0；越界收敛，确定性）。"""
    return min(max(_as_int(value, 0), 0), _MAX_TIMED_EFFECT_TURNS)


def _is_finite_number(value: Any) -> bool:
    """``value`` 是否可解析为有限数字（``groupWeight`` 校验用；bool 视作非法）。"""
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    if isinstance(value, str):
        try:
            return math.isfinite(float(value.strip()))
        except ValueError:
            return False
    return False


def parse_entry_extras(raw: Mapping[str, Any]) -> EntryExtras:
    """从原始条目 JSON 解析「仓储固定列之外/新增」的触发字段（纯函数、幂等）。

    仓储（``world_book_entries``）只持久化 H4 契约 §3 的固定列；``probability``/
    ``use_probability``/``group``/``scan_depth`` 与 P1 新增的定时效果、包含组字段只保留在
    ``raw_json`` 中。服务层把落库记录还原成 ``WorldBookEntry`` 时用本函数补齐，避免为新增
    语义改表/迁移，也让旧数据（raw 里已有这些字段）无需重导即可生效。
    """
    raw = raw if isinstance(raw, Mapping) else {}
    scan_depth_raw = raw.get("scanDepth", raw.get("scan_depth"))
    return EntryExtras(
        probability=min(max(_as_int(raw.get("probability"), 100), 0), 100),
        use_probability=_as_bool(raw.get("useProbability", raw.get("use_probability")), False),
        group=_as_str(raw.get("group")).strip(),
        scan_depth=None if scan_depth_raw is None else max(_as_int(scan_depth_raw, 4), 0),
        sticky=_coerce_timed_turns(raw.get("sticky")),
        cooldown=_coerce_timed_turns(raw.get("cooldown")),
        delay=_coerce_timed_turns(raw.get("delay")),
        group_weight=_as_float(raw.get("groupWeight", raw.get("group_weight")), 1.0),
        group_override=_as_bool(raw.get("groupOverride", raw.get("group_override")), False),
        group_scoring=_as_bool(raw.get("useGroupScoring", raw.get("group_scoring")), False),
    )


def _split_regex_segments(text: str) -> list[str] | None:
    """把「纯正则键列表」字符串切成若干 ``/pattern/flags`` 段；不是纯列表 → ``None``。

    用于避免把正则 pattern 内的逗号误当关键词分隔符：``"/a,b/i"`` 整体是一个正则键，
    ``"/a/i, /b/i"`` 是按分隔符排列的两个正则键。只有整串仅由正则键 + 分隔符组成时才返回
    切分结果；否则调用方退回既有逗号/顿号/分号切分。
    """
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    segments: list[str] = []
    index = 0
    length = len(stripped)
    while index < length:
        while index < length and stripped[index].isspace():
            index += 1
        if index >= length:
            break
        if stripped[index] != "/":
            return None
        start = index
        index += 1
        closed = False
        while index < length:
            char = stripped[index]
            if char == "\\" and index + 1 < length:
                index += 2
                continue
            if char == "/":
                closed = True
                index += 1
                break
            index += 1
        if not closed or index - start <= 2:  # 「/」+ 空 pattern + 「/」不算正则
            return None
        while index < length and stripped[index].isalpha():
            index += 1
        segments.append(stripped[start:index].strip())
        while index < length and stripped[index].isspace():
            index += 1
        if index < length:
            if _KEY_SEPARATORS.match(stripped, index) is None:
                return None
            index += 1
    return segments or None


def _split_key_string(text: str) -> list[str]:
    """关键词字符串 → 关键词条目列表（正则键列表优先按正则段切分）。"""
    segments = _split_regex_segments(text)
    if segments is not None:
        return segments
    return _KEY_SEPARATORS.split(text)


def _split_keys(value: Any) -> tuple[str, ...]:
    """关键词规范化：逗号/顿号/分号分隔字符串或数组 → 去空白、去空、去重（保序）。

    正则键（``/pattern/flags``）作为整体保留，不按逗号切开；数组项内的逗号串仍按旧口径
    拆成多个纯文本关键词（``["火把,松脂"]`` → ``("火把", "松脂")``）。
    """
    if value is None:
        return ()
    raw_items: list[Any] = []
    if isinstance(value, str):
        raw_items = _split_key_string(value)
    elif isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, str):
                segments = _split_regex_segments(item)
                if segments is not None:
                    raw_items.extend(segments)
                else:
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


# ---------------------------------------------------------------------------
# 正则键（Python ``re``；与 SillyTavern/JS 正则的差异见兼容文档）
# ---------------------------------------------------------------------------
#: 灾难性回溯（ReDoS）启发式：组内已有量词、组本身再被量词修饰（如 ``(a+)+``）。
#: Python ``re`` 无超时，导入期直接拒绝这类高风险 pattern（R-12）；正常 ``(ab)+`` 不受影响。
_NESTED_QUANTIFIER_RE = re.compile(r"\((?:[^()]|\\.)*[+*][^()]*\)\s*(?:[+*]|\{\d*,?\d*\})")


def _has_nested_repeat(pattern: str) -> bool:
    """用 ``re`` 解析树检测「重复内部又含重复」（R-12 强化）。

    正则启发式可能漏掉 ``(?:a+){2,}`` 等写法；解析树按 SUBPATTERN/BRANCH/REPEAT 遍历，
    只要 REPEAT 嵌套 REPEAT 就拒绝。保守误拒（如 ``(ab{2})+``）可接受：作者可改写为
    等价的安全正则。
    """
    try:
        from importlib import import_module

        try:
            sre_parse = import_module("re._parser")  # Python 3.11+
        except ModuleNotFoundError:  # pragma: no cover - Python 3.10
            sre_parse = import_module("sre_parse")
    except Exception:  # pragma: no cover - 极端解释器差异
        return False
    try:
        parsed = sre_parse.parse(pattern)
    except Exception:
        return False
    repeat_ops = {
        op
        for op in (
            getattr(sre_parse, "MAX_REPEAT", None),
            getattr(sre_parse, "MIN_REPEAT", None),
            getattr(sre_parse, "POSSESSIVE_REPEAT", None),
        )
        if op is not None
    }

    def walk(tokens: Any, inside_repeat: bool) -> bool:
        for op, value in tokens:
            if op in repeat_ops:
                if inside_repeat:
                    return True
                if walk(value[2], True):
                    return True
            elif op is getattr(sre_parse, "SUBPATTERN", None):
                if walk(value[-1], inside_repeat):
                    return True
            elif op is getattr(sre_parse, "BRANCH", None):
                for alternative in value[1]:
                    if walk(alternative, inside_repeat):
                        return True
        return False

    return walk(parsed, False)


def _looks_like_regex_key(key: str) -> bool:
    """键是否为 ``/pattern/flags`` 形式（正则键）。

    只有「以 / 开头、以 / + 可选字母 flags 结尾、pattern 非空」才算；``/foo`` 这类不闭合的
    键按普通字面量关键词处理（与旧行为一致）。
    """
    return _REGEX_KEY_SHAPE.match(key) is not None


def _parse_regex_key(key: str) -> tuple[str, str] | None:
    """``/pattern/flags`` → ``(pattern, flags)``；非正则形状 → ``None``。"""
    match = _REGEX_KEY_SHAPE.match(key)
    if match is None:
        return None
    return match.group("pattern"), match.group("flags")


@lru_cache(maxsize=512)
def _compile_regex_key(key: str, case_sensitive: bool = False) -> re.Pattern[str] | None:
    """编译正则键（导入期校验与激活期匹配共用同一口径）。

    返回 ``None`` 表示该键**不可作为正则使用**：pattern 超长、flags 不在 i/m/s 内或语法错误；
    调用方此时必须按字面量匹配（不静默丢弃关键词）。默认（条目 ``case_sensitive=false``）补
    ``re.IGNORECASE``，与普通关键词「缺省大小写不敏感」一致；``match_whole_words`` 对正则键
    不生效（正则自己用 ``\\b`` 表达边界）。
    """
    parsed = _parse_regex_key(key)
    if parsed is None:
        return None
    pattern, flags_text = parsed
    if len(pattern) > _MAX_REGEX_KEY_CHARS:
        return None
    if _NESTED_QUANTIFIER_RE.search(pattern) is not None or _has_nested_repeat(pattern):
        return None
    flags = 0
    for flag in flags_text.lower():
        if flag not in _SUPPORTED_REGEX_FLAGS:
            return None
        flags |= {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL}[flag]
    if not case_sensitive:
        flags |= re.IGNORECASE
    try:
        return re.compile(pattern, flags)
    except re.error:
        return None


def _regex_key_issue(key: str) -> str | None:
    """正则键的导入期编译诊断（``None`` = 合法可用）。"""
    parsed = _parse_regex_key(key)
    if parsed is None:
        return None
    pattern, flags_text = parsed
    if len(pattern) > _MAX_REGEX_KEY_CHARS:
        return f"pattern 长度 {len(pattern)} 超过上限 {_MAX_REGEX_KEY_CHARS}"
    if _NESTED_QUANTIFIER_RE.search(pattern) is not None or _has_nested_repeat(pattern):
        return "正则包含嵌套量词（可能灾难性回溯），已拒绝"
    unsupported = sorted(
        {flag for flag in flags_text.lower() if flag not in _SUPPORTED_REGEX_FLAGS}
    )
    if unsupported:
        return f"不支持的 flags {''.join(unsupported)}（仅支持 i/m/s）"
    try:
        re.compile(pattern)
    except re.error as exc:
        return f"正则语法错误（{exc.msg}）"
    return None


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
    extras = parse_entry_extras(entry)
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
        probability=extras.probability,
        use_probability=extras.use_probability,
        group=extras.group,
        scan_depth=extras.scan_depth,
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
        sticky=extras.sticky,
        cooldown=extras.cooldown,
        delay=extras.delay,
        group_weight=extras.group_weight,
        group_override=extras.group_override,
        group_scoring=extras.group_scoring,
    )


def _entry_warnings(entry_raw: Mapping[str, Any], entry: WorldBookEntry) -> list[WorldBookWarning]:
    """单条目的导入期诊断（正则键/定时效果/包含组/未支持字段；不阻塞导入）。

    只产出「已解析但需要玩家知道」的说明；字段原文始终保留在 ``raw_json``，不影响其它条目。
    """
    warnings: list[WorldBookWarning] = []
    for key in entry.keys:
        if not _looks_like_regex_key(key):
            continue
        issue = _regex_key_issue(key)
        if issue is not None:
            warnings.append(
                WorldBookWarning(
                    code=WARNING_REGEX_KEY_INVALID,
                    message=(
                        f"条目 {entry.uid} 的正则键 {key!r} 无法使用（{issue}），"
                        "已按字面量关键词匹配"
                    ),
                )
            )
    for timed_field, limit in (
        ("sticky", _MAX_TIMED_EFFECT_TURNS),
        ("cooldown", _MAX_TIMED_EFFECT_TURNS),
        ("delay", _MAX_TIMED_EFFECT_TURNS),
    ):
        raw_value = entry_raw.get(timed_field)
        if raw_value is None:
            continue
        parsed = _as_int(raw_value, _INVALID_INT)
        normalized = getattr(entry, timed_field)
        if parsed == _INVALID_INT or parsed != normalized:
            warnings.append(
                WorldBookWarning(
                    code=WARNING_TIMED_EFFECT_CLAMPED,
                    message=(
                        f"条目 {entry.uid} 的 {timed_field}={raw_value!r} 非法或超出 "
                        f"0..{limit}，已按 {normalized} 回合处理"
                    ),
                )
            )
    weight_raw = entry_raw.get("groupWeight", entry_raw.get("group_weight"))
    if weight_raw is not None and not _is_finite_number(weight_raw):
        warnings.append(
            WorldBookWarning(
                code=WARNING_GROUP_WEIGHT_INVALID,
                message=f"条目 {entry.uid} 的 groupWeight={weight_raw!r} 非法，已按 1.0 处理",
            )
        )
    if entry.group_scoring:
        warnings.append(
            WorldBookWarning(
                code=WARNING_GROUP_SCORING_UNSUPPORTED,
                message=(
                    f"条目 {entry.uid} 的 useGroupScoring 已保留；本版组内选择按 "
                    "groupOverride → groupWeight → order → uid，不使用命中评分"
                ),
            )
        )
    for field_name, description in _UNSUPPORTED_ENTRY_FIELDS.items():
        raw_value = entry_raw.get(field_name)
        if raw_value is None or raw_value is False or raw_value == 0 or raw_value == "":
            continue
        if isinstance(raw_value, (list, dict)) and not raw_value:
            continue
        warnings.append(
            WorldBookWarning(
                code=WARNING_UNSUPPORTED_FIELD,
                message=(
                    f"条目 {entry.uid} 的字段 {field_name}（{description}）暂未支持，"
                    "已保留原始值且不参与激活"
                ),
            )
        )
    return warnings


def _normalize_entries(
    entries_raw: Any,
) -> tuple[tuple[WorldBookEntry, ...], tuple[WorldBookWarning, ...]]:
    """条目集合（uid map / 数组）→ 按 uid 升序的规范化元组 + 导入期告警。

    非法结构抛 ``ValueError``（接入层映射 422）；告警按原始条目顺序（map 键序/数组下标序）、
    条内固定顺序产出，同一 payload 逐次一致（确定性，便于测试/前端展示）。
    """
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
    warnings: list[WorldBookWarning] = []
    seen_uids: set[int] = set()
    for uid, entry_raw in pairs:
        if uid in seen_uids:
            raise ValueError(f"世界书条目 uid 重复: {uid}")
        seen_uids.add(uid)
        entry = _normalize_entry(entry_raw, uid=uid)
        entries.append(entry)
        warnings.extend(_entry_warnings(entry_raw, entry))
    entries.sort(key=lambda item: item.uid)
    return tuple(entries), tuple(warnings)


def _book_warnings(
    entries: Sequence[WorldBookEntry],
    entry_warnings: Sequence[WorldBookWarning],
    *,
    source_book: Mapping[str, Any] | None,
) -> tuple[WorldBookWarning, ...]:
    """书级导入告警：全局激活设置未支持 + 定时效果需要历史（逐书一次，不阻塞导入）。"""
    warnings = list(entry_warnings)
    if source_book is not None:
        unsupported = [field for field in _UNSUPPORTED_BOOK_FIELDS if field in source_book]
        if unsupported:
            names = "、".join(_UNSUPPORTED_BOOK_FIELDS[field] for field in unsupported)
            warnings.append(
                WorldBookWarning(
                    code=WARNING_BOOK_GLOBAL_SETTINGS_IGNORED,
                    message=(
                        f"世界书全局设置 {names} 不参与本项目运行（固定参数：递归 ≤2 轮 / "
                        "最多 8 条 / char_budget 800），已保留原始值"
                    ),
                )
            )
    if any(entry.sticky or entry.cooldown or entry.delay for entry in entries):
        warnings.append(
            WorldBookWarning(
                code=WARNING_TIMED_EFFECTS_HISTORY_ONLY,
                message=(
                    "定时效果（sticky/cooldown/delay）依赖逐回合事件历史：回合注入按历史模拟，"
                    "无历史的调用（如未接历史重建的只读视图）仅按关键词激活"
                ),
            )
        )
    return tuple(warnings)


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
    零 IO、不修改入参。解析同时产出**导入期告警**（``WorldBook.warnings``：正则键非法、
    定时效果越界、未支持字段等），由服务层转成既有 ``[{code, message}]`` 形状。
    """
    if not isinstance(payload, dict):
        raise ValueError("世界书 JSON 根必须是对象")
    character_book = _detect_character_book(payload)
    if character_book is not None:
        book, name = character_book
        entries, entry_warnings = _normalize_entries(book.get("entries", []))
        return WorldBook(
            name=name,
            entries=entries,
            source="character_card",
            raw=payload,
            warnings=_book_warnings(entries, entry_warnings, source_book=book),
        )
    if "entries" in payload:
        entries_raw = payload["entries"]
        if isinstance(entries_raw, dict):
            entries, entry_warnings = _normalize_entries(entries_raw)
            return WorldBook(
                name=_first_non_empty_str(payload.get("name")),
                entries=entries,
                source="tavern",
                raw=payload,
                warnings=_book_warnings(entries, entry_warnings, source_book=None),
            )
        if isinstance(entries_raw, list):
            is_tavern = any(_looks_like_tavern_entry(entry) for entry in entries_raw)
            entries, entry_warnings = _normalize_entries(entries_raw)
            return WorldBook(
                name=_first_non_empty_str(payload.get("name")),
                entries=entries,
                source="tavern" if is_tavern else "manual",
                raw=payload,
                warnings=_book_warnings(entries, entry_warnings, source_book=None),
            )
        raise ValueError("世界书 entries 必须是对象映射或数组")
    pure_map = _detect_pure_entry_map(payload)
    if pure_map is not None:
        entries, entry_warnings = _normalize_entries(pure_map)
        return WorldBook(
            name=_DEFAULT_BOOK_NAME,
            entries=entries,
            source="tavern",
            raw=payload,
            warnings=_book_warnings(entries, entry_warnings, source_book=None),
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


def history_from_events(
    events: Sequence[Event],
    *,
    limit: int | None = None,
) -> tuple[ActivationTurn, ...]:
    """从已提交事件日志重建定时效果回放历史（纯函数、只读、可回放）。

    口径（与兼容文档 §5 D-8 对应）：

    - 按 ``turn_id`` 升序，每回合一个 :class:`ActivationTurn`；同一回合多条事件的叙事相同，
      ``context_texts`` 取该回合所有事件的 ``result["narrative"]``（按事件 ``seq`` 顺序、
      去空、去重），通常只有 1 条；
    - 没有叙事的事件（``game_created`` 等）与**零事件只读回合**（无叙事落库）不进入历史；
    - **玩家输入不入事件日志**，因此历史回合的关键词匹配上下文以叙事为准（文档口径）；
    - ``limit`` 为可选回合上限（只保留最近 N 回合）；无论 ``limit`` 为何，内部再收敛到
      ``_MAX_HISTORY_TURNS``（100）以保证长局回放成本有界。
    """
    by_turn: dict[int, list[str]] = {}
    for event in events:
        narrative = event.result.get("narrative")
        if not isinstance(narrative, str) or not narrative.strip():
            continue
        bucket = by_turn.setdefault(event.turn_id, [])
        if narrative not in bucket:
            bucket.append(narrative)
    turns = [
        ActivationTurn(turn_id=turn_id, context_texts=tuple(texts))
        for turn_id, texts in sorted(by_turn.items())
    ]
    if limit is not None and limit >= 0:
        turns = turns[-limit:] if limit else []
    if len(turns) > _MAX_HISTORY_TURNS:
        turns = turns[-_MAX_HISTORY_TURNS:]
    return tuple(turns)


# ---------------------------------------------------------------------------
# 确定性触发引擎
# ---------------------------------------------------------------------------
def _context_hash(context_texts: Sequence[str]) -> str:
    """上下文文本的稳定摘要（用于确定性概率派生；与条目/回合无关）。"""
    joined = "\x1f".join(context_texts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def _key_in_text(text: str, key: str, *, case_sensitive: bool, whole_words: bool) -> bool:
    """单个关键词是否命中文本（默认子串/大小写不敏感；整词仅对 ASCII 键加词边界）。

    正则键（``/pattern/flags``）用 Python ``re.search`` 于文本前 ``_MAX_REGEX_INPUT_CHARS``
    字符内匹配；编译失败/超长/非法 flags 时按**字面量**匹配（不静默丢弃关键词）。
    """
    if not key:
        return False
    if _looks_like_regex_key(key):
        compiled = _compile_regex_key(key, case_sensitive)
        if compiled is not None:
            return compiled.search(text[:_MAX_REGEX_INPUT_CHARS]) is not None
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


# ---------------------------------------------------------------------------
# P1 酒馆级：定时效果（history 逐回合模拟）/ 包含组 / lore 排序
# ---------------------------------------------------------------------------
@dataclass
class _TimedState:
    """单条目的定时效果回放状态（只在 ``history`` 非 None 时使用）。"""

    last_trigger_turn: int | None = None
    first_match_turn: int | None = None


def _has_timed_effect(entry: WorldBookEntry) -> bool:
    """条目是否声明了任一 scheduled effect（sticky/cooldown/delay > 0）。"""
    return entry.sticky > 0 or entry.cooldown > 0 or entry.delay > 0


def _simulate_timed_history(
    entries: Sequence[WorldBookEntry],
    history: Sequence[ActivationTurn],
    *,
    save_seed: str,
    current_turn_id: int,
) -> dict[int, _TimedState]:
    """逐回合回放历史，算出每条「有定时效果」条目的首次命中/最近触发回合。

    确定性：只读历史快照（不写任何状态），每回合按该回合 ``context_texts`` 重算关键词命中
    （含概率公式，turn_id/ctx_hash 均取该回合值）；只回放最近 ``_MAX_HISTORY_TURNS`` 回合。
    没有定时效果的条目不参与回放（成本与效果都为零）。
    """
    timed = [entry for entry in entries if _has_timed_effect(entry)]
    states: dict[int, _TimedState] = {entry.uid: _TimedState() for entry in timed}
    if not timed:
        return states
    turns = [turn for turn in history if turn.turn_id < current_turn_id]
    turns.sort(key=lambda turn: turn.turn_id)
    if len(turns) > _MAX_HISTORY_TURNS:
        turns = turns[-_MAX_HISTORY_TURNS:]
    for turn in turns:
        context = [text for text in turn.context_texts if isinstance(text, str) and text]
        ctx_hash = _context_hash(context)
        for entry in timed:
            if not _entry_matches(
                entry,
                _entry_window(entry, context),
                save_seed=save_seed,
                turn_id=turn.turn_id,
                ctx_hash=ctx_hash,
            ):
                continue
            state = states[entry.uid]
            if state.first_match_turn is None:
                state.first_match_turn = turn.turn_id
            if turn.turn_id - state.first_match_turn < entry.delay:
                continue  # delay 未到：不改最近触发回合
            if (
                state.last_trigger_turn is not None
                and turn.turn_id - state.last_trigger_turn <= entry.cooldown
            ):
                continue  # cooldown 内：不刷新触发回合
            state.last_trigger_turn = turn.turn_id
    return states


def _sticky_active(entry: WorldBookEntry, state: _TimedState | None, *, turn_id: int) -> bool:
    """是否处于 sticky 续期窗口（触发后 N 回合内即使关键词不命中仍持续激活）。"""
    return (
        state is not None
        and state.last_trigger_turn is not None
        and turn_id - state.last_trigger_turn <= entry.sticky
    )


def _timed_fires(entry: WorldBookEntry, state: _TimedState | None, *, turn_id: int) -> bool:
    """本回合「自然命中」是否真正激活（sticky 续期 / delay 未到 / cooldown 冷却）。"""
    if state is None:
        return True
    if _sticky_active(entry, state, turn_id=turn_id):
        return True  # sticky：触发后 N 回合内持续激活（含不命中的回合）
    first_match_turn = state.first_match_turn if state.first_match_turn is not None else turn_id
    if turn_id - first_match_turn < entry.delay:
        return False  # delay：首次命中后延迟 N 回合才生效
    return not (
        state.last_trigger_turn is not None and turn_id - state.last_trigger_turn <= entry.cooldown
    )


def _group_rank(entry: WorldBookEntry) -> tuple[int, float, int, int]:
    """组内选择键：group_override 优先 → group_weight 降序 → order 降序 → uid 升序。"""
    return (0 if entry.group_override else 1, -entry.group_weight, -entry.order, entry.uid)


def _select_group_winners(
    matched: Sequence[WorldBookEntry],
    *,
    active: Mapping[int, WorldBookEntry],
    active_groups: dict[str, int],
) -> tuple[list[WorldBookEntry], list[int]]:
    """包含组裁剪：同一 ``group`` 每回合最多 1 条（跨递归轮同样生效）。

    返回 ``(本轮胜出条目, 需从 active 移除的被覆盖 uid)``。组已在更早递归轮激活时：
    命中的 ``group_override=true`` 条目可替换组内当前条目，否则该组本轮候选全部丢弃；
    无 group 的条目原样通过（旧行为不变）。
    """
    winners: list[WorldBookEntry] = []
    replaced_uids: list[int] = []
    grouped: dict[str, list[WorldBookEntry]] = {}
    for entry in matched:
        if entry.group:
            grouped.setdefault(entry.group, []).append(entry)
        else:
            winners.append(entry)
    for group, candidates in grouped.items():
        best = min(candidates, key=_group_rank)
        current_uid = active_groups.get(group)
        current = active.get(current_uid) if current_uid is not None else None
        if current is None:
            winners.append(best)
            active_groups[group] = best.uid
            continue
        if best.group_override and not current.group_override:
            winners.append(best)
            active_groups[group] = best.uid
            replaced_uids.append(current.uid)
    return winners, replaced_uids


def _lore_order_key(entry: WorldBookEntry) -> tuple[int, int, int, int]:
    """lore 块拼接顺序：position 升序 → depth 降序 → order 升序 → uid 升序。"""
    return (entry.position, -entry.depth, entry.order, entry.uid)


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
    history: Sequence[ActivationTurn] | None = None,
) -> list[WorldBookEntry]:
    """确定性计算本回合激活的世界书条目（纯函数，可回放）。

    语义（H4-CONTRACT §3 + P1 酒馆级升级）：
    1. ``enabled`` 为假永不激活；``constant`` 无条件激活（仍受预算/条数裁剪）；
    2. 主关键词：``keys`` 任一命中上下文窗口（默认子串+大小写不敏感；``match_whole_words``
       对 ASCII 键加词边界；``case_sensitive`` 区分大小写；``/pattern/flags`` 为正则键）；
    3. ``selective`` 且 ``secondary_keys`` 非空 → 按 ``selective_logic``（0 AND_ANY / 1 NOT_ALL /
       2 NOT_ANY / 3 AND_ALL）判定；
    4. ``use_probability`` → ``sha256(f"{save_seed}:{turn_id}:{uid}:{ctx_hash}")`` 映射 0..99，
       小于 ``probability`` 才激活（``ctx_hash`` 由上下文文本派生，回放一致）；
    5. 每个条目的窗口取上下文末尾 ``scan_depth`` 条（缺省 4）；
    6. 递归：首轮激活条目 content（``exclude_recursion=False``）追加进上下文再扫第 2 轮；
       ``prevent_recursion=True`` 不在递归轮被激活；最多 2 轮；
    7. **包含组**（P1）：同 ``group`` 的命中条目每回合最多 1 条，选择顺序
       ``group_override`` 优先 → ``group_weight`` 降序 → ``order`` 降序 → ``uid`` 升序；
       无 ``group`` 的条目不受影响；
    8. **定时效果**（P1）：``history`` 非 None 时先逐回合回放 ``sticky``/``cooldown``/``delay``
       得到各条目状态，再据此判定首轮命中的条目是否真正激活（递归轮不受定时约束）；
       ``history`` 为 None 时**逐字等价旧实现**（不模拟任何时间窗，旧测试零改动）；
    9. 结果先按 ``(order, uid)`` 升序做 ``char_budget``/``max_entries`` 截断，再按
       ``(position 升序, depth 降序, order 升序, uid 升序)`` 排序返回（lore 块拼接顺序；
       不改变 LLM prompt 消息结构）。
    """
    if max_entries <= 0 or char_budget <= 0 or not book.entries:
        return []
    context = [text for text in context_texts if isinstance(text, str) and text]
    ctx_hash = _context_hash(context)
    enabled = [entry for entry in book.entries if entry.enabled]
    timed_states = (
        _simulate_timed_history(enabled, history, save_seed=save_seed, current_turn_id=turn_id)
        if history is not None
        else {}
    )
    active: dict[int, WorldBookEntry] = {}
    active_groups: dict[str, int] = {}
    round_context = context
    for round_index in range(_MAX_RECURSION_ROUNDS):
        matched_this_round: list[WorldBookEntry] = []
        for entry in enabled:
            if entry.uid in active:
                continue
            if round_index > 0 and entry.prevent_recursion:
                continue
            matched = _entry_matches(
                entry,
                _entry_window(entry, round_context),
                save_seed=save_seed,
                turn_id=turn_id,
                ctx_hash=ctx_hash,
            )
            if round_index == 0 and history is not None:
                state = timed_states.get(entry.uid)
                if matched:
                    matched = _timed_fires(entry, state, turn_id=turn_id)
                else:
                    matched = _sticky_active(entry, state, turn_id=turn_id)
            if not matched:
                continue
            matched_this_round.append(entry)
        winners, replaced_uids = _select_group_winners(
            matched_this_round, active=active, active_groups=active_groups
        )
        for replaced_uid in replaced_uids:
            del active[replaced_uid]
        for entry in winners:
            active[entry.uid] = entry
        additions = [
            entry.content for entry in winners if entry.content and not entry.exclude_recursion
        ]
        if not additions:
            break
        round_context = [*context, *additions]
    selected = _apply_budget(
        list(active.values()), max_entries=max_entries, char_budget=char_budget
    )
    return sorted(selected, key=_lore_order_key)
