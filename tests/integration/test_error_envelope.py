"""M2 出口审计 P2 修复 + M4 多存档行为的回归测试。

覆盖两处 API 边界行为：

1. 损坏存档（仓储/投影抛 ``ValueError``）经 HTTP 返回 500 ``INTERNAL`` 失败信封，
   而不是 text/plain 500（第 6 章 §6.7 统一失败响应、第 15 章不向客户端暴露内部细节）；
2. M4 前 D-008-3 落地后「同库多存档」：同一 SQLite 库可以创建多个不同 ``game_id`` 的
   存档（均 201），各自的 ``events.seq`` 从 1 起、状态/视图/审计相互隔离；
   仅重复同一个 ``game_id`` 才 CONFLICT。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from textgame.config.settings import get_settings
from textgame.domain.scenario import ITEM_TORCH, PLAYER_ID, ROOM_CELLAR, ROOM_ENTRY
from textgame.persistence.database import create_engine_and_session
from textgame.persistence.sqlite_repository import SqliteRepository

pytestmark = [pytest.mark.integration]

#: 500/409 信封不得出现的实现细节（第 15 章不向客户端暴露实现信息）。
_INTERNAL_ENVELOPE_TERMS = (
    "traceback",
    "sqlalchemy",
    "sqlite",
    "select ",
    "params_hash",
    "insert into",
)
#: 内部实体标识（ULID = 26 位 Crockford Base32）；玩家可见文案不得泄漏。
_ULID_RE = re.compile(r"(?:room|item|npc|char|game)_[0-9A-HJKMNP-TV-Z]{26}")
#: 协议内部字段名（玩家可见文案不得出现）。
_INTERNAL_ENTITY_MARKERS = re.compile(
    r"(event_type|error_code|turn_id|snapshot_version|room_id|item_id|npc_id)"
)


# ---------------------------------------------------------------------------
# 组合根装配辅助（与 test_sqlite_app_boot 同口径：env 覆盖 → _build_app → TestClient）
# ---------------------------------------------------------------------------
def _dispose_app_engine(app: FastAPI) -> None:
    """测试收尾：释放组合根内 SQLite Engine 的文件句柄（app 未暴露 shutdown 钩子）。"""
    orchestrator = getattr(app.state, "orchestrator", None)
    repo = getattr(orchestrator, "_repo", None)
    engine = getattr(repo, "_engine", None)
    if engine is not None:
        engine.dispose()


def _build_app_with_db(monkeypatch: pytest.MonkeyPatch, db_path: Path) -> tuple[FastAPI, str]:
    """把 ``TEXTGAME_DB_URL`` 指向 tmp 文件库后调用组合根 ``_build_app``。"""
    db_url = f"sqlite:///{db_path.as_posix()}"
    monkeypatch.setenv("TEXTGAME_DB_URL", db_url)
    get_settings.cache_clear()
    import textgame.main as main_module

    _dispose_app_engine(main_module.app)
    return main_module._build_app(), db_url


def _repo_for(db_url: str) -> tuple[Engine, SqliteRepository]:
    """独立于 app 的仓储实例（同一 DB 文件，用于断言落库内容）。"""
    engine, _factory = create_engine_and_session(db_url)
    return engine, SqliteRepository(engine)


def _create_game(client: TestClient, *, key: str) -> dict:
    """``POST /api/v1/games``（201 断言），返回响应体。"""
    response = client.post("/api/v1/games", headers={"Idempotency-Key": key}, json={"seed": 7})
    assert response.status_code == 201, response.text
    body: dict = response.json()
    return body


def _post_turn(
    client: TestClient, *, game_id: str, text: str, expected_version: int, key: str
) -> dict:
    """``POST /api/v1/turns``（HTTP 200 断言，业务失败也在 200 信封内）。"""
    response = client.post(
        "/api/v1/turns",
        headers={"Idempotency-Key": key},
        json={"game_id": game_id, "input": text, "expected_version": expected_version},
    )
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# 1. 损坏存档 → 500 INTERNAL 信封
# ---------------------------------------------------------------------------
def test_corrupted_archive_maps_to_internal_envelope(game_harness, monkeypatch) -> None:
    """仓储 ``load`` 抛 ValueError（快照哈希不符/损坏存档）→ 500 INTERNAL 信封。"""

    def _corrupted(_game_id: str) -> None:
        raise ValueError("snapshot state_hash mismatch (corrupted archive)")

    monkeypatch.setattr(game_harness.repo, "load", _corrupted)

    response = game_harness.client.get("/api/v1/games/game_corrupted_0001")

    assert response.status_code == 500
    body = response.json()
    assert body["ok"] is False
    assert body["error_code"] == "INTERNAL"
    assert "存档" in body["message"]
    assert "state_hash" not in response.text  # 不回显内部细节


# ---------------------------------------------------------------------------
# 2. 同库多存档（D-008-3 复合主键落地）
# ---------------------------------------------------------------------------
def test_two_games_on_same_database_are_supported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """同一 SQLite 库建两个游戏均 201；事件 seq 各自从 1、状态/视图/审计隔离。"""
    db_path = tmp_path / "multi-save.db"
    app, db_url = _build_app_with_db(monkeypatch, db_path)
    try:
        with TestClient(app) as client:
            first = _create_game(client, key="m4-multi-game-a-0001")
            second = _create_game(client, key="m4-multi-game-b-0001")
            game_a, game_b = first["game_id"], second["game_id"]
            assert game_a != game_b
            assert (first["turn"], first["version"]) == (1, 1)
            assert (second["turn"], second["version"]) == (1, 1)

            turn_a = _post_turn(
                client,
                game_id=game_a,
                text="拿起火把",
                expected_version=1,
                key="m4-multi-a-turn-01",
            )
            turn_b = _post_turn(
                client,
                game_id=game_b,
                text="向北走进地窖",
                expected_version=1,
                key="m4-multi-b-turn-01",
            )
            assert turn_a["ok"] is True
            assert turn_a["version"] == 2
            assert turn_b["ok"] is True
            assert turn_b["version"] == 2

            for game_id in (game_a, game_b):
                view = client.get(f"/api/v1/games/{game_id}")
                assert view.status_code == 200, view.text
                body = view.json()
                assert body["turn"] == 2
                assert body["version"] == 2
                audit = client.get(f"/api/v1/games/{game_id}/audit")
                assert audit.status_code == 200, audit.text
                assert isinstance(audit.json()["records"], list)

        # 独立仓储核对：事件 seq 每存档各从 1 起、状态互不串档
        engine, repo = _repo_for(db_url)
        try:
            events_a = repo.read_events(game_a)
            events_b = repo.read_events(game_b)
            assert [event.seq for event in events_a] == [1, 2]
            assert [event.seq for event in events_b] == [1, 2]
            # Event.id 是每存档编号：两个存档的 game_created 都是 evt_000001
            assert events_a[0].id == events_b[0].id == "evt_000001"
            assert events_a[1].id == events_b[1].id == "evt_000002"

            record_a = repo.load(game_a)
            record_b = repo.load(game_b)
            assert record_a is not None and record_b is not None
            assert (record_a.version, record_a.last_seq) == (2, 2)
            assert (record_b.version, record_b.last_seq) == (2, 2)
            # A 拿火把（仍在入口），B 移动到地窖：状态不串档
            assert record_a.state.characters[PLAYER_ID].inventory == [ITEM_TORCH]
            assert record_a.state.characters[PLAYER_ID].location == ROOM_ENTRY
            assert record_b.state.characters[PLAYER_ID].location == ROOM_CELLAR

            # 审计只增表也按 game_id 隔离
            audit_a = repo.read_audit(game_a)
            audit_b = repo.read_audit(game_b)
            assert all(record.game_id == game_a for record in audit_a)
            assert all(record.game_id == game_b for record in audit_b)
        finally:
            engine.dispose()
    finally:
        _dispose_app_engine(app)


def test_duplicate_game_id_still_conflicts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """多存档只放宽「不同 game_id」；同一 game_id 重复创建仍 CONFLICT（可达路径）。"""
    db_path = tmp_path / "duplicate-game-id.db"
    app, _db_url = _build_app_with_db(monkeypatch, db_path)
    try:
        with TestClient(app) as client:
            game_id = _create_game(client, key="m4-dup-game-key-0001")["game_id"]
            # 引擎层：同一 game_id 的第二次 create_game → CONFLICT（HTTP 层 game_id 由服务端生成）
            orchestrator = app.state.orchestrator
            engine = orchestrator._engine
            duplicate = engine.create_game(save_seed="save-42", game_id=game_id)
            assert duplicate.ok is False
            assert duplicate.error_code is not None
            # 同一 game_id 的存档只有一份事件流
            assert [event.seq for event in orchestrator._repo.read_events(game_id)] == [1]
    finally:
        _dispose_app_engine(app)


# ---------------------------------------------------------------------------
# 3. R2 QA-1：409 冲突信封（同 Idempotency-Key 异参）
# ---------------------------------------------------------------------------
def test_conflict_same_key_different_body_returns_409_envelope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """创建请求同键异参 → 409 CONFLICT 统一 JSON 信封；不泄漏实现细节。"""
    db_path = tmp_path / "conflict-envelope.db"
    app, _db_url = _build_app_with_db(monkeypatch, db_path)
    try:
        with TestClient(app) as client:
            first = client.post(
                "/api/v1/games",
                headers={"Idempotency-Key": "qa-conflict-game-0001"},
                json={"seed": 42},
            )
            assert first.status_code == 201, first.text
            conflict = client.post(
                "/api/v1/games",
                headers={"Idempotency-Key": "qa-conflict-game-0001"},
                json={"seed": 7},
            )
            assert conflict.status_code == 409
            assert conflict.headers["content-type"].startswith("application/json")
            body = conflict.json()
            assert body["ok"] is False
            assert body["error_code"] == "CONFLICT"
            assert isinstance(body["message"], str) and body["message"]
            lowered = conflict.text.lower()
            for term in _INTERNAL_ENVELOPE_TERMS:
                assert term not in lowered, f"409 信封泄漏实现细节: {term!r}"
            # 换新键仍可正常创建（冲突不污染幂等表）
            other = client.post(
                "/api/v1/games",
                headers={"Idempotency-Key": "qa-conflict-game-0002"},
                json={"seed": 7},
            )
            assert other.status_code == 201 and other.json()["save_seed"] == "save-7"
    finally:
        _dispose_app_engine(app)


# ---------------------------------------------------------------------------
# 4. R2 QA-1：500 信封隐藏堆栈/路径/领域内部词（第 15 章）
# ---------------------------------------------------------------------------
def test_internal_error_envelope_hides_stack_paths_and_domain_terms(
    game_harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """损坏存档 → 500 INTERNAL：响应文本不得出现异常类型、文件路径、SQL/哈希内部词。"""
    sensitive = (
        "ValueError: snapshot state_hash mismatch at /home/runner/work/textgame/db.py:42; "
        'Traceback (most recent call last): File "sqlite_repository.py", line 7, SELECT * FROM events'
    )

    def _corrupted(_game_id: str) -> None:
        raise ValueError(sensitive)

    monkeypatch.setattr(game_harness.repo, "load", _corrupted)
    response = game_harness.client.get("/api/v1/games/game_corrupted_0002")

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert body["ok"] is False
    assert body["error_code"] == "INTERNAL"
    assert "服务内部错误" in body["message"]
    lowered = response.text.lower()
    for term in (*_INTERNAL_ENVELOPE_TERMS, "valueerror", "traceback", "state_hash", "select "):
        assert term not in lowered, f"500 信封泄漏实现细节: {term!r}"
    assert "D:\\" not in response.text and "/home/" not in response.text


# ---------------------------------------------------------------------------
# 5. R2 QA-1：玩家可见失败文案不泄漏协议/内部标识
# ---------------------------------------------------------------------------
def test_turn_failure_copy_hides_internal_identifiers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """回合失败文案（text + hints + 建议）是玩家可读中文，不含 json/game_id/ULID 等内部字段。"""
    db_path = tmp_path / "failure-copy.db"
    app, _db_url = _build_app_with_db(monkeypatch, db_path)
    try:
        with TestClient(app) as client:
            game_id = _create_game(client, key="qa-copy-game-0001")["game_id"]
            failure = _post_turn(
                client,
                game_id=game_id,
                text="跳一支舞",
                expected_version=1,
                key="qa-copy-turn-0001",
            )
            assert failure["ok"] is False
            effects = failure.get("effects") or []
            hints = [item.get("hint", "") for item in effects if isinstance(item, dict)]
            visible = "\n".join(
                [failure["text"], *hints, *(failure.get("suggested_actions") or [])]
            )
            # console 的禁词表 = 终端口径（json/game_id/version/idempotency）；玩家文案同口径
            from textgame.console import assert_clean_output

            assert_clean_output(visible)
            assert _ULID_RE.search(visible) is None
            assert _INTERNAL_ENTITY_MARKERS.search(visible) is None
            assert hints and all(isinstance(hint, str) and hint for hint in hints)
            assert 1 <= len(failure.get("suggested_actions") or []) <= 4
    finally:
        _dispose_app_engine(app)
