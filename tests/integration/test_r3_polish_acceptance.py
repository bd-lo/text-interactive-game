"""R3 / S2 独立黑盒验收（qa-tester，task-7；不 import、不改 backend-dev 的 6 个测试文件）。

覆盖 S2 玩家体验打磨的**玩家可读口径**（全部走真实 HTTP/CLI，进程内临时 SQLite，不联网）：

- P2-4 缺材料建议优先：hint 点名材料+位置；``suggested_actions[0]`` 是**可执行**的一步
  （提交后真的移动/拾取成功）；非 craft 失败与成功回合建议保持现状；
- AC-35 细看文案去重：story 6 个可细看对象 + m2 2 个，无相邻重复句、80–150 字、确定性；
- AC-35 对话框架：story reply 分支不再出现「沉默片刻，抬眼看你」；m2 旧分支 83 字不变；
- P2-5 移动叙事：story up/down 不再用「上边/下边」；m2 north 文案**逐字**等于 HEAD 基线；
- 只读回合（看/背包/细看）：零事件、不写 replay、版本/回合不变（前端中性徽章的数据依据）；
- console 按卡开局（P2/OSS）：真实 CLI 子进程 ``--card-id`` → 建局 → 重开 app 用 replay 的
  ``game_created.params.card_id`` 验身份；``--list-cards`` / 未知卡 / 参数冲突错误路径；
- AC-33 旧档兼容：``game_created.params`` 去 ``scenario`` 等新字段 + 状态 JSON 去 ``quests``
  后，``/games/{id}``、``/view``、``/replay`` 可读且能继续回合；
- AC-34/AC-10d 数据面：12 步胜利链后 ``/view`` 的 ``status/turn/hp/max_hp/goal/ending_text``
  与前端的「通关总结 + 结局入冒险记录」展示契约一致。

写域：本文件为 qa 独立新增；不修改 backend 6 个测试、不碰 src/web/golden。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
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

REPO_ROOT = Path(__file__).resolve().parents[2]
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？；…])")
_INTERNAL_ID = re.compile(r"\b(?:card|game|char|npc|item|room)_[0-9A-Za-z]{6,}\b")
_MIN_TEXT = 80
_MAX_TEXT = 150

#: HEAD 基线：m2「向北走」叙事（P2-5 只改 up/down，四方向必须逐字不变）。
_M2_NORTH_BASELINE = (
    "你沿着北边的通道动身，脚底的石板缝隙里积着陈年的灰，石阶上浮着一层薄薄的潮气。"
    "走了不多时，哨站地窖的气息扑面而来，光线也跟着暗了一截。"
    "你放慢脚步站定，打量着这里的环境。"
)

#: 12 步 story 胜利链（与 replay 基线/玩家验收一致）。
_STORY_FLOW: tuple[str, ...] = (
    "向东走进入口",
    "对守夜人老兵说信号灯的事",
    "拿起火把",
    "向东走进铁匠铺",
    "拿起铜线",
    "拿起铜线",
    "打造信号火把",
    "向西走",
    "向北走进地窖",
    "拿起火石",
    "向南走",
    "向上走",
)

_CARD: dict[str, Any] = {
    "schema_version": "1.0",
    "name": "灰烬旅人",
    "attributes": {"str": 10, "dex": 12, "con": 11, "int": 9, "wis": 10, "cha": 8, "max_hp": 18},
    "hp": 7,
    "starting_items": ["item.potion"],
    "background": "从雪线那头走来，旧伤未愈，想在天亮前找条活路。",
    "appearance": None,
    "source": "manual",
    "generated_by": None,
}
_UNKNOWN_CARD_ID = "card_01J9Z3K4Q7X8Y9Z0ABCDEFGHJZ"


# ---------------------------------------------------------------------------
# 装配与请求助手（自有实现，不复用 backend 测试 helper）
# ---------------------------------------------------------------------------
def _dispose(app: FastAPI) -> None:
    """释放组合根内 SQLite Engine 的文件句柄（Windows 临时文件收尾）。"""
    console.dispose_app_engine(app)


def _build_app(monkeypatch: pytest.MonkeyPatch, db_path: Path) -> FastAPI:
    """把 ``TEXTGAME_DB_URL`` 指向临时库后重建生产组合根（默认离线）。"""
    monkeypatch.setenv("TEXTGAME_DB_URL", f"sqlite:///{db_path.as_posix()}")
    get_settings.cache_clear()
    import textgame.main as main_module

    _dispose(main_module.app)
    return main_module._build_app()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """每个用例独立的临时 SQLite 路径。"""
    return tmp_path / "qa-s2.db"


def _new_key(prefix: str) -> str:
    """唯一写幂等键（8..128 字符，ASCII）。"""
    return f"{prefix}-{uuid.uuid4().hex}"


def _create(client: TestClient, scenario: str) -> tuple[str, int]:
    """创建对局 → ``(game_id, version)``。"""
    response = client.post(
        "/api/v1/games",
        headers={"Idempotency-Key": _new_key("qa-s2-create")},
        json={"seed": 42, "scenario": scenario},
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
    """提交一个回合（HTTP 200 断言）→ ``(payload, 下一版本)``。"""
    response = client.post(
        "/api/v1/turns",
        headers={"Idempotency-Key": _new_key("qa-s2-turn")},
        json={"game_id": game_id, "input": text, "expected_version": version},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    return payload, int(payload["version"])


def _turn_ok(
    client: TestClient, game_id: str, version: int, text: str
) -> tuple[dict[str, Any], int]:
    """成功回合（``ok=True`` 断言）。"""
    payload, next_version = _turn(client, game_id, version, text)
    assert payload["ok"] is True, (text, payload)
    return payload, next_version


def _replay(client: TestClient, game_id: str) -> list[dict[str, Any]]:
    """只读 replay 事件列表（200 断言）。"""
    response = client.get(f"/api/v1/games/{game_id}/replay", params={"from_seq": 1, "limit": 500})
    assert response.status_code == 200, response.text
    events: list[dict[str, Any]] = response.json()["events"]
    return events


def _hint(result: dict[str, Any]) -> str:
    """失败回合全部 ``effects[].hint`` 拼串。"""
    return " ".join(str(effect.get("hint", "")) for effect in result.get("effects") or [])


def _sentences(text: str) -> list[str]:
    """按中文句末标点切句（空段丢弃）。"""
    return [sentence for sentence in _SENTENCE_SPLIT.split(text) if sentence]


def _adjacent_duplicates(text: str) -> list[str]:
    """相邻完全相同的句子（空列表 = 无重复）。"""
    sentences = _sentences(text)
    return [
        sentences[index]
        for index in range(1, len(sentences))
        if sentences[index] == sentences[index - 1] and sentences[index].strip() != ""
    ]


def _walk(client: TestClient, game_id: str, version: int, commands: tuple[str, ...]) -> int:
    """依次提交成功回合，返回最后版本。"""
    for command in commands:
        _payload, version = _turn_ok(client, game_id, version, command)
    return version


def _save_card(client: TestClient) -> str:
    """保存一张合法 manual 人物卡 → ``card_id``。"""
    response = client.post(
        "/api/v1/character-cards",
        headers={"Idempotency-Key": _new_key("qa-s2-card")},
        json=_CARD,
    )
    assert response.status_code == 201, response.text
    return str(response.json()["card_id"])


# ---------------------------------------------------------------------------
# P2-4 缺材料建议优先（AC-09c）
# ---------------------------------------------------------------------------
def test_craft_focus_forge_missing_torch_names_location_and_is_executable(
    monkeypatch: pytest.MonkeyPatch, db_path: Path
) -> None:
    """铁匠铺缺火把：hint 点名火把+入口，suggested[0] 提交后**真的**移动到入口。"""
    app = _build_app(monkeypatch, db_path)
    try:
        with TestClient(app) as client:
            game_id, version = _create(client, "story")
            version = _walk(
                client,
                game_id,
                version,
                ("向东走进入口", "向东走进铁匠铺", "拿起铜线", "拿起铜线"),
            )
            rejected, rejected_version = _turn(client, game_id, version, "打造信号火把")
            hint = _hint(rejected)
            suggestions = list(rejected.get("suggested_actions") or [])
            assert rejected["ok"] is False
            assert rejected["events"] == []
            assert rejected_version == version
            assert "缺少材料" in hint and "火把" in hint
            assert "废弃哨站·入口" in hint, "hint 必须点名火把所在房间（玩家可读位置）"
            assert suggestions, "缺材料失败必须给可执行建议"
            assert len(suggestions) <= 4 and len(set(suggestions)) == len(suggestions)
            first = suggestions[0]
            assert "火把" in first or "入口" in first, first
            assert _INTERNAL_ID.search(hint + " " + " ".join(suggestions)) is None
            moved, _ = _turn_ok(client, game_id, rejected_version, first)
            view = _view(client, game_id)
            assert moved["ok"] is True
            assert view["room"]["title"] == "废弃哨站·入口", "suggested[0] 应当可执行到火把所在房间"
    finally:
        _dispose(app)


def test_craft_focus_current_room_suggests_pick_up(
    monkeypatch: pytest.MonkeyPatch, db_path: Path
) -> None:
    """入口打造（火把就在本房间地上）：hint 说明就在这间屋，suggested[0]「拿起火把」可执行。"""
    app = _build_app(monkeypatch, db_path)
    try:
        with TestClient(app) as client:
            game_id, version = _create(client, "story")
            version = _walk(client, game_id, version, ("向东走进入口",))
            rejected, rejected_version = _turn(client, game_id, version, "打造信号火把")
            hint = _hint(rejected)
            suggestions = list(rejected.get("suggested_actions") or [])
            assert rejected["ok"] is False and rejected_version == version
            assert "火把" in hint and "这间屋" in hint
            assert suggestions and suggestions[0].startswith("拿起") and "火把" in suggestions[0]
            took, _ = _turn_ok(client, game_id, rejected_version, suggestions[0])
            view = _view(client, game_id)
            assert took["ok"] is True
            assert "火把" in [item["name"] for item in view["inventory"]]
    finally:
        _dispose(app)


def test_non_craft_failure_suggestions_stay_legacy(
    monkeypatch: pytest.MonkeyPatch, db_path: Path
) -> None:
    """非 craft 失败（无北出口）：hint/建议保持修复前口径，不被材料聚焦污染。"""
    app = _build_app(monkeypatch, db_path)
    try:
        with TestClient(app) as client:
            game_id, version = _create(client, "story")
            version = _walk(client, game_id, version, ("向东走进入口", "向东走进铁匠铺"))
            rejected, rejected_version = _turn(client, game_id, version, "向北走")
            hint = _hint(rejected)
            suggestions = list(rejected.get("suggested_actions") or [])
            assert rejected["ok"] is False and rejected_version == version
            assert "没有往北的路" in hint
            assert "火把在" not in hint
            assert suggestions and suggestions[0].startswith("向西")
    finally:
        _dispose(app)


def test_success_view_suggestions_not_polluted_by_failure_focus(
    monkeypatch: pytest.MonkeyPatch, db_path: Path
) -> None:
    """成功回合后的 ``/view.suggested_actions`` 仍是常规引导（无失败专用措辞）。"""
    app = _build_app(monkeypatch, db_path)
    try:
        with TestClient(app) as client:
            game_id, version = _create(client, "story")
            payload, version = _turn_ok(client, game_id, version, "向东走进入口")
            assert payload["ok"] is True
            view = _view(client, game_id)
            suggestions = list(view.get("suggested_actions") or [])
            assert suggestions, "成功回合后应给下一步建议"
            assert any("火把" in action for action in suggestions)
            joined = " ".join(suggestions)
            assert "可以从下面的建议里挑一个" not in joined
            assert _INTERNAL_ID.search(joined) is None
            # 只读回合（零事件）同样不改变成功建议口径。
            _turn_ok(client, game_id, version, "环顾四周")
            view_after_look = _view(client, game_id)
            assert view_after_look["suggested_actions"]
    finally:
        _dispose(app)


# ---------------------------------------------------------------------------
# AC-35 细看 / 对话文案去重
# ---------------------------------------------------------------------------
def _assert_inspect_text(label: str, text: str, keywords: tuple[str, ...]) -> None:
    """一屏细看文本的通用验收：长度/无相邻重复句/关键词/无内部 ID。"""
    assert _MIN_TEXT <= len(text) <= _MAX_TEXT, f"{label} 长度 {len(text)} 越界"
    duplicates = _adjacent_duplicates(text)
    assert duplicates == [], f"{label} 存在相邻重复句: {duplicates}"
    for keyword in keywords:
        assert keyword in text, f"{label} 缺少「{keyword}」"
    assert _INTERNAL_ID.search(text) is None, f"{label} 泄漏内部 ID"


def test_inspect_narrative_dedupe_and_determinism(
    monkeypatch: pytest.MonkeyPatch, db_path: Path
) -> None:
    """story 6 对象 + m2 2 对象的细看文案：无相邻重复句、80–150、两次渲染逐字相同。"""
    app = _build_app(monkeypatch, db_path)
    try:
        with TestClient(app) as client:
            story_id, version = _create(client, "story")
            version = _walk(client, story_id, version, ("向东走进入口",))
            texts: dict[str, str] = {}
            for command, keywords in (
                ("细看火把", ("火把", "松脂")),
                ("细看守夜人老兵", ("守夜人老兵",)),
            ):
                payload, version = _turn_ok(client, story_id, version, command)
                _assert_inspect_text(command, payload["text"], keywords)
                texts[command] = payload["text"]
            version = _walk(client, story_id, version, ("向北走进地窖",))
            for command, keywords in (
                ("细看火石", ("火石", "火星")),
                ("细看治疗药水", ("治疗药水", "木塞")),
            ):
                payload, version = _turn_ok(client, story_id, version, command)
                _assert_inspect_text(command, payload["text"], keywords)
                texts[command] = payload["text"]
            version = _walk(client, story_id, version, ("向南走", "向东走进铁匠铺"))
            for command, keywords in (
                ("细看铜线", ("铜线", "暗红")),
                ("细看锻锤", ("锻锤", "锤面")),
            ):
                payload, version = _turn_ok(client, story_id, version, command)
                _assert_inspect_text(command, payload["text"], keywords)
                texts[command] = payload["text"]
            # 确定性：同一对象再细看一次，文本逐字一致（只读回合，版本不变）。
            repeat, _ = _turn_ok(client, story_id, version, "细看锻锤")
            assert repeat["text"] == texts["细看锻锤"]

            # m2 旧场景同样去重（且不影响四方向/golden 路径）。
            m2_id, m2_version = _create(client, "m2")
            for command, keywords in (
                ("细看火把", ("火把", "松脂")),
                ("细看治疗药水", ("治疗药水",)),
            ):
                payload, m2_version = _turn_ok(client, m2_id, m2_version, command)
                _assert_inspect_text(command, payload["text"], keywords)
    finally:
        _dispose(app)


def test_talk_reply_frame_not_duplicated_and_legacy_m2_unchanged(
    monkeypatch: pytest.MonkeyPatch, db_path: Path
) -> None:
    """story reply 分支不再重复「沉默片刻，抬眼看你」；m2 旧对话 83 字逐字不变。"""
    app = _build_app(monkeypatch, db_path)
    try:
        with TestClient(app) as client:
            story_id, version = _create(client, "story")
            version = _walk(client, story_id, version, ("向东走进入口",))
            briefed, version = _turn_ok(client, story_id, version, "对守夜人老兵说信号灯的事")
            idle, version = _turn_ok(client, story_id, version, "对守夜人老兵说你好")
            for label, text in (("briefed", briefed["text"]), ("idle", idle["text"])):
                assert "沉默片刻，抬眼看你" not in text, f"{label} 仍残留重复框架句"
                assert "老兵抬起眼" in text, f"{label} 应保留 NPC 回话正文"
                assert _adjacent_duplicates(text) == [], f"{label} 存在相邻重复句"
                assert _MIN_TEXT <= len(text) <= _MAX_TEXT
                assert _INTERNAL_ID.search(text) is None

            # m2 旧场景（无 result.reply）分支保持原文案：demo 基线 83 字 + 原框架句。
            m2_id, m2_version = _create(client, "m2")
            legacy, _ = _turn_ok(client, m2_id, m2_version, "对老哨兵问好")
            assert len(legacy["text"]) == 83, legacy["text"]
            assert "老哨兵沉默片刻，抬眼打量你" in legacy["text"]
    finally:
        _dispose(app)


# ---------------------------------------------------------------------------
# P2-5 移动叙事 / 只读回合零写入
# ---------------------------------------------------------------------------
def test_movement_up_down_natural_and_four_directions_pinned(
    monkeypatch: pytest.MonkeyPatch, db_path: Path
) -> None:
    """up/down 用爬梯文案；m2 north 逐字等于 HEAD 基线（四方向模板不得被误改）。"""
    app = _build_app(monkeypatch, db_path)
    try:
        with TestClient(app) as client:
            story_id, version = _create(client, "story")
            version = _walk(client, story_id, version, ("向东走进入口",))
            up_first, version = _turn_ok(client, story_id, version, "向上走")
            down, version = _turn_ok(client, story_id, version, "向下走")
            up_second, _ = _turn_ok(client, story_id, version, "向上走")
            for label, text, room in (
                ("up", up_first["text"], "哨站瞭望塔"),
                ("down", down["text"], "废弃哨站·入口"),
            ):
                assert "上边" not in text and "下边" not in text, f"{label} 仍有生硬方向词"
                assert "窄梯" in text, f"{label} 未使用爬梯文案"
                assert room in text
                assert _MIN_TEXT <= len(text) <= _MAX_TEXT, f"{label} 长度 {len(text)}"
            assert up_second["text"] == up_first["text"], "同一移动文案应确定性一致"

            m2_id, m2_version = _create(client, "m2")
            north, _ = _turn_ok(client, m2_id, m2_version, "向北走")
            assert north["text"] == _M2_NORTH_BASELINE, "m2 四方向模板被改动（golden 风险）"
    finally:
        _dispose(app)


def test_read_only_turns_write_no_events_or_replay(
    monkeypatch: pytest.MonkeyPatch, db_path: Path
) -> None:
    """看/背包/细看：零事件、不写 replay、版本与回合数不变（前端中性徽章的数据依据）。"""
    app = _build_app(monkeypatch, db_path)
    try:
        with TestClient(app) as client:
            game_id, version = _create(client, "m2")
            before_view = _view(client, game_id)
            before_events = len(_replay(client, game_id))
            for command in ("环顾四周", "查看背包", "细看火把"):
                payload, version_after = _turn(client, game_id, version, command)
                assert payload["ok"] is True, (command, payload)
                assert payload["events"] == [], f"{command} 不应产生事件"
                assert version_after == version, f"{command} 不应推进版本"
                assert len(_replay(client, game_id)) == before_events
                current = _view(client, game_id)
                assert current["turn"] == before_view["turn"]
                assert current["version"] == before_view["version"]
    finally:
        _dispose(app)


# ---------------------------------------------------------------------------
# console 按卡开局（P2/OSS）：真实子进程黑盒
# ---------------------------------------------------------------------------
def _console_env() -> dict[str, str]:
    """CLI 子进程环境：离线、UTF-8 输出/输入、不串父进程 DB。"""
    environment = dict(os.environ)
    environment.pop("TEXTGAME_DB_URL", None)
    environment["TEXTGAME_LLM_ENABLED"] = "false"
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    return environment


def _run_console(argv: list[str], stdin_text: str = "", timeout: float = 120.0) -> Any:
    """运行真实 ``python -m textgame.console`` 子进程（stdout/stderr 分开捕获）。"""
    return subprocess.run(
        [sys.executable, "-m", "textgame.console", *argv],
        input=stdin_text,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        cwd=str(REPO_ROOT),
        env=_console_env(),
        timeout=timeout,
        check=False,
    )


def test_console_card_id_starts_game_from_card_and_verifies_by_replay(
    monkeypatch: pytest.MonkeyPatch, db_path: Path
) -> None:
    """两阶段黑盒：HTTP 存卡 → CLI ``--card-id`` 开局 → 重开 app 用 replay 验 card_id。"""
    app = _build_app(monkeypatch, db_path)
    try:
        with TestClient(app) as client:
            card_id = _save_card(client)
    finally:
        _dispose(app)

    completed = _run_console(["--db", str(db_path), "--card-id", card_id], "环顾四周\n:quit\n")
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "Traceback" not in output
    assert "已按人物卡开局" in output, output
    assert "废弃哨站·入口" in output, output

    app = _build_app(monkeypatch, db_path)
    try:
        with TestClient(app) as client:
            games = client.get("/api/v1/games").json()["games"]
            assert len(games) == 1, games
            game_id = str(games[0]["game_id"])
            events = _replay(client, game_id)
            created = next(event for event in events if event["method"] == "game_created")
            view = _view(client, game_id)
    finally:
        _dispose(app)

    assert created["params"]["card_id"] == card_id
    assert games[0]["scenario"] == "m2"
    assert (view["hp"], view["max_hp"]) == (7, 18), "首屏 HP 必须来自人物卡"
    assert "治疗药水" in [item["name"] for item in view["inventory"]]


def test_console_list_cards_and_error_paths_are_friendly(
    monkeypatch: pytest.MonkeyPatch, db_path: Path
) -> None:
    """``--list-cards`` 输出卡名+用法；未知卡/参数冲突 exit 1 中文无 traceback 且不建局。"""
    app = _build_app(monkeypatch, db_path)
    try:
        with TestClient(app) as client:
            card_id = _save_card(client)
    finally:
        _dispose(app)

    listed = _run_console(["--db", str(db_path), "--list-cards"])
    listed_output = listed.stdout + listed.stderr
    assert listed.returncode == 0, listed_output
    assert "灰烬旅人" in listed_output
    assert card_id in listed_output
    assert "--card-id" in listed_output

    unknown = _run_console(["--db", str(db_path), "--card-id", _UNKNOWN_CARD_ID])
    unknown_output = unknown.stdout + unknown.stderr
    assert unknown.returncode == 1, unknown_output
    assert "Traceback" not in unknown_output
    assert "找不到" in unknown_output or "人物卡" in unknown_output

    conflict = _run_console(
        ["--db", str(db_path), "--card-id", _UNKNOWN_CARD_ID, "--scenario", "m3"]
    )
    conflict_output = conflict.stdout + conflict.stderr
    assert conflict.returncode == 1, conflict_output
    assert "Traceback" not in conflict_output
    assert "不能与" in conflict_output

    # 未知卡/冲突都不应产生对局（库里仍只有卡、没有 game）。
    app = _build_app(monkeypatch, db_path)
    try:
        with TestClient(app) as client:
            assert client.get("/api/v1/games").json()["games"] == []
    finally:
        _dispose(app)


# ---------------------------------------------------------------------------
# AC-33 旧档兼容
# ---------------------------------------------------------------------------
_LEGACY_PARAM_KEYS = ("scenario", "card_id", "snapshot_version")


def _downgrade_save_to_legacy(db_path: Path, game_id: str) -> None:
    """把生产存档改写为旧格式：事件载荷去新字段 + 状态 JSON 去 quests（哈希重算）。"""
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

        for table in ("game_state_current", "snapshots"):
            state_row = connection.execute(
                f"SELECT state_json FROM {table} WHERE game_id = ?", (game_id,)
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
    finally:
        connection.close()


def test_legacy_save_without_scenario_field_is_readable(
    monkeypatch: pytest.MonkeyPatch, db_path: Path
) -> None:
    """旧档：/games、/view、/replay 可读、场景按状态推断 m2、可继续回合、console 可发现。"""
    app = _build_app(monkeypatch, db_path)
    try:
        with TestClient(app) as client:
            game_id, version = _create(client, "m2")
            payload, _ = _turn_ok(client, game_id, version, "拿起火把")
            assert payload["ok"] is True
    finally:
        _dispose(app)

    _downgrade_save_to_legacy(db_path, game_id)

    app = _build_app(monkeypatch, db_path)
    try:
        with TestClient(app) as client:
            snapshot = client.get(f"/api/v1/games/{game_id}")
            assert snapshot.status_code == 200, snapshot.text
            snapshot_body = snapshot.json()
            view = _view(client, game_id)
            events = _replay(client, game_id)
            created = next(event for event in events if event["method"] == "game_created")
            rows = client.get("/api/v1/games").json()["games"]
            resumed = client.post(
                "/api/v1/turns",
                headers={"Idempotency-Key": _new_key("qa-s2-legacy-resume")},
                json={
                    "game_id": game_id,
                    "input": "查看背包",
                    "expected_version": snapshot_body["version"],
                },
            )
    finally:
        _dispose(app)

    assert snapshot_body["scenario"] == "m2"
    assert view["scenario"] == "m2"
    assert view["room"]["title"] == "废弃哨站·入口"
    assert view["goal"] is None
    assert "scenario" not in created["params"]
    assert len(events) >= 2
    assert len(rows) == 1 and rows[0]["scenario"] == "m2"
    assert resumed.status_code == 200 and resumed.json()["ok"] is True

    db_url = f"sqlite:///{db_path.as_posix()}"
    assert console.latest_saved_game_id(db_url) == game_id
    assert console.saved_scenario(db_url, game_id) == "m2"


# ---------------------------------------------------------------------------
# AC-34 / AC-10d 数据面（前端「通关总结 + 结局入冒险记录」依赖的字段）
# ---------------------------------------------------------------------------
def test_story_ending_view_supports_summary_and_ending_history(
    monkeypatch: pytest.MonkeyPatch, db_path: Path
) -> None:
    """12 步胜利链 → /view：ended、turn=13、hp 12/20、goal 3/3、ending_text、无建议。"""
    app = _build_app(monkeypatch, db_path)
    try:
        with TestClient(app) as client:
            game_id, version = _create(client, "story")
            version = _walk(client, game_id, version, _STORY_FLOW)
            view = _view(client, game_id)
            snapshot = client.get(f"/api/v1/games/{game_id}").json()
            events = _replay(client, game_id)
            final_text = str(view.get("ending_text") or "")
    finally:
        _dispose(app)

    assert view["status"] == "ended"
    assert view["turn"] == 13
    assert (view["hp"], view["max_hp"]) == (12, 20)
    objectives = view["goal"]["objectives"]
    assert len(objectives) == 3 and all(item["done"] is True for item in objectives)
    assert final_text and _INTERNAL_ID.search(final_text) is None
    assert view["suggested_actions"] == []
    assert view["room"]["title"] == "哨站瞭望塔"
    assert snapshot["status"] == "ended" and snapshot["ending_text"] == final_text
    assert len(events) == 13
    # AC-10d：终局回合叙事仍是正常一屏（结局正文由前端从 /view.ending_text 派生展示）。
    last_narrative = [
        str(event["result"].get("narrative", ""))
        for event in reversed(events)
        if isinstance(event.get("result"), dict) and event["result"].get("narrative")
    ]
    assert last_narrative, "replay 应带叙事文本源"
    assert _MIN_TEXT <= len(last_narrative[0]) <= _MAX_TEXT
    assert _adjacent_duplicates(last_narrative[0]) == []
