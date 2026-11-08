"""提交前密钥扫描：阻断疑似 API 密钥入库（第 15、20 章 R-L5）。

自建、只用标准库、不新增依赖；由 ``.pre-commit-config.yaml`` 的 local hook 调用，
参数为 staged 文本文件路径。只在密钥形态明确时阻断（``sk-`` + 长串），避免误伤
文档中的说明文字与 ``.env.example`` 占位。

用法::

    uv run python scripts/check_secrets.py [files...]

不带参数时扫描 ``git ls-files`` 的全部跟踪文件（人工审计用）。
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# DeepSeek / OpenAI 风格密钥：sk- 后接 20+ 个字母或数字（真实 key 为 32 位 hex）
_SECRET_PATTERN = re.compile(r"sk-[A-Za-z0-9]{20,}")
_MAX_BYTES = 2 * 1024 * 1024


def _mask(secret: str) -> str:
    """脱敏命中片段：保留 ``sk-`` 前缀，其余打码（不把密钥写进日志/终端）。"""
    return f"{secret[:3]}***"


def _iter_targets(raw_paths: list[str]) -> list[Path]:
    """把参数转成存在的文件；不存在的跳过（如被删除的 staged 文件）。"""
    return [path for raw in raw_paths if (path := Path(raw)).is_file()]


def _tracked_files() -> list[Path]:
    """无参数时返回 git 已跟踪文件（中文路径用 core.quotepath=false 保持原样）。"""
    try:
        completed = subprocess.run(
            ["git", "-c", "core.quotepath=false", "ls-files"],
            capture_output=True,
            check=False,
        )
    except OSError:
        return []
    if completed.returncode != 0:
        return []
    raw = completed.stdout.decode("utf-8", errors="replace").splitlines()
    return _iter_targets(raw)


def scan_file(path: Path) -> list[tuple[int, str]]:
    """返回 (行号, 脱敏后的命中片段)；非文本或超大文件跳过。"""
    try:
        if path.stat().st_size > _MAX_BYTES:
            return []
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    hits: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for match in _SECRET_PATTERN.finditer(line):
            hits.append((lineno, _mask(match.group(0))))
    return hits


def main(argv: list[str] | None = None) -> int:
    """扫描目标文件；发现疑似密钥返回 1（阻断提交），否则返回 0。"""
    parser = argparse.ArgumentParser(description="阻断疑似 API 密钥入库（pre-commit hook）")
    parser.add_argument("files", nargs="*", help="待扫描文件（由 pre-commit 传入）")
    args = parser.parse_args(argv)

    targets = _iter_targets(args.files) if args.files else _tracked_files()
    violations = 0
    for path in targets:
        for lineno, masked in scan_file(path):
            violations += 1
            print(
                f"{path}:{lineno}: 疑似 API 密钥（{masked}）"
                " —— 真实密钥只能放在 .env（已被 .gitignore 忽略）"
            )
    if violations:
        print(f"[BLOCKED] 发现 {violations} 处疑似密钥，提交已阻断（第 20 章 R-L5）")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
