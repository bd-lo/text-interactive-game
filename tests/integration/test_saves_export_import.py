"""N3-048 存档导出/导入验收：导出→导入→/view 续玩、回放哈希一致、篡改/超限拒绝、幂等。

装配口径与 ``test_player_journey.py`` 一致：临时 SQLite + 进程内组合根（``main._build_app``，
不占端口、不联网）；默认离线确定性叙事。

覆盖：
1. 导出：包字段白名单（无 API Key/凭据/日志）、事件/当前头/checksum、未知存档 404；
2. 导入：新 ``game_id`` + 事件落库 + ``/view`` 续玩 + 回放 ``state_hash`` 与导出端一致；
3. 拒绝：checksum 篡改、事件回放不一致、``state_hash``/版本不一致、未知字段、
   不支持版本、事件数/包体积超限，全部 422/413 且零写入；
4. 幂等：同键同包回放首次 201、同键异包 409；导入不改源存档、重启后仍可读。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from textgame import console
from textgame.config.settings import get_settings
from textgame.services.save_service import (
    MAX_SAVE_EVENTS,
    MAX_SAVE_PACKAGE_BYTES,
    SavePackage,
    package_checksum,
)

pytestmark = [pytest.mark.integration]

_PACKAGE_KEYS = {"export_version", "exported_at", "game", "events", "snapshot", "checksum"}
_EVENT_KEYS = {
    "id",
    "seq",
    "turn_id",
    "actor_id",
    "method",
    "event_type",
    "params",
    "result",
    "rng_seed",
    "ts",
}
_GAME_KEYS = {
    "game_id",
    "turn",
    "version",
    "snapshot_version",
    "save_seed",
    "scenario",
    "created_at",
    "event_count",
}
_SNAPSHOT_KEYS = {"state_hash", "version", "last_event_seq", "turn", "snapshot_version"}


def _dispose(app: FastAPI) -> None:
    """释放组合根内 SQLite Engine 的文件句柄（Windows 临时文件收尾）。"""
    console.dispose_app_engine(app)


def _build_app(monkeypatch: pytest.MonkeyPatch, db_path: Path) -> FastAPI:
    """把 ``TEXTGAME_DB_URL`` 指向临时库后重建生产组合根。"""
    monkeypatch.setenv("TEXTGAME_DB_URL", f"sqlite:///{db_path.as_posix()}")
    get_settings.cache_clear()
    import textgame.main as main_module

    _dispose(main_module.app)
    return main_module._build_app()


@pytest.fixture
def save_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[tuple[TestClient, Path]]:
    """临时库 + 生产组合根 + ``TestClient``（用例结束释放 Engine）。"""
    db_path = tmp_path / "saves.db"
    app = _build_app(monkeypatch, db_path)
    try:
        with TestClient(app) as client:
            yield client, db_path
    finally:
        _dispose(app)


def _key(prefix: str) -> str:
    """唯一幂等键（长度 8..128）。"""
    return f"{prefix}-{uuid.uuid4().hex}"


def _new_game(client: TestClient, *, seed: int = 42) -> dict[str, Any]:
    """``POST /games`` 新建 m2 存档 → 响应体。"""
    response = client.post(
        "/api/v1/games",
        headers={"Idempotency-Key": _key("saves-create")},
        json={"seed": seed},
    )
    assert response.status_code == 201, response.text
    payload: dict[str, Any] = response.json()
    return payload


def _play(client: TestClient, game_id: str, version: int, inputs: list[str]) -> int:
    """依次提交行动（HTTP 200），断言每个回合都成功，返回最新版本号。"""
    for text in inputs:
        response = client.post(
            "/api/v1/turns",
            headers={"Idempotency-Key": _key("saves-turn")},
            json={"game_id": game_id, "input": text, "expected_version": version},
        )
        assert response.status_code == 200, response.text
        result: dict[str, Any] = response.json()
        assert result.get("ok") is True, (text, result)
        version = int(result["version"])
    return version


def _export(client: TestClient, game_id: str) -> dict[str, Any]:
    """``GET /games/{id}/export`` → 存档包（断言 200）。"""
    response = client.get(f"/api/v1/games/{game_id}/export")
    assert response.status_code == 200, response.text
    payload: dict[str, Any] = response.json()
    return payload


def _import(client: TestClient, package: dict[str, Any], *, key: str | None = None) -> Any:
    """``POST /saves/import`` → 响应（幂等键缺省随机）。"""
    return client.post(
        "/api/v1/saves/import",
        headers={"Idempotency-Key": key or _key("saves-import")},
        json=package,
    )


def _resign(package: dict[str, Any]) -> dict[str, Any]:
    """深拷贝并重算 checksum（测试「篡改后重新签名」以进入回放/一致性校验分支）。"""
    resigned: dict[str, Any] = json.loads(json.dumps(package))
    resigned["checksum"] = package_checksum(resigned)
    return resigned


def _games_total(client: TestClient) -> int:
    """当前存档总数（零写入断言用）。"""
    response = client.get("/api/v1/games")
    assert response.status_code == 200, response.text
    return int(response.json()["total"])


def _played_save(client: TestClient) -> tuple[str, dict[str, Any]]:
    """建一局并推进 3 个有事件回合 → ``(game_id, 导出包)``。"""
    created = _new_game(client)
    game_id = str(created["game_id"])
    _play(client, game_id, int(created["version"]), ["拿起火把", "向北走进地窖", "向南走出地窖"])
    return game_id, _export(client, game_id)


# ---------------------------------------------------------------------------
# 1. 导出：字段白名单 / 无密钥 / 未知存档 404
# ---------------------------------------------------------------------------
def test_export_package_shape_and_no_secrets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """导出包字段白名单（顶层/事件/头/快照），且不含 API Key/凭据/日志字样。"""
    monkeypatch.setenv("TEXTGAME_API_TOKEN", "super-secret-token-value")
    db_path = tmp_path / "saves-secrets.db"
    app = _build_app(monkeypatch, db_path)
    try:
        with TestClient(app) as client:
            game_id, package = _played_save(client)
    finally:
        _dispose(app)

    assert set(package) == _PACKAGE_KEYS
    assert package["export_version"] == "1"
    assert set(package["game"]) == _GAME_KEYS
    assert set(package["snapshot"]) == _SNAPSHOT_KEYS
    assert package["events"], "存档包至少包含 game_created 首事件"
    for event in package["events"]:
        assert set(event) == _EVENT_KEYS
    assert package["events"][0]["event_type"] == "game_created"
    assert package["game"]["event_count"] == len(package["events"])
    assert package["snapshot"]["last_event_seq"] == package["events"][-1]["seq"]
    assert package["checksum"] == package_checksum(package)
    assert len(package["checksum"]) == 64

    # 无凭据/密钥/日志：只从事件表/当前状态取值，不读 Settings。
    text = json.dumps(package, ensure_ascii=False).lower()
    for marker in ("api_key", "apikey", "authorization", "idempotency", "sk-", "secret"):
        assert marker not in text, marker
    assert "super-secret-token-value" not in json.dumps(package, ensure_ascii=False)
    assert game_id == package["game"]["game_id"]


def test_export_unknown_game_is_404(save_env: tuple[TestClient, Path]) -> None:
    """未知/已删除存档导出 → 404 统一失败信封（只读、零写入）。"""
    client, _db_path = save_env

    response = client.get("/api/v1/games/game_0123456789abcdef0123456789abcdef/export")

    assert response.status_code == 404
    body = response.json()
    assert body["ok"] is False and body["error_code"] == "NOT_FOUND"
    assert _games_total(client) == 0


# ---------------------------------------------------------------------------
# 2. 导出 → 导入 → /view 续玩 + 回放哈希一致
# ---------------------------------------------------------------------------
def test_export_import_roundtrip_continues_and_hash_matches(
    save_env: tuple[TestClient, Path],
) -> None:
    """导入为新存档：视图/版本/回放哈希/事件派生值一致，且可在导入存档上继续回合。"""
    client, _db_path = save_env
    source_id, package = _played_save(client)
    source_view = client.get(f"/api/v1/games/{source_id}/view").json()
    source_replay = client.get(
        f"/api/v1/games/{source_id}/replay", params={"from_seq": 1, "limit": 500}
    ).json()["events"]

    response = _import(client, package)

    assert response.status_code == 201, response.text
    imported = response.json()
    assert set(imported) == {"game_id", "turn", "version", "snapshot_version"}
    imported_id = str(imported["game_id"])
    assert imported_id != source_id
    assert imported["turn"] == package["game"]["turn"]
    assert imported["version"] == package["game"]["version"]

    imported_view = client.get(f"/api/v1/games/{imported_id}/view").json()
    assert imported_view == {**source_view, "game_id": imported_id}
    imported_replay = client.get(
        f"/api/v1/games/{imported_id}/replay", params={"from_seq": 1, "limit": 500}
    ).json()["events"]
    assert len(imported_replay) == len(source_replay)
    assert [
        (event["seq"], event["turn_id"], event["actor_id"], event["method"], event["rng_seed"])
        for event in imported_replay
    ] == [
        (event["seq"], event["turn_id"], event["actor_id"], event["method"], event["rng_seed"])
        for event in source_replay
    ]

    imported_package = _export(client, imported_id)
    assert imported_package["snapshot"]["state_hash"] == package["snapshot"]["state_hash"]
    assert imported_package["game"]["save_seed"] == package["game"]["save_seed"]

    # 导入存档可继续玩：下一个成功回合照常推进版本链。
    next_turn = client.post(
        "/api/v1/turns",
        headers={"Idempotency-Key": _key("saves-continue")},
        json={
            "game_id": imported_id,
            "input": "向北走进地窖",
            "expected_version": imported["version"],
        },
    )
    assert next_turn.status_code == 200, next_turn.text
    assert next_turn.json().get("ok") is True
    assert next_turn.json()["version"] == imported["version"] + 1


def test_imported_save_persists_across_restart(
    save_env: tuple[TestClient, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """导入事件真正落库：关掉组合根重开同库后，导入存档仍可读。"""
    client, db_path = save_env
    _source_id, package = _played_save(client)
    imported = _import(client, package).json()
    imported_id = str(imported["game_id"])
    expected_view = client.get(f"/api/v1/games/{imported_id}/view").json()

    # 重开组合根（同一 SQLite 文件）
    second_app = _build_app(monkeypatch, db_path)
    try:
        with TestClient(second_app) as second:
            restored = second.get(f"/api/v1/games/{imported_id}/view")
            assert restored.status_code == 200, restored.text
            assert restored.json() == expected_view
    finally:
        _dispose(second_app)


def test_import_does_not_modify_source_save(save_env: tuple[TestClient, Path]) -> None:
    """导入只读源存档：源存档事件/状态哈希/版本不变。"""
    client, _db_path = save_env
    source_id, package = _played_save(client)

    assert _import(client, package).status_code == 201

    after = _export(client, source_id)
    assert after["snapshot"] == package["snapshot"]
    assert after["game"]["version"] == package["game"]["version"]
    assert after["events"] == package["events"]


# ---------------------------------------------------------------------------
# 3. 篡改 / 非法包：422 结构化 violations 且零写入
# ---------------------------------------------------------------------------
def _assert_rejected(
    response: Any, *, status: int, error_code: str, total_before: int, client: TestClient
) -> Any:
    """统一的拒绝断言：状态码/错误码/失败信封，且不新增存档（``total_before`` 为请求前总数）。"""
    assert response.status_code == status, response.text
    body = response.json()
    assert body["ok"] is False
    assert body["error_code"] == error_code
    assert isinstance(body.get("violations"), list) and body["violations"]
    assert _games_total(client) == total_before
    return body


def test_import_rejects_checksum_tamper_without_write(
    save_env: tuple[TestClient, Path],
) -> None:
    """事件被改但未重算 checksum：422 SCHEMA_VIOLATION（loc=checksum），零写入。"""
    client, _db_path = save_env
    _source_id, package = _played_save(client)
    tampered = json.loads(json.dumps(package))
    tampered["events"][1]["params"]["tampered"] = True

    total_before = _games_total(client)
    body = _assert_rejected(
        _import(client, tampered),
        status=422,
        error_code="SCHEMA_VIOLATION",
        total_before=total_before,
        client=client,
    )

    assert any(item["loc"] == ["checksum"] for item in body["violations"])


def test_import_rejects_state_hash_tamper_without_write(
    save_env: tuple[TestClient, Path],
) -> None:
    """重签 checksum 但改 snapshot.state_hash：回放一致性校验拒绝（零写入）。"""
    client, _db_path = save_env
    _source_id, package = _played_save(client)
    tampered = json.loads(json.dumps(package))
    declared_hash = tampered["snapshot"]["state_hash"]
    first = declared_hash[0]
    tampered["snapshot"]["state_hash"] = ("0" if first != "0" else "1") + declared_hash[1:]

    total_before = _games_total(client)
    body = _assert_rejected(
        _import(client, _resign(tampered)),
        status=422,
        error_code="SCHEMA_VIOLATION",
        total_before=total_before,
        client=client,
    )

    assert any(item["loc"] == ["snapshot", "state_hash"] for item in body["violations"])


def test_import_rejects_event_replay_tamper_without_write(
    save_env: tuple[TestClient, Path],
) -> None:
    """重签 checksum 但改事件 rng_seed：EventLog 回放校验拒绝（零写入）。"""
    client, _db_path = save_env
    _source_id, package = _played_save(client)
    tampered = json.loads(json.dumps(package))
    tampered["events"][1]["rng_seed"] = int(tampered["events"][1]["rng_seed"]) + 1

    total_before = _games_total(client)
    body = _assert_rejected(
        _import(client, _resign(tampered)),
        status=422,
        error_code="SCHEMA_VIOLATION",
        total_before=total_before,
        client=client,
    )

    assert any(item["loc"] == ["events"] for item in body["violations"])


def test_import_rejects_unknown_field_and_bad_event_shape(
    save_env: tuple[TestClient, Path],
) -> None:
    """字段白名单：多出未知字段 → 422；事件缺必填字段 → 422（均零写入）。"""
    client, _db_path = save_env
    _source_id, package = _played_save(client)

    extra = json.loads(json.dumps(package))
    extra["unexpected"] = "x"
    total_before = _games_total(client)
    _assert_rejected(
        _import(client, _resign(extra)),
        status=422,
        error_code="SCHEMA_VIOLATION",
        total_before=total_before,
        client=client,
    )

    malformed = json.loads(json.dumps(package))
    del malformed["events"][1]["rng_seed"]
    total_before = _games_total(client)
    _assert_rejected(
        _import(client, _resign(malformed)),
        status=422,
        error_code="SCHEMA_VIOLATION",
        total_before=total_before,
        client=client,
    )


def test_import_rejects_unsupported_version(save_env: tuple[TestClient, Path]) -> None:
    """未来版本包（``export_version="2"``）→ 422 INVALID_INPUT（不猜格式，零写入）。"""
    client, _db_path = save_env
    _source_id, package = _played_save(client)
    future = json.loads(json.dumps(package))
    future["export_version"] = "2"

    total_before = _games_total(client)
    body = _assert_rejected(
        _import(client, _resign(future)),
        status=422,
        error_code="INVALID_INPUT",
        total_before=total_before,
        client=client,
    )

    assert any(item["loc"] == ["export_version"] for item in body["violations"])


def test_import_rejects_too_many_events(save_env: tuple[TestClient, Path]) -> None:
    """事件条数超上限：422 INVALID_INPUT（结构解析前按数量拒绝，零写入）。"""
    client, _db_path = save_env
    _source_id, package = _played_save(client)
    huge = json.loads(json.dumps(package))
    huge["events"] = [{} for _ in range(MAX_SAVE_EVENTS + 1)]

    total_before = _games_total(client)
    body = _assert_rejected(
        _import(client, _resign(huge)),
        status=422,
        error_code="INVALID_INPUT",
        total_before=total_before,
        client=client,
    )

    assert any(item["loc"] == ["events"] for item in body["violations"])


def test_import_rejects_oversized_request_body(save_env: tuple[TestClient, Path]) -> None:
    """请求体超过导入端点 8MB+报文头上限：413 INVALID_INPUT（R-18 中间件，零写入）。"""
    client, _db_path = save_env
    oversized = (
        b'{"export_version":"1","padding":"' + b"x" * (MAX_SAVE_PACKAGE_BYTES + 128 * 1024) + b'"}'
    )

    response = client.post(
        "/api/v1/saves/import",
        headers={"Idempotency-Key": _key("saves-oversize"), "Content-Type": "application/json"},
        content=oversized,
    )

    assert response.status_code == 413, response.text
    body = response.json()
    assert body["ok"] is False and body["error_code"] == "INVALID_INPUT"
    assert _games_total(client) == 0


def test_import_requires_idempotency_key_and_valid_json(
    save_env: tuple[TestClient, Path],
) -> None:
    """缺 Idempotency-Key / 非法 JSON → 422 INVALID_INPUT（第 6 章入口校验）。"""
    client, _db_path = save_env
    _source_id, package = _played_save(client)
    total_before = _games_total(client)

    missing_key = client.post("/api/v1/saves/import", json=package)
    assert missing_key.status_code == 422
    assert missing_key.json()["error_code"] == "INVALID_INPUT"

    bad_json = client.post(
        "/api/v1/saves/import",
        headers={"Idempotency-Key": _key("saves-badjson"), "Content-Type": "application/json"},
        content=b"{not-json",
    )
    assert bad_json.status_code == 422
    assert bad_json.json()["error_code"] == "INVALID_INPUT"
    assert _games_total(client) == total_before


# ---------------------------------------------------------------------------
# 4. 幂等：同键同包回放、同键异包 409
# ---------------------------------------------------------------------------
def test_import_idempotent_replay_creates_single_game(
    save_env: tuple[TestClient, Path],
) -> None:
    """同键同包重复提交：两次都 201 且响应一致，只新增一局。"""
    client, _db_path = save_env
    _source_id, package = _played_save(client)
    key = _key("saves-idem")
    total_before = _games_total(client)

    first = _import(client, package, key=key)
    second = _import(client, package, key=key)

    assert first.status_code == 201 and second.status_code == 201
    assert first.json() == second.json()
    assert _games_total(client) == total_before + 1


def test_import_same_key_different_package_conflicts(
    save_env: tuple[TestClient, Path],
) -> None:
    """同键异包 → 409 CONFLICT（不新增第二局）。"""
    client, _db_path = save_env
    _id_a, package_a = _played_save(client)
    first_game = _new_game(client, seed=7)
    _play(client, str(first_game["game_id"]), int(first_game["version"]), ["拿起火把"])
    package_b = _export(client, str(first_game["game_id"]))
    key = _key("saves-conflict")

    assert _import(client, package_a, key=key).status_code == 201
    total_after_first = _games_total(client)

    conflict = _import(client, package_b, key=key)

    assert conflict.status_code == 409
    body = conflict.json()
    assert body["ok"] is False and body["error_code"] == "CONFLICT"
    assert _games_total(client) == total_after_first


# ---------------------------------------------------------------------------
# 5. QA P2-6：导出/导入的 OpenAPI 响应模型声明（响应 JSON 逐字不变）
# ---------------------------------------------------------------------------
def test_export_and_import_openapi_declare_response_models(
    save_env: tuple[TestClient, Path],
) -> None:
    """OpenAPI 为导出 200 / 导入 201 暴露具体响应模型（P2-6）。"""
    client, _db_path = save_env
    openapi = client.app.openapi()  # type: ignore[attr-defined]

    export_schema = openapi["paths"]["/api/v1/games/{game_id}/export"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]
    assert export_schema.get("$ref") == "#/components/schemas/SavePackage"
    import_schema = openapi["paths"]["/api/v1/saves/import"]["post"]["responses"]["201"]["content"][
        "application/json"
    ]["schema"]
    assert import_schema.get("$ref") == "#/components/schemas/SaveImportResponse"
    schemas = openapi["components"]["schemas"]
    assert "SavePackage" in schemas and "SaveImportResponse" in schemas


def test_export_response_json_unchanged_after_response_model(
    save_env: tuple[TestClient, Path],
) -> None:
    """声明 ``response_model=SavePackage`` 后响应 JSON 逐字不变（键顺序/取值/checksum）。"""
    client, _db_path = save_env
    game_id, _package = _played_save(client)

    response = client.get(f"/api/v1/games/{game_id}/export")

    assert response.status_code == 200
    raw = response.text
    keys = ["export_version", "exported_at", "game", "events", "snapshot", "checksum"]
    positions = [raw.index(f'"{key}":') for key in keys]
    assert positions == sorted(positions), "顶层键顺序应保持导出包原顺序"
    package = response.json()
    assert list(package) == keys
    assert package["checksum"] == package_checksum(package)
    # 响应模型与导出包白名单逐字段一致（无多余/缺失字段）。
    assert SavePackage.model_validate(package).model_dump(mode="json") == package
