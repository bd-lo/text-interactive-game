"""H4 QA：L1 枯井环境伤害 + 倒地结局（真实组合根 + 临时 SQLite + TestClient，禁网）。\r\n\r\n依据 `H4-CONTRACT.md` §6 独立验收：story 存档进入枯井同回合追加\r\n`ENVIRONMENT_DAMAGE`（`hp_delta` 负数、method="environment_damage"、actor_id=玩家），\r\nreducer clamp 0..max；重复进入可扣到 HP≤0 → `status=ended` + 倒地结局 + 拒绝新回合且零写入；\r\n药水可救回；m2/m3/m4 旧场景永不产生该事件（旧 golden 零影响）。\r\n"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def story_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    """临时 SQLite + 生产组合根（含 story 场景/L1 伤害接线）的 TestClient。"""
    from textgame import console
    from textgame.config.settings import get_settings

    db_path = tmp_path / "damage.db"
    monkeypatch.setenv("TEXTGAME_DB_URL", f"sqlite:///{db_path.as_posix()}")
    get_settings.cache_clear()
    import textgame.main as main_module

    console.dispose_app_engine(main_module.app)
    app = main_module._build_app()
    try:
        with TestClient(app) as client:
            yield client
    finally:
        console.dispose_app_engine(app)


def _create(client: TestClient, *, scenario: str = "story", key: str) -> str:
    """建局返回 game_id。"""
    resp = client.post(
        "/api/v1/games",
        headers={"Idempotency-Key": key},
        json={"seed": 42, "scenario": scenario},
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["game_id"])


def _turn(client: TestClient, game_id: str, version: int, text: str, *, key: str) -> dict[str, Any]:
    """提交回合（HTTP 200 + 返回体）。"""
    resp = client.post(
        "/api/v1/turns",
        headers={"Idempotency-Key": key},
        json={"game_id": game_id, "input": text, "expected_version": version},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _view(client: TestClient, game_id: str) -> dict[str, Any]:
    """只读 /view。"""
    resp = client.get(f"/api/v1/games/{game_id}/view")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _events(client: TestClient, game_id: str) -> list[dict[str, Any]]:
    """完整事件列表（replay 只读）。"""
    resp = client.get(f"/api/v1/games/{game_id}/replay", params={"from_seq": 1, "limit": 500})
    assert resp.status_code == 200
    return list(resp.json()["events"])


def _move_sequence(
    client: TestClient, game_id: str, texts: tuple[str, ...]
) -> list[dict[str, Any]]:
    """按脚本连续行动（自动跟随 version），返回每回合结果。"""
    version = _view(client, game_id)["version"]
    results = []
    for index, text in enumerate(texts):
        result = _turn(client, game_id, version, text, key=f"qa-dmg-turn-{index:02d}")
        assert result["ok"] is True, (text, result.get("error_code"), result.get("text"))
        version = result["version"]
        results.append(result)
    return results


def test_well_damage_repeat_until_fallen_ending(story_client: TestClient) -> None:
    """枯井每次进入 -6 HP；重复进入 HP≤0 → status=ended + 倒地结局；拒绝新回合。"""
    client = story_client
    game_id = _create(client, key="qa-dmg-story-0001")
    assert _view(client, game_id)["hp"] == 12

    # 入口 → 地窖（安全）→ 回入口 → 枯井（第一次 -6）
    results = _move_sequence(client, game_id, ("向东走", "向北走进地窖", "向南走", "向南走"))
    first_well = results[-1]
    event_types = [event["event_type"] for event in first_well["events"]]
    assert "CHARACTER_MOVED" in event_types
    assert "ENVIRONMENT_DAMAGE" in event_types
    damage = next(e for e in first_well["events"] if e["event_type"] == "ENVIRONMENT_DAMAGE")
    assert damage["actor_id"] == "char_01J9Z3K4Q7X8Y9Z0ABCDEFGHJN"
    assert damage["method"] == "environment_damage"
    assert damage["result"]["hp_delta"] == -6
    view = _view(client, game_id)
    assert view["hp"] == 6
    assert view["status"] == "active"

    # 在枯井里环顾不再扣血（只在「进入」时受伤）
    look = _turn(client, game_id, view["version"], "环顾四周", key="qa-dmg-turn-10")
    assert look["ok"] is True and look["events"] == []
    assert _view(client, game_id)["hp"] == 6

    # 出井 → 再进井：第二次 -6 → HP 0 → 倒地结局
    final_move = _turn(client, game_id, look["version"], "向北走", key="qa-dmg-turn-11")
    assert final_move["ok"] is True
    version = final_move["version"]
    fatal = _turn(client, game_id, version, "向南走", key="qa-dmg-turn-12")
    assert fatal["ok"] is True
    ending_view = _view(client, game_id)
    assert ending_view["hp"] == 0
    assert ending_view["status"] == "ended"
    assert isinstance(ending_view["ending_text"], str) and ending_view["ending_text"]
    assert "灯" in ending_view["ending_text"] or "寒意" in ending_view["ending_text"]
    assert ending_view["suggested_actions"] == []
    # 结局后拒绝新回合（零写入）
    rejected = _turn(client, game_id, ending_view["version"], "向北走", key="qa-dmg-turn-13")
    assert rejected["ok"] is False
    assert rejected["events"] == [] and rejected["version"] == ending_view["version"]
    assert _view(client, game_id) == ending_view


def test_potion_can_rescue_after_damage(story_client: TestClient) -> None:
    """受伤后用药水：hp 6→14（+8），不死；再次进井可继续掉血。"""
    client = story_client
    game_id = _create(client, key="qa-dmg-story-0002")
    _move_sequence(
        client,
        game_id,
        ("向东走", "向北走进地窖", "拿起治疗药水", "向南走", "向南走"),
    )
    assert _view(client, game_id)["hp"] == 6
    use = _turn(
        client, game_id, _view(client, game_id)["version"], "使用治疗药水", key="qa-dmg-use-01"
    )
    assert use["ok"] is True
    assert _view(client, game_id)["hp"] == 14


def test_legacy_scenarios_never_emit_environment_damage(story_client: TestClient) -> None:
    """旧场景（m2/m3/m4）不产生 ENVIRONMENT_DAMAGE；m2 HP 不变。"""
    client = story_client
    for scenario, script in (
        ("m2", ("拿起火把", "向北走进地窖", "向南走")),
        ("m3", ("拿起火把", "向北走", "向南走")),
        ("m4", ("拿起火把", "向东走进铁匠铺", "打造铁剑")),
    ):
        game_id = _create(client, scenario=scenario, key=f"qa-dmg-legacy-{scenario}-0001")
        results = _move_sequence(client, game_id, script)
        for result in results:
            assert all(event["event_type"] != "ENVIRONMENT_DAMAGE" for event in result["events"]), (
                scenario
            )
        assert all(
            event["event_type"] != "ENVIRONMENT_DAMAGE" for event in _events(client, game_id)
        ), scenario
    assert _view(client, game_id)["hp"] >= 1


def test_story_room_damage_mapping() -> None:
    """纯函数：枯井（按房间名定位）返回负值；其它房间/未知 id 返回 None。"""
    from textgame.domain.scenario_story import (
        STORY_ENTRY_ROOM_ID,
        STORY_TOWER_ROOM_ID,
        build_story_state,
        story_room_damage,
    )

    state = build_story_state(save_seed="save-42")
    well_id = next(room.id for room in state.rooms.values() if room.name == "枯井")
    damage = story_room_damage(well_id)
    assert isinstance(damage, int) and damage < 0
    assert damage == -6
    assert story_room_damage(STORY_ENTRY_ROOM_ID) is None
    assert story_room_damage(STORY_TOWER_ROOM_ID) is None
    assert story_room_damage("room_unknown") is None
