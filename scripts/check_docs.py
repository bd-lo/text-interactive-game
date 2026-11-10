#!/usr/bin/env python3
"""文档一致性门禁（C-2，落地 D-M5-9 到期承诺）。

可本地执行、可进 CI；**仅标准库**，路径与 IO 显式 UTF-8（Windows/非 ASCII 路径可用）。

校验项（任一失败打印 ``FAIL: ...`` 并以退出码 1 结束；全部通过打印 ``PASS``）：

1. 权威单文件与分章文件逐章一致
   - ``docs/开发文档.md`` 的 ``## 第 N 章 ...`` 章节块，与 ``docs/开发文档/第NN章-*.md``
     按章号升序逐章 ``rstrip`` 后**逐字节一致**（搬自 ``.work/status-2026-11-09/rebuild_doc.py``
     的 verify 逻辑，不依赖 ``.work``）；
   - 章号恰为 1..23、章文件数一致、代码围栏成对、每章三节固定小节存在。
2. ``docs/progress/*.md``（``README.md`` 除外）均出现在 ``docs/PROGRESS.md`` §3 索引中。
3. ``docs/PROGRESS.md`` 头部「下一个可用编号」与 §4 编号分配表一致：
   该编号落在 §4 某里程碑区间内、等于该行「下一个可用」且**未被该行「已用编号」占用**；
   当 001–028 全部交付（路线图里程碑完成）时，头部允许写「无」，此时 §4 所有行的
   「下一个可用」都不得再留可用编号（避免出现「声明无号可用、表里却还有空位」的漂移）。

用法::

    uv run python scripts/check_docs.py            # 在仓库根执行
    uv run python scripts/check_docs.py --root .   # 显式指定仓库根（测试用）

退出码：0 = PASS；1 = 存在 FAIL。
"""

from __future__ import annotations

import argparse
import contextlib
import re
import sys
from pathlib import Path

#: 开发文档章节数（第 1–23 章；与 docs/开发文档.md 一致）
EXPECTED_CHAPTERS = 23
#: 拼接比较时视为行尾的字符（与 rebuild_doc.py 的 NL 一致）
NL = "\n"
#: 每章必须存在的固定小节（第 22 章/rebuild_doc.py 口径）
REQUIRED_CHAPTER_SECTIONS = (
    "### 本章检索关键词",
    "### 实际打开确认的仓库列表",
    "### 本章结论",
)

_CHAPTER_HEADING_RE = re.compile(r"^## 第 (\d+) 章 (.+)$", re.M)
_CHAPTER_FILE_RE = re.compile(r"^第(\d+)章-.*\.md$")
_NEXT_NUMBER_RE = re.compile(r"下一个可用编号：\s*\**\s*(\d{3}|无)")
_SECTION_3_RE = re.compile(r"^## 3\..*?$(.*?)(?=^## 4\. )", re.M | re.S)
_SECTION_4_RE = re.compile(r"^## 4\..*?$(.*?)(?=^## 5\. )", re.M | re.S)
_RANGE_RE = re.compile(r"(\d{3})\s*[–—\-~]\s*(\d{3})")


def _ensure_utf8_stdout() -> None:
    """Windows 管道/控制台下中文输出不乱码。"""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        with contextlib.suppress(LookupError, ValueError, OSError):
            reconfigure(encoding="utf-8")


def _read(path: Path) -> str:
    """读 UTF-8 文本（BOM 兼容：utf-8-sig 仅剥离 BOM，不改变内容）。"""
    return path.read_text(encoding="utf-8-sig")


