"""示例内容包逐文件校验（N1-035–036）：``examples/content-pack/``。

覆盖：目录/文件约束（纯数据、无脚本、大小上限）、README 入口文档、**每个 JSON 逐文件** 的
domain 校验（场景 ``parse_definition`` v2 / 世界书 ``parse_import_payload`` / 人物卡
``CharacterCard`` + 建局映射）、v2 条件与 flag 引用覆盖，以及**真实 HTTP 端点**验收：
``/scenarios/validate|import``、``/world-books/validate|import``、``/character-cards/validate``
保存与开局，并把两个场景各跑通一条胜利路径（含多结局分支的失败路径）。
全部离线：生产组合根 + 临时 SQLite，不联网（Q9）。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from textgame.config.settings import get_settings
from textgame.domain.character_card import CharacterCard
from textgame.domain.user_scenario import parse_definition
from textgame.domain.world_book import parse_import_payload
from textgame.services.card_service import build_card_initial_state

pytestmark = pytest.mark.integration

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PACK_ROOT = _REPO_ROOT / "examples" / "content-pack"
_SCENARIO_DIR = _PACK_ROOT / "scenarios"
_WORLD_BOOK_DIR = _PACK_ROOT / "world-books"
_CARD_DIR = _PACK_ROOT / "character-cards"

#: 场景包顶层字段白名单（v2；Pydantic ``extra=forbid`` 已保证无未知字段，这里显式列白名单）。
_SCENARIO_TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "slug",
        "meta",
        "player",
        "rooms",
        "items",
        "npcs",
        "flags",
        "goal",
        "endings",
        "suggestions",
    }
)
#: 人物卡恰好 9 字段（与第 5 章 §5.7 契约一致）。
_CARD_KEYS = frozenset(
    {
        "schema_version",
        "name",
        "attributes",
        "hp",
        "starting_items",
        "background",
        "appearance",
        "source",
        "generated_by",
    }
)
#: 世界书（项目简化 schema）条目允许字段（与 ``parse_entry_extras`` 的已知字段一致）。
_WORLD_BOOK_ENTRY_KEYS = frozenset(
    {"uid", "keys", "key", "content", "text", "constant", "order", "selective", "enabled"}
)
#: v2 条件谓词/组合子键集合（与 ``Condition`` 字段一致，``not`` 是 JSON 键）。
_CONDITION_KEYS = frozenset(
    {
        "in_room",
        "has_item",
        "has_item_tag",
        "npc_state",
        "hp_lte",
        "hp_gte",
        "flag",
        "objective_done",
        "has_item_tag_count",
        "item_in_room",
        "npc_in_room",
        "turn_gte",
        "turn_lte",
        "world_time_gte",
        "world_time_lte",
        "all",
        "any",
        "not",
        "always",
    }
)


def _scenario_files() -> list[Path]:
    """内容包内全部场景 JSON（按文件名稳定排序）。"""
    return sorted(_SCENARIO_DIR.glob("*.json"))


def _world_book_files() -> list[Path]:
    """内容包内全部世界书 JSON。"""
    return sorted(_WORLD_BOOK_DIR.glob("*.json"))


def _card_files() -> list[Path]:
    """内容包内全部人物卡 JSON。"""
    return sorted(_CARD_DIR.glob("*.json"))


def _read_payload(path: Path) -> dict[str, Any]:
    """读取 UTF-8 JSON 对象（内容包全部为对象根）。"""
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict), f"{path} 根必须是对象"
    return payload


def _condition_predicates(value: Any, found: set[str]) -> None:
    """递归收集条件树里出现过的谓词/组合子键（``{"键": ...}`` 单键对象）。"""
    if isinstance(value, dict):
        if len(value) == 1:
            key = next(iter(value))
            if key in _CONDITION_KEYS:
                found.add(key)
        for item in value.values():
            _condition_predicates(item, found)
    elif isinstance(value, list):
        for item in value:
            _condition_predicates(item, found)


def _condition_sections(payload: dict[str, Any]) -> list[Any]:
    """场景里所有可放条件的槽位（flags/goal/endings/suggestions 的 when）。"""
    sections: list[Any] = list(payload.get("flags") or [])
    goal = payload.get("goal")
    if isinstance(goal, dict):
        sections.extend(goal.get("objectives") or [])
    sections.extend(payload.get("endings") or [])
    sections.extend(payload.get("suggestions") or [])
    return sections


# ---------------------------------------------------------------------------
# 目录 / 文档 / 纯数据约束
# ---------------------------------------------------------------------------
def test_pack_layout_has_required_content() -> None:
    """目录结构：≥2 场景、≥1 世界书、≥1 人物卡、README；且只含 JSON/README（纯数据）。"""
    scenarios = _scenario_files()
    books = _world_book_files()
    cards = _card_files()

    assert len(scenarios) >= 2, "内容包至少需要 2 个可玩场景"
    assert len(books) >= 1, "内容包至少需要 1 本世界书"
    assert len(cards) >= 1, "内容包至少需要 1 张人物卡"
    assert (_PACK_ROOT / "README.md").is_file()

    files = [path for path in _PACK_ROOT.rglob("*") if path.is_file()]
    assert files
    for path in files:
        assert path.suffix in {".json", ".md"}, f"内容包只允许 JSON/README 纯数据：{path}"
        if path.suffix == ".json":
            assert path.stat().st_size <= 256 * 1024, f"JSON 超过 256KB 上限：{path}"


def test_pack_readme_documents_one_click_entry() -> None:
    """README 必须给出双平台一键脚本、自检开关与全部示例文件名（玩家入口可发现）。"""
    readme = (_PACK_ROOT / "README.md").read_text(encoding="utf-8")

    assert "quickstart-content-pack.ps1" in readme
    assert "quickstart-content-pack.sh" in readme
    assert "-Check" in readme and "--check" in readme
    for path in [*_scenario_files(), *_world_book_files(), *_card_files()]:
        assert path.name in readme, f"README 未登记 {path.name}"


def test_pack_json_files_have_expected_top_level_shape_only() -> None:
    """每个 JSON 顶层字段都在白名单内（无未知字段）；文件为 UTF-8 且非空。"""
    for path in [*_scenario_files(), *_world_book_files(), *_card_files()]:
        raw = path.read_bytes()
        assert raw, f"空文件：{path}"
        payload = _read_payload(path)
        if path in _scenario_files():
            assert set(payload) == _SCENARIO_TOP_LEVEL_KEYS, path
        elif path in _world_book_files():
            assert set(payload) == {"name", "description", "entries"}, path
            for entry in payload["entries"]:
                assert set(entry) <= _WORLD_BOOK_ENTRY_KEYS, (path, entry)
        else:
            assert set(payload) == _CARD_KEYS, path


# ---------------------------------------------------------------------------
# 场景：domain 校验（v2 / 零 warning / v2 条件覆盖 / 多结局）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", _scenario_files(), ids=lambda path: path.stem)
def test_scenario_is_playable_v2_definition(path: Path) -> None:
    """每个场景：v2 合法、零错误零告警、summary 6 键、两次解析结果一致（确定性）。"""
    raw = path.read_bytes()

    parsed = parse_definition(raw)

    assert parsed.ok, (path.name, [issue.as_dict() for issue in parsed.errors])
    assert parsed.errors == ()
    assert parsed.warnings == (), (path.name, [issue.as_dict() for issue in parsed.warnings])
    assert parsed.definition is not None
    definition = parsed.definition
    assert definition.schema_version == 2
    assert set(parsed.summary) == {
        "rooms",
        "items",
        "npcs",
        "objectives",
        "endings",
        "suggestions",
    }
    assert parsed.summary["rooms"] >= 3
    assert len(definition.flags) >= 5
    assert len(definition.endings) >= 2
    assert definition.goal is not None and len(definition.goal.objectives) >= 2

    again = parse_definition(raw)
    assert again.summary == parsed.summary
    assert [flag.key for flag in again.definition.flags] == [flag.key for flag in definition.flags]


@pytest.mark.parametrize("path", _scenario_files(), ids=lambda path: path.stem)
def test_scenario_covers_v2_conditions_and_multi_endings(path: Path) -> None:
    """每个场景实际用到 v2 条件（flag/目标进度/时间/物品等），且 flag 均被引用。"""
    payload = _read_payload(path)
    predicates: set[str] = set()
    _condition_predicates(_condition_sections(payload), predicates)

    assert {"flag", "all", "not"} <= predicates, (path.name, predicates)
    declared_flags = {flag["key"] for flag in payload.get("flags") or []}
    referenced_flags = {
        node["flag"] for node in _walk_single_key_dicts(_condition_sections(payload), "flag")
    }
    assert declared_flags == referenced_flags, (
        path.name,
        "未引用 flag",
        declared_flags - referenced_flags,
    )

    endings = payload.get("endings") or []
    kinds = {ending["kind"] for ending in endings}
    assert kinds == {"win", "lose"}, f"{path.name} 需要 win/lose 多结局"
    priorities = [ending["priority"] for ending in endings]
    assert len(set(priorities)) == len(priorities), f"{path.name} 结局 priority 需互不相同"

    expected: dict[str, set[str]] = {
        "ashen-lighthouse": {
            "turn_gte",
            "has_item_tag_count",
            "item_in_room",
            "npc_in_room",
            "hp_lte",
            "objective_done",
        },
        "mirror-well": {"world_time_gte", "objective_done", "hp_lte"},
    }
    assert expected[payload["slug"]] <= predicates, (payload["slug"], predicates)


def _walk_single_key_dicts(values: Any, key: str) -> list[dict[str, Any]]:
    """递归找回「包含指定键的 dict」（用于统计条件里引用的 flag key）。"""
    found: list[dict[str, Any]] = []
    if isinstance(values, dict):
        if key in values:
            found.append(values)
        for item in values.values():
            found.extend(_walk_single_key_dicts(item, key))
    elif isinstance(values, list):
        for item in values:
            found.extend(_walk_single_key_dicts(item, key))
    return found


# ---------------------------------------------------------------------------
# 世界书 / 人物卡：domain 校验
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", _world_book_files(), ids=lambda path: path.stem)
def test_world_book_is_valid_and_clean(path: Path) -> None:
    """世界书可被导入格式识别：条目非空、零告警、每条形如 ``{keys, content}``。"""
    book = parse_import_payload(_read_payload(path))

    assert book.name.strip()
    assert len(book.entries) >= 8
    assert book.warnings == (), (path.name, [warning.message for warning in book.warnings])
    for entry in book.entries:
        assert entry.keys, f"条目 {entry.uid} 缺少触发关键词"
        assert entry.content.strip(), f"条目 {entry.uid} 缺少设定正文"


@pytest.mark.parametrize("path", _card_files(), ids=lambda path: path.stem)
def test_character_card_is_valid_and_buildable(path: Path) -> None:
    """人物卡通过模型校验，且能映射为可开局初始状态（起始物品模板均已注册）。"""
    payload = _read_payload(path)

    card = CharacterCard.model_validate(payload)
    initial_state = build_card_initial_state(
        payload, card_id="card_content_pack_check", save_seed="save-42"
    )

    assert card.source == "manual"
    assert set(card.attributes) == {"str", "dex", "con", "int", "wis", "cha", "max_hp"}
    assert initial_state["characters"]
    player = next(iter(initial_state["characters"].values()))
    assert player["name"] == card.name
    assert player["hp"] == card.hp
    assert len(player["inventory"]) == len(card.starting_items)


# ---------------------------------------------------------------------------
# HTTP 端点：validate / import / 建局 / 通关（生产组合根 + 临时 SQLite，不联网）
# ---------------------------------------------------------------------------
def _build_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    """生产组合根（临时 SQLite、显式离线；与 test_user_scenario_flow 同口径）。"""
    import textgame.main as main_module

    monkeypatch.setenv("TEXTGAME_DB_URL", f"sqlite:///{(tmp_path / 'content-pack.db').as_posix()}")
    monkeypatch.setenv("TEXTGAME_LLM_ENABLED", "false")
    monkeypatch.setenv("TEXTGAME_FREE_CRAFT_ENABLED", "false")
    monkeypatch.setenv("TEXTGAME_LLM_CREDENTIALS_FILE", str(tmp_path / "llm.credentials.json"))
    get_settings.cache_clear()
    return main_module._build_app()


@pytest.fixture
def app_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    """生产组合根测试客户端（每个用例独立临时库，不联网）。"""
    app = _build_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        yield client


def _import_scenarios(client: TestClient) -> dict[str, str]:
    """逐文件 validate + import 全部场景，返回 ``{slug: scenario_id}``。"""
    ids: dict[str, str] = {}
    for index, path in enumerate(_scenario_files(), start=1):
        raw = path.read_bytes()
        validated = client.post("/api/v1/scenarios/validate", content=raw)
        assert validated.status_code == 200, validated.text
        body = validated.json()
        assert body["ok"] is True, (path.name, body["errors"])
        assert body["warnings"] == [], (path.name, body["warnings"])
        imported = client.post(
            "/api/v1/scenarios",
            headers={"Idempotency-Key": f"content-pack-scenario-{index:02d}"},
            content=raw,
        )
        assert imported.status_code == 201, imported.text
        payload = imported.json()
        ids[str(payload["slug"])] = str(payload["scenario_id"])
    return ids


def _import_world_books(client: TestClient) -> list[str]:
    """逐文件 validate + multipart import 全部世界书，返回 world_book_id 列表。"""
    ids: list[str] = []
    for index, path in enumerate(_world_book_files(), start=1):
        payload = _read_payload(path)
        validated = client.post("/api/v1/world-books/validate", json={"payload": payload})
        assert validated.status_code == 200, validated.text
        body = validated.json()
        assert body["valid"] is True, (path.name, body["errors"])
        assert body["warnings"] == [], (path.name, body["warnings"])
        imported = client.post(
            "/api/v1/world-books/import",
            headers={"Idempotency-Key": f"content-pack-world-book-{index:02d}"},
            files={"file": (path.name, path.read_bytes(), "application/json")},
        )
        assert imported.status_code == 201, imported.text
        ids.append(str(imported.json()["world_book_id"]))
    return ids


def _import_cards(client: TestClient) -> list[str]:
    """逐文件 validate + 保存全部人物卡，返回 card_id 列表。"""
    ids: list[str] = []
    for index, path in enumerate(_card_files(), start=1):
        raw = path.read_bytes()
        validated = client.post("/api/v1/character-cards/validate", content=raw)
        assert validated.status_code == 200, validated.text
        body = validated.json()
        assert body["valid"] is True, (path.name, body["errors"])
        saved = client.post(
            "/api/v1/character-cards",
            headers={"Idempotency-Key": f"content-pack-card-{index:02d}"},
            content=raw,
        )
        assert saved.status_code == 201, saved.text
        ids.append(str(saved.json()["card_id"]))
    return ids


def _new_game(client: TestClient, payload: dict[str, Any], *, key: str) -> dict[str, Any]:
    """建局（201 断言）并返回响应体。"""
    response = client.post("/api/v1/games", headers={"Idempotency-Key": key}, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _turn(client: TestClient, game_id: str, version: int, text: str, *, key: str) -> dict[str, Any]:
    """提交回合并断言 HTTP 200（业务失败由 ``ok`` 字段表达）。"""
    response = client.post(
        "/api/v1/turns",
        headers={"Idempotency-Key": key},
        json={"game_id": game_id, "input": text, "expected_version": version},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _play_script(
    client: TestClient, game_id: str, version: int, script: list[str], *, prefix: str
) -> dict[str, Any]:
    """按脚本连续回合；全部 ``ok=True`` 后返回最终 ``/view``。"""
    for index, text in enumerate(script, start=1):
        result = _turn(client, game_id, version, text, key=f"{prefix}-turn-{index:03d}")
        assert result["ok"] is True, (text, result)
        version = result["version"]
    view = client.get(f"/api/v1/games/{game_id}/view")
    assert view.status_code == 200, view.text
    return view.json()


def test_pack_validate_and_import_via_http_endpoints(app_client: TestClient) -> None:
    """真实端点：全部 validate 通过、全部 import 成功、列表可见（零告警）。"""
    scenario_ids = _import_scenarios(app_client)
    world_book_ids = _import_world_books(app_client)
    card_ids = _import_cards(app_client)

    assert len(scenario_ids) >= 2
    assert world_book_ids and card_ids
    scenario_list = app_client.get("/api/v1/scenarios?limit=50").json()
    assert scenario_list["total"] >= 2
    listed_slugs = {item["slug"] for item in scenario_list["scenarios"]}
    assert {"ashen-lighthouse", "mirror-well"} <= listed_slugs
    book_list = app_client.get("/api/v1/world-books?limit=50").json()
    assert book_list["total"] >= 1
    card_list = app_client.get("/api/v1/character-cards?limit=50").json()
    assert card_list["total"] >= 1


def test_pack_scenarios_playable_end_to_end(app_client: TestClient) -> None:
    """两个场景各跑通一条胜利路径；世界书绑定生效；多结局失败分支可达；人物卡可开局。"""
    scenario_ids = _import_scenarios(app_client)
    world_book_ids = _import_world_books(app_client)
    card_ids = _import_cards(app_client)

    # 1) 灰烬灯塔（绑定世界书）：问守塔人 → 下地窖取灯油 → 回塔梯取灯芯 → 登灯室 → 胜利。
    lighthouse = _new_game(
        app_client,
        {
            "scenario_id": scenario_ids["ashen-lighthouse"],
            "seed": 7,
            "world_book_id": world_book_ids[0],
        },
        key="content-pack-game-lighthouse",
    )
    lighthouse_view = _play_script(
        app_client,
        str(lighthouse["game_id"]),
        int(lighthouse["version"]),
        [
            "向北走进塔梯",
            "向守塔人打听灯怎么点亮",
            "向下走进地窖",
            "拿起灯油",
            "向上走回塔梯",
            "拿起灯芯",
            "向北走进灯室",
        ],
        prefix="content-pack-lighthouse",
    )
    assert lighthouse_view["status"] == "ended"
    assert "整座灯塔亮了起来" in (lighthouse_view["ending_text"] or "")
    assert [item["done"] for item in lighthouse_view["goal"]["objectives"]] == [
        True,
        True,
        True,
    ]
    assert lighthouse_view["world_book"]["world_book_id"] == world_book_ids[0]
    assert lighthouse_view["lore"]["active_entries"], "绑定世界书后应激活设定条目"

    # 2) 镜井：打听 → 拿麻绳 → 下井 → 返回荒院 → 胜利（掉血 4 是「下去过」的状态证据）。
    mirror = _new_game(
        app_client,
        {"scenario_id": scenario_ids["mirror-well"], "seed": 7},
        key="content-pack-game-mirror",
    )
    mirror_view = _play_script(
        app_client,
        str(mirror["game_id"]),
        int(mirror["version"]),
        [
            "向北走进井口",
            "问卜者井底有什么",
            "拿起麻绳",
            "向下走进井底",
            "向上走回井口",
            "向南走进荒院",
        ],
        prefix="content-pack-mirror",
    )
    assert mirror_view["status"] == "ended"
    assert "卜者替你拍掉肩上的井灰" in (mirror_view["ending_text"] or "")

    # 3) 镜井失败分支：拿古镜即走向「镜中的你」（优先级高于胜利结局，确定性）。
    doomed = _new_game(
        app_client,
        {"scenario_id": scenario_ids["mirror-well"], "seed": 11},
        key="content-pack-game-mirror-doomed",
    )
    doomed_view = _play_script(
        app_client,
        str(doomed["game_id"]),
        int(doomed["version"]),
        ["向北走进井口", "拿起麻绳", "向下走进井底", "拿起古镜"],
        prefix="content-pack-mirror-doomed",
    )
    assert doomed_view["status"] == "ended"
    assert "镜子里的人还站在井底" in (doomed_view["ending_text"] or "")

    # 4) 人物卡开局：卡库保存的卡可直接建局，起始物品/HP 生效（默认 m2 场景）。
    card_game = _new_game(app_client, {"card_id": card_ids[0]}, key="content-pack-game-card")
    card_view = app_client.get(f"/api/v1/games/{card_game['game_id']}/view")
    assert card_view.status_code == 200, card_view.text
    view_body = card_view.json()
    assert view_body["hp"] == 14
    assert len(view_body["inventory"]) == 3
