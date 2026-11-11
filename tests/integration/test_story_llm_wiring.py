"""R2 story 场景 LLM 叙事接线（cassette 手工合成、离线）+ 回退可感知（集成层）。

覆盖：
1. story 前 3 个典型回合（首屏看断桥 / 和守夜人老兵说话 briefed / 拿起火把）在 ``narrate@v2``
   事实供给下由手工 cassette 提供叙事：``narrative_source=llm``、长度 80–150、文本来自 cassette；
2. 默认离线组合根（``TEXTGAME_LLM_ENABLED=false``）不构造 ``LLMNarratorAgent``，回合叙事来源恒
   ``template``（默认玩家零联网、零 Key 可玩）；
3. LLM 不可达（``LLM_UNAVAILABLE``）时回合仍成功提交，事件 ``narrative_source=template``
   （前端据此显示“本地叙事”，回退对玩家可感知但无报错）。

说明：story 场景的 ``create_game`` 接线归 backend T15；本文件只覆盖 narrator 接线层（story 状态 +
事件事实直连生产叙事 Agent），待 T15 合入后可在此扩展 HTTP 级 story 旅程。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from cassette_transport import CASSETTE_DIR, CassetteTransport
from fakes import FakeTransport
from textgame.agents.dto import ParseOutcome
from textgame.agents.narrator_agent import LLMNarratorAgent
from textgame.config.settings import get_settings
from textgame.domain.events import Event, EventType
from textgame.domain.models import ActionIntent, GameState
from textgame.domain.scenario import PLAYER_ID
from textgame.domain.scenario_story import STORY_WATCHMAN_ID, build_story_state, story_reply
from textgame.llm.config import LLMClientConfig
from textgame.llm.errors import ProviderCallError
from textgame.llm.litellm_client import LiteLLMClient
from textgame.orchestration.direct_gate import DirectGate
from textgame.orchestration.dto import TurnRequest
from textgame.orchestration.intent import DeterministicIntentParser
from textgame.orchestration.narrator import TemplateNarrator
from textgame.orchestration.turn_orchestrator import CreateGameRequest, TurnOrchestrator
from textgame.persistence.memory import InMemoryRepository
from textgame.services.action_service import ActionService
from textgame.services.game_engine import GameEngine
from textgame.services.query_service import QueryService

pytestmark = pytest.mark.integration

_SAVE_SEED = "save-42"
_CASSETTE = "story_narrate_three.json"
_MIN_TEXT = 80
_MAX_TEXT = 150
_HUB_NAME = "废弃哨站·入口"
_TORCH_NAME = "火把"


# ---------------------------------------------------------------------------
# 装配辅助
# ---------------------------------------------------------------------------
def _client_config() -> LLMClientConfig:
    """cassette 用客户端配置（假端点/桩密钥，不会发起真实请求）。"""
    return LLMClientConfig(
        provider="deepseek",
        model="cassette-model",
        api_base="https://cassette.invalid",
        api_key=SecretStr("cassette-no-secret"),
    )


def _cassette_client() -> tuple[LiteLLMClient, CassetteTransport]:
    """用 story 三回合手工 cassette 构造治理链客户端（transport 注入 → 零网络）。"""
    transport = CassetteTransport(_CASSETTE)
    client = LiteLLMClient(_client_config(), transport=transport, sleep=lambda _s: None)
    return client, transport


def _unavailable_client() -> tuple[LiteLLMClient, FakeTransport]:
    """构造「供应商不可达」的客户端：第一次调用即不可重试失败。"""
    transport = FakeTransport([ProviderCallError("llm unreachable", retryable=False)])
    client = LiteLLMClient(_client_config(), transport=transport, sleep=lambda _s: None)
    return client, transport


def _cassette_narratives() -> list[str]:
    """读取三回合 cassette 的期望叙事文本（逐条 80–150 字）。"""
    payload = json.loads((CASSETTE_DIR / _CASSETTE).read_text(encoding="utf-8"))
    narratives = [
        json.loads(str(interaction["response"]["body"]["content"]))["narrative"]
        for interaction in payload["interactions"]
    ]
    for narrative in narratives:
        assert _MIN_TEXT <= len(narrative) <= _MAX_TEXT, f"cassette 叙事长度越界: {narrative!r}"
    return narratives


def _room_id(state: GameState, name: str) -> str:
    """按房间展示名取 id（测试夹具用）。"""
    return next(room.id for room in state.rooms.values() if room.name == name)


def _item_id(state: GameState, name: str) -> str:
    """按物品展示名取实例 id（测试夹具用）。"""
    return next(item.id for item in state.items.values() if item.name == name)


def _state_at_hub() -> GameState:
    """剧情初始状态，玩家移到入口（可拿到火把、可和老兵说话）。"""
    state = build_story_state(save_seed=_SAVE_SEED)
    player = state.characters[PLAYER_ID].model_copy(update={"location": _room_id(state, _HUB_NAME)})
    return state.model_copy(update={"characters": {**state.characters, PLAYER_ID: player}})


def _outcome(tool: str) -> ParseOutcome:
    """构造与编排层同构的解析结果。"""
    return ParseOutcome(
        intent=ActionIntent(action=tool, target=None, params={}, confidence=1.0),
        tool=tool,
        args={},
        confidence=1.0,
        source="keyword",
    )


def _talk_event() -> Event:
    """守夜人老兵 briefed 对话事件（与后端 ``talk_to`` 写入口径一致）。"""
    branch = story_reply(STORY_WATCHMAN_ID, "信号灯怎么弄")
    assert branch is not None
    return Event(
        turn_id=2,
        actor_id=PLAYER_ID,
        method="talk_to",
        event_type=EventType.DIALOGUE_EXCHANGED,
        params={"npc_id": STORY_WATCHMAN_ID, "topic": "信号灯怎么弄"},
        result={"dialogue_state": branch["dialogue_state"], "reply": branch["reply"]},
    )


def _take_torch_event(state: GameState) -> Event:
    """拿起火把事件（入口屋内的火把被玩家拾取）。"""
    return Event(
        turn_id=3,
        actor_id=PLAYER_ID,
        method="take_item",
        event_type=EventType.ITEM_TAKEN,
        params={
            "item_id": _item_id(state, _TORCH_NAME),
            "from_room": _room_id(state, _HUB_NAME),
        },
        result={"inventory_size": 1},
    )


# ---------------------------------------------------------------------------
# 1) story 三回合：cassette 叙事接线
# ---------------------------------------------------------------------------
def test_story_three_turns_use_llm_cassette_narratives() -> None:
    """首屏看/老兵 briefed/拿火把三回合：source=llm、文本来自 cassette、长度达标。"""
    client, transport = _cassette_client()
    narrator = LLMNarratorAgent(client, fallback=TemplateNarrator())
    expected = _cassette_narratives()
    bridge_state = build_story_state(save_seed=_SAVE_SEED)
    hub_state = _state_at_hub()

    # 回合 1：首屏环顾（断桥）——只读动作，环境即玩家所见。
    look_text, look_source = narrator.render_with_meta(
        action=_outcome("get_room_view"),
        events=[],
        state=bridge_state,
        result_data={"text": bridge_state.rooms[_room_id(bridge_state, "哨站外·断桥")].description},
    )
    assert look_source == "llm"
    assert look_text == expected[0]
    assert "断桥" in look_text

    # 回合 2：和守夜人老兵说话（briefed）——NPC 展示名 + 回话进入事实。
    talk_text, talk_source = narrator.render_with_meta(
        action=_outcome("talk_to"),
        events=[_talk_event()],
        state=hub_state,
        result_data={},
    )
    assert talk_source == "llm"
    assert talk_text == expected[1]
    assert "信号火把" in talk_text

    # 回合 3：拿起火把——物品展示名进入事实（不得写“一件东西”）。
    take_text, take_source = narrator.render_with_meta(
        action=_outcome("take_item"),
        events=[_take_torch_event(hub_state)],
        state=hub_state,
        result_data={},
    )
    assert take_source == "llm"
    assert take_text == expected[2]
    assert "火把" in take_text

    for text in (look_text, talk_text, take_text):
        assert _MIN_TEXT <= len(text) <= _MAX_TEXT
    assert transport.calls == 3
    assert transport.remaining == 0


# ---------------------------------------------------------------------------
# 2) 默认离线组合根：不构造 LLM 叙事器（零联网、来源=template）
# ---------------------------------------------------------------------------
def _offline_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    """以临时 SQLite + 离线开关构建生产组合根（不注入任何 LLM 客户端）。"""
    import textgame.main as main_module  # 延迟导入：避免默认数据目录副作用

    monkeypatch.setenv("TEXTGAME_DB_URL", f"sqlite:///{(tmp_path / 'story-offline.db').as_posix()}")
    monkeypatch.setenv("TEXTGAME_LLM_ENABLED", "false")
    get_settings.cache_clear()
    return main_module._build_app()


def test_default_offline_assembly_does_not_construct_llm_narrator(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """默认离线装配：叙事器常驻 LLM 版本但**离线时回退模板**，回合来源恒 template。

    2026-11-11 运行期凭据改造后：组合根总是构造 ``LLMNarratorAgent``（内含运行期客户端），
    离线时其 ``_fallback`` 是 ``TemplateNarrator``，输出与旧离线装配一致（零联网）。
    """
    app = _offline_app(monkeypatch, tmp_path)
    orchestrator = app.state.orchestrator
    assert isinstance(orchestrator._narrator, LLMNarratorAgent)
    assert isinstance(orchestrator._narrator._fallback, TemplateNarrator)
    assert app.state.llm_runtime.status().enabled is False

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/games",
            headers={"Idempotency-Key": "story-llm-offline-game"},
            json={"seed": 42},
        ).json()
        body = client.post(
            "/api/v1/turns",
            headers={"Idempotency-Key": "story-llm-offline-turn"},
            json={"game_id": created["game_id"], "input": "拿起火把", "expected_version": 1},
        ).json()

    assert body["ok"] is True, body
    assert body["events"], body
    assert body["events"][0]["result"]["narrative_source"] == "template"
    assert _MIN_TEXT <= len(body["text"]) <= _MAX_TEXT


def test_default_offline_story_first_turns_use_template_narrative(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """默认离线下的 story 对局：create_game 201、移动回合来源 template、目标链可见（零 LLM）。"""
    app = _offline_app(monkeypatch, tmp_path)

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/games",
            headers={"Idempotency-Key": "story-offline-e2e-game"},
            json={"seed": 42, "scenario": "story"},
        )
        assert created.status_code == 201, created.text
        game = created.json()
        body = client.post(
            "/api/v1/turns",
            headers={"Idempotency-Key": "story-offline-e2e-turn"},
            json={"game_id": game["game_id"], "input": "向东走进哨站", "expected_version": 1},
        ).json()
        view = client.get(f"/api/v1/games/{game['game_id']}/view").json()

    assert body["ok"] is True, body
    assert body["events"], body
    assert body["events"][0]["event_type"] == "CHARACTER_MOVED"
    assert body["events"][0]["result"]["narrative_source"] == "template"
    assert _MIN_TEXT <= len(body["text"]) <= _MAX_TEXT
    assert view["room"]["title"] == "废弃哨站·入口"
    assert view["goal"]["title"] == "让信号灯亮起来"
    assert view["status"] == "active"


# ---------------------------------------------------------------------------
# 3) LLM_UNAVAILABLE：回合成功提交 + 来源 template（回退可感知）
# ---------------------------------------------------------------------------
def _m2_orchestrator(narrator: Any) -> TurnOrchestrator:
    """按 M2 单角色口径装配编排器（可注入 LLM 叙事器；内存仓储、零网络）。"""
    repo = InMemoryRepository()
    engine = GameEngine(repo)
    query = QueryService(repo)
    action = ActionService(query)
    return TurnOrchestrator(
        repo=repo,
        engine=engine,
        query=query,
        action=action,
        parser=DeterministicIntentParser(),
        gate=DirectGate(action, query),
        narrator=narrator,
    )


def test_unavailable_llm_turn_commits_with_template_source() -> None:
    """LLM 不可达：回合照常提交、事件 narrative_source=template、文本 80–150、仅尝试 1 次。"""
    llm_client, transport = _unavailable_client()
    narrator = LLMNarratorAgent(llm_client, fallback=TemplateNarrator())
    orchestrator = _m2_orchestrator(narrator)
    created = orchestrator.create_game(
        CreateGameRequest(seed=42, idempotency_key="story-llm-fallback-game")
    )

    result = orchestrator.run(
        TurnRequest(
            game_id=created.game_id,
            text="拿起火把",
            expected_version=1,
            idempotency_key="story-llm-fallback-turn",
        )
    )

    assert result.ok is True, result
    assert result.events, result
    assert result.events[0].result.get("narrative_source") == "template"
    assert "火把" in result.text
    assert _MIN_TEXT <= len(result.text) <= _MAX_TEXT
    assert len(transport.calls) == 1
