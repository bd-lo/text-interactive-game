"""N3-045 场景包批量导入/导出：终端 CLI 的纯逻辑层（扫描/上限/幂等键/文件名/错误文案）。

设计口径（第 17 章 §17.9.2 N3-045、第 6 章 §6.7 场景库端点）：

- **批量导入**：只读扫描目录下 ``*.json``（不递归、按文件名稳定排序），逐文件校验
  ≤256KB / UTF-8 / 顶层 JSON 对象，然后逐个走既有 ``POST /api/v1/scenarios``；
  幂等键由**文件内容 sha256 派生**（稳定可重放：同内容重跑只回放首次结果，不会重复建档，
  CLI 依据「导入前已存在的场景 ID / 本次已创建 ID」报告 ``[skip]``）。
- **批量导出**：先 ``GET /api/v1/scenarios`` 列表（默认 50 / 上限 100），再逐个
  ``GET /api/v1/scenarios/{id}`` 取原始 ``definition`` 写 ``<slug>.json``；slug 重名时
  追加 ``-<scenario_id 后 8 位>``。导出文件可直接作为 ``POST /scenarios`` 的 body 或
  ``--import-scenarios`` 的输入再导入（roundtrip）；只读、零写库。
- 错误口径：逐文件 ``[ok]/[skip]/[error]`` + 中文可读原因（不向玩家暴露端点/异常细节）；
  有失败退出码 1，全成/全跳过 0。
- 本模块不写库、不碰事件/状态/golden、零新增依赖；网页多文件 UI 由后续前端批次接入。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from textgame.domain.user_scenario import MAX_DEFINITION_BYTES

#: 批量操作默认处理数量（与场景库列表端点默认 limit 一致）。
DEFAULT_BATCH_LIMIT = 50
#: 批量操作单次上限（与场景库列表端点上限一致；防一次扫爆目录/请求量）。
MAX_BATCH_LIMIT = 100
#: 单个场景文件字节上限（与 ``POST /scenarios`` 导入上限 256KB 一致）。
MAX_SCENARIO_FILE_BYTES = MAX_DEFINITION_BYTES
#: slug 形态（服务端约束 ``^[a-z0-9-]{2,32}$``；导出文件名只接受 ASCII 安全形态）。
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,31}$")
#: 重名时追加的场景 ID 后 8 位长度。
_ID_SUFFIX_LEN = 8


class ScenarioPackError(ValueError):
    """批量导入/导出的参数/文件错误：消息为玩家可读中文，接入层转 ``ConsoleError``。"""


def normalize_batch_limit(raw: int | None, *, default: int = DEFAULT_BATCH_LIMIT) -> int:
    """解析/校验批量数量：缺省 ``default``，必须落在 1..``MAX_BATCH_LIMIT``。"""
    value = default if raw is None else raw
    if value < 1 or value > MAX_BATCH_LIMIT:
        raise ScenarioPackError(f"数量必须在 1 到 {MAX_BATCH_LIMIT} 之间（收到 {value}）")
    return value


def scan_scenario_files(directory: Path) -> tuple[list[Path], int]:
    """扫描目录下 ``*.json``（不递归、按文件名稳定排序）→ ``(窗口内文件, 总文件数)``。

    目录不存在/不是目录 → :class:`ScenarioPackError`（中文可读）；空目录返回空列表。
    """
    if not directory.exists():
        raise ScenarioPackError(f"目录不存在：{directory}")
    if not directory.is_dir():
        raise ScenarioPackError(f"不是目录：{directory}")
    files = sorted(
        (path for path in directory.iterdir() if path.is_file() and path.suffix.lower() == ".json"),
        key=lambda path: path.name.casefold(),
    )
    return files, len(files)


def read_scenario_file(path: Path) -> bytes:
    """读取并预检单个场景文件：≤256KB、UTF-8（容忍 BOM）、合法 JSON、顶层为对象。

    返回原始字节（作为 ``POST /scenarios`` 的 body，服务端仍做权威校验）；
    任一预检失败抛 :class:`ScenarioPackError`（消息即逐文件报告的中文原因）。
    """
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ScenarioPackError("文件无法读取（请检查路径与权限）") from exc
    if size > MAX_SCENARIO_FILE_BYTES:
        raise ScenarioPackError(f"文件超过 {MAX_SCENARIO_FILE_BYTES // 1024}KB 上限（{size} 字节）")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ScenarioPackError("文件无法读取（请检查路径与权限）") from exc
    if len(data) > MAX_SCENARIO_FILE_BYTES:
        raise ScenarioPackError(f"文件超过 {MAX_SCENARIO_FILE_BYTES // 1024}KB 上限")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ScenarioPackError("文件不是 UTF-8 文本") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ScenarioPackError("文件不是合法的场景内容") from exc
    if not isinstance(payload, dict):
        raise ScenarioPackError("场景文件顶层必须是对象")
    return data


def stable_import_key(data: bytes) -> str:
    """由文件内容 sha256 派生稳定幂等键（同内容必得同一键，重跑只回放首次结果）。"""
    return f"console-scenario-pack-{hashlib.sha256(data).hexdigest()}"


def _slug_base(slug: object, scenario_id: str) -> str:
    """slug → ASCII 安全文件名基名；缺失/非法时回退 ``scenario-<id 后 8 位>``。"""
    raw = slug if isinstance(slug, str) else ""
    if _SLUG_RE.fullmatch(raw):
        return raw
    cleaned = re.sub(r"[^a-z0-9-]+", "-", raw.strip().lower()).strip("-")
    if len(cleaned) >= 2:
        return cleaned[:32]
    suffix = scenario_id[-_ID_SUFFIX_LEN:] if len(scenario_id) >= _ID_SUFFIX_LEN else "unknown"
    return f"scenario-{suffix}"


def scenario_export_filename(slug: object, scenario_id: str, used: set[str]) -> str:
    """生成导出文件名 ``<slug>.json``；同一批内重名追加 ``-<scenario_id 后 8 位>``。"""
    base = _slug_base(slug, scenario_id)
    candidate = base
    if candidate in used:
        candidate = f"{base}-{scenario_id[-_ID_SUFFIX_LEN:]}"
        index = 2
        while candidate in used:  # 理论边界：同 slug 且同 ID 后 8 位碰撞
            candidate = f"{base}-{scenario_id[-_ID_SUFFIX_LEN:]}-{index}"
            index += 1
    used.add(candidate)
    return f"{candidate}.json"


def encode_export_payload(definition: Mapping[str, Any]) -> bytes:
    """把原始场景定义编码为可直接再导入的 UTF-8 JSON 字节（pretty 优先、超限回退紧凑）。"""
    try:
        payload = (
            json.dumps(definition, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        ).encode("utf-8")
        if len(payload) > MAX_SCENARIO_FILE_BYTES:
            payload = (
                json.dumps(
                    definition,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                + "\n"
            ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ScenarioPackError("场景定义含无法序列化的内容") from exc
    if len(payload) > MAX_SCENARIO_FILE_BYTES:
        raise ScenarioPackError(
            f"场景定义超过 {MAX_SCENARIO_FILE_BYTES // 1024}KB 上限，无法导出为单文件"
        )
    return payload


def describe_import_failure(status_code: int, payload: object) -> str:
    """导入失败 → 玩家可读中文原因（不显示端点/异常/服务端内部消息）。"""
    code = payload.get("error_code") if isinstance(payload, dict) else None
    if status_code == 413 or code == "OUT_OF_RANGE":
        return f"超过 {MAX_SCENARIO_FILE_BYTES // 1024}KB 上限"
    if status_code == 422:
        return "场景内容未通过校验（可用网页场景库预检查看具体字段）"
    if status_code == 403:
        return "内容安全检查未通过"
    if status_code == 409:
        return "幂等键与已有内容冲突，请换一份文件后重试"
    if status_code == 404:
        return "当前后端未提供场景导入接口（请升级服务端）"
    if status_code >= 500:
        return "服务器暂时无法处理，请稍后再试"
    return f"导入失败（HTTP {status_code}）"


def describe_export_failure(status_code: int) -> str:
    """导出单个场景失败 → 玩家可读中文原因。"""
    if status_code == 404:
        return "场景已不存在（可能已被删除）"
    if status_code >= 500:
        return "服务器暂时无法处理，请稍后再试"
    return f"导出失败（HTTP {status_code}）"


def describe_list_failure(status_code: int) -> str:
    """读取场景库列表失败 → 玩家可读中文原因。"""
    if status_code == 404:
        return "当前后端未提供场景库列表接口（请升级服务端）"
    if status_code >= 500:
        return "服务器暂时无法处理，请稍后再试"
    return f"场景库暂时打不开（HTTP {status_code}）"


__all__ = [
    "DEFAULT_BATCH_LIMIT",
    "MAX_BATCH_LIMIT",
    "MAX_SCENARIO_FILE_BYTES",
    "ScenarioPackError",
    "describe_export_failure",
    "describe_import_failure",
    "describe_list_failure",
    "encode_export_payload",
    "normalize_batch_limit",
    "read_scenario_file",
    "scan_scenario_files",
    "scenario_export_filename",
    "stable_import_key",
]
