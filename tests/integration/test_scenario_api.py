"""玩家自上传场景库 API 集成测试（契约：docs/design/user-scenario-modules.md §3.4/§6）。

覆盖：validate（200 精确形状/结构化 path/无副作用）、import（JSON 原文 + multipart 兼容、
201 精确形状、必带 Idempotency-Key、同键幂等/异参 409、非法 422、超 256KB 413）、列表
（分页/q 搜索/非法分页 422）、详情（未知/已删 404）、软删（204 空 body、重复删除 404、
列表隐藏、runtime 索引保留）、**导出（v1/v2 roundtrip：导出→再导入逐字段一致/确定性/
零写入/404/不泄漏库内字段）**、OpenAPI 端点、未装配 scenario_service 时 500 信封。
全部使用内存 SQLite + 真实 ``ScenarioService``/``SqliteScenarioRepository``，不联网。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from textgame.api.app import create_app
from textgame.domain.models import GameState
from textgame.domain.user_scenario import MAX_DEFINITION_BYTES
from textgame.persistence.database import init_schema, prepare_db
from textgame.persistence.scenario_repository import SqliteScenarioRepository
from textgame.services.scenario_service import ScenarioService

pytestmark = [pytest.mark.integration]

_IMPORT_PATH = "/api/v1/scenarios"
_VALIDATE_PATH = "/api/v1/scenarios/validate"
_SAVE_SEED = "save-scenario-api-0001"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_LANTERN_VIGIL = _REPO_ROOT / "examples" / "scenarios" / "lantern-vigil.json"
_MOONLIT_MARKET = _REPO_ROOT / "examples" / "scenarios" / "moonlit-market.json"


@dataclass
class ScenarioHarness:
    """一套场景库 API 测试装配：HTTP 客户端 + 服务 + 底层 Engine（断言零事件用）。"""

    client: TestClient
    service: ScenarioService
    engine: Engine


def _tiny() -> dict[str, Any]:
    """最小可玩定义（与 examples/scenarios/tiny-tower.json 一致）。"""
    return {
        "schema_version": 1,
        "slug": "tiny-tower",
        "meta": {"title": "小塔", "author": "示例", "description": "两房间最小可玩场景"},
        "player": {"name": "旅人", "hp": 10, "max_hp": 10, "location": "gate", "inventory": []},
        "rooms": [
            {
                "key": "gate",
                "name": "塔门",
                "description": "一扇虚掩的木门，门缝里透出风。",
                "exits": {"north": "hall"},
            },
            {
                "key": "hall",
                "name": "塔厅",
                "description": "螺旋楼梯向上，墙上挂着一盏没点亮的灯。",
                "exits": {"south": "gate"},
                "items": ["flint"],
            },
        ],
        "items": [{"key": "flint", "name": "火石", "description": "一块干燥的火石。"}],
        "npcs": [],
        "goal": {
            "title": "爬上塔顶",
            "objectives": [
                {
                    "key": "climb",
                    "text": "带着火石进入塔厅",
                    "when": {"all": [{"in_room": "hall"}, {"has_item": "flint"}]},
                }
            ],
        },
        "endings": [
            {
                "key": "win",
                "kind": "win",
                "title": "你登了上去",
                "text": "塔顶的风很大，但灯终于亮了。",
                "priority": 10,
                "when": {"all": [{"in_room": "hall"}, {"has_item": "flint"}]},
            }
        ],
    }


def _variant(*, title: str, author: str, slug: str) -> dict[str, Any]:
    """同结构、不同 meta 的合法定义（列表/搜索用例）。"""
    definition = _tiny()
    definition["slug"] = slug
    definition["meta"] = {"title": title, "author": author, "description": "变体场景"}
    return definition


def _raw(definition: dict[str, Any] | None = None) -> bytes:
    """定义 → UTF-8 JSON 原文。"""
    return json.dumps(_tiny() if definition is None else definition, ensure_ascii=False).encode(
        "utf-8"
    )


def _import(
    client: TestClient,
    raw: bytes,
    *,
    key: str | None = "scn-api-import-key-0001",
):
    """``POST /api/v1/scenarios``（JSON 原文 body；``key=None`` 时不带幂等键）。"""
    headers = {"Idempotency-Key": key} if key is not None else {}
    return client.post(_IMPORT_PATH, headers=headers, content=raw)


def _assert_error(response: Any, status_code: int, error_code: str) -> dict[str, Any]:
    """断言统一失败信封（HTTP 状态 + ``{ok:false, error_code, message, violations?}``）。"""
    assert response.status_code == status_code, response.text
    body: dict[str, Any] = response.json()
    assert body.get("ok") is False
    assert body.get("error_code") == error_code
    assert isinstance(body.get("message"), str) and body["message"]
    assert set(body) <= {"ok", "error_code", "message", "violations"}
    return body


def _imported_id(client: TestClient, raw: bytes, *, key: str) -> str:
    """导入并断言 201，返回 scenario_id。"""
    resp = _import(client, raw, key=key)
    assert resp.status_code == 201, resp.text
    return str(resp.json()["scenario_id"])


@pytest.fixture()
def scenario_harness() -> Iterator[ScenarioHarness]:
    """内存 SQLite + 真实 ``ScenarioService`` + TestClient（不联网、独立库）。"""
    engine, _factory = prepare_db("sqlite:///:memory:")
    init_schema(engine)
    service = ScenarioService(SqliteScenarioRepository(engine))
    with TestClient(create_app(scenario_service=service)) as client:
        yield ScenarioHarness(client=client, service=service, engine=engine)
    engine.dispose()


# ---------------------------------------------------------------------------
# validate（只读预检；body = JSON 原文，无幂等键）
# ---------------------------------------------------------------------------
def test_validate_returns_exact_contract_shape(scenario_harness: ScenarioHarness) -> None:
    """合法定义：200 ``{ok,errors,warnings,summary}``（顶层/子结构键集合精确）。"""
    resp = scenario_harness.client.post(_VALIDATE_PATH, content=_raw())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"ok", "errors", "warnings", "summary"}
    assert body["ok"] is True
    assert body["errors"] == [] and body["warnings"] == []
    assert set(body["summary"]) == {
        "rooms",
        "items",
        "npcs",
        "objectives",
        "endings",
        "suggestions",
    }
    assert body["summary"]["rooms"] == 2
    assert body["summary"]["suggestions"] == 0


def test_validate_invalid_definition_returns_path_errors(
    scenario_harness: ScenarioHarness,
) -> None:
    """非法定义仍 200：``ok=false``，errors 项精确为 ``{path,message}`` 且带 JSON 路径。"""
    definition = _tiny()
    definition["rooms"][0]["exits"]["east"] = "hub2"  # type: ignore[index]
    resp = scenario_harness.client.post(_VALIDATE_PATH, content=_raw(definition))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert body["errors"]
    assert set(body["errors"][0]) == {"path", "message"}
    assert any(
        error["path"] == "rooms[0].exits.east" and "hub2" in error["message"]
        for error in body["errors"]
    )
    assert set(body["summary"]) >= {"rooms", "items", "npcs"}


def test_validate_does_not_require_key_and_does_not_persist(
    scenario_harness: ScenarioHarness,
) -> None:
    """validate 无需 Idempotency-Key、不写库（连续两次后列表仍为空）。"""
    for _ in range(2):
        resp = scenario_harness.client.post(_VALIDATE_PATH, content=_raw())
        assert resp.status_code == 200
    assert scenario_harness.client.get(_IMPORT_PATH).json()["total"] == 0


def test_validate_malformed_json_returns_root_error(scenario_harness: ScenarioHarness) -> None:
    """非法 JSON：200 + ``ok=false``、error.path == "$"。"""
    resp = scenario_harness.client.post(_VALIDATE_PATH, content=b"{not json")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["errors"][0]["path"] == "$"


# ---------------------------------------------------------------------------
# import（201；JSON 原文 + multipart 兼容；幂等键必带）
# ---------------------------------------------------------------------------
def test_import_json_raw_201_then_get_detail(scenario_harness: ScenarioHarness) -> None:
    """JSON 原文导入 201：精确 5 字段；详情含原始定义对象；列表项 10 字段。"""
    resp = _import(scenario_harness.client, _raw(), key="scn-api-import-key-0001")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert set(body) == {"scenario_id", "slug", "title", "summary", "warnings"}
    assert body["slug"] == "tiny-tower"
    assert body["title"] == "小塔"
    assert set(body["summary"]) == {
        "rooms",
        "items",
        "npcs",
        "objectives",
        "endings",
        "suggestions",
    }
    scenario_id = str(body["scenario_id"])

    detail_resp = scenario_harness.client.get(f"{_IMPORT_PATH}/{scenario_id}")
    assert detail_resp.status_code == 200, detail_resp.text
    detail = detail_resp.json()
    assert set(detail) == {
        "scenario_id",
        "slug",
        "title",
        "author",
        "description",
        "schema_version",
        "created_at",
        "definition",
        "summary",
        "warnings",
    }
    assert detail["definition"] == _tiny()
    assert detail["author"] == "示例"

    listed = scenario_harness.client.get(_IMPORT_PATH).json()
    assert set(listed) == {"scenarios", "total"}
    assert listed["total"] == 1
    assert set(listed["scenarios"][0]) == {
        "scenario_id",
        "slug",
        "title",
        "author",
        "description",
        "schema_version",
        "created_at",
        "summary",
        "warnings",
    }


def test_import_multipart_is_accepted_for_frontend_compat(
    scenario_harness: ScenarioHarness,
) -> None:
    """multipart/form-data 的 ``file`` 部件（前端现状）也被接受：201 且入相同模型。"""
    resp = scenario_harness.client.post(
        _IMPORT_PATH,
        headers={"Idempotency-Key": "scn-api-multipart-0001"},
        files={"file": ("scenario.json", _raw(), "application/json")},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert set(body) == {"scenario_id", "slug", "title", "summary", "warnings"}
    assert body["slug"] == "tiny-tower"


def test_import_requires_valid_idempotency_key(scenario_harness: ScenarioHarness) -> None:
    """缺 Idempotency-Key / 键太短 → 422 INVALID_INPUT 信封。"""
    missing = _import(scenario_harness.client, _raw(), key=None)
    _assert_error(missing, 422, "INVALID_INPUT")

    short = _import(scenario_harness.client, _raw(), key="short")
    _assert_error(short, 422, "INVALID_INPUT")
    assert scenario_harness.client.get(_IMPORT_PATH).json()["total"] == 0


def test_import_idempotent_replay_and_conflict(scenario_harness: ScenarioHarness) -> None:
    """同键同参 201 回放首次响应；同键异参 409 CONFLICT。"""
    raw = _raw()
    first = _import(scenario_harness.client, raw, key="scn-api-idem-0001")
    assert first.status_code == 201, first.text
    replay = _import(scenario_harness.client, raw, key="scn-api-idem-0001")
    assert replay.status_code == 201
    assert replay.json() == first.json()

    conflict = _import(
        scenario_harness.client,
        _raw(_variant(title="另一场景", author="甲", slug="another-scene")),
        key="scn-api-idem-0001",
    )
    body = _assert_error(conflict, 409, "CONFLICT")
    assert "Idempotency-Key" in str(body["message"])
    assert scenario_harness.client.get(_IMPORT_PATH).json()["total"] == 1


def test_import_invalid_definition_422_with_paths(scenario_harness: ScenarioHarness) -> None:
    """非法定义 → 422 INVALID_INPUT，``violations`` 为 ``[{path,message}]``。"""
    definition = _tiny()
    definition["rooms"][0]["exits"]["east"] = "hub2"  # type: ignore[index]
    resp = _import(scenario_harness.client, _raw(definition), key="scn-api-bad-0001")
    body = _assert_error(resp, 422, "INVALID_INPUT")
    violations = body.get("violations")
    assert isinstance(violations, list) and violations
    assert set(violations[0]) == {"path", "message"}
    assert any(item["path"] == "rooms[0].exits.east" for item in violations)
    assert scenario_harness.client.get(_IMPORT_PATH).json()["total"] == 0


def test_import_unknown_attribute_422_with_field_paths(scenario_harness: ScenarioHarness) -> None:
    """R-02：未知玩家属性在导入阶段返回 422（不是建局 500），路径指向 player.attributes。"""
    definition = _tiny()
    definition["player"]["attributes"] = {"luck": 1}
    resp = _import(scenario_harness.client, _raw(definition), key="scn-api-bad-attr-0001")
    body = _assert_error(resp, 422, "INVALID_INPUT")
    violations = body.get("violations")
    assert isinstance(violations, list) and violations
    assert any(item["path"] == "player.attributes" for item in violations)
    assert scenario_harness.client.get(_IMPORT_PATH).json()["total"] == 0


def test_import_invalid_json_and_duplicate_keys_422(scenario_harness: ScenarioHarness) -> None:
    """非法 JSON / 重复键 → 422 INVALID_INPUT 信封（不落库）。"""
    bad_json = _import(scenario_harness.client, b"{not json", key="scn-api-bad-0002")
    _assert_error(bad_json, 422, "INVALID_INPUT")

    duplicate = _raw()[:-1] + b', "slug": "tiny-tower"}'
    dup = _import(scenario_harness.client, duplicate, key="scn-api-bad-0003")
    body = _assert_error(dup, 422, "INVALID_INPUT")
    assert "重复键" in str(body.get("violations"))
    assert scenario_harness.client.get(_IMPORT_PATH).json()["total"] == 0


def test_import_too_large_413(scenario_harness: ScenarioHarness) -> None:
    """导入超过 256KB → 413 + INVALID_INPUT 信封（契约特例）。"""
    too_big = b'{"x": "' + b"a" * (MAX_DEFINITION_BYTES + 1024) + b'"}'
    resp = _import(scenario_harness.client, too_big, key="scn-api-big-0001")
    _assert_error(resp, 413, "INVALID_INPUT")


def test_import_does_not_write_events(scenario_harness: ScenarioHarness) -> None:
    """场景库不参与事件溯源：导入/删除不产生 events 行。"""
    _import(scenario_harness.client, _raw(), key="scn-api-events-0001")
    scenario_harness.client.delete(f"{_IMPORT_PATH}/scn_00000000000000000000000000")
    with scenario_harness.engine.connect() as connection:
        count = connection.execute(text("SELECT COUNT(*) FROM events")).scalar()
    assert count == 0


# ---------------------------------------------------------------------------
# 列表：分页 / 搜索
# ---------------------------------------------------------------------------
def test_list_pagination_and_search(scenario_harness: ScenarioHarness) -> None:
    """``limit``/``offset`` 分页 + ``q`` 搜索 title/author；非法分页参数 422。"""
    _import(scenario_harness.client, _raw(), key="scn-api-list-0001")
    _import(
        scenario_harness.client,
        _raw(_variant(title="雪原", author="甲", slug="snow-field")),
        key="scn-api-list-0002",
    )
    _import(
        scenario_harness.client,
        _raw(_variant(title="地窖", author="乙", slug="cellar-scene")),
        key="scn-api-list-0003",
    )

    page = scenario_harness.client.get(_IMPORT_PATH, params={"limit": 2, "offset": 0}).json()
    assert page["total"] == 3 and len(page["scenarios"]) == 2
    page2 = scenario_harness.client.get(_IMPORT_PATH, params={"limit": 2, "offset": 2}).json()
    assert page2["total"] == 3 and len(page2["scenarios"]) == 1

    found = scenario_harness.client.get(_IMPORT_PATH, params={"q": "雪"}).json()
    assert found["total"] == 1 and found["scenarios"][0]["title"] == "雪原"
    by_author = scenario_harness.client.get(_IMPORT_PATH, params={"q": "甲"}).json()
    assert by_author["total"] == 1
    by_slug = scenario_harness.client.get(_IMPORT_PATH, params={"q": "tiny"}).json()
    assert by_slug["total"] == 1

    _assert_error(
        scenario_harness.client.get(_IMPORT_PATH, params={"limit": 101}), 422, "INVALID_INPUT"
    )
    _assert_error(
        scenario_harness.client.get(_IMPORT_PATH, params={"offset": -1}), 422, "INVALID_INPUT"
    )


# ---------------------------------------------------------------------------
# 详情 / 软删
# ---------------------------------------------------------------------------
def test_get_unknown_scenario_404(scenario_harness: ScenarioHarness) -> None:
    """未知/格式非法场景 id → 404 NOT_FOUND 信封。"""
    _assert_error(
        scenario_harness.client.get(f"{_IMPORT_PATH}/scn_00000000000000000000000000"),
        404,
        "NOT_FOUND",
    )
    _assert_error(scenario_harness.client.get(f"{_IMPORT_PATH}/not-a-scenario"), 404, "NOT_FOUND")


def test_delete_returns_204_empty_body_and_hides_scenario(
    scenario_harness: ScenarioHarness,
) -> None:
    """DELETE → 204 空 body；列表/详情不可见；重复删除 404。"""
    scenario_id = _imported_id(scenario_harness.client, _raw(), key="scn-api-delete-key-0001")
    resp = scenario_harness.client.delete(f"{_IMPORT_PATH}/{scenario_id}")
    assert resp.status_code == 204, resp.text
    assert resp.content == b""
    assert scenario_harness.client.get(_IMPORT_PATH).json()["total"] == 0
    _assert_error(scenario_harness.client.get(f"{_IMPORT_PATH}/{scenario_id}"), 404, "NOT_FOUND")
    _assert_error(scenario_harness.client.delete(f"{_IMPORT_PATH}/{scenario_id}"), 404, "NOT_FOUND")


def test_delete_keeps_runtime_index_for_existing_saves(
    scenario_harness: ScenarioHarness,
) -> None:
    """软删后 runtime 索引必须保留：已开局存档仍能解析到场景钩子（reload 也不摘除）。"""
    scenario_id = _imported_id(scenario_harness.client, _raw(), key="scn-api-runtime-0001")
    payload = scenario_harness.service.build_game_payload(scenario_id, _SAVE_SEED)
    state = GameState.model_validate(payload["initial_state"])
    assert scenario_harness.service.runtime.resolve(state) is not None

    assert scenario_harness.client.delete(f"{_IMPORT_PATH}/{scenario_id}").status_code == 204
    assert scenario_harness.service.runtime.resolve(state) is not None
    scenario_harness.service.reload()
    hooks = scenario_harness.service.runtime.resolve(state)
    assert hooks is not None and hooks.scenario_id == scenario_id


def test_delete_does_not_require_idempotency_key(scenario_harness: ScenarioHarness) -> None:
    """DELETE 契约未要求幂等键：不带该头也能 204。"""
    scenario_id = _imported_id(scenario_harness.client, _raw(), key="scn-api-delete-key-0002")
    resp = scenario_harness.client.delete(f"{_IMPORT_PATH}/{scenario_id}")
    assert resp.status_code == 204 and resp.content == b""


# ---------------------------------------------------------------------------
# 导出（分享 = 单文件交换；只读、零写入、可直接再导入）
# ---------------------------------------------------------------------------
def _export_path(scenario_id: str) -> str:
    """导出端点路径（测试辅助）。"""
    return f"{_IMPORT_PATH}/{scenario_id}/export"


def test_export_v1_returns_reimportable_single_file(
    scenario_harness: ScenarioHarness,
) -> None:
    """v1 导出：200 JSON 附件、字段逐字保留、可作为 import body 再导入（逐字段一致）。"""
    client = scenario_harness.client
    original_id = _imported_id(client, _raw(), key="scn-share-v1-import")
    original_detail = client.get(f"{_IMPORT_PATH}/{original_id}").json()

    resp = client.get(_export_path(original_id))
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.headers["content-disposition"] == 'attachment; filename="tiny-tower.json"'
    exported = resp.json()
    assert exported == _tiny()  # schema_version/全部字段逐字保留
    assert set(exported) == {
        "schema_version",
        "slug",
        "meta",
        "player",
        "rooms",
        "items",
        "npcs",
        "goal",
        "endings",
    }

    # 导出内容本身仍是合法定义，可直接再导入（同/另一进程，分享=文件交换）。
    validated = client.post(_VALIDATE_PATH, content=resp.content)
    assert validated.status_code == 200
    assert validated.json()["ok"] is True

    reimported = _import(client, resp.content, key="scn-share-v1-reimport")
    assert reimported.status_code == 201, reimported.text
    second_id = str(reimported.json()["scenario_id"])
    assert second_id != original_id  # 每次导入生成新的内部 ID（slug 可重复）
    second_detail = client.get(f"{_IMPORT_PATH}/{second_id}").json()
    assert second_detail["definition"] == exported
    assert second_detail["schema_version"] == 1
    assert second_detail["summary"] == original_detail["summary"]
    assert second_detail["title"] == original_detail["title"]
    assert second_detail["author"] == original_detail["author"]


@pytest.mark.parametrize(
    ("path", "slug", "objective_count"),
    [
        (_LANTERN_VIGIL, "lantern-vigil", 3),
        (_MOONLIT_MARKET, "moonlit-market", 2),
    ],
)
def test_export_v2_roundtrip_preserves_schema_version_and_flags(
    scenario_harness: ScenarioHarness,
    path: Path,
    slug: str,
    objective_count: int,
) -> None:
    """v2 导出→再导入：schema_version=2、flags/全部字段与 summary 逐字段一致，且仍可开局。"""
    client = scenario_harness.client
    raw = path.read_bytes()
    original = _import(client, raw, key=f"scn-share-v2-import-{slug}")
    assert original.status_code == 201, original.text
    original_id = str(original.json()["scenario_id"])

    resp = client.get(_export_path(original_id))
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-disposition"] == f'attachment; filename="{slug}.json"'
    exported = resp.json()
    assert exported == json.loads(raw.decode("utf-8"))
    assert exported["schema_version"] == 2
    assert len(exported["flags"]) == (5 if slug == "lantern-vigil" else 3)
    assert client.post(_VALIDATE_PATH, content=resp.content).json()["ok"] is True

    reimported = _import(client, resp.content, key=f"scn-share-v2-reimport-{slug}")
    assert reimported.status_code == 201, reimported.text
    second_id = str(reimported.json()["scenario_id"])
    second_detail = client.get(f"{_IMPORT_PATH}/{second_id}").json()
    assert second_detail["definition"] == exported
    assert second_detail["schema_version"] == 2
    assert second_detail["summary"] == client.get(f"{_IMPORT_PATH}/{original_id}").json()["summary"]
    assert second_detail["summary"]["objectives"] == objective_count
    assert second_detail["title"] == exported["meta"]["title"]
    assert second_detail["author"] == exported["meta"]["author"]
    # 分享文件导入后仍可建局（玩家槽位/房间派生 ID 与 v2 语义不变）。
    payload = scenario_harness.service.build_game_payload(second_id, _SAVE_SEED)
    assert payload["scenario_title"] == exported["meta"]["title"]
    assert payload["initial_state"]["characters"]


def test_export_is_read_only_deterministic_and_does_not_write_events(
    scenario_harness: ScenarioHarness,
) -> None:
    """导出只读：两次字节一致、列表/详情零变化、不产生任何事件行。"""
    client = scenario_harness.client
    scenario_id = _imported_id(client, _raw(), key="scn-share-readonly-import")
    list_before = client.get(_IMPORT_PATH).json()
    detail_before = client.get(f"{_IMPORT_PATH}/{scenario_id}").json()

    first = client.get(_export_path(scenario_id))
    second = client.get(_export_path(scenario_id))
    assert first.status_code == 200 and second.status_code == 200
    assert first.content == second.content  # 确定性：同一场景导出字节一致

    assert client.get(_IMPORT_PATH).json() == list_before
    assert client.get(f"{_IMPORT_PATH}/{scenario_id}").json() == detail_before
    with scenario_harness.engine.connect() as connection:
        events = connection.execute(text("SELECT COUNT(*) FROM events")).scalar()
    assert events == 0


def test_export_does_not_leak_internal_fields_or_server_paths(
    scenario_harness: ScenarioHarness,
) -> None:
    """导出体只有场景定义：不含 scenario_id/summary/时间戳/服务器路径等库内信息。"""
    client = scenario_harness.client
    scenario_id = _imported_id(client, _raw(), key="scn-share-leak-import")
    body = client.get(_export_path(scenario_id)).content
    text_body = body.decode("utf-8")
    assert scenario_id not in text_body
    assert "scn_" not in text_body
    for forbidden in ("created_at", "deleted_at", "definition_json", "warnings", "summary"):
        assert forbidden not in text_body
    assert str(_REPO_ROOT) not in text_body
    assert set(json.loads(text_body)) == set(_tiny())


def test_export_unknown_or_soft_deleted_scenario_404(
    scenario_harness: ScenarioHarness,
) -> None:
    """未知 ID / 格式非法 ID / 已软删场景 → 404 NOT_FOUND 统一信封。"""
    client = scenario_harness.client
    _assert_error(client.get(_export_path("scn_00000000000000000000000000")), 404, "NOT_FOUND")
    _assert_error(client.get(_export_path("not-a-scenario")), 404, "NOT_FOUND")

    scenario_id = _imported_id(client, _raw(), key="scn-share-delete-import")
    assert client.delete(f"{_IMPORT_PATH}/{scenario_id}").status_code == 204
    _assert_error(client.get(_export_path(scenario_id)), 404, "NOT_FOUND")


def test_export_does_not_require_idempotency_key(scenario_harness: ScenarioHarness) -> None:
    """导出是只读 GET：不带 ``Idempotency-Key`` 也 200（契约上无需幂等键）。"""
    client = scenario_harness.client
    scenario_id = _imported_id(client, _raw(), key="scn-share-nokey-import")
    response = client.get(_export_path(scenario_id))
    assert response.status_code == 200
    assert response.request.headers.get("Idempotency-Key") is None


# ---------------------------------------------------------------------------
# OpenAPI / 未装配服务
# ---------------------------------------------------------------------------
def test_scenarios_endpoints_are_documented(scenario_harness: ScenarioHarness) -> None:
    """live OpenAPI 含场景库端点（含 032 新增导出）；import 声明 requestBody（JSON + multipart）。"""
    paths = scenario_harness.client.app.openapi()["paths"]
    assert set(paths[_IMPORT_PATH]) >= {"get", "post"}
    assert set(paths[_VALIDATE_PATH]) >= {"post"}
    assert set(paths[f"{_IMPORT_PATH}/{{scenario_id}}"]) >= {"get", "delete"}
    assert set(paths[f"{_IMPORT_PATH}/{{scenario_id}}/export"]) >= {"get"}
    import_operation = paths[_IMPORT_PATH]["post"]
    content = import_operation["requestBody"]["content"]
    assert "application/json" in content
    assert "multipart/form-data" in content


def test_missing_scenario_service_returns_500_envelope() -> None:
    """未装配 ``scenario_service`` 时访问场景端点 → 500 INTERNAL 信封（组合根装配错误）。"""
    with TestClient(create_app()) as client:
        resp = client.get(_IMPORT_PATH)
    _assert_error(resp, 500, "INTERNAL")
