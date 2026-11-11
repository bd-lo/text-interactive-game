"""OSS-1 确定性七屏演示：``python -m textgame.demo`` / ``--check``。

- 固定 ``seed=42`` 跑 README「M2 快速试玩」的 7 回合脚本，默认离线、进程内装配、不占端口、不联网。
- ``--check`` 断言：7/7 回合成功、每屏 80–150 字、屏字数与 golden 精确一致、两次运行逐字一致
  （确定性），并且输出不含协议内部字段。
- ``--markdown --out examples/m2-demo.md`` 由实跑输出生成示例文档，不手写。
"""

from __future__ import annotations

import argparse
import os
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from textgame.console import (
    ConsoleError,
    GameSession,
    assert_clean_output,
    build_app,
    dispose_app_engine,
    ensure_utf8_stdout,
    render_turn,
)

#: README「M2 快速试玩」固定脚本（seed=42）。
WALKTHROUGH: tuple[str, ...] = (
    "环顾四周",
    "对老哨兵问好",
    "拿起火把",
    "拿起治疗药水",
    "使用治疗药水",
    "向北走进地窖",
    "拿起锈剑",
)

#: golden（tests/fixtures/golden/m2_golden.json）逐回合屏字数；改动本值必须同步回放基线。
EXPECTED_TEXT_LEN: tuple[int, ...] = (114, 83, 81, 85, 82, 84, 81)

#: 成功回合的屏字数硬口径（第 2 章 §2.3）。
MIN_TEXT_LEN = 80
MAX_TEXT_LEN = 150


def run_walkthrough(client: TestClient, *, seed: int = 42) -> list[dict[str, Any]]:
    """在给定进程内 client 上跑完整 7 回合，返回逐回合记录（内部版本号不打印）。"""
    session = GameSession(client, seed=seed)
    session.create()
    records: list[dict[str, Any]] = []
    for text in WALKTHROUGH:
        result = session.act(text)
        block = render_turn(result, session.view())
        screen = result.get("text")
        version = result.get("version")
        records.append(
            {
                "input": text,
                "text": screen if isinstance(screen, str) else "",
                "text_len": len(screen) if isinstance(screen, str) else 0,
                "ok": result.get("ok") is True,
                "version": version if isinstance(version, int) else -1,
                "block": block,
            }
        )
    return records


def verify_walkthrough(records: list[dict[str, Any]]) -> None:
    """校验 7 屏成功、字数口径、golden 字数与版本链；不通过抛 ``ConsoleError``。"""
    if len(records) != len(WALKTHROUGH):
        raise ConsoleError("演示未通过：回合数不是 7")
    if any(record.get("ok") is not True for record in records):
        raise ConsoleError("演示未通过：存在未成功的回合")
    lengths = [record.get("text_len") for record in records]
    if lengths != list(EXPECTED_TEXT_LEN):
        raise ConsoleError("演示未通过：屏字数与 golden 不一致")
    texts = [record.get("text") for record in records]
    for text in texts:
        if not isinstance(text, str) or not MIN_TEXT_LEN <= len(text) <= MAX_TEXT_LEN:
            raise ConsoleError("演示未通过：屏字数不在 80–150 字口径内")
    versions = [record.get("version") for record in records]
    if versions != list(range(1, len(WALKTHROUGH) + 1)):
        raise ConsoleError("演示未通过：回合版本链不连续")
    assert_clean_output("\n".join(str(record.get("block", "")) for record in records))


def format_transcript(records: list[dict[str, Any]]) -> str:
    """把记录格式化为玩家可读的纯文本 7 屏（含输入与屏字数）。"""
    lines: list[str] = []
    for index, record in enumerate(records, start=1):
        lines.append(
            f"第 {index} 回合（输入：{record.get('input', '')}，{record.get('text_len', 0)} 字）"
        )
        lines.append(str(record.get("block", "")))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _walkthrough_once(seed: int) -> list[dict[str, Any]]:
    """临时库上跑一次完整演示并释放引擎（不写 ``data/``；强制离线保证 golden 可复现）。"""
    # 演示是确定性 golden 产物（屏字数逐字比对）：即使用户 .env 配了开发 key 也必须离线模板。
    os.environ["TEXTGAME_LLM_ENABLED"] = "false"
    with tempfile.TemporaryDirectory(prefix="textgame-demo-") as tmp:
        db_path = Path(tmp) / "demo.db"
        app = build_app(f"sqlite:///{db_path.as_posix()}")
        try:
            with TestClient(app) as client:
                return run_walkthrough(client, seed=seed)
        finally:
            dispose_app_engine(app)


