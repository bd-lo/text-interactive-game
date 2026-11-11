"""M5-022（缩范围）并发同 ``Idempotency-Key`` 回合一致性（D-M4-15/G-1）。

竞态窗口（修复前）：``run()`` 先查幂等表 → 载入存档 → CAS 提交 → 最后写幂等记录；两个
线程若都在首请求写幂等记录**之前**通过首次查表，就会并发执行，后到者可能返回
CONFLICT/INVALID_INPUT 而非首结果（D-M4-15：只提交一次，但瞬态响应不一致）。

本文件用「首次 parse 阻塞 + Event 放行」把窗口显式放大（真实双线程，不靠 sleep 猜时序）：
若同键锁缺失，第二个线程会并发进入解析并抢先提交，解析调用数 > 1；修复后同键请求
串行，解析只发生一次、后到者读到首请求的幂等记录并原样重放。全部用例只走内存仓储，
零网络；仅在放行前用极短等待确认「后到者没有进入临界区」。
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from textgame.domain.models import PerceptionSlice
from textgame.domain.results import ErrorCode
from textgame.orchestration.direct_gate import DirectGate
from textgame.orchestration.dto import TurnRequest
from textgame.orchestration.intent import DeterministicIntentParser
from textgame.orchestration.turn_orchestrator import (
    CreateGameRequest,
    TurnOrchestrator,
    _idempotency_lock_index,
)
from textgame.persistence.memory import InMemoryRepository
from textgame.services.action_service import ActionService
from textgame.services.game_engine import GameEngine
from textgame.services.query_service import QueryService

_JOIN_TIMEOUT = 5.0


def _build(parser: Any) -> tuple[TurnOrchestrator, InMemoryRepository]:
    """按 ``main.py`` 口径装配 M2 单角色编排器（内存仓储、注入测试解析器）。"""
    repo = InMemoryRepository()
    engine = GameEngine(repo)
    query = QueryService(repo)
    action = ActionService(query)
    orchestrator = TurnOrchestrator(
        repo=repo,
        engine=engine,
        query=query,
        action=action,
        parser=parser,
        gate=DirectGate(action, query),
    )
    return orchestrator, repo


def _new_game(orchestrator: TurnOrchestrator, key: str = "game-key-conc-0001") -> str:
    """开局并返回 game_id（固定 seed 42，保证叙事/事件确定性）。"""
    return orchestrator.create_game(CreateGameRequest(seed=42, idempotency_key=key)).game_id


def _turn(game_id: str, text: str, *, key: str, expected_version: int = 1) -> TurnRequest:
    """构造回合请求（同键/异参由 ``text``/``expected_version`` 决定参数哈希）。"""
    return TurnRequest(
        game_id=game_id,
        text=text,
        expected_version=expected_version,
        idempotency_key=key,
    )


class _BlockingParser:
    """首次 ``parse`` 置位 ``entered`` 并阻塞到 ``release``；记录真实解析调用次数。"""

    def __init__(self) -> None:
        """包装确定性解析器；事件对象供用例做「后到者是否进入临界区」断言。"""
        self._base = DeterministicIntentParser()
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0
        self._guard = threading.Lock()

    def parse(self, text: str, *, perception: PerceptionSlice, ctx: Any = None) -> Any:
        """首次调用阻塞（放大竞态窗口），之后直接走确定性解析。"""
        with self._guard:
            self.calls += 1
            first = self.calls == 1
        if first:
            self.entered.set()
            if not self.release.wait(timeout=_JOIN_TIMEOUT):
                raise AssertionError("测试解析器未被放行（并发窗口未按预期打开）")
        return self._base.parse(text, perception=perception, ctx=ctx)


class _BarrierParser:
    """N 个并发 ``parse`` 必须同时到达屏障：用于证明不同键**没有**被串行化。"""

    def __init__(self, parties: int) -> None:
        """按并发数创建屏障（超时保护避免测试挂死）与确定性解析器。"""
        self._base = DeterministicIntentParser()
        self._barrier = threading.Barrier(parties, timeout=_JOIN_TIMEOUT)
        self.broken = False
        self.calls = 0

    def parse(self, text: str, *, perception: PerceptionSlice, ctx: Any = None) -> Any:
        """先等齐所有并发调用再解析；被串行化时屏障超时（``broken=True``）。"""
        self.calls += 1
        try:
            self._barrier.wait()
        except threading.BrokenBarrierError:
            self.broken = True
            raise
        return self._base.parse(text, perception=perception, ctx=ctx)


class _FailOnceParser:
    """首次 ``parse`` 抛异常（验证异常路径后同键锁仍被释放）。"""

    def __init__(self) -> None:
        """包装确定性解析器并记录调用次数。"""
        self._base = DeterministicIntentParser()
        self.calls = 0

    def parse(self, text: str, *, perception: PerceptionSlice, ctx: Any = None) -> Any:
        """第一次抛 ``RuntimeError``，之后正常解析。"""
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("boom: parser failure for lock-release test")
        return self._base.parse(text, perception=perception, ctx=ctx)


def test_concurrent_same_key_same_body_returns_first_result_once() -> None:
    """两线程同键同参：同一 turn_id/version/文本，事件只写一次，解析只发生一次。"""
    parser = _BlockingParser()
    orchestrator, repo = _build(parser)
    game_id = _new_game(orchestrator)
    request = _turn(game_id, "拿起火把", key="turn-key-conc-0001")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(orchestrator.run, request)
        assert parser.entered.wait(timeout=_JOIN_TIMEOUT), "首请求未进入解析"
        second = pool.submit(orchestrator.run, request)
        time.sleep(0.1)  # 后到者若未被同键锁挡住，此时已经进入解析（calls 会变 2）
        assert parser.calls == 1, "同键后到者绕过了临界区（锁未生效）"
        parser.release.set()
        first_result = first.result(timeout=_JOIN_TIMEOUT)
        second_result = second.result(timeout=_JOIN_TIMEOUT)

    assert first_result.ok and second_result.ok
    assert first_result == second_result
    assert first_result.turn_id == 2
    assert first_result.version == 2
    assert len(repo.read_events(game_id)) == 2  # game_created + ITEM_TAKEN，只写一次


def test_concurrent_same_key_different_body_conflicts_without_extra_writes() -> None:
    """两线程同键异参：先到者成功、后到者 CONFLICT（一致错误码），事件零额外写入。"""
    parser = _BlockingParser()
    orchestrator, repo = _build(parser)
    game_id = _new_game(orchestrator)
    first_req = _turn(game_id, "拿起火把", key="turn-key-conc-0002")
    second_req = _turn(game_id, "拿起治疗药水", key="turn-key-conc-0002")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(orchestrator.run, first_req)
        assert parser.entered.wait(timeout=_JOIN_TIMEOUT), "首请求未进入解析"
        second = pool.submit(orchestrator.run, second_req)
        time.sleep(0.1)
        assert parser.calls == 1
        parser.release.set()
        results = [first.result(timeout=_JOIN_TIMEOUT), second.result(timeout=_JOIN_TIMEOUT)]

    winners = [result for result in results if result.ok]
    losers = [result for result in results if not result.ok]
    assert len(winners) == 1 and winners[0].version == 2
    assert len(losers) == 1
    assert losers[0].error_code is ErrorCode.CONFLICT
    assert losers[0].events == []
    assert len(repo.read_events(game_id)) == 2  # 只提交一次，无额外事件
    loaded = repo.load(game_id)
    assert loaded is not None and loaded.version == 2


def test_serial_retry_reuses_first_result_without_second_write() -> None:
    """串行重试（同键同参）：原样复用首结果，事件数不增。"""
    orchestrator, repo = _build(DeterministicIntentParser())
    game_id = _new_game(orchestrator)
    request = _turn(game_id, "拿起火把", key="turn-key-conc-0003")

    first = orchestrator.run(request)
    second = orchestrator.run(request)

    assert first.ok and second.ok
    assert second == first
    assert len(repo.read_events(game_id)) == 2


def test_different_keys_are_not_serialized() -> None:
    """不同键并发：不被同一把分段锁串行（两解析调用同时到达屏障）。"""
    parser = _BarrierParser(parties=2)
    orchestrator, repo = _build(parser)
    game_id = _new_game(orchestrator)
    key_a, key_b = "turn-key-par-a-01", "turn-key-par-b-01"
    # 断言两键落在不同分段：否则本用例会因「合法串行」而误报失败
    assert _idempotency_lock_index(f"turn:{game_id}:{key_a}") != _idempotency_lock_index(
        f"turn:{game_id}:{key_b}"
    )
    requests = [
        _turn(game_id, "环顾四周", key=key_a),
        _turn(game_id, "环顾四周", key=key_b),
    ]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(orchestrator.run, requests))

    assert parser.calls == 2
    assert parser.broken is False, "不同键被串行化（屏障超时）"
    assert all(result.ok for result in results)  # 只读回合：均成功且不推进版本
    assert len(repo.read_events(game_id)) == 1


def test_parser_exception_releases_key_lock() -> None:
    """异常路径释放锁：首请求内部异常后，同键请求仍能立即执行而不是死锁。"""
    parser = _FailOnceParser()
    orchestrator, repo = _build(parser)
    game_id = _new_game(orchestrator)
    request = _turn(game_id, "环顾四周", key="turn-key-conc-0005")

    failed = orchestrator.run(request)
    assert failed.ok is False
    assert failed.error_code is ErrorCode.INTERNAL

    with ThreadPoolExecutor(max_workers=1) as pool:
        recovered = pool.submit(orchestrator.run, request).result(timeout=_JOIN_TIMEOUT)

    assert recovered.ok is True
    assert parser.calls == 2
    assert len(repo.read_events(game_id)) == 1  # 异常回合零写入，恢复回合只读零写入


def test_four_concurrent_same_key_requests_all_replay_same_result() -> None:
    """四线程同键同参：全部返回同一结果，解析/提交各一次。"""
    parser = _BlockingParser()
    orchestrator, repo = _build(parser)
    game_id = _new_game(orchestrator, key="game-key-conc-0002")
    request = _turn(game_id, "拿起火把", key="turn-key-conc-0006")

    with ThreadPoolExecutor(max_workers=4) as pool:
        first = pool.submit(orchestrator.run, request)
        assert parser.entered.wait(timeout=_JOIN_TIMEOUT), "首请求未进入解析"
        rest = [pool.submit(orchestrator.run, request) for _ in range(3)]
        time.sleep(0.1)
        assert parser.calls == 1
        parser.release.set()
        results = [first.result(timeout=_JOIN_TIMEOUT)]
        results.extend(future.result(timeout=_JOIN_TIMEOUT) for future in rest)

    assert all(result.ok for result in results)
    assert {result.turn_id for result in results} == {2}
    assert {result.version for result in results} == {2}
    assert all(result == results[0] for result in results)
    assert len(repo.read_events(game_id)) == 2


class _IdempotentFailingRepo(InMemoryRepository):
    """幂等表写入必失败（R-03：状态主提交成功后不得报告回合失败）。"""

    def save_idempotent(self, record: Any) -> None:
        raise RuntimeError("idempotency storage unavailable")


def test_turn_succeeds_when_idempotent_write_fails() -> None:
    """R-03：commit 成功后幂等写失败 → run() 仍返回成功且事件只写一次。"""
    repo = _IdempotentFailingRepo()
    engine = GameEngine(repo)
    query = QueryService(repo)
    action = ActionService(query)
    orchestrator = TurnOrchestrator(
        repo=repo,
        engine=engine,
        query=query,
        action=action,
        parser=DeterministicIntentParser(),
        gate=DirectGate(action, query),
    )
    game_id = orchestrator.create_game(
        CreateGameRequest(seed=42, idempotency_key="game-key-idem-fail-0001")
    ).game_id

    result = orchestrator.run(_turn(game_id, "拿起火把", key="turn-key-idem-fail-0001"))

    assert result.ok is True
    assert result.version == 2
    assert len(repo.read_events(game_id)) == 2


# ---------------------------------------------------------------------------
# R-04：创建存档的同键并发
# ---------------------------------------------------------------------------
class _BlockingCreateEngine(GameEngine):
    """首次 ``create_game`` 阻塞（放大 check-then-act 窗口），记录真实调用次数。"""

    def __init__(self, repo: InMemoryRepository) -> None:
        super().__init__(repo)
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0
        self._guard = threading.Lock()

    def create_game(self, **kwargs: Any) -> Any:
        with self._guard:
            self.calls += 1
            first = self.calls == 1
        if first:
            self.entered.set()
            if not self.release.wait(timeout=_JOIN_TIMEOUT):
                raise AssertionError("创建请求未被放行（并发窗口未打开）")
        return super().create_game(**kwargs)


def test_concurrent_same_key_create_game_makes_one_entity() -> None:
    """R-04：两个线程同键同参建局 → 只创建一个存档，返回同一 game_id。"""
    repo = InMemoryRepository()
    engine = _BlockingCreateEngine(repo)
    query = QueryService(repo)
    action = ActionService(query)
    orchestrator = TurnOrchestrator(
        repo=repo,
        engine=engine,
        query=query,
        action=action,
        parser=DeterministicIntentParser(),
        gate=DirectGate(action, query),
    )
    request = CreateGameRequest(seed=42, idempotency_key="game-key-conc-create-0001")

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(orchestrator.create_game, request)
        assert engine.entered.wait(timeout=_JOIN_TIMEOUT), "首请求未进入建局"
        second = pool.submit(orchestrator.create_game, request)
        time.sleep(0.1)
        assert engine.calls == 1, "同键后到者绕过了创建锁（重复建局）"
        engine.release.set()
        views = [first.result(timeout=_JOIN_TIMEOUT), second.result(timeout=_JOIN_TIMEOUT)]

    assert views[0] == views[1]
    assert engine.calls == 1
    assert repo.count_games() == 1
