"""P1 酒馆级世界书集成测试：导入告警 → 存储还原 → 事件历史 → 回合注入全链路。

覆盖（真实 SQLite 仓储 + 生产同构编排器，零网络、确定性）：

- 导入告警沿用既有 ``[{code, message}]`` 形状，新增正则键非法/未支持字段等条目（只增不改）；
- 仓储只落固定列：``probability``/``scanDepth``/``group`` 与 P1 新增定时效果/包含组字段从
  ``raw_json`` 还原（无迁移），旧数据无需重导；
- 编排层 ``_lore_entries_for`` 用 ``history_from_events`` 从已提交事件叙事重建历史，
  sticky 条目在没有关键词命中的回合仍注入（``TemplateNarrator`` 可观测）；
- 正则键经存储往返后仍生效（房间描述里的「井底/井沿」触发）。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from textgame.agents.parser_agent import IntentParseAgent
from textgame.api.app import create_app
from textgame.domain.rules.engine import RuleEngine
from textgame.domain.scenario import M3_NONPLAYER_TAGS, PLAYER_ID
from textgame.domain.world_book import ActivationTurn, activate_entries
from textgame.llm.client import DisabledLLMClient
from textgame.orchestration.intent import DeterministicIntentParser
from textgame.orchestration.narrator import TemplateNarrator
from textgame.orchestration.resolver import Resolver, ToolGateRouter
from textgame.orchestration.turn_orchestrator import TurnOrchestrator
from textgame.persistence.database import init_schema, prepare_db
from textgame.persistence.memory import InMemoryRepository
from textgame.persistence.world_book_repository import SqliteWorldBookRepository
from textgame.services.action_service import ActionService
from textgame.services.actor_directory import ActorDirectory
from textgame.services.audit_service import AuditService
from textgame.services.game_engine import GameEngine
from textgame.services.query_service import QueryService
from textgame.services.recipe_catalog import RecipeCatalog
from textgame.services.world_book_service import WorldBookService
from textgame.tools.builtin import build_game_tool_registry
from textgame.tools.gate import ToolGate

_SAVE_SEED = "save-42"

#: sticky 条目：只出现在移动叙事里（``scanDepth=1`` → 当前上下文只看玩家输入）。
_STICKY_CONTENT = "哨站的旧道在雪下若隐若现，踩上去能听见冻土开裂的轻响。"
#: 正则条目：只在枯井（房间描述含「井底/井沿」）触发；hub 描述只有「枯井」二字，不命中。
_REGEX_CONTENT = "井底比井口暖和得多，寒气却从脚底一层层漫上来。"


def _book_payload() -> dict[str, object]:
    """测试世界书：正则键 + 定时效果（SillyTavern 字段名）。"""
    return {
        "name": "余烬哨站·酒馆级设定",
        "description": "P1 集成测试用",
        "entries": {
            "0": {
                "uid": 0,
                "key": ["/井底|井沿/"],
                "content": _REGEX_CONTENT,
                "order": 10,
            },
            "1": {
                "uid": 1,
                "key": ["你沿着"],
                "content": _STICKY_CONTENT,
                "order": 20,
                "sticky": 2,
                "scanDepth": 1,
            },
        },
    }


@pytest.fixture()
def service() -> WorldBookService:
    """内存 SQLite + 真仓储 + 真服务（每个用例独立库）。"""
    engine, _ = prepare_db("sqlite:///:memory:")
    init_schema(engine)
    return WorldBookService(SqliteWorldBookRepository(engine))


def _import(
    service: WorldBookService,
    payload: dict[str, object],
    *,
    key: str = "wb-tavern-import-0001",
) -> dict[str, object]:
    """把 payload 序列化为 UTF-8 JSON 后走导入（固定文件名/幂等键）。"""
    content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return service.import_book(
        filename="lore.json",
        content_type="application/json",
        content=content,
        name=None,
        idempotency_key=key,
    )


class _Harness:
    """内存事件仓储 + 生产同构编排器；世界书 provider 用真实 ``WorldBookService``。"""

    def __init__(self, service: WorldBookService) -> None:
        """按世界书服务装配（与 ``main.py`` 同构的多角色链路 + 模板叙事）。"""
        self.service = service
        self.repo = InMemoryRepository()
        engine = GameEngine(self.repo)
        query = QueryService(self.repo, world_book_provider=service)
        action = ActionService(query)
        registry = build_game_tool_registry()
        gate = ToolGate(registry=registry, query=query, action=action)
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
            query=query,
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
            world_book_provider=service,
        )
        self.client = TestClient(
            create_app(orchestrator=self.orchestrator, query_service=query, audit_service=audit)
        )

    def create_story_game(self, world_book_id: str, *, key: str = "wb-tavern-game-0001") -> str:
        """通过 HTTP 建 story 局并绑定世界书，返回 game_id。"""
        created = self.client.post(
            "/api/v1/games",
            headers={"Idempotency-Key": key},
            json={"seed": 42, "scenario": "story", "world_book_id": world_book_id},
        )
        assert created.status_code == 201, created.text
        return str(created.json()["game_id"])

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


# ---------------------------------------------------------------------------
# 导入告警形状 / 存储还原
# ---------------------------------------------------------------------------
def test_import_keeps_warning_shape_and_adds_tavern_diagnostics(
    service: WorldBookService,
) -> None:
    """导入告警保持 ``[{code, message}]`` 形状；新增正则非法/未支持字段/定时历史说明。"""
    payload = {
        "data": {
            "name": "角色卡",
            "character_book": {
                "name": "手札",
                "scan_depth": 4,
                "entries": [
                    {
                        "uid": 0,
                        "keys": ["/[/i", "灯"],
                        "content": "设。",
                        "sticky": 2,
                        "vectorized": True,
                    }
                ],
            },
        }
    }
    result = _import(service, payload, key="wb-tavern-import-warnings")

    warnings = result["warnings"]
    assert isinstance(warnings, list)
    for warning in warnings:  # type: ignore[union-attr]
        assert set(warning) == {"code", "message"}
    codes = [warning["code"] for warning in warnings]  # type: ignore[index]
    assert codes[0] == "CHARACTER_BOOK_EXTRACTED"  # 既有告警仍在（只增条目）
    assert "REGEX_KEY_INVALID" in codes
    assert "UNSUPPORTED_FIELD" in codes
    assert "TIMED_EFFECTS_HISTORY_ONLY" in codes
    assert "BOOK_GLOBAL_SETTINGS_IGNORED" in codes


def test_stored_book_restores_p0_and_p1_fields_from_raw_json(
    service: WorldBookService,
) -> None:
    """仓储固定列外的字段（概率/扫描深度/分组/定时）从 raw_json 还原，无需迁移。"""
    payload = {
        "entries": {
            "0": {
                "uid": 0,
                "key": ["灯"],
                "content": "灯。",
                "probability": 40,
                "useProbability": True,
                "scanDepth": 2,
                "group": "signal",
                "groupWeight": "2.5",
                "groupOverride": True,
                "sticky": 2,
                "cooldown": 1,
                "delay": 3,
            }
        }
    }
    imported = _import(service, payload, key="wb-tavern-import-fields")

    book = service.get(str(imported["world_book_id"]))
    assert book is not None
    entry = book.entries[0]

    assert entry.probability == 40
    assert entry.use_probability is True
    assert entry.scan_depth == 2
    assert entry.group == "signal"
    assert entry.group_weight == 2.5
    assert entry.group_override is True
    assert (entry.sticky, entry.cooldown, entry.delay) == (2, 1, 3)


def test_restored_timed_fields_drive_activation(service: WorldBookService) -> None:
    """落库→读回后的 sticky 字段参与激活：触发后 2 回合内无关键词仍激活。"""
    payload = {
        "entries": [{"uid": 0, "key": ["灯"], "content": "灯。", "sticky": 2, "scanDepth": 1}]
    }
    imported = _import(service, payload, key="wb-tavern-import-timed")
    book = service.get(str(imported["world_book_id"]))
    assert book is not None

    history = (ActivationTurn(turn_id=2, context_texts=("灯",)),)

    active = activate_entries(
        book,
        context_texts=["无关"],
        turn_id=4,
        save_seed=_SAVE_SEED,
        history=history,
    )
    expired = activate_entries(
        book,
        context_texts=["无关"],
        turn_id=5,
        save_seed=_SAVE_SEED,
        history=history,
    )

    assert [entry.uid for entry in active] == [0]
    assert expired == []


def test_validate_payload_warnings_are_read_only(service: WorldBookService) -> None:
    """``validate_payload`` 返回新告警但不落库（零写入）。"""
    payload = {"entries": [{"uid": 0, "key": "/[/i", "content": "设。"}]}

    validated = service.validate_payload(payload)

    assert validated["valid"] is True
    codes = [warning["code"] for warning in validated["warnings"]]
    assert "REGEX_KEY_INVALID" in codes
    assert service.list_world_books(limit=20)["total"] == 0


# ---------------------------------------------------------------------------
# 全链路：导入 → 建局绑定 → 事件历史 → 回合注入
# ---------------------------------------------------------------------------
def test_import_to_event_history_to_turn_injection(service: WorldBookService) -> None:
    """sticky 与正则键在全链路生效：历史叙事触发后免关键词续期；存储往返后正则仍匹配。"""
    imported = _import(service, _book_payload(), key="wb-tavern-import-flow")
    harness = _Harness(service)
    game_id = harness.create_story_game(str(imported["world_book_id"]))
    version = 1

    # 第 2 回合：断桥 → 入口（写事件；本回合还没有历史，sticky 不激活）。
    moved, version = harness.turn(game_id, version, "向东走", "wb-tavern-turn-0002")
    assert moved["ok"] is True
    assert moved["events"]

    # 第 3 回合：环顾（零事件只读）。历史里第 2 回合叙事含「你沿着」→ sticky 续期注入。
    look, version = harness.turn(game_id, version, "环顾四周", "wb-tavern-turn-0003")
    assert look["ok"] is True
    assert look["events"] == []
    assert "你隐约想起一段旧事：" in look["text"]
    assert _STICKY_CONTENT[:10] in look["text"]
    assert _REGEX_CONTENT[:10] not in look["text"]  # 入口房描述不含「井底/井沿」

    # 第 4 回合：入口 → 枯井（写事件 + 环境伤害；不推进幂等键语义）。
    well_move, version = harness.turn(game_id, version, "向南走", "wb-tavern-turn-0004")
    assert well_move["ok"] is True

    # 第 5 回合：井边环顾（零事件只读）：正则键经存储往返后由房间描述命中，排序在 sticky 之前。
    well_look, _version = harness.turn(game_id, version, "环顾四周", "wb-tavern-turn-0005")
    assert well_look["ok"] is True
    assert well_look["events"] == []
    assert "你隐约想起一段旧事：" in well_look["text"]
    assert _REGEX_CONTENT[:10] in well_look["text"]
