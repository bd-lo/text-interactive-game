"""用户自上传场景服务单测（契约：docs/design/user-scenario-modules.md §3.3/§3.4）。

覆盖：validate 只读校验（结构化 errors/warnings/summary、重复键、体积上限）、import 校验/
幂等（同键同参回放、同键异参 409、不同键新 ID）、列表分页与 q 搜索、详情原始定义、
软删（列表/详情/建局不可见、重复删除 404、runtime 索引保留）、build_game_payload 建局载荷、
reload 重建 runtime 索引、以及「场景库不写 events」。全部内存 SQLite，不联网。
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, text

from textgame.domain.models import GameState
from textgame.domain.scenario import PLAYER_ID
from textgame.domain.scenario_runtime import ScenarioRuntime
from textgame.domain.user_scenario import (
    MAX_DEFINITION_BYTES,
    ScenarioIds,
    derive_ids,
    parse_definition,
)
from textgame.persistence.database import init_schema, prepare_db
from textgame.persistence.scenario_repository import SqliteScenarioRepository
from textgame.services.scenario_service import (
    ScenarioConflictError,
    ScenarioNotFoundError,
    ScenarioService,
    ScenarioValidationError,
    StoredScenario,
)

_SCENARIO_ID_RE = re.compile(r"^scn_[0-9A-HJKMNP-TV-Z]{26}$")
_SAVE_SEED = "save-user-scenario-0001"


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
    """定义 → UTF-8 JSON 原文（同前端上传口径）。"""
    return json.dumps(_tiny() if definition is None else definition, ensure_ascii=False).encode(
        "utf-8"
    )


def _import(
    service: ScenarioService,
    *,
    key: str = "scn-import-key-0001",
    definition: dict[str, Any] | None = None,
    raw: bytes | None = None,
) -> dict[str, Any]:
    """调用一次导入（缺省导入最小示例）。"""
    content = raw if raw is not None else _raw(definition)
    return service.import_scenario(content, idempotency_key=key)


def _initial_state(service: ScenarioService, scenario_id: str) -> GameState:
    """由服务建局载荷还原 ``GameState``（断言 runtime 解析/建局内容用）。"""
    payload = service.build_game_payload(scenario_id, _SAVE_SEED)
    return GameState.model_validate(payload["initial_state"])


@pytest.fixture()
def engine() -> Iterator[Engine]:
    """每个用例独立内存 SQLite（含 0005 后的全部 ORM 表）。"""
    engine, _factory = prepare_db("sqlite:///:memory:")
    init_schema(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture()
def service(engine: Engine) -> ScenarioService:
    """真实 ``ScenarioService`` + SQLite 仓储 + 独立 runtime。"""
    return ScenarioService(SqliteScenarioRepository(engine))


# ---------------------------------------------------------------------------
# validate：只读校验
# ---------------------------------------------------------------------------
def test_validate_valid_definition_returns_summary(service: ScenarioService) -> None:
    """合法定义：ok、无错误，summary 6 键计数正确；只读（不写库）。"""
    parsed = service.validate(_raw())
    assert parsed.ok is True
    assert parsed.errors == ()
    assert parsed.warnings == ()
    assert parsed.summary == {
        "rooms": 2,
        "items": 1,
        "npcs": 0,
        "objectives": 1,
        "endings": 1,
        "suggestions": 0,
    }
    assert service.list_scenarios()["total"] == 0


def test_validate_reference_error_has_json_path(service: ScenarioService) -> None:
    """未知房间引用 → errors[].path 精确定位（rooms[0].exits.east）。"""
    definition = _tiny()
    definition["rooms"][0]["exits"]["east"] = "hub2"  # type: ignore[index]
    parsed = service.validate(_raw(definition))
    assert parsed.ok is False
    assert any(
        issue.path == "rooms[0].exits.east" and "hub2" in issue.message for issue in parsed.errors
    )


def test_validate_schema_error_has_path_and_summary(service: ScenarioService) -> None:
    """未知字段（extra=forbid）→ path 带数组下标；语义错误时仍返回部分 summary。"""
    definition = _tiny()
    definition["rooms"][0]["unexpected"] = 1  # type: ignore[index]
    parsed = service.validate(_raw(definition))
    assert parsed.ok is False
    assert any(issue.path == "rooms[0].unexpected" for issue in parsed.errors)

    bad_ref = _tiny()
    bad_ref["rooms"][0]["exits"]["east"] = "hub2"  # type: ignore[index]
    parsed_ref = service.validate(_raw(bad_ref))
    assert parsed_ref.ok is False
    assert parsed_ref.summary["rooms"] == 2  # 引用校验阶段已能统计


def test_validate_malformed_and_oversize(service: ScenarioService) -> None:
    """非法 JSON/超大文件 → 结构化 error（path ``$``），validate 不抛异常。"""
    assert service.validate(b"{not json").ok is False
    assert service.validate(b'{"schema_version": 1}').errors  # 缺字段

    too_big = b'{"x": "' + b"a" * (MAX_DEFINITION_BYTES + 10) + b'"}'
    parsed = service.validate(too_big)
    assert parsed.ok is False
    assert parsed.errors[0].path == "$"
    assert "文件过大" in parsed.errors[0].message


def test_validate_rejects_duplicate_json_keys(service: ScenarioService) -> None:
    """JSON 重复键（后者覆盖前者）→ 校验失败（与人物卡/世界书导入口径一致）。"""
    duplicate = _raw()[:-1] + b', "slug": "tiny-tower"}'
    parsed = service.validate(duplicate)
    assert parsed.ok is False
    assert parsed.errors[0].path == "$"
    assert "重复键" in parsed.errors[0].message
    with pytest.raises(ScenarioValidationError) as excinfo:
        _import(service, raw=duplicate)
    assert excinfo.value.error_code.value == "INVALID_INPUT"
    assert "重复键" in excinfo.value.errors[0]["message"]


# ---------------------------------------------------------------------------
# import：入库 + 幂等
# ---------------------------------------------------------------------------
def test_import_returns_contract_payload_and_persists(
    engine: Engine, service: ScenarioService
) -> None:
    """导入成功：返回 5 字段契约、scn_ ULID、详情含原始定义、不写 events。"""
    result = _import(service)
    assert set(result) == {"scenario_id", "slug", "title", "summary", "warnings"}
    assert _SCENARIO_ID_RE.match(str(result["scenario_id"]))
    assert result["slug"] == "tiny-tower"
    assert result["title"] == "小塔"
    assert result["summary"]["rooms"] == 2
    assert result["warnings"] == []

    detail = service.get_scenario(str(result["scenario_id"]))
    assert detail["definition"] == _tiny()  # 原始 JSON 对象原样保留
    assert detail["schema_version"] == 1
    assert detail["summary"] == result["summary"]

    listed = service.list_scenarios()
    assert listed["total"] == 1
    item = listed["scenarios"][0]
    assert set(item) == {
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
    assert item["author"] == "示例"

    with engine.connect() as connection:
        events = connection.execute(text("SELECT COUNT(*) FROM events")).scalar()
    assert events == 0  # 场景库不参与事件溯源


def test_import_is_idempotent_same_key_same_content(service: ScenarioService) -> None:
    """同键同参：回放首次响应、零写入（total 仍为 1、scenario_id 相同）。"""
    first = _import(service, key="scn-idem-key-0001")
    replay = _import(service, key="scn-idem-key-0001")
    assert replay == first
    assert service.list_scenarios()["total"] == 1


def test_import_conflict_on_same_key_different_content(service: ScenarioService) -> None:
    """同键异参 → 409 CONFLICT（``ScenarioConflictError``）。"""
    _import(service, key="scn-idem-key-0002")
    with pytest.raises(ScenarioConflictError) as excinfo:
        _import(
            service,
            key="scn-idem-key-0002",
            definition=_variant(title="另一场景", author="甲", slug="another-scene"),
        )
    assert excinfo.value.error_code.value == "CONFLICT"
    assert service.list_scenarios()["total"] == 1


def test_import_same_content_different_keys_creates_new_ids(service: ScenarioService) -> None:
    """同 slug 重复导入：每次导入生成新的内部场景 id（不覆盖已开局存档）。"""
    first = _import(service, key="scn-idem-key-0003")
    second = _import(service, key="scn-idem-key-0004")
    assert first["scenario_id"] != second["scenario_id"]
    assert service.list_scenarios()["total"] == 2


def test_import_invalid_definition_raises_structured_error(service: ScenarioService) -> None:
    """非法定义 → ``ScenarioValidationError``（422 语义，errors 带 path/message）。"""
    definition = _tiny()
    definition["rooms"][0]["exits"]["east"] = "hub2"  # type: ignore[index]
    with pytest.raises(ScenarioValidationError) as excinfo:
        _import(service, key="scn-invalid-key-0001", definition=definition)
    error = excinfo.value
    assert error.error_code.value == "INVALID_INPUT"
    assert error.http_status is None
    assert {"path", "message"} <= set(error.errors[0])
    assert any(item["path"] == "rooms[0].exits.east" for item in error.errors)
    assert service.list_scenarios()["total"] == 0


def test_import_oversize_raises_413(service: ScenarioService) -> None:
    """导入超过 256KB → ``ScenarioValidationError`` + HTTP 413 特例。"""
    too_big = b'{"x": "' + b"a" * (MAX_DEFINITION_BYTES + 10) + b'"}'
    with pytest.raises(ScenarioValidationError) as excinfo:
        _import(service, key="scn-invalid-key-0002", raw=too_big)
    assert excinfo.value.http_status == 413


def test_import_warnings_are_persisted(engine: Engine, service: ScenarioService) -> None:
    """非阻断 warnings 落库并在详情/列表返回（物品永远拿不到）。"""
    definition = _variant(title="带警告", author="乙", slug="warn-scene")
    definition["items"].append({"key": "lost", "name": "遗失之物"})  # type: ignore[union-attr]
    result = _import(service, key="scn-warn-key-0001", definition=definition)
    assert result["warnings"]
    assert result["warnings"][0]["path"] == "items[1]"
    detail = service.get_scenario(str(result["scenario_id"]))
    assert detail["warnings"] == result["warnings"]


# ---------------------------------------------------------------------------
# 列表：分页 / 搜索
# ---------------------------------------------------------------------------
def test_list_pagination_and_search(service: ScenarioService) -> None:
    """limit/offset 分页与 q 搜索 title/author/slug；非法分页参数 → 结构化错误。"""
    _import(service, key="scn-list-key-0001")
    _import(
        service,
        key="scn-list-key-0002",
        definition=_variant(title="雪原", author="甲", slug="snow-field"),
    )
    _import(
        service,
        key="scn-list-key-0003",
        definition=_variant(title="地窖", author="乙", slug="cellar-scene"),
    )

    first_page = service.list_scenarios(limit=2, offset=0)
    assert first_page["total"] == 3
    assert len(first_page["scenarios"]) == 2
    second_page = service.list_scenarios(limit=2, offset=2)
    assert len(second_page["scenarios"]) == 1
    assert service.list_scenarios(offset=10)["scenarios"] == []

    assert service.list_scenarios(query="雪")["total"] == 1
    assert service.list_scenarios(query="甲")["total"] == 1
    assert service.list_scenarios(query="tiny")["total"] == 1  # slug 命中
    assert service.list_scenarios(query="无此词")["total"] == 0

    with pytest.raises(ScenarioValidationError):
        service.list_scenarios(limit=0)
    with pytest.raises(ScenarioValidationError):
        service.list_scenarios(limit=101)
    with pytest.raises(ScenarioValidationError):
        service.list_scenarios(offset=-1)
    with pytest.raises(ScenarioValidationError):
        service.list_scenarios(query="x" * 101)


# ---------------------------------------------------------------------------
# 详情 / 软删
# ---------------------------------------------------------------------------
def test_get_unknown_or_invalid_id_raises_not_found(service: ScenarioService) -> None:
    """未知/格式非法 id → 404 语义。"""
    with pytest.raises(ScenarioNotFoundError):
        service.get_scenario("scn_00000000000000000000000000")
    with pytest.raises(ScenarioNotFoundError):
        service.get_scenario("not-a-scenario")


def test_soft_delete_hides_scenario_but_keeps_row(engine: Engine, service: ScenarioService) -> None:
    """软删：列表/详情/建局不可见，行仍保留（include_deleted / runtime 用）。"""
    result = _import(service, key="scn-del-key-0001")
    scenario_id = str(result["scenario_id"])

    service.delete_scenario(scenario_id)
    assert service.list_scenarios()["total"] == 0
    with pytest.raises(ScenarioNotFoundError):
        service.get_scenario(scenario_id)
    with pytest.raises(ScenarioNotFoundError):
        service.build_game_payload(scenario_id, _SAVE_SEED)
    with pytest.raises(ScenarioNotFoundError):
        service.delete_scenario(scenario_id)  # 重复删除 404

    repo = SqliteScenarioRepository(engine)
    stored = repo.get(scenario_id, include_deleted=True)
    assert stored is not None and stored.deleted_at is not None
    assert repo.get(scenario_id) is None
    assert len(repo.list_for_runtime()) == 1


# ---------------------------------------------------------------------------
# runtime 索引 / 建局载荷
# ---------------------------------------------------------------------------
def test_import_registers_runtime_immediately(service: ScenarioService) -> None:
    """导入后立即注册 runtime（建局后钩子可用）；识别状态属于该场景。"""
    result = _import(service, key="scn-runtime-key-0001")
    scenario_id = str(result["scenario_id"])
    assert len(service.runtime) == 1
    state = _initial_state(service, scenario_id)
    hooks = service.runtime.resolve(state)
    assert hooks is not None
    assert hooks.scenario_id == scenario_id
    assert service.runtime.scenario_id(state) == scenario_id


def test_accepts_shared_runtime_instance(engine: Engine) -> None:
    """构造时可注入共享 ``ScenarioRuntime``（组合根与查询/编排层共用同一索引）。"""
    runtime = ScenarioRuntime()
    service = ScenarioService(SqliteScenarioRepository(engine), runtime=runtime)
    assert service.runtime is runtime


def test_build_game_payload_returns_initial_state_and_ids(service: ScenarioService) -> None:
    """建局载荷：GameState 可校验、玩家槽位 PLAYER_ID、派生 ID/种子/标题正确。"""
    result = _import(service, key="scn-build-key-0001")
    scenario_id = str(result["scenario_id"])
    payload = service.build_game_payload(scenario_id, _SAVE_SEED)
    assert set(payload) == {
        "scenario_id",
        "scenario_title",
        "initial_state",
        "ids",
        "starting_room_id",
        "initial_character_id",
    }
    assert payload["scenario_id"] == scenario_id
    assert payload["scenario_title"] == "小塔"
    assert payload["initial_character_id"] == PLAYER_ID
    ids = payload["ids"]
    assert isinstance(ids, ScenarioIds)

    state = GameState.model_validate(payload["initial_state"])
    assert state.turn == 1 and state.world_time == 0
    assert state.rng_state.save_seed == _SAVE_SEED
    player = state.characters[PLAYER_ID]
    assert player.location == ids.rooms["gate"]
    assert payload["starting_room_id"] == ids.rooms["gate"]
    assert ids == derive_ids(scenario_id, parse_definition(_raw()).definition)  # type: ignore[arg-type]
    assert all(room_id in state.rooms for room_id in ids.rooms.values())
    assert all(item_id in state.items for item_id in ids.items.values())


def test_build_game_payload_missing_seed_raises_validation_error(service: ScenarioService) -> None:
    """空 save_seed → 结构化 422（避免把非法种子带进 RngState）。"""
    result = _import(service, key="scn-build-key-0002")
    with pytest.raises(ScenarioValidationError):
        service.build_game_payload(str(result["scenario_id"]), "")


def test_reload_registers_all_scenarios_including_deleted(engine: Engine) -> None:
    """``reload``：从库重建 runtime，含已软删场景（旧存档仍可按 room/npc 解析）。"""
    service = ScenarioService(SqliteScenarioRepository(engine))
    kept = _import(service, key="scn-reload-key-0001")
    deleted = _import(
        service,
        key="scn-reload-key-0002",
        definition=_variant(title="删掉", author="甲", slug="deleted-scene"),
    )
    deleted_state = _initial_state(service, str(deleted["scenario_id"]))
    service.delete_scenario(str(deleted["scenario_id"]))

    fresh_runtime = ScenarioRuntime()
    fresh = ScenarioService(SqliteScenarioRepository(engine), runtime=fresh_runtime)
    assert fresh.reload() == 2  # 含已软删
    hooks = fresh_runtime.resolve(deleted_state)
    assert hooks is not None
    assert hooks.scenario_id == str(deleted["scenario_id"])
    assert kept["scenario_id"] != deleted["scenario_id"]


# ---------------------------------------------------------------------------
# 仓储层：搜索 / 软删 / 行映射
# ---------------------------------------------------------------------------
def test_repository_search_and_soft_delete(engine: Engine) -> None:
    """仓储：q 搜索 title/author/slug、软删过滤、``list_for_runtime`` 含已删。"""
    repo = SqliteScenarioRepository(engine)
    service = ScenarioService(repo)
    first = _import(service, key="scn-repo-key-0001")
    second = _import(
        service,
        key="scn-repo-key-0002",
        definition=_variant(title="雪原", author="甲", slug="snow-field"),
    )

    rows, total = repo.list_scenarios(query="雪", limit=10, offset=0)
    assert total == 1 and rows[0].scenario_id == str(second["scenario_id"])
    rows, total = repo.list_scenarios(query="示例", limit=10, offset=0)
    assert total == 1 and rows[0].scenario_id == str(first["scenario_id"])
    rows, total = repo.list_scenarios(query="snow", limit=10, offset=0)
    assert total == 1 and rows[0].slug == "snow-field"

    first_stored = repo.get(str(first["scenario_id"]))
    assert first_stored is not None
    assert repo.soft_delete(str(first["scenario_id"]), deleted_at=first_stored.created_at) is True
    assert repo.soft_delete(str(first["scenario_id"]), deleted_at=first_stored.created_at) is False
    assert repo.list_scenarios(query=None, limit=10, offset=0)[1] == 1
    assert {item.scenario_id for item in repo.list_for_runtime()} == {
        str(first["scenario_id"]),
        str(second["scenario_id"]),
    }


# ---------------------------------------------------------------------------
# R-04：同 Idempotency-Key 并发导入只产生一个场景
# ---------------------------------------------------------------------------
class _BlockingScenarioRepository(SqliteScenarioRepository):
    """首次 ``insert`` 阻塞（放大竞态窗口），记录真实插入次数。"""

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine)
        self.entered = threading.Event()
        self.release = threading.Event()
        self.inserts = 0
        self._guard = threading.Lock()

    def insert(self, stored: Any) -> None:
        with self._guard:
            self.inserts += 1
            first = self.inserts == 1
        if first:
            self.entered.set()
            if not self.release.wait(timeout=5.0):
                raise AssertionError("场景导入未被放行（并发窗口未打开）")
        super().insert(stored)


def test_concurrent_same_key_import_creates_one_scenario(tmp_path: Path) -> None:
    """R-04：两个线程同键同参导入 → 只插入一次，返回同一 scenario_id。

    用**文件型 SQLite**（每线程独立连接）而不是共享内存连接：并发用例验证的是服务层
    幂等锁语义，不应被 ``sqlite3`` 单连接跨线程使用的 ``InterfaceError`` 干扰
    （内存库在并行负载下会随机失败）。
    """
    engine, _factory = prepare_db(f"sqlite:///{(tmp_path / 'concurrent.db').as_posix()}")
    init_schema(engine)
    try:
        repo = _BlockingScenarioRepository(engine)
        service = ScenarioService(repo)
        raw = _raw()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(service.import_scenario, raw, idempotency_key="scn-conc-key-0001")
            assert repo.entered.wait(timeout=5.0), "首请求未进入插入"
            second = pool.submit(service.import_scenario, raw, idempotency_key="scn-conc-key-0001")
            time.sleep(0.1)
            assert repo.inserts == 1, "同键后到者绕过了锁（重复插入）"
            repo.release.set()
            results = [first.result(timeout=5.0), second.result(timeout=5.0)]

        assert results[0] == results[1]
        assert repo.inserts == 1
        assert service.list_scenarios(query=None, limit=10, offset=0)["total"] == 1
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# 导出（分享 = 单文件交换；只读、零写入、可直接再导入）
# ---------------------------------------------------------------------------
def test_export_scenario_returns_reimportable_payload(service: ScenarioService) -> None:
    """导出：文件名取自 slug、payload 可直接再导入且字段/统计逐字一致。"""
    result = _import(service, key="scn-share-export-0001")
    scenario_id = str(result["scenario_id"])
    exported = service.export_scenario(scenario_id)

    assert exported.scenario_id == scenario_id
    assert exported.filename == "tiny-tower.json"
    assert exported.payload.endswith(b"\n")
    assert json.loads(exported.payload) == _tiny()
    parsed = parse_definition(exported.payload)
    assert parsed.ok and parsed.errors == ()
    assert parsed.summary == result["summary"]
    assert exported.payload.decode("utf-8").startswith("{\n")  # 人类可读的 pretty JSON

    reimported = service.import_scenario(exported.payload, idempotency_key="scn-share-reimport-1")
    assert str(reimported["scenario_id"]) != scenario_id
    detail = service.get_scenario(str(reimported["scenario_id"]))
    assert detail["definition"] == _tiny()
    assert detail["summary"] == result["summary"]
    assert detail["schema_version"] == 1


def test_export_scenario_preserves_v2_flags(service: ScenarioService) -> None:
    """v2 定义导出保留 schema_version=2 与 flags；再导入逐字段一致。"""
    definition = _tiny()
    definition["schema_version"] = 2
    definition["flags"] = [{"key": "at_hall", "when": {"in_room": "hall"}}]
    definition["suggestions"] = [{"when": {"flag": "at_hall"}, "text": "去塔厅"}]
    result = _import(service, definition=definition, key="scn-share-v2-0001")

    exported = service.export_scenario(str(result["scenario_id"]))
    body = json.loads(exported.payload)
    assert body == definition
    assert body["schema_version"] == 2 and body["flags"] == definition["flags"]
    assert parse_definition(exported.payload).ok

    reimported = service.import_scenario(exported.payload, idempotency_key="scn-share-v2-re-1")
    assert str(reimported["scenario_id"]) != str(result["scenario_id"])
    assert service.get_scenario(str(reimported["scenario_id"]))["definition"] == definition


def test_export_scenario_unknown_and_soft_deleted_not_found(service: ScenarioService) -> None:
    """未知/格式非法/已软删场景 → ``ScenarioNotFoundError``（与详情同 404 口径）。"""
    with pytest.raises(ScenarioNotFoundError):
        service.export_scenario("scn_00000000000000000000000000")
    with pytest.raises(ScenarioNotFoundError):
        service.export_scenario("not-a-scenario")

    result = _import(service, key="scn-share-delete-0001")
    scenario_id = str(result["scenario_id"])
    service.delete_scenario(scenario_id)
    with pytest.raises(ScenarioNotFoundError):
        service.export_scenario(scenario_id)


def test_export_scenario_is_zero_write_and_deterministic(
    service: ScenarioService, engine: Engine
) -> None:
    """导出只读：两次结果一致、列表/详情零变化、不写 events 行。"""
    _import(service, key="scn-share-det-0001")
    second = _import(
        service,
        key="scn-share-det-0002",
        definition=_variant(title="雪原", author="甲", slug="snow-field"),
    )
    scenario_id = str(second["scenario_id"])
    list_before = service.list_scenarios()
    detail_before = service.get_scenario(scenario_id)

    first_export = service.export_scenario(scenario_id)
    second_export = service.export_scenario(scenario_id)
    assert first_export == second_export  # frozen dataclass：payload/filename 确定性一致
    assert service.list_scenarios() == list_before
    assert service.get_scenario(scenario_id) == detail_before
    with engine.connect() as connection:
        events = connection.execute(text("SELECT COUNT(*) FROM events")).scalar()
    assert events == 0


def test_export_scenario_corrupted_definition_not_found(engine: Engine) -> None:
    """库内定义损坏（绕过导入校验的脏数据）→ 404，而不是导出不可再导入的坏文件。"""
    repo = SqliteScenarioRepository(engine)
    corrupted_id = "scn_00000000000000000000000001"
    repo.insert(
        StoredScenario(
            scenario_id=corrupted_id,
            slug="corrupted",
            title="坏场景",
            author="",
            description="",
            schema_version=2,
            definition={},
            summary={},
            warnings=(),
            created_at=datetime.now(timezone.utc),
        )
    )
    service = ScenarioService(repo)
    with pytest.raises(ScenarioNotFoundError):
        service.export_scenario(corrupted_id)
