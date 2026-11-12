"""OSS-1/OSS-2 终端可玩入口：一条命令进入 2G 文字 MUD 循环。

设计约束（团队裁决 B″）：
- **零新增依赖**：只用已锁定的 ``fastapi``/``httpx``（``TestClient``）与标准库，不在运行时起端口、
  不访问网络；进程内装配 ``textgame.main`` 组合根。
- **播放器视角**：只打印叙事屏文本与服务端只读视图（HP/背包/位置）；不显示 JSON、``game_id``、
  版本号、幂等键等协议字段（第 2 章「失败转成世界内说法」口径）。
- **不伪造数据**：状态行只消费 ``GET /api/v1/games/{id}/view``（并行任务提供的只读契约）；
  端点暂不可用时显示降级提示，绝不编造 HP/背包/位置。
- **OSS-2 安全包**（R1 玩家实测 P0/P1；零文案改动、零 golden 重钉）：``--scenario`` 切换
  m2/m3/m4/story 场景；默认自动继续同库最近存档（``--new`` 显式新开、``--continue`` 强制继续）；
  首屏按只读视图给「目标进度 + 可做动作」引导（真实数据，不编造剧情）；失败回合渲染编排层提供的
  ``suggested_actions``/``effects``（字段缺失/为空时保持原输出，16 字失败文案原样保留）。
- **R2 开源入口轨**：``story`` 剧情场景（首屏展示 ``/view.goal`` 标题 + 3 条子目标完成态与
  ``suggested_actions``；``status=ended`` 时打印 ``ending_text`` 结局屏并停止接收行动输入）；
  ``--list`` 列出同库最近存档；``--db`` 支持 ``sqlite:///`` DSN 与中文报错；``ensure_utf8_stdio``
  同时修复管道输入中文的编码崩溃；显式 ``--scenario`` 与旧存档不一致时提示 ``--new``/``--continue``。
- **R3 打磨（M6 卡库配套）**：``--card-id <卡ID>``（别名 ``--card``）按人物卡开局——进程内
  ``POST /api/v1/games {card_id}``，总是新开一局（与 ``--scenario``/``--continue`` 互斥并有
  中文提示）；``--list-cards`` 只读列出卡库最近人物卡（``GET /api/v1/character-cards``，
  失败时玩家可读降级）。**无卡参数时旧 CLI 行为逐字不变**（含 ``--check``）。
- **N3-047 终端卡库浏览**：``--list-cards`` 补 ``--card-query``（标题/来源过滤）、``--card-limit``
  （1..100，默认 20）、``--card-all``（上限内全量，单次最多 100 张），展示标题/来源标签/更新
  时间/卡 ID 与可玩提示；``--card-detail <卡ID>`` 只读查看安全子集（标题/来源/属性摘要/背包
  提示/警告）。只读浏览不建局；默认开局路径与 ``--check`` 输出逐字不变。
- **N3-045 场景包批量导入/导出**：``--import-scenarios <目录>`` 批量导入目录下 ``*.json``
  场景（逐文件 ≤256KB/UTF-8/JSON 对象，走既有 ``POST /api/v1/scenarios``；幂等键由文件内容
  sha256 派生，重跑只回放不重复建档；逐文件 ``[ok]/[skip]/[error]`` 中文报告，部分失败
  退出码 1）；``--export-scenarios <目录>`` 只读导出场景库为 ``<slug>.json``（先列表再详情取
  原始定义，可直接再导入）。数量默认 50/上限 100（``--import-limit``/``--export-limit``）。

模块结构：``GameSession`` 封装回合推进（内部维护版本链与幂等键，不对外暴露）；
``run_check``/``run_interactive`` 分别承载自动验收与交互循环；``build_app`` 负责进程内装配；
``latest_saved_game_id``/``saved_scenario``/``prepare_session`` 负责同库存档发现、场景识别与
继续/新开决策。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sqlite3
import sys
import tempfile
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI
from fastapi.testclient import TestClient

from textgame import scenario_pack
from textgame.domain.character_card import resolve_item_template
from textgame.domain.scenario import (
    M3_NPC_SCAVENGER_A,
    M3_NPC_SCAVENGER_B,
    M4_ROOM_FORGE,
)
from textgame.domain.scenario_story import STORY_ENTRY_ROOM_ID

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
SUPPORTED_SCENARIOS: tuple[str, ...] = ("m2", "m3", "m4", "story")

#: 玩家上传场景（``scenario="custom"``）：终端用 ``--scenario-id <ID>`` 按场景开局。
#: 场景 JSON 的导入/浏览在网页版场景库完成；终端只做「按场景 ID 开局」。
CUSTOM_SCENARIO = "custom"

#: 场景 → 玩家可读名称（帮助、引导、存档列表用；story 为推荐首玩剧情）。
SCENARIO_LABELS: dict[str, str] = {
    "m2": "m2 · 废弃哨站（单场景）",
    "m3": "m3 · 多角色冲突",
    "m4": "m4 · 制造",
    "story": "story · 余烬哨站（剧情，推荐）",
    "custom": "custom · 玩家上传场景",
}

#: 结局屏提示（status=ended 后不再接收行动输入，只允许退出/查看）。
ENDING_HINT = "故事结束，输入 :quit 退出。"

#: 人物卡来源 → 玩家可读标签（``--list-cards``/``--card-detail`` 展示用；未知来源给中性文案）。
_CARD_SOURCE_LABELS: dict[str, str] = {
    "upload": "导入",
    "llm": "LLM 生成",
    "manual": "手填/模板",
}

#: 卡库浏览：``--card-limit`` 合法范围与默认值（与后端列表端点 1..100 的上限一致）。
CARD_LIMIT_MIN = 1
CARD_LIMIT_MAX = 100
CARD_LIMIT_DEFAULT = 20

#: 属性键 → 中文短标签（``--card-detail`` 属性摘要展示顺序；与人物卡 7 键一致）。
_CARD_ATTRIBUTE_LABELS: dict[str, str] = {
    "str": "力量",
    "dex": "敏捷",
    "con": "体质",
    "int": "智力",
    "wis": "感知",
    "cha": "魅力",
}

#: ``updated_at`` ISO 文本 → 玩家可读「YYYY-MM-DD HH:MM」的前缀匹配（解析失败则不显示）。
_CARD_UPDATED_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})")

#: SQLite DSN 前缀；``:memory:`` / ``file:`` 等 URI 形式不做存档发现（降级为新开）。
_SQLITE_URL_PREFIX = "sqlite:///"

#: 同库「最近存档」查询：``game_state_current`` 是第 14 章 14.4 的当前状态缓存表
#: （``persistence/tables.py``），每次成功提交刷新 ``updated_at``；表缺失/空库时 read 失败，
#: 一律降级为「没有可继续的存档」，不影响新开。
_LATEST_GAME_SQL = "SELECT game_id FROM game_state_current ORDER BY updated_at DESC LIMIT 1"

#: 读取指定存档当前状态（只读）：用于从状态内固定 ID 推断存档场景（场景不落事件/快照标签）。
_GAME_STATE_SQL = "SELECT state_json FROM game_state_current WHERE game_id = ? LIMIT 1"

#: 场景识别标记：M3 状态含两名固定拾荒者；M4 状态含固定铸造间；其余（含人物卡开局 m2）按 m2。
_M3_ACTOR_IDS: tuple[str, ...] = (M3_NPC_SCAVENGER_A, M3_NPC_SCAVENGER_B)
_M4_ROOM_IDS: tuple[str, ...] = (M4_ROOM_FORGE,)

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

#: ``--list-scenarios`` 空库提示（QA P2-8）：面向玩家、不含 ``FORBIDDEN_OUTPUT_TERMS``
#: 里的协议内部字面（如 "JSON"），与终端其余输出口径一致。
EMPTY_SCENARIO_LIBRARY_TIPS: tuple[str, ...] = (
    "还没有上传的场景：先在网页版「场景库」导入一份场景。",
    "也可以直接用内置场景：--scenario m2|m3|m4|story",
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


def ensure_utf8_stdio() -> None:
    """把标准输入/输出切到 UTF-8：Windows 控制台与**管道/重定向**下中文不乱码、不崩。

    R2 修复：此前只 reconfigure ``stdout``，管道输入中文（脚本/CI/Git Bash）会按本地代码页
    GBK 解出代理字符，回合提交 JSON 编码时抛 ``UnicodeEncodeError``。stdin 用
    ``errors="replace"``（坏字节降级为替换符而不是崩溃），stdout/stderr 保持严格 UTF-8。
    """
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        kwargs: dict[str, str] = {"encoding": "utf-8"}
        if stream is sys.stdin:
            kwargs["errors"] = "replace"
        with contextlib.suppress(LookupError, ValueError, OSError):
            reconfigure(**kwargs)


def ensure_utf8_stdout() -> None:
    """兼容旧名（OSS-1 起内部调用）：等价于 :func:`ensure_utf8_stdio`。"""
    ensure_utf8_stdio()


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


def saved_scenario(db_url: str, game_id: str) -> str | None:
    """从同库当前状态推断最近存档的场景（``m2``/``m3``/``m4``/``story``）；不可用返回 ``None``。

    场景不是事件/快照里的独立字段，但各场景初始状态含**固定 ID**：M3 有两名拾荒者、
    story 有固定断桥入口、M4 有固定铸造间。只读查询 ``game_state_current``，不产生写操作；
    旧库/表缺失/JSON 损坏一律降级 ``None``（调用方按「不知道」处理，不阻塞开局）。
    """
    path = _sqlite_path(db_url)
    if path is None:
        return None
    try:
        connection = sqlite3.connect(str(path))
    except sqlite3.Error:
        return None
    try:
        row = connection.execute(_GAME_STATE_SQL, (game_id,)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        connection.close()
    if row is None or not isinstance(row[0], str):
        return None
    try:
        state = json.loads(row[0])
    except (TypeError, ValueError):
        return None
    if not isinstance(state, dict):
        return None
    characters = state.get("characters")
    rooms = state.get("rooms")
    if isinstance(characters, dict) and any(actor in characters for actor in _M3_ACTOR_IDS):
        return "m3"
    if isinstance(rooms, dict) and STORY_ENTRY_ROOM_ID in rooms:
        return "story"
    if isinstance(rooms, dict) and any(room in rooms for room in _M4_ROOM_IDS):
        return "m4"
    if isinstance(characters, dict) or isinstance(rooms, dict):
        return "m2"
    return None


def _parse_db_target(raw: str | None) -> Path | str | None:
    """``--db`` 参数解析：本地文件路径 → ``Path``；``sqlite:///`` DSN → ``str``。

    用户常把 ``TEXTGAME_DB_URL`` 的写法直接传给 ``--db``；此前会被当成本地路径拼进
    ``sqlite:///...`` 变成垃圾文件名。这里做自动转换 + 中文友好报错（不支持的 DSN、内存库）。
    """
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        raise ConsoleError("--db 不能为空：请给 SQLite 文件路径（如 data/textgame.db）")
    if value.startswith(_SQLITE_URL_PREFIX):
        rest = value[len(_SQLITE_URL_PREFIX) :]
        if not rest or rest.startswith(":memory:") or rest.startswith("file:"):
            raise ConsoleError(
                "--db 的内存库/URI 存档无法在重启后继续；请用文件路径（如 data/textgame.db）"
            )
        return value
    if "://" in value:
        scheme = value.split("://", 1)[0] or "未知"
        raise ConsoleError(
            f"--db 目前只支持 SQLite 文件路径或 sqlite:/// DSN（收到 {scheme}://…）；"
            "数据库连接串请改用环境变量 TEXTGAME_DB_URL"
        )
    return Path(value)


def goal_guidance(view: dict[str, Any] | None) -> list[str]:
    """渲染 ``/view.goal``（story 剧情目标）：标题 + 进度 + 每条子目标完成态。

    旧场景（m2/m3/m4）``goal=None`` → 返回空列表（输出与 OSS-1 逐字一致）。
    """
    goal = view.get("goal") if isinstance(view, dict) else None
    if not isinstance(goal, dict):
        return []
    objectives = goal.get("objectives")
    items = (
        [item for item in objectives if isinstance(item, dict)]
        if isinstance(objectives, list)
        else []
    )
    done = sum(1 for item in items if item.get("done") is True)
    title = goal.get("title") if isinstance(goal.get("title"), str) else ""
    if not title:
        return []
    lines = [f"【目标】{title} · {done}/{len(items)}"]
    for item in items:
        text = item.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        mark = "[x]" if item.get("done") is True else "[ ]"
        lines.append(f"  {mark} {text.strip()}")
    return lines


def scene_guidance(view: dict[str, Any] | None) -> list[str]:
    """首屏引导：目标进度 + 只读视图里真实可做的动作（不编造剧情/目标）。

    - story 场景（``/view.goal`` 非空）：先列目标与子目标完成态，再优先用服务端
      ``suggested_actions`` 生成「可以试试」；
    - 旧场景（m2/m3/m4）：保持 OSS-1/OSS-2 行为（按房间出口/物品/NPC 推导），输出逐字不变。
    """
    goal_lines = goal_guidance(view)
    suggestions = view.get("suggested_actions") if isinstance(view, dict) else None
    story_actions = (
        [text for text in (_safe_player_text(item) for item in suggestions or []) if text]
        if isinstance(suggestions, list)
        else []
    )
    if goal_lines and story_actions:
        lines = [*goal_lines, "【引导】可以试试：" + " / ".join(story_actions[:5])]
        lines.append("【提示】" + _GUIDE_COMMANDS)
        return lines
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
    lines = [*goal_lines, "【引导】可以试试：" + " / ".join(actions)]
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
    """一次终端对局的回合封装：内部维护游戏标识、版本链与幂等键，不对外打印。

    ``card_id`` 非空时按人物卡开局（``POST /api/v1/games {card_id}``，D-M6-2）：不再发送
    ``seed``/``scenario``（与后端互斥约束一致），其余回合逻辑与普通对局完全一致。
    ``scenario_id`` 非空时按玩家上传的场景开局（``POST /api/v1/games {scenario_id}``，
    与 ``card_id``/``scenario`` 互斥），同样总是新开一局。
    """

    def __init__(
        self,
        client: TestClient,
        *,
        seed: int = 42,
        scenario: str = "m2",
        card_id: str | None = None,
        scenario_id: str | None = None,
    ) -> None:
        """记录客户端与开局参数；``card_id``/``scenario_id`` 非空表示按卡/按上传场景开局。"""
        self._client = client
        self._seed = seed
        self._scenario = scenario
        self._card_id = card_id
        self._scenario_id = scenario_id
        self._game_id = ""
        self._expected_version = 0
        self._turns = 0

    @property
    def turns(self) -> int:
        """已推进的回合数（不含自动开局/继续前的历史回合数）。"""
        return self._turns

    def create(self) -> None:
        """新建存档（显式携带场景/人物卡/上传场景；默认 m2 与 OSS-1 行为一致）。"""
        payload: dict[str, Any]
        if self._card_id is not None:
            payload = {"card_id": self._card_id}
        elif self._scenario_id is not None:
            payload = {"seed": self._seed, "scenario_id": self._scenario_id}
        else:
            payload = {"seed": self._seed, "scenario": self._scenario}
        response = self._client.post(
            "/api/v1/games",
            headers={"Idempotency-Key": _new_key("create")},
            json=payload,
        )
        if response.status_code != 201:
            raise ConsoleError(self._create_failure_message(response.status_code))
        payload_data: dict[str, Any] = response.json()
        game_id = payload_data.get("game_id")
        version = payload_data.get("version")
        if not isinstance(game_id, str) or not isinstance(version, int):
            raise ConsoleError("无法创建存档：游戏引擎返回了无法识别的开局信息")
        self._game_id = game_id
        self._expected_version = version
        self._turns = 0

    def _create_failure_message(self, status_code: int) -> str:
        """开局失败 → 玩家可读中文（按卡/按场景/普通开局分别给原因，不泄漏协议字段）。"""
        if self._card_id is not None:
            if status_code == 404:
                return "无法按人物卡开局：卡库里找不到这张人物卡（可能已被删除）。"
            if status_code == 422:
                return "无法按人物卡开局：这张人物卡的数据不完整，暂时不能用来开始新的一局。"
            return "无法按人物卡开局：游戏引擎没有正常启动。"
        if self._scenario_id is not None:
            if status_code == 404:
                return "无法按上传场景开局：场景库里找不到这个场景（可能已被删除）。"
            if status_code == 422:
                return "无法按上传场景开局：这个场景文件没有通过校验。"
            return "无法按上传场景开局：游戏引擎没有正常启动。"
        return "无法创建存档：游戏引擎没有正常启动"

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
    requested_scenario: str | None = None,
    card_id: str | None = None,
    scenario_id: str | None = None,
) -> str:
    """决定「继续同库最近存档」还是「新开一局」，返回首屏提示（不打印协议字段）。

    - 默认：同库有存档 → 自动继续（避免每次启动丢进度）；无存档 → 新开。
    - ``--continue``：强制尝试继续；没有可继续存档时明确提示后新开。
    - ``--new``：强制新开（与 ``--continue`` 同给时以 ``--new`` 为准）。
    - ``requested_scenario``：**显式** ``--scenario`` 值（未显式给时为 ``None``）。与同库旧
      存档场景不一致时不再静默继续：``--continue`` 下继续旧档并提示换 ``--new``，否则按显式
      场景新开一局并说明旧存档保留（R2：避免「选了 m3 却在玩 m2」的困惑）。
    - ``card_id``：按人物卡开局（D-M6-2），**不自动继续旧档**；与显式场景/``--continue``
      的互斥校验在 CLI 入口完成（这里只负责建局）。
    - ``scenario_id``：按玩家上传场景开局（同样总是新开，不自动继续旧档）。
    """
    if card_id is not None:
        session.create()
        return "【存档】已按人物卡开局。"
    if scenario_id is not None:
        session.create()
        return "【存档】已按上传场景开局。"
    if new_game:
        session.create()
        return "【存档】已按 --new 新开一局。"
    latest = latest_saved_game_id(db_url)
    if continue_save and latest is None:
        session.create()
        return "【存档】没有找到可继续的存档，已新开一局。"
    if latest is not None:
        saved = saved_scenario(db_url, latest) if requested_scenario is not None else None
        if requested_scenario is not None and saved is not None and saved != requested_scenario:
            if continue_save:
                notice = _resume_notice(session, latest)
                return (
                    f"{notice}\n【提示】上次存档是 {saved} 场景，不是 --scenario "
                    f"{requested_scenario}；想按 {requested_scenario} 玩请用 --new。"
                )
            session.create()
            return (
                f"【存档】同库上次存档是 {saved} 场景，与 --scenario {requested_scenario} 不一致；"
                f"已按 {requested_scenario} 新开一局（旧存档保留，想继续旧档请用 --continue）。"
            )
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
    "场景：--scenario m2|m3|m4|story（story「余烬哨站」剧情，推荐首玩）",
    "上传场景：--scenario-id <场景ID> 按上传场景开局；--list-scenarios 列出已上传场景（场景文件在网页场景库导入）",
    "场景包：--import-scenarios <目录> 批量导入场景文件（--import-limit 数量，默认 50、上限 100）",
    "场景包：--export-scenarios <目录> 批量导出场景文件（--export-limit 数量，默认 50、上限 100）",
    "人物卡：--card-id <卡ID> 按人物卡开局；--list-cards 列出卡库（卡先在网页卡库生成/导入）",
    "卡库浏览：--list-cards 可加 --card-query 关键词 / --card-limit 数量 / --card-all；"
    "--card-detail <卡ID> 看单卡详情",
    "存档：默认自动继续同库最近存档；--new 新开，--continue 强制继续，--list 列出最近存档",
    "当前为默认离线确定性叙事（不联网、无需 API Key）",
)


def _print_help(out: Callable[[str], None], view: dict[str, Any] | None = None) -> None:
    """打印帮助；给出 ``/view`` 时追加剧情目标进度（story 场景）。"""
    for line in _HELP_LINES:
        out(line)
    for line in goal_guidance(view):
        out(line)


def ending_lines(view: dict[str, Any] | None) -> list[str] | None:
    """``/view.status == "ended"`` 时的结局屏文本；未结局返回 ``None``。

    结局正文来自服务端只读视图 ``ending_text``（不伪造），随后固定提示退出方式；
    旧场景（m2/m3/m4）恒 ``active``，输出与 OSS-1 一致。
    """
    if not isinstance(view, dict) or view.get("status") != "ended":
        return None
    lines: list[str] = []
    ending_text = view.get("ending_text")
    if isinstance(ending_text, str) and ending_text.strip():
        lines.append(ending_text.strip())
    lines.append(ENDING_HINT)
    return lines


def _print_ending(view: dict[str, Any] | None, out: Callable[[str], None]) -> bool:
    """打印结局屏（若已结局）并返回是否结束；未结束不打印任何内容。"""
    lines = ending_lines(view)
    if lines is None:
        return False
    for line in lines:
        out(line)
    out("")
    return True


def _show_turn(
    session: GameSession, text: str, out: Callable[[str], None]
) -> dict[str, Any] | None:
    """提交一个回合并渲染；返回本回合之后的只读视图（供目标/结局判断）。"""
    result = session.act(text)
    view = session.view()
    out(render_turn(result, view))
    out("")
    return view


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
    首屏环顾之后按只读视图给「目标进度 + 当前可做」引导（真实数据，不编造剧情）。
    story 场景走到结局（``status=ended``）时打印结局屏并停止接收行动输入（只允许 :quit 等）。
    """
    out("=== 文字交互游戏 · 终端可玩入口（2G 文字 MUD）===")
    out("")
    _print_help(out)
    out("")
    if start_notice:
        out(start_notice)
        out("")
    view = _show_turn(session, "环顾四周", out)
    ended = _print_ending(view, out)
    if not ended:
        guidance = scene_guidance(view)
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
            _print_help(out, view)
            continue
        if command == ":look":
            view = _show_turn(session, "环顾四周", out)
            ended = _print_ending(view, out) or ended
            continue
        if command == ":inv":
            _show_inventory(session, out)
            continue
        if command == ":status":
            out(status_line(session.view()))
            for goal_line in goal_guidance(session.view()):
                out(goal_line)
            out("")
            continue
        if command.startswith(":"):
            out("未知命令；输入 :help 查看可用命令。")
            continue
        if ended:
            out(ENDING_HINT)
            out("")
            continue
        view = _show_turn(session, command, out)
        if _print_ending(view, out):
            ended = True


