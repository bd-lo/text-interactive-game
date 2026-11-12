"""集成测试：``GET /api/v1/games`` 存档列表（M6 续玩 UI 契约，D9；只读零写入）。

覆盖：
- 空库 → ``{games: [], total: 0}``；
- 创建多个存档后按最近更新时间倒序返回摘要字段集合精确、``total`` 正确；
- 分页 ``limit``/``offset`` 生效，``limit`` 越界 → 422；
- 只读语义：列表请求前后所有存档的 version/events 完全不变（零写入）；
- 未完成 story 存档 ``status=active``、字段 ``scenario=story``（场景识别走房间事实）；
- R-21：最后一条叙事在尾部 200 条之外时 ``last_text`` 仍正确（SQLite 有界查询）；
- R-16：``/view.lore`` 与「下一回合注入」使用同一 turn 口径（``state.turn + 1``）。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from textgame.domain.events import Event, EventLog, EventType
from textgame.domain.scenario import PLAYER_ID
from textgame.domain.world_book import activate_entries as real_activate_entries
from textgame.domain.world_book import parse_import_payload
from textgame.orchestration.narrator import TemplateNarrator
from textgame.persistence.database import create_engine_and_session, init_schema
from textgame.persistence.sqlite_repository import SqliteRepository
from textgame.services.game_engine import GameEngine
from textgame.services.ports import CommitPayload
from textgame.services.query_service import QueryService

_LIST_KEYS = {"games", "total"}
_ITEM_KEYS = {
    "game_id",
    "turn",
    "version",
    "snapshot_version",
    "status",
    "scenario",
    "last_text",
    "updated_at",
}


def _create(client: TestClient, key: str, body: dict | None = None) -> dict:
    """创建存档并断言 201，返回响应体。"""
    resp = client.post(
        "/api/v1/games", headers={"Idempotency-Key": key}, json=body if body is not None else {}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_game_list_empty_returns_zero(game_harness) -> None:
    """空库：200 + 空列表 + total=0。"""
    resp = game_harness.client.get("/api/v1/games")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == _LIST_KEYS
    assert body == {"games": [], "total": 0}


def test_game_list_returns_summaries_newest_first(game_harness) -> None:
    """两个存档：摘要字段集合精确、按最近更新时间倒序、total 正确。"""
    client = game_harness.client
    first = _create(client, "list-key-0001", {"seed": 42, "scenario": "m2"})
    second = _create(client, "list-key-0002", {"seed": 7, "scenario": "story"})

    body = client.get("/api/v1/games").json()
    assert body["total"] == 2
    assert [item["game_id"] for item in body["games"]] == [second["game_id"], first["game_id"]]
    for item in body["games"]:
        assert set(item) == _ITEM_KEYS
    by_id = {item["game_id"]: item for item in body["games"]}
    assert by_id[first["game_id"]]["scenario"] == "m2"
    assert by_id[second["game_id"]]["scenario"] == "story"
    assert by_id[second["game_id"]]["status"] == "active"


def test_game_list_pagination(game_harness) -> None:
    """``limit``/``offset`` 分页：total 恒为总数、页大小与偏移正确。"""
    client = game_harness.client
    for index in range(3):
        _create(client, f"list-page-key-{index:04d}", {"seed": index})

    page = client.get("/api/v1/games", params={"limit": 2, "offset": 0}).json()
    assert page["total"] == 3 and len(page["games"]) == 2
    page2 = client.get("/api/v1/games", params={"limit": 2, "offset": 2}).json()
    assert page2["total"] == 3 and len(page2["games"]) == 1
    assert page["games"][0]["game_id"] != page2["games"][0]["game_id"]

    bad = client.get("/api/v1/games", params={"limit": 51})
    assert bad.status_code == 422
    assert bad.json()["ok"] is False
    assert bad.json()["error_code"] == "INVALID_INPUT"

    bad_offset = client.get("/api/v1/games", params={"offset": -1})
    assert bad_offset.status_code == 422


def test_game_list_is_read_only(game_harness) -> None:
    """列表请求零写入：前后所有存档 version/事件序列完全一致。"""
    client = game_harness.client
    created = _create(client, "list-ro-key-0001", {"seed": 42})
    game_id = created["game_id"]
    before = game_harness.repo.load(game_id)
    assert before is not None
    events_before = [event.id for event in game_harness.repo.read_events(game_id)]

    assert client.get("/api/v1/games").status_code == 200

    after = game_harness.repo.load(game_id)
    assert after is not None
    assert (after.version, after.state) == (before.version, before.state)
    assert [event.id for event in game_harness.repo.read_events(game_id)] == events_before


def test_game_list_last_text_uses_latest_narrative(game_harness) -> None:
    """列表项 ``last_text`` 取最近一条带 narrative 的事件文本（与 GET /games/{id} 同口径）。"""
    client = game_harness.client
    game_id = _create(client, "list-text-key-0001", {"seed": 42})["game_id"]
    turn = client.post(
        "/api/v1/turns",
        headers={"Idempotency-Key": "list-text-turn-0001"},
        json={"game_id": game_id, "input": "拿起火把", "expected_version": 1},
    )
    assert turn.status_code == 200 and turn.json()["ok"] is True

    item = client.get("/api/v1/games").json()["games"][0]
    refresh = client.get(f"/api/v1/games/{game_id}").json()
    assert item["last_text"] is not None
    assert item["last_text"] == refresh["last_text"]
    assert item["status"] == refresh["status"] == "active"


def test_read_recent_events_returns_tail_in_seq_order(game_harness) -> None:
    """R-21：列表页只读尾部事件；``read_recent_events`` 返回升序的最后 N 条。"""
    client = game_harness.client
    game_id = _create(client, "list-recent-key-0001", {"seed": 42})["game_id"]
    turn = client.post(
        "/api/v1/turns",
        headers={"Idempotency-Key": "list-recent-turn-0001"},
        json={"game_id": game_id, "input": "拿起火把", "expected_version": 1},
    )
    assert turn.status_code == 200 and turn.json()["ok"] is True

    all_events = game_harness.repo.read_events(game_id)
    recent = game_harness.repo.read_recent_events(game_id, limit=2)

    assert [event.seq for event in recent] == [event.seq for event in all_events[-2:]]
    assert len(recent) == 2 and recent[0].seq < recent[1].seq


# ---------------------------------------------------------------------------
# R-21：最后一条叙事在尾部 200 条窗口之外（SQLite 有界查询）
# ---------------------------------------------------------------------------
_R21_NARRATIVE = "R-21：窗口之外的最后一条叙事仍应出现在列表摘要里。"
_R21_EVENTS = 250


def _sqlite_repo_with_narrative_outside_tail() -> tuple[
    Engine, SqliteRepository, QueryService, str
]:
    """建 SQLite 仓储：``game_created`` 之后追加 250 条事件，叙事只在 seq=2。"""
    engine, _factory = create_engine_and_session("sqlite:///:memory:")
    init_schema(engine)
    repo = SqliteRepository(engine)
    game_engine = GameEngine(repo)
    game_id = "game_r21_tail_0001"
    assert game_engine.create_game(save_seed="save-r21", game_id=game_id).ok
    record = repo.load(game_id)
    assert record is not None
    drafts = [
        Event(
            turn_id=2 + index,
            actor_id=PLAYER_ID,
            method="narrative_probe",
            event_type=EventType.DIALOGUE_EXCHANGED,
            params={},
            result={"narrative": _R21_NARRATIVE} if index == 0 else {"note": "no narrative"},
        )
        for index in range(_R21_EVENTS)
    ]
    stamped = EventLog.resume("save-r21", last_seq=record.last_seq).stamp(drafts)
    payload = CommitPayload(
        game_id=game_id,
        expected_version=record.version,
        new_version=record.version + 1,
        last_seq=stamped[-1].seq or 0,
        new_state=record.state,
        stamped_events=tuple(stamped),
    )
    assert repo.commit(payload) is True
    return engine, repo, QueryService(repo), game_id


def test_game_list_last_text_beyond_tail_window() -> None:
    """R-21：>200 条事件且最后一条叙事在尾部之外时，列表 ``last_text`` 仍取到它。

    旧实现只扫尾部 200 条（seq 52..251 无叙事）→ ``last_text=None``；新实现走
    ``read_last_narrative_event`` 单条有界 SQL（``ORDER BY seq DESC LIMIT 1``）→ 正确。
    """
    engine, repo, query, game_id = _sqlite_repo_with_narrative_outside_tail()
    try:
        # 构造证据：尾部 200 条事件确实不含叙事（旧实现必然拿到 None）。
        tail = repo.read_recent_events(game_id, limit=200)
        assert len(tail) == 200
        assert all("narrative" not in event.result for event in tail)

        item = query.list_games(limit=10)["games"][0]
        assert item["last_text"] == _R21_NARRATIVE
        # 与全量扫描的 GET /games/{id} 同口径（此处直接读 QueryService）。
        assert query.get_game_view(game_id)["last_text"] == _R21_NARRATIVE
        # 有界查询命中的正是 seq=2 那条叙事事件。
        last_narrative = repo.read_last_narrative_event(game_id)
        assert last_narrative is not None
        assert last_narrative.seq == 2
        assert last_narrative.result["narrative"] == _R21_NARRATIVE
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# R-16：``/view.lore`` 与下一回合注入的 turn 口径一致
# ---------------------------------------------------------------------------
_R16_BOOK_ID = "wb_r16_turn_scope"
_R16_LORE = "哨站的信号灯已经灭了两夜，风里一直带着雪。"


class _R16Provider:
    """最小世界书提供者（``WorldBookProvider`` 同签名）。"""

    def __init__(self, book) -> None:
        """保存唯一一本测试世界书。"""
        self._book = book

    def get(self, world_book_id: str):
        """只认测试世界书 ID；其它返回 ``None``。"""
        return self._book if world_book_id == _R16_BOOK_ID else None


def test_view_lore_turn_matches_next_turn_injection(game_harness, monkeypatch) -> None:
    """R-16：``/view.lore`` 与下一回合注入用同一个 ``turn_id``（均为 ``state.turn + 1``）。

    记录两处 ``activate_entries`` 调用的 ``turn_id``：``/view`` 读到的激活条目必须与
    下一回合实际注入使用同一回合口径（R-16 修正的 off-by-one 回归护栏）。
    """
    book = parse_import_payload(
        {
            "name": "R-16 测试书",
            "entries": [{"uid": 0, "constant": True, "content": _R16_LORE}],
        }
    )
    provider = _R16Provider(book)
    monkeypatch.setattr(game_harness.query, "_world_book_provider", provider)
    monkeypatch.setattr(game_harness.orchestrator, "_world_book_provider", provider)
    # 默认装配无叙事器（``_fallback_text`` 不拼接世界设定句）：注入模板叙事器，
    # 以便断言「同一批激活条目确实进入了下一回合注入的叙事」。
    monkeypatch.setattr(game_harness.orchestrator, "_narrator", TemplateNarrator())
    captured: list[int] = []

    def _spy(book_arg, **kwargs):
        captured.append(int(kwargs["turn_id"]))
        return real_activate_entries(book_arg, **kwargs)

    monkeypatch.setattr("textgame.services.query_service.activate_entries", _spy)
    monkeypatch.setattr("textgame.orchestration.turn_orchestrator.activate_entries", _spy)

    client = game_harness.client
    created = _create(
        client,
        "r16-game-key-0001",
        {"seed": 42, "scenario": "story", "world_book_id": _R16_BOOK_ID},
    )
    game_id = created["game_id"]
    version = int(created["version"])
    turn = client.post(
        "/api/v1/turns",
        headers={"Idempotency-Key": "r16-turn-key-0001"},
        json={"game_id": game_id, "input": "向东走", "expected_version": version},
    )
    assert turn.status_code == 200 and turn.json()["ok"] is True
    version = int(turn.json()["version"])
    assert version == 2  # 写事件回合：state.turn 推进到 2

    captured.clear()
    view = client.get(f"/api/v1/games/{game_id}/view").json()
    assert view["lore"] is not None and view["lore"]["active_entries"]
    assert captured == [int(view["turn"]) + 1], (captured, view["turn"])

    captured.clear()
    next_turn = client.post(
        "/api/v1/turns",
        headers={"Idempotency-Key": "r16-turn-key-0002"},
        json={"game_id": game_id, "input": "环顾四周", "expected_version": version},
    )
    body = next_turn.json()
    assert body["ok"] is True
    assert captured == [int(view["turn"]) + 1], (captured, view["turn"])
    assert "信号灯" in body["text"]  # 同一批激活条目确实注入了叙事
