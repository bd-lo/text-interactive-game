"""OSS-2 剧情/任务物品保护验收（R1 玩家实测 P0-3 安全子集）。

覆盖：
1. HTTP 端到端：m2 拿起古物后「使用古物」→ HTTP 200 + ``TurnResult(ok=false)``、
   零事件、版本不变、古物仍在背包（不再被永久销毁）；
2. 普通消耗品不受影响：治疗药水仍可被「使用」并消耗（M2 golden 路径不变）；
3. M3 golden 同款路径「使用火把」不受影响：非剧情物品仍按原行为消耗；
4. console 路径：失败回合仍是 16 字文案 + 「（这一回合没有生效）」，古物不被销毁；
5. 服务层标签判定：quest/quest_item/quest.*/story.* 前缀均拒绝，普通标签放行（参数化）。

装配口径与 ``test_console_walkthrough.py`` 一致：临时库 + 进程内 ``TestClient``（不占端口、不联网）。
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from textgame import console
from textgame.config.settings import get_settings
from textgame.domain.events import EventType
from textgame.domain.models import GameState
from textgame.domain.results import ErrorCode
from textgame.domain.scenario import (
    ITEM_POTION,
    ITEM_RELIC,
    ITEM_TORCH,
    PLAYER_ID,
    build_initial_state,
)
from textgame.persistence.memory import InMemoryRepository
from textgame.services.action_service import ActionService
from textgame.services.query_service import QueryService
from textgame.services.tool_context import ToolContext

pytestmark = [pytest.mark.integration]

#: 编排层对「已知动作被世界拒绝」的固定 16 字文案（D-TEAM-5：本切片不改动）。
REJECTED_TEXT = "这个动作没有生效，你停下了脚步。"


def _dispose(app: FastAPI) -> None:
    """释放组合根内 SQLite Engine 的文件句柄（Windows 临时文件收尾）。"""
    console.dispose_app_engine(app)


def _build_app_with_db(monkeypatch: pytest.MonkeyPatch, db_path: Path) -> tuple[FastAPI, str]:
    """把 ``TEXTGAME_DB_URL`` 指向临时库后重建生产组合根，并返回 DSN。"""
    db_url = f"sqlite:///{db_path.as_posix()}"
    monkeypatch.setenv("TEXTGAME_DB_URL", db_url)
    get_settings.cache_clear()
    import textgame.main as main_module

    _dispose(main_module.app)
    return main_module._build_app(), db_url


def _new_key() -> str:
    """唯一幂等键（每次请求一个新键，避免回放旧结果）。"""
    return f"guard-{uuid.uuid4().hex}"


def _create_m2(client: TestClient) -> tuple[str, int]:
    """建 m2 存档并返回 (game_id, version)。"""
    response = client.post(
        "/api/v1/games", headers={"Idempotency-Key": _new_key()}, json={"seed": 42}
    )
    assert response.status_code == 201
    payload: dict[str, Any] = response.json()
    return str(payload["game_id"]), int(payload["version"])


def _turn(client: TestClient, game_id: str, version: int, text: str) -> tuple[dict[str, Any], int]:
    """提交一个回合，返回 (响应, 下一版本)。"""
    response = client.post(
        "/api/v1/turns",
        headers={"Idempotency-Key": _new_key()},
        json={"game_id": game_id, "input": text, "expected_version": version},
    )
    assert response.status_code == 200
    payload: dict[str, Any] = response.json()
    return payload, int(payload["version"])


def _inventory_names(client: TestClient, game_id: str) -> list[str]:
    """只读视图里的背包物品名。"""
    view = client.get(f"/api/v1/games/{game_id}/view").json()
    return [item["name"] for item in view["inventory"]]


def _state_with_relic(tags: list[str]) -> GameState:
    """构造「古物在玩家背包、且带指定 tags」的状态（服务层单测用）。"""
    state = build_initial_state(save_seed="save-42")
    character = state.characters[PLAYER_ID]
    relic = state.items[ITEM_RELIC].model_copy(update={"tags": tags})
    items = dict(state.items)
    items[ITEM_RELIC] = relic
    characters = dict(state.characters)
    characters[PLAYER_ID] = character.model_copy(update={"inventory": [ITEM_RELIC]})
    return state.model_copy(update={"items": items, "characters": characters})


def _context() -> ToolContext:
    """玩家上下文（六字段与第 6 章 6.2 一致）。"""
    return ToolContext(
        actor_id=PLAYER_ID,
        actor_tags=frozenset({"player"}),
        turn_id=2,
        save_seed="save-42",
        idempotency_key="guard-unit-key",
        caller="player",
    )


def test_http_story_item_use_rejected_without_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """古物「使用」被拒：HTTP 200 业务失败、零事件、版本不变、背包保留古物。"""
    app, _ = _build_app_with_db(monkeypatch, tmp_path / "guard-http.db")
    try:
        with TestClient(app) as client:
            game_id, version = _create_m2(client)
            for text in ("向北走进地窖", "拿起古物"):
                result, version = _turn(client, game_id, version, text)
                assert result["ok"] is True, (text, result)
            assert "古物" in _inventory_names(client, game_id)
            before = client.get(f"/api/v1/games/{game_id}/view").json()["version"]

            rejected, version_after = _turn(client, game_id, version, "使用古物")
            after_view = client.get(f"/api/v1/games/{game_id}/view").json()
    finally:
        _dispose(app)
    assert rejected["ok"] is False
    assert rejected["error_code"] == ErrorCode.INVALID_INPUT.value
    assert rejected["events"] == []
    assert rejected["text"] == REJECTED_TEXT
    assert version_after == version == before  # 失败回合零写入
    assert after_view["version"] == before
    assert "古物" in [item["name"] for item in after_view["inventory"]]


def test_http_consumable_still_usable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """普通消耗品不受保护影响：治疗药水「使用」成功且事件仍为 ITEM_CONSUMED。"""
    app, _ = _build_app_with_db(monkeypatch, tmp_path / "guard-potion.db")
    try:
        with TestClient(app) as client:
            game_id, version = _create_m2(client)
            result, version = _turn(client, game_id, version, "拿起治疗药水")
            assert result["ok"] is True
            used, version = _turn(client, game_id, version, "使用治疗药水")
            assert used["ok"] is True
            assert [event["event_type"] for event in used["events"]] == [
                EventType.ITEM_CONSUMED.value
            ]
            assert _inventory_names(client, game_id) == []
    finally:
        _dispose(app)


def test_http_torch_still_consumed_like_m3_golden(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """M3 golden 脚本同款「使用火把」路径不变：非剧情物品仍按原行为被消耗。"""
    app, _ = _build_app_with_db(monkeypatch, tmp_path / "guard-torch.db")
    try:
        with TestClient(app) as client:
            game_id, version = _create_m2(client)
            result, version = _turn(client, game_id, version, "拿起火把")
            assert result["ok"] is True
            used, _ = _turn(client, game_id, version, "使用火把")
            assert used["ok"] is True
            assert [event["event_type"] for event in used["events"]] == [
                EventType.ITEM_CONSUMED.value
            ]
            assert _inventory_names(client, game_id) == []
    finally:
        _dispose(app)


def test_console_path_shows_16_char_text_and_keeps_relic(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """console 路径不误伤：失败文案仍 16 字 + 固定提示，古物留在背包、版本不变。"""
    app, _ = _build_app_with_db(monkeypatch, tmp_path / "guard-console.db")
    try:
        with TestClient(app) as client:
            session = console.GameSession(client)
            session.create()
            assert session.act("向北走进地窖")["ok"] is True
            assert session.act("拿起古物")["ok"] is True
            view_before = session.view()
            assert view_before is not None
            result = session.act("使用古物")
            view_after = session.view()
    finally:
        _dispose(app)
    assert result["ok"] is False
    assert result["text"] == REJECTED_TEXT
    assert console.FAILED_HINT in console.render_turn(result, view_after)
    assert view_after is not None and view_before is not None
    assert view_after["version"] == view_before["version"]
    assert "古物" in [item["name"] for item in view_after["inventory"]]


@pytest.mark.parametrize(
    "tags",
    [
        ["quest"],
        ["quest_item"],
        ["quest.main"],
        ["story"],
        ["story.main"],
        ["item.relic", "quest_item"],
    ],
)
def test_service_rejects_story_tag_prefixes(tags: list[str]) -> None:
    """服务层：剧情/任务标签前缀一律拒绝、零事件；错误码取 INVALID_INPUT。"""
    service = ActionService(QueryService(InMemoryRepository()))
    result = service.use_item(_context(), _state_with_relic(tags), item_id=ITEM_RELIC)
    assert result.ok is False
    assert result.error_code is ErrorCode.INVALID_INPUT
    assert result.events == []


@pytest.mark.parametrize(
    "tags",
    [
        ["consumable"],
        ["light"],
        ["weapon"],
        ["item.iron_ingot", "material"],
        ["item.potion", "consumable"],
    ],
)
def test_service_allows_non_story_items(tags: list[str]) -> None:
    """服务层：普通物品标签放行，产出与原行为一致的 ITEM_CONSUMED 草稿事件。"""
    service = ActionService(QueryService(InMemoryRepository()))
    state = build_initial_state(save_seed="save-42")
    character = state.characters[PLAYER_ID]
    relic = state.items[ITEM_RELIC].model_copy(update={"tags": tags})
    items = dict(state.items)
    items[ITEM_RELIC] = relic
    characters = dict(state.characters)
    characters[PLAYER_ID] = character.model_copy(update={"inventory": [ITEM_RELIC]})
    state = state.model_copy(update={"items": items, "characters": characters})
    result = service.use_item(_context(), state, item_id=ITEM_RELIC)
    assert result.ok is True
    assert [event.event_type for event in result.events] == [EventType.ITEM_CONSUMED]


def test_potion_and_torch_tags_are_not_story() -> None:
    """场景数据回归：药水/火把/锈剑不得被误判为剧情物品（M3 golden 依赖火把可消耗）。"""
    state = build_initial_state(save_seed="save-42")
    for item_id in (ITEM_POTION, ITEM_TORCH):
        item = state.items[item_id]
        result = ActionService(QueryService(InMemoryRepository())).use_item(
            _context(),
            state.model_copy(
                update={
                    "characters": {
                        **state.characters,
                        PLAYER_ID: state.characters[PLAYER_ID].model_copy(
                            update={"inventory": [item_id]}
                        ),
                    }
                }
            ),
            item_id=item_id,
        )
        assert result.ok is True, item.tags