def _run_check_app() -> int:
    """自检用临时库装配（不写 ``data/``；**强制离线**保证逐屏 golden 可复现）。"""
    # 自检是确定性产物（屏字数/golden 比对）：即使用户 .env 配了开发 key，也必须走离线模板路径。
    os.environ["TEXTGAME_LLM_ENABLED"] = "false"
    with tempfile.TemporaryDirectory(prefix="textgame-console-") as tmp:
        db_path = Path(tmp) / "check.db"
        app = build_app(f"sqlite:///{db_path.as_posix()}")
        try:
            with TestClient(app) as client:
                return run_check(client)
        finally:
            dispose_app_engine(app)


def _run_list_app(db_target: Path | str | None = None) -> int:
    """``--list``：列出同库最近存档（``GET /api/v1/games``，按最近更新倒序）后退出。

    只读：不建局、不写库；``--db`` 与交互模式同口径（文件路径 / sqlite:/// DSN）。
    旧后端缺该端点时降级为提示（不阻塞其它入口）。
    """
    if isinstance(db_target, str):
        db_url = db_target
    elif db_target is not None:
        db_url = f"sqlite:///{db_target.resolve().as_posix()}"
    else:
        db_url = _configured_db_url()
    app = build_app(db_url if db_target is not None else None)
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/games", params={"limit": 10})
            if response.status_code != 200:
                raise ConsoleError(
                    "存档列表暂不可用（后端未提供 /api/v1/games）；可先用 --continue 继续最近存档。"
                )
            payload = response.json()
        games = payload.get("games") if isinstance(payload, dict) else None
        rows = [row for row in games if isinstance(row, dict)] if isinstance(games, list) else []
        total = payload.get("total") if isinstance(payload, dict) else None
        if not rows:
            print("还没有存档：运行 .\\scripts\\play.ps1（或 python -m textgame.console）开一局。")
            return 0
        shown_total = total if isinstance(total, int) and total > 0 else len(rows)
        print(f"最近存档（共 {shown_total} 个，按最近更新排序；最多显示 10 个）：")
        for index, row in enumerate(rows, start=1):
            raw_scenario = row.get("scenario")
            scenario = raw_scenario if isinstance(raw_scenario, str) and raw_scenario else "m2"
            label = SCENARIO_LABELS.get(scenario, scenario)
            turn = row.get("turn")
            turn_part = f"第 {turn} 回合" if isinstance(turn, int) else "回合未知"
            status_part = "已结局" if row.get("status") == "ended" else "进行中"
            raw_game_id = row.get("game_id")
            short_id = raw_game_id[:8] if isinstance(raw_game_id, str) else "未知"
            print(f"{index}. [{label}] {turn_part} · {status_part} · 存档 {short_id}")
        print("")
        print("续玩最近存档：python -m textgame.console --continue")
        print("开新一局：python -m textgame.console --new --scenario story")
        return 0
    finally:
        dispose_app_engine(app)


