"""OSS-1/OSS-2 终端可玩入口：一条命令进入 2G 文字 MUD 循环。

设计约束（团队裁决 B″）：
- **零新增依赖**：只用已锁定的 ``fastapi``/``httpx``（``TestClient``）与标准库，不在运行时起端口、
  不访问网络；进程内装配 ``textgame.main`` 组合根。
- **播放器视角**：只打印叙事屏文本与服务端只读视图（HP/背包/位置）；不显示 JSON、``game_id``、
  版本号、幂等键等协议字段（第 2 章「失败转成世界内说法」口径）。
- **不伪造数据**：状态行只消费 ``GET /api/v1/games/{id}/view``（并行任务提供的只读契约）；
  端点暂不可用时显示降级提示，绝不编造 HP/背包/位置。
- **OSS-2 安全包**（R1 玩家实测 P0/P1；零文案改动、零 golden 重钉）：``--scenario`` 切换
  m2/m3/m4 场景；默认自动继续同库最近存档（``--new`` 显式新开、``--continue`` 强制继续）；
  首屏按只读视图给「可做动作」引导（真实数据，不编造剧情）；失败回合渲染编排层提供的
  ``suggested_actions``/``effects``（字段缺失/为空时保持原输出，16 字失败文案原样保留）。

模块结构：``GameSession`` 封装回合推进（内部维护版本链与幂等键，不对外暴露）；
``run_check``/``run_interactive`` 分别承载自动验收与交互循环；``build_app`` 负责进程内装配；
``latest_saved_game_id``/``prepare_session`` 负责同库存档发现与继续/新开。
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sqlite3
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

#: 已支持场景（``POST /api/v1/games`` 的 ``scenario`` 取值；缺省 m2 保持 M2 契约）。
SUPPORTED_SCENARIOS: tuple[str, ...] = ("m2", "m3", "m4")

#: SQLite DSN 前缀；``:memory:`` / ``file:`` 等 URI 形式不做存档发现（降级为新开）。
_SQLITE_URL_PREFIX = "sqlite:///"

#: 同库「最近存档」查询：``game_state_current`` 是第 14 章 14.4 的当前状态缓存表
#: （``persistence/tables.py``），每次成功提交刷新 ``updated_at``；表缺失/空库时 read 失败，
#: 一律降级为「没有可继续的存档」，不影响新开。
_LATEST_GAME_SQL = "SELECT game_id FROM game_state_current ORDER BY updated_at DESC LIMIT 1"

#: 方向英文枚举值 → 中文（首屏引导用；与叙事口径一致）。
_DIRECTION_LABELS: dict[str, str] = {
    "north": "北",
    "south": "南",
    "east": "东",
    "west": "西",
    "up": "上",
    "down": "下",
}

#: 首屏引导的固定命令提示（不编造剧情，只列真实可输入的命令）。
_GUIDE_COMMANDS = "输入 :help 查看全部命令 ｜ :inv 背包 ｜ :status 状态 ｜ :quit 退出"

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


def _safe_player_text(text: object) -> str:
    """把编排层提示安全化为玩家可见文本：空/非字符串/含协议字段 → 空串（不显示）。"""
    if not isinstance(text, str):
        return ""
    stripped = text.strip()
    if not stripped:
        return ""
    lowered = stripped.lower()
    if any(term in lowered for term in FORBIDDEN_OUTPUT_TERMS):
        return ""
    return stripped


def _collect_result_items(result: Any, field: str) -> list[Any]:
    """容错读取 ``TurnResult`` 的列表字段（dict 或对象；缺字段/类型不对 → 空列表）。"""
    raw = result.get(field) if isinstance(result, dict) else getattr(result, field, None)
    if isinstance(raw, (list, tuple)):
        return list(raw)
    return []


def _failure_detail_lines(result: Any) -> list[str]:
    """失败回合的附加提示：``suggested_actions`` 一行 + ``effects`` 内 hint/message。

    仅渲染玩家可读文本；``reason`` 类审计内部诊断不显示（避免英文/内部词泄漏）。
    字段缺失或为空时不输出任何行——16 字失败文案与原有输出保持不变。
    """
    lines: list[str] = []
    suggestions = [
        safe
        for safe in (
            _safe_player_text(item) for item in _collect_result_items(result, "suggested_actions")
        )
        if safe
    ]
    if suggestions:
        lines.append("可以试试：" + " / ".join(suggestions[:5]))
    for effect in _collect_result_items(result, "effects"):
        if not isinstance(effect, dict):
            continue
        for key in ("hint", "message", "text"):
            hint = _safe_player_text(effect.get(key))
            if hint:
                lines.append(f"提示：{hint}")
                break
    return lines


def _sqlite_path(db_url: str) -> Path | None:
    """从 ``sqlite:///`` DSN 解析出已存在的数据库文件路径；非 SQLite/内存库/文件缺失 → None。"""
    if not db_url.startswith(_SQLITE_URL_PREFIX):
        return None
    raw = db_url[len(_SQLITE_URL_PREFIX) :]
    if not raw or raw.startswith(":memory:") or raw.startswith("file:"):
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve() if path.is_file() else None


