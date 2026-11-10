"""H4 世界书后端存储/服务集成测试（repo + service，内存 SQLite，不占端口、不联网）。

覆盖：三种导入格式（SillyTavern entries map/array、Character Card V2 character_book、简化
schema）、规范化字段落库（keys/secondary/selective_logic/order/constant/禁用条目）、
列表分页/搜索、详情、Provider（``get``）语义、软删幂等、写幂等（同键同参回放/同键异参 409）、
安全（256KB 上限/UTF-8/重复键/深度/非法格式）、以及 ``read_first_event``/引擎世界书参数。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from textgame.domain.events import EventType
from textgame.persistence.database import init_schema, prepare_db
from textgame.persistence.memory import InMemoryRepository
from textgame.persistence.sqlite_repository import SqliteRepository
from textgame.persistence.world_book_repository import SqliteWorldBookRepository
from textgame.services.game_engine import GameEngine
from textgame.services.world_book_service import (
    MAX_UPLOAD_BYTES,
    WorldBookService,
    WorldBookServiceError,
)

_TAVERN_MAP = {
    "name": "余烬哨站设定",
    "description": "北境哨站的旧事。",
    "entries": {
        "0": {
            "uid": 0,
            "key": "哨站,大桥",
            "content": "哨站建在封冻河谷的断桥尽头。",
            "constant": True,
            "order": 10,
        },
        "1": {
            "uid": 1,
            "key": "信号灯",
            "keysecondary": "老兵,守夜人",
            "content": "信号灯由守夜人点燃。",
            "selective": True,
            "selectiveLogic": 0,
            "order": 20,
        },
        "2": {
            "uid": 2,
            "key": "旧闻",
            "content": "一条被禁用的旧闻。",
            "disable": True,
            "order": 30,
        },
    },
}
_TAVERN_ARRAY = {
    "entries": [
        {"uid": 0, "key": ["火把", "火石"], "content": "火把与火石。", "order": 5},
        {"uid": 1, "key": "铁匠铺", "content": "铁匠铺的炉膛积着冷灰。", "order": 6},
    ]
}
_CHARACTER_BOOK = {
    "schema_version": "1.0",
    "name": "灰烬旅人",
    "data": {
        "character_book": {
            "name": "旅人世界",
            "entries": [{"key": "灰烬", "content": "灰烬之路从旧矿镇开始。", "order": 1}],
        }
    },
}
_SIMPLE = {
    "name": "简化世界书",
    "entries": [
        {"keys": ["风", "雪"], "content": "风雪封路。", "order": 3, "enabled": True},
    ],
}


@pytest.fixture()
def service() -> WorldBookService:
    """内存 SQLite + 真仓储 + 真服务（每个用例独立库）。"""
    engine, _ = prepare_db("sqlite:///:memory:")
    init_schema(engine)
    return WorldBookService(SqliteWorldBookRepository(engine))


def _import(
    service: WorldBookService,
    payload: dict[str, object],
    *,
    key: str = "wb-import-key-0001",
    filename: str = "lore.json",
    name: str | None = None,
) -> dict[str, object]:
    """把 payload 序列化为 UTF-8 JSON 后走导入（默认幂等键固定）。"""
    content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return service.import_book(
        filename=filename,
        content_type="application/json",
        content=content,
        name=name,
        idempotency_key=key,
    )


# ---------------------------------------------------------------------------
# 导入格式与规范化
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("payload", "expected_source"),
    [
        (_TAVERN_MAP, "tavern"),
        (_TAVERN_ARRAY, "tavern"),
        (_CHARACTER_BOOK, "character_card"),
        (_SIMPLE, "manual"),
    ],
)
def test_import_accepts_four_format_shapes(
    service: WorldBookService, payload: dict[str, object], expected_source: str
) -> None:
    """四种导入形态均被解析：tavern map/array、角色卡 character_book、简化 schema。"""
    result = _import(service, payload, key=f"wb-import-{expected_source}")
    assert result["source"] == expected_source
    assert int(result["entry_count"]) >= 1


def test_tavern_entry_fields_are_normalized(service: WorldBookService) -> None:
    """SillyTavern 字段规范化：``key`` 逗号串→数组、``keysecondary``→secondary_keys、
    ``disable=True``→enabled False、``selectiveLogic`` 数字保留。"""
    imported = _import(service, _TAVERN_MAP)
    detail = service.get_detail(str(imported["world_book_id"]))
    entries = {int(entry["entry_uid"]): entry for entry in detail["entries"]}  # type: ignore[index]

    assert entries[0]["keys"] == ["哨站", "大桥"]
    assert entries[1]["secondary_keys"] == ["老兵", "守夜人"]
    assert entries[1]["selective_logic"] == 0
    assert entries[2]["enabled"] is False
    assert detail["world_book"]["source"] == "tavern"  # type: ignore[index]


def test_entries_are_sorted_by_uid_and_raw_kept_in_repo() -> None:
    """条目按 ``entry_uid`` 升序；仓储保留原始 entry（含未映射字段，供审计/前向兼容）。"""
    engine, _ = prepare_db("sqlite:///:memory:")
    init_schema(engine)
    repo = SqliteWorldBookRepository(engine)
    service = WorldBookService(repo)
    payload = {
        "entries": {
            "5": {"uid": 5, "key": "乙", "content": "乙。", "comment": "原始注释"},
            "3": {"uid": 3, "key": "甲", "content": "甲。"},
        }
    }
    imported = _import(service, payload)
    entries = repo.get_entries(str(imported["world_book_id"]))

    assert [entry.entry_uid for entry in entries] == [3, 5]
    assert entries[1].raw is not None and entries[1].raw.get("comment") == "原始注释"


def test_character_card_import_emits_warning(service: WorldBookService) -> None:
    """角色卡内嵌 character_book 导入成功并带 ``CHARACTER_BOOK_EXTRACTED`` 告警。"""
    result = _import(service, _CHARACTER_BOOK, key="wb-import-card")
    warnings = result["warnings"]
    assert isinstance(warnings, list)
    assert any(item["code"] == "CHARACTER_BOOK_EXTRACTED" for item in warnings)  # type: ignore[index]


def test_import_name_override_form_field(service: WorldBookService) -> None:
    """multipart 表单 ``name`` 覆盖书名，且截断到 64 字符列宽。"""
    result = _import(service, _SIMPLE, key="wb-import-named", name="自定义书名")
    assert result["name"] == "自定义书名"
    long_name = "书" * 100
    long_result = _import(service, _SIMPLE, key="wb-import-long-name", name=long_name)
    assert len(str(long_result["name"])) <= 64


# ---------------------------------------------------------------------------
# 列表 / 详情 / Provider
# ---------------------------------------------------------------------------
def test_list_pagination_and_search(service: WorldBookService) -> None:
    """列表按 updated_at 倒序、``query`` 匹配 name、``total`` 为过滤后总数。"""
    _import(service, _SIMPLE, key="wb-import-list-1")
    second = _import(service, _TAVERN_MAP, key="wb-import-list-2")

    all_books = service.list_world_books(limit=20, offset=0)
    assert all_books["total"] == 2
    assert len(all_books["world_books"]) == 2
    first_page = service.list_world_books(limit=1, offset=0)
    assert len(first_page["world_books"]) == 1

    searched = service.list_world_books(query="余烬", limit=20, offset=0)
    assert searched["total"] == 1
    assert searched["world_books"][0]["world_book_id"] == second["world_book_id"]


@pytest.mark.parametrize("limit", [0, 101])
def test_list_rejects_out_of_range_limit(service: WorldBookService, limit: int) -> None:
    """``limit`` 超出 1..100 → 422 ``INVALID_INPUT``。"""
    with pytest.raises(WorldBookServiceError) as exc_info:
        service.list_world_books(limit=limit)
    assert exc_info.value.error_code.value == "INVALID_INPUT"


def test_detail_unknown_returns_not_found(service: WorldBookService) -> None:
    """未知 id 与非法 id 一律 404（不泄漏内部信息）。"""
    for world_book_id in ("wb_" + "0" * 30, "not-an-id"):
        with pytest.raises(WorldBookServiceError) as exc_info:
            service.get_detail(world_book_id)
        assert exc_info.value.error_code.value == "NOT_FOUND"


def test_provider_get_returns_domain_book_and_none_after_delete(service: WorldBookService) -> None:
    """Provider 语义：``get`` 返回 domain ``WorldBook``；软删后返回 ``None``。"""
    imported = _import(service, _TAVERN_MAP)
    world_book_id = str(imported["world_book_id"])

    book = service.get(world_book_id)
    assert book is not None
    assert book.name == "余烬哨站设定"
    assert [entry.uid for entry in book.entries] == [0, 1, 2]
    assert book.source == "tavern"

    service.delete_book(world_book_id=world_book_id, idempotency_key="wb-delete-key-0001")
    assert service.get(world_book_id) is None
    assert service.get_world_book(world_book_id) is None


# ---------------------------------------------------------------------------
# 写幂等与软删
# ---------------------------------------------------------------------------
def test_import_same_key_same_payload_replays_once(service: WorldBookService) -> None:
    """同一 Idempotency-Key + 同参：回放首次响应，不新增第二本书。"""
    first = _import(service, _SIMPLE, key="wb-import-idem")
    second = _import(service, _SIMPLE, key="wb-import-idem")
    assert first == second
    assert service.list_world_books(limit=20)["total"] == 1


def test_import_same_key_different_payload_conflicts(service: WorldBookService) -> None:
    """同一 Idempotency-Key + 异参 → 409 ``CONFLICT``（零新增）。"""
    _import(service, _SIMPLE, key="wb-import-conflict")
    with pytest.raises(WorldBookServiceError) as exc_info:
        _import(service, _TAVERN_MAP, key="wb-import-conflict")
    assert exc_info.value.error_code.value == "CONFLICT"
    assert service.list_world_books(limit=20)["total"] == 1


def test_delete_is_idempotent_and_unknown_is_404(service: WorldBookService) -> None:
    """软删：首次 deleted=True；重复删（不同键）仍 deleted=True；从未存在 404。"""
    imported = _import(service, _SIMPLE)
    world_book_id = str(imported["world_book_id"])

    assert service.delete_book(world_book_id=world_book_id, idempotency_key="wb-delete-a") == {
        "world_book_id": world_book_id,
        "deleted": True,
    }
    assert service.delete_book(world_book_id=world_book_id, idempotency_key="wb-delete-b") == {
        "world_book_id": world_book_id,
        "deleted": True,
    }
    with pytest.raises(WorldBookServiceError) as exc_info:
        service.delete_book(world_book_id="wb_" + "0" * 30, idempotency_key="wb-delete-c")
    assert exc_info.value.error_code.value == "NOT_FOUND"
    # 删除后列表排除
    assert service.list_world_books(limit=20)["total"] == 0


# ---------------------------------------------------------------------------
# 安全 / 校验
# ---------------------------------------------------------------------------
def test_import_rejects_oversized_payload_with_413(service: WorldBookService) -> None:
    """超过 256KB → ``INVALID_INPUT`` + HTTP 413（不落库）。"""
    oversized = b"{" + b" " * (MAX_UPLOAD_BYTES + 1) + b"}"
    with pytest.raises(WorldBookServiceError) as exc_info:
        service.import_book(
            filename="lore.json",
            content_type="application/json",
            content=oversized,
            name=None,
            idempotency_key="wb-import-big",
        )
    assert exc_info.value.http_status == 413
    assert service.list_world_books(limit=20)["total"] == 0


@pytest.mark.parametrize(
    "content",
    [
        b"\xff\xfe\x00",  # 非 UTF-8
        b"{not json",  # JSON 语法错误
        b'{"entries": {}, "entries": {}}',  # 重复键
        b"[]",  # 根不是对象
        b'{"no": 1}',  # 无法识别格式
        b'{"entries": [{"content": "x", "uid": 1}, {"content": "y", "uid": 1}]}',  # uid 重复
    ],
)
def test_import_rejects_invalid_json_or_format(service: WorldBookService, content: bytes) -> None:
    """非 UTF-8/语法错/重复键/根非对象/格式不支持/uid 重复 → 422 ``INVALID_INPUT``。"""
    with pytest.raises(WorldBookServiceError) as exc_info:
        service.import_book(
            filename="lore.json",
            content_type="application/json",
            content=content,
            name=None,
            idempotency_key="wb-import-bad",
        )
    assert exc_info.value.error_code.value == "INVALID_INPUT"
    assert service.list_world_books(limit=20)["total"] == 0


def test_import_rejects_non_json_filename(service: WorldBookService) -> None:
    """只接受 ``.json`` 文件名（multipart 元数据校验）。"""
    with pytest.raises(WorldBookServiceError) as exc_info:
        service.import_book(
            filename="lore.txt",
            content_type="text/plain",
            content=b"{}",
            name=None,
            idempotency_key="wb-import-txt",
        )
    assert exc_info.value.error_code.value == "INVALID_INPUT"


def test_validate_payload_is_read_only(service: WorldBookService) -> None:
    """校验：合法 payload → valid True + preview；非法 → valid False + errors；零落库。"""
    valid = service.validate_payload(_TAVERN_MAP)
    assert valid["valid"] is True
    assert valid["preview"]["detected_format"] == "tavern"
    assert valid["preview"]["entry_count"] == 3

    invalid = service.validate_payload({"bad": 1})
    assert invalid["valid"] is False
    assert invalid["errors"]
    assert invalid["preview"]["detected_format"] == "unknown"
    assert service.list_world_books(limit=20)["total"] == 0


def test_control_characters_are_rejected(service: WorldBookService) -> None:
    """条目内容含控制字符 → 422 ``INVALID_INPUT``（玩家可见文本卫生）。"""
    payload = {"entries": [{"key": "旧闻", "content": "危险的\x00内容", "order": 1}]}
    content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    with pytest.raises(WorldBookServiceError) as exc_info:
        service.import_book(
            filename="lore.json",
            content_type="application/json",
            content=content,
            name=None,
            idempotency_key="wb-import-ctrl",
        )
    assert exc_info.value.error_code.value == "INVALID_INPUT"


# ---------------------------------------------------------------------------
# read_first_event / GameEngine 世界书参数（H4 加法）
# ---------------------------------------------------------------------------
def test_read_first_event_memory_and_sqlite(tmp_path: Path) -> None:
    """``read_first_event`` 返回 ``seq=1`` 的 ``game_created``；无存档返回 None。"""
    memory_repo = InMemoryRepository()
    memory_engine = GameEngine(memory_repo)
    assert memory_engine.create_game(save_seed="save-h4", game_id="game-mem").ok
    first = memory_repo.read_first_event("game-mem")
    assert first is not None and first.seq == 1
    assert first.event_type is EventType.GAME_CREATED
    assert memory_repo.read_first_event("game-missing") is None

    engine, _ = prepare_db(f"sqlite:///{(tmp_path / 'first.db').as_posix()}")
    init_schema(engine)
    sqlite_repo = SqliteRepository(engine)
    sqlite_engine = GameEngine(sqlite_repo)
    assert sqlite_engine.create_game(save_seed="save-h4", game_id="game-sql").ok
    first_sql = sqlite_repo.read_first_event("game-sql")
    assert first_sql is not None and first_sql.id == "evt_000001"
    assert sqlite_repo.read_first_event("game-missing") is None
    engine.dispose()


def test_game_engine_world_book_params_only_when_provided() -> None:
    """``GameEngine.create_game``：仅传 world_book_id 时向 params 追加两项；旧路径逐字不变。"""
    repo = InMemoryRepository()
    engine = GameEngine(repo)
    assert engine.create_game(save_seed="save-a", game_id="game-plain", card_id=None).ok
    plain = repo.read_first_event("game-plain")
    assert plain is not None
    assert plain.params == {
        "card_id": None,
        "starting_room_id": "room_01J9Z3K4Q7X8Y9Z0ABCDEFGHJK",
        "initial_character_id": "char_01J9Z3K4Q7X8Y9Z0ABCDEFGHJN",
        "save_seed": "save-a",
        "snapshot_version": 1,
    }

    assert engine.create_game(
        save_seed="save-b",
        game_id="game-bound",
        world_book_id="wb_" + "a" * 30,
        world_book_name="余烬哨站设定",
    ).ok
    bound = repo.read_first_event("game-bound")
    assert bound is not None
    assert bound.params["world_book_id"] == "wb_" + "a" * 30
    assert bound.params["world_book_name"] == "余烬哨站设定"