def _readonly_db_url(db_target: Path | str | None) -> str:
    """``--db`` 目标 → 只读浏览用数据库 DSN（与交互/``--list`` 同口径）。"""
    if isinstance(db_target, str):
        return db_target
    if db_target is not None:
        return f"sqlite:///{db_target.resolve().as_posix()}"
    return _configured_db_url()


def _build_readonly_app(db_target: Path | str | None, unavailable: str) -> FastAPI:
    """装配只读浏览组合根；装配失败转成玩家可读提示（不显示内部异常细节）。"""
    try:
        return build_app(_readonly_db_url(db_target) if db_target is not None else None)
    except Exception as exc:  # 存档路径不可用/装配失败：玩家可读提示，不泄漏内部细节
        raise ConsoleError(unavailable) from exc


def _card_source_label(source: object) -> str:
    """人物卡来源 → 玩家可读标签（未知/缺失来源给中性「人物卡」）。"""
    return _CARD_SOURCE_LABELS.get(source, "人物卡") if isinstance(source, str) else "人物卡"


def _card_updated_text(value: object) -> str:
    """``updated_at`` ISO 文本 → 「YYYY-MM-DD HH:MM」；无法识别返回空串（该段不显示）。"""
    if not isinstance(value, str):
        return ""
    match = _CARD_UPDATED_RE.match(value.strip())
    if match is None:
        return ""
    return f"{match.group(1)} {match.group(2)}"


