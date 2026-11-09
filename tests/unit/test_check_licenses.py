"""单元测试：CI 许可证门禁脚本（M1-004，不联网、不新增依赖）。

覆盖：干净清单放行、GPL/AGPL/LGPL 阻断、UNKNOWN/空许可证阻断、
``--ignore-packages`` 生效、非法 JSON 与缺失文件返回 2。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_licenses.py"

# 本项目环境实测会出现的宽松许可证写法（pip-licenses 直接读取各包元数据/正文）。
_ALLOWED_LICENSES = [
    "MIT",
    "MIT License",
    "Apache-2.0",
    "Apache Software License",
    "BSD-3-Clause",
    "BSD-2-Clause",
    "BSD License",
    "ISC",
    "ISC License (ISCL)",
    "PSF-2.0",
    "Python Software Foundation License",
    "MPL-2.0",
    "Mozilla Public License 2.0 (MPL 2.0)",
    "Apache-2.0 AND MIT",
    "Apache-2.0 OR MIT",
    "Apache-2.0 OR BSD-2-Clause",
    "Apache-2.0 AND CNRI-Python",
    "MPL-2.0 AND MIT",
    "MPL-2.0 AND (Apache-2.0 OR MIT)",
    "MIT AND PSF-2.0",
    "Apache Software License; MIT License",
]

# 个别包会把整段 MIT 正文塞进 License 字段：仍应放行。
_MIT_FULL_TEXT = """MIT License

