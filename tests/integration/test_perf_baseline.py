"""R2 QA-1：性能 / 容量 / 并发基线（`slow` 标记，含定时断言）。

为什么入仓而不是只做一次性探针：
- R2 会改动叙事/解析/状态派生（D2/D7/D8）与建议算法，玩家最容易感知的退化是「点一下要等」；
- 本文件用**保守预算**（CI 慢机安全）钉住三条基线，防止后续实现悄悄引入 O(n²)/全表扫描：
  1. 单回合 HTTP 延迟：3 seed × 10 回合的 p95 < 200ms（R2 开跑本地实测 p95≤59ms，留 3 倍余量）；
  2. 单存档容量：10 回合（7 事件）DB 增量 ≤ 64KiB（实测 +40,960B）；
  3. 同 Idempotency-Key 并发：2 线程 × 20 次 → 全部 200/ok，`turn_id` 唯一、事件只 +1。
- 全部走 `tmp_path` SQLite + `main._build_app` + `TestClient`，禁网（conftest autouse guard）。

标记为 `slow`（pyproject 已有 marker）：内循环用 `uv run pytest -q -m "not slow"` 排除；
基线验收用 `uv run pytest -q -m slow tests/integration/test_perf_baseline.py`。
运行相对时间受机器负载影响，预算刻意宽松；本文件只防「数量级退化」，不追求微基准精度。
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from textgame import console
from textgame.config.settings import get_settings

pytestmark = [pytest.mark.integration, pytest.mark.slow]

#: 与 R1 QA 审计相同的 10 回合脚本：读/读/说/拿/拿/用/走/拿/读/读（6 次写 + 1 次 create = 7 事件）。
_SCRIPT: tuple[str, ...] = (
    "环顾四周",
    "查看背包",
    "对老哨兵问好",
    "拿起火把",
    "拿起治疗药水",
    "使用治疗药水",
    "向北走进地窖",
    "拿起锈剑",
    "查看背包",
    "环顾四周",
)
_SEEDS = (42, 7, 2026)
#: 单回合 p95 预算（本文件只防数量级退化；本地基线 p95≈21–59ms）。
_TURN_P95_BUDGET_MS = 200.0
#: 单存档 DB 增量预算（实测 +40,960B；留 1.5 倍余量）。
_DB_DELTA_BUDGET_BYTES = 64 * 1024
#: 同键并发：2 线程 × 20 = 40 次请求。
_CONCURRENCY_THREADS = 2
_CONCURRENCY_PER_THREAD = 20


def _build_app(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """把 ``TEXTGAME_DB_URL`` 指向临时文件库后重建生产组合根（不联网）。"""
    monkeypatch.setenv("TEXTGAME_DB_URL", f"sqlite:///{db_path.as_posix()}")
    get_settings.cache_clear()
    import textgame.main as main_module

    console.dispose_app_engine(main_module.app)
    return main_module._build_app()


@dataclass
class Baseline:
    """基线用的 HTTP 客户端 + 临时 SQLite 路径。"""

    client: TestClient
    db_path: Path


@pytest.fixture
def baseline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Baseline]:
    """每个用例一套独立临时库 + 生产组合根；用例结束释放 Engine。"""
    db_path = tmp_path / "perf.db"
    app = _build_app(db_path, monkeypatch)
    try:
        with TestClient(app) as client:
            yield Baseline(client=client, db_path=db_path)
    finally:
        console.dispose_app_engine(app)


def _percentile(values: list[float], quantile: float) -> float:
    """nearest-rank 百分位（样本少时比插值更诚实，和 R1 探针口径一致）。"""
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round(quantile * (len(ordered) - 1))))
    return ordered[index]


def _db_bytes(path: Path) -> int:
    """SQLite 主文件字节数（本工程默认 journal 模式无 -wal 常驻）。"""
    return path.stat().st_size if path.exists() else 0


def _create_game(client: TestClient, *, seed: int, key: str) -> dict[str, Any]:
    """``POST /api/v1/games``（201 断言）。"""
    response = client.post("/api/v1/games", headers={"Idempotency-Key": key}, json={"seed": seed})
    assert response.status_code == 201, response.text
    payload: dict[str, Any] = response.json()
    return payload


def _post_turn(
    client: TestClient, *, game_id: str, text: str, expected_version: int, key: str
) -> tuple[dict[str, Any], float]:
    """``POST /api/v1/turns``（200 断言），返回 (TurnResult, 墙钟毫秒)。"""
    started = time.perf_counter()
    response = client.post(
        "/api/v1/turns",
        headers={"Idempotency-Key": key},
        json={"game_id": game_id, "input": text, "expected_version": expected_version},
    )
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert response.status_code == 200, response.text
    payload: dict[str, Any] = response.json()
    return payload, elapsed_ms


def _replay_count(client: TestClient, game_id: str) -> int:
    """``GET /replay`` 的事件总数（只读，不推进版本）。"""
    response = client.get(f"/api/v1/games/{game_id}/replay", params={"from_seq": 1, "limit": 500})
    assert response.status_code == 200, response.text
    events: list[dict[str, Any]] = response.json()["events"]
    return len(events)


def test_three_seeds_ten_turn_latency_and_db_growth(baseline: Baseline) -> None:
    """3 seed × 10 回合：全成功、p95 < 200ms、单存档 DB 增量 ≤ 64KiB、事件数 = 7。"""
    client = baseline.client
    initial_bytes = _db_bytes(baseline.db_path)
    assert initial_bytes > 0, "组合根建表后应有非空 SQLite 文件"

    latencies: list[float] = []
    db_deltas: list[int] = []
    for seed in _SEEDS:
        before_bytes = _db_bytes(baseline.db_path)
        created = _create_game(client, seed=seed, key=f"qa-perf-game-{seed}")
        game_id = created["game_id"]
        version = created["version"]
        for index, text in enumerate(_SCRIPT):
            result, elapsed_ms = _post_turn(
                client,
                game_id=game_id,
                text=text,
                expected_version=version,
                key=f"qa-perf-{seed}-turn-{index:02d}",
            )
            latencies.append(elapsed_ms)
            assert result["ok"] is True, (seed, text, result.get("error_code"))
            version = result["version"]
        db_delta = _db_bytes(baseline.db_path) - before_bytes
        db_deltas.append(db_delta)
        assert db_delta <= _DB_DELTA_BUDGET_BYTES, (
            f"seed={seed} 单存档 10 回合 DB 增量 {db_delta}B 超过预算 "
            f"{_DB_DELTA_BUDGET_BYTES}B（快照/事件膨胀？）"
        )
        assert _replay_count(client, game_id) == 7

    assert len(latencies) == len(_SEEDS) * len(_SCRIPT) == 30
    p50 = _percentile(latencies, 0.50)
    p95 = _percentile(latencies, 0.95)
    assert p95 < _TURN_P95_BUDGET_MS, (
        f"单回合 p95={p95:.1f}ms 超过预算 {_TURN_P95_BUDGET_MS}ms（p50={p50:.1f}ms；"
        f"3 seed×10 回合共 {len(latencies)} 样本）"
    )
    print(
        f"[perf-baseline] turns={len(latencies)} p50={p50:.2f}ms p95={p95:.2f}ms "
        f"max={max(latencies):.2f}ms db_deltas={db_deltas}B"
    )


def test_same_idempotency_key_concurrency_commits_once(baseline: Baseline) -> None:
    """同 Idempotency-Key 2 线程×20 次：全部 200/ok、turn_id 唯一、事件只 +1、版本只 +1。"""
    client = baseline.client
    created = _create_game(client, seed=42, key="qa-perf-conc-game-0001")
    game_id = created["game_id"]
    assert created["version"] == 1
    assert _replay_count(client, game_id) == 1

    payload = {"game_id": game_id, "input": "拿起火把", "expected_version": 1}

    def _fire(_: int) -> dict[str, Any]:
        """发一次同键同参的回合请求（共享 TestClient）。"""
        response = client.post(
            "/api/v1/turns",
            headers={"Idempotency-Key": "qa-perf-conc-turn-0001"},
            json=payload,
        )
        body: dict[str, Any] = response.json()
        return {
            "http": response.status_code,
            "ok": body.get("ok"),
            "error_code": body.get("error_code"),
            "turn_id": body.get("turn_id"),
            "version": body.get("version"),
        }

    attempts = _CONCURRENCY_THREADS * _CONCURRENCY_PER_THREAD
    with ThreadPoolExecutor(max_workers=_CONCURRENCY_THREADS) as pool:
        results = list(pool.map(_fire, range(attempts)))

    assert len(results) == attempts == 40
    assert all(result["http"] == 200 for result in results), results
    assert all(result["ok"] is True for result in results), results
    assert {result["turn_id"] for result in results} == {2}
    assert {result["version"] for result in results} == {2}
    # 只提交一次：事件表 = game_created + ITEM_TAKEN；版本只推进 1
    assert _replay_count(client, game_id) == 2
    game_view = client.get(f"/api/v1/games/{game_id}")
    assert game_view.status_code == 200
    assert game_view.json()["version"] == 2
    assert game_view.json()["turn"] == 2
