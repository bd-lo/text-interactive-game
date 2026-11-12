#!/usr/bin/env python3
"""m3 叙事受控重基线工具（N4-054 / ADR D-N4-1）。

**默认 dry-run（只读）**：按冻结 golden 的 ``create_game`` + ``script`` 用生产同构装配
（``InMemoryRepository`` + M3 多角色编排链路 + 确定性模板叙事 + ``DisabledLLMClient``）
重新跑一遍，把「重生成观测」与「冻结 golden」逐字段 diff，输出 JSON 报告；
无叙事变更时证明 **0 diff**（旧 == 新）。

**``--apply``（受控写入）**：必须同时给 ``--confirm <golden-name>``，先备份目标 golden 到
``.work/rebaseline/<UTC>-<golden>/``（``.bak`` + ``.sha256`` 双哈希），再原子写目标文件；
只写 ``<golden-dir>/<name>_golden.json`` 一个文件，不碰 provenance/测试/运行时代码/生产库。
apply 后必须由 Lead 手工更新 ``tests/replay/test_golden_provenance.py::_GOLDEN_SHA256``
（工具会把新的 LF 规范化 sha256 打印/写进报告，便于粘贴），并在进度文档 + PROGRESS 登记；
禁止 ``--snapshot-update`` 或手工编辑 golden 掩盖回归。

支持范围（registry）**只含 m3**：本次受控重基线只服务 m3 叙事层变更（R2 §6 / H4 §5 遗留的
「m3 建模重构会破 golden text_len」）；m2/m4 或未知 golden 一律拒绝（需另立 ADR 扩展 registry）。

用法::

    # 默认 dry-run（0 diff 证据；--diff-report 输出逐字段 old/new）
    .venv\\Scripts\\python.exe scripts/rebaseline_golden.py --golden m3 \\
        --dry-run --diff-report --out .work/n-batch/rebase-054.json

    # 受控 apply（示例；真实执行前须 Lead 审批 + clean 工作区）
    .venv\\Scripts\\python.exe scripts/rebaseline_golden.py --golden m3 --apply --confirm m3

    # 在临时副本上演练 apply（不改 tracked golden）
    .venv\\Scripts\\python.exe scripts/rebaseline_golden.py --golden m3 --apply --confirm m3 \\
        --golden-dir .work/tmp-golden --backup-dir .work/tmp-backup

退出码：0 = 成功（默认含「有 diff」）；1 = ``--fail-on-diff`` 且有差异；2 = 参数/校验错误
（未知 golden、apply 未确认、golden 缺失/结构非法等）。本工具不联网、不新增依赖。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

# 与 tests/conftest.py 同口径：离线锁定 litellm 成本表（本工具不联网）。
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from textgame.agents.character_agent import DeterministicCharacterAgent
from textgame.agents.parser_agent import IntentParseAgent
from textgame.domain.hashing import state_hash
from textgame.domain.rules.engine import RuleEngine
from textgame.domain.scenario import (
    M3_NONPLAYER_TAGS,
    M3_NPC_PLANS,
    PLAYER_ID,
)
from textgame.llm.client import DisabledLLMClient
from textgame.orchestration.dto import TurnRequest
from textgame.orchestration.intent import DeterministicIntentParser
from textgame.orchestration.narrator import TemplateNarrator
from textgame.orchestration.resolver import Resolver, ToolGateRouter
from textgame.orchestration.turn_orchestrator import (
    _MAX_TEXT_CHARS,
    _MIN_TEXT_CHARS,
    CreateGameRequest,
    TurnOrchestrator,
)
from textgame.persistence.memory import InMemoryRepository
from textgame.services.action_service import ActionService
from textgame.services.actor_directory import ActorDirectory
from textgame.services.audit_service import AuditService
from textgame.services.game_engine import GameEngine
from textgame.services.query_service import QueryService
from textgame.tools.builtin.m2 import build_m2_tool_registry
from textgame.tools.gate import ToolGate

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
DEFAULT_GOLDEN_DIR: Final[Path] = REPO_ROOT / "tests" / "fixtures" / "golden"
DEFAULT_BACKUP_ROOT: Final[Path] = REPO_ROOT / ".work" / "rebaseline"

#: golden 顶层字段顺序（与冻结 fixture 一致；apply 写出时保持）。
_GOLDEN_TOP_KEYS: Final[tuple[str, ...]] = (
    "_comment",
    "save_seed",
    "create_game",
    "script",
    "turns",
    "final_state_hash",
    "final_turn",
    "final_last_seq",
    "final_version",
    "event_total",
    "audit_total",
    "conflict_turn",
    "text_len_min",
    "text_len_max",
)
#: 单个 turn 观测字段顺序（与冻结 fixture 一致）。
_TURN_KEYS: Final[tuple[str, ...]] = (
    "input",
    "attempted_turn",
    "ok",
    "turn_id",
    "version",
    "last_seq",
    "event_ids",
    "event_actors",
    "event_methods",
    "rng_seeds",
    "state_hash",
    "text_len",
    "audit",
)
#: 不参与「叙事重基线」数据 diff 的字段（注释由 Lead 维护，apply 时原样保留）。
_IGNORED_DIFF_ROOTS: Final[frozenset[str]] = frozenset({"_comment"})


class RebaselineError(RuntimeError):
    """受控重基线参数/校验错误（CLI 映射为退出码 2）。"""


class GoldenNotSupportedError(RebaselineError):
    """未知/不支持的 golden 名称（本工具 registry 只含 m3）。"""


@dataclass(frozen=True)
class GoldenProfile:
    """受支持 golden 的元信息（registry 条目）。"""

    name: str
    filename: str
    scenario: str
    #: 从冻结 golden 的 ``create_game``/``script`` 重生成候选观测（纯函数式：无 IO 写入）。
    generate: Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class M3Harness:
    """m3 生产同构内存装配（与 tests/conftest.py 的 m3_game_harness 同构）。"""

    repo: InMemoryRepository
    orchestrator: TurnOrchestrator
    audit: AuditService


def _sha256_bytes(data: bytes) -> str:
    """原始字节 sha256（备份文件完整性用）。"""
    return hashlib.sha256(data).hexdigest()


def normalized_sha256(path: Path) -> str:
    """按 LF 规范化后的内容算 sha256（与 ``test_golden_provenance.py`` 完全同口径）。"""
    if not path.is_file():
        raise RebaselineError(f"golden fixture 不存在: {path}")
    normalized = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return _sha256_bytes(normalized)


def _serialize_golden(document: dict[str, Any]) -> str:
    """按冻结 fixture 的既有格式序列化（UTF-8、2 空格缩进、中文不转义、末尾换行）。"""
    return json.dumps(document, ensure_ascii=False, indent=2) + "\n"


# ---------------------------------------------------------------------------
# m3 生成器（与 tests/replay/test_m3_golden_replay.py 的 _play 观测同构）
# ---------------------------------------------------------------------------
def _build_m3_harness() -> M3Harness:
    """按 ``main.py`` 组合根口径装配 M3 多角色内存应用（确定性、零联网）。"""
    repo = InMemoryRepository()
    engine = GameEngine(repo)
    query = QueryService(repo)
    action = ActionService(query)
    registry = build_m2_tool_registry()
    gate = ToolGate(registry=registry, query=query, action=action)
    rules = RuleEngine()
    actors = ActorDirectory(nonplayer_tags=M3_NONPLAYER_TAGS)
    resolver = Resolver(
        rules=rules,
        services=ToolGateRouter(gate),
        actors=actors,
        save_seed="save-42",
    )
    character_agent = DeterministicCharacterAgent(M3_NPC_PLANS)
    character_agents = dict.fromkeys(M3_NPC_PLANS, character_agent)
    audit = AuditService(repo)
    parser = IntentParseAgent(
        DisabledLLMClient(),
        DeterministicIntentParser(),
        tool_specs=registry.visible_tools(PLAYER_ID, frozenset({"player"})),
    )
    orchestrator = TurnOrchestrator(
        repo=repo,
        engine=engine,
        query=query,
        action=action,
        parser=parser,
        gate=gate,
        narrator=TemplateNarrator(),
        rules=rules,
        save_seed_default="save-42",
        registry=registry,
        resolver=resolver,
        actors=actors,
        character_agents=character_agents,
        audit=audit,
    )
    return M3Harness(repo=repo, orchestrator=orchestrator, audit=audit)


def _audit_summary(harness: M3Harness, game_id: str, turn_id: int) -> list[dict[str, Any]]:
    """该回合审计行摘要（按 turn_id、proposal_id 排序；与 golden 字段同构）。"""
    rows = harness.audit.query(game_id, turn_id=turn_id)
    return [
        {
            "proposal_id": row.proposal_id,
            "actor_id": row.actor_id,
            "tool": row.tool,
            "verdict": row.verdict,
            "status": row.status,
            "attempt": row.attempt,
        }
        for row in sorted(rows, key=lambda row: (row.turn_id, row.proposal_id))
    ]


def _conflict_turn(turns: list[dict[str, Any]]) -> int | None:
    """首个「同回合既有 applied 又有 rejected」的 attempted_turn（决策冲突演示回合）。"""
    for turn in turns:
        verdicts = {row["verdict"] for row in turn["audit"]}
        if "applied" in verdicts and "rejected" in verdicts:
            return int(turn["attempted_turn"])
    return None


def generate_m3_candidate(frozen: dict[str, Any]) -> dict[str, Any]:
    """用冻结 golden 的 create_game/script 重新生成数据字段（注释/输入契约原样保留）。"""
    name = "m3"
    try:
        script = list(frozen["script"])
        create_game = dict(frozen["create_game"])
    except (KeyError, TypeError, ValueError) as exc:  # pragma: no cover - 结构校验已前置
        raise RebaselineError(f"{name} golden 缺少 script/create_game: {exc}") from exc
    if not all(isinstance(text, str) and text for text in script):
        raise RebaselineError(f"{name} golden.script 非法：必须是非空字符串列表")
    seed = create_game.get("seed")
    key = create_game.get("idempotency_key")
    scenario = create_game.get("scenario", "m3")
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise RebaselineError(f"{name} golden.create_game.seed 非法: {seed!r}")
    if not isinstance(key, str) or not key:
        raise RebaselineError(f"{name} golden.create_game.idempotency_key 非法: {key!r}")
    if scenario != "m3":
        raise RebaselineError(f"{name} golden.create_game.scenario 非法: {scenario!r}")

    harness = _build_m3_harness()
    view = harness.orchestrator.create_game(
        CreateGameRequest(seed=seed, idempotency_key=key, scenario="m3")
    )
    game_id = view.game_id
    version = view.version
    turns: list[dict[str, Any]] = []
    for index, text in enumerate(script, start=1):
        before = harness.repo.load(game_id)
        if before is None:  # pragma: no cover - 防御
            raise RebaselineError(f"{name} 生成失败：存档 {game_id} 不存在")
        attempted_turn = before.state.turn + 1
        result = harness.orchestrator.run(
            TurnRequest(
                game_id=game_id,
                text=text,
                expected_version=version,
                idempotency_key=f"m3-golden-turn-{index:02d}",
            )
        )
        after = harness.repo.load(game_id)
        if after is None:  # pragma: no cover - 防御
            raise RebaselineError(f"{name} 生成失败：第 {index} 回合后读不到存档")
        turns.append(
            {
                "input": text,
                "attempted_turn": attempted_turn,
                "ok": result.ok,
                "turn_id": after.state.turn,
                "version": after.version,
                "last_seq": after.last_seq,
                "event_ids": [event.id for event in result.events],
                "event_actors": [event.actor_id for event in result.events],
                "event_methods": [event.method for event in result.events],
                "rng_seeds": [event.rng_seed for event in result.events],
                "state_hash": state_hash(after.state),
                "text_len": len(result.text),
                "audit": _audit_summary(harness, game_id, attempted_turn),
            }
        )
        version = after.version
    final = harness.repo.load(game_id)
    if final is None:  # pragma: no cover - 防御
        raise RebaselineError(f"{name} 生成失败：最终状态读不到存档")
    events = harness.repo.read_events(game_id)
    candidate = {
        "_comment": frozen.get("_comment", ""),
        "save_seed": final.state.rng_state.save_seed,
        "create_game": create_game,
        "script": script,
        "turns": turns,
        "final_state_hash": state_hash(final.state),
        "final_turn": final.state.turn,
        "final_last_seq": final.last_seq,
        "final_version": final.version,
        "event_total": len(events),
        "audit_total": len(harness.audit.query(game_id)),
        "conflict_turn": _conflict_turn(turns),
        "text_len_min": _MIN_TEXT_CHARS,
        "text_len_max": _MAX_TEXT_CHARS,
    }
    return candidate


GOLDEN_REGISTRY: Final[dict[str, GoldenProfile]] = {
    "m3": GoldenProfile(
        name="m3",
        filename="m3_golden.json",
        scenario="m3",
        generate=generate_m3_candidate,
    ),
}


def _profile_or_raise(name: str) -> GoldenProfile:
    """取 registry 条目；未知 golden → ``GoldenNotSupportedError``（CLI exit 2）。"""
    profile = GOLDEN_REGISTRY.get(name)
    if profile is None:
        supported = "、".join(sorted(GOLDEN_REGISTRY))
        raise GoldenNotSupportedError(
            f"未知/不支持的 golden: {name!r}（本工具仅支持: {supported}）"
        )
    return profile


def _validate_golden_structure(name: str, document: Any) -> dict[str, Any]:
    """最小结构校验：必须是 dict 且含顶层必备键（避免对损坏文件做 diff/重写）。"""
    if not isinstance(document, dict):
        raise RebaselineError(f"{name} golden 顶层必须是 JSON 对象，实际 {type(document).__name__}")
    missing = [key for key in _GOLDEN_TOP_KEYS if key not in document]
    if missing:
        raise RebaselineError(f"{name} golden 缺少必备字段: {', '.join(missing)}")
    turns = document.get("turns")
    if not isinstance(turns, list) or not turns:
        raise RebaselineError(f"{name} golden.turns 必须是非空列表")
    for index, turn in enumerate(turns):
        if not isinstance(turn, dict):
            raise RebaselineError(f"{name} golden.turns[{index}] 必须是对象")
        missing_turn = [key for key in _TURN_KEYS if key not in turn]
        if missing_turn:
            raise RebaselineError(
                f"{name} golden.turns[{index}] 缺少必备字段: {', '.join(missing_turn)}"
            )
    return document


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------
def flatten_diff(
    old: Any,
    new: Any,
    *,
    path: str = "",
    diff_report: bool = False,
    diffs: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """递归展开 old/new 差异为 ``[{path, old, new}]``（容器逐层下钻；标量比较值）。

    ``path`` 使用 ``turns[1].event_ids`` 这类可读形式；列表/字典均逐元素下钻，因此
    同一字段内的元素差异也能定位（例如 ``turns[2].audit[0].verdict``）。
    """
    collected = diffs if diffs is not None else []
    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(set(old) | set(new)):
            child = f"{path}.{key}" if path else str(key)
            if not path and child in _IGNORED_DIFF_ROOTS:
                continue
            flatten_diff(
                old.get(key), new.get(key), path=child, diff_report=diff_report, diffs=collected
            )
        return collected
    if isinstance(old, list) and isinstance(new, list):
        for index in range(max(len(old), len(new))):
            child = f"{path}[{index}]"
            flatten_diff(
                old[index] if index < len(old) else "<missing>",
                new[index] if index < len(new) else "<missing>",
                path=child,
                diff_report=diff_report,
                diffs=collected,
            )
        return collected
    if old != new:
        item: dict[str, Any] = {"path": path or "<root>"}
        if diff_report:
            item["old"] = old
            item["new"] = new
        collected.append(item)
    return collected


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def _utc_stamp() -> str:
    """备份目录时间戳（UTC，秒级；文件名安全）。"""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _write_json_atomic(path: Path, document: dict[str, Any]) -> int:
    """原子写 JSON（同目录临时文件 + ``os.replace``）；返回写入字节数。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _serialize_golden(document)
    tmp = path.with_name(path.name + ".tmp-rebaseline")
    tmp.write_text(payload, encoding="utf-8")
    try:
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return len(payload.encode("utf-8"))


