"""集成契约测试：`web/` 前端类型快照 ↔ live OpenAPI（M6-025 骨架 + M6-027 回放）。

纯静态读取 + TestClient 实测，**不依赖 Node/pnpm 运行**（CI 的 quality job 不装 Node 也能跑）：

- 前端只用现有端点：`API_PATHS` 必须是 live OpenAPI 路径的子集，且恰为前端已接入的 5 个端点；
- 响应/请求接口字段集合与 OpenAPI schema 一致（字段名逐一比对，OpenAPI 必填项不得在 TS 侧可选）；
- 实测 `POST /api/v1/games` / `GET /view` / `POST /turns` / `GET /replay` 的真实字段集合与
  TS 接口一致（不止文档）；replay 以 `result.narrative` 为回放文本源、seq 升序、只读零写入；
- 前端源码静态禁入：禁 `v-html`/`innerHTML`/`eval`/`EventSource`/自动定时重试（第 23 章 §23.7、D-M6-6④）。

character-cards 等 M6 后端前置交付后，按同一模式做加法扩展。
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from textgame.api.app import create_app

WEB_ROOT = Path(__file__).resolve().parents[2] / "web"
TYPES_TS = WEB_ROOT / "src" / "api" / "types.ts"
SRC_ROOT = WEB_ROOT / "src"

# 前端已接入的现有端点（新增端点须同步扩大并更新本测试）
EXPECTED_FRONTEND_PATHS = {
    "/api/v1/games",
    "/api/v1/games/{game_id}",
    "/api/v1/games/{game_id}/view",
    "/api/v1/games/{game_id}/replay",
    "/api/v1/turns",
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
    """前端 API_PATHS 恰为已接入的 5 个现有端点（含 replay），且全部存在于 live OpenAPI。"""
    interfaces = TYPES_TS.read_text(encoding="utf-8")
    frontend_paths = dict(_API_PATH_RE.findall(interfaces))
    assert set(frontend_paths.values()) == EXPECTED_FRONTEND_PATHS
    live_paths = set(_openapi()["paths"])
    assert set(frontend_paths.values()) <= live_paths


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