def _card_matches_query(row: dict[str, Any], keyword: str) -> bool:
    """``--card-query`` 客户端过滤：关键词匹配标题/来源标签/来源原值（大小写不敏感）。"""
    needle = keyword.casefold()
    if not needle:
        return True
    candidates = (row.get("name"), row.get("source"), _card_source_label(row.get("source")))
    return any(isinstance(value, str) and needle in value.casefold() for value in candidates)


def _card_detail_failure_message(status_code: int) -> str:
    """卡详情失败 → 玩家可读中文（404 不存在、422 ID 格式非法、其余暂不可用）。"""
    if status_code == 404:
        return "卡库里找不到这张人物卡（可能已经被删除了）。"
    if status_code == 422:
        return "人物卡 ID 格式不正确：请从 --list-cards 里复制完整卡 ID（以 card_ 开头）。"
    return "人物卡详情暂时打不开：可以先用 --list-cards 浏览卡库，或用网页版卡库查看。"


def _card_detail_lines(card: dict[str, Any], card_id: str) -> list[str]:
    """单卡详情 → 玩家可读安全子集（标题/来源/属性摘要/背包提示/警告）。

    只读预览：不输出 ``schema_version``/``generated_by`` 等契约字段，也不渲染 ``background``
    自由文本，终端保持玩家视角；卡片数据本身来自服务端 ``GET /character-cards/{id}``。
    """
    raw_name = card.get("name")
    name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else "未命名人物卡"
    lines: list[str] = [
        f"【人物卡】{name}",
        f"来源：{_card_source_label(card.get('source'))} · 卡 {card_id}",
    ]
    raw_attributes = card.get("attributes")
    attributes: dict[str, Any] = raw_attributes if isinstance(raw_attributes, dict) else {}
    hp = card.get("hp")
    max_hp = attributes.get("max_hp")
    if isinstance(hp, int) and isinstance(max_hp, int):
        lines.append(f"生命：HP {hp}/{max_hp}")
    elif isinstance(hp, int):
        lines.append(f"生命：HP {hp}")
    attribute_parts: list[str] = []
    for key, label in _CARD_ATTRIBUTE_LABELS.items():
        value = attributes.get(key)
        if isinstance(value, int):
            attribute_parts.append(f"{label} {value}")
    if attribute_parts:
        lines.append("属性：" + " ｜ ".join(attribute_parts))
    raw_items = card.get("starting_items")
    template_ids = (
        [item for item in raw_items if isinstance(item, str)] if isinstance(raw_items, list) else []
    )
    item_names: list[str] = []
    warnings: list[str] = []
    for template_id in template_ids:
        try:
            item_names.append(resolve_item_template(template_id).name)
        except KeyError:
            warnings.append("初始物品里有无法识别的内容，开局可能失败。")
    if item_names:
        lines.append("背包：" + "、".join(item_names) + f"（合计 {len(item_names)} 件）")
    else:
        lines.append("背包：没有初始物品（开局时行囊是空的）")
    if isinstance(hp, int) and isinstance(max_hp, int) and hp < max_hp:
        warnings.append(f"开局时生命值未满（HP {hp}/{max_hp}），会从这张卡的当前生命值开始。")
    if warnings:
        lines.extend(f"警告：{warning}" for warning in warnings)
    else:
        lines.append("警告：无（这张人物卡可以直接用来开局）")
    lines.append("")
    lines.append(f"按这张卡开局：python -m textgame.console --card-id {card_id}")
    return lines


