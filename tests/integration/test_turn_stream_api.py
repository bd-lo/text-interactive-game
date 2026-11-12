"""集成测试：N2-038 流式回合端点 ``POST /api/v1/turns/stream``（NDJSON，ADR D-N2-1/2/5）。

覆盖：事件顺序与 5 类事件、``done.result`` 与 ``/turns`` 的 ``TurnResult`` 等价（模板叙事下
逐字段相同）、模板单段回退、业务失败 ``error`` 事件（HTTP 200）、幂等重放单段下发、同键异参
冲突、LLM 中途失败 → ``replace`` 模板、Schema 修复 → ``replace`` 权威文本、跨 chunk 转义
``exact match``、开关关闭 404、**客户端提前断连后回合仍提交**（D-N2-5）、协议错误流前 4xx。
全部离线：LLM 叙事客户端为脚本化替身（零 socket，Q9）。
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient

from textgame.agents.narrator_agent import LLMNarratorAgent
from textgame.api.app import create_app
from textgame.api.routes.turns import _stream_generator
from textgame.config.settings import get_settings
from textgame.domain.results import ErrorCode, Result
from textgame.llm.client import DisabledLLMClient, LLMRequest
from textgame.orchestration.direct_gate import DirectGate
from textgame.orchestration.dto import TurnRequest
from textgame.orchestration.intent import DeterministicIntentParser
from textgame.orchestration.narrator import TemplateNarrator
from textgame.orchestration.turn_orchestrator import TurnOrchestrator
from textgame.persistence.memory import InMemoryRepository
from textgame.services.action_service import ActionService
from textgame.services.game_engine import GameEngine
from textgame.services.query_service import QueryService

pytestmark = pytest.mark.integration

_GAME_KEY = "stream-game-key-0001"
_TURN_KEY = "stream-turn-key-0001"

#: 结构合法、长度 80–150 的中文叙事（``NarrativeOutput`` Schema 验收）。
_NARRATIVE = (
    "你沿着北边的通道动身，脚下的石板缝里积着陈年的灰，石阶上浮着一层薄薄的潮气。"
    "走了不多时，地窖的气息扑面而来，光线也跟着暗了一截。"
    "你放慢脚步站定，打量着这里的环境，墙角堆着几只落了灰的木箱。"
)
#: 含转义边界（引号/反斜杠/中文/emoji 代理对）的叙事（跨 chunk exact match 用）。
_TRICKY_NARRATIVE = _NARRATIVE + "他说「快走」\\ 然后\n抬头\t看见一片星空，还有😀。"


class ScriptedNarrateClient:
    """脚本化 LLM 叙事客户端：``complete_json_stream`` 可编排 chunk/中途失败/阻塞。

    - ``content``：流式成功时返回的完整 JSON 文本（默认 ``{"narrative": ...}``）；
    - ``chunks``：逐段下发的原始文本（默认按 7 字切分 ``content``）；
    - ``repair_content``：``complete_json``（一次修复链）返回的完整 JSON；
    - ``fail_after_chunks``：下发 N 段后抛异常（模拟流式中途失败）；
    - ``gate``：下发 ``gate_after_chunks`` 段后阻塞等待（断连测试用），``release`` 后继续完成。
    """

    def __init__(
        self,
        *,
        content: str | None = None,
        chunks: list[str] | None = None,
        repair_content: str | None = None,
        fail_after_chunks: int | None = None,
        gate: threading.Event | None = None,
        gate_after_chunks: int = 1,
    ) -> None:
        self.content = (
            content
            if content is not None
            else json.dumps({"narrative": _NARRATIVE}, ensure_ascii=False)
        )
        self.chunks = chunks if chunks is not None else _split(self.content, 7)
        self.repair_content = repair_content
        self.fail_after_chunks = fail_after_chunks
        self.gate = gate
        self.gate_after_chunks = gate_after_chunks
        self.stream_calls: list[LLMRequest] = []
        self.text_calls: list[LLMRequest] = []

    def complete_json(self, req: LLMRequest) -> Result:
        """非流式路径（修复请求）：按 ``repair_content`` 返回，未配置则失败。"""
        self.text_calls.append(req)
        if self.repair_content is None:
            return Result.fail(ErrorCode.LLM_UNAVAILABLE, "scripted: no repair content")
        return Result.success(data={"content": self.repair_content, "repaired": True, "usage": {}})

    def complete_json_stream(self, req: LLMRequest, on_delta: Any) -> Result:
        """流式路径：按脚本下发 chunk，可中途失败/阻塞后继续。"""
        self.stream_calls.append(req)
        for index, chunk in enumerate(self.chunks):
            if self.gate is not None and index == self.gate_after_chunks:
                assert self.gate.wait(timeout=10.0), "断连测试 gate 未被释放（可能死锁）"
            on_delta(chunk)
            if self.fail_after_chunks is not None and index + 1 >= self.fail_after_chunks:
                raise RuntimeError("scripted: stream broke")
        return Result.success(data={"content": self.content, "repaired": False, "usage": {}})


def _json_prefix_chunks(narrative: str) -> list[str]:
    """把 ``{"narrative": ...}`` 切成「JSON 前缀 + 两段正文 + 后缀」（便于控制断点位置）。"""
    return [
        '{"narrative": "',
        narrative[:10],
        narrative[10:],
        '"}',
    ]


def _split(text: str, size: int) -> list[str]:
    """把文本切成固定大小片段（模拟供应商 chunk）。"""
    return [text[index : index + size] for index in range(0, len(text), size)]


@dataclass
class StreamHarness:
    """流式端点测试装配：HTTP 客户端 + 内存仓储 + 编排器 + 注入的 LLM 替身。"""

    client: TestClient
    repo: InMemoryRepository
    orchestrator: TurnOrchestrator
    llm: Any


def _build_harness(
    *,
    llm: Any = None,
    narrator: Any = None,
) -> tuple[StreamHarness, TestClient]:
    """按 M2 单角色口径装配（可注入 LLM 叙事器/模板叙事器）。"""
    repo = InMemoryRepository()
    engine = GameEngine(repo)
    query = QueryService(repo)
    action = ActionService(query)
    if narrator is None:
        narrator = (
            TemplateNarrator()
            if llm is None
            else LLMNarratorAgent(llm, fallback=TemplateNarrator())
        )
    orchestrator = TurnOrchestrator(
        repo=repo,
        engine=engine,
        query=query,
        action=action,
        parser=DeterministicIntentParser(),
        gate=DirectGate(action, query),
        narrator=narrator,
    )
    app = create_app(orchestrator=orchestrator, query_service=query)
    client = TestClient(app)
    harness = StreamHarness(client=client, repo=repo, orchestrator=orchestrator, llm=llm)
    return harness, client


@contextmanager
def _harness(*, llm: Any = None, narrator: Any = None) -> Iterator[StreamHarness]:
    """``with`` 管理 TestClient 生命周期（关闭时触发应用 shutdown）。"""
    harness, client = _build_harness(llm=llm, narrator=narrator)
    with client:
        yield harness


def _create_game(client: TestClient, *, key: str = _GAME_KEY, seed: int = 42) -> str:
    """POST /api/v1/games 并返回 game_id（201 断言）。"""
    resp = client.post("/api/v1/games", headers={"Idempotency-Key": key}, json={"seed": seed})
    assert resp.status_code == 201, resp.text
    return str(resp.json()["game_id"])


def _post_stream(
    client: TestClient,
    *,
    game_id: str,
    text: str,
    expected_version: int,
    key: str = _TURN_KEY,
) -> tuple[int, list[dict[str, Any]], str]:
    """POST /turns/stream，返回 ``(status, 事件列表, content-type)``（一次性读完）。"""
    resp = client.post(
        "/api/v1/turns/stream",
        headers={"Idempotency-Key": key},
        json={"game_id": game_id, "input": text, "expected_version": expected_version},
    )
    lines = [line for line in resp.text.splitlines() if line]
    return (
        resp.status_code,
        [json.loads(line) for line in lines],
        resp.headers.get("content-type", ""),
    )


def _post_turn(
    client: TestClient,
    *,
    game_id: str,
    text: str,
    expected_version: int,
    key: str = "plain-turn-key-0001",
) -> tuple[int, dict[str, Any]]:
    """POST /api/v1/turns（对照组）。"""
    resp = client.post(
        "/api/v1/turns",
        headers={"Idempotency-Key": key},
        json={"game_id": game_id, "input": text, "expected_version": expected_version},
    )
    return resp.status_code, resp.json()


def _wait_for_version(
    repo: InMemoryRepository, game_id: str, version: int, *, timeout_s: float = 8.0
) -> Any:
    """轮询等待仓储版本推进（断连后后台线程仍提交，D-N2-5）。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        record = repo.load(game_id)
        if record is not None and record.version == version:
            return record
        time.sleep(0.01)
    return repo.load(game_id)


