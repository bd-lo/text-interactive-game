"""单元测试：提交前密钥扫描脚本的阻断/放行行为（M1 环境追加项，不联网）。"""

import subprocess
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_secrets.py"


def _run_scan(*files: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *(str(path) for path in files)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def test_secret_scan_blocks_fake_secret(tmp_path: Path) -> None:
    """疑似密钥形态（运行时拼接，源码本身不构成命中）必须阻断并脱敏。"""
    fake = tmp_path / "fake.txt"
    fake.write_text("config: sk-" + "0123456789abcdef" * 2 + "\n", encoding="utf-8")

    result = _run_scan(fake)

    assert result.returncode == 1
    assert "[BLOCKED]" in result.stdout
    assert "sk-0123456789abcdef" not in result.stdout


def test_secret_scan_passes_clean_file(tmp_path: Path) -> None:
    """干净文件返回 0，不误伤文档说明文字。"""
    clean = tmp_path / "clean.txt"
    clean.write_text("TEXTGAME_LLM_API_KEY=<在 .env 中填入，不入库>\n", encoding="utf-8")

    result = _run_scan(clean)

    assert result.returncode == 0
    assert result.stdout == ""
