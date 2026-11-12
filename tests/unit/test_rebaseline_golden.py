"""m3 叙事受控重基线工具（``scripts/rebaseline_golden.py``，N4-054 / D-N4-1）单元测试。

覆盖受控流程的安全边界：
- **dry-run 只读**：对真实冻结 m3 golden 重生成 → 0 diff / 哈希相等 / 目标文件字节不变 / 无备份；
- **拒绝未知 golden**（registry 只含 m3）与 **apply 未确认**（``--confirm`` 必须严格等于 golden）；
- **apply 受控写入**（在临时副本上演示，绝不改 tracked golden）：只改目标文件、先写 ``.bak`` +
  ``.sha256``、写后哈希自检、报告含逐字段 old/new diff；
- 报告结构（``changed_fields``/``diff_field_count``/``diff_turn_count``/``old/new_sha256``/checks）。

全量 pytest 内保持快速（每个用例只跑 10 回合内存装配，整文件 ≈3–5s；不联网、不写生产库）。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "rebaseline_golden.py"
_REAL_GOLDEN_DIR = _REPO_ROOT / "tests" / "fixtures" / "golden"
_REAL_M3 = _REAL_GOLDEN_DIR / "m3_golden.json"
#: 冻结 m3 golden 的 LF 规范化 sha256（与 tests/replay/test_golden_provenance.py 同源）。
_FROZEN_M3_SHA256 = "438d8bed1213ec5f0526a55e941e851bc2f6f0d3e06aea8d15b892433e48c48b"


def _load_module() -> ModuleType:
    """按路径加载脚本模块（``scripts/`` 非包、文件名含连字符；dataclass 需注册 sys.modules）。"""
    spec = importlib.util.spec_from_file_location("rebaseline_golden", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None, f"无法加载 {_SCRIPT_PATH}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


@pytest.fixture(scope="module")
def rebaseline() -> ModuleType:
    """受控重基线脚本模块。"""
    return _load_module()


def _sha256(path: Path) -> str:
    """原始字节 sha256（对比「文件是否被改动」用，不受行尾影响）。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _copy_golden_dir(tmp_path: Path) -> Path:
    """把 m3（目标）+ m2（诱饵，验证只改目标）复制到临时目录。"""
    golden_dir = tmp_path / "golden"
    golden_dir.mkdir()
    for name in ("m3_golden.json", "m2_golden.json"):
        (golden_dir / name).write_bytes((_REAL_GOLDEN_DIR / name).read_bytes())
    return golden_dir


def _perturb_m3(path: Path) -> dict[str, Any]:
    """模拟「叙事层已变更但 golden 未重基线」：改一个 text_len 与 final_state_hash。"""
    document = json.loads(path.read_text(encoding="utf-8"))
    document["turns"][5]["text_len"] += 1
    document["final_state_hash"] = "0" * 64
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return document


def test_dry_run_is_read_only_and_zero_diff_on_frozen_m3(
    rebaseline: ModuleType, tmp_path: Path
) -> None:
    """dry-run：对冻结 m3 重生成 → 0 diff、哈希相等、目标文件字节不变、无备份写入。"""
    before_bytes = _REAL_M3.read_bytes()
    before_sha = _sha256(_REAL_M3)
    out = tmp_path / "report.json"
    backup_dir = tmp_path / "backup"
    report = rebaseline.run_rebaseline(
        golden="m3",
        mode="dry-run",
        golden_dir=_REAL_GOLDEN_DIR,
        out=out,
        diff_report=True,
        backup_dir=backup_dir,
    )

    assert report["schema_version"] == 1
    assert report["kind"] == "golden-rebaseline-report"
    assert report["golden"] == "m3"
    assert report["mode"] == "dry-run"
    assert report["applied"] is False
    assert report["result"] == "no-diff"
    assert report["turns_compared"] == 10
    assert report["diff_field_count"] == 0
    assert report["diff_turn_count"] == 0
    assert report["changed_fields"] == []
    assert report["diffs"] == []
    assert report["old_sha256"] == report["new_sha256"] == _FROZEN_M3_SHA256
    assert report["hashes_match"] is True
    assert report["backup"] is None and report["write"] is None
    assert all(report["checks"].values()), report["checks"]
    # 只读语义：真实 golden 字节不变、未创建备份目录、报告已落盘且结构完整
    assert _REAL_M3.read_bytes() == before_bytes
    assert _sha256(_REAL_M3) == before_sha
    assert not backup_dir.exists()
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["result"] == "no-diff"


