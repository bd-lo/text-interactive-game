"""M2 出口审计 P2 修复的回归测试。

覆盖两处 API 边界行为：
1. 损坏存档（仓储/投影抛 ``ValueError``）经 HTTP 返回 500 ``INTERNAL`` 失败信封，
   而不是 text/plain 500（第 6 章 §6.7 统一失败响应、第 15 章不向客户端暴露内部细节）；
2. M2「每库单存档」限制（第 14 章 §14.3 单机一存档一库）：同一 SQLite 库第二次
   ``create_game`` 返回 ``CONFLICT`` 且消息可读（PROGRESS §5 D-008-3）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from textgame.domain.results import ErrorCode
from textgame.persistence.database import init_schema, prepare_db
from textgame.persistence.sqlite_repository import SqliteRepository
from textgame.services.game_engine import GameEngine

pytestmark = [pytest.mark.integration]


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


def test_second_game_on_same_database_returns_conflict(tmp_path: Path) -> None:
    """M2 每库单存档：同一 DB 第二次建局 → CONFLICT 且提示为何失败。"""
    db_url = f"sqlite:///{tmp_path.joinpath('single.db').as_posix()}"
    engine, _factory = prepare_db(db_url)
    init_schema(engine)
    repo = SqliteRepository(engine)
    game_engine = GameEngine(repo)

    first = game_engine.create_game(save_seed="save-42")
    assert first.ok is True

    second = game_engine.create_game(save_seed="save-43")

    assert second.ok is False
    assert second.error_code is ErrorCode.CONFLICT
    assert "单存档" in (second.message or "")
    engine.dispose()
