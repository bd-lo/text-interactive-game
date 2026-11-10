"""集成契约测试：`web/` 前端类型快照 ↔ live OpenAPI（M6-025 骨架 + M6-027 回放 + M6-026 卡库）。

纯静态读取 + TestClient 实测，**不依赖 Node/pnpm 运行**（CI 的 quality job 不装 Node 也能跑）：

- 前端只用现有端点：`API_PATHS` 必须是 live OpenAPI 路径的子集，且恰为前端已接入的 10 个端点；
- 响应/请求接口字段集合与 OpenAPI schema 一致（字段名逐一比对，OpenAPI 必填项不得在 TS 侧可选）；
- 实测 `POST /api/v1/games` / `GET /view` / `POST /turns` / `GET /replay` / character-cards 的
  真实字段集合与 TS 接口一致（不止文档）；replay 以 `result.narrative` 为回放文本源、seq 升序、
  只读零写入；卡库覆盖 list/validate/import/detail 与 9 字段 CharacterCard；
- 前端源码静态禁入：禁 `v-html`/`innerHTML`/`eval`/`EventSource`/自动定时重试（第 23 章 §23.7、D-M6-6④）。

generate（M6-028）后端并行交付中：若 live OpenAPI 尚无该路径，契约测试断言前端未提前接入并 skip。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from textgame.api.app import create_app
from textgame.persistence.card_repository import SqliteCardRepository
from textgame.persistence.database import init_schema, prepare_db
from textgame.services.card_service import CardService

WEB_ROOT = Path(__file__).resolve().parents[2] / "web"
TYPES_TS = WEB_ROOT / "src" / "api" / "types.ts"
SRC_ROOT = WEB_ROOT / "src"

#: M6-028 生成卡片端点（后端并行交付中；未就绪时前端不得提前接入）
GENERATE_CARD_PATH = "/api/v1/character-cards/generate"

# 前端已接入的现有端点（新增端点须同步扩大并更新本测试）
EXPECTED_FRONTEND_PATHS = {
    "/api/v1/games",
    "/api/v1/games/{game_id}",
    "/api/v1/games/{game_id}/view",
    "/api/v1/games/{game_id}/replay",
    "/api/v1/turns",
    "/api/v1/character-cards",
    "/api/v1/character-cards/schema",
    "/api/v1/character-cards/import",
    "/api/v1/character-cards/validate",
    "/api/v1/character-cards/{card_id}",
}

# TS 接口 → OpenAPI schema（响应；字段集合必须一致）
RESPONSE_INTERFACES = {
    "CreatedGameResponse": "CreatedGameResponse",
    "GameViewResponse": "GameViewResponse",
    "GamePlayerViewResponse": "GamePlayerViewResponse",
    "ItemRef": "ItemRefResponse",
    "NpcRef": "NpcRefResponse",
    "RoomView": "RoomViewResponse",
    "EventRecord": "Event",
    "ReplayEvent": "ReplayEventResponse",
    "ReplayResponse": "ReplayResponse",
    "TurnResult": "TurnResult",
    "CharacterCard": "CharacterCard",
    "CharacterCardSummary": "CharacterCardListItemResponse",
    "CharacterCardListResponse": "CharacterCardListResponse",
    "CharacterCardDetailResponse": "CharacterCardDetailResponse",
    "CharacterCardWarning": "CharacterCardWarningResponse",
    "CharacterCardImportResponse": "CharacterCardImportResponse",
    "CharacterCardSaveResponse": "CharacterCardSaveResponse",
    "CharacterCardValidationError": "CharacterCardValidationErrorResponse",
    "CharacterCardValidateResponse": "CharacterCardValidateResponse",
}

# TS 接口 → OpenAPI schema（请求；字段名 + 可选项必须一致）
REQUEST_INTERFACES = {
    "CreateGameRequest": "CreateGameBody",
    "TurnRequest": "TurnRequestBody",
}

_INTERFACE_RE = re.compile(r"export interface (\w+)(?:<[^>]*>)?\s*\{(.*?)\n\}", re.DOTALL)
_FIELD_RE = re.compile(r"^\s{2}([A-Za-z_][A-Za-z0-9_]*)(\?)?:", re.MULTILINE)
_API_PATH_RE = re.compile(r"([A-Za-z]+):\s*'(/api/v1/[^']+)'")

_FORBIDDEN_SOURCE_PATTERNS = {
    "v-html": re.compile(r"v-html"),
    "innerHTML": re.compile(r"innerHTML"),
    "eval()": re.compile(r"\beval\s*\("),
    "new Function()": re.compile(r"new\s+Function\s*\("),
    "EventSource/SSE": re.compile(r"EventSource"),
    "自动定时重试": re.compile(r"setInterval\s*\("),
}


def _ts_interfaces() -> dict[str, dict[str, bool]]:
    """解析 types.ts：接口名 → {字段名: 是否可选}（只解析顶层扁平字段，够用且无 node 依赖）。"""
    source = TYPES_TS.read_text(encoding="utf-8")
    interfaces: dict[str, dict[str, bool]] = {}
    for name, body in _INTERFACE_RE.findall(source):
        interfaces[name] = {field: optional == "?" for field, optional in _FIELD_RE.findall(body)}
    return interfaces


def _frontend_paths() -> set[str]:
    """types.ts 中 `API_PATHS` 常量声明的端点路径集合。"""
    source = TYPES_TS.read_text(encoding="utf-8")
    return set(dict(_API_PATH_RE.findall(source)).values())


#: 一份合法人物卡（9 字段；第 5 章 §5.7）。
_CARD_ATTRIBUTES = {"str": 12, "dex": 14, "con": 11, "int": 15, "wis": 10, "cha": 8, "max_hp": 30}


def _card(**overrides: object) -> dict[str, object]:
    card: dict[str, object] = {
        "schema_version": "1.0",
        "name": "灰烬旅人",
        "attributes": dict(_CARD_ATTRIBUTES),
        "hp": 30,
        "starting_items": ["item.torch", "item.potion"],
        "background": "在旧矿镇长大的拾荒者。",
        "appearance": None,
        "source": "upload",
        "generated_by": None,
    }
    card.update(overrides)
    return card


@pytest.fixture
def card_client() -> Iterator[TestClient]:
    """内存 SQLite + 真实 CardService 的 TestClient（卡库 live 契约；不联网、独立库）。"""
    engine, _factory = prepare_db("sqlite:///:memory:")
    init_schema(engine)
    service = CardService(SqliteCardRepository(engine))
    with TestClient(create_app(card_service=service)) as client:
        yield client
    engine.dispose()


def _openapi() -> dict:
    return create_app().openapi()


def _schema_fields(schema_name: str) -> dict[str, bool]:
    """OpenAPI schema → {字段名: 是否非必填}。"""
    schemas = _openapi()["components"]["schemas"]
    schema = schemas[schema_name]
    required = set(schema.get("required") or [])
    properties = schema.get("properties") or {}
    return {field: field not in required for field in properties}


def test_frontend_paths_are_subset_of_live_openapi() -> None:
    """前端 API_PATHS 恰为已接入端点（含 live 就绪的 generate），且全部存在于 live OpenAPI。"""
    live_paths = set(_openapi()["paths"])
    frontend_paths = _frontend_paths()
    expected = set(EXPECTED_FRONTEND_PATHS)
    if GENERATE_CARD_PATH in live_paths:
        expected.add(GENERATE_CARD_PATH)
    assert frontend_paths == expected
    assert frontend_paths <= live_paths


def test_response_interfaces_match_openapi_schemas() -> None:
    """响应接口字段集合 = OpenAPI properties；OpenAPI 必填字段在 TS 侧不得标 `?`。"""
    ts = _ts_interfaces()
    for ts_name, schema_name in RESPONSE_INTERFACES.items():
        ts_fields = ts[ts_name]
        openapi_fields = _schema_fields(schema_name)
        assert set(ts_fields) == set(openapi_fields), f"{ts_name} 字段与 {schema_name} 漂移"
        for field, optional in openapi_fields.items():
            if not optional:
                assert ts_fields[field] is False, (
                    f"{ts_name}.{field} 在 OpenAPI 必填，TS 侧不应标记可选"
                )


def test_request_interfaces_match_openapi_schemas() -> None:
    """请求接口字段名与可选性必须与 OpenAPI 完全一致。"""
    ts = _ts_interfaces()
    for ts_name, schema_name in REQUEST_INTERFACES.items():
        assert ts[ts_name] == _schema_fields(schema_name), f"{ts_name} 与 {schema_name} 漂移"


def test_live_created_game_and_view_fields_match_frontend_types(game_harness) -> None:
    """实测响应字段集合与 TS 接口一致（不只比对文档）。"""
    ts = _ts_interfaces()
    client: TestClient = game_harness.client
    created_resp = client.post(
        "/api/v1/games",
        headers={"Idempotency-Key": "frontend-contract-key-0001"},
        json={"seed": 42},
    )
    assert created_resp.status_code == 201, created_resp.text
    created = created_resp.json()
    assert set(created) == set(ts["CreatedGameResponse"])

    view_resp = client.get(f"/api/v1/games/{created['game_id']}/view")
    assert view_resp.status_code == 200, view_resp.text
    view = view_resp.json()
    assert set(view) == set(ts["GamePlayerViewResponse"])
    assert set(view["room"]) == set(ts["RoomView"])
    assert view["room"]["items"], "入口房间应有可见物品，用于断言 ItemRef 字段集合"
    assert set(view["room"]["items"][0]) == set(ts["ItemRef"])
    assert view["room"]["npcs"], "入口房间应有可见 NPC，用于断言 NpcRef 字段集合"
    assert set(view["room"]["npcs"][0]) == set(ts["NpcRef"])


def test_live_turn_result_fields_match_frontend_types(game_harness) -> None:
    """`POST /turns` 实测 TurnResult/Event 字段集合与 TS 接口一致。"""
    ts = _ts_interfaces()
    client: TestClient = game_harness.client
    created = client.post(
        "/api/v1/games",
        headers={"Idempotency-Key": "frontend-contract-key-0002"},
        json={"seed": 42},
    ).json()
    turn_resp = client.post(
        "/api/v1/turns",
        headers={"Idempotency-Key": "frontend-contract-turn-0001"},
        json={
            "game_id": created["game_id"],
            "input": "拿起火把",
            "expected_version": created["version"],
        },
    )
    assert turn_resp.status_code == 200, turn_resp.text
    result = turn_resp.json()
    assert set(result) == set(ts["TurnResult"])
    assert result["ok"] is True
    assert result["events"], "成功的回合应携带事件，用于断言 Event 字段集合"
    assert set(result["events"][0]) == set(ts["EventRecord"])


def test_live_replay_fields_match_frontend_types(game_harness) -> None:
    """`GET /replay` 实测字段集合、seq 升序、narrative 文本源与只读零写入。"""
    ts = _ts_interfaces()
    client: TestClient = game_harness.client
    created = client.post(
        "/api/v1/games",
        headers={"Idempotency-Key": "frontend-contract-key-0003"},
        json={"seed": 42},
    ).json()
    turn_resp = client.post(
        "/api/v1/turns",
        headers={"Idempotency-Key": "frontend-contract-turn-0003"},
        json={
            "game_id": created["game_id"],
            "input": "拿起火把",
            "expected_version": created["version"],
        },
    )
    assert turn_resp.status_code == 200 and turn_resp.json()["ok"] is True

    before = client.get(f"/api/v1/games/{created['game_id']}").json()
    resp = client.get(
        f"/api/v1/games/{created['game_id']}/replay",
        params={"from_seq": 1, "limit": 500},
    )
    assert resp.status_code == 200, resp.text
    page = resp.json()
    assert set(page) == set(ts["ReplayResponse"])
    assert page["from_seq"] == 1
    events = page["events"]
    assert events, "开局 + 一个回合应产生事件"
    for event in events:
        assert set(event) == set(ts["ReplayEvent"])
    seqs = [event["seq"] for event in events]
    assert seqs == sorted(seqs)
    assert page["to_seq"] == events[-1]["seq"]
    narratives = [
        event["result"].get("narrative")
        for event in events
        if isinstance(event["result"].get("narrative"), str) and event["result"]["narrative"]
    ]
    assert narratives, "至少一条事件应以 result.narrative 作为回放文本源"

    # 只读零写入：版本链不变，重复请求结果一致
    after = client.get(f"/api/v1/games/{created['game_id']}").json()
    assert (after["version"], after["turn"]) == (before["version"], before["turn"])
    again = client.get(
        f"/api/v1/games/{created['game_id']}/replay",
        params={"from_seq": 1, "limit": 500},
    ).json()
    assert again == page


def test_card_operations_exist_in_live_openapi() -> None:
    """character-cards 7 端点（5 路径 / 7 操作）与 multipart `file` 声明、games 的 card_id 加法。"""
    paths = _openapi()["paths"]
    assert set(paths["/api/v1/character-cards"]) == {"get", "post"}
    assert set(paths["/api/v1/character-cards/schema"]) == {"get"}
    assert set(paths["/api/v1/character-cards/import"]) == {"post"}
    assert set(paths["/api/v1/character-cards/validate"]) == {"post"}
    assert set(paths["/api/v1/character-cards/{card_id}"]) == {"get", "delete"}

    import_schema = paths["/api/v1/character-cards/import"]["post"]["requestBody"]["content"][
        "multipart/form-data"
    ]["schema"]
    assert "file" in import_schema["properties"]

    create_props = _openapi()["components"]["schemas"]["CreateGameBody"]["properties"]
    assert "card_id" in create_props


def test_generate_endpoint_consistency() -> None:
    """M6-028 generate：路径常量 + 请求/响应类型与 live OpenAPI 一致（未就绪则 skip 并报）。"""
    live_paths = set(_openapi()["paths"])
    frontend_paths = _frontend_paths()
    if GENERATE_CARD_PATH not in live_paths:
        assert GENERATE_CARD_PATH not in frontend_paths
        pytest.skip(
            "generate 端点未交付（M6-028 切片，后端并行交付中）：前端不提前接入，以 live OpenAPI 为准"
        )
    assert GENERATE_CARD_PATH in frontend_paths

    ts = _ts_interfaces()
    # 请求体：字段名 + 可选性一致（brief 必填；schema_version/options 可选）
    assert ts["CardGenerateRequest"] == _schema_fields("CardGenerateBody")
    # 响应：字段集合一致（draft/validation 必填，generated_by 可缺省）
    for ts_name, schema_name in {
        "CardGenerateResponse": "CardGenerateResponse",
        "CardGenerateValidation": "CardGenerateValidation",
    }.items():
        assert set(ts[ts_name]) == set(_schema_fields(schema_name)), (
            f"{ts_name} 与 {schema_name} 漂移"
        )


def test_live_card_lifecycle_fields_match_frontend_types(card_client: TestClient) -> None:
    """卡库实测：schema/validate/import/list/detail 字段集合与 TS 接口一致（9 字段）。"""
    ts = _ts_interfaces()

    schema_resp = card_client.get("/api/v1/character-cards/schema")
    assert schema_resp.status_code == 200
    assert set(schema_resp.json()["properties"]) == set(ts["CharacterCard"])

    valid_resp = card_client.post("/api/v1/character-cards/validate", json=_card())
    assert valid_resp.status_code == 200
    assert set(valid_resp.json()) == set(ts["CharacterCardValidateResponse"])
    assert valid_resp.json()["valid"] is True

    invalid_resp = card_client.post("/api/v1/character-cards/validate", json=_card(hp=999))
    assert invalid_resp.status_code == 200
    invalid = invalid_resp.json()
    assert invalid["valid"] is False and invalid["errors"]
    for error in invalid["errors"]:
        assert set(error) == set(ts["CharacterCardValidationError"])

    content = json.dumps(_card(), ensure_ascii=False).encode("utf-8")
    import_resp = card_client.post(
        "/api/v1/character-cards/import",
        headers={"Idempotency-Key": "frontend-contract-card-0001"},
        files={"file": ("card.json", content, "application/json")},
    )
    assert import_resp.status_code == 201, import_resp.text
    imported = import_resp.json()
    assert set(imported) == set(ts["CharacterCardImportResponse"])
    assert set(imported["card"]) == set(ts["CharacterCard"])
    card_id = imported["card_id"]
    assert card_id.startswith("card_")

    detail_resp = card_client.get(f"/api/v1/character-cards/{card_id}")
    assert detail_resp.status_code == 200
    assert set(detail_resp.json()) == set(ts["CharacterCardDetailResponse"])
    assert set(detail_resp.json()["card"]) == set(ts["CharacterCard"])

    list_resp = card_client.get("/api/v1/character-cards")
    assert list_resp.status_code == 200
    listing = list_resp.json()
    assert set(listing) == set(ts["CharacterCardListResponse"])
    assert listing["total"] >= 1
    for item in listing["cards"]:
        assert set(item) == set(ts["CharacterCardSummary"])


def test_replay_openapi_query_contract() -> None:
    """分页口径钉死：from_seq 默认 1（min 1）、limit 默认 200（min 1、max 500）。"""
    operation = _openapi()["paths"]["/api/v1/games/{game_id}/replay"]["get"]
    query_params = {
        parameter["name"]: parameter
        for parameter in operation.get("parameters", [])
        if parameter.get("in") == "query"
    }
    from_seq_schema = query_params["from_seq"]["schema"]
    limit_schema = query_params["limit"]["schema"]
    assert from_seq_schema["default"] == 1
    assert from_seq_schema["minimum"] == 1
    assert limit_schema["default"] == 200
    assert limit_schema["minimum"] == 1
    assert limit_schema["maximum"] == 500


def test_frontend_source_has_no_forbidden_patterns() -> None:
    """前端源码静态门禁：禁 v-html/innerHTML/eval/SSE/自动定时重试（§23.7、D-M6-6④）。"""
    violations: list[str] = []
    for path in sorted(SRC_ROOT.rglob("*")):
        if not path.is_file() or path.suffix not in {".ts", ".vue", ".css", ".html"}:
            continue
        content = path.read_text(encoding="utf-8")
        for name, pattern in _FORBIDDEN_SOURCE_PATTERNS.items():
            if pattern.search(content):
                violations.append(f"{path.relative_to(WEB_ROOT)}: {name}")
    assert violations == []
