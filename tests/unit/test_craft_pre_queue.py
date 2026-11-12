"""R-19 单元测试：pre-queue 草稿端口 + CraftService 两段式（生成一次 / 执行纯函数）。

覆盖：
- ``orchestration.pre_queue``：仅 ``craft_item(mode="free")`` 且工具门实现 pre-queue 端口、
  ``free_craft_ready=True`` 时才生成；不适用/未装配/异常/失败 Result 都收敛为确定性结果；
- ``CraftService.prepare_free_craft``：一次生成（含失败载荷），零事件零写入；
- ``CraftService.craft(prepared_craft=...)``：执行路径零 LLM（把 FakeLLM 脚本清空后仍成功），
  失败载荷原样拒绝且不重试；``ctx.caller="resolver"`` 无载荷时绝不回落到生成器；
- 开关关闭（默认）：``free_craft_ready=False``、prepare 返回 NOT_ALLOWED、零 LLM 调用；
- 旧路径（``caller="player"`` 直接调用）：服务内仍生成一次，M2 行为不回归。
"""

from __future__ import annotations

from typing import Any

from fakes import FakeLLM
from textgame.agents.craft_agent import CraftAgent
from textgame.domain.crafting import CraftMode, CraftRequest
from textgame.domain.models import GameState
from textgame.domain.results import ErrorCode, Result
from textgame.domain.scenario import PLAYER_ID, build_m4_state
from textgame.orchestration.pre_queue import (
    is_free_craft_proposal,
    prepare_free_craft_draft,
)
from textgame.services.craft_service import CraftService
from textgame.services.free_craft import FreeCraftSwitch
from textgame.services.recipe_catalog import RecipeCatalog
from textgame.services.tool_context import ToolContext

_SEED = "save-42"
_DESCRIPTION = "把铁锭和铜线做成一个简易电击棒"


def _draft(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "item_name": "简易电击棒",
        "output_template_id": "item.makeshift_taser",
        "inputs": [
            {"template_id": "item.iron_ingot", "qty": 1, "role": "material"},
            {"template_id": "item.copper_wire", "qty": 1, "role": "material"},
        ],
        "station": None,
        "steps": ["把铜线缠在铁锭上", "固定接头"],
    }
    payload.update(overrides)
    return payload


def _state() -> GameState:
    return build_m4_state(save_seed=_SEED)


def _svc(fake_llm: FakeLLM, *, enabled: bool = True) -> CraftService:
    return CraftService(
        RecipeCatalog.load_builtin(),
        generator=CraftAgent(fake_llm),
        free_craft_enabled=enabled,
    )


def _ctx(
    state: GameState, *, caller: str = "player", key: str = "r19-unit-craft-0001"
) -> ToolContext:
    return ToolContext(
        actor_id=PLAYER_ID,
        turn_id=state.turn + 1,
        save_seed=state.rng_state.save_seed,
        idempotency_key=key,
        caller=caller,  # type: ignore[arg-type]
    )


def _request() -> CraftRequest:
    return CraftRequest(mode=CraftMode.FREE, free_description=_DESCRIPTION)


# ---------------------------------------------------------------------------
# CraftService：pre-queue 生成一次
# ---------------------------------------------------------------------------
def test_prepare_free_craft_generates_once_and_returns_payload() -> None:
    fake_llm = FakeLLM()
    fake_llm.reset([_draft()])
    service = _svc(fake_llm)
    state = _state()

    result = service.prepare_free_craft(_ctx(state), state, _request())

    assert result.ok is True
    prepared = (result.data or {})["prepared"]
    assert prepared["ok"] is True
    assert prepared["draft"] == _draft()
    assert prepared["prompt_version"] == "craft@v1"
    assert len(fake_llm.calls) == 1
    assert result.events == []