def latest_saved_game_id(db_url: str) -> str | None:
    """返回同库最近一次落盘的存档 id；不可发现时返回 ``None``（不抛错、不伪造存档）。

    直接只读查询持久化层的当前状态缓存表（第 14 章 14.4；见 ``persistence/tables.py``），
    不依赖不存在的「存档列表」HTTP 端点；SQLite 是单机唯一存储，进程内读取不产生写操作。
    """
    path = _sqlite_path(db_url)
    if path is None:
        return None
    try:
        connection = sqlite3.connect(str(path))
    except sqlite3.Error:
        return None
    try:
        row = connection.execute(_LATEST_GAME_SQL).fetchone()
    except sqlite3.Error:
        return None
    finally:
        connection.close()
    game_id = row[0] if row is not None else None
    return game_id if isinstance(game_id, str) and game_id else None


def _configured_db_url() -> str:
    """读取当前配置的数据库 DSN（``None`` 装配路径下的存档发现用）。"""
    from textgame.config.settings import get_settings

    return get_settings().db_url


def scene_guidance(view: dict[str, Any] | None) -> list[str]:
    """首屏引导：只列只读视图里真实可做的事情（不编造剧情/目标）。"""
    actions: list[str] = []
    room = view.get("room") if isinstance(view, dict) else None
    if isinstance(room, dict):
        exits = room.get("exits")
        if isinstance(exits, dict) and exits:
            direction = str(next(iter(exits)))
            actions.append(f"往{_DIRECTION_LABELS.get(direction, direction)}走")
        items = room.get("items")
        if isinstance(items, list):
            for item in items:
                name = item.get("name") if isinstance(item, dict) else None
                if isinstance(name, str) and name:
                    actions.append(f"拿起{name}")
                    break
        npcs = room.get("npcs")
        if isinstance(npcs, list):
            for npc in npcs:
                name = npc.get("name") if isinstance(npc, dict) else None
                if isinstance(name, str) and name:
                    actions.append(f"对{name}问好")
                    break
    actions.append("查看背包")
    lines = ["【引导】可以试试：" + " / ".join(actions)]
    lines.append("【提示】" + _GUIDE_COMMANDS)
    return lines


