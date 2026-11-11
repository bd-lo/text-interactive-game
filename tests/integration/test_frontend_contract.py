"""集成契约测试：`web/` 前端类型快照 ↔ live OpenAPI（M6-025 骨架 + M6-027 回放 + M6-026 卡库 + M6-028 生成）。

纯静态读取 + TestClient 实测，**不依赖 Node/pnpm 运行**（CI 的 quality job 不装 Node 也能跑）：

- 前端只用现有端点：`API_PATHS` 必须是 live OpenAPI 路径的子集，且恰为前端已接入的 11 个端点；
- 响应/请求接口字段集合与 OpenAPI schema 一致（字段名逐一比对，OpenAPI 必填项不得在 TS 侧可选）；
- 实测 `POST /api/v1/games` / `GET /view` / `POST /turns` / `GET /replay` / character-cards 的
  真实字段集合与 TS 接口一致（不止文档）；replay 以 `result.narrative` 为回放文本源、seq 升序、
  只读零写入；卡库覆盖 list/validate/import/detail 与 9 字段 CharacterCard；
- M6-028 generate 实测（默认离线）：200 `{draft, validation, generated_by}` 字段集合与 TS 一致、
  模板兜底语义（`source=manual`/`generated_by=null`/`TEMPLATE_FALLBACK`）、同键同参复用/异参 409/
  缺键 422/第 6 次 429+`Retry-After`；save 复用 `POST /character-cards`（方法级 201）并读回；
- `/cards/generate` 深链由静态托管 SPA fallback 返回 `index.html`（D-M6-5①，刷新不 404）；
- 前端源码静态禁入：禁 `v-html`/`innerHTML`/`eval`/`EventSource`/自动定时重试（第 23 章 §23.7、D-M6-6④）。

契约测试以 live OpenAPI 为唯一权威：前端未接入 generate 时本文件必须失败（不允许 skip 放行）。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from textgame.api.app import create_app
from textgame.persistence.card_repository import SqliteCardRepository
from textgame.persistence.database import init_schema, prepare_db
from textgame.persistence.world_book_repository import SqliteWorldBookRepository
from textgame.services.card_service import CardService
from textgame.services.world_book_service import WorldBookService

WEB_ROOT = Path(__file__).resolve().parents[2] / "web"
TYPES_TS = WEB_ROOT / "src" / "api" / "types.ts"
SRC_ROOT = WEB_ROOT / "src"

#: M6-028 生成卡片端点（后端 M6B-4 已交付；前端 028 接入后本测试必须通过，不允许 skip）
GENERATE_CARD_PATH = "/api/v1/character-cards/generate"

# 前端已接入的端点（新增端点须同步扩大并更新本测试）
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
    GENERATE_CARD_PATH,
    "/api/v1/character-cards/{card_id}",
    # H4 世界书（world-books；H4-CONTRACT §2）
    "/api/v1/world-books",
    "/api/v1/world-books/import",
    "/api/v1/world-books/validate",
    "/api/v1/world-books/{world_book_id}",
    # 用户自上传场景 JSON（design/user-scenario-modules.md §3.4）
    "/api/v1/scenarios",
    "/api/v1/scenarios/validate",
    "/api/v1/scenarios/{scenario_id}",
    # LLM 运行期设置（DeepSeek API Key 输入/测试/清除）
    "/api/v1/settings/llm",
    "/api/v1/settings/llm/test",
}

# TS 接口 → OpenAPI schema（响应；字段集合必须一致）
RESPONSE_INTERFACES = {
    "CreatedGameResponse": "CreatedGameResponse",
    "GameViewResponse": "GameViewResponse",
    "GamePlayerViewResponse": "GamePlayerViewResponse",
    "ItemRef": "ItemRefResponse",
    "InventoryItem": "InventoryItemResponse",
    "NpcRef": "NpcRefResponse",
    "RoomView": "RoomViewResponse",
    "GoalObjective": "GoalObjectiveResponse",
    "Goal": "GoalResponse",
    "GameListItem": "GameListItemResponse",
    "GameListResponse": "GameListResponse",
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
    "CardGenerateResponse": "CardGenerateResponse",
    "CardGenerateValidation": "CardGenerateValidation",
    # H4 世界书（world-books；含 /view 加法字段 world_book/lore）
    "WorldBookRef": "WorldBookRefResponse",
    "LoreEntry": "LoreEntryResponse",
    "LoreView": "LoreResponse",
    "WorldBookWarning": "WorldBookWarningResponse",
    "WorldBookSummary": "WorldBookSummaryResponse",
    "WorldBookListResponse": "WorldBookListResponse",
    "WorldBookEntry": "WorldBookEntryResponse",
    "WorldBookDetailBody": "WorldBookDetailBodyResponse",
    "WorldBookDetailResponse": "WorldBookDetailResponse",
    "WorldBookImportResponse": "WorldBookImportResponse",
    "WorldBookValidationError": "WorldBookValidationErrorResponse",
    "WorldBookValidatePreview": "WorldBookValidatePreviewResponse",
    "WorldBookValidateResponse": "WorldBookValidateResponse",
    "WorldBookDeleteResponse": "WorldBookDeleteResponse",
    # 用户自上传场景 JSON（design/user-scenario-modules.md §3.4；8 个 TS 接口）
    "ScenarioStats": "ScenarioStatsResponse",
    "ScenarioWarning": "ScenarioWarningResponse",
    "ScenarioValidationError": "ScenarioValidationErrorResponse",
    "ScenarioSummary": "ScenarioSummaryResponse",
    "ScenarioListResponse": "ScenarioListResponse",
    "ScenarioDetailResponse": "ScenarioDetailResponse",
    "ScenarioValidateResponse": "ScenarioValidateResponse",
    "ScenarioImportResponse": "ScenarioImportResponse",
    # LLM 运行期设置（DeepSeek API Key）
    "LlmSettingsStatus": "LlmStatusResponse",
    "LlmConnectionTest": "LlmTestResponse",
    "LlmSettingsSaveResponse": "LlmSaveResponse",
    "LlmSettingsClearResponse": "LlmDeleteResponse",
}

# TS 接口 → OpenAPI schema（请求；字段名 + 可选项必须一致）
REQUEST_INTERFACES = {
    "CreateGameRequest": "CreateGameBody",
    "TurnRequest": "TurnRequestBody",
    "CardGenerateRequest": "CardGenerateBody",
    "LlmSettingsSaveRequest": "LlmSaveRequestBody",
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


def _frontend_api_paths() -> dict[str, str]:
    """types.ts 中 `API_PATHS` 常量：名字 → 路径（用于断言方法级契约）。"""
    source = TYPES_TS.read_text(encoding="utf-8")
    return dict(_API_PATH_RE.findall(source))


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
    """前端 API_PATHS 恰为已接入的 11 个端点（含 M6-028 generate），且全部存在于 live OpenAPI。"""
    live_paths = set(_openapi()["paths"])
    frontend_paths = _frontend_paths()
    expected = set(EXPECTED_FRONTEND_PATHS)
    assert GENERATE_CARD_PATH in expected
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
    """character-cards 8 端点（6 路径 / 8 操作）与 multipart `file` 声明、games 的 card_id 加法。"""
    paths = _openapi()["paths"]
    assert set(paths["/api/v1/character-cards"]) == {"get", "post"}
    assert set(paths["/api/v1/character-cards/schema"]) == {"get"}
    assert set(paths["/api/v1/character-cards/import"]) == {"post"}
    assert set(paths["/api/v1/character-cards/validate"]) == {"post"}
    assert set(paths[GENERATE_CARD_PATH]) == {"post"}
    assert set(paths["/api/v1/character-cards/{card_id}"]) == {"get", "delete"}

    import_schema = paths["/api/v1/character-cards/import"]["post"]["requestBody"]["content"][
        "multipart/form-data"
    ]["schema"]
    assert "file" in import_schema["properties"]

    create_props = _openapi()["components"]["schemas"]["CreateGameBody"]["properties"]
    assert "card_id" in create_props


#: M6-028 生成用例的默认 brief（默认离线确定性模板兜底；brief 只作数据展示，不执行其中指令）。
_GENERATE_BRIEF = "一个在灰烬平原流浪的盲眼剑客，性格寡言"


def _generate(
    client: TestClient,
    *,
    key: str | None,
    brief: str = _GENERATE_BRIEF,
    session: str | None = None,
) -> Any:
    """实测 `POST /generate`；`key=None` 时不带幂等键（缺键 422 用例）。"""
    headers: dict[str, str] = {}
    if key is not None:
        headers["Idempotency-Key"] = key
    if session is not None:
        headers["X-Session-Id"] = session
    return client.post(
        GENERATE_CARD_PATH,
        headers=headers,
        json={"brief": brief, "schema_version": "1.0", "options": {}},
    )


def _generate_ok(client: TestClient, **kwargs: Any) -> dict[str, Any]:
    """`POST /generate` 成功（200）→ payload。"""
    response = _generate(client, **kwargs)
    assert response.status_code == 200, response.text
    payload: dict[str, Any] = response.json()
    return payload


def _assert_error_envelope(response: Any, status_code: int, error_code: str) -> dict[str, Any]:
    """失败响应 = 统一信封 `{ok:false, error_code, message, violations?}`（第 6 章 §6.7）。"""
    assert response.status_code == status_code, response.text
    body: dict[str, Any] = response.json()
    assert body.get("ok") is False
    assert body.get("error_code") == error_code
    assert isinstance(body.get("message"), str) and body["message"]
    assert set(body) <= {"ok", "error_code", "message", "violations"}
    return body


def test_generate_endpoint_contract_and_frontend_types() -> None:
    """M6-028 generate：前端已接入（路径/请求/响应接口）+ OpenAPI 方法级契约。

    请求/响应字段集合与可选性由 `test_request_interfaces_match_openapi_schemas` /
    `test_response_interfaces_match_openapi_schemas` 的通用断言覆盖（已登记
    `CardGenerateRequest/Response/Validation`）；这里补路径与操作级契约：POST + JSON 请求体 +
    `Idempotency-Key` 头 + 200 成功码 + save 复用 `POST /character-cards`（list/save 同路径）。
    """
    live_paths = set(_openapi()["paths"])
    assert GENERATE_CARD_PATH in live_paths
    assert GENERATE_CARD_PATH in _frontend_paths()

    api_paths = _frontend_api_paths()
    assert api_paths["generateCard"] == GENERATE_CARD_PATH
    assert api_paths["saveCard"] == "/api/v1/character-cards"
    assert api_paths["listCards"] == "/api/v1/character-cards"

    operation = _openapi()["paths"][GENERATE_CARD_PATH]["post"]
    assert "200" in operation["responses"]
    assert operation["requestBody"]["required"] is True
    body_schema = operation["requestBody"]["content"]["application/json"]["schema"]
    assert body_schema["$ref"] == "#/components/schemas/CardGenerateBody"
    header_names = {
        parameter["name"]
        for parameter in operation.get("parameters", [])
        if parameter.get("in") == "header"
    }
    assert "Idempotency-Key" in header_names


def test_live_generate_fields_match_frontend_types(card_client: TestClient) -> None:
    """实测 generate（默认离线模板兜底）：字段集合与 TS 一致 + 模板兜底语义可判定。"""
    ts = _ts_interfaces()
    payload = _generate_ok(card_client, key="frontend-contract-generate-0001")

    assert set(payload) == set(ts["CardGenerateResponse"])
    draft = payload["draft"]
    assert set(draft) == set(ts["CharacterCard"])
    assert draft["source"] == "manual", "默认离线生成的草稿必须是模板兜底（source=manual）"
    assert draft["generated_by"] is None
    validation = payload["validation"]
    assert set(validation) == set(ts["CardGenerateValidation"])
    assert validation["valid"] is True
    for warning in validation["warnings"]:
        assert set(warning) == set(ts["CharacterCardWarning"])
    assert any(warning["code"] == "TEMPLATE_FALLBACK" for warning in validation["warnings"]), (
        "前端据 TEMPLATE_FALLBACK 显示「模板草稿」徽章，默认离线必须带该 warning"
    )
    assert payload["generated_by"] is None


def test_live_generate_idempotency_contract(card_client: TestClient) -> None:
    """同键同参回放首结果（不重复生成）、同键异参 409、缺 `Idempotency-Key` 422。"""
    session = "frontend-contract-generate-idem"
    key = "frontend-contract-generate-idem-0001"
    first = _generate_ok(card_client, key=key, session=session)
    replay = _generate_ok(card_client, key=key, session=session)
    assert replay == first

    conflict = _generate(card_client, key=key, session=session, brief="另一个完全不同的人物设定")
    _assert_error_envelope(conflict, 409, "CONFLICT")

    missing = _generate(card_client, key=None, session=session)
    _assert_error_envelope(missing, 422, "INVALID_INPUT")


def test_live_generate_rate_limit_contract(card_client: TestClient) -> None:
    """前 5 次 200；第 6 次 429 `OUT_OF_RANGE` + `Retry-After`（前端据此提示 N 秒后重试）。"""
    session = "frontend-contract-generate-rate"
    for index in range(5):
        _generate_ok(card_client, key=f"frontend-contract-rate-{index:04d}", session=session)
    blocked = _generate(card_client, key="frontend-contract-rate-blocked", session=session)
    _assert_error_envelope(blocked, 429, "OUT_OF_RANGE")
    retry_after = blocked.headers.get("Retry-After")
    assert retry_after is not None and retry_after.isdigit(), "429 必须带 Retry-After 头"
    assert int(retry_after) >= 1


def test_live_save_card_method_level_contract(card_client: TestClient) -> None:
    """save 复用 `POST /character-cards`（方法级）：201 `{card_id, card}`，详情/列表可读回。"""
    ts = _ts_interfaces()
    payload = _generate_ok(card_client, key="frontend-contract-save-generate-0001")
    draft = payload["draft"]

    operations = _openapi()["paths"]["/api/v1/character-cards"]
    assert {"get", "post"} <= set(operations), "列表 GET 与保存 POST 同路径，必须方法级区分"
    assert "200" in operations["get"]["responses"]
    assert "201" in operations["post"]["responses"]

    saved = card_client.post(
        "/api/v1/character-cards",
        headers={"Idempotency-Key": "frontend-contract-save-card-0001"},
        json=draft,
    )
    assert saved.status_code == 201, saved.text
    record = saved.json()
    assert set(record) == set(ts["CharacterCardSaveResponse"])
    assert set(record["card"]) == set(ts["CharacterCard"])
    card_id = record["card_id"]
    assert card_id.startswith("card_")
    assert record["card"]["name"] == draft["name"]
    assert record["card"]["source"] == draft["source"] == "manual"

    detail = card_client.get(f"/api/v1/character-cards/{card_id}")
    assert detail.status_code == 200
    assert set(detail.json()) == set(ts["CharacterCardDetailResponse"])
    listing = card_client.get("/api/v1/character-cards").json()
    assert card_id in {item["card_id"] for item in listing["cards"]}


def test_generate_route_spa_deep_link_served() -> None:
    """`/cards/generate` 深链：静态托管 SPA fallback 返回 `index.html`（刷新 200 不 404）。"""
    dist = Path(__file__).resolve().parents[1] / "fixtures" / "web_dist"
    with TestClient(create_app(static_dir=dist)) as client:
        resp = client.get("/cards/generate")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "textgame-spa-fixture" in resp.text
    assert "default-src 'self'" in resp.headers["content-security-policy"]


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


def test_game_list_endpoint_consistency() -> None:
    """D9 存档列表契约：前端 `listGames` 映射到 `GET /api/v1/games`（与建局同路径不同方法）。

    `API_PATHS` 只有路径集合，无法区分 GET/POST；这里补方法级断言，防止把列表端点
    误接成建局（同一路径）或只声明不接。
    """
    api_paths = _frontend_api_paths()
    assert api_paths["listGames"] == "/api/v1/games"
    assert api_paths["createGame"] == "/api/v1/games"
    operations = _openapi()["paths"]["/api/v1/games"]
    assert {"get", "post"} <= set(operations), "GET（列表）与 POST（建局）都必须存在"
    assert "200" in operations["get"]["responses"], "GET /games 应返回 200 列表"
    assert "201" in operations["post"]["responses"], "POST /games 应返回 201 建局"
    # 列表端点分页口径：limit 默认 10（1..50）、offset ≥0
    params = {
        parameter["name"]: parameter
        for parameter in operations["get"].get("parameters", [])
        if parameter.get("in") == "query"
    }
    assert params["limit"]["schema"]["default"] == 10
    assert params["limit"]["schema"]["maximum"] == 50
    assert params["offset"]["schema"]["minimum"] == 0


#: 一份合法简化世界书（H4-CONTRACT §2：name + entries[{keys,content,...}]）。
_SIMPLE_WORLD_BOOK = {
    "name": "余烬哨站设定",
    "description": "前端契约测试用世界书。",
    "entries": [
        {"uid": 1, "keys": ["哨站"], "content": "余烬哨站是河谷里的旧哨站。", "order": 10},
        {
            "uid": 2,
            "keys": ["守夜人", "老兵"],
            "keysecondary": ["信号灯"],
            "content": "守夜人老兵在此看守信号灯。",
            "constant": True,
            "order": 20,
        },
    ],
}


@pytest.fixture
def world_book_client() -> Iterator[TestClient]:
    """内存 SQLite + 真实 WorldBookService 的 TestClient（H4 世界书 live 契约）。"""
    engine, _factory = prepare_db("sqlite:///:memory:")
    init_schema(engine)
    service = WorldBookService(SqliteWorldBookRepository(engine))
    with TestClient(create_app(world_book_service=service)) as client:
        yield client
    engine.dispose()


def test_world_book_endpoints_consistency() -> None:
    """H4 世界书 5 端点的方法/路径/分页/multipart 声明与前端 API_PATHS 一致。"""
    api_paths = _frontend_api_paths()
    expected = {
        "listWorldBooks": "/api/v1/world-books",
        "importWorldBook": "/api/v1/world-books/import",
        "validateWorldBook": "/api/v1/world-books/validate",
        "getWorldBook": "/api/v1/world-books/{world_book_id}",
        "deleteWorldBook": "/api/v1/world-books/{world_book_id}",
    }
    for name, path in expected.items():
        assert api_paths[name] == path, f"前端 API_PATHS.{name} 未指向 {path}"

    paths = _openapi()["paths"]
    assert set(paths["/api/v1/world-books"]) == {"get"}
    assert set(paths["/api/v1/world-books/import"]) == {"post"}
    assert set(paths["/api/v1/world-books/validate"]) == {"post"}
    assert set(paths["/api/v1/world-books/{world_book_id}"]) == {"get", "delete"}

    import_schema = paths["/api/v1/world-books/import"]["post"]["requestBody"]["content"][
        "multipart/form-data"
    ]["schema"]
    assert "file" in import_schema["properties"], "import 必须是 multipart file 部件"
    assert "name" in import_schema["properties"], "import 支持可选 name 表单字段"

    list_params = {
        parameter["name"]: parameter
        for parameter in paths["/api/v1/world-books"]["get"].get("parameters", [])
        if parameter.get("in") == "query"
    }
    assert {"query", "limit", "offset"} <= set(list_params)
    assert list_params["limit"]["schema"]["default"] == 20
    assert list_params["limit"]["schema"]["maximum"] == 100
    assert list_params["offset"]["schema"]["minimum"] == 0


def test_live_world_book_lifecycle_fields_match_frontend_types(
    world_book_client: TestClient,
) -> None:
    """世界书实测：导入→列表→详情→校验→软删，字段集合与 TS 接口一致（纯加法、零事件）。"""
    ts = _ts_interfaces()
    content = json.dumps(_SIMPLE_WORLD_BOOK, ensure_ascii=False).encode("utf-8")

    imported = world_book_client.post(
        "/api/v1/world-books/import",
        headers={"Idempotency-Key": "frontend-contract-wb-0001"},
        files={"file": ("world-book.json", content, "application/json")},
    )
    assert imported.status_code == 201, imported.text
    payload = imported.json()
    assert set(payload) == set(ts["WorldBookImportResponse"])
    assert payload["entry_count"] == 2
    # 简化 schema 可能被识别为 tavern（entries 数组兼容）或 manual；只断言是已知来源
    assert payload["source"] in {"tavern", "manual", "character_card"}
    world_book_id = payload["world_book_id"]
    assert world_book_id.startswith("wb_")

    listing = world_book_client.get("/api/v1/world-books", params={"limit": 5})
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert set(body) == set(ts["WorldBookListResponse"])
    assert body["total"] >= 1
    assert set(body["world_books"][0]) == set(ts["WorldBookSummary"])

    detail = world_book_client.get(f"/api/v1/world-books/{world_book_id}")
    assert detail.status_code == 200, detail.text
    detail_body = detail.json()
    assert set(detail_body) == set(ts["WorldBookDetailResponse"])
    assert set(detail_body["world_book"]) == set(ts["WorldBookDetailBody"])
    assert detail_body["entries"], "导入的世界书应有条目"
    for entry in detail_body["entries"]:
        assert set(entry) == set(ts["WorldBookEntry"])

    validated = world_book_client.post(
        "/api/v1/world-books/validate", json={"payload": _SIMPLE_WORLD_BOOK}
    )
    assert validated.status_code == 200, validated.text
    validate_body = validated.json()
    assert set(validate_body) == set(ts["WorldBookValidateResponse"])
    assert validate_body["valid"] is True
    assert set(validate_body["preview"]) == set(ts["WorldBookValidatePreview"])

    deleted = world_book_client.delete(
        f"/api/v1/world-books/{world_book_id}",
        headers={"Idempotency-Key": "frontend-contract-wb-del-0001"},
    )
    assert deleted.status_code == 200, deleted.text
    assert set(deleted.json()) == set(ts["WorldBookDeleteResponse"])
    assert world_book_client.get(f"/api/v1/world-books/{world_book_id}").status_code == 404


def test_scenario_endpoints_consistency() -> None:
    """用户上传场景 5 端点的方法/路径/分页与前端 API_PATHS 一致（design §3.4）。"""
    api_paths = _frontend_api_paths()
    expected = {
        "listScenarios": "/api/v1/scenarios",
        "importScenario": "/api/v1/scenarios",
        "validateScenario": "/api/v1/scenarios/validate",
        "getScenario": "/api/v1/scenarios/{scenario_id}",
        "deleteScenario": "/api/v1/scenarios/{scenario_id}",
    }
    for name, path in expected.items():
        assert api_paths[name] == path, f"前端 API_PATHS.{name} 未指向 {path}"

    paths = _openapi()["paths"]
    assert set(paths["/api/v1/scenarios"]) == {"get", "post"}
    assert set(paths["/api/v1/scenarios/validate"]) == {"post"}
    assert set(paths["/api/v1/scenarios/{scenario_id}"]) == {"get", "delete"}

    # 导入：前端现状走 multipart file（后端同时接受 JSON 原文，见设计文档 §3.4）。
    import_schema = paths["/api/v1/scenarios"]["post"]["requestBody"]["content"][
        "multipart/form-data"
    ]["schema"]
    assert "file" in import_schema["properties"], "import 必须接受 multipart file 部件"

    # 列表：q 搜索 + 分页（默认 20 / 上限 100）。
    list_params = {
        parameter["name"]: parameter
        for parameter in paths["/api/v1/scenarios"]["get"].get("parameters", [])
        if parameter.get("in") == "query"
    }
    assert {"q", "limit", "offset"} <= set(list_params)
    assert list_params["limit"]["schema"]["default"] == 20
    assert list_params["limit"]["schema"]["maximum"] == 100
    assert list_params["offset"]["schema"]["minimum"] == 0

    # 校验：body = 场景 JSON 原文（不是 {payload} 包装）、200 返回 errors/warnings/summary。
    validate_operation = paths["/api/v1/scenarios/validate"]["post"]
    assert "application/json" in validate_operation["requestBody"]["content"]
    assert "200" in validate_operation["responses"]

    # 删除：204 空 body（与前端「无内容」解析一致）。
    delete_operation = paths["/api/v1/scenarios/{scenario_id}"]["delete"]
    assert "204" in delete_operation["responses"]

    # 建局：body 加法 scenario_id（与 card_id/scenario 互斥由服务端保证）。
    create_schema = _openapi()["components"]["schemas"]["CreateGameBody"]
    assert "scenario_id" in create_schema["properties"]


def test_llm_settings_endpoints_consistency() -> None:
    """LLM 运行期设置：路径/方法/请求体与前端 API_PATHS 一致（写端点无 Idempotency-Key）。"""
    api_paths = _frontend_api_paths()
    expected = {
        "getLlmSettings": "/api/v1/settings/llm",
        "saveLlmSettings": "/api/v1/settings/llm",
        "clearLlmSettings": "/api/v1/settings/llm",
        "testLlmConnection": "/api/v1/settings/llm/test",
    }
    for name, path in expected.items():
        assert api_paths[name] == path, f"前端 API_PATHS.{name} 未指向 {path}"

    paths = _openapi()["paths"]
    assert set(paths["/api/v1/settings/llm"]) == {"get", "put", "delete"}
    assert set(paths["/api/v1/settings/llm/test"]) == {"post"}

    schemas = _openapi()["components"]["schemas"]
    save_schema = schemas["LlmSaveRequestBody"]
    assert set(save_schema["properties"]) == {
        "api_key",
        "provider",
        "model",
        "api_base",
        "run_test",
    }
    assert save_schema["required"] == ["api_key"]

    # 设置写端点按值幂等：不需要 Idempotency-Key（与游戏回合/导入的幂等语义区分）。
    for method in ("put", "delete"):
        params = paths["/api/v1/settings/llm"][method].get("parameters", [])
        assert all(parameter.get("name") != "Idempotency-Key" for parameter in params)


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