def test_prepare_free_craft_schema_violation_payload_zero_events() -> None:
    fake_llm = FakeLLM()
    fake_llm.reset(["not-json", "still-not-json"])
    service = _svc(fake_llm)
    state = _state()

    result = service.prepare_free_craft(_ctx(state), state, _request())

    assert result.ok is True, "失败草稿也收敛为成功 Result（载荷 ok=False）"
    prepared = (result.data or {})["prepared"]
    assert prepared["ok"] is False
    assert prepared["error_code"] == ErrorCode.SCHEMA_VIOLATION.value
    assert len(fake_llm.calls) == 2  # 生成 + 一次修复，仅此而已
    assert result.events == []


# ---------------------------------------------------------------------------
# CraftService：执行路径零 LLM
# ---------------------------------------------------------------------------
def test_execute_prepared_draft_makes_no_llm_call() -> None:
    """执行已生成草稿时把 LLM 脚本清空也成功 → 证明执行路径不触碰生成器。"""
    fake_llm = FakeLLM()
    fake_llm.reset([_draft()])
    service = _svc(fake_llm)
    state = _state()
    prepared = (service.prepare_free_craft(_ctx(state), state, _request()).data or {})["prepared"]
    fake_llm.reset([])  # 清空脚本与调用记录：执行路径若调 LLM 必然 LLM_UNAVAILABLE

    result = service.craft(
        _ctx(state, caller="resolver"),
        state,
        _request(),
        idempotency_key="res:1:p_000001_001",
        prepared_craft=prepared,
    )

    assert result.ok is True
    assert len(fake_llm.calls) == 0, "执行路径不得调用 LLM"
    assert len(result.events) == 1
    assert result.events[0].event_type is not None
    assert result.events[0].event_type.value == "CRAFT_GENERATED"


def test_execute_failed_payload_rejects_without_llm_retry() -> None:
    fake_llm = FakeLLM()
    fake_llm.reset(["not-json", "still-not-json"])
    service = _svc(fake_llm)
    state = _state()
    prepared = (service.prepare_free_craft(_ctx(state), state, _request()).data or {})["prepared"]
    fake_llm.reset([])

    first = service.craft(
        _ctx(state, caller="resolver"),
        state,
        _request(),
        idempotency_key="res:1:p_000001_001",
        prepared_craft=prepared,
    )
    second = service.craft(
        _ctx(state, caller="resolver"),
        state,
        _request(),
        idempotency_key="res:1:p_000001_001",
        prepared_craft=prepared,
    )

    for result in (first, second):
        assert result.ok is False
        assert result.error_code is ErrorCode.SCHEMA_VIOLATION
        assert result.events == []
    assert len(fake_llm.calls) == 0, "失败载荷复用也不得重试 LLM"


def test_resolver_caller_without_prepared_payload_never_calls_generator() -> None:
    """防御性红线：Resolver 调用方无 pre-queue 载荷时直接拒绝，绝不回落生成器。"""
    fake_llm = FakeLLM()
    fake_llm.reset([_draft()])
    service = _svc(fake_llm)
    state = _state()

    result = service.craft(
        _ctx(state, caller="resolver"),
        state,
        _request(),
        idempotency_key="res:1:p_000001_001",
    )

    assert result.ok is False
    assert result.error_code is ErrorCode.INVALID_INPUT
    assert result.events == []
    assert len(fake_llm.calls) == 0


def test_legacy_player_path_still_generates_once() -> None:
    """M2/直接 ToolGate 调用（caller=player）保持旧行为：服务内生成一次并执行。"""
    fake_llm = FakeLLM()
    fake_llm.reset([_draft()])
    service = _svc(fake_llm)
    state = _state()

    result = service.craft(
        _ctx(state, caller="player"),
        state,
        _request(),
        idempotency_key="r19-unit-craft-0001",
    )

    assert result.ok is True
    assert len(fake_llm.calls) == 1
    assert len(result.events) == 1