def _write_report(report: dict[str, Any], out_path: Path | None) -> None:
    """把报告写到 ``--out``（可选）；父目录自动创建。"""
    if out_path is None:
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_rebaseline(
    *,
    golden: str,
    mode: str = "dry-run",
    golden_dir: Path | None = None,
    out: Path | None = None,
    diff_report: bool = False,
    confirm: str | None = None,
    backup_dir: Path | None = None,
) -> dict[str, Any]:
    """执行一次 dry-run 或 apply，返回 JSON 可序列化报告（脚本 CLI 与单元测试共用）。

    - dry-run：只读；diff 报告写到内存并（可选）``--out``；目标文件 sha 不变（内部自检）。
    - apply：``confirm`` 必须严格等于 ``golden``；先备份（.bak + .sha256）再原子写；
      0 diff 时不写文件（避免无意义改动），报告 ``applied=false``。
    """
    profile = _profile_or_raise(golden)
    if mode not in {"dry-run", "apply"}:
        raise RebaselineError(f"未知 mode: {mode!r}（只允许 dry-run / apply）")
    root = (golden_dir or DEFAULT_GOLDEN_DIR).resolve()
    target = root / profile.filename
    if not target.is_file():
        raise RebaselineError(f"golden fixture 不存在: {target}")
    if mode == "apply" and confirm != golden:
        raise RebaselineError(
            f"apply 需要显式 --confirm {golden}（当前 confirm={confirm!r}）；"
            "受控重基线必须由 Lead 审批并显式确认目标。"
        )

    old_bytes = target.read_bytes()
    old_sha_norm = normalized_sha256(target)
    frozen = _validate_golden_structure(golden, json.loads(old_bytes.decode("utf-8")))
    candidate = profile.generate(frozen)
    new_sha_norm = _sha256_bytes(_serialize_golden(candidate).encode("utf-8"))
    diffs = flatten_diff(frozen, candidate, diff_report=diff_report)
    changed_fields = [item["path"] for item in diffs]
    changed_turns = sorted(
        {path.split("]")[0].split("[")[1] for path in changed_fields if path.startswith("turns[")},
        key=int,
    )
    report: dict[str, Any] = {
        "schema_version": 1,
        "kind": "golden-rebaseline-report",
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "golden": golden,
        "golden_path": str(target),
        "mode": mode,
        "applied": False,
        "result": "diff" if diffs else "no-diff",
        "turns_compared": len(frozen["turns"]),
        "diff_field_count": len(diffs),
        "diff_turn_count": len(changed_turns),
        "changed_fields": changed_fields,
        "old_sha256": old_sha_norm,
        "new_sha256": new_sha_norm,
        "hashes_match": old_sha_norm == new_sha_norm,
        "provenance_hint": (
            "若 apply 成功：把 tests/replay/test_golden_provenance.py 的 _GOLDEN_SHA256"
            f'["{profile.filename}"] 更新为 "{new_sha_norm}"，并复跑 tests/replay。'
        ),
        "backup": None,
        "write": None,
        "checks": {},
    }
    if diff_report:
        report["diffs"] = diffs

    if mode == "apply":
        if diffs:
            backup_root = (
                backup_dir or (DEFAULT_BACKUP_ROOT / f"{_utc_stamp()}-{golden}")
            ).resolve()
            backup_root.mkdir(parents=True, exist_ok=True)
            backup_path = backup_root / (profile.filename + ".bak")
            backup_path.write_bytes(old_bytes)
            sha_path = backup_root / (profile.filename + ".bak.sha256")
            sha_path.write_text(
                f"normalized_sha256={old_sha_norm}\nraw_sha256={_sha256_bytes(old_bytes)}\n"
                f"source={target}\n",
                encoding="utf-8",
            )
            written = _write_json_atomic(target, candidate)
            written_sha = normalized_sha256(target)
            if written_sha != new_sha_norm:  # pragma: no cover - 写后自检（磁盘/编码异常）
                raise RebaselineError(
                    f"写后自检失败：expected {new_sha_norm}，actual {written_sha}；"
                    f"备份在 {backup_path}，请用备份恢复并排查"
                )
            report["applied"] = True
            report["result"] = "applied"
            report["backup"] = {
                "dir": str(backup_root),
                "file": str(backup_path),
                "normalized_sha256": old_sha_norm,
                "raw_sha256": _sha256_bytes(old_bytes),
                "sha256_file": str(sha_path),
            }
            report["write"] = {
                "file": str(target),
                "bytes": written,
                "normalized_sha256": written_sha,
            }
            report["checks"] = {
                "golden_was_backed_up": backup_path.is_file(),
                "backup_matches_old": normalized_sha256(backup_path) == old_sha_norm,
                "target_written": True,
                "written_hash_matches_candidate": written_sha == new_sha_norm,
                "only_target_file_touched": True,
            }
        else:
            report["result"] = "no-diff"
            report["checks"] = {"no_changes_to_apply": True}
    else:
        # dry-run 只读自检：跑完再算一次目标 sha，必须与开始时一致。
        unchanged = normalized_sha256(target) == old_sha_norm and target.read_bytes() == old_bytes
        report["checks"] = {
            "dry_run_read_only": unchanged,
            "golden_untouched": unchanged,
            "diff_field_count_is_zero": len(diffs) == 0,
            "hashes_match": old_sha_norm == new_sha_norm,
        }
        if not unchanged:  # pragma: no cover - 防御：工具自身绝不写目标
            raise RebaselineError("dry-run 自检失败：目标 golden 在只读流程中被修改")

    _write_report(report, out)
    return report


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：默认 dry-run；``--fail-on-diff`` 有差异 exit 1；参数错误 exit 2。"""
    parser = argparse.ArgumentParser(
        description="m3 叙事受控重基线（dry-run 默认；apply 需 --confirm）"
    )
    parser.add_argument("--golden", required=True, help="golden 名称（本工具仅支持 m3）")
    parser.add_argument(
        "--dry-run", action="store_true", help="只读重生成 + diff（默认行为，可显式指定）"
    )
    parser.add_argument(
        "--apply", action="store_true", help="受控写入目标 golden（需 --confirm + 备份）"
    )
    parser.add_argument("--confirm", default=None, help="显式确认 apply 的 golden 名称")
    parser.add_argument("--diff-report", action="store_true", help="报告含逐字段 old/new 摘要")
    parser.add_argument("--golden-dir", type=Path, default=None, help="golden 目录（默认真实目录）")
    parser.add_argument(
        "--backup-dir", type=Path, default=None, help="备份目录（默认 .work/rebaseline/…）"
    )
    parser.add_argument("--out", type=Path, default=None, help="JSON 报告输出路径")
    parser.add_argument(
        "--fail-on-diff", action="store_true", help="dry-run 有差异时以退出码 1 结束"
    )
    args = parser.parse_args(argv)
    if args.apply and args.dry_run:
        print("FAIL: --apply 与 --dry-run 不能同时指定", file=sys.stderr)
        return 2
    mode = "apply" if args.apply else "dry-run"
    try:
        report = run_rebaseline(
            golden=args.golden,
            mode=mode,
            golden_dir=args.golden_dir,
            out=args.out,
            diff_report=args.diff_report,
            confirm=args.confirm,
            backup_dir=args.backup_dir,
        )
    except RebaselineError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    print(
        f"[rebaseline] golden={report['golden']} mode={report['mode']} "
        f"result={report['result']} diff_fields={report['diff_field_count']} "
        f"diff_turns={report['diff_turn_count']} turns={report['turns_compared']}"
    )
    print(
        f"[rebaseline] old_sha256={report['old_sha256']} new_sha256={report['new_sha256']} "
        f"hashes_match={report['hashes_match']}"
    )
    if report.get("backup"):
        backup = report["backup"]
        print(f"[rebaseline] backup={backup['file']} sha256={backup['sha256_file']}")
    if report.get("write"):
        print(f"[rebaseline] written={report['write']['file']} bytes={report['write']['bytes']}")
        print(f"[rebaseline] 下一步（Lead）：{report['provenance_hint']}")
    if args.out is not None:
        print(f"[rebaseline] report={args.out}")
    if args.fail_on_diff and report["diff_field_count"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
