"""旧 golden fixture 出处指纹（provenance）门禁：改 fixture 必红。

背景（第 18 章 §18.3、01-PLAN §3 golden 红线）：
- `tests/fixtures/golden/m2_golden.json` / `m3_golden.json` / `m4_golden.json` 是**冻结基线**，
  除 Lead 批准的受控重基线外禁止修改，更禁止 `--snapshot-update` 掩盖回归；
- 本文件把 3 个 fixture 的 sha256 钉在代码里：任何人（含 `pytest` 自动重写、编辑器保存、
  脚本重生成）改动 golden 内容都会让本文件失败，从而把「静默改基线」变成一次必须显式
  修改 pin 的代码变更，进入 review 视野。

重基线流程（仅 Lead）：
1. 在独立 commit 中更新 golden fixture，并在 `docs/PROGRESS.md` 登记变更原因；
2. 同步更新本文件 `_GOLDEN_SHA256` 三个值（新 sha256 见失败信息）；
3. 重新跑 `uv run pytest tests/replay -q` 确认 61 例旧 golden + 本 provenance 全绿。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.replay]

_GOLDEN_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "golden"

#: 冻结基线指纹（2026-11-10 HEAD 33e620b；重基线时由 Lead 显式更新）。
#: 指纹按「LF 规范化后的内容」计算：Windows（core.autocrlf=true）检出为 CRLF、
#: Linux CI 检出为 LF，若按原始字节算哈希会在不同平台得出不同值（CI-only 失败）。
_GOLDEN_SHA256: dict[str, str] = {
    "m2_golden.json": "26f6408697615499325fe7b252ccd569a2faf135538eaf15ad8cef58a2a9f3d7",
    "m3_golden.json": "438d8bed1213ec5f0526a55e941e851bc2f6f0d3e06aea8d15b892433e48c48b",
    "m4_golden.json": "e8061724ab1cf6bcb39393542583b143b14e142048c9ca510bf4b9ae8da508a3",
}


def _sha256(path: Path) -> str:
    """按 LF 规范化后的 UTF-8 内容计算 sha256（跨 Windows/Linux 检出稳定）。"""
    normalized = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(normalized).hexdigest()


@pytest.mark.parametrize("filename", sorted(_GOLDEN_SHA256))
def test_golden_fixture_sha256_is_frozen(filename: str) -> None:
    """golden fixture 内容必须与冻结指纹一致：任何改动都会在这里失败。"""
    path = _GOLDEN_DIR / filename
    assert path.is_file(), f"golden fixture 缺失: {path}"
    actual = _sha256(path)
    expected = _GOLDEN_SHA256[filename]
    assert actual == expected, (
        f"{filename} 内容已变更：expected sha256={expected}，actual sha256={actual}。\n"
        "若确属 Lead 批准的受控重基线：① 在 docs/PROGRESS.md 登记原因；"
        "② 同步更新 tests/replay/test_golden_provenance.py 的 _GOLDEN_SHA256；"
        "③ 复跑 `uv run pytest tests/replay -q`。否则请 revert，禁止 --snapshot-update。"
    )


@pytest.mark.parametrize("filename", sorted(_GOLDEN_SHA256))
def test_golden_fixture_is_still_valid_json(filename: str) -> None:
    """指纹命中之外再做最小结构校验：损坏/截断的 fixture 必须立刻失败。"""
    document = json.loads((_GOLDEN_DIR / filename).read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    assert document.get("save_seed") == "save-42"
    assert isinstance(document.get("_comment"), str) and "禁止" in document["_comment"]


def test_golden_scripts_are_frozen() -> None:
    """脚本长度是回放基线的一部分：m2=7 回合、m3=10 回合、m4=5 case。"""
    m2 = json.loads((_GOLDEN_DIR / "m2_golden.json").read_text(encoding="utf-8"))
    m3 = json.loads((_GOLDEN_DIR / "m3_golden.json").read_text(encoding="utf-8"))
    m4 = json.loads((_GOLDEN_DIR / "m4_golden.json").read_text(encoding="utf-8"))
    assert len(m2["script"]) == len(m2["turns"]) == 7
    assert len(m3["script"]) == len(m3["turns"]) == 10
    assert len(m4["cases"]) == 5
