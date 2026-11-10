"""M5-021：结构化输出「恰好一次修复 + 确定性兜底」的集成实证（D-M5-2 归属复核）。

归属结论（2026-11-10 复核后定稿）：JSON 解析 / Schema 校验 / 一次修复仍由
``llm/validation.py::complete_json_with_repair`` 承担，由 ``IntentParseAgent`` /
``CraftAgent`` 显式调用；**不迁入 ``LiteLLMClient``、不启用 ``instructor`` 运行路径**
（零新增依赖，``LiteLLMClient`` 只返回原始文本）。本文件用 FakeLLM + 真实编排/HTTP
链路验证：

- 首次 Schema 违规 → **恰好 1 次**修复请求（``max_repair=0``、携带上次错误）→ 提交；
- 修复仍失败 → 解析 Agent 走确定性关键词兜底（成功提交，或无法理解时零写入）；
- 制造 Agent：一次修复成功 → CRAFT_GENERATED 提交；两次失败 → ``SCHEMA_VIOLATION``
  且零写入（版本/事件/``state_hash``/``last_seq`` 不变）；
- 全程只消费注入的 FakeLLM（conftest 全局 socket guard 兜底），无真实网络调用。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from fakes import FakeLLM
from textgame.agents.parser_agent import IntentParseAgent
from textgame.api.app import create_app
from textgame.domain.crafting import CraftMode
from textgame.domain.events import Event, EventType
from textgame.domain.hashing import state_hash
from textgame.domain.results import ErrorCode
from textgame.domain.scenario import ITEM_TORCH, PLAYER_ID
from textgame.orchestration.dto import TurnRequest
from textgame.orchestration.intent import DeterministicIntentParser
from textgame.orchestration.turn_orchestrator import CreateGameRequest, TurnOrchestrator
from textgame.persistence.memory import InMemoryRepository
from textgame.services.action_service import ActionService
from textgame.services.dto import TurnResult
from textgame.services.game_engine import GameEngine
from textgame.services.query_service import QueryService
from textgame.tools.builtin.m2 import build_m2_tool_registry
from textgame.tools.gate import ToolGate

pytestmark = pytest.mark.integration

_GAME_KEY = "game-repair-fallback-0001"
_INVALID_JSON = "抱歉，我无法理解这个动作。"


# ---------------------------------------------------------------------------
# 解析 Agent HTTP 全链路 harness（组合根同口径：FakeLLM 替换 DisabledLLMClient）
# ---------------------------------------------------------------------------
@dataclass
class _ParserHarness:
    """HTTP 客户端 + 内存仓储 + FakeLLM 的可断言装配。"""

    client: TestClient
    repo: InMemoryRepository
    llm: FakeLLM
    game_id: str
    _turn_seq: int = 0

    def snapshot(self) -> tuple[int, int, str, int]:
        """(version, 事件数, state_hash, last_seq)，用于零写入对比。"""
        record = self.repo.load(self.game_id)
        assert record is not None
        return record.version, len(self.events()), state_hash(record.state), record.last_seq

    def events(self) -> list[Event]:
        """当前存档全部事件（只读）。"""
        return self.repo.read_events(self.game_id)

    def post_turn(self, text: str) -> dict[str, Any]:
        """POST /api/v1/turns（自动取当前版本 + 唯一幂等键），返回响应体。"""
        self._turn_seq += 1
        record = self.repo.load(self.game_id)
        assert record is not None
        resp = self.client.post(
            "/api/v1/turns",
            headers={"Idempotency-Key": f"turn-repair-fallback-{self._turn_seq:04d}"},
            json={"game_id": self.game_id, "input": text, "expected_version": record.version},
        )
        assert resp.status_code == 200, resp.text
        return resp.json()


def _valid_parse_output(
    *,
    tool: str = "take_item",
    args: dict[str, Any] | None = None,
    confidence: float = 0.9,
) -> dict[str, Any]:
    """构造符合 ``ParseOutput`` 的合法 LLM JSON 输出。"""
    payload = {"item_id": ITEM_TORCH} if args is None else args
    return {
        "intent": {
            "action": tool,
            "target": payload.get("item_id") or payload.get("direction"),
            "params": payload,
            "confidence": confidence,
        },
        "tool": tool,
        "args": payload,
        "candidate_narrative": "你把地上的东西收进行囊，心里踏实了一些。",
    }


@contextmanager
def _parser_harness(responses: list[Any]) -> Iterator[_ParserHarness]:
    """装配解析 Agent 链（IntentParseAgent + 真实编排/门禁/内存仓储）并创建存档。"""
    repo = InMemoryRepository()
    engine = GameEngine(repo)
    query = QueryService(repo)
    action = ActionService(query)
    registry = build_m2_tool_registry()
    llm = FakeLLM(responses)
    parser = IntentParseAgent(
        llm,
        DeterministicIntentParser(),
        tool_specs=registry.visible_tools(PLAYER_ID, frozenset({"player"})),
    )
    orchestrator = TurnOrchestrator(
        repo=repo,
        engine=engine,
        query=query,
        action=action,
        parser=parser,
        gate=ToolGate(registry=registry, query=query, action=action),
    )
    with TestClient(create_app(orchestrator=orchestrator, query_service=query)) as client:
        created = client.post(
            "/api/v1/games", headers={"Idempotency-Key": _GAME_KEY}, json={"seed": 42}
        )
        assert created.status_code == 201, created.text
        yield _ParserHarness(
            client=client,
            repo=repo,
            llm=llm,
            game_id=str(created.json()["game_id"]),
        )


# ---------------------------------------------------------------------------
# 解析 Agent：一次修复 / 兜底 / 零写入
# ---------------------------------------------------------------------------
def test_parser_first_schema_violation_is_repaired_exactly_once() -> None:
    """首次违规（额外字段）→ 恰好 1 次修复（max_repair=0 + 错误说明）→ 提交事件。"""
    invalid = {**_valid_parse_output(), "hacked": True}
    with _parser_harness([invalid, _valid_parse_output()]) as harness:
        before = harness.snapshot()

        body = harness.post_turn("拿起火把")

        result = TurnResult.model_validate(body)
        assert result.ok is True
        assert [event.event_type for event in result.events] == [EventType.ITEM_TAKEN]
        assert result.events[0].params["item_id"] == ITEM_TORCH
        assert harness.snapshot()[0] == before[0] + 1
        assert harness.snapshot()[1] == before[1] + 1
        # 恰好两次请求：首次允许修复、修复请求禁止再次修复；修复请求携带上次错误
        assert len(harness.llm.calls) == 2
        first, repair = harness.llm.calls
        assert first.max_repair == 1
        assert repair.max_repair == 0
        repair_hint = repair.messages[-1].content
        assert "JSON Schema" in repair_hint
        assert "hacked" in repair_hint or "extra" in repair_hint
        assert repair.messages[:-1] == first.messages


def test_parser_repair_exhausted_falls_back_to_keyword_and_commits() -> None:
    """两次违规 → 确定性关键词兜底（拿起火把 → take_item）→ 提交；不发第 3 次请求。"""
    with _parser_harness([_INVALID_JSON, "not-json", _valid_parse_output()]) as harness:
        before = harness.snapshot()

        body = harness.post_turn("拿起火把")

        result = TurnResult.model_validate(body)
        assert result.ok is True
        assert [event.event_type for event in result.events] == [EventType.ITEM_TAKEN]
        assert result.events[0].params["item_id"] == ITEM_TORCH
        assert harness.snapshot()[1] == before[1] + 1
        assert len(harness.llm.calls) == 2
        assert harness.llm.remaining == 1  # 第 3 条合法响应不得被消费（最多修复一次）


def test_parser_fallback_unparseable_is_zero_write_with_hints() -> None:
    """两次违规 + 兜底也解析不出动作（“跳舞”）→ 零写入，且失败提示字段已填充（A4）。"""
    with _parser_harness([_INVALID_JSON, _INVALID_JSON]) as harness:
        before = harness.snapshot()

        body = harness.post_turn("跳舞")

        result = TurnResult.model_validate(body)
        assert result.ok is False
        assert result.error_code is ErrorCode.INVALID_INPUT
        assert result.events == []
        assert harness.snapshot() == before  # version/事件数/state_hash/last_seq 全不变
        assert len(harness.llm.calls) == 2
        assert body["suggested_actions"]
        assert body["effects"][0]["hint"]


def test_parser_llm_unavailable_does_not_attempt_repair() -> None:
    """客户端不可用（脚本耗尽）不是 Schema 违规：不发起修复，直接确定性兜底提交。"""
    with _parser_harness([]) as harness:
        before = harness.snapshot()

        body = harness.post_turn("拿起火把")

        result = TurnResult.model_validate(body)
        assert result.ok is True
        assert [event.event_type for event in result.events] == [EventType.ITEM_TAKEN]
        assert len(harness.llm.calls) == 1
        assert harness.snapshot()[1] == before[1] + 1


def test_parser_runtime_path_does_not_import_instructor() -> None:
    """归属护栏：运行路径源码不 import instructor（D-M5-2：修复链留在 validation.py）。"""
    src_root = Path(__file__).resolve().parents[2] / "src" / "textgame"
    runtime_files = [
        src_root / "llm" / "validation.py",
        src_root / "agents" / "parser_agent.py",
        src_root / "agents" / "craft_agent.py",
    ]
    for path in runtime_files:
        for line in path.read_text(encoding="utf-8").splitlines():
            statement = line.strip()
            assert not statement.startswith(("import instructor", "from instructor")), path


# ---------------------------------------------------------------------------
# 制造 Agent：一次修复 / 修复耗尽零写入
# ---------------------------------------------------------------------------
_SEED = 1
_CRAFT_TEXT = "把铁锭和铜线做成一个简易电击棒"


def _craft_draft() -> dict[str, Any]:
    """与 ``test_free_craft`` 同构的自由制造草案（数值字段一律由框架重算）。"""
    return {
        "item_name": "简易电击棒",
        "output_template_id": "item.makeshift_taser",
        "inputs": [
            {"template_id": "item.iron_ingot", "qty": 1, "role": "material"},
            {"template_id": "item.copper_wire", "qty": 1, "role": "material"},
        ],
        "station": None,
        "steps": ["把铜线缠在铁锭上", "固定接头"],
    }


def _free_craft_setup(m4_free_harness: Any) -> str:
    """创建 scenario=m4 存档（seed=1，首回合自由制造确定性成功）。"""
    view = m4_free_harness.orchestrator.create_game(
        CreateGameRequest(seed=_SEED, idempotency_key="craft-repair-create-0001", scenario="m4")
    )
    return view.game_id


def _free_craft_turn(m4_free_harness: Any, game_id: str) -> Any:
    """经编排层执行一次自由制造回合（FreeCraftIntentParser 映射为 craft_item(mode=free)）。"""
    return m4_free_harness.orchestrator.run(
        TurnRequest(
            game_id=game_id,
            text=_CRAFT_TEXT,
            expected_version=1,
            idempotency_key="craft-repair-turn-0001",
        )
    )


def _craft_snapshot(m4_free_harness: Any, game_id: str) -> tuple[int, int, str, int]:
    """(version, 事件数, state_hash, last_seq)，用于免费制造回合的零写入对比。"""
    record = m4_free_harness.repo.load(game_id)
    assert record is not None
    return (
        record.version,
        len(m4_free_harness.repo.read_events(game_id)),
        state_hash(record.state),
        record.last_seq,
    )


def test_craft_agent_schema_violation_is_repaired_once_then_commits(
    m4_free_harness: Any,
) -> None:
    """制造 Agent 一次修复：非 JSON 草案 → 修复一次 → CRAFT_GENERATED 提交。"""
    game_id = _free_craft_setup(m4_free_harness)
    before = _craft_snapshot(m4_free_harness, game_id)
    m4_free_harness.fake_llm.reset(["not-json", _craft_draft()])

    result = _free_craft_turn(m4_free_harness, game_id)

    assert result.ok is True
    assert [event.event_type for event in result.events] == [EventType.CRAFT_GENERATED]
    assert result.events[0].params["mode"] == CraftMode.FREE.value
    assert _craft_snapshot(m4_free_harness, game_id)[0] == before[0] + 1
    calls = m4_free_harness.fake_llm.calls
    assert len(calls) == 2
    assert calls[0].max_repair == 1
    assert calls[1].max_repair == 0
    assert "JSON Schema" in calls[1].messages[-1].content


def test_craft_agent_repair_exhausted_is_schema_violation_zero_write(
    m4_free_harness: Any,
) -> None:
    """制造 Agent 两次违规 → SCHEMA_VIOLATION、零写入、只修复一次（第 3 条不消费）。"""
    game_id = _free_craft_setup(m4_free_harness)
    before = _craft_snapshot(m4_free_harness, game_id)
    m4_free_harness.fake_llm.reset(["not-json", "still-not-json", _craft_draft()])

    result = _free_craft_turn(m4_free_harness, game_id)

    assert result.ok is False
    assert result.error_code is ErrorCode.SCHEMA_VIOLATION
    assert result.events == []
    assert _craft_snapshot(m4_free_harness, game_id) == before
    assert len(m4_free_harness.fake_llm.calls) == 2
    assert m4_free_harness.fake_llm.remaining == 1
    assert result.suggested_actions
    assert result.effects[0]["hint"]
