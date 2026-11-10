"""R3 AC-35：细看屏（inspect）文案句级去重（m2 / story 全对象矩阵）。

背景：``_fit_text`` 补钩子句时，15 字钩子（≡0 mod 3）在 ``len % 3 == 2`` 时会连续追加同一句，
story/m2 的短物品描述会出现「空气里浮着灰尘与旧木头的气味。」连两遍（铜线连三遍）。
修复只加在 inspect 分支（``_inspect_text`` → ``_dedupe_adjacent_sentences`` +
``_pad_text_no_repeat``），``_fit_text`` 本体重构禁止（会改 m2/m3/m4 golden 的 text_len）。

本文件只读状态/纯渲染：零 IO、零网络、不写状态。
"""

from __future__ import annotations

import re
from itertools import pairwise

import pytest

from textgame.agents.dto import ParseOutcome
from textgame.domain.scenario import PLAYER_ID, build_initial_state
from textgame.domain.scenario_story import build_story_state
from textgame.orchestration.narrator import TemplateNarrator
from textgame.persistence.memory import InMemoryRepository
from textgame.services.action_service import ActionService
from textgame.services.query_service import QueryService
from textgame.services.tool_context import ToolContext

_SENTENCE_SPLIT = re.compile(r"(?<=[。！？；…])")
_MIN_TEXT_CHARS = 80
_MAX_TEXT_CHARS = 150
_SAVE_SEED = "save-42"


def _sentences(text: str) -> list[str]:
    """按句末标点切句（与 narrator 内部同口径）。"""
    return [sentence for sentence in _SENTENCE_SPLIT.split(text) if sentence]


def _adjacent_duplicates(text: str) -> list[str]:
    """相邻完全相同的句子列表（空 = 无重复）。"""
    sentences = _sentences(text)
    return [right for left, right in pairwise(sentences) if left == right]


def _inspect_targets(state):
    """枚举状态内每个房间可细看的物品与 NPC → ``(标签, result_data)``（真实服务路径）。"""
    service = ActionService(QueryService(InMemoryRepository()))
    ctx = ToolContext(
        actor_id=PLAYER_ID,
        turn_id=1,
        save_seed=_SAVE_SEED,
        idempotency_key="inspect-dedupe-probe",
        caller="player",
    )
    for room in state.rooms.values():
        player = state.characters[PLAYER_ID].model_copy(update={"location": room.id})
        local = state.model_copy(update={"characters": {**state.characters, PLAYER_ID: player}})
        for item_id in room.item_ids:
            result = service.inspect(ctx, local, item_id=item_id)
            if result.ok:
                yield f"{room.name}/{result.data['name']}", dict(result.data or {})
        for npc_id in room.npc_ids:
            result = service.inspect(ctx, local, npc_id=npc_id)
            if result.ok:
                yield f"{room.name}/{result.data['name']}", dict(result.data or {})


def _render(data: dict) -> str:
    """按只读 inspect 动作渲染一屏叙事（与编排层调用同口径）。"""
    return TemplateNarrator().render(
        action=ParseOutcome(tool="inspect_item", confidence=1.0, source="keyword"),
        events=[],
        state=build_initial_state(save_seed=_SAVE_SEED),
        result_data=data,
    )


@pytest.mark.parametrize(
    ("build_state", "scenario"),
    [(build_story_state, "story"), (build_initial_state, "m2")],
    ids=["story", "m2"],
)
def test_inspect_texts_have_no_adjacent_duplicate_sentences(build_state, scenario: str) -> None:
    """AC-35 回归：两个场景全部可细看对象无相邻重复句、80–150 字、含名称与描述。"""
    state = build_state(save_seed=_SAVE_SEED)
    narrator = TemplateNarrator()
    action = ParseOutcome(tool="inspect_item", confidence=1.0, source="keyword")
    checked = 0
    for label, data in _inspect_targets(state):
        text = narrator.render(action=action, events=[], state=state, result_data=data)
        assert 80 <= len(text) <= 150, (scenario, label, len(text))
        assert _adjacent_duplicates(text) == [], (scenario, label, text)
        assert data["name"] in text, (scenario, label)
        assert data["description"] in text, (scenario, label)
        checked += 1
    assert checked >= (7 if scenario == "story" else 4), (scenario, checked)


def test_known_duplicate_case_is_fixed() -> None:
    """修复前的具体重复样本（m2 火把）：同一钩子句不再连续出现，且仍只出现一次。"""
    state = build_initial_state(save_seed=_SAVE_SEED)
    service = ActionService(QueryService(InMemoryRepository()))
    ctx = ToolContext(
        actor_id=PLAYER_ID,
        turn_id=1,
        save_seed=_SAVE_SEED,
        idempotency_key="inspect-dedupe-torch",
        caller="player",
    )
    torch_id = next(item_id for item_id, item in state.items.items() if item.name == "火把")
    result = service.inspect(ctx, state, item_id=torch_id)
    assert result.ok
    text = _render(dict(result.data or {}))

    hook = "空气里浮着灰尘与旧木头的气味。"
    assert text.count(hook) <= 1  # 修复后至多出现一次（修复前连续两遍）
    assert len(text) == len(text.strip())
    assert "你凑近细看火把。" in text
    assert _adjacent_duplicates(text) == []


def test_inspect_text_is_deterministic_and_keeps_legacy_assertions() -> None:
    """同输入两次逐字一致；保留既有测试依赖的「你凑近细看 / 细节记在心里」口径。"""
    data = {
        "kind": "item",
        "name": "火把",
        "description": "一支受潮的火把，布头上还留着松脂的气味。",
    }
    first = _render(data)
    second = _render(data)

    assert first == second
    assert 80 <= len(first) <= 150
    assert "你凑近细看" in first
    assert "细节记在心里" in first
    assert "松脂" in first


def test_inspect_npc_uses_person_subject() -> None:
    """NPC 细看走「这个人」主语，且同样无相邻重复句。"""
    state = build_story_state(save_seed=_SAVE_SEED)
    service = ActionService(QueryService(InMemoryRepository()))
    ctx = ToolContext(
        actor_id=PLAYER_ID,
        turn_id=1,
        save_seed=_SAVE_SEED,
        idempotency_key="inspect-dedupe-npc",
        caller="player",
    )
    hub = next(room for room in state.rooms.values() if "入口" in room.name)
    player = state.characters[PLAYER_ID].model_copy(update={"location": hub.id})
    local = state.model_copy(update={"characters": {**state.characters, PLAYER_ID: player}})
    npc_id = hub.npc_ids[0]
    result = service.inspect(ctx, local, npc_id=npc_id)
    assert result.ok
    text = _render(dict(result.data or {}))

    assert "你凑近细看" in text and "这个人" in text
    assert 80 <= len(text) <= 150
    assert _adjacent_duplicates(text) == []
