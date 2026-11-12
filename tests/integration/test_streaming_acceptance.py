"""N2-040 流式叙事阶段 1 验收（后端双路径证据）。

覆盖 ADR D-N2-1/2/5/8 与 038 后端的**验收语义**（038 单测之外的端到端口径）：

1. **离线回退等价**：`TEXTGAME_LLM_ENABLED=false` 的生产组合根（临时 SQLite）下，
   `POST /api/v1/turns/stream` 返回 `start → 单条完整 delta → done`；`done.result` 与同输入
   `POST /api/v1/turns` 的 `TurnResult` 等价（ok/version/turn_id/text/narrative_source/events），
   回合照常提交（版本推进、事件 narrative 落库）。
2. **开关回滚**：`TEXTGAME_STREAM_ENABLED=false` → `/turns/stream` 404 统一信封且零写入，
   旧 `/turns` 正常；重新启用后流式端点恢复（一键回滚可逆）。
3. **断连不撤销**（D-N2-5）：HTTP 层「读到 start 即关闭流」后回合仍提交并可回放；另用
   受控 gate 的脚本化 LLM 叙事证明**提交确实发生在断连之后**（不是断连前已提交的巧合）。

全部离线（LLM 关闭或脚本化替身，零 socket）；生产装配的 DB 一律指向 `tmp_path`，不碰
仓库 `data/textgame.db`。
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from textgame.agents.narrator_agent import LLMNarratorAgent
from textgame.api.routes.turns import _stream_generator
from textgame.config.settings import get_settings
from textgame.domain.results import ErrorCode, Result
from textgame.llm.client import LLMRequest
from textgame.orchestration.direct_gate import DirectGate
from textgame.orchestration.dto import TurnRequest
from textgame.orchestration.intent import DeterministicIntentParser
from textgame.orchestration.narrator import TemplateNarrator
from textgame.orchestration.turn_orchestrator import (
    CreateGameRequest,
    TurnOrchestrator,
)
from textgame.persistence.memory import InMemoryRepository
from textgame.services.action_service import ActionService
from textgame.services.game_engine import GameEngine
from textgame.services.query_service import QueryService

pytestmark = pytest.mark.integration

_STREAM_URL = "/api/v1/turns/stream"
_PLAIN_URL = "/api/v1/turns"
_NDJSON = "application/x-ndjson"

#: 脚本化 LLM 叙事文本（80–150 字，``NarrativeOutput`` Schema 验收）。
_NARRATIVE = (
    "你沿着北边的通道动身，脚下的石板缝里积着陈年的灰，石阶上浮着一层薄薄的潮气。"
    "走了不多时，地窖的气息扑面而来，光线也跟着暗了一截。"
    "你放慢脚步站定，打量着这里的环境，墙角堆着几只落了灰的木箱。"
)


# ---------------------------------------------------------------------------
# 生产组合根（离线）夹具：临时 SQLite + LLM 关闭 + 流式开关可控
# ---------------------------------------------------------------------------
def _dispose_app_engine(app: FastAPI) -> None:
    """释放组合根内 SQLite Engine 文件句柄（与项目其它组合根测试同口径）。"""
    orchestrator = getattr(app.state, "orchestrator", None)
    repo = getattr(orchestrator, "_repo", None)
    engine = getattr(repo, "_engine", None)
    dispose = getattr(engine, "dispose", None)
    if callable(dispose):
        dispose()


@pytest.fixture()
def offline_app(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> Iterator[tuple[TestClient, FastAPI]]:
    """生产组合根（``main._build_app``）离线装配：临时库、LLM 显式关闭、流式默认开。"""
    monkeypatch.setenv("TEXTGAME_DB_URL", f"sqlite:///{(tmp_path / 'stream-accept.db').as_posix()}")
    monkeypatch.setenv("TEXTGAME_LLM_ENABLED", "false")
    monkeypatch.setenv("TEXTGAME_STREAM_ENABLED", "true")
    monkeypatch.setenv("TEXTGAME_FREE_CRAFT_ENABLED", "false")
    monkeypatch.setenv("TEXTGAME_FREE_CRAFT_SETTINGS_FILE", str(tmp_path / "no-craft.json"))
    monkeypatch.setenv("TEXTGAME_LLM_CREDENTIALS_FILE", str(tmp_path / "no-credentials.json"))
    get_settings.cache_clear()
    import textgame.main as main_module

    _dispose_app_engine(main_module.app)
    app = main_module._build_app()
    try:
        with TestClient(app) as client:
            yield client, app
    finally:
        _dispose_app_engine(app)
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _create_game(client: TestClient, *, key: str, seed: int = 42) -> tuple[str, int]:
    """``POST /games``（201），返回 ``(game_id, version)``。"""
    resp = client.post("/api/v1/games", headers={"Idempotency-Key": key}, json={"seed": seed})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return str(body["game_id"]), int(body["version"])


def _post_stream(
    client: TestClient,
    *,
    game_id: str,
    text: str,
    expected_version: int,
    key: str,
) -> tuple[int, list[dict[str, Any]], str]:
    """``POST /turns/stream`` 一次性读完，返回 ``(status, events, content-type)``。"""
    resp = client.post(
        _STREAM_URL,
        headers={"Idempotency-Key": key},
        json={"game_id": game_id, "input": text, "expected_version": expected_version},
    )
    events = [json.loads(line) for line in resp.text.splitlines() if line]
    return resp.status_code, events, resp.headers.get("content-type", "")


def _post_plain(
    client: TestClient,
    *,
    game_id: str,
    text: str,
    expected_version: int,
    key: str,
) -> tuple[int, dict[str, Any]]:
    """``POST /turns``（对照组）。"""
    resp = client.post(
        _PLAIN_URL,
        headers={"Idempotency-Key": key},
        json={"game_id": game_id, "input": text, "expected_version": expected_version},
    )
    return resp.status_code, resp.json()


def _strip_event_ts(result: dict[str, Any]) -> dict[str, Any]:
    """去掉事件盖章墙钟 ``ts``（同 seed 下两份装配唯一非确定字段），用于等价断言。"""
    normalized = json.loads(json.dumps(result))
    for event in normalized.get("events", []):
        event.pop("ts", None)
    return normalized


def _game_version(client: TestClient, game_id: str) -> int:
    """``GET /games/{id}`` 的当前版本。"""
    resp = client.get(f"/api/v1/games/{game_id}")
    assert resp.status_code == 200, resp.text
    return int(resp.json()["version"])


def _wait_game_version(
    client: TestClient, game_id: str, version: int, *, timeout_s: float = 8.0
) -> int:
    """轮询等待 HTTP 视图版本推进（断连后后台提交，D-N2-5）。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        current = _game_version(client, game_id)
        if current == version:
            return current
        time.sleep(0.01)
    return _game_version(client, game_id)


