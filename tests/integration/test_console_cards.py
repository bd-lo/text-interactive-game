"""N3-047 终端卡库浏览验收：``--list-cards`` 过滤/限量/全量 + ``--card-detail`` 详情/错误。

装配口径与 ``test_console_card.py`` 一致：临时 SQLite + 进程内组合根（``console.build_app``，
不占端口、不联网）。覆盖：
1. ``--list-cards``：标题/来源过滤、``--card-limit`` 限量与上限校验、``--card-all`` 全量、
   空卡库/无匹配/后端不可用给玩家可读中文提示（不崩、不显示内部细节、不建局）；
2. ``--card-detail``：安全子集（标题/来源/属性摘要/背包提示/警告）、不存在/非法 ID 中文可读
   错误 + 退出码非 0、与 ``--list-cards`` 互斥；
3. 交互帮助文本补一行卡库浏览用法；旧 ``--list-cards`` 关键输出（卡名/卡 ID/``--card-id``）
   继续成立。
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from textgame import console
from textgame.config.settings import get_settings

pytestmark = [pytest.mark.integration]

#: 合法格式但卡库中不存在的人物卡 ID（``card_<26 位 ULID>``）。
_UNKNOWN_CARD_ID = "card_01J9Z3K4Q7X8Y9Z0ABCDEFGHJZ"

#: 手填卡（HP 未满 → 详情应给「生命值未满」警告）。
_MANUAL_CARD: dict[str, Any] = {
    "schema_version": "1.0",
    "name": "灰烬旅人",
    "attributes": {"str": 10, "dex": 12, "con": 11, "int": 9, "wis": 10, "cha": 8, "max_hp": 18},
    "hp": 7,
    "starting_items": ["item.potion"],
    "background": "从雪线那头走来，旧伤未愈，想在天亮前找条活路。",
    "appearance": None,
    "source": "manual",
    "generated_by": None,
}

#: LLM 草稿保存的卡（HP 满 → 详情「警告：无」）。
_LLM_CARD: dict[str, Any] = {
    "schema_version": "1.0",
    "name": "观星者",
    "attributes": {"str": 8, "dex": 11, "con": 10, "int": 14, "wis": 15, "cha": 9, "max_hp": 18},
    "hp": 18,
    "starting_items": ["item.torch", "item.potion"],
    "background": "在灯塔废墟里读星象的老人，袖口沾着盐霜。",
    "appearance": "灰蓝长袍，肩头落着星尘。",
    "source": "llm",
    "generated_by": "test-draft",
}


def _dispose(app: FastAPI) -> None:
    """释放组合根内 SQLite Engine 的文件句柄（Windows 临时文件收尾）。"""
    console.dispose_app_engine(app)


def _build_app_with_db(monkeypatch: pytest.MonkeyPatch, db_path: Path) -> FastAPI:
    """把 ``TEXTGAME_DB_URL`` 指向临时库后重建生产组合根。"""
    monkeypatch.setenv("TEXTGAME_DB_URL", f"sqlite:///{db_path.as_posix()}")
    get_settings.cache_clear()
    import textgame.main as main_module

    _dispose(main_module.app)
    return main_module._build_app()


def _new_key(prefix: str) -> str:
    """唯一幂等键（每次请求新键，避免回放旧结果）。"""
    return f"{prefix}-{uuid.uuid4().hex}"


def _save_card(client: TestClient, card: dict[str, Any]) -> str:
    """保存一张合法人物卡 → 返回 ``card_id``。"""
    response = client.post(
        "/api/v1/character-cards",
        headers={"Idempotency-Key": _new_key("console-cards-save")},
        json=card,
    )
    assert response.status_code == 201, response.text
    return str(response.json()["card_id"])


def _seed_cards(
    monkeypatch: pytest.MonkeyPatch, db_path: Path, cards: list[dict[str, Any]]
) -> list[str]:
    """在临时库里依次保存人物卡，返回对应 ``card_id`` 列表（读路径不建局）。"""
    app = _build_app_with_db(monkeypatch, db_path)
    try:
        with TestClient(app) as client:
            return [_save_card(client, card) for card in cards]
    finally:
        _dispose(app)


def _run_console(argv: list[str]) -> int:
    """执行 ``console.main(argv)``；恢复 ``console.build_app`` 写入的进程级环境变量。"""
    previous = os.environ.get("TEXTGAME_DB_URL")
    try:
        return console.main(argv)
    finally:
        if previous is None:
            os.environ.pop("TEXTGAME_DB_URL", None)
        else:
            os.environ["TEXTGAME_DB_URL"] = previous
        get_settings.cache_clear()


def _assert_no_games(monkeypatch: pytest.MonkeyPatch, db_path: Path) -> None:
    """只读浏览不得建局：库里 ``games`` 仍为空。"""
    app = _build_app_with_db(monkeypatch, db_path)
    try:
        with TestClient(app) as client:
            assert client.get("/api/v1/games").json()["games"] == []
    finally:
        _dispose(app)


def _unavailable_app() -> FastAPI:
    """后端不可用替身：卡库两个只读端点都返回 503（模拟旧后端/库打不开）。"""
    app = FastAPI()

    @app.get("/api/v1/character-cards")
    def _list_unavailable() -> None:
        raise HTTPException(status_code=503, detail="internal")

    @app.get("/api/v1/character-cards/{card_id}")
    def _detail_unavailable(card_id: str) -> None:
        raise HTTPException(status_code=503, detail=card_id)

    return app


def _patch_build_app(monkeypatch: pytest.MonkeyPatch, app: FastAPI) -> None:
    """让 ``console.build_app`` 返回指定替身组合根（不触碰真实库）。"""

    def _fake_build_app(db_url: str | None = None) -> FastAPI:
        del db_url
        return app

    monkeypatch.setattr(console, "build_app", _fake_build_app)


@pytest.fixture
def cards_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[tuple[Path, str, str]]:
    """临时库 + 两张卡（手填「灰烬旅人」与 LLM「观星者」）；用例结束释放 Engine。"""
    db_path = tmp_path / "console-cards.db"
    manual_id, llm_id = _seed_cards(monkeypatch, db_path, [_MANUAL_CARD, _LLM_CARD])
    yield db_path, manual_id, llm_id


def test_parser_supports_card_library_flags() -> None:
    """CLI：卡库浏览参数可解析，未给时默认不启用（不改变旧参数默认值）。"""
    parser = console._build_parser()
    defaults = parser.parse_args([])
    assert defaults.card_query is None
    assert defaults.card_limit is None
    assert defaults.card_all is False
    assert defaults.card_detail is None

    parsed = parser.parse_args(["--list-cards", "--card-query", "灰烬", "--card-limit", "5"])
    assert parsed.list_cards is True
    assert parsed.card_query == "灰烬"
    assert parsed.card_limit == 5
    assert parser.parse_args(["--card-all"]).card_all is True
    assert parser.parse_args(["--card-detail", _UNKNOWN_CARD_ID]).card_detail == _UNKNOWN_CARD_ID


def test_list_cards_shows_source_updated_and_play_hints(
    monkeypatch: pytest.MonkeyPatch,
    cards_db: tuple[Path, str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--list-cards``：展示标题/来源标签/更新时间/完整卡 ID，并给按卡开局与详情提示。"""
    db_path, manual_id, _llm_id = cards_db

    code = _run_console(["--db", str(db_path), "--list-cards"])

    out = capsys.readouterr().out
    assert code == 0
    assert "最近人物卡" in out
    assert "灰烬旅人" in out and "观星者" in out
    assert "[手填/模板]" in out and "[LLM 生成]" in out
    assert "更新 " in out
    assert manual_id in out
    assert "--card-id" in out
    assert "--card-detail" in out
    assert "Traceback" not in out
    console.assert_clean_output(out)
    _assert_no_games(monkeypatch, db_path)


