"""单元测试：文档一致性门禁脚本 ``scripts/check_docs.py``（C-2，D-M5-9）。

用 ``tmp_path`` 构造最小仓库样例，覆盖：
1. 全部一致的样例 → 输出 ``PASS`` 且 exit 0；
2. 权威单文件与分章文件拼接不一致 → ``FAIL:`` 且 exit 1；
3. ``docs/progress/`` 有文档未在 PROGRESS §3 索引 → ``FAIL:``；
4. 头部「下一个可用编号」与 §4 表不一致 → ``FAIL:``；
5. 头部编号已被标记为「已用编号」→ ``FAIL:``；
6. 分章文件数不足（缺章）→ ``FAIL:``。

脚本仅标准库；测试以子进程方式执行（与 CI 同命令），显式 UTF-8 解码。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_docs.py"
_EXPECTED_CHAPTERS = 23


def _chapter_text(number: int) -> str:
    """构造与分章文件逐字一致的一章最小内容（含三节固定小节）。"""
    return (
        f"## 第 {number:02d} 章 测试章节{number}\n"
        "\n"
        "### 本章检索关键词\n"
        f"- keyword{number}\n"
        "\n"
        "### 实际打开确认的仓库列表\n"
        f"- repo{number}\n"
        "\n"
        "### 本章结论\n"
        f"- conclusion{number}"
    )


def _build_repo(
    root: Path,
    *,
    header_next: int = 21,
    row_next: int = 21,
    used: str = "019–020、022",
    progress_files: tuple[str, ...] = ("001-2026-11-10-test.md",),
    indexed_files: tuple[str, ...] | None = None,
    chapter_files: int = _EXPECTED_CHAPTERS,
    tamper_chapter: int | None = None,
) -> Path:
    """构造最小仓库：docs/开发文档.md + 分章 + progress + PROGRESS 总览。"""
    docs = root / "docs"
    chapter_dir = docs / "开发文档"
    progress_dir = docs / "progress"
    chapter_dir.mkdir(parents=True)
    progress_dir.mkdir(parents=True)

    chapters = [_chapter_text(number) for number in range(1, _EXPECTED_CHAPTERS + 1)]
    (docs / "开发文档.md").write_text(
        "# 测试开发文档\n\n## 阅读指南\n\n测试用。\n\n" + "\n\n".join(chapters) + "\n",
        encoding="utf-8",
    )
    for number, body in enumerate(chapters, start=1):
        if number > chapter_files:
            continue
        content = body
        if tamper_chapter == number:
            content = body + "\n- 分章文件多出的一行"
        (chapter_dir / f"第{number:02d}章-测试章节{number}.md").write_text(
            content + "\n", encoding="utf-8"
        )

    (progress_dir / "README.md").write_text("# 进度文档目录\n", encoding="utf-8")
    for name in progress_files:
        (progress_dir / name).write_text(f"# {name}\n\n内容。\n", encoding="utf-8")

    indexed = indexed_files if indexed_files is not None else progress_files
    index_rows = "\n".join(
        f"| {name[:3]} | 2026-11-10 | M5 | test | 测试 | [link](progress/{name}) |"
        for name in indexed
    )
    (docs / "PROGRESS.md").write_text(
        "# 总体进展总览（PROGRESS）\n"
        "\n"
        "> 统计基准日：2026-11-10 ｜ 当前里程碑：M5 ｜ "
        f"下一个可用编号：**{header_next:03d}**（测试样例）\n"
        "\n"
        "## 3. 单次进度文档索引\n"
        "\n"
        "| 编号 | 日期 | 里程碑 | 主题 | 一句话结论 | 链接 |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        f"{index_rows}\n"
        "\n"
        "## 4. 编号分配与变更记录\n"
        "\n"
        "| 里程碑 | 区间 | 已用编号 | 下一个可用 | 备注 |\n"
        "| --- | --- | --- | --- | --- |\n"
        f"| M5 | 019–024 | {used} | {row_next:03d} | 测试 |\n"
        "\n"
        "## 5. 接口偏离登记\n"
        "\n"
        "| 编号 |\n"
        "| --- |\n",
        encoding="utf-8",
    )
    return root


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    """以子进程执行门禁脚本（与 CI 同命令；显式 UTF-8）。"""
    return subprocess.run(
        [sys.executable, str(_SCRIPT), "--root", str(root)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def test_clean_repo_prints_pass(tmp_path: Path) -> None:
    """全部一致的样例：输出 PASS 且退出码 0（含多篇进度文档均被索引）。"""
    root = _build_repo(
        tmp_path,
        progress_files=("001-2026-11-10-test.md", "002-2026-11-10-another.md"),
    )

    result = _run(root)

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "PASS"


def test_chapter_mismatch_fails(tmp_path: Path) -> None:
    """分章文件与权威单文件不一致：FAIL 且退出码 1。"""
    root = _build_repo(tmp_path, tamper_chapter=7)

    result = _run(root)

    assert result.returncode == 1
    assert "FAIL:" in result.stdout
    assert "拼接不一致" in result.stdout
    assert "第 07 章" in result.stdout


def test_missing_progress_index_fails(tmp_path: Path) -> None:
    """docs/progress 有文档未登记到 PROGRESS §3：FAIL。"""
    root = _build_repo(
        tmp_path,
        progress_files=("001-2026-11-10-test.md", "002-2026-11-10-unindexed.md"),
        indexed_files=("001-2026-11-10-test.md",),
    )

    result = _run(root)

    assert result.returncode == 1
    assert "FAIL:" in result.stdout
    assert "未在 PROGRESS §3 索引" in result.stdout
    assert "002-2026-11-10-unindexed.md" in result.stdout


def test_next_number_mismatch_fails(tmp_path: Path) -> None:
    """头部「下一个可用编号」与 §4 行「下一个可用」不一致：FAIL。"""
    root = _build_repo(tmp_path, header_next=22, row_next=21)

    result = _run(root)

    assert result.returncode == 1
    assert "FAIL:" in result.stdout
    assert "不一致" in result.stdout
    assert "022" in result.stdout


def test_next_number_already_used_fails(tmp_path: Path) -> None:
    """头部编号已被 §4「已用编号」占用：FAIL。"""
    root = _build_repo(tmp_path, header_next=21, row_next=21, used="019–021、022")

    result = _run(root)

    assert result.returncode == 1
    assert "FAIL:" in result.stdout
    assert "已用" in result.stdout


def test_missing_chapter_file_fails(tmp_path: Path) -> None:
    """分章文件数不足（少一章）：FAIL。"""
    root = _build_repo(tmp_path, chapter_files=_EXPECTED_CHAPTERS - 1)

    result = _run(root)

    assert result.returncode == 1
    assert "FAIL:" in result.stdout
    assert "分章文件数" in result.stdout
