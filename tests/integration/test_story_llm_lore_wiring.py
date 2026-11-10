"""H4 世界书 × LLM 叙事接线（cassette 手工合成、离线）。

cassette：``tests/fixtures/cassettes/story_narrate_with_lore.json``（两条手工交互）：
1. 带 ``result_data["lore_entries"]`` 的 look 请求 → 世界设定事实进入 system prompt、叙事由
   cassette 提供（80–150）、``narrative_source=llm``；
2. 无 lore 的 look 请求 → system prompt 不含世界设定段/条目，叙事仍合法、来源 llm。

全部离线（``CassetteTransport`` 注入 transport + conftest socket guard）；不依赖真实世界书
存储/API（编排层注入契约见 H4-CONTRACT §4/§5）。
"""

from __future__ import annotations

import json

import pytest
from pydantic import SecretStr

from cassette_transport import CASSETTE_DIR, CassetteTransport
from textgame.agents.dto import ParseOutcome
from textgame.agents.narrator_agent import PROMPT_VERSION, LLMNarratorAgent
from textgame.domain.models import ActionIntent, GameState
from textgame.domain.scenario import PLAYER_ID
from textgame.domain.scenario_story import build_story_state
from textgame.llm.config import LLMClientConfig
from textgame.llm.litellm_client import LiteLLMClient
from textgame.orchestration.narrator import TemplateNarrator

pytestmark = pytest.mark.integration

_SAVE_SEED = "save-42"
_CASSETTE = "story_narrate_with_lore.json"
_MIN_TEXT = 80
_MAX_TEXT = 150
_LORE_HEADER = "世界设定事实（必须遵守、不得与玩家状态/事件冲突"
_LORE_1 = "【世界设定】哨站的灯是河谷商队的信标，灭灯意味着封路。"
_LORE_2 = "【世界设定】守夜人老兵守灯已有十二年。"


class _RecordingCassette(CassetteTransport):
    """回放 cassette 的同时记录每次请求的 messages（只读，不改变回放语义）。"""

    def __init__(self, name: str) -> None:
        """加载 cassette 并初始化 messages 记录。"""
        super().__init__(name)
        self.messages: list[list[dict[str, str]]] = []

    def __call__(
        self,
        *,
        provider: str,
        model: str,
        api_key: str,
        api_base: str,
        messages: list[dict[str, str]],
        timeout_s: float,
    ) -> object:
        """记录本次请求 messages 后原样回放（不记录 api_key/messages 原文进日志）。"""
        self.messages.append([dict(message) for message in messages])
        return super().__call__(
            provider=provider,
            model=model,
            api_key=api_key,
            api_base=api_base,
            messages=messages,
            timeout_s=timeout_s,
        )


def _client() -> tuple[LiteLLMClient, _RecordingCassette]:
    """按 cassette 构造治理链客户端（假端点/桩密钥，零网络）。"""
    transport = _RecordingCassette(_CASSETTE)
    config = LLMClientConfig(
        provider="deepseek",
        model="cassette-model",
        api_base="https://cassette.invalid",
        api_key=SecretStr("cassette-no-secret"),
    )
    return LiteLLMClient(config, transport=transport, sleep=lambda _s: None), transport


def _cassette_narratives() -> list[str]:
    """读取 cassette 期望叙事（逐条 80–150 字）。"""
    payload = json.loads((CASSETTE_DIR / _CASSETTE).read_text(encoding="utf-8"))
    narratives = [
        json.loads(str(interaction["response"]["body"]["content"]))["narrative"]
        for interaction in payload["interactions"]
    ]
    for narrative in narratives:
        assert _MIN_TEXT <= len(narrative) <= _MAX_TEXT, f"cassette 叙事长度越界: {narrative!r}"
    return narratives


def _outcome(tool: str = "get_room_view") -> ParseOutcome:
    """构造与编排层同构的解析结果（只读 look）。"""
    return ParseOutcome(
        intent=ActionIntent(action=tool, target=None, params={}, confidence=1.0),
        tool=tool,
        args={},
        confidence=1.0,
        source="keyword",
    )