def test_unknown_golden_is_rejected(
    rebaseline: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """registry 只含 m3：未知/未支持 golden 一律拒绝（函数抛错 + CLI exit 2）。"""
    for name in ("m9", "m2", "m4"):
        with pytest.raises(rebaseline.GoldenNotSupportedError):
            rebaseline.run_rebaseline(golden=name, mode="dry-run", golden_dir=_REAL_GOLDEN_DIR)
    assert rebaseline.main(["--golden", "m9", "--dry-run"]) == 2
    assert "未知/不支持的 golden" in capsys.readouterr().err


def test_apply_requires_explicit_matching_confirm(
    rebaseline: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """apply 缺 ``--confirm`` 或 confirm 与 golden 不一致：拒绝且零写入/零备份。"""
    golden_dir = _copy_golden_dir(tmp_path)
    target = golden_dir / "m3_golden.json"
    _perturb_m3(target)
    before = _sha256(target)
    backup_dir = tmp_path / "backup"

    for confirm in (None, "", "m2", "M3"):
        with pytest.raises(rebaseline.RebaselineError, match="--confirm m3"):
            rebaseline.run_rebaseline(
                golden="m3",
                mode="apply",
                golden_dir=golden_dir,
                confirm=confirm,
                backup_dir=backup_dir,
            )
    assert _sha256(target) == before
    assert not backup_dir.exists()
    assert (
        rebaseline.main(
            [
                "--golden",
                "m3",
                "--apply",
                "--golden-dir",
                str(golden_dir),
                "--backup-dir",
                str(backup_dir),
            ]
        )
        == 2
    )
    assert "--confirm m3" in capsys.readouterr().err


def test_apply_writes_only_target_with_backup_and_sha(
    rebaseline: ModuleType, tmp_path: Path
) -> None:
    """apply（临时副本）：只改 m3 目标、先备份 .bak + .sha256、报告逐字段 diff、恢复冻结基线。"""
    golden_dir = _copy_golden_dir(tmp_path)
    target = golden_dir / "m3_golden.json"
    decoy = golden_dir / "m2_golden.json"
    decoy_before = decoy.read_bytes()
    real_before = _REAL_M3.read_bytes()
    perturbed = _perturb_m3(target)
    perturbed_bytes = target.read_bytes()
    backup_dir = tmp_path / "backup"
    out = tmp_path / "apply-report.json"

    report = rebaseline.run_rebaseline(
        golden="m3",
        mode="apply",
        golden_dir=golden_dir,
        out=out,
        diff_report=True,
        confirm="m3",
        backup_dir=backup_dir,
    )

    # diff 报告：2 处字段差异（final_state_hash + 一个 turn text_len），old/new 可见
    assert report["applied"] is True
    assert report["result"] == "applied"
    assert report["diff_field_count"] == 2
    assert report["diff_turn_count"] == 1
    assert report["changed_fields"] == ["final_state_hash", "turns[5].text_len"]
    diffs = {item["path"]: item for item in report["diffs"]}
    assert diffs["turns[5].text_len"]["old"] == perturbed["turns"][5]["text_len"]
    assert diffs["turns[5].text_len"]["new"] == perturbed["turns"][5]["text_len"] - 1
    assert diffs["final_state_hash"]["old"] == "0" * 64
    # 写后哈希自检：目标恢复为冻结基线（LF 规范化 sha 与 provenance pin 一致）
    assert report["write"]["normalized_sha256"] == report["new_sha256"] == _FROZEN_M3_SHA256
    assert rebaseline.normalized_sha256(target) == _FROZEN_M3_SHA256
    # 备份：原始（被污染的）字节 + 双哈希文件；report 记录可追溯
    backup_file = backup_dir / "m3_golden.json.bak"
    backup_sha_file = backup_dir / "m3_golden.json.bak.sha256"
    assert backup_file.read_bytes() == perturbed_bytes
    sha_lines = backup_sha_file.read_text(encoding="utf-8")
    assert report["backup"]["normalized_sha256"] in sha_lines
    assert "raw_sha256=" in sha_lines
    assert report["backup"]["file"] == str(backup_file)
    # —— 只改目标文件：诱饵 m2 不变、真实 tracked golden 字节不变
    assert decoy.read_bytes() == decoy_before
    assert _REAL_M3.read_bytes() == real_before
    assert rebaseline.normalized_sha256(_REAL_M3) == _FROZEN_M3_SHA256
    assert all(report["checks"].values()), report["checks"]
    assert json.loads(out.read_text(encoding="utf-8"))["result"] == "applied"


def test_dry_run_after_apply_is_idempotent(
    rebaseline: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """apply 之后在同一副本 dry-run → 0 diff；``--fail-on-diff`` 在污染副本上 exit 1。"""
    golden_dir = _copy_golden_dir(tmp_path)
    target = golden_dir / "m3_golden.json"
    _perturb_m3(target)
    backup_dir = tmp_path / "backup"
    rebaseline.run_rebaseline(
        golden="m3",
        mode="apply",
        golden_dir=golden_dir,
        confirm="m3",
        backup_dir=backup_dir,
    )
    report = rebaseline.run_rebaseline(golden="m3", mode="dry-run", golden_dir=golden_dir)
    assert report["diff_field_count"] == 0
    assert report["result"] == "no-diff"

    # 0 diff 时 apply 是安全 no-op：不写文件、不产生新备份（避免无意义改动）
    noop = rebaseline.run_rebaseline(
        golden="m3", mode="apply", golden_dir=golden_dir, confirm="m3", backup_dir=backup_dir
    )
    assert noop["applied"] is False
    assert noop["result"] == "no-diff"
    assert noop["backup"] is None and noop["write"] is None
    assert sorted(path.name for path in backup_dir.iterdir()) == [
        "m3_golden.json.bak",
        "m3_golden.json.bak.sha256",
    ]

    # 重新污染后，CLI --fail-on-diff 应把「有差异」暴露为退出码 1（供 CI/发布门禁使用）
    _perturb_m3(target)
    assert (
        rebaseline.main(
            ["--golden", "m3", "--dry-run", "--fail-on-diff", "--golden-dir", str(golden_dir)]
        )
        == 1
    )
    capsys.readouterr()
