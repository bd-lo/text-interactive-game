"""集成测试：``POST /api/v1/character-cards/generate``（M6-028 后端前置，第 12 章 §12.6/第 23 章 §23.6）。

覆盖：

- 默认离线（未注入生成器）→ 确定性模板兜底：9 字段草稿、固定属性键、``source="manual"``、
  ``generated_by=null``、``validation.warnings`` 含 ``TEMPLATE_FALLBACK``；同 brief 确定性；
- LLM 路径（``FakeLLM`` + ``LLMCardDraftGenerator`` 注入）：成功（source=llm/generated_by）、
  Schema 违规 → 恰好修复一次 → 成功；修复后仍不合法 → 模板兜底；LLM 不可用 → 模板兜底；
  内容安全拒绝 → 403；未知物品模板 → 模板兜底；
- 限流：第 6 次 429 + ``Retry-After``；按 IP+session 隔离；
- 幂等：同键同参复用首结果（LLM 不重复调用）、同键异参 409、缺幂等键 422；
- 输入校验：brief 空/全空白/超长、schema_version 非法、多余字段、options 非对象/超大 → 422；
- 只读语义：不写卡库、不写 events/snapshots、不建局；草稿不落库；
- OpenAPI：``/generate`` 路径/请求体/响应/``Idempotency-Key`` 头。

装配：内存 SQLite 卡库 + 真实 ``CardService``（LLM 用 ``tests/fakes.FakeLLM`` 替身），零联网。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from fakes import FakeLLM
from textgame.api.app import create_app
from textgame.domain.character_card import MAX_BRIEF_CHARS
from textgame.llm.client import ModelTier
from textgame.persistence.card_repository import SqliteCardRepository
from textgame.persistence.database import init_schema, prepare_db
from textgame.services.card_service import CardService, LLMCardDraftGenerator

pytestmark = [pytest.mark.integration]

_ATTRIBUTES = {"str": 13, "dex": 14, "con": 11, "int": 9, "wis": 15, "cha": 8, "max_hp": 17}
_BRIEF = "一个在灰烬平原流浪的盲眼剑客，性格寡言"
_GENERATE_KEYS = {"draft", "validation", "generated_by"}
_DRAFT_FIELDS = {
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
_ATTRIBUTE_KEYS = {"str", "dex", "con", "int", "wis", "cha", "max_hp"}


@dataclass
class GenerateHarness:
    """卡库生成测试装配：HTTP 客户端 + 服务 + 底层 Engine（断言零写入）+ LLM 替身。"""

    client: TestClient
    service: CardService
    engine: Engine
    fake_llm: FakeLLM | None


def _valid_llm_draft(**overrides: Any) -> dict[str, Any]:
    """LLM 返回的合法 9 字段人物卡草稿（source=llm，第 23 章 §23.6）。"""
    draft: dict[str, Any] = {
        "schema_version": "1.0",
        "name": "盲眼剑客",
        "attributes": dict(_ATTRIBUTES),
        "hp": 17,
        "starting_items": ["item.oak_cane"],
        "background": "流浪在灰烬平原的盲眼剑客，靠听风辨位。",
        "appearance": None,
        "source": "llm",
        "generated_by": "deepseek-chat",
    }
    draft.update(overrides)
    return draft


def _build_harness(
    *, draft_generator: LLMCardDraftGenerator | None = None, fake_llm: FakeLLM | None = None
) -> GenerateHarness:
    """内存 SQLite 卡库 + CardService（可选注入 LLM 生成器）+ TestClient。"""
    engine, _factory = prepare_db("sqlite:///:memory:")
    init_schema(engine)
    service = CardService(SqliteCardRepository(engine), draft_generator=draft_generator)
    client = TestClient(create_app(card_service=service))
    return GenerateHarness(client=client, service=service, engine=engine, fake_llm=fake_llm)


@pytest.fixture
def generate_harness() -> Iterator[GenerateHarness]:
    """默认装配（无 LLM 生成器）：生成端点走确定性模板兜底。"""
    harness = _build_harness()
    with harness.client:
        yield harness
    harness.engine.dispose()


@pytest.fixture
def llm_harness() -> Iterator[GenerateHarness]:
    """注入 ``LLMCardDraftGenerator(FakeLLM)``：LLM 路径（成功/修复/兜底）测试用。"""
    fake_llm = FakeLLM()
    harness = _build_harness(
        draft_generator=LLMCardDraftGenerator(fake_llm, model_label="fake:model"),
        fake_llm=fake_llm,
    )
    with harness.client:
        yield harness
    harness.engine.dispose()


def _generate(
    harness: GenerateHarness,
    *,
    brief: str = _BRIEF,
    key: str,
    schema_version: str = "1.0",
    options: dict[str, Any] | None = None,
    session: str | None = None,
    extra: dict[str, Any] | None = None,
) -> Any:
    """``POST /generate``；``key=None`` 时不带幂等键（缺键用例）。"""
    headers: dict[str, str] = {}
    if key is not None:
        headers["Idempotency-Key"] = key
    if session is not None:
        headers["X-Session-Id"] = session
    body: dict[str, Any] = {"brief": brief, "schema_version": schema_version}
    if options is not None:
        body["options"] = options
    if extra is not None:
        body.update(extra)
    return harness.client.post("/api/v1/character-cards/generate", headers=headers, json=body)


def _generate_ok(harness: GenerateHarness, *, key: str, **kwargs: Any) -> dict[str, Any]:
    response = _generate(harness, key=key, **kwargs)
    assert response.status_code == 200, response.text
    payload: dict[str, Any] = response.json()
    return payload


def _assert_error(response: Any, status_code: int, error_code: str) -> dict[str, Any]:
    assert response.status_code == status_code, response.text
    body: dict[str, Any] = response.json()
    assert body.get("ok") is False
    assert body.get("error_code") == error_code
    assert isinstance(body.get("message"), str) and body["message"]
    return body


def _table_count(engine: Engine, table: str) -> int:
    with engine.connect() as connection:
        return int(connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0)


def _warning_codes(payload: dict[str, Any]) -> list[str]:
    warnings = payload["validation"]["warnings"]
    return [str(warning["code"]) for warning in warnings]


# ---------------------------------------------------------------------------
# 默认离线：确定性模板兜底
# ---------------------------------------------------------------------------
def test_fallback_returns_contract_payload(generate_harness: GenerateHarness) -> None:
    """默认离线 → 200 ``{draft, validation, generated_by}``；9 字段草稿 + TEMPLATE_FALLBACK。"""
    payload = _generate_ok(generate_harness, key="gen-fallback-key-0001")

    assert set(payload) == _GENERATE_KEYS
    draft = payload["draft"]
    assert set(draft) == _DRAFT_FIELDS
    assert draft["schema_version"] == "1.0"
    assert draft["source"] == "manual"
    assert draft["generated_by"] is None
    assert set(draft["attributes"]) == _ATTRIBUTE_KEYS
    assert all(isinstance(value, int) for value in draft["attributes"].values())
    assert 0 <= draft["hp"] <= draft["attributes"]["max_hp"]
    assert payload["generated_by"] is None
    assert payload["validation"]["valid"] is True
    assert payload["validation"]["errors"] == []
    assert _warning_codes(payload) == ["TEMPLATE_FALLBACK"]
    warning = payload["validation"]["warnings"][0]
    assert set(warning) <= {"code", "message", "field"}
    assert _BRIEF[:6] in draft["background"]


def test_fallback_is_deterministic(generate_harness: GenerateHarness) -> None:
    """同一 brief 的模板草稿确定性一致（不同幂等键也是同一草稿）。"""
    first = _generate_ok(generate_harness, key="gen-deterministic-key-0001")
    second = _generate_ok(generate_harness, key="gen-deterministic-key-0002")

    assert first["draft"] == second["draft"]
    assert first["generated_by"] is second["generated_by"] is None


def test_fallback_sanitizes_brief_injection(generate_harness: GenerateHarness) -> None:
    """brief 里的 HTML/脚本/URL 标记被摘要阶段剔除，兜底草稿仍过内容安全。"""
    payload = _generate_ok(
        generate_harness,
        key="gen-sanitize-key-0001",
        brief="<script>alert(1)</script>一个剑客 https://evil.example/x",
    )

    draft = payload["draft"]
    assert "<" not in draft["name"] + draft["background"]
    assert "<script" not in draft["background"]
    assert "javascript:" not in draft["background"]
    assert "https://" not in draft["background"]
    assert payload["validation"]["valid"] is True


def test_generate_does_not_write_anything(generate_harness: GenerateHarness) -> None:
    """生成草稿不落卡库、不写 events/snapshots、不建局（只写幂等记录）。"""
    _generate_ok(generate_harness, key="gen-nowrite-key-0001")
    _generate_ok(generate_harness, key="gen-nowrite-key-0002", brief="另一位旅人")

    listed = generate_harness.client.get("/api/v1/character-cards").json()
    assert listed["total"] == 0
    assert _table_count(generate_harness.engine, "character_cards") == 0
    assert _table_count(generate_harness.engine, "events") == 0
    assert _table_count(generate_harness.engine, "snapshots") == 0


# ---------------------------------------------------------------------------
# LLM 路径（FakeLLM 注入）
# ---------------------------------------------------------------------------
def test_llm_success_returns_llm_draft(llm_harness: GenerateHarness) -> None:
    """LLM 成功 → ``source="llm"``、``generated_by`` 为模型标识、无兜底 warning。"""
    assert llm_harness.fake_llm is not None
    llm_harness.fake_llm.reset([_valid_llm_draft()])

    payload = _generate_ok(llm_harness, key="gen-llm-key-0001")

    assert payload["draft"]["source"] == "llm"
    assert payload["draft"]["name"] == "盲眼剑客"
    assert payload["generated_by"] == "fake:model"
    assert payload["draft"]["generated_by"] == "fake:model"
    assert payload["validation"]["warnings"] == []
    assert len(llm_harness.fake_llm.calls) == 1
    request = llm_harness.fake_llm.calls[0]
    assert request.task == "character_card_generate"
    assert request.model_tier is ModelTier.STRONG
    assert request.max_repair == 1
    assert request.output_schema is not None
    assert request.agent_id == "character_card"
    assert request.timeout_s == 20.0
    assert any(_BRIEF in message.content for message in request.messages)


def test_llm_schema_violation_is_repaired_once(llm_harness: GenerateHarness) -> None:
    """LLM 首次输出非 JSON → 恰好一次修复请求 → 修复后成功（不再兜底）。"""
    assert llm_harness.fake_llm is not None
    llm_harness.fake_llm.reset(["not-json", _valid_llm_draft()])

    payload = _generate_ok(llm_harness, key="gen-repair-key-0001")

    assert payload["draft"]["source"] == "llm"
    assert payload["validation"]["warnings"] == []
    assert len(llm_harness.fake_llm.calls) == 2
    repair_request = llm_harness.fake_llm.calls[1]
    assert repair_request.max_repair == 0  # 修复请求不得再次递归修复
    assert len(repair_request.messages) == 3


def test_llm_schema_violation_after_repair_falls_back(llm_harness: GenerateHarness) -> None:
    """修复后仍不合法 → 确定性模板兜底（200，不报错，TEMPLATE_FALLBACK）。"""
    assert llm_harness.fake_llm is not None
    llm_harness.fake_llm.reset(["not-json", "still-not-json"])

    payload = _generate_ok(llm_harness, key="gen-repair-fallback-key-0001")

    assert len(llm_harness.fake_llm.calls) == 2
    assert payload["draft"]["source"] == "manual"
    assert payload["generated_by"] is None
    assert _warning_codes(payload) == ["TEMPLATE_FALLBACK"]


def test_llm_unavailable_falls_back(llm_harness: GenerateHarness) -> None:
    """LLM 不可用（离线客户端/Fake 耗尽）→ 模板兜底，warning 带失败原因。"""
    payload = _generate_ok(llm_harness, key="gen-unavailable-key-0001")

    assert payload["draft"]["source"] == "manual"
    assert payload["generated_by"] is None
    assert _warning_codes(payload) == ["TEMPLATE_FALLBACK"]
    assert "LLM_UNAVAILABLE" in payload["validation"]["warnings"][0]["message"]
    assert llm_harness.fake_llm is not None
    assert len(llm_harness.fake_llm.calls) == 1


def test_llm_unknown_item_template_falls_back(llm_harness: GenerateHarness) -> None:
    """LLM 草稿引用未知物品模板 → 不报 404，回退确定性模板草稿。"""
    assert llm_harness.fake_llm is not None
    llm_harness.fake_llm.reset([_valid_llm_draft(starting_items=["item.no_such_template"])])

    payload = _generate_ok(llm_harness, key="gen-unknown-template-key-0001")

    assert payload["draft"]["source"] == "manual"
    assert _warning_codes(payload) == ["TEMPLATE_FALLBACK"]


def test_llm_unsafe_content_returns_403(llm_harness: GenerateHarness) -> None:
    """LLM 草稿通过 Schema 但内容安全拒绝 → 403 ``NOT_ALLOWED``（不回退、不返回草稿）。"""
    assert llm_harness.fake_llm is not None
    llm_harness.fake_llm.reset([_valid_llm_draft(background="<script>alert(1)</script>")])

    response = _generate(llm_harness, key="gen-unsafe-key-0001")

    body = _assert_error(response, 403, "NOT_ALLOWED")
    assert body["violations"]
    assert body["violations"][0]["loc"] == ["background"]


# ---------------------------------------------------------------------------
# 限流（5 次/分钟，IP+session 进程内滑窗）
# ---------------------------------------------------------------------------
def test_sixth_request_returns_429_with_retry_after(generate_harness: GenerateHarness) -> None:
    """前 5 次 200；第 6 次 429 ``OUT_OF_RANGE`` + ``Retry-After`` 头。"""
    for index in range(5):
        _generate_ok(generate_harness, key=f"gen-rate-key-{index:04d}", brief=f"第 {index} 位旅人")

    response = _generate(generate_harness, key="gen-rate-key-9999", brief="第 6 位旅人")

    body = _assert_error(response, 429, "OUT_OF_RANGE")
    retry_after = response.headers.get("Retry-After")
    assert retry_after is not None and retry_after.isdigit()
    assert 1 <= int(retry_after) <= 60
    assert "频繁" in body["message"]


def test_rate_limit_is_per_ip_and_cannot_be_bypassed_by_session(
    generate_harness: GenerateHarness,
) -> None:
    """R-09：限流主体只有 IP；轮换 ``X-Session-Id`` 不能绕过配额。"""
    for index in range(5):
        _generate_ok(
            generate_harness,
            key=f"gen-session-a-key-{index:04d}",
            brief=f"A 会话第 {index} 位旅人",
            session="session-a",
        )
    other = _generate(
        generate_harness,
        key="gen-session-b-key-0001",
        brief="B 会话旅人",
        session="session-b",
    )
    _assert_error(other, 429, "OUT_OF_RANGE")
    blocked = _generate(
        generate_harness,
        key="gen-session-a-key-9999",
        brief="A 会话第 6 位旅人",
        session="session-a",
    )
    _assert_error(blocked, 429, "OUT_OF_RANGE")


# ---------------------------------------------------------------------------
# 幂等
# ---------------------------------------------------------------------------
def test_same_key_same_params_replays_first_result(generate_harness: GenerateHarness) -> None:
    """同键同参 → 回放首次响应（含 draft/validation），不重复生成副作用。"""
    first = _generate_ok(generate_harness, key="gen-idem-key-0001")
    second = _generate_ok(generate_harness, key="gen-idem-key-0001")

    assert second == first


def test_same_key_different_params_returns_409(generate_harness: GenerateHarness) -> None:
    """同键异参（brief/options 变了）→ 409 ``CONFLICT``。"""
    _generate_ok(generate_harness, key="gen-idem-conflict-key-0001")
    response = _generate(
        generate_harness, key="gen-idem-conflict-key-0001", brief="另一个完全不同的 brief"
    )
    _assert_error(response, 409, "CONFLICT")


def test_llm_idempotent_replay_does_not_call_llm_again(llm_harness: GenerateHarness) -> None:
    """同键同参重放命中幂等记录，不重复调用 LLM。"""
    assert llm_harness.fake_llm is not None
    llm_harness.fake_llm.reset([_valid_llm_draft()])

    first = _generate_ok(llm_harness, key="gen-idem-llm-key-0001")
    second = _generate_ok(llm_harness, key="gen-idem-llm-key-0001")

    assert second == first
    assert len(llm_harness.fake_llm.calls) == 1


def test_generate_requires_idempotency_key(generate_harness: GenerateHarness) -> None:
    """缺 ``Idempotency-Key`` → 422 ``INVALID_INPUT``。"""
    response = _generate(generate_harness, key=None)
    _assert_error(response, 422, "INVALID_INPUT")


# ---------------------------------------------------------------------------
# 输入校验
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "body",
    [
        {"brief": ""},
        {"brief": "   "},
        {"brief": "x" * (MAX_BRIEF_CHARS + 1)},
        {"brief": _BRIEF, "schema_version": "2.0"},
        {"brief": _BRIEF, "surprise": 1},
        {"brief": _BRIEF, "options": ["not", "an", "object"]},
    ],
)
def test_invalid_generate_body_returns_422(
    generate_harness: GenerateHarness, body: dict[str, Any]
) -> None:
    """brief/schema_version/多余字段/options 类型非法 → 422 ``INVALID_INPUT`` 信封。"""
    response = generate_harness.client.post(
        "/api/v1/character-cards/generate",
        headers={"Idempotency-Key": "gen-invalid-body-key-0001"},
        json=body,
    )
    _assert_error(response, 422, "INVALID_INPUT")


def test_oversized_options_returns_422(generate_harness: GenerateHarness) -> None:
    """``options`` 序列化后 >4KB → 422 ``INVALID_INPUT``（防畸形大对象）。"""
    response = _generate(
        generate_harness,
        key="gen-options-key-0001",
        options={"padding": "x" * 5000},
    )
    _assert_error(response, 422, "INVALID_INPUT")


def test_empty_brief_does_not_consume_cache_or_rate_quota(
    generate_harness: GenerateHarness,
) -> None:
    """非法 brief 在校验阶段拒绝：不写幂等记录，且 5 次合法生成仍全部通过。"""
    for index in range(3):
        response = _generate(generate_harness, key=f"gen-bad-{index:04d}", brief="   ")
        _assert_error(response, 422, "INVALID_INPUT")
    for index in range(5):
        _generate_ok(generate_harness, key=f"gen-good-{index:04d}", brief=f"合法旅人 {index}")


# ---------------------------------------------------------------------------
# OpenAPI
# ---------------------------------------------------------------------------
def test_openapi_documents_generate_endpoint(generate_harness: GenerateHarness) -> None:
    """live OpenAPI：``/generate`` 请求体/响应/幂等头齐备（前端 028 的类型源）。"""
    paths = generate_harness.client.app.openapi()["paths"]
    operation = paths["/api/v1/character-cards/generate"]["post"]

    request_schema = operation["requestBody"]["content"]["application/json"]["schema"]
    assert request_schema["$ref"].endswith("/CardGenerateBody")
    assert "200" in operation["responses"]
    headers = {
        parameter["name"]
        for parameter in operation.get("parameters", [])
        if parameter.get("in") == "header"
    }
    assert "Idempotency-Key" in headers