def _split_chapters(text: str) -> list[tuple[int, str]]:
    """按 ``## 第 N 章 `` 切块，返回 ``[(章号, 章节块文本)]``。"""
    matches = list(_CHAPTER_HEADING_RE.finditer(text))
    chapters: list[tuple[int, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        chapters.append((int(match.group(1)), text[match.start() : end]))
    return chapters


def check_chapter_sync(root: Path) -> list[str]:
    """校验 ①：权威单文件 ↔ 分章文件一致。"""
    problems: list[str] = []
    doc = root / "docs" / "开发文档.md"
    chapter_dir = root / "docs" / "开发文档"
    if not doc.is_file():
        return [f"缺少权威开发文档: {doc}"]
    if not chapter_dir.is_dir():
        return [f"缺少分章目录: {chapter_dir}"]

    text = _read(doc)
    chapters = _split_chapters(text)
    numbers = [number for number, _ in chapters]
    if numbers != list(range(1, EXPECTED_CHAPTERS + 1)):
        problems.append(f"权威文档章号不是 1..{EXPECTED_CHAPTERS}: {numbers}")

    files = [path for path in chapter_dir.glob("第*章-*.md") if _CHAPTER_FILE_RE.match(path.name)]
    files.sort(key=lambda path: int(_CHAPTER_FILE_RE.match(path.name).group(1)))  # type: ignore[union-attr]
    if len(files) != EXPECTED_CHAPTERS:
        problems.append(f"分章文件数 {len(files)} ≠ 期望 {EXPECTED_CHAPTERS}")
    if len(chapters) != len(files):
        problems.append(f"权威文档章节数 {len(chapters)} ≠ 分章文件数 {len(files)}")

    for (number, chunk), path in zip(chapters, files, strict=False):
        body = _read(path).rstrip(NL)
        if body != chunk.rstrip(NL):
            problems.append(f"第 {number:02d} 章拼接不一致: {path.name}")

    fences = text.count("`" * 3)
    if fences % 2:
        problems.append(f"代码围栏不成对: {fences} 个 ```")
    for number, chunk in chapters:
        for section in REQUIRED_CHAPTER_SECTIONS:
            if section not in chunk:
                problems.append(f"第 {number:02d} 章缺少固定小节: {section}")
    return problems


def check_progress_index(root: Path) -> list[str]:
    """校验 ②：docs/progress 下每篇（README 除外）都在 PROGRESS §3 索引。"""
    problems: list[str] = []
    progress_md = root / "docs" / "PROGRESS.md"
    progress_dir = root / "docs" / "progress"
    if not progress_md.is_file():
        return [f"缺少总览文档: {progress_md}"]
    if not progress_dir.is_dir():
        return [f"缺少进度文档目录: {progress_dir}"]
    match = _SECTION_3_RE.search(_read(progress_md))
    section = match.group(1) if match else ""
    if not section:
        return ["PROGRESS 缺少可解析的 §3 索引段"]
    for path in sorted(progress_dir.glob("*.md")):
        if path.name == "README.md":
            continue
        if path.name not in section:
            problems.append(f"进度文档未在 PROGRESS §3 索引: {path.name}")
    return problems


def _parse_section4_rows(text: str) -> list[dict[str, str]]:
    """解析 §4 编号分配表为行字典（里程碑/区间/已用编号/下一个可用）。"""
    match = _SECTION_4_RE.search(text)
    if not match:
        return []
    rows: list[dict[str, str]] = []
    for line in match.group(1).splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 4 or cells[0] in {"里程碑", ""}:
            continue
        if all(set(cell) <= set("-: ") for cell in cells):
            continue
        rows.append({"milestone": cells[0], "range": cells[1], "used": cells[2], "next": cells[3]})
    return rows


def _parse_milestone_range(cell: str) -> tuple[int, int] | None:
    match = _RANGE_RE.search(cell)
    return (int(match.group(1)), int(match.group(2))) if match else None


def _parse_used_numbers(cell: str) -> set[int]:
    """解析「已用编号」单元格：``019–020、022`` → {19,20,22}；``-`` → 空集。

    容错单元格里的中文注记（如 §4 的 ``「025–028（全部）」``、``「019–024（全部）」``）：
    区间用 ``search``、单号取前三位数字，注记不影响解析。
    """
    used: set[int] = set()
    for part in re.split(r"[、,，\s]+", cell):
        part = part.strip()
        if not part or part in {"-", "—", "–"}:
            continue
        range_match = _RANGE_RE.search(part)
        if range_match:
            used.update(range(int(range_match.group(1)), int(range_match.group(2)) + 1))
            continue
        single_match = re.match(r"(\d{3})", part)
        if single_match:
            used.add(int(single_match.group(1)))
    return used


def check_next_number(root: Path) -> list[str]:
    """校验 ③：PROGRESS 头部「下一个可用编号」与 §4 表一致。

    头部为「无」时（001–028 全部交付）：要求 §4 所有行的「下一个可用」不再留可用编号。
    """
    progress_md = root / "docs" / "PROGRESS.md"
    if not progress_md.is_file():
        return [f"缺少总览文档: {progress_md}"]
    text = _read(progress_md)
    header = _NEXT_NUMBER_RE.search(text)
    if not header:
        return ["PROGRESS 头部缺少「下一个可用编号：NNN / 无」"]
    rows = _parse_section4_rows(text)
    if not rows:
        return ["PROGRESS §4 编号分配表缺失或无法解析"]
    if header.group(1) == "无":
        problems: list[str] = []
        # 「可用编号」跨里程碑共享：只有未被任何 §4 行「已用编号」占用的数字才算剩余空位
        # （例如 M4 行「下一个可用 019」在 019 已被 M5 使用后不再是可用编号）。
        all_used: set[int] = set()
        for row in rows:
            all_used |= _parse_used_numbers(row["used"])
        for row in rows:
            leftover = {int(number) for number in re.findall(r"\d{3}", row["next"])} - all_used
            if leftover:
                problems.append(
                    f"头部声明无可用编号，但 §4 {row['milestone']} 行仍有可用编号 "
                    f"{[f'{number:03d}' for number in sorted(leftover)]}"
                )
        return problems
    next_number = int(header.group(1))
    matched = [
        row
        for row in rows
        if (milestone_range := _parse_milestone_range(row["range"]))
        and milestone_range[0] <= next_number <= milestone_range[1]
    ]
    if not matched:
        return [f"头部下一个可用编号 {next_number:03d} 不在 §4 任何里程碑区间内"]
    row = matched[0]
    row_next = re.search(r"(\d{3})", row["next"])
    problems: list[str] = []
    if row_next is None or int(row_next.group(1)) != next_number:
        problems.append(
            f"头部下一个可用编号 {next_number:03d} 与 §4 {row['milestone']} 行「下一个可用」不一致"
        )
    if next_number in _parse_used_numbers(row["used"]):
        problems.append(
            f"头部下一个可用编号 {next_number:03d} 已被 §4 {row['milestone']} 行标记为已用"
        )
    return problems


def collect_problems(root: Path) -> list[str]:
    """执行全部校验，返回问题列表（空 = 通过）。"""
    problems: list[str] = []
    problems.extend(check_chapter_sync(root))
    problems.extend(check_progress_index(root))
    problems.extend(check_next_number(root))
    return problems


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：PASS → 0；FAIL → 打印 `FAIL: ...` 并返回 1。"""
    parser = argparse.ArgumentParser(description="文档一致性门禁（C-2）")
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="仓库根目录（默认取脚本所在仓库根）",
    )
    args = parser.parse_args(argv)
    _ensure_utf8_stdout()
    root = (args.root or Path(__file__).resolve().parent.parent).resolve()
    try:
        problems = collect_problems(root)
    except OSError as exc:  # pragma: no cover - 文件不可读等环境错误
        print(f"FAIL: 读取文档失败: {exc}")
        return 1
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
