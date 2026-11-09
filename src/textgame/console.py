"""OSS-1 终端可玩入口：一条命令进入 2G 文字 MUD 循环。

设计约束（团队裁决 B″）：
- **零新增依赖**：只用已锁定的 ``fastapi``/``httpx``（``TestClient``）与标准库，不在运行时起端口、
  不访问网络；进程内装配 ``textgame.main`` 组合根。
- **播放器视角**：只打印叙事屏文本与服务端只读视图（HP/背包/位置）；不显示 JSON、``game_id``、
  版本号、幂等键等协议字段（第 2 章「失败转成世界内说法」口径）。
- **不伪造数据**：状态行只消费 ``GET /api/v1/games/{id}/view``（并行任务提供的只读契约）；
  端点暂不可用时显示降级提示，绝不编造 HP/背包/位置。

模块结构：``GameSession`` 封装回合推进（内部维护版本链与幂等键，不对外暴露）；
``run_check``/``run_interactive`` 分别承载自动验收与交互循环；``build_app`` 负责进程内装配。
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
import tempfile
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

#: 只读对局视图端点（并行任务交付；404 时降级）。
VIEW_PATH_TEMPLATE = "/api/v1/games/{game_id}/view"

#: 状态视图暂不可用时的降级提示（不伪造 HP/背包/位置）。
STATE_UNAVAILABLE = "（状态视图暂不可用：等只读视图就绪后这里会显示 HP/背包/位置）"

#: 失败回合后的固定提示（只说明「没有生效」，不编造原因）。
FAILED_HINT = "（这一回合没有生效）"

#: 终端输出禁止出现的协议内部字段（小写匹配）。
FORBIDDEN_OUTPUT_TERMS: tuple[str, ...] = (
    "json",
    "game_id",
    "version",
    "idempotency-key",
    "idempotency",
)

_CHECK_SCRIPT: tuple[str, ...] = ("环顾四周", "查看背包", "使用火把", "向北走进地窖")


class ConsoleError(RuntimeError):
    """终端入口对外错误：消息仅含玩家可读文字，不携带协议内部字段。"""


def _new_key(purpose: str) -> str:
    """生成写操作的幂等键（仅内部使用，不打印）。"""
    return f"console-{purpose}-{uuid.uuid4().hex}"


def dispose_app_engine(app: FastAPI) -> None:
    """释放组合根内 SQLite Engine 的文件句柄（进程内装配/临时库收尾用）。"""
    orchestrator = getattr(app.state, "orchestrator", None)
    repo = getattr(orchestrator, "_repo", None)
    engine = getattr(repo, "_engine", None)
    dispose = getattr(engine, "dispose", None)
    if callable(dispose):
        dispose()


def build_app(db_url: str | None = None) -> FastAPI:
    """进程内装配生产组合根；``db_url`` 非空时覆盖 ``TEXTGAME_DB_URL`` 后重建。

    ``db_url`` 为空时直接复用模块级 ``textgame.main.app``（遵循 ``.env``/环境变量默认值）。
    """
    if db_url is not None:
        os.environ["TEXTGAME_DB_URL"] = db_url
    from textgame.config.settings import get_settings

    get_settings.cache_clear()
    import textgame.main as main_module

    if db_url is None:
        return main_module.app
    dispose_app_engine(main_module.app)
    return main_module._build_app()


def ensure_utf8_stdout() -> None:
    """把标准输出切到 UTF-8：Windows 控制台/管道下中文屏文本不乱码。"""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        with contextlib.suppress(LookupError, ValueError, OSError):
            reconfigure(encoding="utf-8")


def assert_clean_output(text: str) -> None:
    """断言终端输出不含协议内部字段；违反时抛 :class:`ConsoleError`。"""
    lowered = text.lower()
    for term in FORBIDDEN_OUTPUT_TERMS:
        if term in lowered:
            raise ConsoleError("终端输出包含协议内部字段，自检失败")


def status_line(view: dict[str, Any] | None) -> str:
    """把只读视图渲染成一行状态（HP/位置/行囊）；视图缺失时返回降级提示。"""
    if not view:
        return STATE_UNAVAILABLE
    hp = view.get("hp")
    max_hp = view.get("max_hp")
    room = view.get("room")
    title = room.get("title") if isinstance(room, dict) else None
    names: list[str] = []
    inventory = view.get("inventory")
    if isinstance(inventory, list):
        for item in inventory:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                names.append(item["name"])
    hp_part = f"HP {hp}/{max_hp}" if isinstance(hp, int) and isinstance(max_hp, int) else "HP 未知"
    room_part = f"位置 {title}" if isinstance(title, str) else "位置未知"
    bag_part = "行囊：" + "、".join(names) if names else "行囊：空"
    return f"【状态】{hp_part} ｜ {room_part} ｜ {bag_part}"


def render_turn(result: dict[str, Any], view: dict[str, Any] | None) -> str:
    """渲染一个回合：服务端叙事文本（+ 失败提示）+ 状态行（不写进叙事正文）。"""
    text = result.get("text")
    body = text if isinstance(text, str) else ""
    lines: list[str] = []
    if body:
        lines.append(body)
    if result.get("ok") is not True:
        lines.append(FAILED_HINT)
    lines.append(status_line(view))
    return "\n".join(lines)


class GameSession:
    """一次终端对局的回合封装：内部维护游戏标识、版本链与幂等键，不对外打印。"""

    def __init__(self, client: TestClient, *, seed: int = 42) -> None:
        self._client = client
        self._seed = seed
        self._game_id = ""
        self._expected_version = 0
        self._turns = 0

    @property
    def turns(self) -> int:
        """已推进的回合数（不含自动开局）。"""
        return self._turns

    def create(self) -> None:
        """新建存档（固定 seed，默认离线确定性路径）。"""
        response = self._client.post(
            "/api/v1/games",
            headers={"Idempotency-Key": _new_key("create")},
            json={"seed": self._seed},
        )
        if response.status_code != 201:
            raise ConsoleError("无法创建存档：游戏引擎没有正常启动")
        payload: dict[str, Any] = response.json()
        game_id = payload.get("game_id")
        version = payload.get("version")
        if not isinstance(game_id, str) or not isinstance(version, int):
            raise ConsoleError("无法创建存档：游戏引擎返回了无法识别的开局信息")
        self._game_id = game_id
        self._expected_version = version

    def _refresh_version(self) -> bool:
        """从只读视图/存档读取当前版本（版本链恢复用，不对外打印）。"""
        response = self._client.get(f"/api/v1/games/{self._game_id}")
        if response.status_code != 200:
            return False
        payload: dict[str, Any] = response.json()
        version = payload.get("version")
        if not isinstance(version, int):
            return False
        self._expected_version = version
        return True

    def act(self, text: str) -> dict[str, Any]:
        """提交一次自然语言行动；遇到版本冲突时刷新版本重试一次。"""
        self._turns += 1
        result = self._post_turn(text)
        if result.get("ok") is not True and self._refresh_version():
            result = self._post_turn(text)
        version = result.get("version")
        if isinstance(version, int):
            self._expected_version = version
        return result

    def _post_turn(self, text: str) -> dict[str, Any]:
        response = self._client.post(
            "/api/v1/turns",
            headers={"Idempotency-Key": _new_key("turn")},
            json={
                "game_id": self._game_id,
                "input": text,
                "expected_version": self._expected_version,
            },
        )
        if response.status_code != 200:
            raise ConsoleError("这一回合没有送达：游戏引擎返回了异常")
        payload: dict[str, Any] = response.json()
        return payload

    def view(self) -> dict[str, Any] | None:
        """读取只读对局视图；端点暂不可用时返回 ``None``（不伪造数据）。"""
        response = self._client.get(VIEW_PATH_TEMPLATE.format(game_id=self._game_id))
        if response.status_code != 200:
            return None
        payload: dict[str, Any] = response.json()
        return payload if isinstance(payload, dict) else None


def run_check(client: TestClient, *, out: Callable[[str], None] = print) -> int:
    """脚本化 4 回合自检（含 1 个失败样本）；通过时返回 0，失败抛 ``ConsoleError``。"""
    session = GameSession(client)
    session.create()
    results: list[dict[str, Any]] = []
    rendered: list[str] = []
    for index, text in enumerate(_CHECK_SCRIPT, start=1):
        result = session.act(text)
        view = session.view()
        block = render_turn(result, view)
        out(f"第 {index} 回合 输入：{text}")
        out(block)
        out("")
        results.append(result)
        rendered.append(block)
    ok_flags = [result.get("ok") is True for result in results]
    success_texts = [
        text
        for result, flag in zip(results, ok_flags, strict=False)
        if flag
        for text in [result.get("text")]
        if isinstance(text, str)
    ]
    if ok_flags != [True, True, False, True]:
        raise ConsoleError("终端自检未通过：回合成功/失败样本与预期不符")
    for success_text in success_texts:
        if not 80 <= len(success_text) <= 150:
            raise ConsoleError("终端自检未通过：成功回合叙事屏不在 80–150 字口径内")
    assert_clean_output("\n".join(rendered))
    out("自检通过：4/4 回合（含失败样本），终端输出不含协议内部字段")
    return 0


_HELP_LINES: tuple[str, ...] = (
    "直接输入自然语言行动，例如：环顾四周 / 拿起火把 / 向北走进地窖",
    "命令：:help 帮助 ｜ :look 环顾四周 ｜ :inv 查看行囊 ｜ :status 状态行 ｜ :quit 退出",
    "当前为默认离线确定性叙事（不联网、无需 API Key）",
)


def _print_help(out: Callable[[str], None]) -> None:
    for line in _HELP_LINES:
        out(line)


def _show_turn(session: GameSession, text: str, out: Callable[[str], None]) -> None:
    result = session.act(text)
    out(render_turn(result, session.view()))
    out("")


def _show_inventory(session: GameSession, out: Callable[[str], None]) -> None:
    view = session.view()
    if not view:
        out("（行囊视图暂不可用：只读对局视图就绪后会自动显示）")
        return
    names: list[str] = []
    inventory = view.get("inventory")
    if isinstance(inventory, list):
        for item in inventory:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                names.append(item["name"])
    out("行囊：" + ("、".join(names) if names else "空"))
    out("")


def run_interactive(
    session: GameSession,
    *,
    read_line: Callable[[str], str] = input,
    out: Callable[[str], None] = print,
) -> None:
    """交互循环：自然语言输入直接推进回合；``:help/:look/:inv/:status/:quit`` 为本地命令。"""
    out("=== 文字交互游戏 · 终端可玩入口（2G 文字 MUD）===")
    out("")
    _print_help(out)
    out("")
    _show_turn(session, "环顾四周", out)
    while True:
        try:
            line = read_line("> ")
        except (EOFError, KeyboardInterrupt):
            out("")
            out("再见。")
            return
        command = line.strip()
        if not command:
            continue
        if command in {":quit", ":q"}:
            out("再见。")
            return
        if command == ":help":
            _print_help(out)
            continue
        if command == ":look":
            _show_turn(session, "环顾四周", out)
            continue
        if command == ":inv":
            _show_inventory(session, out)
            continue
        if command == ":status":
            out(status_line(session.view()))
            out("")
            continue
        if command.startswith(":"):
            out("未知命令；输入 :help 查看可用命令。")
            continue
        _show_turn(session, command, out)


def _run_check_app() -> int:
    """自检用临时库装配（不写 ``data/``）。"""
    with tempfile.TemporaryDirectory(prefix="textgame-console-") as tmp:
        db_path = Path(tmp) / "check.db"
        app = build_app(f"sqlite:///{db_path.as_posix()}")
        try:
            with TestClient(app) as client:
                return run_check(client)
        finally:
            dispose_app_engine(app)


def _run_interactive_app(db_path: Path | None, seed: int) -> int:
    db_url = f"sqlite:///{db_path.resolve().as_posix()}" if db_path is not None else None
    app = build_app(db_url)
    try:
        with TestClient(app) as client:
            session = GameSession(client, seed=seed)
            session.create()
            run_interactive(session)
    finally:
        dispose_app_engine(app)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """终端入口：``python -m textgame.console [--check] [--db PATH] [--seed N]``。"""
    parser = argparse.ArgumentParser(
        prog="python -m textgame.console",
        description="2G 文字 MUD 终端可玩入口（默认离线确定性，零新增依赖）",
    )
    parser.add_argument("--check", action="store_true", help="脚本化 4 回合自检后退出")
    parser.add_argument(
        "--db", type=Path, default=None, help="SQLite 存档路径（默认 data/textgame.db）"
    )
    parser.add_argument("--seed", type=int, default=42, help="新存档随机种子（默认 42）")
    args = parser.parse_args(argv)
    ensure_utf8_stdout()
    try:
        if args.check:
            return _run_check_app()
        return _run_interactive_app(args.db, int(args.seed))
    except ConsoleError as exc:
        print(f"终端入口未通过：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