def status_line(view: dict[str, Any] | None) -> str:
    """把只读视图渲染成一行状态（HP/位置/行囊）；视图缺失时返回降级提示。

    ``inventory`` 若带 A4 加法字段 ``qty``（同模板聚合数量），按「铁锭×2」展示，避免
    重复名；``qty`` 缺省/为 1 时保持原有「名字」列表输出（与 OSS-1 一致）。
    """
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
                qty = item.get("qty")
                if isinstance(qty, int) and qty > 1:
                    names.append(f"{item['name']}×{qty}")
                else:
                    names.append(item["name"])
    hp_part = f"HP {hp}/{max_hp}" if isinstance(hp, int) and isinstance(max_hp, int) else "HP 未知"
    room_part = f"位置 {title}" if isinstance(title, str) else "位置未知"
    bag_part = "行囊：" + "、".join(names) if names else "行囊：空"
    return f"【状态】{hp_part} ｜ {room_part} ｜ {bag_part}"


def render_turn(result: dict[str, Any], view: dict[str, Any] | None) -> str:
    """渲染一个回合：服务端叙事文本（+ 失败提示）+ 状态行（不写进叙事正文）。

    失败回合在固定「（这一回合没有生效）」之后追加编排层提供的玩家提示
    （``suggested_actions``/``effects``）；两字段缺失或为空时输出与 OSS-1 完全一致。
    """
    text = result.get("text")
    body = text if isinstance(text, str) else ""
    lines: list[str] = []
    if body:
        lines.append(body)
    if result.get("ok") is not True:
        lines.append(FAILED_HINT)
        lines.extend(_failure_detail_lines(result))
    lines.append(status_line(view))
    return "\n".join(lines)


class GameSession:
    """一次终端对局的回合封装：内部维护游戏标识、版本链与幂等键，不对外打印。"""

    def __init__(self, client: TestClient, *, seed: int = 42, scenario: str = "m2") -> None:
        self._client = client
        self._seed = seed
        self._scenario = scenario
        self._game_id = ""
        self._expected_version = 0
        self._turns = 0

    @property
    def turns(self) -> int:
        """已推进的回合数（不含自动开局/继续前的历史回合数）。"""
        return self._turns

    def create(self) -> None:
        """新建存档（显式携带场景；默认 m2 与 OSS-1 行为一致）。"""
        response = self._client.post(
            "/api/v1/games",
            headers={"Idempotency-Key": _new_key("create")},
            json={"seed": self._seed, "scenario": self._scenario},
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
        self._turns = 0

    def resume(self, game_id: str) -> dict[str, Any]:
        """恢复已有存档：用 ``GET /api/v1/games/{id}`` 取回版本链，后续回合直接续写。"""
        response = self._client.get(f"/api/v1/games/{game_id}")
        if response.status_code != 200:
            raise ConsoleError("无法继续上次存档：游戏引擎找不到这个存档。")
        payload: dict[str, Any] = response.json()
        resolved_id = payload.get("game_id")
        version = payload.get("version")
        if not isinstance(resolved_id, str) or not isinstance(version, int):
            raise ConsoleError("无法继续上次存档：游戏引擎返回了无法识别的存档信息。")
        self._game_id = resolved_id
        self._expected_version = version
        turn = payload.get("turn")
        self._turns = turn - 1 if isinstance(turn, int) and turn > 1 else 0
        return payload

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


def _resume_notice(session: GameSession, game_id: str) -> str:
    """继续存档并返回首屏提示；存档损坏/缺失时降级为新开（入口不整体失败）。"""
    try:
        payload = session.resume(game_id)
    except ConsoleError:
        session.create()
        return "【存档】上次存档无法继续，已新开一局。"
    turn = payload.get("turn")
    played = turn - 1 if isinstance(turn, int) and turn > 1 else 0
    return f"【存档】已继续上次存档（进度：第 {played} 回合）。想重新开始请用 --new。"


def prepare_session(
    session: GameSession,
    *,
    db_url: str,
    continue_save: bool = False,
    new_game: bool = False,
) -> str:
    """决定「继续同库最近存档」还是「新开一局」，返回首屏提示（不打印协议字段）。

    - 默认：同库有存档 → 自动继续（避免每次启动丢进度）；无存档 → 新开。
    - ``--continue``：强制尝试继续；没有可继续存档时明确提示后新开。
    - ``--new``：强制新开（与 ``--continue`` 同给时以 ``--new`` 为准）。
    """
    if new_game:
        session.create()
        return "【存档】已按 --new 新开一局。"
    latest = latest_saved_game_id(db_url)
    if continue_save and latest is None:
        session.create()
        return "【存档】没有找到可继续的存档，已新开一局。"
    if latest is not None:
        return _resume_notice(session, latest)
    session.create()
    return "【存档】已新开一局。"


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
    "存档：默认自动继续同库最近存档；新开用 --new，强制继续用 --continue ｜ 场景：--scenario m2|m3|m4",
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
    start_notice: str | None = None,
) -> None:
    """交互循环：自然语言输入直接推进回合；``:help/:look/:inv/:status/:quit`` 为本地命令。

    ``start_notice`` 为首屏「存档：继续/新开」提示（默认不打印，保持原有输出）；
    首屏环顾之后按只读视图给一行「当前可做」引导（真实数据，不编造剧情）。
    """
    out("=== 文字交互游戏 · 终端可玩入口（2G 文字 MUD）===")
    out("")
    _print_help(out)
    out("")
    if start_notice:
        out(start_notice)
        out("")
    _show_turn(session, "环顾四周", out)
    guidance = scene_guidance(session.view())
    if guidance:
        out("\n".join(guidance))
        out("")
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