def test_disabled_switch_no_prepare_and_no_llm() -> None:
    fake_llm = FakeLLM()
    fake_llm.reset([_draft()])
    service = CraftService(
        RecipeCatalog.load_builtin(),
        generator=CraftAgent(fake_llm),
        free_craft_switch=FreeCraftSwitch(enabled=False),
    )
    state = _state()

    assert service.free_craft_ready is False
    prepared = service.prepare_free_craft(_ctx(state), state, _request())
    executed = service.craft(_ctx(state), state, _request(), idempotency_key="r19-unit-craft-0002")

    assert prepared.ok is False
    assert prepared.error_code is ErrorCode.NOT_ALLOWED
    assert executed.ok is False
    assert executed.error_code is ErrorCode.NOT_ALLOWED
    assert len(fake_llm.calls) == 0


# ---------------------------------------------------------------------------
# pre_queue 端口判定（纯函数）
# ---------------------------------------------------------------------------
class _StubGate:
    """pre-queue 最小端口替身：可配置 ready / 载荷 / 异常 / 失败 Result。"""

    def __init__(
        self,
        *,
        ready: bool = True,
        prepared: dict[str, Any] | None = None,
        failure: Result | None = None,
        raises: bool = False,
    ) -> None:
        self._ready = ready
        self._prepared = prepared if prepared is not None else {"ok": True}
        self._failure = failure
        self._raises = raises
        self.calls = 0

    @property
    def free_craft_ready(self) -> bool:
        return self._ready

    def prepare_free_craft(
        self, *, args: dict[str, Any], ctx: ToolContext, state: GameState
    ) -> Result:
        self.calls += 1
        if self._raises:
            raise RuntimeError("boom")
        if self._failure is not None:
            return self._failure
        return Result.success(data={"prepared": self._prepared})


def test_prepare_free_craft_draft_only_for_free_craft() -> None:
    gate = _StubGate()
    state = _state()
    ctx = _ctx(state)

    assert (
        prepare_free_craft_draft(
            gate=gate, ctx=ctx, state=state, tool="craft_item", args={"mode": "recipe"}
        )
        is None
    )
    assert (
        prepare_free_craft_draft(
            gate=gate, ctx=ctx, state=state, tool="move_to", args={"direction": "north"}
        )
        is None
    )
    assert gate.calls == 0
    assert is_free_craft_proposal("craft_item", {"mode": "free"}) is True


def test_prepare_free_craft_draft_skips_unwired_or_disabled_gate() -> None:
    state = _state()
    ctx = _ctx(state)
    args = {"mode": "free", "free_description": _DESCRIPTION}

    assert (
        prepare_free_craft_draft(gate=object(), ctx=ctx, state=state, tool="craft_item", args=args)
        is None
    )
    disabled = _StubGate(ready=False)
    assert (
        prepare_free_craft_draft(gate=disabled, ctx=ctx, state=state, tool="craft_item", args=args)
        is None
    )
    assert disabled.calls == 0


def test_prepare_free_craft_draft_translates_payloads_and_failures() -> None:
    state = _state()
    ctx = _ctx(state)
    args = {"mode": "free", "free_description": _DESCRIPTION}
    payload = {"ok": True, "draft": _draft(), "prompt_version": "craft@v1"}

    ok = prepare_free_craft_draft(
        gate=_StubGate(prepared=payload), ctx=ctx, state=state, tool="craft_item", args=args
    )
    assert ok == payload

    failed = prepare_free_craft_draft(
        gate=_StubGate(failure=Result.fail(ErrorCode.LLM_UNAVAILABLE, "offline")),
        ctx=ctx,
        state=state,
        tool="craft_item",
        args=args,
    )
    assert failed == {
        "ok": False,
        "error_code": ErrorCode.LLM_UNAVAILABLE.value,
        "message": "offline",
    }

    crashed = prepare_free_craft_draft(
        gate=_StubGate(raises=True), ctx=ctx, state=state, tool="craft_item", args=args
    )
    assert crashed is not None
    assert crashed["ok"] is False
    assert crashed["error_code"] == ErrorCode.INTERNAL.value
