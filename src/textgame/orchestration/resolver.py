"""``Resolver``：多角色提案的确定性裁决（契约 ``.work/m3-contract.md`` §3.3、第 11 章 §11.2）。

架构红线（第 11 章 §11.1–§11.3）：**多个角色 Agent 只生产提案，``ProposalQueue`` 收集并排序，
``Resolver`` 统一裁决，唯一写入者 ``GameEngine`` 原子提交**。``Resolver`` 是**纯函数**：

- 不调模型（裁判证据由编排层调用 ``JudgeAgent`` 并 Schema 校验后随 ``judgements`` 传入；
  本模块只消费证据，M5E-2 打开 ``needs_judge`` 分支，但**只在分支内**读取 ``judgements``）；
- 不写库、不读仓储、不读时钟、无随机；同一 ``(entries, snapshot, judgements)`` 必得同一
  ``list[Resolution]``（否则固定种子回放会漂移，第 11 章 §11.3）；
- 按**传入顺序**逐条裁决，不再排序：``entries`` 已由 ``ProposalQueue.drain`` 按
  ``(priority desc, enqueue_seq asc, actor_id asc)`` 排好（第 11 章 §11.3 规则 1）。

单条提案的裁决过程（逐字对齐第 21 章 §21.2.2，差异见 ``resolve`` docstring）：

1. 用**服务端推导**的 ``actor_tags``（``ActorDirectory.tags_of``）与 ``caller="resolver"`` 构造
   ``ToolContext``；幂等键 ``f"res:{turn_id}:{proposal_id}"``。提案/模型输出中的
   ``authority``/``actor_tags`` 字段一律不参与授权（第 11 章 §11.1 红线）；
2. ``rules.evaluate_preconditions(...state=working_state)`` 失败 → ``verdict="rejected"``，
   附前置条件给出的理由（如 ``item ... is not in ...``，第 11 章 §11.3 规则 2）；
3. **M5E-2 裁判分支**：``preconditions.data["needs_judge"]`` 为真时，只取本提案对应槽位的
   已校验 ``JudgeOutput``——``confidence >= 0.7`` 且 ``reason`` 非空 → ``verify_agent_override``
   复核，否则 ``deterministic_fallback`` 保守兜底；不通过即 ``rejected``（零事件）。非
   ``needs_judge`` 提案**完全不读取** ``judgements``（ExplodingJudge 安全测试保持零访问）；
4. ``services.call(tool, ctx, args, working_state)``（= ``ToolGate.dispatch``）失败 →
   ``"conflict"`` 当且仅当 ``error_code is ErrorCode.CONFLICT``，否则 ``"rejected"``；
   成功 → ``"applied"``，``applied_events`` 必须是草稿事件，每条事件 ``params`` 追加
   ``{"proposal_id": ..., "resolution_id": ...}``（契约 §3.3 / D-M3-8：``Event`` 基线字段不扩展）；
5. 每条 applied 之后 ``working_state = working_state.apply(events)``：后序提案基于前序结果重新
   校验（资源竞争/目标冲突规则，第 11 章 §11.3 规则 2/3）。

``services`` 端口按契约是 ``ServiceRouter.call``；``ToolGate`` 的方法是 ``dispatch``，
组合根用本模块的 ``ToolGateRouter`` 适配（不改变契约命名）。

状态词口径（第 11 章 §11.4）：``Resolution.verdict`` 只出现在本模块返回值，
``QueueEntry.status``/审计状态词由队列与编排层各自维护，本模块不读写队列内部状态。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Final, Literal, Protocol

from textgame.domain.events import Event
from textgame.domain.judge import JUDGE_CONFIDENCE_FLOOR, JudgeOutput
from textgame.domain.models import GameState, Proposal, Resolution
from textgame.domain.results import ErrorCode, Result
from textgame.orchestration.proposal_queue import QueueEntry
from textgame.services.actor_directory import ActorDirectory
from textgame.services.tool_context import ToolContext

#: ``Resolution.reason`` 的模型上限（``domain.models.Resolution.reason`` max_length=1000）。
_REASON_LIMIT: Final[int] = 1000


class ServiceRouter(Protocol):
    """服务层写方法端口（第 21 章 §21.2.2）：返回 ``Result``，``events`` 为草稿 ``Event``。

    实现由组合根装配（``ToolGateRouter`` 包装 ``ToolGate.dispatch``）；``Resolver`` 只依赖
    该结构化端口，不直接 import 具体服务类，也不持有仓储。
    """

    def call(
        self,
        *,
        tool: str,
        ctx: ToolContext,
        args: dict[str, Any],
        state: GameState,
    ) -> Result: ...


class PreconditionRules(Protocol):
    """确定性前置条件端口（第 10 章 §10.1 / 第 21 章 §21.2.2），签名同 ``RuleEngine``。"""

    def evaluate_preconditions(
        self,
        *,
        tool: str,
        args: dict[str, Any],
        actor_id: str,
        turn_id: int,
        save_seed: str,
        state: GameState,
    ) -> Result: ...

    def verify_agent_override(self, ctx: Any, proposal: Proposal, judge: JudgeOutput) -> Result:
        """复核裁判覆盖建议（纯计算、零事件；M5E-2 由 ``RuleEngine`` 实现）。"""
        ...

    def deterministic_fallback(self, ctx: Any, proposal: Proposal, snapshot: GameState) -> Result:
        """裁判证据缺失/不可用时的确定性保守兜底（纯计算、零事件）。"""
        ...


class DispatcherLike(Protocol):
    """``ToolGate`` 的结构化最小端口（只要求 ``dispatch``，不 import ``tools`` 具体类）。"""

    def dispatch(
        self,
        *,
        tool: str,
        args: dict[str, Any],
        ctx: ToolContext,
        state: GameState,
    ) -> Result: ...


class ToolGateRouter:
    """把 ``ToolGate.dispatch`` 适配为契约 §3.3 的 ``ServiceRouter.call``。

    组合根（``main.py``，Lead 负责）构造 ``Resolver(services=ToolGateRouter(gate), ...)`` 即可；
    适配器只做参数透传，不含任何业务逻辑，故不破坏 ``Resolver`` 的纯函数性。
    """

    def __init__(self, dispatcher: DispatcherLike) -> None:
        """注入工具门（``ToolGate`` 或等价实现），本适配器不持有其他状态。"""
        self._dispatcher = dispatcher

    def call(
        self,
        *,
        tool: str,
        ctx: ToolContext,
        args: dict[str, Any],
        state: GameState,
    ) -> Result:
        """透传到 ``dispatch``；工具门负责白名单/``actor_scope``/Schema 校验与路由。"""
        return self._dispatcher.dispatch(tool=tool, args=args, ctx=ctx, state=state)


def _clip_reason(reason: str) -> str:
    """把理由裁剪到 ``Resolution.reason`` 的模型上限内（防御注入/异常长文本导致模型拒绝）。"""
    if len(reason) <= _REASON_LIMIT:
        return reason
    return reason[: _REASON_LIMIT - 3] + "..."


def _tag_event(event: Event, *, proposal_id: str, resolution_id: str) -> Event:
    """给草稿事件 ``params`` 追加审计键（D-M3-8：事件基线字段不扩展，只增 ``params`` 键）。"""
    params = dict(event.params)
    params["proposal_id"] = proposal_id
    params["resolution_id"] = resolution_id
    return event.model_copy(update={"params": params})


def _draft_events_violation(events: Sequence[Event], proposal: Proposal) -> str | None:
    """校验服务返回的事件批：只能是对本提案 actor 的草稿事件（第 11 章 §11.1/§11.4）。

    - 已盖章事件 → 拒绝：``Resolver`` 只能产出草稿，盖章是 ``GameEngine`` 的职责
      （第 21 章 §21.2.1，混来源/预盖章批次不得绕过提交门）；
    - 事件 ``actor_id`` 与提案 actor 不一致 → 拒绝：同批次混来源，违反 authority 红线
      （第 11 章 §11.1“同批次内混来源的提案一律拒绝”）；
    - 事件 ``turn_id`` 与提案回合不一致 → 拒绝（回合错位会污染 ``GameState`` 投影）；
    - 空事件批 → 拒绝：``Resolution(verdict="applied")`` 至少携带一条事件（第 5 章模型校验）。
    """
    if not events:
        return "service returned no draft events for applied resolution"
    for event in events:
        if event.is_stamped:
            return "service returned a stamped event; resolver only accepts draft events"
        if event.actor_id != proposal.actor_id:
            return (
                "mixed-source event batch rejected: event actor "
                f"{event.actor_id!r} does not match proposal actor {proposal.actor_id!r}"
            )
        if event.turn_id != proposal.turn_id:
            return (
                "event turn_id mismatch: event turn "
                f"{event.turn_id} does not match proposal turn {proposal.turn_id}"
            )
    return None


def _precondition_rejection(result: Result) -> str | None:
    """把前置条件结果翻译为拒绝理由；通过返回 ``None``（第 21 章 §21.2.2 步骤 1/2）。"""
    if not result.ok:
        return _clip_reason(result.message or "precondition rejected")
    data = result.data or {}
    if data.get("allowed") is False:
        reason = data.get("reason")
        if isinstance(reason, str) and reason:
            return _clip_reason(reason)
        return "precondition rejected"
    return None


class Resolver:
    """纯函数裁决器：构造注入规则/服务端口/actor 标签目录/存档种子，无 IO 无随机。"""

    def __init__(
        self,
        *,
        rules: PreconditionRules,
        services: ServiceRouter,
        actors: ActorDirectory,
        save_seed: str,
    ) -> None:
        """依赖全部构造注入；``actors`` 负责服务端推导 ``actor_tags``（第 11 章 §11.1）。

        ``save_seed`` 为组合根默认种子（M2/M3 兼容）；D-M4-14 起裁决实际使用
        ``snapshot.rng_state.save_seed``（每存档真实种子），构造参数仅保留兼容。
        """
        self._rules = rules
        self._services = services
        self._actors = actors
        self._save_seed = save_seed

    def resolve(
        self,
        entries: Sequence[QueueEntry],
        snapshot: GameState,
        judgements: Sequence[JudgeOutput | None] | None = None,
    ) -> list[Resolution]:
        """按传入顺序逐条裁决并返回与 ``entries`` 等长、同序的 ``Resolution`` 列表。

        - ``judgements`` 为编排层调用 ``JudgeAgent`` 并完成 Schema 校验后传入的裁判证据，
          与 ``entries`` **同序等长、槽位可为 None**（第 11 章 §11.2、第 21 章 §21.2.2）；
          **只有** ``needs_judge`` 提案才会读取对应槽位，其余提案零访问（ExplodingJudge
          安全测试口径保持）；``judgements=None`` 等价空列表；
        - ``needs_judge`` 分支：``confidence >= JUDGE_CONFIDENCE_FLOOR`` 且 ``reason`` 非空 →
          ``rules.verify_agent_override`` 复核；否则 ``rules.deterministic_fallback`` 保守兜底；
          不通过即 ``rejected``（零事件），裁判不写状态/不调模型（模型调用在编排层）；
        - ``snapshot`` 不被修改（``GameState`` frozen）；裁决过程只用局部 ``working_state``，
          每条 applied 后累积 apply，后序提案基于前序结果重新校验（第 11 章 §11.3 规则 3）；
        - 不合并同一 actor 的多提案、不重排输入（审计逐提案一行，第 11 章 §11.4）。
        """
        # D-M4-14：裁决内 ToolContext/规则前置使用的种子必须绑定「本存档」真实 save_seed，
        # 不能用组合根默认种子（save-42）；否则非默认 seed 存档的服务层派生（craft rng_seed/
        # 实例 ID）会与 EventLog.stamp 的派生不一致。
        save_seed = snapshot.rng_state.save_seed
        working_state = snapshot
        results: list[Resolution] = []
        for index, entry in enumerate(entries, start=1):
            proposal = entry.proposal
            proposal_id = entry.proposal_id
            resolution_id = f"r_{proposal.turn_id:06d}_{index:03d}"
            ctx = ToolContext(
                actor_id=proposal.actor_id,
                actor_tags=self._actors.tags_of(snapshot, proposal.actor_id),
                turn_id=proposal.turn_id,
                save_seed=save_seed,
                idempotency_key=f"res:{proposal.turn_id}:{proposal_id}",
                caller="resolver",
            )
            preconditions = self._rules.evaluate_preconditions(
                tool=proposal.tool,
                args=dict(proposal.args),
                actor_id=proposal.actor_id,
                turn_id=proposal.turn_id,
                save_seed=save_seed,
                state=working_state,
            )
            rejection = _precondition_rejection(preconditions)
            if rejection is not None:
                results.append(
                    Resolution(
                        proposal_id=proposal_id,
                        verdict="rejected",
                        reason=rejection,
                        applied_events=[],
                    )
                )
                continue
            # M5E-2 裁判分支（第 11 章 §11.2 / 第 21 章 §21.2.2）：只消费编排层注入的已校验证据，
            # 证据缺失/低置信 → deterministic_fallback 保守处理（不产生有利效果）。
            if preconditions.data and preconditions.data.get("needs_judge"):
                judge: JudgeOutput | None = None
                if judgements is not None and index - 1 < len(judgements):
                    judge = judgements[index - 1]
                if (
                    judge is not None
                    and getattr(judge, "confidence", 0.0) >= JUDGE_CONFIDENCE_FLOOR
                    and str(getattr(judge, "reason", "")).strip()
                ):
                    preconditions = self._rules.verify_agent_override(ctx, proposal, judge)
                else:
                    preconditions = self._rules.deterministic_fallback(ctx, proposal, snapshot)
                if not preconditions.ok or not (preconditions.data or {}).get("allowed", False):
                    results.append(
                        Resolution(
                            proposal_id=proposal_id,
                            verdict="rejected",
                            reason=_clip_reason(
                                preconditions.message or "judge evidence rejected / fallback"
                            ),
                            applied_events=[],
                        )
                    )
                    continue
            executed = self._services.call(
                tool=proposal.tool,
                ctx=ctx,
                args=dict(proposal.args),
                state=working_state,
            )
            if not executed.ok:
                verdict: Literal["conflict", "rejected"] = (
                    "conflict" if executed.error_code is ErrorCode.CONFLICT else "rejected"
                )
                results.append(
                    Resolution(
                        proposal_id=proposal_id,
                        verdict=verdict,
                        reason=_clip_reason(executed.message or ""),
                        applied_events=[],
                    )
                )
                continue
            violation = _draft_events_violation(executed.events, proposal)
            if violation is not None:
                results.append(
                    Resolution(
                        proposal_id=proposal_id,
                        verdict="rejected",
                        reason=violation,
                        applied_events=[],
                    )
                )
                continue
            applied_events = [
                _tag_event(event, proposal_id=proposal_id, resolution_id=resolution_id)
                for event in executed.events
            ]
            try:
                projected = working_state.apply(applied_events)
            except ValueError as exc:
                # 服务/规则端口本应是受信内部实现；投影失败时拒绝该提案并保持 working_state
                # 不变（零半成品状态），而不是让单个坏事件中断整回合的确定性裁决。
                results.append(
                    Resolution(
                        proposal_id=proposal_id,
                        verdict="rejected",
                        reason=_clip_reason(f"event projection failed: {exc}"),
                        applied_events=[],
                    )
                )
                continue
            data = preconditions.data or {}
            applied_reason = data.get("reason")
            results.append(
                Resolution(
                    proposal_id=proposal_id,
                    verdict="applied",
                    reason=_clip_reason(applied_reason) if isinstance(applied_reason, str) else "",
                    applied_events=applied_events,
                )
            )
            working_state = projected
        return results


__all__ = [
    "DispatcherLike",
    "PreconditionRules",
    "Resolver",
    "ServiceRouter",
    "ToolGateRouter",
]