def _strip_event_ts(result: dict[str, Any]) -> dict[str, Any]:
    """去掉事件盖章墙钟 ``ts``（同 seed 下两份装配唯一非确定字段），用于等价断言。"""
    normalized = json.loads(json.dumps(result))
    for event in normalized.get("events", []):
        event.pop("ts", None)
    return normalized


# ---------------------------------------------------------------------------
# 成功路径 / 事件协议
# ---------------------------------------------------------------------------
def test_stream_success_event_sequence_and_commit() -> None:
    """成功流：start → delta… → done；拼接 == 权威文本；回合照常提交（version 2）。"""
    llm = ScriptedNarrateClient()
    with _harness(llm=llm) as h:
        game_id = _create_game(h.client)

        status, events, content_type = _post_stream(
            h.client, game_id=game_id, text="拿起火把", expected_version=1
        )

        assert status == 200
        assert "application/x-ndjson" in content_type
        assert events[0] == {"type": "start", "turn_id": 2, "expected_version": 1}
        assert events[-1]["type"] == "done"
        delta_text = "".join(event["text"] for event in events if event["type"] == "delta")
        done = events[-1]["result"]
        assert delta_text == done["text"] == _NARRATIVE
        assert done["ok"] is True
        assert done["version"] == 2
        assert done["turn_id"] == 2
        assert done["narrative_source"] == "llm"
        assert [event["event_type"] for event in done["events"]] == ["ITEM_TAKEN"]
        assert len(llm.stream_calls) == 1
        # 回合已提交：事件日志 +1（GAME_CREATED + ITEM_TAKEN），版本推进。
        assert len(h.repo.read_events(game_id)) == 2
        assert h.repo.load(game_id).version == 2


