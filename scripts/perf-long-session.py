#!/usr/bin/env python3
"""长会话性能基线（N4-056）：固定种子 + 离线模板叙事 + M3 多角色长会话压测。

做什么：
- 用**生产同构装配**（SQLite 仓储 + ``GameEngine`` 唯一写入者 + ``TurnOrchestrator``
  多角色链路 + ``TemplateNarrator`` 确定性模板叙事；LLM 用 ``DisabledLLMClient`` 强制离线）
  在临时 SQLite 库上创建一局 ``scenario="m3"``，按固定脚本跑 120 回合；
- 为了让**单个存档**在 120 回合内产生 500+ 已提交事件，复用 M3 的确定性角色 Agent
  ``DeterministicCharacterAgent``，但把「计划表」扩展为覆盖全部回合：两名拾荒者每回合各
  2 条 ``move_to`` 提案（``PlanStep`` 是服务端静态数据，生产 ``M3_NPC_PLANS`` 仅覆盖 10 回合）。
  每回合事件 = 玩家 ``talk_to`` 1 条 + NPC 2 名 × 2 条 = 5 条；120 回合 + 开局 = 601 条。
  装配与生产 ``main.py`` 的差异**只有计划表数据长度**（与生产 M3 golden 回放路径同构），
  不触碰任何运行时代码/事件结构；
- 采集墙钟总耗时/每回合耗时（mean/p50/p95/max）、``tracemalloc`` 峰值内存、事件数、
  临时库体积、最终 ``state_hash``，并做「事件日志回放 → 纯事件重建状态 → 哈希一致」校验；
- 输出 JSON（默认标准输出摘要 + ``--out`` 落盘），供 docs/design/long-session-perf.md
  记录本机基线与 tests/integration/test_long_session_perf.py 断言。

约束：只用标准库 + 项目既有依赖；库是 ``tempfile`` 临时 SQLite 文件（默认跑完删除），
**绝不写** ``data/textgame.db``；不联网（LLM 客户端禁用）。

用法::

    .venv\\Scripts\\python.exe scripts/perf-long-session.py --turns 120 \
        --out .work/n-batch/perf-056.json

    .venv\\Scripts\\python.exe scripts/perf-long-session.py --turns 120 --strict  # 阈值不达标 exit 1

退出码：0 = 跑完（默认）；1 = 运行错误 / ``--strict`` 下阈值不达标；2 = 参数错误。
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sqlite3
import statistics
import sys
import tempfile
import time
import tracemalloc
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import Engine

from textgame.agents.character_agent import DeterministicCharacterAgent
from textgame.agents.parser_agent import IntentParseAgent
from textgame.domain.events import EventLog
from textgame.domain.hashing import state_hash
from textgame.domain.models import GameState
from textgame.domain.rules.engine import RuleEngine
from textgame.domain.scenario import (
    M3_NONPLAYER_TAGS,
    M3_NPC_SCAVENGER_A,
    M3_NPC_SCAVENGER_B,
    PLAYER_ID,
    PlanStep,
)
from textgame.llm.client import DisabledLLMClient
from textgame.orchestration.dto import TurnRequest
from textgame.orchestration.intent import DeterministicIntentParser
from textgame.orchestration.narrator import TemplateNarrator
from textgame.orchestration.resolver import Resolver, ToolGateRouter
from textgame.orchestration.turn_orchestrator import CreateGameRequest, TurnOrchestrator
from textgame.persistence.database import init_schema, prepare_db
from textgame.persistence.sqlite_repository import SqliteRepository
from textgame.services.action_service import ActionService
from textgame.services.actor_directory import ActorDirectory
from textgame.services.audit_service import AuditService
from textgame.services.game_engine import GameEngine
from textgame.services.query_service import QueryService
from textgame.tools.builtin.m2 import build_m2_tool_registry
from textgame.tools.gate import ToolGate

#: 默认回合数（第 17 章 §17.9.2 N4-056：100+ 回合）。
DEFAULT_TURNS: int = 120
#: 固定种子（与 M2/M3 golden 的 ``save-42`` 同源，保证 ``state_hash`` 可复算）。
DEFAULT_SEED: int = 42
#: 回放事件数下限（056 验收：回放 500+ 事件）。
MIN_REPLAY_EVENTS: int = 500
#: 玩家每回合固定输入：入口房间与老哨兵同屋，``talk_to`` 每回合稳定产生 1 条事件。
PLAYER_INPUT: str = "对老哨兵问好"
#: 每回合期望事件数 = 玩家 1 + 2 名拾荒者 × 2 条移动提案。
EXPECTED_EVENTS_PER_TURN: int = 5
#: 阈值（刻意宽松，仅防数量级退化；本机实测见 docs/design/long-session-perf.md）。
THRESHOLDS: dict[str, float] = {
    # 120 回合总耗时（本机含 tracemalloc ≈ 11s，留 5 倍余量给 CI 慢机）。
    "total_seconds_max": 60.0,
    # 单回合 p95（本机含 tracemalloc ≈ 0.14s；阈值防 O(n²) 级退化）。
    "per_turn_p95_ms_max": 3000.0,
    # tracemalloc 峰值（本机 ≈ 3.3MB；512MB 是绝对上限告警）。
    "peak_tracemalloc_mb_max": 512.0,
    # 事件数下限（056 验收：回放 500+ 事件）。
    "events_min": float(MIN_REPLAY_EVENTS),
}


@dataclass(frozen=True)
class LongSessionHarness:
    """生产同构装配句柄（编排器 + 仓储 + Engine；用完必须 ``engine.dispose()``）。"""

    orchestrator: TurnOrchestrator
    repo: SqliteRepository
    engine: Engine


def build_long_session_plans(turns: int) -> dict[str, tuple[PlanStep, ...]]:
    """构造覆盖 ``turns`` 回合的确定性 NPC 计划表（每名拾荒者每回合 2 条往返移动）。

    生产 ``M3_NPC_PLANS`` 只覆盖 10 回合（M3 golden 口径）；长会话压测需要**可重复且
    每回合都有写入**的 NPC 动作，因此按同一 ``PlanStep`` 契约生成 2～``turns+1`` 回合的
    计划：先向北、再向南（同回合内回到入口），保证每条提案的前置条件在裁决时都成立。
    """
    if turns < 1:
        raise ValueError(f"turns 必须为正整数，收到 {turns!r}")
    plans: dict[str, tuple[PlanStep, ...]] = {}
    for actor_id in (M3_NPC_SCAVENGER_A, M3_NPC_SCAVENGER_B):
        steps: list[PlanStep] = []
        for turn_id in range(2, turns + 2):  # 开局第 1 回合 = game_created，玩家回合从 2 起
            steps.append(PlanStep(turn_id, "move_to", {"direction": "north"}))
            steps.append(PlanStep(turn_id, "move_to", {"direction": "south"}))
        plans[actor_id] = tuple(steps)
    return plans


def build_harness(db_path: Path, *, turns: int) -> LongSessionHarness:
    """按 ``main.py`` 组合根口径装配 M3 多角色链路（确定性、零联网、模板叙事）。"""
    engine, _session_factory = prepare_db(f"sqlite:///{db_path.as_posix()}")
    init_schema(engine)
    repo = SqliteRepository(engine)
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
        save_seed=f"save-{DEFAULT_SEED}",
    )
    character_agent = DeterministicCharacterAgent(build_long_session_plans(turns))
    character_agents = {M3_NPC_SCAVENGER_A: character_agent, M3_NPC_SCAVENGER_B: character_agent}
    parser = IntentParseAgent(
        DisabledLLMClient(),
        DeterministicIntentParser(),
        tool_specs=registry.visible_tools(PLAYER_ID, frozenset({"player"})),
    )
    orchestrator = TurnOrchestrator(
        repo=repo,
        engine=GameEngine(repo),
        query=query,
        action=action,
        parser=parser,
        gate=gate,
        narrator=TemplateNarrator(),
        rules=rules,
        save_seed_default=f"save-{DEFAULT_SEED}",
        registry=registry,
        resolver=resolver,
        actors=actors,
        character_agents=character_agents,
        audit=AuditService(repo),
    )
    return LongSessionHarness(orchestrator=orchestrator, repo=repo, engine=engine)


def _percentile(values: list[float], quantile: float) -> float:
    """nearest-rank 百分位（与 ``test_perf_baseline.py`` 口径一致，样本少时更诚实）。"""
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round(quantile * (len(ordered) - 1))))
    return ordered[index]


def _summarise_ms(latencies: list[float]) -> dict[str, float]:
    """每回合耗时统计（ms）。"""
    return {
        "mean": round(statistics.fmean(latencies), 2),
        "p50": round(_percentile(latencies, 0.50), 2),
        "p95": round(_percentile(latencies, 0.95), 2),
        "max": round(max(latencies), 2),
        "min": round(min(latencies), 2),
    }


def _replay_session(repo: SqliteRepository, game_id: str) -> dict[str, Any]:
    """回放事件日志：``EventLog.replay`` 校验 + 纯事件重建状态 + 哈希一致断言。"""
    events = repo.read_events(game_id)
    save_seed = events[0].params["save_seed"]
    started = time.perf_counter()
    log = EventLog(save_seed)
    log.append(events)
    replayed = log.replay()
    initial = GameState.model_validate(replayed[0].result["initial_state"])
    rebuilt = initial.apply(replayed[1:])
    elapsed = time.perf_counter() - started
    live = repo.load(game_id)
    if live is None:  # pragma: no cover - 刚跑完的库不应缺失
        raise RuntimeError(f"回放阶段读不到存档: {game_id}")
    live_hash = state_hash(live.state)
    return {
        "events": len(replayed),
        "seconds": round(elapsed, 4),
        "seconds_per_1000_events": round(elapsed / max(len(replayed), 1) * 1000, 3),
        "state_hash": state_hash(rebuilt),
        "live_state_hash": live_hash,
        "hash_matches_live_state": state_hash(rebuilt) == live_hash,
        "seq_continuous": [event.seq for event in replayed] == list(range(1, len(replayed) + 1)),
    }


def run_benchmark(
    *,
    turns: int = DEFAULT_TURNS,
    seed: int = DEFAULT_SEED,
    db_path: Path | None = None,
    keep_db: bool = False,
    min_replay_events: int = MIN_REPLAY_EVENTS,
) -> dict[str, Any]:
    """跑一次长会话基线并返回 JSON 可序列化指标（脚本 CLI 与 pytest 共用）。

    ``min_replay_events`` 为回放事件数验收下限（默认 500，056 验收口径）；短回合的
    确定性回归（如 8 回合）可传 0 关闭该检查，避免小样本被当成长会话验收。
    """
    if turns < 1:
        raise ValueError(f"turns 必须为正整数，收到 {turns!r}")
    temp_dir: Path | None = None
    if db_path is None:
        temp_dir = Path(tempfile.mkdtemp(prefix="textgame-perf-056-"))
        db_path = temp_dir / "long-session.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    harness = build_harness(db_path, turns=turns)
    latencies: list[float] = []
    events_per_turn: list[int] = []
    turns_ok = 0
    failures: list[dict[str, Any]] = []
    try:
        tracemalloc.start()
        try:
            started = time.perf_counter()
            view = harness.orchestrator.create_game(
                CreateGameRequest(seed=seed, idempotency_key="perf-056-game-0001", scenario="m3")
            )
            version = view.version
            for index in range(turns):
                turn_started = time.perf_counter()
                result = harness.orchestrator.run(
                    TurnRequest(
                        game_id=view.game_id,
                        input=PLAYER_INPUT,
                        expected_version=version,
                        idempotency_key=f"perf-056-turn-{index:04d}",
                    )
                )
                latencies.append((time.perf_counter() - turn_started) * 1000)
                if result.ok:
                    turns_ok += 1
                    version = result.version
                else:  # pragma: no cover - 固定脚本理论上不应失败；失败时保留证据便于定位
                    failures.append(
                        {
                            "turn_index": index,
                            "error_code": result.error_code,
                            "text": result.text[:80],
                        }
                    )
                events_per_turn.append(len(result.events))
            total_seconds = time.perf_counter() - started
            peak_bytes = tracemalloc.get_traced_memory()[1]
        finally:
            # 异常路径也必须停止追踪，避免污染同进程后续测试的计时/内存口径。
            if tracemalloc.is_tracing():
                tracemalloc.stop()
        db_bytes = db_path.stat().st_size if db_path.exists() else 0
        replay = _replay_session(harness.repo, view.game_id)
        record = harness.repo.load(view.game_id)
        if record is None:  # pragma: no cover - 防御
            raise RuntimeError(f"跑完 {turns} 回合后读不到存档: {view.game_id}")
        final_hash = state_hash(record.state)
    finally:
        harness.engine.dispose()
        if temp_dir is not None and not keep_db:
            shutil.rmtree(temp_dir, ignore_errors=True)

    latencies_ms = _summarise_ms(latencies)
    window = min(20, len(latencies))
    first_mean_ms = round(statistics.fmean(latencies[:window]), 2) if window else 0.0
    last_mean_ms = round(statistics.fmean(latencies[-window:]), 2) if window else 0.0
    growth_ratio = round(last_mean_ms / first_mean_ms, 2) if first_mean_ms else 0.0
    events_min = max(min_replay_events, 0)
    checks = {
        "all_turns_ok": turns_ok == turns and not failures,
        "events_min_met": replay["events"] >= events_min,
        "events_per_turn_stable": len(set(events_per_turn)) == 1,
        "total_seconds_within_budget": total_seconds < THRESHOLDS["total_seconds_max"],
        "per_turn_p95_within_budget": latencies_ms["p95"] < THRESHOLDS["per_turn_p95_ms_max"],
        "peak_memory_within_budget": peak_bytes / (1024 * 1024)
        < THRESHOLDS["peak_tracemalloc_mb_max"],
        "replay_hash_matches": replay["hash_matches_live_state"],
        "replay_seq_continuous": replay["seq_continuous"],
    }
    return {
        "schema_version": 1,
        "kind": "long-session-perf",
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "environment": {
            "python": sys.version.split()[0],
            "python_implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "cpu_count": os.cpu_count(),
            "sqlite_version": sqlite3.sqlite_version,
        },
        "command": {
            "turns": turns,
            "seed": seed,
            "scenario": "m3",
            "player_input": PLAYER_INPUT,
            "npc_plan": "long-session-extension (2 scavengers x 2 move_to/turn)",
            "db": "temporary sqlite file",
            "tracemalloc": True,
        },
        "session": {
            "turns_requested": turns,
            "turns_ok": turns_ok,
            "failures": failures,
            "events_total": replay["events"],
            "events_per_turn": {
                "min": min(events_per_turn),
                "max": max(events_per_turn),
                "mean": round(statistics.fmean(events_per_turn), 2),
            },
            "total_seconds": round(total_seconds, 3),
            "per_turn_ms": latencies_ms,
            "latency_growth": {
                "first_20_mean_ms": first_mean_ms,
                "last_20_mean_ms": last_mean_ms,
                "growth_ratio": growth_ratio,
                "note": "长会话事件日志每回合全量读取，比值用于观察 O(n) 增长；无硬阈值",
            },
            "peak_tracemalloc_mb": round(peak_bytes / (1024 * 1024), 3),
            "db_bytes": db_bytes,
            "final_version": record.version,
            "final_turn": record.state.turn,
            "final_last_seq": record.last_seq,
            "save_seed": record.state.rng_state.save_seed,
            "state_hash": final_hash,
        },
        "replay": replay,
        "thresholds": {**THRESHOLDS, "events_min": float(events_min)},
        "checks": checks,
        "ok": all(checks.values()),
    }


def _write_json(payload: dict[str, Any], out_path: Path) -> None:
    """UTF-8 写 JSON 证据（父目录自动创建；不含时间敏感内容之外的东西）。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _print_summary(payload: dict[str, Any], out_path: Path | None) -> None:
    """打印一行机器可读摘要 + 关键指标，便于进度文档直接引用。"""
    session = payload["session"]
    replay = payload["replay"]
    status = "OK" if payload["ok"] else "THRESHOLD-FAIL"
    print(
        f"[perf-056] {status} turns={session['turns_ok']}/{session['turns_requested']} "
        f"events={session['events_total']} replay_events={replay['events']} "
        f"total={session['total_seconds']}s "
        f"per_turn(mean={session['per_turn_ms']['mean']}ms "
        f"p95={session['per_turn_ms']['p95']}ms) "
        f"growth={session['latency_growth']['growth_ratio']}x "
        f"peak_tracemalloc={session['peak_tracemalloc_mb']}MB "
        f"state_hash={session['state_hash'][:16]}… "
        f"replay_hash_match={replay['hash_matches_live_state']}"
    )
    print(
        f"[perf-056] replay {replay['events']} events in {replay['seconds']}s "
        f"({replay['seconds_per_1000_events']}s/1000 events)"
    )
    if out_path is not None:
        print(f"[perf-056] JSON: {out_path}")
    if not payload["ok"]:
        failed = [name for name, value in payload["checks"].items() if not value]
        print(f"[perf-056] failed checks: {', '.join(failed)}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：默认跑 120 回合并输出 JSON 摘要；``--strict`` 下阈值不达标 exit 1。"""
    parser = argparse.ArgumentParser(description="长会话性能基线（N4-056）")
    parser.add_argument("--turns", type=int, default=DEFAULT_TURNS, help="回合数（默认 120）")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="固定种子（默认 42）")
    parser.add_argument("--out", type=Path, default=None, help="JSON 证据输出路径（可选）")
    parser.add_argument(
        "--strict", action="store_true", help="阈值不达标时以退出码 1 结束（默认只报告）"
    )
    args = parser.parse_args(argv)
    if args.turns < 1:
        parser.error(f"--turns 必须为正整数，收到 {args.turns}")
    payload = run_benchmark(turns=args.turns, seed=args.seed)
    _print_summary(payload, args.out)
    if args.out is not None:
        _write_json(payload, args.out)
    return 0 if (payload["ok"] or not args.strict) else 1


if __name__ == "__main__":
    raise SystemExit(main())
