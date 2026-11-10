"""D1/D2/D3：剧情场景 ``story`` 的完整目标链 → 结局 → 结局后拒绝新回合（HTTP 级）。

装配口径与 ``test_item_use_guard.py`` 一致：临时 SQLite + 进程内 ``TestClient``（不占端口、
不联网、不做真实 LLM 调用）。覆盖：

1. ``POST /games {scenario:"story"}`` → ``/view`` 暴露 ``scenario``/``goal``/``hp=12/20``；
2. 12 回合目标链（听老兵 → 造信号火把 → 带火石登塔）后 ``status=ended``、``ending_text`` 非空、
   三条 objectives 全部 done；
3. 结局后 ``POST /turns`` → ``ok=false`` + 固定友好文案、零事件、版本不变（D3 守卫）；
4. 结局后 ``/view.suggested_actions`` 为空（不再引导行动）；
5. 全程 ``text`` 在 80–150 字、事件类型只来自既有 EventType（未新增事件类型）。
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

pytestmark = [pytest.mark.integration]

_FLOW: tuple[str, ...] = (
    "向东走",  # 断桥 → 入口
    "对守夜人老兵说信号灯的事",  # briefed（目标 1）
    "拿起火把",
    "向东走进铁匠铺",
    "拿起铜线",
    "拿起铜线",
    "打造信号火把",  # 目标 2
    "向西走",  # 回入口
    "向北走进地窖",
    "拿起火石",
    "向南走",  # 回入口
    "向上走",  # 上瞭望塔 → 目标 3 / 结局
)

_ALLOWED_EVENT_TYPES = {event_type.value for event_type in EventType}
_STORY_ENDED_TEXT = "故事已经结束了。回到首页可以开新的一局。"


def _dispose(app: FastAPI) -> None:
    """释放组合根内 SQLite Engine 的文件句柄（Windows 临时文件收尾）。"""
    console.dispose_app_engine(app)


def _build_app_with_db(monkeypatch: pytest.MonkeyPatch, db_path: Path) -> FastAPI:
    """把 ``TEXTGAME_DB_URL`` 指向临时库后重建生产组合根。"""
    db_url = f"sqlite:///{db_path.as_posix()}"
    monkeypatch.setenv("TEXTGAME_DB_URL", db_url)
    get_settings.cache_clear()
    import textgame.main as main_module

    _dispose(main_module.app)
    return main_module._build_app()


def _new_key(prefix: str) -> str:
    """唯一幂等键（每次请求新键，避免回放旧结果）。"""
    return f"{prefix}-{uuid.uuid4().hex}"


def _create_story(client: TestClient) -> tuple[str, int]:
    """创建 story 存档并返回 ``(game_id, version)``。"""
    response = client.post(
        "/api/v1/games",
        headers={"Idempotency-Key": _new_key("story-create")},
        json={"seed": 42, "scenario": "story"},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    return str(payload["game_id"]), int(payload["version"])


def _view(client: TestClient, game_id: str) -> dict[str, Any]:
    """只读 ``/view`` 响应（200 断言）。"""
    response = client.get(f"/api/v1/games/{game_id}/view")
    assert response.status_code == 200, response.text
    return response.json()


def _turn(client: TestClient, game_id: str, version: int, text: str) -> tuple[dict[str, Any], int]:
    """提交一个回合，返回 ``(响应, 下一版本)``。"""
    response = client.post(
        "/api/v1/turns",
        headers={"Idempotency-Key": _new_key("story-turn")},
        json={"game_id": game_id, "input": text, "expected_version": version},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    return payload, int(payload["version"])


def test_story_initial_view_exposes_goal_and_wounded_hp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """开局 ``/view``：scenario=story、hp=12/20、goal 三条全未完成、有下一步建议。"""
    app = _build_app_with_db(monkeypatch, tmp_path / "story-initial.db")
    try:
        with TestClient(app) as client:
            game_id, _ = _create_story(client)
            view = _view(client, game_id)
            snapshot = client.get(f"/api/v1/games/{game_id}").json()
    finally:
        _dispose(app)

    assert snapshot["scenario"] == "story"
    assert snapshot["status"] == "active"
    assert snapshot["ending_text"] is None
    assert view["scenario"] == "story"
    assert (view["hp"], view["max_hp"]) == (12, 20)
    assert view["status"] == "active"
    assert view["ending_text"] is None
    goal = view["goal"]
    assert goal is not None
    assert goal["title"] == "让信号灯亮起来"
    assert [objective["id"] for objective in goal["objectives"]] == ["listen", "craft", "light"]
    assert all(objective["done"] is False for objective in goal["objectives"])
    assert 1 <= len(view["suggested_actions"]) <= 4


def test_story_goal_chain_reaches_victory_ending(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """12 回合走完目标链：objectives 逐个完成 → status=ended + ending_text 非空。"""
    app = _build_app_with_db(monkeypatch, tmp_path / "story-victory.db")
    try:
        with TestClient(app) as client:
            game_id, version = _create_story(client)
            seen_event_types: list[str] = []
            for index, text in enumerate(_FLOW):
                result, version = _turn(client, game_id, version, text)
                assert result["ok"] is True, (index, text, result)
                assert 80 <= len(result["text"]) <= 150
                for event in result["events"]:
                    seen_event_types.append(str(event["event_type"]))
                # 成功回合给出下一步建议（≤4、非空），结局回合（最后一击）除外
                if index < len(_FLOW) - 1:
                    assert 1 <= len(result["suggested_actions"]) <= 4, (index, text)
            final = _view(client, game_id)
            snapshot = client.get(f"/api/v1/games/{game_id}").json()
    finally:
        _dispose(app)

    assert snapshot["status"] == "ended"
    assert snapshot["ending_text"] == final["ending_text"]

    assert seen_event_types, "目标链应至少产生事件"
    assert set(seen_event_types) <= _ALLOWED_EVENT_TYPES, "不得引入新事件类型"
    assert final["status"] == "ended"
    assert isinstance(final["ending_text"], str) and len(final["ending_text"]) >= 60
    assert all(objective["done"] for objective in final["goal"]["objectives"])
    assert final["suggested_actions"] == []


def test_story_ended_rejects_new_turn_without_any_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """结局后再提交：``ok=false`` + 固定文案 + 零事件 + 版本不变（已到结局的存档不可续玩）。"""
    app = _build_app_with_db(monkeypatch, tmp_path / "story-ended-guard.db")
    try:
        with TestClient(app) as client:
            game_id, version = _create_story(client)
            for text in _FLOW:
                result, version = _turn(client, game_id, version, text)
                assert result["ok"] is True, (text, result)
            before = _view(client, game_id)
            events_before = client.get(
                f"/api/v1/games/{game_id}/replay", params={"limit": 500}
            ).json()["events"]
            after_result, version_after = _turn(client, game_id, version, "环顾四周")
            after = _view(client, game_id)
            events_after = client.get(
                f"/api/v1/games/{game_id}/replay", params={"limit": 500}
            ).json()["events"]
    finally:
        _dispose(app)

    assert after_result["ok"] is False
    assert after_result["text"] == _STORY_ENDED_TEXT
    assert after_result["events"] == []
    assert after_result["error_code"] == "INVALID_INPUT"
    assert version_after == version
    assert before["version"] == after["version"] == version
    assert before["ending_text"] == after["ending_text"]
    # 拒绝回合零写入：事件序列与通关时逐条相同
    assert [event["id"] for event in events_after] == [event["id"] for event in events_before]
    assert len(events_before) == 1 + len(_FLOW)  # 开局 + 每个写回合 1 条事件
    # 结局后不再引导行动，但给出「故事结束」提示
    assert after_result["suggested_actions"] == []
    hints = [effect.get("hint") for effect in after_result["effects"]]
    assert any(isinstance(hint, str) and "结束" in hint for hint in hints)


def test_story_rejected_action_shows_specific_chinese_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """P0-2 残余：被拒绝的动作要给出具体中文原因（不是通用「换种说法」），且零写入。"""
    app = _build_app_with_db(monkeypatch, tmp_path / "story-reason.db")
    try:
        with TestClient(app) as client:
            game_id, version = _create_story(client)
            for text in ("向东走进入口", "向东走进铁匠铺"):
                result, version = _turn(client, game_id, version, text)
                assert result["ok"] is True
            rejected, version_after = _turn(client, game_id, version, "向北走")
            view = _view(client, game_id)
    finally:
        _dispose(app)

    assert rejected["ok"] is False
    assert rejected["error_code"] == "INVALID_INPUT"
    assert rejected["events"] == []
    assert version_after == version
    hints = [str(effect.get("hint", "")) for effect in rejected["effects"]]
    assert any("没有往北的路" in hint for hint in hints), hints
    # 建议是当前房间真正可执行的下一步（铁匠铺 → 往西回入口 / 拿起铜线或锻锤）
    assert rejected["suggested_actions"]
    assert rejected["suggested_actions"][0].startswith("向西")
    # 不得泄漏内部 ID / 英文诊断
    joined = " ".join(hints) + " ".join(rejected["suggested_actions"])
    assert "item_" not in joined and "npc_" not in joined and "room_" not in joined
    assert view["version"] == version


def test_story_craft_missing_material_hint_names_the_item(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC-09c：缺制造材料时必须点名缺什么（而不是「没听懂」），且零写入。

    复现：入口 → 铁匠铺拿两卷铜线（无火把）→ 打造信号火把。
    """
    app = _build_app_with_db(monkeypatch, tmp_path / "story-craft-hint.db")
    try:
        with TestClient(app) as client:
            game_id, version = _create_story(client)
            for text in ("向东走进入口", "向东走进铁匠铺", "拿起铜线", "拿起铜线"):
                result, version = _turn(client, game_id, version, text)
                assert result["ok"] is True, (text, result)
            rejected, version_after = _turn(client, game_id, version, "打造信号火把")
            view = _view(client, game_id)
    finally:
        _dispose(app)

    assert rejected["ok"] is False
    assert rejected["events"] == []
    assert version_after == version
    hints = " ".join(str(effect.get("hint", "")) for effect in rejected["effects"])
    assert "缺少材料" in hints and "火把" in hints, hints
    assert "item_" not in hints and "npc_" not in hints and "room_" not in hints
    assert rejected["suggested_actions"]
    assert view["version"] == version


