"""集成测试：M6 人物卡库 API（character-cards 7 端点 + 0003 表 + 安全/幂等契约）。

覆盖（第 5 章 §5.7、第 6 章 §6.7、第 23 章 §23.4/§23.5、D-M6-5/6）：

- ``GET /schema``：返回 Schema 对象本身（draft 2020-12、9 字段、attributes 逐键展开）；
- ``GET /character-cards``：``{cards, total}``、列表项字段集合、query/limit/offset、非法分页；
- ``GET /character-cards/{id}``：``{card}`` 与 404/422 错误码；
- ``POST /import``：multipart 上传 201、source 强制 upload、重复模板合并 warning、
  模板不存在 404、超大 413、非法 JSON/编码/重复键/深度/类型 422、XSS 403、幂等；
- ``POST /validate``：查询式校验 200 ``{valid, errors}``、只读无副作用、非 JSON/超大 4xx；
- ``POST /character-cards``：保存 201、强制 card_id/source 判定、幂等同键异参 409；
- ``DELETE /character-cards/{id}``：软删除 204、重放 204、未知 404、必带幂等键；
- 安全（R-L3/R-C2）：≤64KB、字段白名单、HTML/XSS/URL 拒绝、trim 规范化；
- 人物卡库**不参与事件溯源**：读写卡不产生任何 ``events`` 行。

全部用例使用内存 SQLite + 真实 ``CardService``/``SqliteCardRepository``，不联网。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from textgame.api.app import create_app
from textgame.persistence.card_repository import SqliteCardRepository
from textgame.persistence.database import init_schema, prepare_db
from textgame.services.card_service import CardService

pytestmark = [pytest.mark.integration]

_CARD_FIELDS = {
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
#: 无默认值的必填字段（``appearance``/``generated_by`` 允许 ``null``，故非 required）。
_CARD_REQUIRED_FIELDS = {
    "schema_version",
    "name",
    "attributes",
    "hp",
    "starting_items",
    "background",
    "source",
}
_LIST_ITEM_FIELDS = {"card_id", "name", "source", "generated_by", "updated_at"}
_ATTRIBUTES = {"str": 12, "dex": 14, "con": 11, "int": 15, "wis": 10, "cha": 8, "max_hp": 30}
_VALID_CARD_ID = "card_01J9Z3K4Q7X8Y9Z0ABCDEFGHJK"


@dataclass
class CardHarness:
    """一套卡片库测试装配：HTTP 客户端 + 服务 + 底层 Engine（断言零事件用）。"""

    client: TestClient
    service: CardService
    engine: Engine


def _card(**overrides: Any) -> dict[str, Any]:
    """一份合法人物卡 JSON（可按字段覆盖）。"""
    card: dict[str, Any] = {
        "schema_version": "1.0",
        "name": "灰烬旅人",
        "attributes": dict(_ATTRIBUTES),
        "hp": 30,
        "starting_items": ["item.torch", "item.potion"],
        "background": "在旧矿镇长大的拾荒者，靠辨认废矿里的风声过活。",
        "appearance": "煤灰斗篷，左眉有一道旧疤。",
        "source": "upload",
        "generated_by": None,
    }
    card.update(overrides)
    return card


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _import(
    client: TestClient,
    content: bytes,
    *,
    key: str,
    filename: str = "card.json",
    content_type: str = "application/json",
):
    """``POST /import``（multipart 上传 bytes）。"""
    return client.post(
        "/api/v1/character-cards/import",
        headers={"Idempotency-Key": key},
        files={"file": (filename, content, content_type)},
    )


def _post_json(client: TestClient, path: str, content: bytes, *, key: str | None) -> Any:
    """向需要幂等键的 JSON 写端点发原始 bytes（便于构造超大/非法体）。"""
    headers = {"Idempotency-Key": key} if key is not None else {}
    return client.post(path, headers=headers, content=content)


def _assert_error(response: Any, status_code: int, error_code: str) -> dict[str, Any]:
    """断言统一失败信封（HTTP 状态 + ``{ok:false, error_code, message, violations?}``）。"""
    assert response.status_code == status_code, response.text
    body: dict[str, Any] = response.json()
    assert body.get("ok") is False
    assert body.get("error_code") == error_code
    assert isinstance(body.get("message"), str) and body["message"]
    assert set(body) <= {"ok", "error_code", "message", "violations"}
    return body


def _import_ok(
    client: TestClient, content: bytes, *, key: str, filename: str = "card.json"
) -> dict[str, Any]:
    """上传合法卡并断言 201，返回响应体。"""
    resp = _import(client, content, key=key, filename=filename)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _event_count(engine: Engine) -> int:
    """``events`` 表行数（人物卡库不得写入事件溯源）。"""
    with engine.connect() as connection:
        return int(connection.execute(text("SELECT COUNT(*) FROM events")).scalar() or 0)


@pytest.fixture
def card_harness() -> Iterator[CardHarness]:
    """内存 SQLite + 真实 CardService + TestClient（不联网、独立库）。"""
    engine, _factory = prepare_db("sqlite:///:memory:")
    init_schema(engine)
    service = CardService(SqliteCardRepository(engine))
    with TestClient(create_app(card_service=service)) as client:
        yield CardHarness(client=client, service=service, engine=engine)
    engine.dispose()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
def test_schema_endpoint_returns_json_schema_object(card_harness: CardHarness) -> None:
    """``GET /schema`` 直接返回 Schema 对象：draft 2020-12、9 字段、attributes 逐键展开。"""
    resp = card_harness.client.get("/api/v1/character-cards/schema")
    assert resp.status_code == 200, resp.text
    schema = resp.json()

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == "https://textgame.local/schemas/character-card-1.0.json"
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == _CARD_FIELDS
    assert set(schema["required"]) == _CARD_REQUIRED_FIELDS
    assert schema["properties"]["schema_version"]["const"] == "1.0"
    attributes = schema["properties"]["attributes"]
    assert set(attributes["properties"]) == set(_ATTRIBUTES)
    assert set(attributes["required"]) == set(_ATTRIBUTES)
    assert attributes["properties"]["max_hp"]["minimum"] == 1
    assert attributes["properties"]["str"]["minimum"] == -10
    assert attributes["additionalProperties"] is False
    assert "pattern" in schema["properties"]["starting_items"]["items"]


def test_openapi_documents_all_seven_endpoints(card_harness: CardHarness) -> None:
    """live OpenAPI 逐项包含 7 端点（含 import 的 multipart requestBody 与写端点幂等头）。"""
    paths = card_harness.client.app.openapi()["paths"]

    assert set(paths["/api/v1/character-cards"]) == {"get", "post"}
    assert set(paths["/api/v1/character-cards/import"]) == {"post"}
    assert set(paths["/api/v1/character-cards/schema"]) == {"get"}
    assert set(paths["/api/v1/character-cards/validate"]) == {"post"}
    assert set(paths["/api/v1/character-cards/{card_id}"]) == {"get", "delete"}

    import_operation = paths["/api/v1/character-cards/import"]["post"]
    assert import_operation["responses"]["201"]
    multipart = import_operation["requestBody"]["content"]["multipart/form-data"]
    assert multipart["schema"]["required"] == ["file"]
    save_operation = paths["/api/v1/character-cards"]["post"]
    assert "application/json" in save_operation["requestBody"]["content"]
    delete_operation = paths["/api/v1/character-cards/{card_id}"]["delete"]
    assert "204" in delete_operation["responses"]
    for operation in (
        import_operation,
        save_operation,
        delete_operation,
    ):
        headers = {
            parameter["name"]
            for parameter in operation.get("parameters", [])
            if parameter.get("in") == "header"
        }
        assert "Idempotency-Key" in headers

    # ---------------------------------------------------------------------------
    # 列表 / 详情
    # ---------------------------------------------------------------------------


def test_list_is_empty_before_any_card(card_harness: CardHarness) -> None:
    """空库列表：``{cards: [], total: 0}`` 精确字段集合。"""
    resp = card_harness.client.get("/api/v1/character-cards")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"cards", "total"}
    assert body == {"cards": [], "total": 0}


def test_list_contains_imported_cards_with_exact_fields(card_harness: CardHarness) -> None:
    """导入两张卡后：total=2、列表项字段集合精确、updated_at 为 UTC ISO 时间。"""
    _import_ok(
        card_harness.client,
        _json_bytes(_card(name="灰烬旅人")),
        key="list-key-card-0001",
    )
    _import_ok(
        card_harness.client,
        _json_bytes(_card(name="矿镇斥候")),
        key="list-key-card-0002",
    )

    resp = card_harness.client.get("/api/v1/character-cards")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    assert len(body["cards"]) == 2
    for item in body["cards"]:
        assert set(item) == _LIST_ITEM_FIELDS
        assert item["card_id"].startswith("card_")
        assert item["source"] == "upload"
        assert item["generated_by"] is None
        parsed = datetime.fromisoformat(str(item["updated_at"]).replace("Z", "+00:00"))
        assert parsed.tzinfo is not None


def test_list_supports_query_and_pagination(card_harness: CardHarness) -> None:
    """``query`` 过滤 name 子串；``limit``/``offset`` 生效且 ``total`` 为过滤后总数。"""
    for index, name in enumerate(("灰烬旅人", "矿镇斥候", "灰烬剑客")):
        _import_ok(
            card_harness.client,
            _json_bytes(_card(name=name)),
            key=f"list-page-key-{index:04d}",
        )

    filtered = card_harness.client.get("/api/v1/character-cards", params={"query": "灰烬"}).json()
    assert filtered["total"] == 2
    assert {item["name"] for item in filtered["cards"]} == {"灰烬旅人", "灰烬剑客"}

    page = card_harness.client.get(
        "/api/v1/character-cards", params={"limit": 1, "offset": 1}
    ).json()
    assert page["total"] == 3
    assert len(page["cards"]) == 1


def test_list_invalid_pagination_returns_422(card_harness: CardHarness) -> None:
    """``limit`` 越界 → 422 ``INVALID_INPUT`` 统一信封。"""
    for params in ({"limit": 0}, {"limit": 101}, {"offset": -1}):
        resp = card_harness.client.get("/api/v1/character-cards", params=params)
        _assert_error(resp, 422, "INVALID_INPUT")


def test_get_card_returns_authoritative_card(card_harness: CardHarness) -> None:
    """``GET /{card_id}`` 返回 ``{card}``，且 9 字段与上传一致（source 被强制 upload）。"""
    payload = _card()
    created = _import_ok(card_harness.client, _json_bytes(payload), key="get-card-key-0001")

    resp = card_harness.client.get(f"/api/v1/character-cards/{created['card_id']}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"card"}
    assert set(body["card"]) == _CARD_FIELDS
    assert body["card"]["name"] == payload["name"]
    assert body["card"]["hp"] == payload["hp"]
    assert body["card"]["starting_items"] == payload["starting_items"]
    assert body["card"]["source"] == "upload"


def test_get_unknown_card_returns_404(card_harness: CardHarness) -> None:
    """合法格式但不存在 → 404 ``NOT_FOUND``。"""
    resp = card_harness.client.get(f"/api/v1/character-cards/{_VALID_CARD_ID}")
    _assert_error(resp, 404, "NOT_FOUND")


def test_get_malformed_card_id_returns_422(card_harness: CardHarness) -> None:
    """ID 格式非法（非 ``card_<ULID>``）→ 422 ``INVALID_INPUT``。"""
    resp = card_harness.client.get("/api/v1/character-cards/not-a-card-id")
    _assert_error(resp, 422, "INVALID_INPUT")


# ---------------------------------------------------------------------------
# import：成功路径 / source / 规范化
# ---------------------------------------------------------------------------
def test_import_returns_201_with_contract_payload(card_harness: CardHarness) -> None:
    """导入合法卡 → 201 ``{card_id, card, warnings}``；卡入库且不产生 events。"""
    resp = _import(
        card_harness.client,
        _json_bytes(_card()),
        key="import-key-0001",
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert set(body) == {"card_id", "card", "warnings"}
    assert body["warnings"] == []
    assert body["card"] == _card()
    assert _event_count(card_harness.engine) == 0

    listed = card_harness.client.get("/api/v1/character-cards").json()
    assert listed["total"] == 1
    assert listed["cards"][0]["card_id"] == body["card_id"]


def test_import_forces_source_upload_and_warns(card_harness: CardHarness) -> None:
    """上传文件的 source 由服务端强制 ``upload``；客户端值被覆盖并记 warning。"""
    body = _import_ok(
        card_harness.client,
        _json_bytes(_card(source="llm", generated_by="fake:model")),
        key="import-source-key-0001",
    )
    assert body["card"]["source"] == "upload"
    assert [warning["code"] for warning in body["warnings"]] == ["SOURCE_OVERRIDDEN"]
    assert body["warnings"][0]["field"] == "source"


def test_import_merges_duplicate_starting_items(card_harness: CardHarness) -> None:
    """重复模板合并为一项并记 ``DUPLICATE_ITEM_MERGED`` warning（保留首次写法）。"""
    body = _import_ok(
        card_harness.client,
        _json_bytes(
            _card(starting_items=["item.torch", "item.torch", "item.potion", "item.torch"])
        ),
        key="import-dup-key-0001",
    )
    assert body["card"]["starting_items"] == ["item.torch", "item.potion"]
    codes = [warning["code"] for warning in body["warnings"]]
    assert codes.count("DUPLICATE_ITEM_MERGED") == 2


def test_import_accepts_underscore_template_notation(card_harness: CardHarness) -> None:
    """``item_torch`` / ``item.torch`` 两种模板写法等价，可混合导入。"""
    body = _import_ok(
        card_harness.client,
        _json_bytes(_card(starting_items=["item_torch", "item.rusty_knife"])),
        key="import-notation-key-0001",
    )
    assert body["card"]["starting_items"] == ["item_torch", "item.rusty_knife"]


def test_import_merges_cross_notation_duplicates(card_harness: CardHarness) -> None:
    """``item_torch`` 与 ``item.torch`` 是同一模板：合并去重并记 warning。"""
    body = _import_ok(
        card_harness.client,
        _json_bytes(_card(starting_items=["item_torch", "item.torch"])),
        key="import-cross-dup-key-0001",
    )
    assert body["card"]["starting_items"] == ["item_torch"]
    assert [warning["code"] for warning in body["warnings"]].count("DUPLICATE_ITEM_MERGED") == 1


def test_import_trims_name(card_harness: CardHarness) -> None:
    """服务端规范化：``name`` 前后空白 trim 后入库。"""
    body = _import_ok(
        card_harness.client,
        _json_bytes(_card(name="  灰烬旅人  ")),
        key="import-trim-key-0001",
    )
    assert body["card"]["name"] == "灰烬旅人"


# ---------------------------------------------------------------------------
# import：错误码（404/413/422）与内容安全
# ---------------------------------------------------------------------------
def test_import_unknown_item_template_returns_404(card_harness: CardHarness) -> None:
    """模板不存在 → 404 ``NOT_FOUND``（第 5 章 §5.7）。"""
    resp = _import(
        card_harness.client,
        _json_bytes(_card(starting_items=["item.no_such_template"])),
        key="import-template-key-0001",
    )
    _assert_error(resp, 404, "NOT_FOUND")


def test_import_oversized_file_returns_413(card_harness: CardHarness) -> None:
    """文件 >64KB → 413 + ``INVALID_INPUT``（第 6 章 §6.7 文件过大特例）。"""
    oversized = b'{"schema_version":"1.0","padding":"' + b"a" * (64 * 1024) + b'"}'
    resp = _import(card_harness.client, oversized, key="import-big-key-0001")
    body = _assert_error(resp, 413, "INVALID_INPUT")
    assert "64KB" in body["message"]


def test_import_malformed_json_returns_422(card_harness: CardHarness) -> None:
    """JSON 语法错误 → 422 ``INVALID_INPUT``。"""
    resp = _import(card_harness.client, b"{not json", key="import-bad-json-key-0001")
    _assert_error(resp, 422, "INVALID_INPUT")


def test_import_non_utf8_returns_422(card_harness: CardHarness) -> None:
    """非 UTF-8 字节 → 422 ``INVALID_INPUT``。"""
    resp = _import(
        card_harness.client,
        b'\xff\xfe{"schema_version": "1.0"}',
        key="import-encoding-key-0001",
    )
    _assert_error(resp, 422, "INVALID_INPUT")


def test_import_non_json_filename_returns_422(card_harness: CardHarness) -> None:
    """非 ``.json`` 文件名 → 422 ``INVALID_INPUT``。"""
    resp = _import(
        card_harness.client,
        _json_bytes(_card()),
        key="import-filename-key-0001",
        filename="card.txt",
    )
    _assert_error(resp, 422, "INVALID_INPUT")


def test_import_non_json_content_type_returns_422(card_harness: CardHarness) -> None:
    """非 JSON 类 Content-Type → 422 ``INVALID_INPUT``。"""
    resp = _import(
        card_harness.client,
        _json_bytes(_card()),
        key="import-ctype-key-0001",
        content_type="text/plain",
    )
    _assert_error(resp, 422, "INVALID_INPUT")


def test_import_duplicate_json_keys_returns_422(card_harness: CardHarness) -> None:
    """JSON 对象重复键 → 直接拒绝 422 ``INVALID_INPUT``（第 15 章防注入）。"""
    content = b'{"schema_version":"1.0","name":"a","name":"b"}'
    resp = _import(card_harness.client, content, key="import-dup-json-key-0001")
    _assert_error(resp, 422, "INVALID_INPUT")


def test_import_too_deep_json_returns_422(card_harness: CardHarness) -> None:
    """JSON 嵌套深度 >8 → 422 ``INVALID_INPUT``。"""
    deep = b'{"a":' * 10 + b"{}" + b"}" * 10
    resp = _import(card_harness.client, deep, key="import-deep-key-0001")
    _assert_error(resp, 422, "INVALID_INPUT")


def test_import_schema_violation_returns_422_with_violations(
    card_harness: CardHarness,
) -> None:
    """``hp > max_hp`` → 422 ``SCHEMA_VIOLATION``（带 violations，loc 指出 hp）。"""
    resp = _import(
        card_harness.client,
        _json_bytes(_card(hp=99)),
        key="import-schema-key-0001",
    )
    body = _assert_error(resp, 422, "SCHEMA_VIOLATION")
    assert body["violations"]
    locations = [".".join(violation["loc"]) for violation in body["violations"]]
    assert any("hp" in location for location in locations)


def test_import_extra_field_returns_422_schema_violation(card_harness: CardHarness) -> None:
    """多余字段（extra=forbid）→ 422 ``SCHEMA_VIOLATION``。"""
    resp = _import(
        card_harness.client,
        _json_bytes(_card(surprise="extra")),
        key="import-extra-key-0001",
    )
    _assert_error(resp, 422, "SCHEMA_VIOLATION")


def test_import_missing_attribute_key_returns_422(card_harness: CardHarness) -> None:
    """``attributes`` 缺键/多键 → 422 ``SCHEMA_VIOLATION``。"""
    attributes = dict(_ATTRIBUTES)
    attributes.pop("cha")
    resp = _import(
        card_harness.client,
        _json_bytes(_card(attributes=attributes)),
        key="import-attrs-key-0001",
    )
    _assert_error(resp, 422, "SCHEMA_VIOLATION")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("background", '<script>alert("xss")</script>'),
        ("appearance", "javascript:alert(1)"),
        ("name", "<b>坏名字</b>"),
        ("background", "参见 https://evil.example/payload"),
        ("background", "带控制字符\x00的背景"),
        ("background", "实体绕过&#x3c;script&#x3e;"),
    ],
)
def test_import_xss_or_forbidden_content_returns_403(
    card_harness: CardHarness, field: str, value: str
) -> None:
    """HTML/脚本/JS URI/URL/控制字符/实体 —— 内容安全 → 403 ``NOT_ALLOWED``（R-L3/R-C2）。"""
    resp = _import(
        card_harness.client,
        _json_bytes(_card(**{field: value})),
        key=f"import-xss-key-{field}-{abs(hash(value)) % 1000:03d}",
    )
    body = _assert_error(resp, 403, "NOT_ALLOWED")
    assert body["violations"]
    assert body["violations"][0]["loc"] == [field]


# ---------------------------------------------------------------------------
# import：幂等
# ---------------------------------------------------------------------------
def test_import_requires_idempotency_key(card_harness: CardHarness) -> None:
    """写端点缺 ``Idempotency-Key`` → 422 ``INVALID_INPUT``。"""
    resp = card_harness.client.post(
        "/api/v1/character-cards/import",
        files={"file": ("card.json", _json_bytes(_card()), "application/json")},
    )
    _assert_error(resp, 422, "INVALID_INPUT")


def test_import_same_key_same_params_replays_first_result(card_harness: CardHarness) -> None:
    """同键同参 → 回放首次结果（同 card_id），只落一张卡。"""
    content = _json_bytes(_card())
    first = _import_ok(card_harness.client, content, key="import-idem-key-0001")
    second = _import_ok(card_harness.client, content, key="import-idem-key-0001")

    assert second == first
    assert card_harness.client.get("/api/v1/character-cards").json()["total"] == 1


def test_import_same_key_different_params_returns_409(card_harness: CardHarness) -> None:
    """同键异参 → 409 ``CONFLICT``（第 6 章 §6.7）。"""
    _import_ok(
        card_harness.client, _json_bytes(_card(name="灰烬旅人")), key="import-conflict-key-0001"
    )
    resp = _import(
        card_harness.client,
        _json_bytes(_card(name="另一个名字")),
        key="import-conflict-key-0001",
    )
    _assert_error(resp, 409, "CONFLICT")


def test_import_failure_is_replayed_with_same_key(card_harness: CardHarness) -> None:
    """首次失败也进幂等缓存：同键同参重试得到相同失败；异参 → 409。"""
    bad = _json_bytes(_card(background="<script>x</script>"))
    first = _import(card_harness.client, bad, key="import-fail-key-0001")
    _assert_error(first, 403, "NOT_ALLOWED")
    replay = _import(card_harness.client, bad, key="import-fail-key-0001")
    _assert_error(replay, 403, "NOT_ALLOWED")
    changed = _import(card_harness.client, _json_bytes(_card()), key="import-fail-key-0001")
    _assert_error(changed, 409, "CONFLICT")
    assert card_harness.client.get("/api/v1/character-cards").json()["total"] == 0


# ---------------------------------------------------------------------------
# validate：查询式校验（只读无副作用）
# ---------------------------------------------------------------------------
def _post_validate(client: TestClient, content: bytes) -> Any:
    return client.post("/api/v1/character-cards/validate", content=content)


def test_validate_valid_card_returns_200_valid(card_harness: CardHarness) -> None:
    """合法卡 → 200 ``{valid: true, errors: []}``，且不落库（只读无副作用）。"""
    resp = _post_validate(card_harness.client, _json_bytes(_card()))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"valid", "errors"}
    assert body == {"valid": True, "errors": []}
    assert card_harness.client.get("/api/v1/character-cards").json()["total"] == 0
    assert _event_count(card_harness.engine) == 0


def test_validate_invalid_card_returns_200_with_errors(card_harness: CardHarness) -> None:
    """``hp > max_hp`` → 仍 200，``valid: false``，errors 项为 ``{loc, type, msg}``。"""
    resp = _post_validate(card_harness.client, _json_bytes(_card(hp=99)))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["valid"] is False
    assert body["errors"]
    for error in body["errors"]:
        assert set(error) == {"loc", "type", "msg"}
    assert any("hp" in ".".join(error["loc"]) for error in body["errors"])


def test_validate_unknown_template_reports_error(card_harness: CardHarness) -> None:
    """模板不存在 → ``valid: false``，错误定位到 ``starting_items``。"""
    resp = _post_validate(
        card_harness.client, _json_bytes(_card(starting_items=["item.no_such_template"]))
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["valid"] is False
    assert any(error["loc"] == ["starting_items"] for error in body["errors"])


def test_validate_xss_reports_content_error(card_harness: CardHarness) -> None:
    """XSS 内容 → ``valid: false``，错误类型 ``content_not_allowed``。"""
    resp = _post_validate(
        card_harness.client, _json_bytes(_card(background="<script>alert(1)</script>"))
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is False
    assert any(error["type"] == "content_not_allowed" for error in body["errors"])


def test_validate_non_json_returns_422(card_harness: CardHarness) -> None:
    """非 JSON 请求体 → 422 ``INVALID_INPUT``（第 6 章 §6.7 查询式校验例外）。"""
    resp = _post_validate(card_harness.client, b"not-json")
    _assert_error(resp, 422, "INVALID_INPUT")


def test_validate_oversized_body_returns_413(card_harness: CardHarness) -> None:
    """校验请求体 >64KB → 413 ``INVALID_INPUT``。"""
    oversized = b'{"padding":"' + b"a" * (64 * 1024) + b'"}'
    resp = _post_validate(card_harness.client, oversized)
    _assert_error(resp, 413, "INVALID_INPUT")


def test_validate_needs_no_idempotency_key(card_harness: CardHarness) -> None:
    """``validate`` 只读无副作用，不需要 ``Idempotency-Key``。"""
    resp = card_harness.client.post(
        "/api/v1/character-cards/validate", content=_json_bytes(_card())
    )
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# save（POST /character-cards）
# ---------------------------------------------------------------------------
def test_save_manual_card_returns_201(card_harness: CardHarness) -> None:
    """保存手填卡 → 201 ``{card_id, card}``；source=manual 保留、card_id 服务端生成。"""
    resp = _post_json(
        card_harness.client,
        "/api/v1/character-cards",
        _json_bytes(_card(source="manual", generated_by=None)),
        key="save-key-0001",
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert set(body) == {"card_id", "card"}
    assert body["card"]["source"] == "manual"
    assert body["card_id"].startswith("card_")
    listed = card_harness.client.get("/api/v1/character-cards").json()
    assert listed["total"] == 1
    assert listed["cards"][0]["card_id"] == body["card_id"]


def test_save_rejects_upload_source(card_harness: CardHarness) -> None:
    """保存入口的 body.source=upload → 422 ``INVALID_INPUT``（source 与入口不符）。"""
    resp = _post_json(
        card_harness.client,
        "/api/v1/character-cards",
        _json_bytes(_card(source="upload")),
        key="save-source-key-0001",
    )
    _assert_error(resp, 422, "INVALID_INPUT")


def test_save_requires_idempotency_key(card_harness: CardHarness) -> None:
    """保存缺幂等键 → 422 ``INVALID_INPUT``。"""
    resp = _post_json(
        card_harness.client,
        "/api/v1/character-cards",
        _json_bytes(_card(source="manual")),
        key=None,
    )
    _assert_error(resp, 422, "INVALID_INPUT")


def test_save_schema_violation_returns_422(card_harness: CardHarness) -> None:
    """保存非法卡 → 422 ``SCHEMA_VIOLATION``（带 violations）。"""
    resp = _post_json(
        card_harness.client,
        "/api/v1/character-cards",
        _json_bytes(_card(source="manual", hp=99)),
        key="save-schema-key-0001",
    )
    body = _assert_error(resp, 422, "SCHEMA_VIOLATION")
    assert body["violations"]


def test_save_xss_returns_403(card_harness: CardHarness) -> None:
    """保存时同样执行内容安全 → 403 ``NOT_ALLOWED``。"""
    resp = _post_json(
        card_harness.client,
        "/api/v1/character-cards",
        _json_bytes(_card(source="manual", appearance="<img src=x onerror=alert(1)>")),
        key="save-xss-key-0001",
    )
    _assert_error(resp, 403, "NOT_ALLOWED")


def test_save_unknown_template_returns_404(card_harness: CardHarness) -> None:
    """保存时模板不存在 → 404 ``NOT_FOUND``。"""
    resp = _post_json(
        card_harness.client,
        "/api/v1/character-cards",
        _json_bytes(_card(source="manual", starting_items=["item.no_such_template"])),
        key="save-template-key-0001",
    )
    _assert_error(resp, 404, "NOT_FOUND")


def test_save_oversized_body_returns_413(card_harness: CardHarness) -> None:
    """保存请求体 >64KB → 413 ``INVALID_INPUT``。"""
    oversized = b'{"schema_version":"1.0","padding":"' + b"a" * (64 * 1024) + b'"}'
    resp = _post_json(
        card_harness.client, "/api/v1/character-cards", oversized, key="save-big-key-0001"
    )
    _assert_error(resp, 413, "INVALID_INPUT")


def test_save_same_key_same_params_replays(card_harness: CardHarness) -> None:
    """保存同键同参 → 回放首次 card_id，只落一张卡。"""
    content = _json_bytes(_card(source="manual"))
    first = _post_json(
        card_harness.client, "/api/v1/character-cards", content, key="save-idem-key-0001"
    )
    assert first.status_code == 201, first.text
    second = _post_json(
        card_harness.client, "/api/v1/character-cards", content, key="save-idem-key-0001"
    )
    assert second.status_code == 201, second.text
    assert second.json() == first.json()
    assert card_harness.client.get("/api/v1/character-cards").json()["total"] == 1


def test_save_same_key_different_params_returns_409(card_harness: CardHarness) -> None:
    """保存同键异参 → 409 ``CONFLICT``。"""
    _post_json(
        card_harness.client,
        "/api/v1/character-cards",
        _json_bytes(_card(source="manual", name="灰烬旅人")),
        key="save-conflict-key-0001",
    )
    resp = _post_json(
        card_harness.client,
        "/api/v1/character-cards",
        _json_bytes(_card(source="manual", name="另一个名字")),
        key="save-conflict-key-0001",
    )
    _assert_error(resp, 409, "CONFLICT")


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------
def _create_card(card_harness: CardHarness, *, key: str = "create-for-delete-0001") -> str:
    created = _import_ok(card_harness.client, _json_bytes(_card()), key=key, filename="card.json")
    card_id: str = created["card_id"]
    return card_id


def test_delete_returns_204_and_soft_deletes(card_harness: CardHarness) -> None:
    """删除 → 204 无 body；再 GET 404，列表不再包含（软删除）。"""
    card_id = _create_card(card_harness)

    resp = card_harness.client.delete(
        f"/api/v1/character-cards/{card_id}",
        headers={"Idempotency-Key": "delete-key-0001"},
    )
    assert resp.status_code == 204, resp.text
    assert resp.content == b""

    _assert_error(card_harness.client.get(f"/api/v1/character-cards/{card_id}"), 404, "NOT_FOUND")
    assert card_harness.client.get("/api/v1/character-cards").json()["total"] == 0


def test_delete_requires_idempotency_key(card_harness: CardHarness) -> None:
    """删除缺幂等键 → 422 ``INVALID_INPUT``。"""
    card_id = _create_card(card_harness, key="create-for-delete-0002")
    resp = card_harness.client.delete(f"/api/v1/character-cards/{card_id}")
    _assert_error(resp, 422, "INVALID_INPUT")


def test_delete_same_key_replays_204(card_harness: CardHarness) -> None:
    """同键重放删除 → 仍 204（幂等回放首次响应）。"""
    card_id = _create_card(card_harness, key="create-for-delete-0003")
    headers = {"Idempotency-Key": "delete-replay-key-0001"}
    first = card_harness.client.delete(f"/api/v1/character-cards/{card_id}", headers=headers)
    second = card_harness.client.delete(f"/api/v1/character-cards/{card_id}", headers=headers)
    assert first.status_code == second.status_code == 204


def test_delete_unknown_card_returns_404(card_harness: CardHarness) -> None:
    """删除不存在的卡 → 404 ``NOT_FOUND``。"""
    resp = card_harness.client.delete(
        f"/api/v1/character-cards/{_VALID_CARD_ID}",
        headers={"Idempotency-Key": "delete-unknown-key-0001"},
    )
    _assert_error(resp, 404, "NOT_FOUND")


def test_delete_twice_with_new_key_returns_404(card_harness: CardHarness) -> None:
    """已删除的卡用新幂等键再删 → 404（软删除不可重复命中）。"""
    card_id = _create_card(card_harness, key="create-for-delete-0004")
    first = card_harness.client.delete(
        f"/api/v1/character-cards/{card_id}",
        headers={"Idempotency-Key": "delete-newkey-key-0001"},
    )
    assert first.status_code == 204
    second = card_harness.client.delete(
        f"/api/v1/character-cards/{card_id}",
        headers={"Idempotency-Key": "delete-newkey-key-0002"},
    )
    _assert_error(second, 404, "NOT_FOUND")


# ---------------------------------------------------------------------------
# 全局安全收口：所有写路径零事件、错误信封不含内部细节
# ---------------------------------------------------------------------------
def test_card_writes_never_touch_event_sourcing(card_harness: CardHarness) -> None:
    """卡库写操作全程 events/snapshots 表保持空（人物卡不参与事件溯源）。"""
    content = _json_bytes(_card())
    created = _import_ok(card_harness.client, content, key="no-events-import-0001")
    _post_json(
        card_harness.client,
        "/api/v1/character-cards",
        _json_bytes(_card(source="manual")),
        key="no-events-save-0001",
    )
    _post_validate(card_harness.client, content)
    card_harness.client.delete(
        f"/api/v1/character-cards/{created['card_id']}",
        headers={"Idempotency-Key": "no-events-delete-0001"},
    )

    assert _event_count(card_harness.engine) == 0
    with card_harness.engine.connect() as connection:
        snapshots = connection.execute(text("SELECT COUNT(*) FROM snapshots")).scalar()
    assert snapshots == 0


def test_error_envelope_hides_internal_details(card_harness: CardHarness) -> None:
    """错误信封只有 ``ok/error_code/message[/violations]``，不回显 SQL/堆栈等内部细节。"""
    resp = _import(card_harness.client, b"{bad", key="envelope-key-0001")
    body = _assert_error(resp, 422, "INVALID_INPUT")
    assert "Traceback" not in body["message"]
    assert "sqlite" not in body["message"].lower()
