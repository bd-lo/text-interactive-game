"""用户上传场景 JSON 端到端：导入 → 建局 → 目标/伤害/结局 → /view 与回放。

用生产组合根（``textgame.main._build_app``）跑真实 HTTP 链路：场景服务、运行时钩子、
事件溯源与查询投影必须串起来。默认离线（模板叙事），零联网。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from textgame.config.settings import get_settings

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EMBER_WATCH = _REPO_ROOT / "examples" / "scenarios" / "ember-watch.json"
_TINY_TOWER = _REPO_ROOT / "examples" / "scenarios" / "tiny-tower.json"
_LANTERN_VIGIL = _REPO_ROOT / "examples" / "scenarios" / "lantern-vigil.json"
_MOONLIT_MARKET = _REPO_ROOT / "examples" / "scenarios" / "moonlit-market.json"

_M2_ENTRY_ROOM_ID = "room_01J9Z3K4Q7X8Y9Z0ABCDEFGHJK"


def _build_app(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    db_name: str,
    isolate_llm: bool = True,
) -> object:
    """用临时 SQLite 构建生产组合根（默认显式关闭 LLM，测试确定性）。"""
    import textgame.main as main_module

    monkeypatch.setenv("TEXTGAME_DB_URL", f"sqlite:///{(tmp_path / db_name).as_posix()}")
    monkeypatch.setenv("TEXTGAME_FREE_CRAFT_ENABLED", "false")
    monkeypatch.setenv("TEXTGAME_LLM_CREDENTIALS_FILE", str(tmp_path / "llm.credentials.json"))
    if isolate_llm:
        monkeypatch.setenv("TEXTGAME_LLM_ENABLED", "false")
    else:
        # 模拟「干净克隆 + 本地开发机」的自动判定路径：无显式开关、无 env key。
        monkeypatch.delenv("TEXTGAME_LLM_ENABLED", raising=False)
        monkeypatch.setenv("TEXTGAME_LLM_API_KEY", "")
    get_settings.cache_clear()
    return main_module._build_app()


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[object]:
    """生产组合根（临时 SQLite + 临时本机凭据文件；LLM 关闭、自由制造关闭）。"""
    yield _build_app(monkeypatch, tmp_path, db_name="scenario-flow.db")


def _import_scenario(client: TestClient, path: Path, key: str) -> dict:
    """按 multipart 导入一份示例场景（与前端现状一致）。"""
    content = path.read_bytes()
    response = client.post(
        "/api/v1/scenarios",
        headers={"Idempotency-Key": key},
        files={"file": (path.name, content, "application/json")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _new_game(client: TestClient, payload: dict, key: str) -> dict:
    """建局并返回 ``{game_id, version}``。"""
    response = client.post(
        "/api/v1/games",
        headers={"Idempotency-Key": key},
        json=payload,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _turn(client: TestClient, game_id: str, version: int, text: str, key: str) -> dict:
    """提交一个回合（HTTP 200 = 请求被受理；ok 字段表示业务成败）。"""
    response = client.post(
        "/api/v1/turns",
        headers={"Idempotency-Key": key},
        json={"game_id": game_id, "input": text, "expected_version": version},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _view(client: TestClient, game_id: str) -> dict:
    """只读玩家视图（目标/结局/HP 等）。"""
    response = client.get(f"/api/v1/games/{game_id}/view")
    assert response.status_code == 200, response.text
    return response.json()


def test_import_and_play_uploaded_scenario_to_victory(app: object) -> None:
    """导入 → 按场景开局 → 对话/移动/拿取 → 目标全完成 → 胜利结局 → 结局后拒绝新回合。"""
    with TestClient(app) as client:  # type: ignore[arg-type]
        imported = _import_scenario(client, _EMBER_WATCH, "flow-import-0001")
        scenario_id = imported["scenario_id"]
        assert imported["slug"] == "ember-watch"
        assert imported["summary"]["rooms"] == 5

        created = _new_game(client, {"scenario_id": scenario_id}, "flow-game-0001")
        game_id = created["game_id"]
        version = created["version"]

        first = _view(client, game_id)
        assert first["scenario"] == "custom"
        assert first["status"] == "active"
        goal = first["goal"]
        assert goal is not None
        assert [item["id"] for item in goal["objectives"]] == ["listen", "gather", "light"]
        assert [item["done"] for item in goal["objectives"]] == [False, False, False]

        # 开局事件参数：scenario=custom 必须记录 scenario_id，出生房间来自场景而非 m2 常量。
        replay = client.get(f"/api/v1/games/{game_id}/replay").json()
        created_event = replay["events"][0]
        assert created_event["params"]["scenario_id"] == scenario_id
        assert created_event["params"]["starting_room_id"] != _M2_ENTRY_ROOM_ID

        script = [
            "向东走进哨站",  # bridge → hub
            "和守夜人说话",  # listen 目标完成（briefed）
            "向北走进地窖",  # hub → cellar
            "拿起火石",  # 拿火石
            "向南走回入口",  # cellar → hub
            "向上登上瞭望塔",  # hub → tower
            "拿起火把",  # 拿火把 → 胜利条件达成（塔+火石+火把）
        ]
        for index, text in enumerate(script, start=1):
            result = _turn(client, game_id, version, text, f"flow-turn-{index:04d}")
            assert result["ok"] is True, (text, result)
            version = result["version"]

        final = _view(client, game_id)
        assert final["status"] == "ended"
        assert final["ending_text"], "终局必须给出结局文本"
        assert [item["done"] for item in final["goal"]["objectives"]] == [True, True, True]

        # 结局后拒绝新回合（读时判定，不写状态）。
        rejected = _turn(client, game_id, version, "再看看", "flow-turn-after-end")
        assert rejected["ok"] is False
        assert rejected["error_code"] == "INVALID_INPUT"


def test_enter_damage_room_until_fallen_ending(app: object) -> None:
    """进入枯井每次 -6 HP；HP 归零触发倒下结局（场景定义驱动，非 story 硬编码）。"""
    with TestClient(app) as client:  # type: ignore[arg-type]
        imported = _import_scenario(client, _EMBER_WATCH, "flow-import-0002")
        created = _new_game(
            client, {"scenario_id": imported["scenario_id"], "seed": 7}, "flow-game-0002"
        )
        game_id = created["game_id"]
        version = created["version"]

        _turn(client, game_id, version, "向东走进哨站", "flow-dmg-0001")
        version += 1
        hp = _view(client, game_id)["hp"]
        assert hp == 14

        for round_index in range(3):
            result = _turn(client, game_id, version, "向下走进枯井", f"flow-dmg-down-{round_index}")
            assert result["ok"] is True, result
            version = result["version"]
            hp = _view(client, game_id)["hp"]
            if round_index < 2:
                assert hp == 14 - 6 * (round_index + 1)
                up = _turn(client, game_id, version, "向上走回入口", f"flow-dmg-up-{round_index}")
                assert up["ok"] is True, up
                version = up["version"]

        # 伤害事件与场景定义里的提示文案都落进事件日志（回放可审计）。
        replay_events = client.get(f"/api/v1/games/{game_id}/replay").json()["events"]
        damage_events = [
            event for event in replay_events if event["event_type"] == "ENVIRONMENT_DAMAGE"
        ]
        assert len(damage_events) == 3
        assert all(event["result"]["hp_delta"] == -6 for event in damage_events)
        assert all(event["result"]["hint"] for event in damage_events)

        final = _view(client, game_id)
        assert final["hp"] == 0
        assert final["status"] == "ended"
        assert final["ending_text"]


def test_import_validation_and_conflicts(app: object) -> None:
    """校验/导入/建局错误路径：非法 JSON 200 带 path；未知场景 404；场景+卡冲突 422。"""
    with TestClient(app) as client:  # type: ignore[arg-type]
        validated = client.post(
            "/api/v1/scenarios/validate",
            content=b"{not-json",
            headers={"Content-Type": "application/json"},
        )
        assert validated.status_code == 200, validated.text
        body = validated.json()
        assert body["ok"] is False
        assert body["errors"][0]["path"] == "$"

        missing = client.post(
            "/api/v1/games",
            headers={"Idempotency-Key": "flow-game-missing-0001"},
            json={"scenario_id": "scn_missing"},
        )
        assert missing.status_code == 404

        imported = _import_scenario(client, _TINY_TOWER, "flow-import-0003")
        conflict = client.post(
            "/api/v1/games",
            headers={"Idempotency-Key": "flow-game-conflict-0001"},
            json={"scenario_id": imported["scenario_id"], "scenario": "m2"},
        )
        assert conflict.status_code == 422
        assert conflict.json()["error_code"] == "INVALID_INPUT"


def test_console_session_can_start_uploaded_scenario(app: object) -> None:
    """终端 ``--scenario-id`` 复用同一 API：GameSession 建局后 /view 场景为 custom。"""
    from textgame.console import GameSession

    with TestClient(app) as client:  # type: ignore[arg-type]
        imported = _import_scenario(client, _TINY_TOWER, "flow-import-0004")
        session = GameSession(client, scenario_id=imported["scenario_id"])
        session.create()
        view = session.view()
        assert view["scenario"] == "custom"
        assert view["goal"] is not None
        assert len(view["goal"]["objectives"]) == 1


def test_settings_endpoint_wired_in_production_app(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """运行期 LLM 设置端点在组合根装配：离线状态可读、保存后热切换、清除回离线。"""
    built = _build_app(monkeypatch, tmp_path, db_name="scenario-settings.db", isolate_llm=False)
    with TestClient(built) as client:  # type: ignore[arg-type]
        status = client.get("/api/v1/settings/llm")
        assert status.status_code == 200, status.text
        assert status.json()["enabled"] is False

        saved = client.put(
            "/api/v1/settings/llm",
            json={"api_key": "sk-flow-test-0001", "model": "deepseek-chat"},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["status"]["enabled"] is True
        assert "sk-flow-test-0001" not in saved.text

        cleared = client.delete("/api/v1/settings/llm")
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["status"]["enabled"] is False


def test_examples_are_valid_scenarios() -> None:
    """仓库内示例场景必须通过领域校验（文档/联调可用性护栏）。"""
    from textgame.domain.user_scenario import parse_definition

    for path in (_TINY_TOWER, _EMBER_WATCH):
        parsed = parse_definition(path.read_bytes())
        assert parsed.ok, (path.name, parsed.errors)
        assert parsed.definition is not None
        assert parsed.definition.meta.title


def test_replay_contains_scenario_events(app: object) -> None:
    """回放端点可读：导入场景建局后的事件序列含 game_created 与移动事件。"""
    with TestClient(app) as client:  # type: ignore[arg-type]
        imported = _import_scenario(client, _TINY_TOWER, "flow-import-0005")
        created = _new_game(client, {"scenario_id": imported["scenario_id"]}, "flow-game-0005")
        result = _turn(
            client, created["game_id"], created["version"], "向北进入塔厅", "flow-replay-0001"
        )
        assert result["ok"] is True, result
        replay = client.get(f"/api/v1/games/{created['game_id']}/replay").json()
        kinds = [event["event_type"] for event in replay["events"]]
        assert kinds[0] == "game_created"
        assert "CHARACTER_MOVED" in kinds
        json.dumps(replay)  # 可 JSON 序列化（无不可序列化对象泄漏）


# ---------------------------------------------------------------------------
# v2（schema_version=2）：flag / 目标进度 / 收集 / 时间 / 多结局
# ---------------------------------------------------------------------------


def test_v2_example_is_valid_and_reports_schema_version() -> None:
    """v2 示例通过领域校验：schema_version=2、flags 生效、summary 6 键不变。"""
    from textgame.domain.user_scenario import parse_definition

    parsed = parse_definition(_LANTERN_VIGIL.read_bytes())
    assert parsed.ok, parsed.errors
    assert parsed.definition is not None
    assert parsed.definition.schema_version == 2
    assert len(parsed.definition.flags) == 5
    # summary 契约冻结：v2 不新增统计键（前端契约测试保持不变）。
    assert set(parsed.summary) == {
        "rooms",
        "items",
        "npcs",
        "objectives",
        "endings",
        "suggestions",
    }
    assert not parsed.warnings  # 示例定义无未使用 flag


def test_v2_flags_goal_progress_and_ending_end_to_end(app: object) -> None:
    """v2 端到端：导入 → 对话使 flag 成立 → 收集计数 → objective_done 链 → 胜利结局。"""
    with TestClient(app) as client:  # type: ignore[arg-type]
        imported = _import_scenario(client, _LANTERN_VIGIL, "flow-v2-import-0001")
        assert imported["summary"]["rooms"] == 3
        detail = client.get(f"/api/v1/scenarios/{imported['scenario_id']}").json()
        assert detail["schema_version"] == 2  # v2 定义原样存/原样回
        assert len(detail["definition"]["flags"]) == 5
        created = _new_game(client, {"scenario_id": imported["scenario_id"]}, "flow-v2-game-0001")
        game_id = created["game_id"]
        version = created["version"]

        first = _view(client, game_id)
        assert first["scenario"] == "custom"
        goal = first["goal"]
        assert [item["id"] for item in goal["objectives"]] == ["listen", "gather", "light"]
        assert [item["done"] for item in goal["objectives"]] == [False, False, False]

        script = [
            "向东走进厅堂",  # gate → hall
            "和灯守说话",  # dialogue_state=briefed → flag keeper_briefed → listen 完成
            "拿起蜡烛",  # 第 1 件 quest 物品
            "向东走进灯室",  # hall → shrine（进入伤害 -2）
            "拿起点灯的灯油",  # 第 2 件 quest 物品 → gather/light 完成 → 胜利
        ]
        for index, text in enumerate(script, start=1):
            result = _turn(client, game_id, version, text, f"flow-v2-turn-{index:04d}")
            assert result["ok"] is True, (text, result)
            version = result["version"]

        final = _view(client, game_id)
        assert final["status"] == "ended"
        assert final["ending_text"]
        assert [item["done"] for item in final["goal"]["objectives"]] == [True, True, True]
        assert final["hp"] == 10  # 初始 12，灯室进入伤害 -2

        replay = client.get(f"/api/v1/games/{game_id}/replay").json()
        kinds = [event["event_type"] for event in replay["events"]]
        assert "DIALOGUE_EXCHANGED" in kinds  # flag 的数据来源
        assert kinds.count("ENVIRONMENT_DAMAGE") == 1

        # 结局后拒绝新回合（与 v1 同一读时判定，不写状态）。
        rejected = _turn(client, game_id, version, "再看看", "flow-v2-turn-after-end")
        assert rejected["ok"] is False
        assert rejected["error_code"] == "INVALID_INPUT"


def test_v2_declared_as_v1_rejected_via_validate_endpoint(app: object) -> None:
    """schema_version=1 声明里出现 v2 字段/谓词 → validate 200 + 结构化 errors（带路径）。"""
    payload = json.loads(_LANTERN_VIGIL.read_text(encoding="utf-8"))
    payload["schema_version"] = 1
    with TestClient(app) as client:  # type: ignore[arg-type]
        response = client.post(
            "/api/v1/scenarios/validate",
            content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["ok"] is False
        paths = {item["path"] for item in body["errors"]}
        assert "flags" in paths
        assert any(path.endswith(".when.flag") for path in paths), paths
        assert any("schema_version=2" in item["message"] for item in body["errors"])


# ---------------------------------------------------------------------------
# 示例场景（分享来源）：全部示例可校验，且规范化（导出同口径）后仍可再导入
# ---------------------------------------------------------------------------
def test_all_share_examples_are_valid_and_reimportable() -> None:
    """4 个示例（v1×2 + v2×2）通过领域校验；model_dump 规范化后再解析仍合法。"""
    from textgame.domain.user_scenario import MAX_DEFINITION_BYTES, parse_definition

    for path in (_TINY_TOWER, _EMBER_WATCH, _LANTERN_VIGIL, _MOONLIT_MARKET):
        raw = path.read_bytes()
        assert len(raw) <= MAX_DEFINITION_BYTES, path.name
        parsed = parse_definition(raw)
        assert parsed.ok, (path.name, parsed.errors)
        assert parsed.definition is not None
        # 「作者/标题/进入方式」写在格式自带的 meta 字段（JSON 不支持注释）。
        assert parsed.definition.meta.title and parsed.definition.meta.author
        assert parsed.definition.meta.description, path.name
        normalized = json.dumps(
            parsed.definition.model_dump(mode="json"), ensure_ascii=False
        ).encode("utf-8")
        again = parse_definition(normalized)
        assert again.ok, (path.name, again.errors)
        assert again.summary == parsed.summary