def test_story_first_talk_with_generic_topic_counts_as_briefed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """首次搭话即使用普通话题（LLM 可能给不出任务关键词）也必须听完任务说明。

    回归：LLM 解析的 topic 不含「信号/灯/任务」时，旧实现会停在 ``talked``，
    导致建议动作「和守夜人老兵说话」永远完不成目标 1。
    """
    app = _build_app_with_db(monkeypatch, tmp_path / "story-first-talk.db")
    try:
        with TestClient(app) as client:
            game_id, version = _create_story(client)
            result, version = _turn(client, game_id, version, "向东走")
            assert result["ok"] is True
            talked, version = _turn(client, game_id, version, "对守夜人老兵说天色")
            view = _view(client, game_id)
    finally:
        _dispose(app)

    assert talked["ok"] is True
    objectives = {item["id"]: item["done"] for item in view["goal"]["objectives"]}
    assert objectives["listen"] is True


def test_story_ending_not_shared_by_legacy_scenario(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """旧场景不受结局逻辑影响：m2 存档 ``scenario=m2``、``goal=None``、``status=active``。"""
    app = _build_app_with_db(monkeypatch, tmp_path / "story-legacy.db")
    try:
        with TestClient(app) as client:
            created = client.post(
                "/api/v1/games",
                headers={"Idempotency-Key": _new_key("legacy-create")},
                json={"seed": 42},
            ).json()
            view = _view(client, str(created["game_id"]))
    finally:
        _dispose(app)

    assert view["scenario"] == "m2"
    assert view["goal"] is None
    assert view["ending_text"] is None
    assert view["status"] == "active"