def render_markdown(records: list[dict[str, Any]]) -> str:
    """把实跑记录渲染为示例文档（``examples/m2-demo.md`` 的唯一生成来源）。"""
    lines: list[str] = [
        "# M2 七回合终端演示（seed=42，默认离线确定性路径）",
        "",
        "> 本文件由**实跑**生成，请勿手改：",
        "> `uv run python -m textgame.demo --markdown --out examples/m2-demo.md`",
        "> 默认离线：不需要 API Key、Node/pnpm 或 curl；每屏 80–150 字，状态行来自只读对局视图。",
        "",
        "## 一、预期长度清单（与 README 试玩实测 81–114 字一致）",
        "",
        "| 回合 | 输入 | 屏字数 |",
        "| --- | --- | --- |",
    ]
    for index, record in enumerate(records, start=1):
        lines.append(f"| {index} | {record.get('input', '')} | {record.get('text_len', 0)} |")
    lines.extend(["", "## 二、实跑输出（本机生成，逐字可复现）", ""])
    for index, record in enumerate(records, start=1):
        lines.append(
            f"### 第 {index} 回合：{record.get('input', '')}（{record.get('text_len', 0)} 字）"
        )
        lines.extend(["", "```text", str(record.get("block", "")), "```", ""])
    lines.extend(
        [
            "## 三、复现与自动验收",
            "",
            "```powershell",
            "uv run python -m textgame.demo           # 打印 7 屏",
            "uv run python -m textgame.demo --check   # 断言 7/7、字数、确定性后 exit 0",
            "uv run pytest -q tests/integration/test_console_walkthrough.py",
            "```",
            "",
            "同一 seed 与同一输入下两次运行逐字一致（`--check` 会跑两遍比对）；"
            "上游 golden 基线见 `tests/fixtures/golden/`（m2 golden）。",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """演示入口：``python -m textgame.demo [--check] [--markdown [--out PATH]] [--seed N]``。"""
    parser = argparse.ArgumentParser(
        prog="python -m textgame.demo",
        description="2G 文字 MUD 七屏确定性演示（默认离线、零新增依赖）",
    )
    parser.add_argument("--check", action="store_true", help="断言 7/7、字数、确定性后 exit 0")
    parser.add_argument("--markdown", action="store_true", help="输出示例文档（由本次实跑生成）")
    parser.add_argument("--out", type=Path, default=None, help="配合 --markdown 写入文件")
    parser.add_argument("--seed", type=int, default=42, help="随机种子（默认 42）")
    args = parser.parse_args(argv)
    ensure_utf8_stdout()
    try:
        if args.check:
            first = _walkthrough_once(int(args.seed))
            verify_walkthrough(first)
            second = _walkthrough_once(int(args.seed))
            verify_walkthrough(second)
            if [record["text"] for record in first] != [record["text"] for record in second]:
                raise ConsoleError("演示未通过：两次运行屏文本不一致（确定性被破坏）")
            print(format_transcript(first), end="")
            print("自检通过：7/7 回合、屏字数与确定性断言全部通过（输出不含协议内部字段）")
            return 0
        records = _walkthrough_once(int(args.seed))
        if args.markdown:
            document = render_markdown(records)
            if args.out is not None:
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(document, encoding="utf-8")
                print(f"已生成示例文档：{args.out}")
            else:
                print(document, end="")
            return 0
        print("=== 文字交互游戏 · 七屏确定性演示（默认离线，无需 API Key）===")
        print()
        print(format_transcript(records), end="")
        return 0
    except ConsoleError as exc:
        print(f"演示未通过：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
