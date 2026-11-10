"""HTTP 级「玩家旅程」E2E（真实组合根 + 临时 SQLite + TestClient，禁网；QA-1/QA-2）。

覆盖玩家实际会走的路径（不是单端点测试）：

1. 新玩家（m2）：创建对局 → 同键重放（刷新不丢档）→ 首屏视图 → 环顾/问好/拿取/使用/移动 → 再次查看；
2. 恢复/续玩：`GET /games/{id}` 取回 `last_text`/版本，`/replay` 按 seq 回放叙事，重复请求零写入；
3. 重启续玩：释放进程内 Engine 后用同一 SQLite 文件重建组合根，再读视图并继续出下一回合；
4. m4 制造旅程：拿火把 → 走进铁匠铺 → 打造铁剑（CRAFT_DETERMINISTIC 事件）；
5. 失败回合：无法理解输入 → 200 `ok=false` + 玩家可读 hints + 不泄漏内部字段 + 零写入；
6. 错误信封：404 / 409 / 422 统一 JSON 信封（第 6 章 §6.7、第 15 章不暴露内部细节）；
7. story「余烬哨站」完整玩家旅程（QA-2 转正）：目标 0/3 → 听/造/点三段推进 → inspect →
   heal 12→20 → 结局 → 结局后拒绝新回合且零写入（D2/D3/D4/D9）。

装配口径与 `test_console_walkthrough.py` 一致：`TEXTGAME_DB_URL` 指向 `tmp_path` →
`get_settings.cache_clear()` → `main._build_app()` → `TestClient`；网络由 conftest autouse
socket guard 阻断（本文件不联网）。
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from textgame import console
from textgame.config.settings import get_settings
from textgame.orchestration.turn_orchestrator import _UNKNOWN_ACTION_TEXT

pytestmark = [pytest.mark.integration]

#: 内部实体标识形态（ULID === 26 位 Crockford Base32）；错误文案/失败提示不得泄漏。
_ULID_RE = re.compile(r"(?:room|item|npc|char|game)_[0-9A-HJKMNP-TV-Z]{26}")
#: 500/409 信封不得出现实现细节（第 15 章）。
_INTERNAL_TERMS = ("traceback", "sqlalchemy", "sqlite", "select ", "valueerror", "state_hash")


def _build_app(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """把 ``TEXTGAME_DB_URL`` 指向临时文件库后重建生产组合根（不联网）。"""
    monkeypatch.setenv("TEXTGAME_DB_URL", f"sqlite:///{db_path.as_posix()}")
    get_settings.cache_clear()
    import textgame.main as main_module

    console.dispose_app_engine(main_module.app)
    return main_module._build_app()


@dataclass
class Journey:
    """一条旅程的 HTTP 客户端与组合根（fixture 负责收尾释放 SQLite 文件句柄）。"""

    client: TestClient
    app: FastAPI
    db_path: Path


@pytest.fixture
def journey(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Journey]:
    """每个用例一套独立临时库 + 生产组合根；用例结束释放 Engine。"""
    db_path = tmp_path / "journey.db"
    app = _build_app(db_path, monkeypatch)
    try:
        with TestClient(app) as client:
            yield Journey(client=client, app=app, db_path=db_path)
    finally:
        console.dispose_app_engine(app)


# ---------------------------------------------------------------------------
# 旅程辅助
# ---------------------------------------------------------------------------
def _create_game(
    journey: Journey,
    *,
    key: str,
    seed: int | None = 42,
    scenario: str | None = None,
) -> dict[str, Any]:
    """``POST /api/v1/games``（201 断言）；``scenario`` 仅在显式给定时传入。"""
    body: dict[str, Any] = {}
    if seed is not None:
        body["seed"] = seed
    if scenario is not None:
        body["scenario"] = scenario
    response = journey.client.post("/api/v1/games", headers={"Idempotency-Key": key}, json=body)
    assert response.status_code == 201, response.text
    payload: dict[str, Any] = response.json()
    return payload


def _view(journey: Journey, game_id: str) -> dict[str, Any]:
    """``GET /view``（200 断言）并返回玩家视图。"""
    response = journey.client.get(f"/api/v1/games/{game_id}/view")
    assert response.status_code == 200, response.text
    payload: dict[str, Any] = response.json()
    return payload


def _replay(journey: Journey, game_id: str) -> dict[str, Any]:
    """``GET /replay``（from_seq=1、limit=500，200 断言）返回完整事件页。"""
    response = journey.client.get(
        f"/api/v1/games/{game_id}/replay", params={"from_seq": 1, "limit": 500}
    )
    assert response.status_code == 200, response.text
    payload: dict[str, Any] = response.json()
    return payload


def _post_turn(
    journey: Journey,
    *,
    game_id: str,
    text: str,
    expected_version: int,
    key: str,
) -> dict[str, Any]:
    """``POST /turns``：HTTP 200 断言，业务失败也在 200 信封内，返回 TurnResult。"""
    response = journey.client.post(
        "/api/v1/turns",
        headers={"Idempotency-Key": key},
        json={"game_id": game_id, "input": text, "expected_version": expected_version},
    )
    assert response.status_code == 200, response.text
    payload: dict[str, Any] = response.json()
    return payload


def _names(items: list[dict[str, Any]]) -> set[str]:
    """视图物品/NPC 列表 → 名字集合。"""
    return {str(item["name"]) for item in items}


def _assert_error_envelope(response_json: dict[str, Any], code: str) -> None:
    """统一失败信封：``ok=False``、稳定 error_code、中文 message、无内部细节。"""
    assert response_json["ok"] is False
    assert response_json["error_code"] == code
    message = response_json["message"]
    assert isinstance(message, str) and message
    lowered = message.lower()
    for term in _INTERNAL_TERMS:
        assert term not in lowered, f"错误信封泄漏内部细节: {term!r}"
    assert _ULID_RE.search(message) is None


# ---------------------------------------------------------------------------
# 1. m2 新玩家主旅程（创建 → 探索 → 拿取/使用 → 移动 → 恢复 → 回放）
# ---------------------------------------------------------------------------
def test_m2_new_player_journey(journey: Journey) -> None:
    """新玩家 HTTP 主链路：创建、同键重放、首屏、6 个成功回合、视图/回放一致。"""
    client = journey.client
    assert client.get("/healthz").status_code == 200

    created = _create_game(journey, key="qa-journey-m2-game-0001")
    # 契约是加法式（D9/T15 可能追加 scenario 等字段）：必需字段齐全即可，允许新增。
    assert {"game_id", "turn", "version", "snapshot_version", "save_seed"} <= set(created)
    assert (created["turn"], created["version"], created["save_seed"]) == (1, 1, "save-42")
    # 刷新/重复提交不丢档：同键同参幂等回放（201，响应一致，零写入）
    replayed_create = _create_game(journey, key="qa-journey-m2-game-0001")
    assert replayed_create == created

    game_id = created["game_id"]
    view = _view(journey, game_id)
    assert view["status"] == "active"
    assert (view["turn"], view["version"]) == (1, 1)
    assert view["room"]["title"] == "废弃哨站·入口"
    assert "north" in view["room"]["exits"]
    assert _names(view["room"]["items"]) == {"火把", "治疗药水"}
    assert _names(view["room"]["npcs"]) == {"老哨兵"}
    assert (view["hp"], view["max_hp"]) == (20, 20)
    assert view["inventory"] == []
    # T15/D9 后 /view 会追加 suggested_actions；当前缺省允许，存在时必须是非空字符串列表。
    suggested = view.get("suggested_actions")
    if suggested is not None:
        assert suggested and all(isinstance(action, str) and action for action in suggested)

    # 回合 1：环顾（只读，不推进 version）
    look = _post_turn(
        journey, game_id=game_id, text="环顾四周", expected_version=1, key="qa-journey-t01"
    )
    assert look["ok"] is True and look["version"] == 1 and look["events"] == []
    assert 80 <= len(look["text"]) <= 150

    # 回合 2：问好（DIALOGUE_EXCHANGED）
    talk = _post_turn(
        journey, game_id=game_id, text="对老哨兵问好", expected_version=1, key="qa-journey-t02"
    )
    assert talk["ok"] is True and talk["version"] == 2
    assert [event["event_type"] for event in talk["events"]] == ["DIALOGUE_EXCHANGED"]

    # 回合 3/4：拿火把、拿药水（ITEM_TAKEN）
    torch = _post_turn(
        journey, game_id=game_id, text="拿起火把", expected_version=2, key="qa-journey-t03"
    )
    assert torch["ok"] is True and torch["version"] == 3
    assert [event["event_type"] for event in torch["events"]] == ["ITEM_TAKEN"]
    potion = _post_turn(
        journey, game_id=game_id, text="拿起治疗药水", expected_version=3, key="qa-journey-t04"
    )
    assert potion["ok"] is True and potion["version"] == 4

    # 回合 5：使用药水（ITEM_CONSUMED；满血时 D4 不改变 HP，背包移除）
    use = _post_turn(
        journey, game_id=game_id, text="使用治疗药水", expected_version=4, key="qa-journey-t05"
    )
    assert use["ok"] is True and use["version"] == 5
    assert [event["event_type"] for event in use["events"]] == ["ITEM_CONSUMED"]
    mid_view = _view(journey, game_id)
    assert _names(mid_view["inventory"]) == {"火把"}
    assert (mid_view["hp"], mid_view["max_hp"]) == (20, 20)  # 满血：heal clamp 0，零 diff
    # 成功回合的 suggested_actions 是 T13/D7 加法字段；当前允许为空，存在时最多 4 条。
    assert isinstance(use.get("suggested_actions"), list)
    assert len(use.get("suggested_actions") or []) <= 4

    # 回合 6：向北进地窖（CHARACTER_MOVED）
    moved = _post_turn(
        journey, game_id=game_id, text="向北走进地窖", expected_version=5, key="qa-journey-t06"
    )
    assert moved["ok"] is True and moved["version"] == 6
    assert [event["event_type"] for event in moved["events"]] == ["CHARACTER_MOVED"]
    room_view = _view(journey, game_id)
    assert room_view["room"]["title"] == "哨站地窖"

    # 回合 7：拿锈剑
    sword = _post_turn(
        journey, game_id=game_id, text="拿起锈剑", expected_version=6, key="qa-journey-t07"
    )
    assert sword["ok"] is True and sword["version"] == 7
    final_view = _view(journey, game_id)
    assert final_view["turn"] == 7 and final_view["version"] == 7
    assert _names(final_view["inventory"]) == {"火把", "锈剑"}

    # 恢复视图：刷新后能拿回版本与最后一段叙事（Web 续玩依赖）
    game_view = client.get(f"/api/v1/games/{game_id}")
    assert game_view.status_code == 200, game_view.text
    snapshot = game_view.json()
    assert (snapshot["turn"], snapshot["version"]) == (7, 7)
    assert isinstance(snapshot["last_text"], str) and 80 <= len(snapshot["last_text"]) <= 150

    # 回放：seq 连续、含 game_created、result.narrative 是文本源；重复请求零写入
    page = _replay(journey, game_id)
    assert page["from_seq"] == 1 and page["to_seq"] == 7
    events = page["events"]
    assert len(events) == 7
    assert [event["seq"] for event in events] == list(range(1, 8))
    assert events[0]["event_type"] == "game_created"
    assert [event["event_type"] for event in events[1:]] == [
        "DIALOGUE_EXCHANGED",
        "ITEM_TAKEN",
        "ITEM_TAKEN",
        "ITEM_CONSUMED",
        "CHARACTER_MOVED",
        "ITEM_TAKEN",
    ]
    assert any(
        isinstance((event.get("result") or {}).get("narrative"), str)
        and len(event["result"]["narrative"]) > 0
        for event in events
    )
    assert _view(journey, game_id)["version"] == 7  # 回放/视图只读，不推进版本
    assert _replay(journey, game_id) == page


# ---------------------------------------------------------------------------
# 2. 重启续玩（释放 Engine → 同一 SQLite 文件重建组合根 → 继续出回合）
# ---------------------------------------------------------------------------
def test_journey_resumes_after_process_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """模拟进程重启：同一 DB 文件新建组合根后能读回进度并继续下一回合。"""
    db_path = tmp_path / "resume.db"
    first_app = _build_app(db_path, monkeypatch)
    try:
        with TestClient(first_app) as client:
            created = client.post(
                "/api/v1/games",
                headers={"Idempotency-Key": "qa-resume-game-0001"},
                json={"seed": 42},
            ).json()
            game_id = created["game_id"]
            talk = client.post(
                "/api/v1/turns",
                headers={"Idempotency-Key": "qa-resume-turn-0001"},
                json={"game_id": game_id, "input": "对老哨兵问好", "expected_version": 1},
            )
            assert talk.status_code == 200 and talk.json()["version"] == 2
            before = client.get(f"/api/v1/games/{game_id}/view").json()
    finally:
        console.dispose_app_engine(first_app)

    second_app = _build_app(db_path, monkeypatch)
    try:
        with TestClient(second_app) as client:
            restored = client.get(f"/api/v1/games/{game_id}")
            assert restored.status_code == 200, restored.text
            assert (restored.json()["turn"], restored.json()["version"]) == (2, 2)
            view = client.get(f"/api/v1/games/{game_id}/view")
            assert view.status_code == 200, view.text
            after = view.json()
            assert after["room"]["title"] == before["room"]["title"]
            assert (after["turn"], after["version"], after["inventory"]) == (
                before["turn"],
                before["version"],
                before["inventory"],
            )
            # 续玩：用恢复出的 version 继续出回合；事件 seq 接着原存档继续（2 → 3）
            resumed = client.post(
                "/api/v1/turns",
                headers={"Idempotency-Key": "qa-resume-turn-0002"},
                json={"game_id": game_id, "input": "拿起火把", "expected_version": 2},
            )
            assert resumed.status_code == 200, resumed.text
            body = resumed.json()
            assert body["ok"] is True and body["version"] == 3
            replay = client.get(
                f"/api/v1/games/{game_id}/replay", params={"from_seq": 1, "limit": 500}
            ).json()
            assert [event["seq"] for event in replay["events"]] == [1, 2, 3]
    finally:
        console.dispose_app_engine(second_app)


# ---------------------------------------------------------------------------
# 3. m4 制造旅程（确定性制造链路）
# ---------------------------------------------------------------------------
def test_m4_crafting_journey(journey: Journey) -> None:
    """m4：拿火把 → 向东进铁匠铺 → 打造铁剑；事件类型与产物对得上。"""
    created = _create_game(journey, key="qa-journey-m4-game-0001", scenario="m4")
    game_id = created["game_id"]
    version = created["version"]

    script = (
        ("拿起火把", 2, "ITEM_TAKEN"),
        ("向东走进铁匠铺", 3, "CHARACTER_MOVED"),
        ("打造铁剑", 4, "CRAFT_DETERMINISTIC"),
    )
    for index, (text, expected_version, expected_event) in enumerate(script):
        result = _post_turn(
            journey,
            game_id=game_id,
            text=text,
            expected_version=version,
            key=f"qa-m4-turn-{index:02d}",
        )
        assert result["ok"] is True, (text, result.get("error_code"), result.get("text"))
        assert result["version"] == expected_version
        assert [event["event_type"] for event in result["events"]] == [expected_event]
        version = expected_version

    view = _view(journey, game_id)
    assert (view["turn"], view["version"]) == (4, 4)
    assert view["room"]["title"] == "废弃铁匠铺"
    assert "铁剑" in _names(view["inventory"])

    page = _replay(journey, game_id)
    assert [event["seq"] for event in page["events"]] == [1, 2, 3, 4]
    assert page["events"][-1]["event_type"] == "CRAFT_DETERMINISTIC"
    assert isinstance(page["events"][-1]["result"], dict)


# ---------------------------------------------------------------------------
# 4. 失败回合：hints 可读 + 零写入 + 不泄漏内部字段
# ---------------------------------------------------------------------------
def test_failure_turn_hints_are_player_readable_and_read_only(journey: Journey) -> None:
    """无法理解的输入：200 失败信封、3–4 条建议、hint 中文可读、版本/事件零变化。"""
    created = _create_game(journey, key="qa-fail-game-0001")
    game_id = created["game_id"]
    before = _replay(journey, game_id)

    failed = _post_turn(
        journey, game_id=game_id, text="跳舞", expected_version=1, key="qa-fail-turn-0001"
    )
    assert failed["ok"] is False
    assert failed["error_code"] == "INVALID_INPUT"
    assert failed["version"] == 1  # 失败不推进版本
    assert failed["events"] == []
    assert failed["text"] == _UNKNOWN_ACTION_TEXT  # D12：失败 text 保持通用文案

    hints = [entry.get("hint") for entry in failed.get("effects") or []]
    assert hints and all(isinstance(hint, str) and hint for hint in hints)
    suggested = failed.get("suggested_actions") or []
    assert 1 <= len(suggested) <= 4
    assert all(isinstance(action, str) and action for action in suggested)

    # 玩家可见文案不得含协议内部字段（json/game_id/version/idempotency…）
    visible = "\n".join([failed["text"], *hints, *suggested])
    console.assert_clean_output(visible)
    # 失败提示不得泄漏内部实体 ID
    assert _ULID_RE.search(visible) is None
    # 失败回合零写入：事件仍只有 game_created，重复同键失败不回写
    assert _replay(journey, game_id) == before
    again = _post_turn(
        journey, game_id=game_id, text="跳舞", expected_version=1, key="qa-fail-turn-0001"
    )
    assert again == failed


# ---------------------------------------------------------------------------
# 5. API 错误信封（404 / 409 / 422）
# ---------------------------------------------------------------------------
def test_api_error_envelopes_are_json_and_player_safe(journey: Journey) -> None:
    """404/409/422 一律 JSON 统一信封；不含堆栈/路径/SQL 等实现细节。"""
    client = journey.client

    not_found = client.get("/api/v1/games/game_missing_0001")
    assert not_found.status_code == 404
    assert not_found.headers["content-type"].startswith("application/json")
    _assert_error_envelope(not_found.json(), "NOT_FOUND")

    created = _create_game(journey, key="qa-err-game-0001")
    conflict = client.post(
        "/api/v1/games",
        headers={"Idempotency-Key": "qa-err-game-0001"},
        json={"seed": 7},
    )
    assert conflict.status_code == 409
    _assert_error_envelope(conflict.json(), "CONFLICT")

    invalid = client.post(
        "/api/v1/turns",
        headers={"Idempotency-Key": "qa-err-turn-0001"},
        json={"game_id": created["game_id"], "expected_version": 1},  # 缺 input
    )
    assert invalid.status_code == 422
    assert invalid.headers["content-type"].startswith("application/json")
    _assert_error_envelope(invalid.json(), "INVALID_INPUT")
    assert invalid.json()["violations"]  # 字段级 violations 供前端定位


# ---------------------------------------------------------------------------
# 6. story「余烬哨站」完整玩家旅程（QA-2 已转正：目标链 → 结局 → 结局后拒绝）
# ---------------------------------------------------------------------------
_STORY_GOAL_TITLE = "让信号灯亮起来"
_STORY_ENDED_TEXT = "故事已经结束了。回到首页可以开新的一局。"
#: 玩家旅程脚本：移动/细看/问话/拿取/制造/失败恢复/回血/登塔，共 16 个回合（3 个只读或失败不写）。
_STORY_FLOW: tuple[str, ...] = (
    "向东走",
    "细看火把",
    "对守夜人老兵说信号灯的事",
    "拿起火把",
    "向东走进铁匠铺",
    "拿起铜线",
    "拿起铜线",
    "打造信号火把",
    "向北走",  # 预期失败：铁匠铺没有北出口（断言具体 hint 后继续旅程）
    "向西走",
    "向北走进地窖",
    "拿起火石",
    "拿起治疗药水",
    "使用治疗药水",
    "向南走",
    "向上走",
)


def _goal_done(view: dict[str, Any]) -> dict[str, bool]:
    """``/view.goal.objectives`` → ``{objective_id: done}``。"""
    return {
        str(objective["id"]): bool(objective["done"]) for objective in view["goal"]["objectives"]
    }


def test_story_player_journey_to_ending_and_rejection(journey: Journey) -> None:
    """story 玩家旅程：目标 0/3 → 听/造/点推进 + inspect + heal 12→20 + 结局 + 零写入拒绝。"""
    client = journey.client
    created = _create_game(journey, key="qa-story-game-0001", scenario="story")
    game_id = created["game_id"]
    version = created["version"]

    # 首屏：断桥、雪夜旧伤（hp 12/20，药水回血可观测）、目标链未完成、给得出下一步
    view = _view(journey, game_id)
    assert view["scenario"] == "story"
    assert view["status"] == "active"
    assert (view["hp"], view["max_hp"]) == (12, 20)
    assert view["ending_text"] is None
    assert view["room"]["title"] == "哨站外·断桥"
    assert view["goal"]["title"] == _STORY_GOAL_TITLE
    assert [objective["id"] for objective in view["goal"]["objectives"]] == [
        "listen",
        "craft",
        "light",
    ]
    assert _goal_done(view) == {"listen": False, "craft": False, "light": False}
    assert 1 <= len(view["suggested_actions"]) <= 4
    assert "向东走" in view["suggested_actions"]

    results: list[dict[str, Any]] = []
    views: list[dict[str, Any]] = []
    for index, text in enumerate(_STORY_FLOW):
        result = _post_turn(
            journey,
            game_id=game_id,
            text=text,
            expected_version=version,
            key=f"qa-story-turn-{index:02d}",
        )
        version = result["version"]
        results.append(result)
        views.append(_view(journey, game_id))
        if text != "向北走":  # 失败步单独断言（见下方第 4 段）
            assert result["ok"] is True, (index, text, result.get("error_code"), result.get("text"))
            if index < len(_STORY_FLOW) - 1:
                assert 1 <= len(result["suggested_actions"]) <= 4, (index, text)
            assert 80 <= len(result["text"]) <= 150, (index, text)

    # 1) 移动 + 只读 inspect：细看不推进版本链，文本含物品描述
    assert views[0]["room"]["title"] == "废弃哨站·入口"
    inspect_result = results[1]
    assert inspect_result["ok"] is True and inspect_result["events"] == []
    assert inspect_result["version"] == 2  # inspect 只读：版本仍是「向东走」后的 2
    assert "受潮的火把" in inspect_result["text"] and "松脂" in inspect_result["text"]

    # 2) 目标 1：听完老兵的任务说明（briefed），玩家看得到 3 步交代
    talk = results[2]
    assert [event["event_type"] for event in talk["events"]] == ["DIALOGUE_EXCHANGED"]
    assert "灯座空着" in talk["text"]
    assert _goal_done(views[2])["listen"] is True

    # 3) 目标 2：两卷铜线按模板聚合成 qty=2；打造出信号火把；随后建议「向西走」
    forge_view = views[6]
    copper = [item for item in forge_view["inventory"] if item["name"] == "铜线"]
    assert len(copper) == 1 and copper[0]["qty"] == 2
    craft = results[7]
    assert [event["event_type"] for event in craft["events"]] == ["CRAFT_DETERMINISTIC"]
    assert _goal_done(views[7])["craft"] is True
    assert "信号火把" in _names(views[7]["inventory"])
    assert "向西走" in craft["suggested_actions"]

    # 4) 失败路径：铁匠铺没有北出口 → 具体中文原因 + 可执行建议 + 零写入（版本不推进）
    rejected = results[8]
    assert rejected["ok"] is False and rejected["error_code"] == "INVALID_INPUT"
    assert rejected["events"] == [] and rejected["version"] == craft["version"]
    rejected_hints = [str(effect.get("hint", "")) for effect in rejected["effects"]]
    assert any("没有往北的路" in hint for hint in rejected_hints), rejected_hints
    assert rejected["suggested_actions"] and rejected["suggested_actions"][0].startswith("向西")
    console.assert_clean_output("\n".join([rejected["text"], *rejected_hints]))
    assert _ULID_RE.search(" ".join(rejected_hints)) is None

    # 5) 回血：地窖拿药水后使用 → hp 12→20（D4 在真实受伤状态下的玩家可观测效果）
    assert views[12]["hp"] == 12  # 用药前
    use = results[13]
    assert [event["event_type"] for event in use["events"]] == ["ITEM_CONSUMED"]
    assert views[13]["hp"] == views[13]["max_hp"] == 20  # heal=8，clamp 到满血
    assert "治疗药水" not in _names(views[13]["inventory"])

    # 6) 结局：带火石登塔点亮 → status=ended + ending_text；三目标全 done；不再引导行动
    final = views[-1]
    assert final["status"] == "ended"
    assert results[-1]["version"] == 15
    assert final["room"]["title"] == "哨站瞭望塔"
    assert all(_goal_done(final).values())
    assert isinstance(final["ending_text"], str) and len(final["ending_text"]) >= 60
    assert "灯" in final["ending_text"] and "活人" in final["ending_text"]
    assert final["suggested_actions"] == []
    snapshot = client.get(f"/api/v1/games/{game_id}")
    assert snapshot.status_code == 200
    snapshot_body = snapshot.json()
    assert snapshot_body["status"] == "ended"
    assert snapshot_body["scenario"] == "story"
    assert snapshot_body["ending_text"] == final["ending_text"]
    # 结局屏文本不泄漏内部字段，玩家可读
    console.assert_clean_output(final["ending_text"])
    assert _ULID_RE.search(final["ending_text"]) is None

    # 7) 回放：15 条事件（1 开局 + 14 写回合）；inspect/失败/环顾零写入
    page = _replay(journey, game_id)
    assert [event["seq"] for event in page["events"]] == list(range(1, 16))
    assert page["events"][0]["event_type"] == "game_created"
    assert page["events"][-1]["event_type"] == "CHARACTER_MOVED"

    # 8) 结局后拒绝新回合：固定友好文案 + 版本/事件/视图三不变（零写入）
    after = _post_turn(
        journey,
        game_id=game_id,
        text="环顾四周",
        expected_version=version,
        key="qa-story-after-end-0001",
    )
    assert after["ok"] is False
    assert after["text"] == _STORY_ENDED_TEXT
    assert after["error_code"] == "INVALID_INPUT"
    assert after["events"] == []
    assert after["version"] == version == 15
    assert after["suggested_actions"] == []
    post_hints = [str(effect.get("hint", "")) for effect in after["effects"]]
    assert any("结束" in hint for hint in post_hints), post_hints
    assert _view(journey, game_id) == final
    assert _replay(journey, game_id) == page