def _replay_events(client: TestClient, game_id: str) -> list[dict[str, Any]]:
    """``GET /games/{id}/replay`` 的事件列表（只读）。"""
    resp = client.get(f"/api/v1/games/{game_id}/replay", params={"from_seq": 1, "limit": 100})
    assert resp.status_code == 200, resp.text
    return list(resp.json()["events"])


# ---------------------------------------------------------------------------
# 1) 离线回退：单条完整 delta + done，done.result 与 /turns 等价
# ---------------------------------------------------------------------------
def test_offline_stream_single_delta_and_done(offline_app: tuple[TestClient, FastAPI]) -> None:
    """离线生产装配：start → 单条完整 delta → done；template 叙事；回合照常提交。"""
    client, _app = offline_app
    game_id, _version = _create_game(client, key="accept-offline-game-0001")

    status, events, content_type = _post_stream(
        client, game_id=game_id, text="拿起火把", expected_version=1, key="accept-offline-turn-01"
    )

    assert status == 200
    assert content_type.split(";")[0].strip() == _NDJSON
    assert [event["type"] for event in events] == ["start", "delta", "done"]
    delta_text = events[1]["text"]
    done = events[2]["result"]
    assert done["ok"] is True
    assert done["version"] == 2
    assert done["turn_id"] == 2
    assert done["narrative_source"] == "template"
    assert 80 <= len(done["text"]) <= 150
    assert delta_text == done["text"]
    assert [event["event_type"] for event in done["events"]] == ["ITEM_TAKEN"]
    assert done["events"][0]["result"]["narrative"] == done["text"]
    assert done["events"][0]["result"]["narrative_source"] == "template"
    # 回合已提交（HTTP 视图与回放双重证据；回放 event_type 的 game_created 为小写枚举值）
    assert _game_version(client, game_id) == 2
    replay = _replay_events(client, game_id)
    assert [event["event_type"] for event in replay] == ["game_created", "ITEM_TAKEN"]