def test_stream_done_result_equals_plain_turns_result() -> None:
    """模板叙事下 ``done.result`` 与 ``POST /turns`` 的 ``TurnResult`` 逐字段相同。"""
    with _harness(narrator=TemplateNarrator()) as stream_h:
        game_a = _create_game(stream_h.client, key="stream-eq-game-a")
        stream_status, events, _ = _post_stream(
            stream_h.client,
            game_id=game_a,
            text="拿起火把",
            expected_version=1,
            key="stream-eq-turn-a",
        )
    with _harness(narrator=TemplateNarrator()) as plain_h:
        game_b = _create_game(plain_h.client, key="stream-eq-game-b")
        plain_status, plain_result = _post_turn(
            plain_h.client,
            game_id=game_b,
            text="拿起火把",
            expected_version=1,
            key="stream-eq-turn-b",
        )

    assert stream_status == plain_status == 200
    assert events[-1]["type"] == "done"
    assert _strip_event_ts(events[-1]["result"]) == _strip_event_ts(plain_result)
    # 模板叙事无增量收益：单段完整 delta（不是 replace）。
    delta_events = [event for event in events if event["type"] == "delta"]
    assert [event["text"] for event in delta_events] == [plain_result["text"]]
    assert not [event for event in events if event["type"] == "replace"]


def test_stream_offline_client_template_single_chunk() -> None:
    """LLM 离线（DisabledLLMClient）→ 模板单 chunk 回退：start/delta/done，无 replace。"""
    with _harness(llm=DisabledLLMClient()) as h:
        game_id = _create_game(h.client)

        status, events, _ = _post_stream(
            h.client, game_id=game_id, text="拿起火把", expected_version=1
        )

    assert status == 200
    assert [event["type"] for event in events] == ["start", "delta", "done"]
    done = events[-1]["result"]
    assert done["narrative_source"] == "template"
    assert events[1]["text"] == done["text"]
    assert 80 <= len(done["text"]) <= 150


def test_stream_escaped_unicode_across_chunks_exact_match() -> None:
    """转义 JSON（``ensure_ascii=True``）按字符切 chunk：增量拼接与 done 文本 exact match。"""
    content = json.dumps({"narrative": _TRICKY_NARRATIVE}, ensure_ascii=True)
    llm = ScriptedNarrateClient(content=content, chunks=list(content))
    with _harness(llm=llm) as h:
        game_id = _create_game(h.client)

        status, events, _ = _post_stream(
            h.client, game_id=game_id, text="拿起火把", expected_version=1
        )

    assert status == 200
    delta_text = "".join(event["text"] for event in events if event["type"] == "delta")
    assert delta_text == _TRICKY_NARRATIVE
    assert events[-1]["result"]["text"] == _TRICKY_NARRATIVE


