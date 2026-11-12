"""单元测试：N2-038 JSON 叙事增量提取器（``llm/streaming.py``，ADR D-N2-3）。

覆盖转义（标准转义/``\\uXXXX``/代理对/反斜杠）、跨 chunk 分割（含转义与代理对在 chunk
边界被切开）、超长上限、非字符串值、键乱序/前缀垃圾/字符串内假键、结束 flush，以及
**核心不变量：增量拼接与最终完整 JSON 的 ``narrative`` 完全一致（exact match）**。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from textgame.llm.streaming import (
    MAX_EXTRACT_CHARS,
    MAX_NARRATIVE_CHARS,
    NarrativeDeltaExtractor,
    extract_narrative_prefix,
)

#: 贴近真实叙事的中文文本（80–150 字），含常见标点与长句。
_NARRATIVE_TEXT = (
    "你沿着北边的通道动身，脚下的石板缝里积着陈年的灰，石阶上浮着一层薄薄的潮气。"
    "走了不多时，地窖的气息扑面而来，光线也跟着暗了一截。"
    "你放慢脚步站定，打量着这里的环境，墙角堆着几只落了灰的木箱。"
)

#: 覆盖 JSON 转义边界：引号/反斜杠/换行/制表/斜杠/中文/emoji（代理对）。
_TRICKY_TEXT = "他说「快走」\\ 然后\n抬头\t看见 / 一片星空，还有😀和𠀀。"


def _json_narrative(text: str, *, ensure_ascii: bool = False) -> str:
    """构造 ``{"narrative": text}`` 的 JSON 文本（可强制 ``\\uXXXX`` 转义）。"""
    return json.dumps({"narrative": text}, ensure_ascii=ensure_ascii)


def _json_with_extra_fields(text: str) -> str:
    """带前缀字段/空白/后缀字段的 JSON（模拟 provider 字段顺序差异）。"""
    return (
        '{"mood": "quiet",\n  "confidence": 0.9, "narrative" :  '
        + json.dumps(text, ensure_ascii=False)
        + ', "extra": {"ignored": true}}'
    )


# ---------------------------------------------------------------------------
# 纯函数：定位与基本语义
# ---------------------------------------------------------------------------
def test_extract_returns_none_without_narrative_key() -> None:
    """没有 ``narrative`` 键（含 chunk 只到键的一部分）→ ``None``。"""
    assert extract_narrative_prefix("") is None
    assert extract_narrative_prefix('{"mood": "quiet"}') is None
    assert extract_narrative_prefix('{"narr') is None


def test_extract_returns_empty_when_value_not_started() -> None:
    """键/冒号已到但字符串值尚未开始 → ``None``；引号刚出现 → 空前缀 ``""``。"""
    assert extract_narrative_prefix('{"narrative"') is None
    assert extract_narrative_prefix('{"narrative":') is None
    assert extract_narrative_prefix('{"narrative": "') == ""


def test_extract_partial_prefix() -> None:
    """半截 JSON 返回已到达的正文前缀。"""
    assert extract_narrative_prefix('{"narrative": "你沿') == "你沿"
    assert extract_narrative_prefix('{"narrative":"你沿着北边') == "你沿着北边"


def test_extract_stops_at_closing_quote() -> None:
    """字符串关闭后不再吞后续字段。"""
    buffer = '{"narrative": "正文", "other": "不应出现"}'

    assert extract_narrative_prefix(buffer) == "正文"


def test_extract_key_after_other_fields_and_whitespace() -> None:
    """字段顺序/空白不敏感；前缀垃圾被容忍。"""
    buffer = "好的，这是结果：\n" + _json_with_extra_fields("你推开门。")

    assert extract_narrative_prefix(buffer) == "你推开门。"


def test_extract_ignores_fake_key_inside_escaped_string() -> None:
    """其它字符串值里转义的 ``\\"narrative\\"`` 不是键，不能误匹配。"""
    buffer = '{"note": "他说\\"narrative\\":\\"假的\\"", "narrative": "真正的正文"}'

    assert extract_narrative_prefix(buffer) == "真正的正文"


def test_extract_non_string_values_return_none() -> None:
    """``narrative`` 值为数字/null/数组/对象/布尔 → ``None``（非字符串无法增量提取）。"""
    for value in ("123", "null", "true", "[]", '{"nested": 1}'):
        assert extract_narrative_prefix(f'{{"narrative": {value}}}') is None


def test_extract_decodes_standard_escapes() -> None:
    """标准 JSON 转义（引号/反斜杠/换行/制表/斜杠）全部解转义。"""
    buffer = _json_narrative(_TRICKY_TEXT)

    extracted = extract_narrative_prefix(buffer)

    assert extracted == _TRICKY_TEXT
    assert json.loads(buffer)["narrative"] == extracted


def test_extract_decodes_unicode_escape() -> None:
    """``\\uXXXX`` 转义（含中文与 BMP 字符）解为真实字符。"""
    buffer = _json_narrative("你好，旅人。", ensure_ascii=True)

    assert "\\u" in buffer
    assert extract_narrative_prefix(buffer) == "你好，旅人。"


def test_extract_decodes_surrogate_pair() -> None:
    """非 BMP 字符的 UTF-16 代理对（``\\ud83d\\ude00``）合并为一个字符。"""
    buffer = _json_narrative("😀😀", ensure_ascii=True)

    assert "\\ud83d" in buffer.lower()
    assert extract_narrative_prefix(buffer) == "😀😀"


def test_extract_backslash_before_closing_quote() -> None:
    """正文以反斜杠结尾（JSON ``\\\\`` 后紧跟关闭引号）时不能把关闭引号吞进正文。"""
    text = "路径是 C:\\temp\\"

    assert extract_narrative_prefix(_json_narrative(text)) == text


# ---------------------------------------------------------------------------
# 纯函数：跨 chunk 不完整（等待后续）、超长上限
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("tail", ["\\", "\\u", "\\u12", "\\ud83d"])
def test_extract_incomplete_escape_returns_decoded_so_far(tail: str) -> None:
    """chunk 末尾的未完成转义不产出，返回此前已解前缀（后续补齐后由纯函数重算）。"""
    buffer = '{"narrative": "开头' + tail

    assert extract_narrative_prefix(buffer) == "开头"


def test_extract_overlong_is_capped() -> None:
    """超长正文按 ``MAX_EXTRACT_CHARS`` 截断（ADR D-N2-3 安全余量；最终以校验结果为准）。"""
    long_text = "长" * (MAX_NARRATIVE_CHARS * 3)

    extracted = extract_narrative_prefix(_json_narrative(long_text))

    assert extracted is not None
    assert len(extracted) == MAX_EXTRACT_CHARS
    assert long_text.startswith(extracted)


# ---------------------------------------------------------------------------
# 有状态增量提取器：跨 chunk / exact match
# ---------------------------------------------------------------------------
def test_delta_extractor_across_chunks_exact_match() -> None:
    """逐字符喂入完整 JSON：增量拼接 == 最终 narrative，且每段都是其前缀。"""
    text = _NARRATIVE_TEXT
    buffer = _json_narrative(text)
    extractor = NarrativeDeltaExtractor()
    emitted: list[str] = []
    for char in buffer:
        emitted.append(extractor.feed(char))

    joined = "".join(emitted)
    assert joined == text
    assert extractor.text == text
    assert extractor.found is True
    assert extractor.truncated is False
    assert extractor.flush() == ""


@pytest.mark.parametrize("chunk_size", [1, 2, 3, 5, 7, 11, 16, 32, 64, 4096])
def test_delta_extractor_any_chunk_size_exact_match(chunk_size: int) -> None:
    """任意 chunk 大小（含把转义/UTF-16 代理对切在边界）拼接结果都与完整文本一致。"""
    text = _TRICKY_TEXT * 2
    buffer = _json_narrative(text, ensure_ascii=True)
    extractor = NarrativeDeltaExtractor()
    emitted: list[str] = []
    for start in range(0, len(buffer), chunk_size):
        emitted.append(extractor.feed(buffer[start : start + chunk_size]))

    assert "".join(emitted) == text
    assert extractor.text == text


def test_delta_extractor_never_emits_partial_surrogate() -> None:
    """代理对的高位转义先到时不下发，低代理补齐后一次下发完整 emoji（不产生坏字符）。"""
    buffer = _json_narrative("😀", ensure_ascii=True)
    high = buffer.index("\\ud83d")
    extractor = NarrativeDeltaExtractor()

    first = extractor.feed(buffer[: high + 7])  # 只到高代理转义的中途
    second = extractor.feed(buffer[high + 7 :])

    assert first == ""
    assert second == "😀"
    assert extractor.text == "😀"


def test_delta_extractor_truncated_at_cap() -> None:
    """超过上限后不再产出增量，``truncated=True``。"""
    long_text = "长" * (MAX_NARRATIVE_CHARS * 3)
    buffer = _json_narrative(long_text)
    extractor = NarrativeDeltaExtractor()

    first = extractor.feed(buffer)
    extra = extractor.feed("更多")  # 不会改变上限后的结果

    assert len(first) == MAX_EXTRACT_CHARS
    assert extra == ""
    assert extractor.truncated is True
    assert len(extractor.text) == MAX_EXTRACT_CHARS


def test_delta_extractor_non_string_value_yields_nothing() -> None:
    """非字符串 narrative 值：不产出增量、``found=False``。"""
    extractor = NarrativeDeltaExtractor()

    assert extractor.feed('{"narrative": 42}') == ""
    assert extractor.found is False
    assert extractor.text == ""


def test_delta_extractor_reset() -> None:
    """``reset`` 清空缓冲与已下发状态，可复用同一实例。"""
    extractor = NarrativeDeltaExtractor()
    extractor.feed('{"narrative": "旧文')
    extractor.reset()

    assert extractor.feed(_json_narrative("新的一屏。")) == "新的一屏。"
    assert extractor.text == "新的一屏。"


def test_extract_doc_example_matches_adr_design() -> None:
    """设计文档示例形态：真实 provider chunk 序列（87 chunks 同构）exact match。"""
    text = _NARRATIVE_TEXT
    buffer = _json_narrative(text)
    chunks = [buffer[index : index + 3] for index in range(0, len(buffer), 3)]
    extractor = NarrativeDeltaExtractor()
    emitted: list[str] = []

    for chunk in chunks:
        emitted.append(extractor.feed(chunk))

    assert "".join(emitted) == text
    assert extractor.text == json.loads(buffer)["narrative"]


def test_extract_is_pure_function_for_same_buffer() -> None:
    """纯函数语义：同一输入多次调用结果一致、无内部状态。"""
    buffer = '{"narrative": "你沿着北边的通道动身'

    first = extract_narrative_prefix(buffer)
    second = extract_narrative_prefix(buffer)

    assert first == second == "你沿着北边的通道动身"


def test_extract_prefixed_garbage_is_tolerated() -> None:
    """provider 先输出无关文本/代码围栏时仍能定位 ``narrative`` 键。"""
    buffer = '```json\n{"narrative": "正文从这里开始。"}\n```'

    extracted: Any = extract_narrative_prefix(buffer)

    assert extracted == "正文从这里开始。"