@pytest.mark.parametrize(
    ("query", "shown", "hidden"),
    [
        ("观星", "观星者", "灰烬旅人"),
        ("手填", "灰烬旅人", "观星者"),
        ("llm", "观星者", "灰烬旅人"),
    ],
    ids=["by-title", "by-source-label", "by-source-raw"],
)
def test_list_cards_query_filters_title_or_source(
    monkeypatch: pytest.MonkeyPatch,
    cards_db: tuple[Path, str, str],
    capsys: pytest.CaptureFixture[str],
    query: str,
    shown: str,
    hidden: str,
) -> None:
    """``--card-query`` 客户端过滤：命中标题/来源标签/来源原值（大小写不敏感）。"""
    db_path, _manual_id, _llm_id = cards_db

    code = _run_console(["--db", str(db_path), "--list-cards", "--card-query", query])

    out = capsys.readouterr().out
    assert code == 0
    assert f"匹配「{query}」" in out
    assert shown in out
    assert hidden not in out


def test_list_cards_query_without_match_is_friendly(
    monkeypatch: pytest.MonkeyPatch,
    cards_db: tuple[Path, str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """关键词无匹配：exit 0 + 玩家可读中文提示（不崩、不显示卡 ID、不建局）。"""
    db_path, _manual_id, _llm_id = cards_db

    code = _run_console(["--db", str(db_path), "--list-cards", "--card-query", "不存在的名字"])

    out = capsys.readouterr().out
    assert code == 0
    assert "没有匹配" in out
    assert "--card-query" in out
    assert " · 卡 card_" not in out
    assert "Traceback" not in out
    _assert_no_games(monkeypatch, db_path)


def test_list_cards_limit_caps_displayed_rows(
    monkeypatch: pytest.MonkeyPatch,
    cards_db: tuple[Path, str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--card-limit 1``：只显示 1 张，并提示用 ``--card-limit``/``--card-all`` 看更多。"""
    db_path, _manual_id, _llm_id = cards_db

    code = _run_console(["--db", str(db_path), "--list-cards", "--card-limit", "1"])

    out = capsys.readouterr().out
    assert code == 0
    assert "显示 1 张" in out
    assert out.count(" · 卡 card_") == 1
    assert "--card-limit" in out and "--card-all" in out


def test_list_cards_all_shows_every_card_within_cap(
    monkeypatch: pytest.MonkeyPatch,
    cards_db: tuple[Path, str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--card-all``：显示上限（100）内的全部人物卡（两张卡都出现且行数正确）。"""
    db_path, _manual_id, _llm_id = cards_db

    code = _run_console(["--db", str(db_path), "--list-cards", "--card-all"])

    out = capsys.readouterr().out
    assert code == 0
    assert "显示 2 张" in out
    assert out.count(" · 卡 card_") == 2
    assert "灰烬旅人" in out and "观星者" in out
    assert "Traceback" not in out


def test_list_cards_empty_library_is_friendly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """空卡库：友好提示「还没有人物卡」，exit 0（不建局、不写库）。"""
    db_path = tmp_path / "empty-cards.db"

    code = _run_console(["--db", str(db_path), "--list-cards"])

    out = capsys.readouterr().out
    assert code == 0
    assert "还没有人物卡" in out
    assert "Traceback" not in out
    _assert_no_games(monkeypatch, db_path)


@pytest.mark.parametrize("limit", [0, -1, 101], ids=["zero", "negative", "over-max"])
def test_card_limit_out_of_range_is_friendly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str], limit: int
) -> None:
    """``--card-limit`` 超出 1..100：exit 1 + 中文范围提示（不建库、不跑浏览）。"""
    del monkeypatch  # 参数校验发生在装配之前，无需触碰数据库
    db_path = tmp_path / f"limit-{limit}.db"

    code = _run_console(["--db", str(db_path), "--list-cards", "--card-limit", str(limit)])

    out = capsys.readouterr().out
    assert code == 1
    assert "1 到 100" in out
    assert "Traceback" not in out


@pytest.mark.parametrize(
    "extra_args",
    [["--card-query", "灰烬"], ["--card-limit", "5"], ["--card-all"]],
    ids=["query", "limit", "all"],
)
def test_card_browse_flags_require_list_cards(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], extra_args: list[str]
) -> None:
    """``--card-query/--card-limit/--card-all`` 必须搭配 ``--list-cards``（互斥清晰）。"""
    del monkeypatch
    code = _run_console(extra_args)

    out = capsys.readouterr().out
    assert code == 1
    assert "只能与 --list-cards 一起使用" in out
    assert "Traceback" not in out


def test_card_all_and_limit_conflict_is_friendly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--card-all`` 与 ``--card-limit`` 互斥：exit 1 中文提示。"""
    del monkeypatch
    code = _run_console(["--list-cards", "--card-all", "--card-limit", "5"])

    out = capsys.readouterr().out
    assert code == 1
    assert "不能同时使用" in out


def test_card_detail_shows_safe_subset(
    monkeypatch: pytest.MonkeyPatch,
    cards_db: tuple[Path, str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--card-detail``：标题/来源/属性摘要/背包提示/警告；不渲染背景与契约字段。"""
    db_path, manual_id, _llm_id = cards_db

    code = _run_console(["--db", str(db_path), "--card-detail", manual_id])

    out = capsys.readouterr().out
    assert code == 0
    assert "【人物卡】灰烬旅人" in out
    assert "来源：手填/模板" in out
    assert f"卡 {manual_id}" in out
    assert "生命：HP 7/18" in out
    assert "力量 10" in out and "敏捷 12" in out and "魅力 8" in out
    assert "背包：" in out and "治疗药水" in out
    assert "警告：" in out and "生命值未满" in out
    assert "--card-id" in out
    # 安全子集：不渲染 background 自由文本与契约字段
    assert "旧伤未愈" not in out
    assert "schema_version" not in out and "generated_by" not in out
    assert "/api/v1" not in out
    assert "Traceback" not in out
    console.assert_clean_output(out)
    _assert_no_games(monkeypatch, db_path)


def test_card_detail_full_hp_card_has_no_warning(
    monkeypatch: pytest.MonkeyPatch,
    cards_db: tuple[Path, str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """满 HP 的卡详情显示「警告：无」，背包按可读物品名列出。"""
    db_path, _manual_id, llm_id = cards_db

    code = _run_console(["--db", str(db_path), "--card-detail", llm_id])

    out = capsys.readouterr().out
    assert code == 0
    assert "【人物卡】观星者" in out
    assert "来源：LLM 生成" in out
    assert "生命：HP 18/18" in out
    assert "警告：无" in out
    assert "火把" in out and "治疗药水" in out


def test_card_detail_unknown_id_is_friendly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """不存在/已删除的卡：exit 1、中文提示、无 traceback、不暴露端点、不建局。"""
    db_path = tmp_path / "detail-unknown.db"

    code = _run_console(["--db", str(db_path), "--card-detail", _UNKNOWN_CARD_ID])

    out = capsys.readouterr().out
    assert code == 1
    assert "找不到这张人物卡" in out
    assert "Traceback" not in out
    assert "/api/v1" not in out
    _assert_no_games(monkeypatch, db_path)


@pytest.mark.parametrize("bad_id", ["not-a-card", "card_短"], ids=["no-prefix", "too-short"])
def test_card_detail_invalid_id_is_friendly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str], bad_id: str
) -> None:
    """非法卡 ID：exit 1 + 中文格式提示（提示从 --list-cards 复制完整卡 ID）。"""
    db_path = tmp_path / "detail-invalid.db"

    code = _run_console(["--db", str(db_path), "--card-detail", bad_id])

    out = capsys.readouterr().out
    assert code == 1
    assert "ID 格式不正确" in out
    assert "--list-cards" in out
    assert "Traceback" not in out


@pytest.mark.parametrize(
    "extra_args",
    [["--list-cards"], ["--card-id", _UNKNOWN_CARD_ID], ["--list"], ["--list-scenarios"]],
    ids=["list-cards", "card-id", "list-games", "list-scenarios"],
)
def test_card_detail_conflicts_are_friendly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], extra_args: list[str]
) -> None:
    """``--card-detail`` 与列表/开局参数互斥：exit 1 中文提示（不建局）。"""
    del monkeypatch
    code = _run_console(["--card-detail", _UNKNOWN_CARD_ID, *extra_args])

    out = capsys.readouterr().out
    assert code == 1
    assert "不能与" in out
    assert "Traceback" not in out


