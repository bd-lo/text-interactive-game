"""JSON 叙事的增量提取（N2-038 / ADR D-N2-3）：从流式 JSON 片段提取 ``narrative`` 前缀。

叙事契约仍是 ``{"narrative": "80–150 字"}``（``narrate@v3``）；流式 chunk 是 JSON 文本片段
（如 ``{"narrative":"你沿``），**不能直接展示**。本模块用白名单增量提取器扫描已到达文本的
``"narrative"`` 字符串值前缀：

- 只认 ``"narrative"`` 键后、以引号开始的字符串值；其它字段/前缀垃圾/字段顺序无关；
- 逐字符处理 JSON 转义（``\\"``/``\\\\``/``\\n``/``\\t``/``\\uXXXX`` 与 UTF-16 代理对）；
  **未完成的转义**（chunk 末尾的 ``\\`` 或 ``\\u12``）不产出，等后续 chunk 补齐；
- 上限 ``MAX_EXTRACT_CHARS = 150 + 64``（ADR D-N2-3 的安全余量）：超出部分丢弃（最终仍以
  完整 JSON 校验结果为准，``done.result.text`` 是权威文本）；
- 非字符串值（数字/布尔/null/对象/数组）、找不到键、格式非法 → ``None``（不抛异常）。

两个入口：

- :func:`extract_narrative_prefix`：**纯函数**，输入累计缓冲，返回已解转义前缀（不含末尾
  未闭合的转义/半截代理对）；
- :class:`NarrativeDeltaExtractor`：有状态薄封装（供叙事 Agent 使用），``feed(chunk)`` 只返回
  「比上次多出来的后缀」；因为「累计缓冲的解码前缀是单调前缀」，所以增量拼接 == 最终
  ``narrative`` 文本（ADR D-N2-3 验收断言 ``exact_match``）。

本模块不 import 供应商 SDK、不读时钟/随机；可被 ``agents`` 层（叙事 Agent）与单测直接使用。
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "MAX_EXTRACT_CHARS",
    "MAX_NARRATIVE_CHARS",
    "NarrativeDeltaExtractor",
    "extract_narrative_prefix",
]

#: ``narrate@v3`` Schema 的正文上限（``NarrativeOutput.narrative`` max_length）。
MAX_NARRATIVE_CHARS: Final = 150
#: 增量提取上限 = 正文上限 + 64 字安全余量（超出丢弃，等权威 ``done`` 文本；D-N2-3）。
MAX_EXTRACT_CHARS: Final = MAX_NARRATIVE_CHARS + 64

_NARRATIVE_KEY: Final = '"narrative"'
_WHITESPACE: Final = " \t\r\n"
_ESCAPE_MAP: Final[dict[str, str]] = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}
_HEX_DIGITS: Final = frozenset("0123456789abcdefABCDEF")


def _skip_whitespace(buffer: str, position: int) -> int:
    """跳过 JSON 空白（空格/制表/回车/换行），返回首个非空白下标。"""
    while position < len(buffer) and buffer[position] in _WHITESPACE:
        position += 1
    return position


def _narrative_value_start(buffer: str) -> int | None:
    """定位 ``"narrative"`` 键之后字符串值的**首字符下标**（引号后一位）。

    - 尚未出现键/冒号/引号（chunk 不完整）：返回 ``None``（等待更多 chunk）；
    - 键存在但值是数字/布尔/null/对象/数组：返回 ``None``（非字符串，无法增量提取）；
    - 命中：返回字符串内容起点。前缀垃圾（模型输出多余文本）被容忍。
    """
    search_from = 0
    while True:
        index = buffer.find(_NARRATIVE_KEY, search_from)
        if index < 0:
            return None
        position = _skip_whitespace(buffer, index + len(_NARRATIVE_KEY))
        if position >= len(buffer):
            return None  # 键已出现、冒号未到：等待下一 chunk
        if buffer[position] != ":":
            search_from = index + 1
            continue
        position = _skip_whitespace(buffer, position + 1)
        if position >= len(buffer):
            return None  # 冒号已出现、值未到：等待
        if buffer[position] != '"':
            return None  # 非字符串值（数字/null/对象/数组等）
        return position + 1


def _decode_unicode_escape(buffer: str, position: int, length: int) -> tuple[str, int] | None:
    """解 ``\\uXXXX``（含 UTF-16 代理对）；未完成/非法/孤立代理返回 ``None``。

    ``position`` 是反斜杠下标；成功返回 ``(解码字符, 下一个待处理下标)``。返回 ``None``
    表示「当前缓冲不足以解码或格式非法」——调用方停止在已解前缀处（若后续 chunk 补齐，
    纯函数会重新从累计缓冲解码，结果仍是单调前缀）。
    """
    hex_start = position + 2  # 跳 ``\u``
    if hex_start + 4 > length:
        return None
    digits = buffer[hex_start : hex_start + 4]
    if any(digit not in _HEX_DIGITS for digit in digits):
        return None
    code_point = int(digits, 16)
    if 0xD800 <= code_point <= 0xDBFF:  # 高代理：必须紧跟一个低代理 ``\uDCxx``
        low_start = hex_start + 4
        if low_start + 6 > length:
            return None  # 低代理转义尚未到齐：等待下一 chunk
        if buffer[low_start] != "\\" or buffer[low_start + 1] != "u":
            return None  # 孤立高代理（非法 JSON）
        low_digits = buffer[low_start + 2 : low_start + 6]
        if any(digit not in _HEX_DIGITS for digit in low_digits):
            return None
        low = int(low_digits, 16)
        if not 0xDC00 <= low <= 0xDFFF:
            return None  # 高代理后不是低代理（非法 JSON）
        combined = 0x10000 + ((code_point - 0xD800) << 10) + (low - 0xDC00)
        return chr(combined), low_start + 6
    if 0xDC00 <= code_point <= 0xDFFF:
        return None  # 孤立低代理（非法 JSON；chr 会产生不可 UTF-8 编码的字符）
    return chr(code_point), hex_start + 4


def _decode_prefix(buffer: str, start: int, *, max_chars: int) -> str:
    """从字符串内容起点开始逐字符解码，直到关闭引号/缓冲结束/上限/非法转义。"""
    if max_chars <= 0:
        return ""
    chars: list[str] = []
    position = start
    length = len(buffer)
    while position < length:
        char = buffer[position]
        if char == '"':  # 字符串值结束
            return "".join(chars)
        if char != "\\":
            if len(chars) >= max_chars:
                return "".join(chars)
            chars.append(char)
            position += 1
            continue
        if position + 1 >= length:
            return "".join(chars)  # 转义序列跨 chunk：等待后续
        escape = buffer[position + 1]
        if escape == "u":
            decoded = _decode_unicode_escape(buffer, position, length)
            if decoded is None:
                return "".join(chars)
            decoded_char, next_position = decoded
            if len(chars) >= max_chars:
                return "".join(chars)
            chars.append(decoded_char)
            position = next_position
            continue
        mapped = _ESCAPE_MAP.get(escape)
        if mapped is None:
            return "".join(chars)  # 非法转义：停止（不返回半截坏数据）
        if len(chars) >= max_chars:
            return "".join(chars)
        chars.append(mapped)
        position += 2
    return "".join(chars)  # 缓冲已耗尽但字符串未闭合：返回已解前缀


def extract_narrative_prefix(
    buffer: str,
    *,
    max_chars: int = MAX_EXTRACT_CHARS,
) -> str | None:
    """纯函数：从**累计** JSON 文本缓冲中提取 ``narrative`` 字符串的前缀。

    :param buffer: 已到达的完整 JSON 文本（调用方负责把 chunk 累加进来）。
    :param max_chars: 提取上限（默认 ``MAX_EXTRACT_CHARS``；超出部分丢弃）。
    :return: 已解转义文本前缀；未找到 ``"narrative"`` 字符串值（或值非字符串）返回 ``None``。
        字符串值已开始但尚未产出字符时返回 ``""``。
    """
    start = _narrative_value_start(buffer)
    if start is None:
        return None
    return _decode_prefix(buffer, start, max_chars=max_chars)


class NarrativeDeltaExtractor:
    """有状态的增量提取器：``feed`` 返回「比上次多出来的后缀」，供 ``delta`` 事件下发。

    实现保证（与 ``extract_narrative_prefix`` 纯函数一致）：

    - 增量拼接严格等于最终 ``narrative`` 的前缀（缓冲只追加 → 解码前缀单调）；
    - 未完成的转义/半截代理对不会提前产出，补齐后才解码；
    - 达到 ``max_chars`` 后不再产出（``truncated=True``），最终以完整 JSON 校验结果为准；
    - ``feed``/``flush`` 不抛异常、不读时钟；可安全用于异常兜底路径。
    """

    def __init__(self, *, max_chars: int = MAX_EXTRACT_CHARS) -> None:
        """``max_chars`` 为提取上限（默认 ``MAX_EXTRACT_CHARS``，与 ADR D-N2-3 一致）。"""
        self._max_chars = max_chars
        self._buffer = ""
        self._prefix = ""
        self._found = False
        self._truncated = False

    @property
    def found(self) -> bool:
        """是否已定位到 ``"narrative"`` 字符串值（即使前缀暂时为空）。"""
        return self._found

    @property
    def truncated(self) -> bool:
        """提取是否已触及上限（后续增量被丢弃）。"""
        return self._truncated

    @property
    def text(self) -> str:
        """当前已提取的完整前缀（已下发增量的拼接结果）。"""
        return self._prefix

    def feed(self, chunk: str) -> str:
        """追加一个 chunk，返回**新增**且可安全展示的 narrative 文本（可能为 ``""``）。"""
        if not chunk:
            return ""
        self._buffer += chunk
        prefix = extract_narrative_prefix(self._buffer, max_chars=self._max_chars)
        return self._absorb(prefix)

    def flush(self) -> str:
        """结束流：对当前缓冲再提取一次（完整转义已在 ``feed`` 消费，正常返回 ``""``）。"""
        prefix = extract_narrative_prefix(self._buffer, max_chars=self._max_chars)
        return self._absorb(prefix)

    def reset(self) -> None:
        """清空状态（同一实例复用于下一次渲染时调用）。"""
        self._buffer = ""
        self._prefix = ""
        self._found = False
        self._truncated = False

    def _absorb(self, prefix: str | None) -> str:
        """把纯函数结果合并进已下发前缀，返回本次新增后缀。"""
        if prefix is None:
            return ""
        self._found = True
        if len(prefix) >= self._max_chars:
            self._truncated = True
        if len(prefix) <= len(self._prefix):
            return ""
        # 理论不可达（缓冲只追加 → 解码前缀单调）；防御式处理：异常时不回退已显示内容。
        if not prefix.startswith(self._prefix):
            self._prefix = prefix
            return ""
        delta = prefix[len(self._prefix) :]
        self._prefix = prefix
        return delta