def test_offline_done_result_equals_plain_turn(offline_app: tuple[TestClient, FastAPI]) -> None:
    """同 seed 两局：stream 的 done.result 与 /turns 的 TurnResult 等价（除事件 ts 外逐字段）。"""
    client, _app = offline_app
    stream_game, _ = _create_game(client, key="accept-eq-game-stream")
    plain_game, _ = _create_game(client, key="accept-eq-game-plain")

    stream_status, events, _ = _post_stream(
        client,
        game_id=stream_game,
        text="拿起火把",
        expected_version=1,
        key="accept-eq-turn-stream",
    )
    plain_status, plain_result = _post_plain(
        client,
        game_id=plain_game,
        text="拿起火把",
        expected_version=1,
        key="accept-eq-turn-plain",
    )

    assert stream_status == plain_status == 200
    assert events[-1]["type"] == "done"
    streamed = _strip_event_ts(events[-1]["result"])
    plain = _strip_event_ts(plain_result)
    assert streamed == plain
    assert streamed["narrative_source"] == plain["narrative_source"] == "template"
    assert streamed["ok"] is True and streamed["version"] == 2


# ---------------------------------------------------------------------------
# 2) 开关回滚：404 + 零写入，旧 /turns 正常，重新启用可逆
# ---------------------------------------------------------------------------
def test_stream_disabled_404_plain_turns_ok_then_recoverable(
    offline_app: tuple[TestClient, FastAPI], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``TEXTGAME_STREAM_ENABLED=false``：流式 404（零写入）、旧 /turns 正常；恢复后流式可用。"""
    client, _app = offline_app
    game_id, _ = _create_game(client, key="accept-rollback-game-0001")
    assert _game_version(client, game_id) == 1

    monkeypatch.setenv("TEXTGAME_STREAM_ENABLED", "false")
    get_settings.cache_clear()
    try:
        resp = client.post(
            _STREAM_URL,
            headers={"Idempotency-Key": "accept-rollback-turn-01"},
            json={"game_id": game_id, "input": "拿起火把", "expected_version": 1},
        )
        assert resp.status_code == 404
        body = resp.json()
        assert body["ok"] is False
        assert body["error_code"] == "NOT_FOUND"
        # 回滚证据 ①：流式 404 零写入（版本仍 1）
        assert _game_version(client, game_id) == 1
        # 回滚证据 ②：旧 /turns 行为不变（正常提交）
        plain_status, plain = _post_plain(
            client,
            game_id=game_id,
            text="拿起火把",
            expected_version=1,
            key="accept-rollback-plain-01",
        )
        assert plain_status == 200 and plain["ok"] is True and plain["version"] == 2
    finally:
        monkeypatch.setenv("TEXTGAME_STREAM_ENABLED", "true")
        get_settings.cache_clear()

    # 回滚可逆：重新启用后流式端点恢复（新一局、新回合）
    recover_game, _ = _create_game(client, key="accept-rollback-recover-game")
    status, events, _ = _post_stream(
        client,
        game_id=recover_game,
        text="环顾四周",
        expected_version=1,
        key="accept-rollback-recover-turn",
    )
    assert status == 200
    assert [event["type"] for event in events] == ["start", "delta", "done"]


# ---------------------------------------------------------------------------
# 3) 断连不撤销（D-N2-5）
# ---------------------------------------------------------------------------
def test_http_disconnect_after_start_still_commits(offline_app: tuple[TestClient, FastAPI]) -> None:
    """HTTP 层：读到 ``start`` 即关闭流 → 回合仍提交，事件 narrative 完整可回放。"""
    client, _app = offline_app
    game_id, _ = _create_game(client, key="accept-disconnect-game-01")

    with client.stream(
        "POST",
        _STREAM_URL,
        headers={"Idempotency-Key": "accept-disconnect-turn-01"},
        json={"game_id": game_id, "input": "拿起火把", "expected_version": 1},
    ) as response:
        assert response.status_code == 200
        first = json.loads(next(response.iter_lines()))
        assert first["type"] == "start"
        # 退出 with 即客户端断连（只停止消费事件，不取消服务端已开始的回合）
    assert _wait_game_version(client, game_id, 2) == 2
    replay = _replay_events(client, game_id)
    taken = [event for event in replay if event["event_type"] == "ITEM_TAKEN"]
    assert len(taken) == 1
    assert 80 <= len(taken[0]["result"]["narrative"]) <= 150
    assert taken[0]["result"]["narrative_source"] == "template"


class _GatedStreamLLM:
    """脚本化流式 LLM 叙事客户端：下发前 gate 阻塞（证明提交发生在断连之后）。"""

    def __init__(self) -> None:
        self.gate = threading.Event()
        #: 已进入流式调用（worker 线程已到达 gate）的显式屏障：P1-4 修复——不用固定 sleep
        #: 猜 worker 调度，避免慢机上「断连时版本断言」偶发时序波动。
        self.entered = threading.Event()
        self.stream_calls = 0

    def complete_json(self, req: LLMRequest) -> Result:
        """非流式修复请求：本用例不配置修复内容。"""
        return Result.fail(ErrorCode.LLM_UNAVAILABLE, "scripted: no repair")

    def complete_json_stream(self, req: LLMRequest, on_delta: Any) -> Result:
        """下发前阻塞在 gate；释放后一次性下发完整 JSON，返回校验后的内容。"""
        self.stream_calls += 1
        self.entered.set()
        assert self.gate.wait(timeout=10.0), "断连测试 gate 未被释放（可能死锁）"
        content = json.dumps({"narrative": _NARRATIVE}, ensure_ascii=False)
        on_delta(content)
        return Result.success(data={"content": content, "repaired": False, "usage": {}})


def test_disconnect_before_first_delta_commits_only_after_release() -> None:
    """因果证明：断连时 gate 未释放（版本仍 1），释放后回合才提交（版本 2、叙事=LLM 文本）。

    用路由生成器 ``_stream_generator`` 的 ``close()`` 模拟断连（与 038 / ADR D-N2-5 验收
    口径一致：只停止消费事件、不取消工作线程）；生产 HTTP 层的断连由上一个用例覆盖。
    """
    repo = InMemoryRepository()
    engine = GameEngine(repo)
    query = QueryService(repo)
    action = ActionService(query)
    llm = _GatedStreamLLM()
    orchestrator = TurnOrchestrator(
        repo=repo,
        engine=engine,
        query=query,
        action=action,
        parser=DeterministicIntentParser(),
        gate=DirectGate(action, query),
        narrator=LLMNarratorAgent(llm, fallback=TemplateNarrator()),
    )
    created = orchestrator.create_game(
        CreateGameRequest(seed=42, idempotency_key="gate-game-key-01")
    )
    request = TurnRequest(
        game_id=created.game_id,
        text="拿起火把",
        expected_version=1,
        idempotency_key="gate-turn-key-01",
    )
    generator = _stream_generator(orchestrator, request, expected_version=1)
    first = json.loads(next(generator))
    assert first["type"] == "start"
    generator.close()  # 客户端断连（只停止消费事件）
    # 断连已发生，但 worker 线程已进入流式调用、仍被 gate 阻塞 → 回合尚未提交（因果证据）。
    # 用 entered 屏障替代固定 sleep：不依赖 worker 调度时序，慢机也不会误报（P1-4）。
    assert llm.entered.wait(timeout=8.0), "worker 未进入流式调用（断连测试前置条件不成立）"
    record = repo.load(created.game_id)
    assert record is not None and record.version == 1
    llm.gate.set()
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        record = repo.load(created.game_id)
        if record is not None and record.version == 2:
            break
        time.sleep(0.01)
    record = repo.load(created.game_id)
    events = repo.read_events(created.game_id)
    assert llm.stream_calls == 1
    assert record is not None and record.version == 2
    taken = [
        event
        for event in events
        if event.event_type is not None and event.event_type.value == "ITEM_TAKEN"
    ]
    assert len(taken) == 1
    assert taken[0].result["narrative"] == _NARRATIVE
    assert taken[0].result["narrative_source"] == "llm"