def test_list_cards_backend_unavailable_is_friendly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """后端卡库端点不可用：exit 1 + 玩家可读提示（不显示端点/异常细节）。"""
    _patch_build_app(monkeypatch, _unavailable_app())

    code = _run_console(["--list-cards"])

    out = capsys.readouterr().out
    assert code == 1
    assert "暂时打不开" in out
    assert "网页版卡库" in out
    assert "Traceback" not in out
    assert "/api/v1" not in out


def test_card_detail_backend_unavailable_is_friendly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """后端详情端点不可用：exit 1 + 玩家可读提示（不显示端点/异常细节）。"""
    _patch_build_app(monkeypatch, _unavailable_app())

    code = _run_console(["--card-detail", _UNKNOWN_CARD_ID])

    out = capsys.readouterr().out
    assert code == 1
    assert "暂时打不开" in out
    assert "Traceback" not in out
    assert "/api/v1" not in out


def test_card_library_build_failure_is_friendly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """组合根装配失败（库打不开/路径不可用）：exit 1 中文提示，不打印内部异常。"""

    def _boom(db_url: str | None = None) -> FastAPI:
        del db_url
        raise RuntimeError("internal-boom")

    monkeypatch.setattr(console, "build_app", _boom)

    code = _run_console(["--list-cards"])

    out = capsys.readouterr().out
    assert code == 1
    assert "暂时打不开" in out
    assert "internal-boom" not in out
    assert "Traceback" not in out


def test_interactive_help_mentions_card_browse() -> None:
    """交互帮助文本补一行卡库浏览用法（列表过滤/限量/全量 + 单卡详情）。"""
    lines: list[str] = []
    console._print_help(lines.append)

    joined = "\n".join(lines)
    assert "--card-query" in joined
    assert "--card-limit" in joined
    assert "--card-all" in joined
    assert "--card-detail" in joined
