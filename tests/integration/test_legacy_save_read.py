"""R3 AC-33：旧存档兼容（事件载荷无 ``scenario`` + 更老的状态字段）读取回归。

旧档模拟（合成，不伪造业务数据）：
1. ``game_created`` 事件 ``params`` 去掉 ``scenario``（若存在；当前实现本就不写该字段——
   第 21 章 §21.2.8 伪代码含 ``scenario``，实现未落）、``card_id``（D-M6-2 加法）、
   ``snapshot_version``（更老载荷）→ 模拟 M2 时代事件载荷；
2. ``game_state_current``/``snapshots`` 的 ``state_json`` 去掉 ``quests`` 键（更老状态模型
   无该字段），快照 ``state_hash`` 按新状态重算 → 模拟旧状态 JSON，由 pydantic 默认值补齐。

验证：``GET /games/{id}``、``/view``、``/replay`` 均可读、场景由状态事实推断为 m2、可继续
提交回合；console 的 ``latest_saved_game_id``/``saved_scenario`` 同样可用。零网络。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from textgame import console
from textgame.config.settings import get_settings
from textgame.domain.hashing import state_hash
from textgame.domain.models import GameState

pytestmark = [pytest.mark.integration]

#: 旧档模拟时要删除的 ``game_created.params`` 键（scenario 可能本就不存在）。
_LEGACY_PARAM_KEYS = ("scenario", "card_id", "snapshot_version")


def _dispose(app: FastAPI) -> None:
    """释放组合根内 SQLite Engine 的文件句柄（Windows 临时文件收尾）。"""
    console.dispose_app_engine(app)


def _build_app_with_db(monkeypatch: pytest.MonkeyPatch, db_path: Path) -> FastAPI:
    """把 ``TEXTGAME_DB_URL`` 指向临时库后重建生产组合根。"""
    monkeypatch.setenv("TEXTGAME_DB_URL", f"sqlite:///{db_path.as_posix()}")
    get_settings.cache_clear()
    import textgame.main as main_module

    _dispose(main_module.app)
    return main_module._build_app()


def _new_key(prefix: str) -> str:
    """唯一幂等键（每次请求新键，避免回放旧结果）。"""
    return f"{prefix}-{uuid.uuid4().hex}"


def _create_m2_game(client: TestClient) -> tuple[str, int]:
    """创建 m2 存档并走 1 个写回合（让事件表/当前状态有真实内容）。"""
    created = client.post(
        "/api/v1/games",
        headers={"Idempotency-Key": _new_key("legacy-create")},
        json={"seed": 42, "scenario": "m2"},
    )
    assert created.status_code == 201, created.text
    game_id = str(created.json()["game_id"])
    turn = client.post(
        "/api/v1/turns",
        headers={"Idempotency-Key": _new_key("legacy-turn")},
        json={"game_id": game_id, "input": "拿起火把", "expected_version": 1},
    )
    assert turn.status_code == 200 and turn.json()["ok"] is True, turn.text
    return game_id, int(turn.json()["version"])


def _downgrade_to_legacy_save(db_path: Path, game_id: str) -> None:
    """把生产存档改写为「旧格式」：事件载荷去新字段 + 状态 JSON 去 quests（哈希重算）。"""
    connection = sqlite3.connect(str(db_path))
    try:
        row = connection.execute(
            "SELECT params FROM events WHERE game_id = ? AND method = 'game_created'",
            (game_id,),
        ).fetchone()
        assert row is not None, "未找到 game_created 事件"
        params = json.loads(row[0])
        for key in _LEGACY_PARAM_KEYS:
            params.pop(key, None)
        connection.execute(
            "UPDATE events SET params = ? WHERE game_id = ? AND method = 'game_created'",
            (json.dumps(params, ensure_ascii=False), game_id),
        )

        # 旧状态 JSON：去掉 M3 之后才有的 quests 键，由 GameState 默认值补齐。
        for table in ("game_state_current", "snapshots"):
            state_row = connection.execute(
                f"SELECT state_json FROM {table} WHERE game_id = ?",
                (game_id,),
            ).fetchone()
            assert state_row is not None, f"{table} 缺少存档行"
            raw_state = json.loads(state_row[0])
            raw_state.pop("quests", None)
            legacy_state = GameState.model_validate(raw_state)
            if table == "snapshots":
                connection.execute(
                    f"UPDATE {table} SET state_json = ?, state_hash = ? WHERE game_id = ?",
                    (legacy_state.model_dump_json(), state_hash(legacy_state), game_id),
                )
            else:
                connection.execute(
                    f"UPDATE {table} SET state_json = ? WHERE game_id = ?",
                    (legacy_state.model_dump_json(), game_id),
                )
        connection.commit()

        after = connection.execute(
            "SELECT params FROM events WHERE game_id = ? AND method = 'game_created'",
            (game_id,),
        ).fetchone()
        assert after is not None
        legacy_params = json.loads(after[0])
        assert all(key not in legacy_params for key in _LEGACY_PARAM_KEYS)
    finally:
        connection.close()


def test_legacy_save_without_scenario_field_is_readable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """旧档：``/games/{id}``、``/view``、``/replay`` 可读，且可继续推进回合。"""
    db_path = tmp_path / "legacy-read.db"
    app = _build_app_with_db(monkeypatch, db_path)
    try:
        with TestClient(app) as client:
            game_id, version = _create_m2_game(client)
    finally:
        _dispose(app)

    _downgrade_to_legacy_save(db_path, game_id)

    app = _build_app_with_db(monkeypatch, db_path)
    try:
        with TestClient(app) as client:
            snapshot = client.get(f"/api/v1/games/{game_id}")
            assert snapshot.status_code == 200, snapshot.text
            snapshot_body: dict[str, Any] = snapshot.json()

            view = client.get(f"/api/v1/games/{game_id}/view")
            assert view.status_code == 200, view.text
            view_body: dict[str, Any] = view.json()

            replay = client.get(
                f"/api/v1/games/{game_id}/replay", params={"from_seq": 1, "limit": 50}
            )
            assert replay.status_code == 200, replay.text
            events: list[dict[str, Any]] = replay.json()["events"]
            created = next(event for event in events if event["method"] == "game_created")

            listed = client.get("/api/v1/games")
            assert listed.status_code == 200
            rows = listed.json()["games"]

            resumed = client.post(
                "/api/v1/turns",
                headers={"Idempotency-Key": _new_key("legacy-resume")},
                json={
                    "game_id": game_id,
                    "input": "查看背包",
                    "expected_version": snapshot_body["version"],
                },
            )
            assert resumed.status_code == 200 and resumed.json()["ok"] is True
    finally:
        _dispose(app)

    # 场景名是读时从状态事实推断（不依赖事件 scenario 字段）；旧状态缺 quests 也照常可读
    assert snapshot_body["scenario"] == "m2"
    assert snapshot_body["version"] == version
    assert view_body["scenario"] == "m2"
    assert view_body["room"]["title"] == "废弃哨站·入口"
    assert "scenario" not in created["params"]
    assert len(events) >= 2  # game_created + 1 个写回合
    assert len(rows) == 1 and rows[0]["scenario"] == "m2"


def test_legacy_save_console_discovery_and_scenario_inference(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """console 兼容：同库旧档可发现、场景推断 m2、``/games`` 列表入口不崩。"""
    db_path = tmp_path / "legacy-console.db"
    app = _build_app_with_db(monkeypatch, db_path)
    try:
        with TestClient(app) as client:
            game_id, _version = _create_m2_game(client)
    finally:
        _dispose(app)

    _downgrade_to_legacy_save(db_path, game_id)

    db_url = f"sqlite:///{db_path.as_posix()}"
    latest = console.latest_saved_game_id(db_url)
    assert latest == game_id
    assert console.saved_scenario(db_url, game_id) == "m2"

    app = _build_app_with_db(monkeypatch, db_path)
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/games", params={"limit": 10})
            assert response.status_code == 200
            assert response.json()["total"] == 1
    finally:
        _dispose(app)