# ---------------------------------------------------------------------------
# 业务失败 / 协议错误
# ---------------------------------------------------------------------------
def test_stream_stale_version_error_event_zero_writes() -> None:
    """expected_version 过期 → start 后 ``error``（HTTP 200，零写入，无 done）。"""
    with _harness(narrator=TemplateNarrator()) as h:
        game_id = _create_game(h.client)

        status, events, _ = _post_stream(
            h.client, game_id=game_id, text="拿起火把", expected_version=0
        )

        assert status == 200
        assert [event["type"] for event in events] == ["start", "error"]
        assert events[1]["error_code"] == "CONFLICT"
        assert events[1]["message"]
        assert h.repo.load(game_id).version == 1
        assert len(h.repo.read_events(game_id)) == 1


def test_stream_unknown_game_error_event() -> None:
    """未知存档 → ``error`` NOT_FOUND（回合未提交）。"""
    with _harness(narrator=TemplateNarrator()) as h:
        status, events, _ = _post_stream(
            h.client, game_id="game_unknown", text="环顾四周", expected_version=0
        )

    assert status == 200
    assert [event["type"] for event in events] == ["start", "error"]
    assert events[1]["error_code"] == "NOT_FOUND"


def test_stream_requires_idempotency_key_before_stream() -> None:
    """缺 ``Idempotency-Key`` → 流开始前 422 统一失败信封（与 /turns 同口径）。"""
    with _harness(narrator=TemplateNarrator()) as h:
        game_id = _create_game(h.client)

        resp = h.client.post(
            "/api/v1/turns/stream",
            json={"game_id": game_id, "input": "环顾四周", "expected_version": 1},
        )

    assert resp.status_code == 422
    body = resp.json()
    assert body["ok"] is False
    assert body["error_code"] == "INVALID_INPUT"


def test_stream_invalid_body_before_stream() -> None:
    """body 未知字段 → 流开始前 422（不是 error 事件）。"""
    with _harness(narrator=TemplateNarrator()) as h:
        game_id = _create_game(h.client)

        resp = h.client.post(
            "/api/v1/turns/stream",
            headers={"Idempotency-Key": "stream-bad-body-01"},
            json={
                "game_id": game_id,
                "input": "环顾四周",
                "expected_version": 1,
                "actor_id": "char_x",
            },
        )

    assert resp.status_code == 422
    assert resp.json()["error_code"] == "INVALID_INPUT"


# ---------------------------------------------------------------------------
# 幂等
# ---------------------------------------------------------------------------
def test_stream_idempotent_replay_single_delta_and_done() -> None:
    """同键同参重放：单段完整 delta + 相同 done.result，不重复调用 LLM、不重复写事件。"""
    llm = ScriptedNarrateClient()
    with _harness(llm=llm) as h:
        game_id = _create_game(h.client)
        request = {"game_id": game_id, "text": "拿起火把", "expected_version": 1}

        first_status, first_events, _ = _post_stream(h.client, **request)
        events_after_first = len(h.repo.read_events(game_id))
        second_status, second_events, _ = _post_stream(h.client, **request)

        assert first_status == second_status == 200
        assert first_events[-1]["type"] == second_events[-1]["type"] == "done"
        assert first_events[-1]["result"] == second_events[-1]["result"]
        assert [event["type"] for event in second_events] == ["start", "delta", "done"]
        assert second_events[1]["text"] == second_events[-1]["result"]["text"]
        assert len(llm.stream_calls) == 1
        assert len(h.repo.read_events(game_id)) == events_after_first


def test_stream_same_key_different_params_error_after_commit() -> None:
    """同键异参 → error CONFLICT（零写入；已提交首回合不受影响）。"""
    llm = ScriptedNarrateClient()
    with _harness(llm=llm) as h:
        game_id = _create_game(h.client)
        _post_stream(h.client, game_id=game_id, text="拿起火把", expected_version=1)
        events_after_first = len(h.repo.read_events(game_id))

        status, events, _ = _post_stream(
            h.client, game_id=game_id, text="环顾四周", expected_version=2
        )

        assert status == 200
        assert events[-1]["type"] == "error"
        assert events[-1]["error_code"] == "CONFLICT"
        assert len(h.repo.read_events(game_id)) == events_after_first