Copyright (c) 2022 OpenAI, Shantanu Jain

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
"""


def _write_licenses(tmp_path: Path, records: list[dict[str, str]]) -> Path:
    path = tmp_path / "licenses.json"
    path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    return path


def _run_gate(*args: str, stdin_text: str | None = None) -> subprocess.CompletedProcess[str]:
    """以子进程运行脚本；PYTHONIOENCODING 固定 UTF-8，避免终端代码页干扰断言。"""
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        input=stdin_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        check=False,
    )


def test_clean_manifest_passes(tmp_path: Path) -> None:
    """全部为实测宽松许可证写法时放行，exit 0。"""
    records = [
        {"Name": f"pkg-{index}", "Version": "1.0.0", "License": license_text}
        for index, license_text in enumerate(_ALLOWED_LICENSES)
    ]
    records.append({"Name": "openai-like", "Version": "0.1.0", "License": _MIT_FULL_TEXT})

    result = _run_gate(str(_write_licenses(tmp_path, records)))

    assert result.returncode == 0, result.stdout + result.stderr
    assert "[OK]" in result.stdout
    assert "[BLOCKED]" not in result.stdout


def test_project_package_mit_license_passes(tmp_path: Path) -> None:
    """项目自身已声明 MIT（R2 关闭）：门禁默认不再豁免 textgame，且应放行。"""
    records = [
        {"Name": "textgame", "Version": "0.1.0", "License": "MIT"},
        {"Name": "fastapi", "Version": "0.162.2", "License": "MIT"},
    ]

    result = _run_gate(str(_write_licenses(tmp_path, records)))

    assert result.returncode == 0, result.stdout + result.stderr
    assert "[OK]" in result.stdout


def test_project_package_unknown_license_blocks_by_default(tmp_path: Path) -> None:
    """默认无豁免：项目自身许可证元数据缺失/UNKNOWN 也必须阻断。"""
    records = [
        {"Name": "textgame", "Version": "0.1.0", "License": "UNKNOWN"},
        {"Name": "fastapi", "Version": "0.162.2", "License": "MIT"},
    ]

    result = _run_gate(str(_write_licenses(tmp_path, records)))

    assert result.returncode == 1, result.stdout + result.stderr
    assert "textgame" in result.stdout


def test_stdin_input_passes() -> None:
    """省略文件参数时读取 stdin 上的 pip-licenses JSON。"""
    payload = json.dumps([{"Name": "fastapi", "Version": "0.162.2", "License": "MIT"}])

    result = _run_gate(stdin_text=payload)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "[OK]" in result.stdout


def test_gpl_package_blocks(tmp_path: Path) -> None:
    """GPL-3.0 出现即阻断，exit 1，且输出包名与许可证。"""
    records = [
        {"Name": "fastapi", "Version": "0.162.2", "License": "MIT"},
        {"Name": "evil-gpl-lib", "Version": "9.9.9", "License": "GPL-3.0-only"},
    ]

    result = _run_gate(str(_write_licenses(tmp_path, records)))

    assert result.returncode == 1, result.stdout + result.stderr
    assert "[BLOCKED]" in result.stdout
    assert "evil-gpl-lib" in result.stdout
    assert "GPL-3.0-only" in result.stdout
    assert "[OK]" not in result.stdout


@pytest.mark.parametrize("license_text", ["AGPL-3.0-only", "LGPL-2.1-or-later", "GPL-2.0"])
def test_agpl_lgpl_packages_block(tmp_path: Path, license_text: str) -> None:
    """AGPL/LGPL 同样阻断（GPL 子串覆盖写法）。"""
    records = [{"Name": "copyleft-lib", "Version": "2.0.0", "License": license_text}]

    result = _run_gate(str(_write_licenses(tmp_path, records)))

    assert result.returncode == 1, result.stdout + result.stderr
    assert "[BLOCKED]" in result.stdout
    assert "copyleft-lib" in result.stdout


def test_dual_license_with_gpl_blocks(tmp_path: Path) -> None:
    """双许可（MIT OR GPL）不放行：护栏是 GPL 出现即阻断。"""
    records = [{"Name": "dual-lib", "Version": "1.0.0", "License": "MIT OR GPL-3.0-only"}]

    result = _run_gate(str(_write_licenses(tmp_path, records)))

    assert result.returncode == 1, result.stdout + result.stderr
    assert "dual-lib" in result.stdout


def test_unknown_and_empty_license_block(tmp_path: Path) -> None:
    """UNKNOWN / 空 License 无法证明合规，一律阻断并列出包名。"""
    records = [
        {"Name": "mystery-lib", "Version": "1.0.0", "License": "UNKNOWN"},
        {"Name": "empty-license-lib", "Version": "1.0.0", "License": ""},
        {"Name": "noassertion-lib", "Version": "1.0.0", "License": "NOASSERTION"},
    ]

    result = _run_gate(str(_write_licenses(tmp_path, records)))

    assert result.returncode == 1, result.stdout + result.stderr
    assert "[BLOCKED]" in result.stdout
    for name in ("mystery-lib", "empty-license-lib", "noassertion-lib"):
        assert name in result.stdout


def test_ignore_packages_skips_gpl_package(tmp_path: Path) -> None:
    """--ignore-packages 命中的包不参与门禁（只用于已人工登记 allowlist 的例外）。"""
    records = [
        {"Name": "gpl-tool", "Version": "1.0.0", "License": "GPL-3.0-only"},
        {"Name": "fastapi", "Version": "0.162.2", "License": "MIT"},
    ]

    result = _run_gate(str(_write_licenses(tmp_path, records)), "--ignore-packages", "gpl-tool")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "[OK]" in result.stdout


def test_invalid_json_returns_2(tmp_path: Path) -> None:
    """非法 JSON 返回 2（输入错误，区别于许可证阻断的 1）。"""
    path = tmp_path / "broken.json"
    path.write_text("{ this is not json", encoding="utf-8")

    result = _run_gate(str(path))

    assert result.returncode == 2, result.stdout + result.stderr
    assert "[ERROR]" in result.stderr


def test_missing_file_returns_2(tmp_path: Path) -> None:
    """文件不存在返回 2，不产出阻断结论。"""
    missing = tmp_path / "not-there.json"

    result = _run_gate(str(missing))

    assert result.returncode == 2, result.stdout + result.stderr
    assert "[ERROR]" in result.stderr
