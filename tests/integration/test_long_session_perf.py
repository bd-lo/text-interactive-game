"""N4-056 长会话性能基线回归测试（轻量阈值，全量 pytest 内 ≤60s）。

为什么入仓：056 之前只有 R2 的 10 回合短会话预算（``test_perf_baseline.py``），
玩家最可能在意的「一局玩很久后变慢/内存涨」没有回归信号。本文件用**保守阈值**钉住
「120 回合 + 601 事件的离线确定性长会话」的数量级，防止后续实现引入 O(n²)/无界缓存。

断言口径（本机基线见 ``docs/design/long-session-perf.md``，阈值留 ≥5 倍余量防 CI 慢机）：
- 120 回合全部成功、每回合 5 条事件、事件总数 = 开局 1 + 120×5 = 601（稳定可复算）；
- 总耗时 < 60s、单回合 p95 < 3000ms、tracemalloc 峰值 < 512MB；
- 事件日志回放 601（≥500）条、``seq`` 连续、纯事件重建 ``state_hash`` == 实时投影哈希；
- 固定种子在两个独立临时库上重跑 8 回合 → ``state_hash``/事件数完全一致（哈希稳定）。

实现复用 ``scripts/perf-long-session.py`` 的生产同构装配（SQLite 临时库 + M3 多角色链路
+ 模板叙事 + 禁用 LLM），保证脚本与测试口径永远一致；不写 ``data/textgame.db``、不联网。

标记 ``slow``：与 ``tests/integration/test_perf_baseline.py`` 同口径，vibe 内循环用
``-m "not slow"`` 排除；全量 pytest 仍会执行本文件。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.slow]

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "perf-long-session.py"
#: 120 回合、每回合 5 条事件（玩家 talk_to 1 + 2 名拾荒者 × 2 条移动提案）。
_TURNS = 120
_EVENTS_PER_TURN = 5
_EXPECTED_EVENTS = 1 + _TURNS * _EVENTS_PER_TURN  # 601
#: 阈值（与脚本 ``THRESHOLDS`` 同源；此处显式列出便于失败信息直读）。
_TOTAL_SECONDS_MAX = 60.0
_PEAK_MB_MAX = 512.0
_PER_TURN_P95_MS_MAX = 3000.0


def _load_perf_module() -> ModuleType:
    """按路径加载脚本模块（``scripts/`` 非包、文件名含连字符，用 importlib 复用一份实现）。"""
    spec = importlib.util.spec_from_file_location("perf_long_session", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None, f"无法加载 {_SCRIPT_PATH}"
    module = importlib.util.module_from_spec(spec)
    # dataclass 需要模块在 sys.modules 中可查（``dataclasses._process_class``），
    # 否则 ``@dataclass(frozen=True)`` 在脚本加载期抛 AttributeError。
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


@pytest.fixture(scope="module")
def perf_module() -> ModuleType:
    """脚本模块（内含 ``run_benchmark`` 与阈值常量）。"""
    return _load_perf_module()


def _assert_metrics(
    result: dict[str, Any], *, turns: int, expected_events: int, min_replay_events: int = 500
) -> None:
    """对 ``run_benchmark`` 返回值做一次完整断言（基线用例与确定性用例共用）。"""
    session = result["session"]
    replay = result["replay"]
    assert result["ok"] is True, result["checks"]
    assert all(result["checks"].values()), result["checks"]
    assert session["turns_requested"] == turns
    assert session["turns_ok"] == turns
    assert session["failures"] == []
    assert session["events_total"] == expected_events
    assert session["events_per_turn"] == {
        "min": _EVENTS_PER_TURN,
        "max": _EVENTS_PER_TURN,
        "mean": float(_EVENTS_PER_TURN),
    }
    assert session["final_version"] == turns + 1
    assert session["final_turn"] == turns + 1
    assert session["final_last_seq"] == expected_events
    assert session["save_seed"] == "save-42"
    assert len(session["state_hash"]) == 64
    # 性能预算（宽松阈值：只防数量级退化）
    assert session["total_seconds"] < _TOTAL_SECONDS_MAX, session["per_turn_ms"]
    assert session["per_turn_ms"]["p95"] < _PER_TURN_P95_MS_MAX, session["per_turn_ms"]
    assert 0 < session["peak_tracemalloc_mb"] < _PEAK_MB_MAX, session["peak_tracemalloc_mb"]
    assert session["db_bytes"] > 0
    # 回放：≥500 事件（长会话验收）、seq 连续、纯事件重建状态 == 实时投影哈希
    assert replay["events"] == expected_events
    assert replay["events"] >= min_replay_events
    assert replay["seq_continuous"] is True
    assert replay["hash_matches_live_state"] is True
    assert replay["state_hash"] == replay["live_state_hash"] == session["state_hash"]


def test_long_session_120_turns_baseline(perf_module: ModuleType, tmp_path: Path) -> None:
    """120 回合长会话：全部成功、601 事件、耗时/内存/回放哈希全部达标。"""
    result = perf_module.run_benchmark(turns=_TURNS, seed=42, db_path=tmp_path / "long-session.db")
    _assert_metrics(result, turns=_TURNS, expected_events=_EXPECTED_EVENTS)
    session = result["session"]
    print(
        f"[perf-056] turns={session['turns_ok']} events={session['events_total']} "
        f"total={session['total_seconds']}s "
        f"p95={session['per_turn_ms']['p95']}ms "
        f"peak={session['peak_tracemalloc_mb']}MB "
        f"replay={result['replay']['events']} events "
        f"{result['replay']['seconds']}s hash={session['state_hash'][:16]}…"
    )


def test_fixed_seed_state_hash_is_deterministic(perf_module: ModuleType, tmp_path: Path) -> None:
    """固定种子在两个独立临时库上重跑：事件数相同、``state_hash`` 完全一致。"""
    turns = 8
    expected_events = 1 + turns * _EVENTS_PER_TURN
    first = perf_module.run_benchmark(
        turns=turns, seed=42, db_path=tmp_path / "first.db", min_replay_events=0
    )
    second = perf_module.run_benchmark(
        turns=turns, seed=42, db_path=tmp_path / "second.db", min_replay_events=0
    )
    _assert_metrics(first, turns=turns, expected_events=expected_events, min_replay_events=0)
    _assert_metrics(second, turns=turns, expected_events=expected_events, min_replay_events=0)
    assert first["session"]["state_hash"] == second["session"]["state_hash"]
    assert first["replay"]["state_hash"] == second["replay"]["state_hash"]