def _validate_card_browse_args(args: argparse.Namespace) -> None:
    """卡库浏览参数校验：互斥/范围错误给玩家可读中文（main 统一转 exit 1）。"""
    browse_only = args.card_query is not None or args.card_all or args.card_limit is not None
    if args.card_detail is not None:
        conflicts = [
            flag
            for flag, given in (
                ("--list-cards", bool(args.list_cards)),
                ("--list", bool(args.list_games)),
                ("--list-scenarios", bool(args.list_scenarios)),
                ("--card-id", args.card_id is not None),
            )
            if given
        ]
        if conflicts:
            raise ConsoleError(
                f"--card-detail 不能与 {'/'.join(conflicts)} 同时使用："
                "看单卡详情就用 --card-detail <卡ID>。"
            )
        if browse_only:
            raise ConsoleError(
                "--card-query/--card-limit/--card-all 只能与 --list-cards 一起使用。"
            )
        return
    if browse_only and not args.list_cards:
        raise ConsoleError("--card-query/--card-limit/--card-all 只能与 --list-cards 一起使用。")
    if args.card_all and args.card_limit is not None:
        raise ConsoleError(
            "--card-all 与 --card-limit 不能同时使用：--card-all 表示显示上限内的全部人物卡。"
        )
    if args.card_query is not None and not str(args.card_query).strip():
        raise ConsoleError("--card-query 需要一个关键词：留空请直接去掉该参数查看全部人物卡。")
    if args.card_limit is not None and not (
        CARD_LIMIT_MIN <= int(args.card_limit) <= CARD_LIMIT_MAX
    ):
        raise ConsoleError(
            f"--card-limit 必须在 {CARD_LIMIT_MIN} 到 {CARD_LIMIT_MAX} 之间"
            f"（收到 {args.card_limit}）。"
        )


def _run_list_cards_app(
    db_target: Path | str | None = None,
    *,
    query: str | None = None,
    limit: int = CARD_LIMIT_DEFAULT,
    show_all: bool = False,
) -> int:
    """``--list-cards``：只读浏览人物卡库（关键词过滤/限量/上限内全量）后退出。

    只读：不建局、不写库；``--db`` 与交互/``--list`` 同口径。检索/全量时一次最多读
    ``CARD_LIMIT_MAX``（100）张（后端列表端点单次上限），其余按 ``limit`` 读；过滤在客户端
    按标题/来源标签完成。空结果、无匹配、后端不可用都只打印玩家可读中文提示。
    """
    keyword = query.strip() if isinstance(query, str) else ""
    display_limit = limit if CARD_LIMIT_MIN <= limit <= CARD_LIMIT_MAX else CARD_LIMIT_DEFAULT
    fetch_limit = CARD_LIMIT_MAX if (keyword or show_all) else display_limit
    app = _build_readonly_app(db_target, "人物卡库暂时打不开：请检查存档路径，或稍后再试。")
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/character-cards", params={"limit": fetch_limit})
            if response.status_code != 200:
                raise ConsoleError(
                    "人物卡列表暂时打不开：可以先用网页版卡库查看人物卡，或稍后再试。"
                )
            payload = response.json()
    finally:
        dispose_app_engine(app)
    cards = payload.get("cards") if isinstance(payload, dict) else None
    rows = [row for row in cards if isinstance(row, dict)] if isinstance(cards, list) else []
    total = payload.get("total") if isinstance(payload, dict) else None
    matched = [row for row in rows if _card_matches_query(row, keyword)] if keyword else rows
    displayed = matched if show_all else matched[:display_limit]
    if not displayed:
        if keyword:
            print(f"卡库里没有匹配「{keyword}」的人物卡：换个关键词试试。")
            print("（去掉 --card-query 可以查看全部人物卡）")
        else:
            print("还没有人物卡：先在网页版卡库生成或导入一张，再用 --card-id 开局。")
        return 0
    total_count = total if isinstance(total, int) and total > 0 else len(rows)
    if keyword:
        print(
            f"最近人物卡（共 {total_count} 张，按最近更新排序；"
            f"匹配「{keyword}」{len(matched)} 张，显示 {len(displayed)} 张）："
        )
    else:
        print(f"最近人物卡（共 {total_count} 张，按最近更新排序；显示 {len(displayed)} 张）：")
    for index, row in enumerate(displayed, start=1):
        raw_name = row.get("name")
        name = raw_name if isinstance(raw_name, str) and raw_name.strip() else "未命名人物卡"
        raw_card_id = row.get("card_id")
        card_id = raw_card_id if isinstance(raw_card_id, str) and raw_card_id else "未知"
        updated = _card_updated_text(row.get("updated_at"))
        updated_part = f" · 更新 {updated}" if updated else ""
        source_label = _card_source_label(row.get("source"))
        print(f"{index}. [{source_label}] {name}{updated_part} · 卡 {card_id}")
    print("")
    if not keyword and not show_all and total_count > len(displayed):
        print(
            f"（还有更多人物卡：用 --card-limit <数量> 或 --card-all 查看，"
            f"单次最多 {CARD_LIMIT_MAX} 张）"
        )
    elif total_count > len(rows):
        print(
            f"（卡库共 {total_count} 张，终端单次最多浏览 {CARD_LIMIT_MAX} 张；"
            "更多请在网页版卡库查看）"
        )
    print("按人物卡开局：python -m textgame.console --card-id <卡ID>")
    print("查看单卡详情：python -m textgame.console --card-detail <卡ID>")
    print("（人物卡的生成/导入请在网页版卡库操作）")
    return 0


def _run_card_detail_app(db_target: Path | str | None, card_id: str) -> int:
    """``--card-detail``：只读查看单张人物卡详情（``GET /character-cards/{id}``）后退出。

    只打印安全子集（标题/来源/属性摘要/背包提示/警告）；卡 ID 不存在/格式非法/后端不可用
    一律中文可读报错（退出码非 0），不显示内部错误细节，也不建局/写库。
    """
    resolved = card_id.strip() if isinstance(card_id, str) else ""
    if not resolved:
        raise ConsoleError("--card-detail 需要人物卡 ID：先用 --list-cards 查看卡库里的卡 ID。")
    app = _build_readonly_app(db_target, "人物卡详情暂时打不开：请检查存档路径，或稍后再试。")
    try:
        with TestClient(app) as client:
            response = client.get(f"/api/v1/character-cards/{quote(resolved, safe='')}")
            if response.status_code != 200:
                raise ConsoleError(_card_detail_failure_message(response.status_code))
            payload = response.json()
    finally:
        dispose_app_engine(app)
    card = payload.get("card") if isinstance(payload, dict) else None
    if not isinstance(card, dict):
        raise ConsoleError("人物卡详情暂时打不开：游戏引擎返回了无法识别的卡片信息。")
    for line in _card_detail_lines(card, resolved):
        print(line)
    return 0


