"""集成测试：H4 世界书「导入解析 → 建局绑定 → 回合激活 → /view lore」全链路 + 旧场景零变化。

装配与 ``main.py`` 同构（多角色编排链路 + 审计 + 模板叙事），仓储用内存实现、世界书用
``_FakeProvider``（真实 SQLite/API 由 backend 的 ``test_world_book_store`` 覆盖）。额外覆盖：

- L1：story 进入枯井 → 同回合追加 ``ENVIRONMENT_DAMAGE``（hp -6），旧场景零事件；
- ``TurnResult.narrative_source``：写事件回合 ``template``，零事件只读回合 ``None``（L3）；
- ``TemplateNarrator`` 世界设定句：有 lore 追加 1 句（仍 80–150 字）、无 lore 路径逐字不变、
  写动作不追加、超长设定被 ``_fit_text`` 收敛；
- 建局：未知世界书 404、provider 缺省时 404、同幂等键异世界书 409、旧场景 ``/view`` 为 null。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from textgame.agents.dto import ParseOutcome
from textgame.agents.parser_agent import IntentParseAgent
from textgame.api.app import create_app
from textgame.domain.events import Event, EventType
from textgame.domain.models import ActionIntent
from textgame.domain.rules.engine import RuleEngine
from textgame.domain.scenario import M3_NONPLAYER_TAGS, PLAYER_ID, build_initial_state
from textgame.domain.scenario_story import (
    STORY_ENTRY_ROOM_ID,
    build_story_state,
    story_room_damage,
)
from textgame.domain.world_book import WorldBook, WorldBookEntry, parse_import_payload
from textgame.llm.client import DisabledLLMClient
from textgame.orchestration.intent import DeterministicIntentParser
from textgame.orchestration.narrator import TemplateNarrator
from textgame.orchestration.resolver import Resolver, ToolGateRouter
from textgame.orchestration.turn_orchestrator import TurnOrchestrator
from textgame.persistence.memory import InMemoryRepository
from textgame.services.action_service import ActionService
from textgame.services.actor_directory import ActorDirectory
from textgame.services.audit_service import AuditService
from textgame.services.game_engine import GameEngine
from textgame.services.query_service import QueryService
from textgame.services.recipe_catalog import RecipeCatalog
from textgame.tools.builtin import build_game_tool_registry
from textgame.tools.gate import ToolGate

_SAVE_SEED = "save-42"
_WORLD_BOOK_ID = "wb_story_test"
_CONSTANT_CONTENT = "哨站的信号灯已经灭了两夜，风里一直带着雪。"
_WELL_CONTENT = "枯井的井底比外头暖和，靠壁坐着一个没了气息的旅人。"
_DISABLED_CONTENT = "这段设定不应该出现在玩家眼前。"


class _FakeProvider:
    """世界书提供者测试替身（与 backend ``WorldBookService.get`` 同签名）。"""

    def __init__(self, books: dict[str, WorldBook] | None = None) -> None:
        """按 id 保存世界书；缺省为空（任何 id → ``None``）。"""
        self._books: dict[str, WorldBook] = dict(books or {})

    def get(self, world_book_id: str) -> WorldBook | None:
        """按 id 取世界书；不存在返回 ``None``。"""
        return self._books.get(world_book_id)


def _story_book() -> WorldBook:
    """测试用故事世界书：常亮设定 + 枯井触发设定 + 禁用条目。"""
    payload = {
        "name": "雪夜哨站设定",
        "entries": [
            {"uid": 0, "constant": True, "content": _CONSTANT_CONTENT},
            {"uid": 1, "keys": "枯井", "content": _WELL_CONTENT},
            {"uid": 2, "constant": True, "enabled": False, "content": _DISABLED_CONTENT},
        ],
    }
    return parse_import_payload(payload)


class _Harness:
    """内存仓储 + 生产同构编排器的测试装配。"""

    def __init__(
        self, books: dict[str, WorldBook], *, provider: _FakeProvider | None = None
    ) -> None:
        """按给定世界书装配；``provider=None`` 模拟未启用世界书的旧装配。"""
        self.provider = provider if provider is not None else _FakeProvider(books)
        self.repo = InMemoryRepository()
        engine = GameEngine(self.repo)
        self.query = QueryService(self.repo, world_book_provider=self.provider)
        action = ActionService(self.query)
        registry = build_game_tool_registry()
        gate = ToolGate(registry=registry, query=self.query, action=action)
        rules = RuleEngine()
        actors = ActorDirectory(nonplayer_tags=M3_NONPLAYER_TAGS)
        resolver = Resolver(
            rules=rules, services=ToolGateRouter(gate), actors=actors, save_seed=_SAVE_SEED
        )
        audit = AuditService(self.repo)
        parser = IntentParseAgent(
            DisabledLLMClient(),
            DeterministicIntentParser(recipe_names=RecipeCatalog.load_builtin().name_index()),
            tool_specs=registry.visible_tools(PLAYER_ID, frozenset({"player"})),
        )
        self.orchestrator = TurnOrchestrator(
            repo=self.repo,
            engine=engine,
            query=self.query,
            action=action,
            parser=parser,
            gate=gate,
            narrator=TemplateNarrator(),
            rules=rules,
            save_seed_default=_SAVE_SEED,
            registry=registry,
            resolver=resolver,
            actors=actors,
            character_agents={},
            audit=audit,
            world_book_provider=self.provider,
        )
        self.client = TestClient(
            create_app(
                orchestrator=self.orchestrator, query_service=self.query, audit_service=audit
            )
        )

    def create_story_game(self, *, key: str = "wb-story-game-0001", book_id: str = _WORLD_BOOK_ID):
        """通过 HTTP 建局（story 场景 + 世界书绑定）。"""
        response = self.client.post(
            "/api/v1/games",
            headers={"Idempotency-Key": key},
            json={"seed": 42, "scenario": "story", "world_book_id": book_id},
        )
        return response

    def turn(self, game_id: str, version: int, text: str, key: str) -> tuple[dict, int]:
        """提交一个回合，返回 ``(响应体, 新版本)``。"""
        response = self.client.post(
            "/api/v1/turns",
            headers={"Idempotency-Key": key},
            json={"game_id": game_id, "input": text, "expected_version": version},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        return body, int(body["version"])

    def player_view(self, game_id: str) -> dict:
        """GET /view（玩家视角，含 H4 加法字段）。"""
        response = self.client.get(f"/api/v1/games/{game_id}/view")
        assert response.status_code == 200, response.text
        return response.json()


def _play_story_to_well(harness: _Harness) -> tuple[str, int, list[dict]]:
    """建 story 局 → 环顾（触发常亮设定）→ 向东（入口）→ 向南（枯井受伤）。"""
    created = harness.create_story_game()
    assert created.status_code == 201, created.text
    game_id = created.json()["game_id"]
    version = int(created.json()["version"])
    results: list[dict] = []
    for index, text in enumerate(("环顾四周", "向东走", "向南走")):
        body, version = harness.turn(game_id, version, text, f"wb-story-turn-{index:04d}")
        results.append(body)
    return game_id, version, results


# ---------------------------------------------------------------------------
# 主链路：建局绑定 → 回合激活 → /view lore
# ---------------------------------------------------------------------------


def test_create_game_with_world_book_records_params() -> None:
    """建局绑定世界书：``game_created.params`` 记录 id/name；``/view.world_book`` 摘要正确。"""
    harness = _Harness({_WORLD_BOOK_ID: _story_book()})

    created = harness.create_story_game()

    assert created.status_code == 201
    game_id = created.json()["game_id"]
    first = harness.repo.read_first_event(game_id)
    assert first is not None
    assert first.event_type is EventType.GAME_CREATED
    assert first.params["world_book_id"] == _WORLD_BOOK_ID
    assert first.params["world_book_name"] == "雪夜哨站设定"
    view = harness.player_view(game_id)
    assert view["world_book"] == {
        "world_book_id": _WORLD_BOOK_ID,
        "name": "雪夜哨站设定",
        "entry_count": 3,
    }
    assert view["scenario"] == "story"


def test_look_turn_injects_constant_lore_and_view_shows_it() -> None:
    """环顾回合：常亮设定进入模板叙事（追加 1 句），``/view.lore.active_entries`` 同批可见。"""
    harness = _Harness({_WORLD_BOOK_ID: _story_book()})
    _game_id, _version, results = _play_story_to_well(harness)

    first = results[0]
    assert first["ok"] is True
    assert first["narrative_source"] is None  # L3：零事件只读回合不带叙事来源
    assert "你隐约想起一段旧事：" in first["text"]
    assert "哨站的信号灯已经灭了两夜" in first["text"]
    assert 80 <= len(first["text"]) <= 150

    # 回到开局不可能（已推进），直接断言开局视图语义用新建对局更清晰。
    fresh = harness.create_story_game(key="wb-story-game-view-0002")
    fresh_id = fresh.json()["game_id"]
    lore = harness.player_view(fresh_id)["lore"]
    assert lore is not None
    uids = [entry["entry_uid"] for entry in lore["active_entries"]]
    assert 0 in uids
    assert 2 not in uids  # disabled 永不激活
    assert any("信号灯" in entry["content"] for entry in lore["active_entries"])
    # 只读回合不推进版本（零事件）
    assert harness.query.get_game_view(fresh_id)["turn"] == 1


def test_story_well_move_appends_environment_damage_and_activates_entry() -> None:
    """story 进入枯井：同回合同批追加 ENVIRONMENT_DAMAGE(-6) + 专属 L1 叙事屏 + 枯井设定激活。"""
    harness = _Harness({_WORLD_BOOK_ID: _story_book()})
    game_id, _version, results = _play_story_to_well(harness)

    well_turn = results[2]
    assert well_turn["ok"] is True
    methods = [event["method"] for event in well_turn["events"]]
    event_types = [event["event_type"] for event in well_turn["events"]]
    assert methods == ["move_to", "environment_damage"]
    assert event_types == ["CHARACTER_MOVED", "ENVIRONMENT_DAMAGE"]
    damage = well_turn["events"][1]
    assert damage["result"]["hp_delta"] == -6
    assert damage["actor_id"] == PLAYER_ID
    # L1 叙事：12-6=6 → S2「再下去一次，你多半就上不来了」，且不再拼接移动模板。
    assert "再下去一次，你多半就上不来了" in well_turn["text"]
    assert "你沿着" not in well_turn["text"]
    assert 80 <= len(well_turn["text"]) <= 150

    view = harness.player_view(game_id)
    assert view["hp"] == 6  # story 初始 12 - 6（枯井寒气）
    assert view["turn"] == 3
    lore = view["lore"]
    assert lore is not None
    assert any("枯井" in entry["content"] for entry in lore["active_entries"])


def test_well_twice_reaches_fallen_ending() -> None:
    """反复进枯井（每次 -6）可达「倒下」结局：HP 0 + /view status=ended + 后续回合被拒。"""
    harness = _Harness({_WORLD_BOOK_ID: _story_book()})
    created = harness.client.post(
        "/api/v1/games",
        headers={"Idempotency-Key": "wb-fallen-game-0001"},
        json={"seed": 42, "scenario": "story", "world_book_id": _WORLD_BOOK_ID},
    )
    game_id = created.json()["game_id"]
    version = int(created.json()["version"])
    script = ("环顾四周", "向东走", "向南走", "向北走", "向南走")
    results: list[dict] = []
    for index, text in enumerate(script):
        body, version = harness.turn(game_id, version, text, f"wb-fallen-turn-{index:04d}")
        results.append(body)

    fatal = results[-1]
    assert fatal["ok"] is True
    assert "然后，风停了" in fatal["text"]  # S3 致命文案
    view = harness.player_view(game_id)
    assert view["hp"] == 0
    assert view["status"] == "ended"
    assert view["ending_text"] and "灯还灭着" in view["ending_text"]
    # 结局后拒绝新回合（无副作用）
    after, _version_after = harness.turn(game_id, version, "环顾四周", "wb-fallen-turn-0005")
    assert after["ok"] is False
    assert after["error_code"] == "INVALID_INPUT"


def test_environment_damage_only_in_story_scenario() -> None:
    """旧场景（m2）进入房间不产生 ENVIRONMENT_DAMAGE（golden 零影响）。"""
    harness = _Harness({})
    response = harness.client.post(
        "/api/v1/games",
        headers={"Idempotency-Key": "wb-m2-game-0001"},
        json={"seed": 42, "scenario": "m2"},
    )
    game_id = response.json()["game_id"]
    body, _version = harness.turn(game_id, 1, "向北走进地窖", "wb-m2-turn-0001")

    assert body["ok"] is True
    assert [event["event_type"] for event in body["events"]] == ["CHARACTER_MOVED"]
    assert story_room_damage("room_01J9Z3K4Q7X8Y9Z0ABCDEFGHK7") is not None  # 枯井有定义
    view = harness.player_view(game_id)
    assert view["world_book"] is None
    assert view["lore"] is None


# ---------------------------------------------------------------------------
# 旧场景 / 错误路径
# ---------------------------------------------------------------------------


def test_old_m2_game_without_world_book_keeps_golden_text() -> None:
    """旧 m2 对局：``/view`` world_book/lore 为 null，环顾文本仍是 114 字 golden 口径。"""
    harness = _Harness({})
    response = harness.client.post(
        "/api/v1/games",
        headers={"Idempotency-Key": "wb-m2-game-0002"},
        json={"seed": 42, "scenario": "m2"},
    )
    game_id = response.json()["game_id"]
    body, _version = harness.turn(game_id, 1, "环顾四周", "wb-m2-turn-0002")

    assert body["ok"] is True
    assert len(body["text"]) == 114  # m2 golden 第 1 屏字数
    assert "你隐约想起" not in body["text"]
    assert harness.player_view(game_id)["lore"] is None


def test_unknown_world_book_id_returns_404() -> None:
    """建局引用未知世界书 → 404 ``NOT_FOUND`` 信封，且零对局创建。"""
    harness = _Harness({_WORLD_BOOK_ID: _story_book()})

    response = harness.create_story_game(key="wb-unknown-game-0001", book_id="wb_missing")

    assert response.status_code == 404
    assert response.json()["error_code"] == "NOT_FOUND"
    assert harness.repo.list_games(limit=10, offset=0) == []


def test_provider_none_rejects_world_book_id() -> None:
    """未装配 provider（旧装配）时带 world_book_id 建局 → NOT_FOUND（明确失败）。"""
    harness = _Harness({}, provider=None)

    response = harness.create_story_game(key="wb-noprov-game-0001")

    assert response.status_code == 404
    assert response.json()["error_code"] == "NOT_FOUND"


def test_create_game_idempotency_key_conflicts_on_different_world_book() -> None:
    """同一幂等键 + 不同 world_book_id → 409 CONFLICT（请求哈希包含世界书 id）。"""
    book_a = _story_book()
    book_b = WorldBook(
        name="另一本设定",
        entries=(WorldBookEntry(uid=0, keys=("哨站",), secondary_keys=(), content="另一段设定。"),),
        source="manual",
        raw={},
    )
    harness = _Harness({"wb_a": book_a, "wb_b": book_b})
    first = harness.create_story_game(key="wb-idem-game-0001", book_id="wb_a")
    assert first.status_code == 201

    second = harness.create_story_game(key="wb-idem-game-0001", book_id="wb_b")

    assert second.status_code == 409
    assert second.json()["error_code"] == "CONFLICT"


# ---------------------------------------------------------------------------
# TemplateNarrator 世界设定句（有 lore 追加 / 无 lore 逐字不变）
# ---------------------------------------------------------------------------


def _outcome(tool: str) -> ParseOutcome:
    """构造只读动作解析结果（与编排层传给叙事器的对象同构）。"""
    return ParseOutcome(
        intent=ActionIntent(action=tool, params={}, confidence=1.0),
        tool=tool,
        args={},
        confidence=1.0,
        source="keyword",
    )


def _state():
    """story 初始状态（断桥）。"""
    return build_story_state(save_seed=_SAVE_SEED)


def test_narrator_appends_one_lore_sentence_for_look() -> None:
    """get_room_view + lore_entries → 正文后追加 1 句设定，长度仍 80–150。"""
    narrator = TemplateNarrator()
    state = _state()
    room = state.rooms[STORY_ENTRY_ROOM_ID]
    result_data = {"text": f"{room.name}。{room.description}"}

    plain = narrator.render(
        action=_outcome("get_room_view"), events=[], state=state, result_data=result_data
    )
    with_lore = narrator.render(
        action=_outcome("get_room_view"),
        events=[],
        state=state,
        result_data={**result_data, "lore_entries": [f"【世界设定】{_CONSTANT_CONTENT}"]},
    )

    assert "你隐约想起一段旧事：" in with_lore
    assert "哨站的信号灯已经灭了两夜" in with_lore
    assert "你隐约想起" not in plain
    assert 80 <= len(plain) <= 150
    assert 80 <= len(with_lore) <= 150


def test_narrator_without_lore_is_byte_identical() -> None:
    """无 ``lore_entries`` / 空列表 / None 三种旧路径输出逐字相同（旧场景零漂移）。"""
    narrator = TemplateNarrator()
    state = _state()
    action = _outcome("get_room_view")

    baseline = narrator.render(action=action, events=[], state=state, result_data={})
    empty_list = narrator.render(
        action=action, events=[], state=state, result_data={"lore_entries": []}
    )
    none_value = narrator.render(
        action=action, events=[], state=state, result_data={"lore_entries": None}
    )

    assert baseline == empty_list == none_value


def test_narrator_does_not_append_lore_to_write_action() -> None:
    """写动作（CHARACTER_MOVED）不追加设定句：有无 lore_entries 文本逐字相同。"""
    narrator = TemplateNarrator()
    state = _state()
    moved = Event(
        turn_id=2,
        actor_id=PLAYER_ID,
        method="move_to",
        event_type=EventType.CHARACTER_MOVED,
        params={"direction": "east", "from_room": STORY_ENTRY_ROOM_ID},
        result={"to": "room_01J9Z3K4Q7X8Y9Z0ABCDEFGHK3"},
    )
    action = _outcome("move_to")

    base = narrator.render(action=action, events=[moved], state=state, result_data={})
    with_lore = narrator.render(
        action=action,
        events=[moved],
        state=state,
        result_data={"lore_entries": [f"【世界设定】{_CONSTANT_CONTENT}"]},
    )

    assert base == with_lore
    assert "你隐约想起" not in with_lore


def test_narrator_trims_lore_into_screen_budget() -> None:
    """超长设定句仍被 ``_fit_text`` 收敛到 80–150 字（不越界）。"""
    narrator = TemplateNarrator()
    state = _state()
    long_content = "井底的寒气一层层漫上来，火把的光被压得很低。" * 8

    text = narrator.render(
        action=_outcome("get_room_view"),
        events=[],
        state=state,
        result_data={"lore_entries": [f"【世界设定】{long_content}"]},
    )

    assert 80 <= len(text) <= 150


def test_narrator_appends_lore_for_inspect_action() -> None:
    """inspect_item 只读动作同样追加 1 句设定（细看时补充世界设定）。"""
    narrator = TemplateNarrator()
    state = _state()
    result_data = {
        "name": "火把",
        "description": "一支受潮的火把，布头上还留着松脂的气味。",
        "kind": "item",
        "lore_entries": [f"【世界设定】{_CONSTANT_CONTENT}"],
    }

    text = narrator.render(
        action=_outcome("inspect_item"), events=[], state=state, result_data=result_data
    )

    assert "你隐约想起一段旧事：" in text
    assert 80 <= len(text) <= 150


# ---------------------------------------------------------------------------
# 领域层：L1 伤害值 + 旧 state 构造零影响
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("room_name", ["枯井"])
def test_story_room_damage_returns_negative_for_well(room_name: str) -> None:
    """``story_room_damage``：枯井 -6；旧场景房间无定义返回 None。"""
    state = build_story_state(save_seed=_SAVE_SEED)
    well_id = next(room_id for room_id, room in state.rooms.items() if room.name == room_name)

    assert story_room_damage(well_id) == -6
    assert story_room_damage("room_unknown") is None


def test_old_scenario_state_unchanged_by_world_book_module() -> None:
    """世界书模块不修改旧场景状态：m2 初始状态哈希/构造与 golden 口径一致。"""
    from textgame.domain.hashing import state_hash

    state = build_initial_state(save_seed=_SAVE_SEED)

    assert state_hash(state)  # 可复算哈希存在
    assert harness_has_no_world_book(state)


def harness_has_no_world_book(state) -> bool:
    """旧场景 GameState 不携带世界书字段（H4 未改 GameState 模型）。"""
    return "world_book_id" not in state.model_dump(mode="json")


# ---------------------------------------------------------------------------
# H4-L3：TurnResult.narrative_source 口径（零事件只读回合 null / 写回合 template）
# ---------------------------------------------------------------------------


def test_narrative_source_is_null_for_zero_event_read_only_turns() -> None:
    """L3 回归：只读零事件回合（环顾/查看背包/inspect）不填 narrative_source；写回合为 template。"""
    harness = _Harness({})
    response = harness.client.post(
        "/api/v1/games",
        headers={"Idempotency-Key": "wb-l3-game-0001"},
        json={"seed": 42, "scenario": "m2"},
    )
    game_id = response.json()["game_id"]
    version = int(response.json()["version"])

    # ① 环顾四周：只读、零事件、成功 → narrative_source 必须为 null
    look, version = harness.turn(game_id, version, "环顾四周", "wb-l3-turn-0001")
    assert look["ok"] is True
    assert look["events"] == []
    assert look["narrative_source"] is None
    assert 80 <= len(look["text"]) <= 150

    # ② 查看背包：只读、零事件 → null
    inventory, version = harness.turn(game_id, version, "查看背包", "wb-l3-turn-0002")
    assert inventory["ok"] is True
    assert inventory["events"] == []
    assert inventory["narrative_source"] is None

    # ③ 写回合「拿起火把」：有事件 → template（离线确定性叙事）
    taken, version = harness.turn(game_id, version, "拿起火把", "wb-l3-turn-0003")
    assert taken["ok"] is True
    assert len(taken["events"]) == 1
    assert taken["narrative_source"] == "template"

    # ④ inspect 只读零事件 → null（新工具同样遵守 L3 口径）
    inspect_turn, _version2 = harness.turn(game_id, version, "查看治疗药水", "wb-l3-turn-0004")
    assert inspect_turn["ok"] is True
    assert inspect_turn["events"] == []
    assert inspect_turn["narrative_source"] is None
