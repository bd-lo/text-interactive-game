"""M2-008 整合阶段集成测试：SQLite 仓储接入组合根后的重启恢复。

覆盖：``main._build_app`` 按 ``Settings.db_url``（``TEXTGAME_DB_URL``）真实装配
``SqliteRepository``；tmp 文件库在应用启动时自动建库建表；``POST /games`` + 多回合
（只读/拿取/移动）经 HTTP 全链路写库；TestClient 关闭后重新装配同一 DB 文件的应用
（模拟重启）→ ``GET /games`` 视图不变、``state_hash`` 一致、事件 seq 跨重启连续、
用最新 version 继续回合并 version+1、过期 version → CONFLICT；幂等键跨重启回放
（同键同参零写入、同键异参 CONFLICT）；以及 ``prepare_db`` 的父目录创建/内存库/
非 sqlite 拒绝。

约束：只写 ``tmp_path`` 下的 DB 文件，绝不写仓库 ``data/``；import ``textgame.main``
必须发生在 ``TEXTGAME_DB_URL`` 指向 tmp 库之后（模块 import 时会装配一次模块级 app）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, inspect

from textgame.config.settings import get_settings
from textgame.domain.hashing import state_hash
from textgame.persistence.database import create_engine_and_session, init_schema, prepare_db
from textgame.persistence.sqlite_repository import SqliteRepository

_GAME_KEY = "boot-game-key-0001"


# ---------------------------------------------------------------------------
# 应用装配辅助（env 覆盖 → 组合根 → TestClient）
# ---------------------------------------------------------------------------
def _dispose_app_engine(app: FastAPI) -> None:
    """测试收尾：释放组合根内 SQLite Engine 的文件句柄（app 未暴露 shutdown 钩子）。"""
    orchestrator = getattr(app.state, "orchestrator", None)
    repo = getattr(orchestrator, "_repo", None)
    engine = getattr(repo, "_engine", None)
    if engine is not None:
        engine.dispose()


def _build_app_with_db(monkeypatch: pytest.MonkeyPatch, db_path: Path) -> tuple[FastAPI, str]:
    """把 ``TEXTGAME_DB_URL`` 指向 tmp 文件库后调用组合根 ``_build_app``。

    ``import textgame.main`` 放在设置环境变量之后：该模块 import 时会用当前
    ``Settings`` 装配一次模块级 ``app``（本测试用不到，立刻释放其 Engine 句柄）。
    """
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


def _create_game(client: TestClient, *, key: str = _GAME_KEY) -> dict[str, Any]:
    """``POST /api/v1/games``（201 断言），返回响应体。"""
    resp = client.post("/api/v1/games", headers={"Idempotency-Key": key}, json={"seed": 7})
    assert resp.status_code == 201, resp.text
    body: dict[str, Any] = resp.json()
    return body


def _post_turn(
    client: TestClient,
    *,
    game_id: str,
    text: str,
    expected_version: int,
    key: str,
) -> dict[str, Any]:
    """``POST /api/v1/turns``（HTTP 200 断言，业务失败也在 200 信封内），返回响应体。"""
    resp = client.post(
        "/api/v1/turns",
        headers={"Idempotency-Key": key},
        json={"game_id": game_id, "input": text, "expected_version": expected_version},
    )
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


def _get_game(client: TestClient, game_id: str) -> dict[str, Any]:
    """``GET /api/v1/games/{game_id}``（200 断言），返回对局视图。"""
    resp = client.get(f"/api/v1/games/{game_id}")
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


# ---------------------------------------------------------------------------
# prepare_db：目录创建 / 内存库 / 非 sqlite 拒绝
# ---------------------------------------------------------------------------
def test_prepare_db_creates_parent_directory_and_schema(tmp_path: Path) -> None:
    """文件库 URL 自动创建父目录（默认 data/ 同理），init_schema 建 4 张表。"""
    db_path = tmp_path / "var" / "m2" / "app.db"
    assert not db_path.exists()

    engine, _factory = prepare_db(f"sqlite:///{db_path.as_posix()}")
    try:
        init_schema(engine)
        assert db_path.exists()
        tables = set(inspect(engine).get_table_names())
        assert {"events", "snapshots", "game_state_current", "idempotency"} <= tables
    finally:
        engine.dispose()


def test_prepare_db_memory_sqlite_and_non_sqlite_rejection() -> None:
    """内存库可用；M2 只支持 SQLite，其它后端直接 NotImplementedError。"""
    engine, _factory = prepare_db("sqlite:///:memory:")
    try:
        init_schema(engine)
        assert "events" in set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    with pytest.raises(NotImplementedError):
        prepare_db("postgresql+psycopg://localhost/textgame")


# ---------------------------------------------------------------------------
# 组合根真实装配：自动建库建表 + HTTP 全链路写库
# ---------------------------------------------------------------------------
def test_build_app_auto_creates_db_and_plays_turns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db_path = tmp_path / "var" / "m2app" / "app.db"
    assert not db_path.exists()
    app, db_url = _build_app_with_db(monkeypatch, db_path)
    try:
        assert db_path.exists()  # _build_app → prepare_db + init_schema 自动建库建表
        with TestClient(app) as client:
            created = _create_game(client)
            game_id = created["game_id"]
            assert created["turn"] == 1
            assert created["version"] == 1

            look = _post_turn(
                client,
                game_id=game_id,
                text="环顾四周",
                expected_version=1,
                key="boot-look-0001",
            )
            assert look["ok"] is True
            assert look["version"] == 1  # 只读回合不涨版本
            assert look["events"] == []

            take = _post_turn(
                client,
                game_id=game_id,
                text="拿起火把",
                expected_version=1,
                key="boot-take-torch-01",
            )
            assert take["ok"] is True
            assert take["version"] == 2
            assert take["turn_id"] == 2

            move = _post_turn(
                client,
                game_id=game_id,
                text="向北走进地窖",
                expected_version=2,
                key="boot-move-north-01",
            )
            assert move["ok"] is True
            assert move["version"] == 3
            assert move["turn_id"] == 3

            view = _get_game(client, game_id)
            assert view["game_id"] == game_id
            assert view["turn"] == 3
            assert view["version"] == 3

        engine, repo = _repo_for(db_url)
        try:
            record = repo.load(game_id)
            assert record is not None
            assert record.version == 3
            assert record.last_seq == 3
            events = repo.read_events(game_id)
            assert [event.seq for event in events] == [1, 2, 3]
            assert [event.id for event in events] == [f"evt_{n:06d}" for n in range(1, 4)]
        finally:
            engine.dispose()
    finally:
        _dispose_app_engine(app)


# ---------------------------------------------------------------------------
# 重启恢复：同一 DB 重新装配 app → 视图/状态哈希一致 + 续写 + 过期版本 CONFLICT
# ---------------------------------------------------------------------------
def test_restart_restores_view_state_hash_and_next_commit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db_path = tmp_path / "restart.db"
    app_before, db_url = _build_app_with_db(monkeypatch, db_path)
    try:
        with TestClient(app_before) as client:
            game_id = _create_game(client)["game_id"]
            assert (
                _post_turn(
                    client,
                    game_id=game_id,
                    text="拿起火把",
                    expected_version=1,
                    key="boot-restart-take-torch",
                )["version"]
                == 2
            )
            assert (
                _post_turn(
                    client,
                    game_id=game_id,
                    text="向北走进地窖",
                    expected_version=2,
                    key="boot-restart-move-north",
                )["version"]
                == 3
            )
            view_before = _get_game(client, game_id)
    finally:
        _dispose_app_engine(app_before)  # 关闭第一个 app（模拟进程退出）

    engine_before, repo_before = _repo_for(db_url)
    try:
        record_before = repo_before.load(game_id)
        assert record_before is not None
        assert (record_before.version, record_before.last_seq) == (3, 3)
        hash_before = state_hash(record_before.state)
        events_before = repo_before.read_events(game_id)
    finally:
        engine_before.dispose()

    app_after, _ = _build_app_with_db(monkeypatch, db_path)  # 同一 DB 文件重新装配
    engine_after, repo_after = _repo_for(db_url)
    try:
        record_after = repo_after.load(game_id)
        assert record_after is not None
        assert record_after.version == record_before.version
        assert record_after.last_seq == record_before.last_seq
        assert state_hash(record_after.state) == hash_before  # 重启不改状态

        with TestClient(app_after) as client:
            view_after = _get_game(client, game_id)
            assert view_after == view_before  # turn/version/snapshot 视图一致

            stale = _post_turn(
                client,
                game_id=game_id,
                text="环顾四周",
                expected_version=view_after["version"] - 2,  # 过期版本
                key="boot-stale-version-1",
            )
            assert stale["ok"] is False
            assert stale["error_code"] == "CONFLICT"
            assert stale["version"] == view_after["version"]

            next_turn = _post_turn(
                client,
                game_id=game_id,
                text="拿起锈剑",
                expected_version=view_after["version"],  # 最新版本：续写成功
                key="boot-restart-take-sword",
            )
            assert next_turn["ok"] is True
            assert next_turn["version"] == view_after["version"] + 1

        final_record = repo_after.load(game_id)
        assert final_record is not None
        assert final_record.version == 4
        assert final_record.last_seq == 4
        events_after = repo_after.read_events(game_id)
        assert [event.seq for event in events_after] == [1, 2, 3, 4]  # 跨重启连续
        assert events_after[: len(events_before)] == events_before  # 历史事件不被改写
    finally:
        engine_after.dispose()
        _dispose_app_engine(app_after)


# ---------------------------------------------------------------------------
# 幂等键跨重启：同键同参零写入回放 / 同键异参 CONFLICT
# ---------------------------------------------------------------------------
def test_idempotency_replay_across_restart(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "idempotency.db"
    app_before, db_url = _build_app_with_db(monkeypatch, db_path)
    try:
        with TestClient(app_before) as client:
            game_id = _create_game(client, key="boot-idem-game-01")["game_id"]
            first = _post_turn(
                client,
                game_id=game_id,
                text="拿起火把",
                expected_version=1,
                key="boot-idem-turn-01",
            )
            assert first["ok"] is True
            assert first["version"] == 2
    finally:
        _dispose_app_engine(app_before)

    app_after, _ = _build_app_with_db(monkeypatch, db_path)
    engine, repo = _repo_for(db_url)
    try:
        events_before = repo.read_events(game_id)
        assert len(events_before) == 2  # game_created + 拿火把
        with TestClient(app_after) as client:
            replay = _post_turn(
                client,
                game_id=game_id,
                text="拿起火把",
                expected_version=1,
                key="boot-idem-turn-01",
            )
            assert replay == first  # 同键同参：原样回放首次响应（含盖章事件）
            conflict = _post_turn(
                client,
                game_id=game_id,
                text="拿起治疗药水",  # 同键异参
                expected_version=1,
                key="boot-idem-turn-01",
            )
            assert conflict["ok"] is False
            assert conflict["error_code"] == "CONFLICT"
        events_after = repo.read_events(game_id)
        assert events_after == events_before  # 回放与冲突都零写入
    finally:
        engine.dispose()
        _dispose_app_engine(app_after)