def _story_look_state() -> tuple[GameState, str]:
    """剧情初始状态（断桥）与房间描述文本（look 的 ``result_data["text"]`` 口径）。"""
    state = build_story_state(save_seed=_SAVE_SEED)
    player = state.characters[PLAYER_ID]
    room = state.rooms[player.location]
    return state, room.description


def _system_of(recorded: list[dict[str, str]]) -> str:
    """取某次请求的 system 消息内容。"""
    return "\n".join(message["content"] for message in recorded)


# ---------------------------------------------------------------------------
# 有 lore → 世界设定段进 prompt，叙事来源 llm
# ---------------------------------------------------------------------------
def test_lore_entries_enter_prompt_and_narrative_from_cassette() -> None:
    """激活条目出现在 prompt 的世界设定段；叙事来自 cassette、80–150、来源 llm。"""
    client, transport = _client()
    narrator = LLMNarratorAgent(client, fallback=TemplateNarrator())
    state, room_text = _story_look_state()
    expected = _cassette_narratives()

    text, source = narrator.render_with_meta(
        action=_outcome(),
        events=[],
        state=state,
        result_data={"text": room_text, "lore_entries": [_LORE_1, _LORE_2]},
    )

    assert source == "llm"
    assert text == expected[0]
    assert _MIN_TEXT <= len(text) <= _MAX_TEXT
    assert "世界设定" not in text  # 叙事正文不向玩家复述“世界设定”字样
    assert transport.calls == 1
    requested_prompt = _system_of(transport.messages[0])
    assert _LORE_HEADER in requested_prompt
    assert _LORE_1 in requested_prompt
    assert _LORE_2 in requested_prompt
    assert PROMPT_VERSION == "narrate@v3"
    assert transport.remaining == 1


# ---------------------------------------------------------------------------
# 无 lore → 不出现世界设定段，叙事仍 llm
# ---------------------------------------------------------------------------
def test_no_lore_prompt_has_no_world_setting_segment() -> None:
    """无 lore 的同一动作：system prompt 不含世界设定段/条目，叙事 80–150、来源 llm。"""
    client, transport = _client()
    narrator = LLMNarratorAgent(client, fallback=TemplateNarrator())
    state, room_text = _story_look_state()
    expected = _cassette_narratives()

    # 先消费第 1 条（带 lore），再对同一叙事器做无 lore 请求消费第 2 条。
    narrator.render_with_meta(
        action=_outcome(),
        events=[],
        state=state,
        result_data={"text": room_text, "lore_entries": [_LORE_1, _LORE_2]},
    )
    text, source = narrator.render_with_meta(
        action=_outcome(), events=[], state=state, result_data={"text": room_text}
    )

    assert source == "llm"
    assert text == expected[1]
    assert _MIN_TEXT <= len(text) <= _MAX_TEXT
    assert transport.calls == 2
    no_lore_prompt = _system_of(transport.messages[1])
    assert _LORE_HEADER not in no_lore_prompt
    assert _LORE_1 not in no_lore_prompt
    assert _LORE_2 not in no_lore_prompt
    # 无 lore 请求的 user payload 不出现 lore 字段（与 narrate@v2 行为一致）。
    assert "lore_entries" not in no_lore_prompt


# ---------------------------------------------------------------------------
# 空/脏 lore 与无 lore 等价；内部 ID 不进 prompt
# ---------------------------------------------------------------------------
def test_empty_or_dirty_lore_is_equivalent_to_no_lore() -> None:
    """``lore_entries`` 为空列表/全脏数据时不追加设定段（与无 lore 请求同构）。"""
    client, transport = _client()
    narrator = LLMNarratorAgent(client, fallback=TemplateNarrator())
    state, room_text = _story_look_state()

    dirty = narrator._build_request(
        action=_outcome(),
        events=[],
        state=state,
        result_data={"text": room_text, "lore_entries": [None, 42, "  ", {"x": 1}]},
    )
    clean = narrator._build_request(
        action=_outcome(), events=[], state=state, result_data={"text": room_text}
    )

    assert [message.content for message in dirty.messages] == [
        message.content for message in clean.messages
    ]
    assert _LORE_HEADER not in "\n".join(message.content for message in dirty.messages)
    assert transport.calls == 0
