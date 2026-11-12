"""N3-045 场景包批量导入/导出（终端 CLI）验收：导入/跳过/失败/上限/导出 roundtrip/帮助。

装配口径与 ``test_console_card.py`` 一致：临时 SQLite + 进程内组合根（``console.build_app``，
不占端口、不联网）；场景 JSON 为纯数据（用户 JSON 永不执行）。

覆盖：
1. ``--import-scenarios``：v1+v2 成功、重跑幂等跳过、同内容去重、非法/超大文件逐文件
   ``[error]`` 中文报告、部分失败退出码 1、空/不存在目录、数量上限与校验；
2. ``--export-scenarios``：列表+详情取原始定义写 ``<slug>.json``、重名加后缀、导出文件
   可直接再导入（roundtrip 定义逐字段一致）、空库/上限/错误；
3. 帮助文本补两行用法；互斥参数清晰报错；不改旧 ``--list-scenarios`` 行为。
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from textgame import console
from textgame.config.settings import get_settings
from textgame.scenario_pack import (
    MAX_BATCH_LIMIT,
    MAX_SCENARIO_FILE_BYTES,
    encode_export_payload,
)

pytestmark = [pytest.mark.integration]


def _tiny(
    *, schema_version: int = 1, slug: str = "tiny-tower", title: str = "小塔"
) -> dict[str, Any]:
    """最小合法可玩定义（v1/v2 同构；与 ``examples/scenarios/tiny-tower.json`` 对齐）。"""
    return {
        "schema_version": schema_version,
        "slug": slug,
        "meta": {"title": title, "author": "示例", "description": "两房间最小可玩场景"},
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
    }


def _write_json(directory: Path, name: str, payload: object) -> Path:
    """把定义写成 UTF-8 JSON 文件并返回路径。"""
    path = directory / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


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


def _scenario_rows(monkeypatch: pytest.MonkeyPatch, db_path: Path) -> list[dict[str, Any]]:
    """只读读取场景库列表（``GET /scenarios``）。"""
    app = _build_app_with_db(monkeypatch, db_path)
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/scenarios", params={"limit": MAX_BATCH_LIMIT})
            assert response.status_code == 200, response.text
            payload: dict[str, Any] = response.json()
            return [row for row in payload["scenarios"] if isinstance(row, dict)]
    finally:
        _dispose(app)


def _definition_of(
    monkeypatch: pytest.MonkeyPatch, db_path: Path, scenario_id: str
) -> dict[str, Any]:
    """只读读取单个场景的原始 ``definition``（``GET /scenarios/{id}``）。"""
    app = _build_app_with_db(monkeypatch, db_path)
    try:
        with TestClient(app) as client:
            response = client.get(f"/api/v1/scenarios/{scenario_id}")
            assert response.status_code == 200, response.text
            definition = response.json()["definition"]
            assert isinstance(definition, dict)
            return definition
    finally:
        _dispose(app)


@pytest.fixture
def pack_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[tuple[Path, Path]]:
    """``(场景库 DB 路径, 工作目录)``；用例结束无需额外收尾。"""
    yield tmp_path / "scenario-pack.db", tmp_path / "work"


# ---------------------------------------------------------------------------
# 1. 批量导入：成功 / 幂等跳过 / 逐文件错误 / 上限
# ---------------------------------------------------------------------------
def test_import_scenarios_v1_and_v2_ok(
    monkeypatch: pytest.MonkeyPatch, pack_env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """目录内 v1+v2 场景逐个导入成功：``[ok]`` 行 + 汇总 + 场景库落库（slug 正确）。"""
    db_path, work = pack_env
    inbox = work / "inbox"
    inbox.mkdir(parents=True)
    _write_json(inbox, "tiny-tower.json", _tiny())
    _write_json(
        inbox, "tiny-tower-v2.json", _tiny(schema_version=2, slug="tiny-tower-v2", title="小塔 v2")
    )

    code = _run_console(["--db", str(db_path), "--import-scenarios", str(inbox)])

    out = capsys.readouterr().out
    assert code == 0
    assert "批量导入场景包" in out
    assert "[ok] tiny-tower.json" in out
    assert "[ok] tiny-tower-v2.json" in out
    assert "汇总：成功 2 / 跳过 0 / 失败 0" in out
    assert "--scenario-id" in out
    rows = _scenario_rows(monkeypatch, db_path)
    assert {row["slug"] for row in rows} == {"tiny-tower", "tiny-tower-v2"}
    assert {row["schema_version"] for row in rows} == {1, 2}


def test_import_scenarios_rerun_skips_without_duplicates(
    monkeypatch: pytest.MonkeyPatch, pack_env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """重跑同一目录：幂等键由内容派生 → 全部 ``[skip]``、不重复建档、退出码 0。"""
    db_path, work = pack_env
    inbox = work / "inbox"
    inbox.mkdir(parents=True)
    _write_json(inbox, "tiny-tower.json", _tiny())
    argv = ["--db", str(db_path), "--import-scenarios", str(inbox)]

    assert _run_console(argv) == 0
    capsys.readouterr()
    code = _run_console(argv)

    out = capsys.readouterr().out
    assert code == 0
    assert "[skip] tiny-tower.json" in out
    assert "已存在（同内容）" in out
    assert "汇总：成功 0 / 跳过 1 / 失败 0" in out
    assert len(_scenario_rows(monkeypatch, db_path)) == 1


def test_import_scenarios_duplicate_content_in_same_batch_skips(
    monkeypatch: pytest.MonkeyPatch, pack_env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """同目录两份内容相同的文件：第一份 ok、第二份 skip（同内容不重复建档）。"""
    db_path, work = pack_env
    inbox = work / "inbox"
    inbox.mkdir(parents=True)
    data = json.dumps(_tiny(), ensure_ascii=False, indent=2)
    (inbox / "a.json").write_text(data, encoding="utf-8")
    (inbox / "b-copy.json").write_text(data, encoding="utf-8")

    code = _run_console(["--db", str(db_path), "--import-scenarios", str(inbox)])

    out = capsys.readouterr().out
    assert code == 0
    assert "[ok] a.json" in out
    assert "[skip] b-copy.json" in out
    assert "汇总：成功 1 / 跳过 1 / 失败 0" in out
    assert len(_scenario_rows(monkeypatch, db_path)) == 1


def test_import_scenarios_invalid_files_report_error_and_exit_1(
    monkeypatch: pytest.MonkeyPatch, pack_env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """非法 JSON / 顶层非对象 / 超 256KB / Schema 不合法：逐文件 ``[error]`` 中文原因 + 退出码 1。"""
    db_path, work = pack_env
    inbox = work / "inbox"
    inbox.mkdir(parents=True)
    _write_json(inbox, "ok.json", _tiny())
    (inbox / "broken.json").write_text("{not-json", encoding="utf-8")
    (inbox / "array.json").write_text("[1, 2, 3]", encoding="utf-8")
    (inbox / "huge.json").write_text(
        '{"schema_version": 1, "padding": "' + "x" * (MAX_SCENARIO_FILE_BYTES + 1024) + '"}',
        encoding="utf-8",
    )
    _write_json(inbox, "schema-invalid.json", {"schema_version": 1, "slug": "broken"})

    code = _run_console(["--db", str(db_path), "--import-scenarios", str(inbox)])

    out = capsys.readouterr().out
    assert code == 1
    assert "[ok] ok.json" in out
    assert "[error] broken.json → 文件不是合法的场景内容" in out
    assert "[error] array.json → 场景文件顶层必须是对象" in out
    assert f"[error] huge.json → 文件超过 {MAX_SCENARIO_FILE_BYTES // 1024}KB 上限" in out
    assert "[error] schema-invalid.json → 场景内容未通过校验" in out
    assert "汇总：成功 1 / 跳过 0 / 失败 4" in out
    assert "Traceback" not in out
    assert "/api/v1" not in out
    assert len(_scenario_rows(monkeypatch, db_path)) == 1


def test_import_scenarios_empty_or_missing_directory_is_friendly(
    monkeypatch: pytest.MonkeyPatch, pack_env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """空目录：友好提示 + 退出码 0；不存在目录：中文报错 + 退出码 1（不建库、不崩）。"""
    db_path, work = pack_env
    empty = work / "empty"
    empty.mkdir(parents=True)

    code = _run_console(["--db", str(db_path), "--import-scenarios", str(empty)])
    out = capsys.readouterr().out
    assert code == 0
    assert "目录里没有可导入" in out

    missing = work / "missing-dir"
    code = _run_console(["--db", str(db_path), "--import-scenarios", str(missing)])
    out = capsys.readouterr().out
    assert code == 1
    assert "目录不存在" in out
    assert "Traceback" not in out
    assert not db_path.exists() or _scenario_rows(monkeypatch, db_path) == []


def test_import_scenarios_limit_processes_first_n(
    monkeypatch: pytest.MonkeyPatch, pack_env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """``--import-limit 2``：目录 3 个文件只处理前 2 个，提示可用 --import-limit 调整。"""
    db_path, work = pack_env
    inbox = work / "inbox"
    inbox.mkdir(parents=True)
    for index, name in enumerate(("a.json", "b.json", "c.json")):
        _write_json(
            inbox,
            name,
            _tiny(slug=f"tiny-{index}", title=f"小塔 {index}"),
        )

    code = _run_console(
        ["--db", str(db_path), "--import-scenarios", str(inbox), "--import-limit", "2"]
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "共 3 个 .json，本次处理前 2 个" in out
    assert "汇总：成功 2 / 跳过 0 / 失败 0" in out
    assert len(_scenario_rows(monkeypatch, db_path)) == 2


@pytest.mark.parametrize(
    "limit", [0, -1, MAX_BATCH_LIMIT + 1], ids=["zero", "negative", "over-max"]
)
def test_import_limit_out_of_range_is_friendly(
    monkeypatch: pytest.MonkeyPatch,
    pack_env: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    limit: int,
) -> None:
    """``--import-limit`` 超出 1..100：退出码 1 + 中文范围提示（不扫描、不建库）。"""
    del monkeypatch
    db_path, work = pack_env
    inbox = work / "inbox"
    inbox.mkdir(parents=True)

    code = _run_console(
        ["--db", str(db_path), "--import-scenarios", str(inbox), "--import-limit", str(limit)]
    )

    out = capsys.readouterr().out
    assert code == 1
    assert "1 到 100" in out
    assert "Traceback" not in out


def test_import_limit_requires_import_scenarios(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--import-limit`` 脱离 ``--import-scenarios``：退出码 1 中文互斥提示。"""
    del monkeypatch
    code = _run_console(["--import-limit", "5"])

    out = capsys.readouterr().out
    assert code == 1
    assert "只能与 --import-scenarios 一起使用" in out