def _run_list_scenarios_app(db_target: Path | str | None = None) -> int:
    """``--list-scenarios``：只读列出玩家上传的场景（``GET /api/v1/scenarios``）后退出。

    场景 JSON 的导入/浏览在网页版场景库完成；终端只做「看 ID → ``--scenario-id`` 开局」。
    旧后端缺该端点时给出玩家可读提示（不阻塞其它入口）。
    """
    if isinstance(db_target, str):
        db_url = db_target
    elif db_target is not None:
        db_url = f"sqlite:///{db_target.resolve().as_posix()}"
    else:
        db_url = _configured_db_url()
    app = build_app(db_url if db_target is not None else None)
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/scenarios", params={"limit": 20})
            if response.status_code != 200:
                raise ConsoleError(
                    "场景库暂不可用（后端未提供 /api/v1/scenarios）；"
                    "可以先用网页版场景库导入/查看场景。"
                )
            payload = response.json()
        scenarios = payload.get("scenarios") if isinstance(payload, dict) else None
        rows = (
            [row for row in scenarios if isinstance(row, dict)]
            if isinstance(scenarios, list)
            else []
        )
        total = payload.get("total") if isinstance(payload, dict) else None
        if not rows:
            for tip in EMPTY_SCENARIO_LIBRARY_TIPS:
                print(tip)
            return 0
        shown_total = total if isinstance(total, int) and total > 0 else len(rows)
        print(f"已上传场景（共 {shown_total} 个；最多显示 20 个）：")
        for index, row in enumerate(rows, start=1):
            raw_title = row.get("title")
            title = raw_title if isinstance(raw_title, str) and raw_title else "未命名场景"
            raw_slug = row.get("slug")
            slug = raw_slug if isinstance(raw_slug, str) and raw_slug else ""
            summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
            rooms = summary.get("rooms") if isinstance(summary, dict) else None
            objectives = summary.get("objectives") if isinstance(summary, dict) else None
            endings = summary.get("endings") if isinstance(summary, dict) else None
            parts = []
            if isinstance(rooms, int):
                parts.append(f"{rooms} 个房间")
            if isinstance(objectives, int) and objectives:
                parts.append(f"{objectives} 个目标")
            if isinstance(endings, int) and endings:
                parts.append(f"{endings} 个结局")
            suffix = f" · {' / '.join(parts)}" if parts else ""
            raw_scenario_id = row.get("scenario_id")
            scenario_id = raw_scenario_id if isinstance(raw_scenario_id, str) else "未知"
            print(f"{index}. {title}（{slug}）{suffix} · 场景 {scenario_id}")
        print("")
        print("按上传场景开局：python -m textgame.console --scenario-id <场景 ID>")
        print("导入/删除场景：网页版「场景库」（/scenarios）")
        return 0
    finally:
        dispose_app_engine(app)


def _safe_json(response: Any) -> Any:
    """容错解析 HTTP 响应 JSON；失败返回空字典（错误报告只用状态码，不回显内部细节）。"""
    try:
        return response.json()
    except ValueError:
        return {}


def _existing_scenario_ids(client: TestClient) -> set[str]:
    """只读分页读取场景库现有场景 ID（批量导入的 ``[skip]`` 判定用）；失败降级为空集。"""
    ids: set[str] = set()
    offset = 0
    for _ in range(20):  # 防御：最多 20 页 × 100 = 2000 个场景
        response = client.get(
            "/api/v1/scenarios",
            params={"limit": scenario_pack.MAX_BATCH_LIMIT, "offset": offset},
        )
        if response.status_code != 200:
            return ids
        payload = _safe_json(response)
        scenarios = payload.get("scenarios") if isinstance(payload, dict) else None
        rows = (
            [row for row in scenarios if isinstance(row, dict)]
            if isinstance(scenarios, list)
            else []
        )
        for row in rows:
            scenario_id = row.get("scenario_id")
            if isinstance(scenario_id, str) and scenario_id:
                ids.add(scenario_id)
        total = payload.get("total") if isinstance(payload, dict) else None
        offset += len(rows)
        if not rows or not isinstance(total, int) or offset >= total:
            break
    return ids


def _run_import_scenarios_app(
    db_target: Path | str | None,
    directory: str,
    *,
    limit: int = scenario_pack.DEFAULT_BATCH_LIMIT,
) -> int:
    """``--import-scenarios``：批量导入目录下场景 JSON 到本机场景库（走既有导入端点）。

    只读扫描 + 逐文件预检（≤256KB/UTF-8/JSON 对象），逐个 ``POST /api/v1/scenarios``；
    幂等键 = 文件内容 sha256（重跑稳定跳过、不重复建档）。逐文件 ``[ok]/[skip]/[error]``
    中文报告；有失败退出码 1，全部成功/跳过退出码 0。不建局、不写事件（只写场景资料库）。
    """
    directory_path = Path(directory).expanduser()
    try:
        files, total_files = scenario_pack.scan_scenario_files(directory_path)
    except scenario_pack.ScenarioPackError as exc:
        raise ConsoleError(str(exc)) from exc
    print(f"批量导入场景包：{directory_path}")
    if not files:
        print("目录里没有可导入的场景文件（文件以对象为顶层、不超过 256KB）。")
        return 0
    selected = files[:limit]
    if total_files > limit:
        print(
            f"（目录中共 {total_files} 个 .json，本次处理前 {limit} 个；"
            f"可用 --import-limit 调整，上限 {scenario_pack.MAX_BATCH_LIMIT}）"
        )
    app = _build_readonly_app(db_target, "场景库暂时打不开：请检查存档路径，或稍后再试。")
    ok = skip = failed = 0
    try:
        with TestClient(app) as client:
            existing_ids = _existing_scenario_ids(client)
            created_ids: set[str] = set()
            for path in selected:
                try:
                    data = scenario_pack.read_scenario_file(path)
                except scenario_pack.ScenarioPackError as exc:
                    failed += 1
                    print(f"[error] {path.name} → {exc}")
                    continue
                response = client.post(
                    "/api/v1/scenarios",
                    headers={
                        "Idempotency-Key": scenario_pack.stable_import_key(data),
                        "Content-Type": "application/json",
                    },
                    content=data,
                )
                if response.status_code not in (200, 201):
                    failed += 1
                    reason = scenario_pack.describe_import_failure(
                        response.status_code, _safe_json(response)
                    )
                    print(f"[error] {path.name} → {reason}")
                    continue
                payload = _safe_json(response)
                scenario_id = payload.get("scenario_id") if isinstance(payload, dict) else None
                if not isinstance(scenario_id, str) or not scenario_id:
                    failed += 1
                    print(f"[error] {path.name} → 服务器返回了无法识别的场景信息")
                    continue
                if scenario_id in existing_ids or scenario_id in created_ids:
                    skip += 1
                    print(f"[skip] {path.name} → 已存在（同内容）· {scenario_id}")
                else:
                    created_ids.add(scenario_id)
                    ok += 1
                    print(f"[ok] {path.name} → {scenario_id}")
    finally:
        dispose_app_engine(app)
    print("")
    print(f"汇总：成功 {ok} / 跳过 {skip} / 失败 {failed}")
    if ok:
        print("按上传场景开局：python -m textgame.console --scenario-id <场景 ID>")
        print("查看/导出场景：--list-scenarios、--export-scenarios <目录>")
    return 1 if failed else 0