def _run_interactive_app(
    db_path: Path | None,
    seed: int,
    *,
    scenario: str = "m2",
    continue_save: bool = False,
    new_game: bool = False,
) -> int:
    """交互入口：装配组合根 → 决定继续/新开 → 进入回合循环。"""
    db_url = (
        f"sqlite:///{db_path.resolve().as_posix()}" if db_path is not None else _configured_db_url()
    )
    app = build_app(db_url if db_path is not None else None)
    try:
        with TestClient(app) as client:
            session = GameSession(client, seed=seed, scenario=scenario)
            notice = prepare_session(
                session,
                db_url=db_url,
                continue_save=continue_save,
                new_game=new_game,
            )
            run_interactive(session, start_notice=notice)
    finally:
        dispose_app_engine(app)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    """构建命令行解析器（``--check`` 固定 m2 自检；交互模式支持场景与存档选择）。"""
    parser = argparse.ArgumentParser(
        prog="python -m textgame.console",
        description="2G 文字 MUD 终端可玩入口（默认离线确定性，零新增依赖）",
    )
    parser.add_argument(
        "--check", action="store_true", help="脚本化 4 回合自检后退出（固定 m2，输出与 OSS-1 一致）"
    )
    parser.add_argument(
        "--db", type=Path, default=None, help="SQLite 存档路径（默认 data/textgame.db）"
    )
    parser.add_argument("--seed", type=int, default=42, help="新存档随机种子（默认 42）")
    parser.add_argument(
        "--scenario",
        choices=SUPPORTED_SCENARIOS,
        default="m2",
        help="新开存档的场景：m2 单场景 / m3 多角色 / m4 制造（默认 m2）",
    )
    parser.add_argument(
        "--continue",
        "--resume",
        dest="continue_save",
        action="store_true",
        help="强制继续同库最近存档（默认已有存档时也会自动继续）",
    )
    parser.add_argument(
        "--new",
        dest="new_game",
        action="store_true",
        help="强制新开一局（不继续已有存档）",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """终端入口：``python -m textgame.console [--check] [--db PATH] [--seed N]
    [--scenario m2|m3|m4] [--continue|--resume] [--new]``。"""
    args = _build_parser().parse_args(argv)
    ensure_utf8_stdout()
    try:
        if args.check:
            return _run_check_app()
        return _run_interactive_app(
            args.db,
            int(args.seed),
            scenario=str(args.scenario),
            continue_save=bool(args.continue_save),
            new_game=bool(args.new_game),
        )
    except ConsoleError as exc:
        print(f"终端入口未通过：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