def test_import_and_export_scenarios_are_mutually_exclusive(
    monkeypatch: pytest.MonkeyPatch, pack_env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """``--import-scenarios`` 与 ``--export-scenarios`` 互斥：退出码 1 中文提示。"""
    del monkeypatch
    _db_path, work = pack_env
    inbox = work / "inbox"
    inbox.mkdir(parents=True)
    outbox = work / "outbox"

    code = _run_console(["--import-scenarios", str(inbox), "--export-scenarios", str(outbox)])

    out = capsys.readouterr().out
    assert code == 1
    assert "不能同时使用" in out


def test_import_scenarios_conflicts_with_other_actions(
    monkeypatch: pytest.MonkeyPatch, pack_env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """批量导入不能与 ``--list-scenarios`` 等浏览/开局参数同用：中文互斥提示。"""
    del monkeypatch
    _db_path, work = pack_env
    inbox = work / "inbox"
    inbox.mkdir(parents=True)

    code = _run_console(["--import-scenarios", str(inbox), "--list-scenarios"])

    out = capsys.readouterr().out
    assert code == 1
    assert "不能与" in out
    assert "--list-scenarios" in out


# ---------------------------------------------------------------------------
# 2. 批量导出 + roundtrip
# ---------------------------------------------------------------------------
def _seed_via_import(
    db_path: Path,
    work: Path,
    definitions: dict[str, dict[str, Any]],
    *,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """把给定定义写入临时目录并用 CLI 批量导入（测试数据准备）。"""
    inbox = work / f"seed-{uuid.uuid4().hex[:8]}"
    inbox.mkdir(parents=True)
    for name, definition in definitions.items():
        _write_json(inbox, name, definition)
    code = _run_console(["--db", str(db_path), "--import-scenarios", str(inbox)])
    capsys.readouterr()
    assert code == 0


def test_export_scenarios_roundtrip_definitions_match(
    monkeypatch: pytest.MonkeyPatch, pack_env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """导出 → 逐文件 ``<slug>.json`` → 再导入新库：场景数与定义逐字段一致（roundtrip）。"""
    db_path, work = pack_env
    definitions = {
        "tiny-tower.json": _tiny(),
        "tiny-tower-v2.json": _tiny(schema_version=2, slug="tiny-tower-v2", title="小塔 v2"),
    }
    _seed_via_import(db_path, work, definitions, capsys=capsys)
    outbox = work / "outbox"

    export_code = _run_console(["--db", str(db_path), "--export-scenarios", str(outbox)])

    export_out = capsys.readouterr().out
    assert export_code == 0
    assert "批量导出场景包" in export_out
    assert "[ok]" in export_out and "汇总：成功 2 / 失败 0" in export_out
    exported_files = sorted(path.name for path in outbox.glob("*.json"))
    assert "tiny-tower.json" in exported_files and "tiny-tower-v2.json" in exported_files
    for path in outbox.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(payload, dict) and payload["slug"]
        assert encode_export_payload(payload)  # 结构可再次编码（无非法数值）

    second_db = work / "roundtrip.db"
    import_code = _run_console(["--db", str(second_db), "--import-scenarios", str(outbox)])
    assert import_code == 0
    capsys.readouterr()

    first_rows = _scenario_rows(monkeypatch, db_path)
    second_rows = _scenario_rows(monkeypatch, second_db)
    assert len(first_rows) == len(second_rows) == 2
    for first, second in zip(
        sorted(first_rows, key=lambda row: row["slug"]),
        sorted(second_rows, key=lambda row: row["slug"]),
        strict=False,
    ):
        assert _definition_of(monkeypatch, db_path, first["scenario_id"]) == _definition_of(
            monkeypatch, second_db, second["scenario_id"]
        )


def test_export_scenarios_duplicate_slug_gets_id_suffix(
    monkeypatch: pytest.MonkeyPatch, pack_env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """同 slug 的两个场景导出：第二个文件名追加 ``-<scenario_id 后 8 位>``（不互相覆盖）。"""
    db_path, work = pack_env
    first = _tiny(title="同名一")
    second = _tiny(title="同名二")
    inbox = work / "inbox"
    inbox.mkdir(parents=True)
    _write_json(inbox, "one.json", first)
    _write_json(inbox, "two.json", second)
    assert _run_console(["--db", str(db_path), "--import-scenarios", str(inbox)]) == 0
    capsys.readouterr()
    outbox = work / "outbox"

    code = _run_console(["--db", str(db_path), "--export-scenarios", str(outbox)])

    out = capsys.readouterr().out
    assert code == 0
    files = sorted(path.name for path in outbox.glob("*.json"))
    assert len(files) == 2
    assert "tiny-tower.json" in files
    suffixed = [name for name in files if name.startswith("tiny-tower-")]
    assert len(suffixed) == 1
    assert suffixed[0].endswith(".json")
    assert "[ok]" in out


def test_export_scenarios_empty_library_is_friendly(
    monkeypatch: pytest.MonkeyPatch, pack_env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """空场景库导出：友好提示 + 退出码 0（不写文件）。"""
    db_path, work = pack_env
    outbox = work / "outbox"

    code = _run_console(["--db", str(db_path), "--export-scenarios", str(outbox)])

    out = capsys.readouterr().out
    assert code == 0
    assert "还没有可导出的场景" in out
    assert not list(outbox.glob("*.json"))


def test_export_scenarios_limit(
    monkeypatch: pytest.MonkeyPatch, pack_env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """``--export-limit 2``：只导出 2 个文件并提示可用 --export-limit 调整。"""
    db_path, work = pack_env
    definitions = {
        f"tiny-{index}.json": _tiny(slug=f"tiny-{index}", title=f"小塔 {index}")
        for index in range(3)
    }
    _seed_via_import(db_path, work, definitions, capsys=capsys)
    outbox = work / "outbox"

    code = _run_console(
        ["--db", str(db_path), "--export-scenarios", str(outbox), "--export-limit", "2"]
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "场景库共 3 个，本次导出 2 个" in out
    assert "可用 --export-limit 调整" in out
    assert len(list(outbox.glob("*.json"))) == 2


@pytest.mark.parametrize("limit", [0, MAX_BATCH_LIMIT + 1], ids=["zero", "over-max"])
def test_export_limit_out_of_range_is_friendly(
    monkeypatch: pytest.MonkeyPatch,
    pack_env: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    limit: int,
) -> None:
    """``--export-limit`` 超出 1..100：退出码 1 中文范围提示。"""
    del monkeypatch
    _db_path, work = pack_env

    code = _run_console(["--export-scenarios", str(work / "out"), "--export-limit", str(limit)])

    out = capsys.readouterr().out
    assert code == 1
    assert "1 到 100" in out


# ---------------------------------------------------------------------------
# 3. 帮助 / 解析器 / 旧行为
# ---------------------------------------------------------------------------
def test_parser_supports_scenario_pack_flags() -> None:
    """CLI：批量导入/导出四个参数可解析，未给时默认 None（不影响旧参数默认值）。"""
    parser = console._build_parser()
    defaults = parser.parse_args([])
    assert defaults.import_scenarios is None
    assert defaults.import_limit is None
    assert defaults.export_scenarios is None
    assert defaults.export_limit is None

    parsed = parser.parse_args(
        [
            "--import-scenarios",
            "in",
            "--import-limit",
            "7",
            "--export-scenarios",
            "out",
            "--export-limit",
            "9",
        ]
    )
    assert parsed.import_scenarios == "in" and parsed.import_limit == 7
    assert parsed.export_scenarios == "out" and parsed.export_limit == 9


def test_interactive_help_mentions_scenario_pack() -> None:
    """交互 help 补两行场景包用法（导入/导出 + 数量默认 50/上限 100）。"""
    lines: list[str] = []
    console._print_help(lines.append)

    joined = "\n".join(lines)
    assert "--import-scenarios" in joined
    assert "--export-scenarios" in joined
    assert "--import-limit" in joined and "--export-limit" in joined
    assert "默认 50、上限 100" in joined


def test_list_scenarios_behavior_unchanged(
    monkeypatch: pytest.MonkeyPatch, pack_env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """旧 ``--list-scenarios`` 行为不变：导入后仍按原格式列出（标题/场景 ID/开局提示）。"""
    db_path, work = pack_env
    _seed_via_import(db_path, work, {"tiny-tower.json": _tiny()}, capsys=capsys)

    code = _run_console(["--db", str(db_path), "--list-scenarios"])

    out = capsys.readouterr().out
    assert code == 0
    assert "已上传场景" in out
    assert "小塔" in out
    assert "--scenario-id" in out
    assert "Traceback" not in out