def _run_export_scenarios_app(
    db_target: Path | str | None,
    directory: str,
    *,
    limit: int = scenario_pack.DEFAULT_BATCH_LIMIT,
) -> int:
    """``--export-scenarios``：只读导出场景库为 ``<slug>.json``（可直接再导入）。

    先 ``GET /api/v1/scenarios`` 列表，再逐个 ``GET /{id}`` 取原始 ``definition`` 写文件
    （slug 重名追加 ``-<scenario_id 后 8 位>``）；逐文件 ``[ok]/[error]`` 中文报告；有失败
    退出码 1。导出只读场景资料库，不建局、不写事件。
    """
    directory_path = Path(directory).expanduser()
    if directory_path.exists() and not directory_path.is_dir():
        raise ConsoleError(f"导出目标不是目录：{directory_path}")
    try:
        directory_path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConsoleError("无法创建导出目录：请检查路径与权限") from exc
    app = _build_readonly_app(db_target, "场景库暂时打不开：请检查存档路径，或稍后再试。")
    print(f"批量导出场景包：{directory_path}")
    ok = failed = 0
    used_names: set[str] = set()
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/scenarios", params={"limit": limit})
            if response.status_code != 200:
                raise ConsoleError(
                    f"场景库暂时打不开：{scenario_pack.describe_list_failure(response.status_code)}"
                )
            payload = _safe_json(response)
            scenarios = payload.get("scenarios") if isinstance(payload, dict) else None
            rows = (
                [row for row in scenarios if isinstance(row, dict)]
                if isinstance(scenarios, list)
                else []
            )
            total = payload.get("total") if isinstance(payload, dict) else None
            if not rows:
                print(
                    "场景库里还没有可导出的场景：先用 --import-scenarios 导入，或在网页场景库导入。"
                )
                return 0
            shown_total = total if isinstance(total, int) and total > 0 else len(rows)
            export_count = min(limit, len(rows))
            note = "；可用 --export-limit 调整，上限 100" if shown_total > export_count else ""
            print(f"（场景库共 {shown_total} 个，本次导出 {export_count} 个{note}）")
            for row in rows[:limit]:
                scenario_id = row.get("scenario_id")
                if not isinstance(scenario_id, str) or not scenario_id:
                    failed += 1
                    print("[error] （缺少场景 ID 的列表项） → 无法定位场景")
                    continue
                detail = client.get(f"/api/v1/scenarios/{quote(scenario_id, safe='')}")
                if detail.status_code != 200:
                    failed += 1
                    reason = scenario_pack.describe_export_failure(detail.status_code)
                    print(f"[error] {scenario_id} → {reason}")
                    continue
                detail_payload = _safe_json(detail)
                definition = (
                    detail_payload.get("definition") if isinstance(detail_payload, dict) else None
                )
                if not isinstance(definition, dict):
                    failed += 1
                    print(f"[error] {scenario_id} → 详情里没有可导出的场景定义")
                    continue
                try:
                    data = scenario_pack.encode_export_payload(definition)
                except scenario_pack.ScenarioPackError as exc:
                    failed += 1
                    print(f"[error] {scenario_id} → {exc}")
                    continue
                filename = scenario_pack.scenario_export_filename(
                    row.get("slug"), scenario_id, used_names
                )
                try:
                    (directory_path / filename).write_bytes(data)
                except OSError:
                    failed += 1
                    print(f"[error] {scenario_id} → 无法写入文件（请检查目录权限）")
                    continue
                ok += 1
                print(f"[ok] {scenario_id} → {filename}")
    finally:
        dispose_app_engine(app)
    print("")
    print(f"汇总：成功 {ok} / 失败 {failed}")
    if ok:
        print("导出文件可直接再导入：python -m textgame.console --import-scenarios <目录>")
    return 1 if failed else 0


def _validate_scenario_pack_args(args: argparse.Namespace, raw_args: Sequence[str]) -> None:
    """场景包批量导入/导出参数校验：互斥/范围错误给玩家可读中文（main 统一转 exit 1）。"""
    import_dir = args.import_scenarios
    export_dir = args.export_scenarios
    if import_dir is not None and export_dir is not None:
        raise ConsoleError(
            "--import-scenarios 与 --export-scenarios 不能同时使用：一次只做一种批量操作。"
        )
    if import_dir is None and export_dir is None:
        if args.import_limit is not None:
            raise ConsoleError("--import-limit 只能与 --import-scenarios 一起使用。")
        if args.export_limit is not None:
            raise ConsoleError("--export-limit 只能与 --export-scenarios 一起使用。")
        return
    conflicts = [
        flag
        for flag, given in (
            ("--list", bool(args.list_games)),
            ("--list-cards", bool(args.list_cards)),
            ("--list-scenarios", bool(args.list_scenarios)),
            ("--card-id", args.card_id is not None),
            ("--card-detail", args.card_detail is not None),
            ("--scenario-id", args.scenario_id is not None),
            ("--continue", bool(args.continue_save)),
            ("--new", bool(args.new_game)),
            ("--scenario", _explicit_scenario(raw_args) is not None),
            (
                "--card-query/--card-limit/--card-all",
                bool(args.card_all) or args.card_query is not None or args.card_limit is not None,
            ),
        )
        if given
    ]
    if conflicts:
        raise ConsoleError(
            f"--import-scenarios/--export-scenarios 不能与 {'/'.join(conflicts)} 同时使用："
            "请一次只执行一种操作。"
        )
    if import_dir is not None:
        if not str(import_dir).strip():
            raise ConsoleError("--import-scenarios 需要目录路径。")
        try:
            scenario_pack.normalize_batch_limit(args.import_limit)
        except scenario_pack.ScenarioPackError as exc:
            raise ConsoleError(f"--import-limit：{exc}") from exc
    if export_dir is not None:
        if not str(export_dir).strip():
            raise ConsoleError("--export-scenarios 需要目录路径。")
        try:
            scenario_pack.normalize_batch_limit(args.export_limit)
        except scenario_pack.ScenarioPackError as exc:
            raise ConsoleError(f"--export-limit：{exc}") from exc