# ---------------------------------------------------------------------------
# replace：LLM 中途失败 / Schema 修复
# ---------------------------------------------------------------------------
def test_stream_mid_stream_failure_replaces_with_template() -> None:
    """首 chunk 后失败 → 已显示增量以 ``replace`` 覆盖为模板权威文本，回合照常提交。"""
    llm = ScriptedNarrateClient(chunks=_json_prefix_chunks(_NARRATIVE), fail_after_chunks=2)
    with _harness(llm=llm) as h:
        game_id = _create_game(h.client)

        status, events, _ = _post_stream(
            h.client, game_id=game_id, text="拿起火把", expected_version=1
        )

        done = events[-1]["result"]
        assert status == 200
        assert [event["type"] for event in events] == ["start", "delta", "replace", "done"]
        assert events[2]["reason"] == "TEMPLATE_FALLBACK"
        assert events[2]["text"] == done["text"]
        assert done["narrative_source"] == "template"
        assert 80 <= len(done["text"]) <= 150
        assert h.repo.load(game_id).version == 2


def test_stream_schema_repair_replaces_with_authoritative_text() -> None:
    """流式完整 JSON 不合格 → 原一次修复链；``replace`` 为修复后的权威文本。"""
    llm = ScriptedNarrateClient(
        content=json.dumps({"narrative": "太短"}, ensure_ascii=False),
        repair_content=json.dumps({"narrative": _NARRATIVE}, ensure_ascii=False),
    )
    with _harness(llm=llm) as h:
        game_id = _create_game(h.client)

        status, events, _ = _post_stream(
            h.client, game_id=game_id, text="拿起火把", expected_version=1
        )

        done = events[-1]["result"]
        assert status == 200
        assert [event["type"] for event in events] == ["start", "delta", "replace", "done"]
        assert events[1]["text"] == "太短"
        assert events[2]["reason"] == "NARRATIVE_REVISED"
        assert events[2]["text"] == done["text"] == _NARRATIVE
        assert done["narrative_source"] == "llm"
        assert len(llm.text_calls) == 1  # 恰好一次修复


# ---------------------------------------------------------------------------
# 断连不撤销（D-N2-5）
# ---------------------------------------------------------------------------
def test_stream_disconnect_after_first_delta_still_commits() -> None:
    """客户端收到首段增量后断开（生成器 close）：工作线程继续完成并提交回合（D-N2-5）。

    用「生成器提前 close」模拟断连（ADR D-N2-5 验收口径）：关闭只停止事件消费，不取消工作
    线程——回合在叙事完整结束后照常经唯一写入者/CAS 提交。
    """
    gate = threading.Event()
    llm = ScriptedNarrateClient(
        chunks=_json_prefix_chunks(_NARRATIVE), gate=gate, gate_after_chunks=2
    )
    with _harness(llm=llm) as h:
        game_id = _create_game(h.client)
        request = TurnRequest(
            game_id=game_id,
            text="拿起火把",
            expected_version=1,
            idempotency_key="stream-disconnect-01",
        )
        generator = _stream_generator(h.orchestrator, request, expected_version=1)
        first = json.loads(next(generator))
        second = json.loads(next(generator))
        assert first["type"] == "start"
        assert second["type"] == "delta"
        generator.close()  # 断连：只停止消费事件，后台工作线程继续
        gate.set()
        record = _wait_for_version(h.repo, game_id, 2)
        committed = h.repo.read_events(game_id)

    assert record is not None
    assert record.version == 2
    assert len(llm.stream_calls) == 1
    assert committed[-1].result["narrative"] == _NARRATIVE


# ---------------------------------------------------------------------------
# 开关回滚（D-N2-8）
# ---------------------------------------------------------------------------
def test_stream_disabled_returns_404_and_turns_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``TEXTGAME_STREAM_ENABLED=false`` → 404 统一信封；旧 ``POST /turns`` 行为不变。"""
    with _harness(narrator=TemplateNarrator()) as h:
        game_id = _create_game(h.client)
        monkeypatch.setenv("TEXTGAME_STREAM_ENABLED", "false")
        get_settings.cache_clear()

        resp = h.client.post(
            "/api/v1/turns/stream",
            headers={"Idempotency-Key": "stream-disabled-01"},
            json={"game_id": game_id, "input": "环顾四周", "expected_version": 1},
        )

        assert resp.status_code == 404
        body = resp.json()
        assert body["ok"] is False
        assert body["error_code"] == "NOT_FOUND"

        status, plain = _post_turn(
            h.client,
            game_id=game_id,
            text="拿起火把",
            expected_version=1,
            key="plain-after-disabled",
        )
        assert status == 200
        assert plain["ok"] is True
    get_settings.cache_clear()