def _run_interactive_app(
    db_target: Path | str | None,
    seed: int,
    *,
    scenario: str = "m2",
    requested_scenario: str | None = None,
    continue_save: bool = False,
    new_game: bool = False,
    card_id: str | None = None,
    scenario_id: str | None = None,
) -> int:
    """交互入口：装配组合根 → 决定继续/新开/按卡（按上传场景）开局 → 进入回合循环。

    ``db_target``：``None``=用配置默认库；``Path``=本地 SQLite 文件；``str``=``sqlite:///`` DSN
    （``--db`` 的 DSN 自动转换结果）。``card_id``/``scenario_id`` 非空时按卡/按场景新开一局
    （不自动继续旧档）。
    """
    if isinstance(db_target, str):
        db_url = db_target
    elif db_target is not None:
        db_url = f"sqlite:///{db_target.resolve().as_posix()}"
    else:
        db_url = _configured_db_url()
    try:
        app = build_app(db_url if db_target is not None else None)
    except Exception as exc:  # 存档路径不可用时转成玩家可读提示
        raise ConsoleError(
            f"无法打开或创建存档（请检查 --db 路径与目录权限）：{exc.__class__.__name__}"
        ) from exc
    try:
        with TestClient(app) as client:
            session = GameSession(
                client, seed=seed, scenario=scenario, card_id=card_id, scenario_id=scenario_id
            )
            notice = prepare_session(
                session,
                db_url=db_url,
                continue_save=continue_save,
                new_game=new_game,
                requested_scenario=requested_scenario,
                card_id=card_id,
                scenario_id=scenario_id,
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
        "--db",
        type=str,
        default=None,
        help="SQLite 存档路径或 sqlite:/// DSN（默认 data/textgame.db）",
    )
    parser.add_argument(
        "--list",
        dest="list_games",
        action="store_true",
        help="列出同库最近存档（按最近更新排序）后退出",
    )
    parser.add_argument(
        "--list-cards",
        dest="list_cards",
        action="store_true",
        help="列出人物卡库最近人物卡（按最近更新排序）后退出",
    )
    parser.add_argument(
        "--card-query",
        dest="card_query",
        type=str,
        default=None,
        metavar="关键词",
        help="配合 --list-cards：按人物卡标题或来源标签过滤（客户端匹配）",
    )
    parser.add_argument(
        "--card-limit",
        dest="card_limit",
        type=int,
        default=None,
        metavar="N",
        help=f"配合 --list-cards：显示数量 1..{CARD_LIMIT_MAX}（默认 {CARD_LIMIT_DEFAULT}）",
    )
    parser.add_argument(
        "--card-all",
        dest="card_all",
        action="store_true",
        help=f"配合 --list-cards：显示上限内的全部人物卡（单次最多 {CARD_LIMIT_MAX} 张）",
    )
    parser.add_argument(
        "--card-detail",
        dest="card_detail",
        type=str,
        default=None,
        metavar="卡ID",
        help="查看单张人物卡详情（只读；--list-cards 可查卡 ID）",
    )
    parser.add_argument(
        "--card-id",
        "--card",
        dest="card_id",
        type=str,
        default=None,
        help="按人物卡开局：传卡库中的卡 ID（--list-cards 可查；总是新开一局）",
    )
    parser.add_argument(
        "--list-scenarios",
        dest="list_scenarios",
        action="store_true",
        help="列出玩家上传的场景（按最近更新排序）后退出",
    )
    parser.add_argument(
        "--scenario-id",
        dest="scenario_id",
        type=str,
        default=None,
        help="按玩家上传的场景开局：传场景库中的场景 ID（--list-scenarios 可查；总是新开一局）",
    )
    parser.add_argument(
        "--import-scenarios",
        dest="import_scenarios",
        type=str,
        default=None,
        metavar="目录",
        help="批量导入目录中的 *.json 场景（逐文件 ≤256KB；幂等键由内容派生，可安全重跑）",
    )
    parser.add_argument(
        "--import-limit",
        dest="import_limit",
        type=int,
        default=None,
        metavar="N",
        help=(
            f"配合 --import-scenarios：最多处理数量 1..{scenario_pack.MAX_BATCH_LIMIT}"
            f"（默认 {scenario_pack.DEFAULT_BATCH_LIMIT}）"
        ),
    )
    parser.add_argument(
        "--export-scenarios",
        dest="export_scenarios",
        type=str,
        default=None,
        metavar="目录",
        help="批量导出场景库到目录（每个场景一个 <slug>.json，可直接再导入）",
    )
    parser.add_argument(
        "--export-limit",
        dest="export_limit",
        type=int,
        default=None,
        metavar="N",
        help=(
            f"配合 --export-scenarios：最多导出数量 1..{scenario_pack.MAX_BATCH_LIMIT}"
            f"（默认 {scenario_pack.DEFAULT_BATCH_LIMIT}）"
        ),
    )
    parser.add_argument("--seed", type=int, default=42, help="新存档随机种子（默认 42）")
    parser.add_argument(
        "--scenario",
        choices=SUPPORTED_SCENARIOS,
        default="m2",
        help=(
            "新开存档的场景：m2 单场景 / m3 多角色 / m4 制造 / "
            "story「余烬哨站」剧情（推荐首玩）；默认 m2"
        ),
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


def _explicit_scenario(argv: Sequence[str]) -> str | None:
    """从原始 argv 判断用户是否**显式**给了 ``--scenario``（argparse 默认值无法区分）。

    未显式给时返回 ``None``（自动继续旧存档不提示场景差异）；显式给时返回其值
    （``--scenario m3`` 或 ``--scenario=m3``），供 :func:`prepare_session` 做场景一致性检查。
    """
    tokens = list(argv)
    for index, token in enumerate(tokens):
        if token == "--scenario" and index + 1 < len(tokens):
            return tokens[index + 1]
        if token.startswith("--scenario="):
            return token.split("=", 1)[1]
    return None


def main(argv: Sequence[str] | None = None) -> int:
    """终端入口：``python -m textgame.console [--check|--list|--list-cards|--list-scenarios]
    [--db PATH|DSN] [--seed N] [--scenario m2|m3|m4|story] [--continue|--resume] [--new]
    [--card-id ID] [--scenario-id ID] [--card-query 关键词] [--card-limit N] [--card-all]
    [--card-detail 卡ID] [--import-scenarios 目录] [--import-limit N]
    [--export-scenarios 目录] [--export-limit N]``。"""
    raw_args = list(argv) if argv is not None else sys.argv[1:]
    args = _build_parser().parse_args(raw_args)
    ensure_utf8_stdio()
    try:
        if args.check:
            return _run_check_app()
        _validate_scenario_pack_args(args, raw_args)
        if args.import_scenarios is not None:
            return _run_import_scenarios_app(
                _parse_db_target(args.db),
                str(args.import_scenarios),
                limit=scenario_pack.normalize_batch_limit(args.import_limit),
            )
        if args.export_scenarios is not None:
            return _run_export_scenarios_app(
                _parse_db_target(args.db),
                str(args.export_scenarios),
                limit=scenario_pack.normalize_batch_limit(args.export_limit),
            )
        _validate_card_browse_args(args)
        if args.card_detail is not None:
            return _run_card_detail_app(_parse_db_target(args.db), str(args.card_detail))
        if args.list_games:
            return _run_list_app(_parse_db_target(args.db))
        if args.list_cards:
            return _run_list_cards_app(
                _parse_db_target(args.db),
                query=None if args.card_query is None else str(args.card_query),
                limit=CARD_LIMIT_DEFAULT if args.card_limit is None else int(args.card_limit),
                show_all=bool(args.card_all),
            )
        if args.list_scenarios:
            return _run_list_scenarios_app(_parse_db_target(args.db))
        if args.scenario_id is not None:
            # 按上传场景开局总是新建存档：与显式场景/按卡/强制继续互斥（--new 冗余但容忍）。
            if (
                _explicit_scenario(raw_args) is not None
                or args.card_id is not None
                or bool(args.continue_save)
            ):
                raise ConsoleError(
                    "--scenario-id 不能与 --scenario/--card-id/--continue 同时使用："
                    "按上传场景开局总是新开一局，请去掉其中一个参数。"
                )
            return _run_interactive_app(
                _parse_db_target(args.db),
                int(args.seed),
                scenario_id=str(args.scenario_id),
            )
        if args.card_id is not None:
            # 按卡开局总是新建存档：与显式场景/强制继续互斥（--new 冗余但容忍）。
            if _explicit_scenario(raw_args) is not None or bool(args.continue_save):
                raise ConsoleError(
                    "--card-id 不能与 --scenario/--continue 同时使用："
                    "按人物卡开局总是新开一局，请去掉其中一个参数。"
                )
            return _run_interactive_app(
                _parse_db_target(args.db),
                int(args.seed),
                card_id=str(args.card_id),
            )
        return _run_interactive_app(
            _parse_db_target(args.db),
            int(args.seed),
            scenario=str(args.scenario),
            requested_scenario=_explicit_scenario(raw_args),
            continue_save=bool(args.continue_save),
            new_game=bool(args.new_game),
        )
    except ConsoleError as exc:
        print(f"终端入口未通过：{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
