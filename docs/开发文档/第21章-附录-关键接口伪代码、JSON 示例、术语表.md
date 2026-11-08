## 第 21 章 附录：关键接口伪代码、JSON 示例、术语表

### 本章检索关键词
- `pydantic v2 model_json_schema JSON Schema generation`
- `python jsonschema Draft 2020-12 validate`
- `langgraph state graph checkpointer`
- `pydantic-ai structured output validation`
- `github.com/pydantic/pydantic`、`github.com/python-jsonschema/jsonschema`

### 实际打开确认的仓库列表
- `pydantic/pydantic`（https://github.com/pydantic/pydantic）— 打开确认：许可证 MIT、最近提交 2026-11、star 约 29.4k、topics 标注 python310–python314、Python 3.10 兼容性未装包实测（未能确认）；读过 README：V2 含 breaking changes，附 v1 兼容层；本附录的模型与 Schema 示例按 Pydantic v2 公开接口书写。
- `python-jsonschema/jsonschema`（https://github.com/python-jsonschema/jsonschema）— 打开确认：许可证 MIT、最近提交 2026-11、star 约 5.0k、Python 3.10 兼容性未能确认；读过 README：支持 Draft 2020-12/2019-09/7/6/4/3，提供 `validate` 与 `iter_errors`；作为 Pydantic 之外的第二道校验。
- `langchain-ai/langgraph`（https://github.com/langchain-ai/langgraph）— 打开确认：许可证 MIT、最近提交 2026-11、star 约 44.6k、Python 3.10 兼容（第 9 章核对 `requires-python >=3.10`）；读过 README：low-level 状态化编排、durable execution；本附录只定义契约，选型见第 8 章。
- `pydantic/pydantic-ai`（https://github.com/pydantic/pydantic-ai）— 打开确认：许可证 MIT、最近提交 2026-11、star 约 21.1k、open issues 约 1.8k、Python 3.10 兼容性未能确认；读过仓库描述（agents / typed end to end）；仅参考其“输出类型 + 校验重试”模式，不引入。

### 21.1 阅读约定
- 本附录是**契约速查**，不是最终实现：签名以第 5–15 章正文为准；若正文与本附录冲突，以正文为准并回填本附录。
- 类型只引用不重复定义：`TurnInput`/`TurnResult`（第 9 章）、`CraftRequest`/`CraftMode`/`CraftDraft`（第 13 章）、`MemoryStore`/`MemoryOp`（第 8 章）、`QueryService.build_perception_slice(actor_id, turn_id, save_seed)`（第 6 章）等，来源见 §21.1.1。
- 上下文统一为 `ToolContext`（第 6 章 §6.2：`actor_id` / `actor_tags` / `turn_id` / `save_seed` / `idempotency_key` / `caller`）；LLM 调用统一为 `LLMClient.complete_json(req: LLMRequest) -> Result`（第 12 章）；解析输出统一为 `ParseOutput`（第 8 章）。
- 所有写路径最终只经过 `GameEngine.submit`；服务层只返回 `Result(events=[草稿 Event])`，不提交、不写库；所有 LLM 输出必须先过 JSON Schema 校验。
- **词表分离**：`Resolution.verdict ∈ {applied, rejected, conflict, deferred}`（第 9、11 章）；提案审计状态另用 `{committed, conflicted, aborted}`（第 11 章审计表），二者不得混用。
- **预算口径**（第 9、12 章）：单回合默认 2 次模型调用、最多 3 次；首版 P95 ≤ 8 s，M2 缓存命中 ≤ 3 s；CAS 最多 3 次提交尝试（首次 + 2 次重试），重试只重裁决。
- 伪代码使用 Python 3.10+ 类型注解；`async` 只出现在真实异步边界（LLMClient、TurnOrchestrator）；`...` 表示省略实现。

#### 21.1.1 类型来源索引（本附录只引用，不重复定义）

| 类型 / 接口 | 定义位置 | 本附录用途 |
| --- | --- | --- |
| `TurnInput` / `TurnResult` | 第 9 章（接入 DTO） | `TurnOrchestrator.run` 入参/出参 |
| `ToolContext` | 第 6 章 §6.2 | 工具层透传、服务层消费 |
| `ToolSpec` 及 `_first_sentence` / `generate_json_schema` / `build_params_model` | 第 7 章 §7.3–7.4 | `ToolRegistry.register` |
| `ProposalQueue` / `Resolver` / `GameEngine` 协议 | 第 11 章 §11.2–11.4 | 排队、裁决、提交 |
| `LLMRequest` / `LLMCallRecord` / `ModelTier` | 第 12 章 §12.2 | `LLMClient.complete_json` |
| `ParseOutput` / `ToolCall` / `ActionIntent` / `JudgeOutput` / `MemoryStore` / `MemoryOp` | 第 8 章 §8.2 | 解析、裁判、记忆 |
| `verify_agent_override` / `deterministic_fallback`（RuleEngine 裁决接口） | 第 7 章 §7.4、第 11 章 §11.2 | 裁判证据复核、确定性兜底 |
| `CraftRequest` / `CraftMode` / `CraftDraft` / `CraftStateSlice` / `Recipe` / `CraftResult` | 第 13 章 §13.2–13.5 | `CraftService.craft` |
| `Event` / `EventLog.stamp` / `RandomSource.derive_seed` / `RandomSource.mask_seed` | 第 5、6、10 章 | 草稿事件、盖章、种子派生 |
| `Result` / `ErrorCode` | 第 5 章 | 统一返回 |

### 21.2 关键接口伪代码

#### 21.2.1 GameEngine.submit（唯一写入者 + CAS + 幂等）
```python
class GameEngine:
    """唯一写入口：只有本类可以在同一事务内写 EventLog 与 GameState。

    events 由 Resolver 裁决产出（第 11 章）；GameEngine 不调模型、不做规则计算。
    """

    def submit(self, expected_version: int, events: list[Event],
               idempotency_key: str) -> Result:
        # 0) 幂等：同一幂等键直接返回上次 Result，不重复写事件
        cached = self.repo.get_result_by_key(idempotency_key)
        if cached is not None:
            return cached
        # 1) 提交门：CAS 版本 + 领域不变量 + 事件序号单调
        #    expected_version 为持久化层游戏状态行的 version_id（第 11 章），不写入 GameState 基线字段
        state = self.repo.load_state()
        if self.repo.current_version(state) != expected_version:
            return Result.fail(ErrorCode.CONFLICT, "version mismatch")   # 重读快照后重裁决；最多 3 次提交尝试
        if not self.invariants.holds(state, events):                        # hp>=0、背包非负、位置合法
            return Result.fail(ErrorCode.NOT_ALLOWED, "domain invariant violated")
        # 盖章由 EventLog 完成：id=evt_<seq:06d>、ts=UTC now、
        # rng_seed = RandomSource.derive_seed(save_seed, turn_id, actor_id, method, seq) & 0x7FFF_FFFF_FFFF_FFFF
        # （derive_seed / mask_seed 为第 10 章 RandomSource 类方法；save_seed 为存档级种子，装配 GameEngine 时由编排层注入）
        stamped = self.event_log.stamp(events, turn_id=state.turn,
                                       save_seed=self.save_seed,
                                       start_seq=self.repo.next_seq(state.turn))
        new_state = state.apply(stamped)                                    # 纯投影，不写库
        # 2) 原子提交：追加事件 + 更新快照 + 记录幂等结果，任一失败整体回滚
        with self.repo.transaction() as tx:
            if not tx.append_events(stamped):                               # 事件序号在事务内校验
                tx.rollback()
                return Result.fail(ErrorCode.CONFLICT, "event sequence conflict")
            if not tx.save_snapshot(new_state, expected_version=expected_version):
                tx.rollback()
                return Result.fail(ErrorCode.CONFLICT, "concurrent write detected")
            tx.save_idempotent_result(idempotency_key, stamped)
        return Result(ok=True, data={"event_ids": [e.id for e in stamped]}, events=stamped)
```

#### 21.2.2 Resolver.resolve（纯函数裁决：确定性优先，裁判证据由编排层注入）
```python
class ServiceRouter(Protocol):
    """附录显式声明的端口：tool → 第 6 章服务层写方法（返回 Result，events 为草稿 Event）。

    实现由 tools/args_adapter 装配；Resolver 只依赖该端口，不直接 import 服务类。
    """
    def call(self, tool: str, ctx: ToolContext, args: dict) -> Result: ...


class Resolver:
    """纯函数：不调模型、不写库、不读时钟；同一输入必得同一 Resolution 列表。

    入参 proposals 已由 ProposalQueue 按 (priority desc, 入队序号 asc, actor_id asc) 排好序
    （第 11 章 §11.3），本类不再二次排序，也不读取 Proposal 之外的排队字段。
    `judgements` 为编排层按第 9 章步骤 6 调用 JudgeAgent 并完成 Schema 校验后传入的裁判证据
    （第 8 章 JudgeOutput，顺序与 proposals 对齐）。
    """

    def __init__(self, rules: RuleEngine, services: ServiceRouter,
                 actors: ActorDirectory, save_seed: str) -> None:
        self._rules = rules
        self._services = services
        self._actors = actors
        self._save_seed = save_seed

    def resolve(self, proposals: Sequence[Proposal], snapshot: GameState,
                judgements: Sequence[JudgeOutput] | None = None) -> list[Resolution]:
        results: list[Resolution] = []
        judge_list = list(judgements or [])
        for seq, proposal in enumerate(proposals, start=1):    # 队列顺序即裁决顺序，不重排
            ctx = ToolContext(actor_id=proposal.actor_id,
                              actor_tags=self._actors.tags_of(proposal.actor_id),
                              turn_id=proposal.turn_id, save_seed=self._save_seed,
                              idempotency_key=f"{proposal.turn_id}:{proposal.actor_id}:{proposal.tool}",
                              caller="resolver")
            pid = f"p_{proposal.turn_id:06d}_{seq:03d}"        # 审计用 proposal_id（第 11 章审计表）
            # 1) 前置条件（确定性纯计算，第 6 章 RuleEngine.evaluate_preconditions）
            pre = self._rules.evaluate_preconditions(ctx, proposal.tool, proposal.args, snapshot)
            if not pre.ok:
                results.append(Resolution(proposal_id=pid, verdict="rejected",
                                          reason=pre.message, applied_events=[]))
                continue
            # 2) 裁判分支（第 7 章 §7.4、第 11 章 §11.2）：只消费编排层传入的已校验证据，
            #    confidence >= 0.7 且带 reason 才允许覆盖，并必须经 verify_agent_override 复核；
            #    证据缺失/低置信 → deterministic_fallback 保守处理，不采纳未验证建议
            if pre.data.get("needs_judge"):
                judge = judge_list[seq - 1] if seq - 1 < len(judge_list) else None
                if judge is not None and judge.confidence >= 0.7 and judge.reason:
                    pre = self._rules.verify_agent_override(ctx, proposal, judge)
                else:
                    pre = self._rules.deterministic_fallback(ctx, proposal, snapshot)
                if not pre.ok or not pre.data["allowed"]:
                    results.append(Resolution(proposal_id=pid, verdict="rejected",
                                              reason=pre.message or "judge evidence rejected / fallback",
                                              applied_events=[]))
                    continue
            if not pre.data["allowed"]:
                results.append(Resolution(proposal_id=pid, verdict="rejected",
                                          reason=pre.data.get("reason", "precondition rejected"),
                                          applied_events=[]))
                continue
            # 3) 通过后调用服务层写方法取草稿事件（第 6 章：服务层不提交、不写库）
            executed = self._services.call(proposal.tool, ctx, proposal.args)
            if not executed.ok:
                verdict = "conflict" if executed.error_code is ErrorCode.CONFLICT else "rejected"
                results.append(Resolution(proposal_id=pid, verdict=verdict,
                                          reason=executed.message, applied_events=[]))
                continue
            # 4) 产出裁决：applied_events 必须是草稿态 Event（第 5 章校验），由 GameEngine 盖章提交
            results.append(Resolution(proposal_id=pid, verdict="applied",
                                      reason=pre.data.get("reason", ""),
                                      applied_events=executed.events))
        return results
```

裁判覆盖归属（第 7 章 §7.4、第 8 章 §8.2、第 11 章 §11.2）：`JudgeAgent` 由编排层调用并完成 Schema 校验，产出 `JudgeOutput` 后随 `judgements` 传入；`confidence >= 0.7` 且带 `reason` 时由 `verify_agent_override` 复核采纳，否则 `deterministic_fallback` 保守处理。Resolver 不调模型、不写库，回放可复现。

#### 21.2.3 ToolRegistry（register / visible_tools / invoke / build_proposal，以第 7 章为准）
```python
class ToolContext(BaseModel):                         # 第 6 章约定：工具层透传、服务层消费
    model_config = ConfigDict(frozen=True, extra="forbid")
    actor_id: str
    actor_tags: frozenset[str] = frozenset()          # {"player"} / {"npc", "npc:blacksmith"}
    turn_id: int
    save_seed: str                                    # 第 10 章派生随机用
    idempotency_key: str
    caller: Literal["player", "npc_agent", "parser", "system", "resolver"]

class ToolRegistry:
    def register(self, fn: Callable[..., Result], *, actor_scope: set[str] | None = None,
                 side_effect: SideEffect | None = None,
                 resolution_policy: ResolutionPolicy | None = None) -> ToolSpec:
        """注册工具：优先用 @tool 附带的 __tool_spec__，也允许显式传参现建（便于单测）。"""
        spec: ToolSpec | None = getattr(fn, "__tool_spec__", None)
        if spec is None:
            if actor_scope is None:
                raise ValueError(f"{fn.__name__}: register() needs @tool metadata or explicit actor_scope")
            spec = ToolSpec(name=fn.__name__, description=_first_sentence(fn.__doc__ or ""),
                            json_schema=generate_json_schema(fn),      # 由类型注解生成，禁止手写
                            actor_scope=frozenset(actor_scope),
                            side_effect=side_effect or SideEffect.READ_ONLY,
                            resolution_policy=resolution_policy or ResolutionPolicy.DETERMINISTIC)
        if spec.name in self._specs:
            raise ValueError(f"duplicate tool: {spec.name}")
        self._specs[spec.name] = spec
        self._impls[spec.name] = fn
        self._models[spec.name] = build_params_model(fn)
        return spec

    def visible_tools(self, actor_id: str, actor_tags: frozenset[str] = frozenset()) -> list[ToolSpec]:
        """白名单：Actor 标签命中 actor_scope 才可见；空 scope 只允许编排层直调。"""
        return [s for s in self._specs.values()
                if actor_id in s.actor_scope or not s.actor_scope.isdisjoint(actor_tags)]

    def invoke(self, name: str, ctx: ToolContext, args: dict) -> Result:
        """仅用于 READ_ONLY 工具的即时执行；写请求走 build_proposal（第 7 章签名定稿）。

        错误码口径：工具名不存在 → NOT_FOUND；actor_scope 越权 → NOT_ALLOWED；
        参数不符合 JSON Schema → INVALID_INPUT；执行异常 → INTERNAL。
        """
        spec = self._specs.get(name)
        if spec is None:                                     # 1) 工具名不存在
            return Result.fail(ErrorCode.NOT_FOUND, f"unknown tool: {name}")
        if not self._visible(spec, ctx):                     # 2) 白名单/actor_scope 越权
            return Result.fail(ErrorCode.NOT_ALLOWED, "actor_scope violation")
        if spec.side_effect is not SideEffect.READ_ONLY:     # 3) 写工具必须走提案
            return Result.fail(ErrorCode.NOT_ALLOWED, "write tools must go through Proposal → Resolver")
        try:
            params = self._validate_args(spec, args)         # 4) 参数不符 JSON Schema
        except ValidationError as exc:
            return Result.fail(ErrorCode.INVALID_INPUT, self._brief(exc))
        try:
            return self._impls[name](ctx=ctx, **params)
        except Exception:                                    # 5) 不把堆栈/内部信息返回给 Agent
            return Result.fail(ErrorCode.INTERNAL, "tool execution failed")

    def build_proposal(self, tool: str, args: dict, *, actor_id: str,
                       actor_tags: frozenset[str], turn_id: int,
                       reason: str, priority: int) -> Proposal | None:
        """白名单 + 参数校验通过则产提案；工具不可见返回 None（编排层转澄清）。

        `priority` 为必填，由调用方按第 11 章传入：玩家动作 100、NPC 动作 50。
        `tool`/`args` 由编排层从第 8 章 `ParseOutput` 提取并映射（args_adapter）。
        """
        spec = self._specs.get(tool)
        if spec is None:
            return None
        if actor_id not in spec.actor_scope and spec.actor_scope.isdisjoint(actor_tags):
            return None
        if spec.side_effect is SideEffect.READ_ONLY:
            return None                                      # 读工具不值得走提案
        if spec.resolution_policy is ResolutionPolicy.MANUAL:
            return None                                      # MVP 不启用
        try:
            validated = self._validate_args(spec, args)
        except ValidationError:
            return None
        return Proposal(turn_id=turn_id, actor_id=actor_id, tool=spec.name,
                        args=validated, reason=reason, priority=priority)

    @staticmethod
    def _visible(spec: ToolSpec, ctx: ToolContext) -> bool:
        return ctx.actor_id in spec.actor_scope or not spec.actor_scope.isdisjoint(ctx.actor_tags)

    # 其余辅助（_validate_args / _brief / _first_sentence / generate_json_schema /
    # build_params_model）见第 7 章 7.4，本附录不重复展开
```

#### 21.2.4 LLMClient.complete_json（成功 data={"parsed","usage","call"}，失败四类错误码）
```python
class LLMClient:
    async def complete_json(self, req: LLMRequest) -> Result:
        # req 即第 12.2 节 LLMRequest：agent(=agent_id)/purpose/tier(=model_tier)/messages/
        # response_model(=schema)/turn_id/actor_id/idempotency_key/timeout_s/cache_ttl_s
        if req.response_model is None:
            return Result.fail(ErrorCode.INVALID_INPUT, "response_model required for complete_json")
        # 1) 幂等：同键已成功直接复用（llm_call_log 唯一索引），不重复扣费
        if (hit := await self.calls.find_succeeded(req.idempotency_key)) is not None:
            return Result.ok(data={"parsed": hit.parsed, "usage": hit.usage, "call": hit.call})
        # 2) 供应商调用：LiteLLM 只做协议与回退；tenacity 是唯一网络重试计数者；
        #    req.timeout_s 是整条链 deadline（单次尝试 ≤ 剩余时间）
        raw = await self._call(req)
        if not raw.ok:                                   # LLM_UNAVAILABLE：超时/限流/供应商全挂
            return raw
        # 3) 校验 + 一次修复重试（instructor max_retries=1，共 2 次请求）
        try:
            parsed = req.response_model.model_validate_json(raw.text)
        except ValidationError as first_error:
            raw = await self._call(req, repair_hint=first_error)
            if not raw.ok:
                return raw
            try:
                parsed = req.response_model.model_validate_json(raw.text)
            except ValidationError as second_error:
                self.calls.log_schema_failure(req, second_error)   # 写 llm_call_log，events 为空
                return Result.fail(ErrorCode.SCHEMA_VIOLATION, "schema validation failed twice")
        call = self.calls.record(req, raw, parsed)       # LLMCallRecord：token/成本/延迟/prompt_version
        return Result.ok(data={"parsed": parsed, "usage": raw.usage, "call": call})
        # 失败码固定四类：LLM_UNAVAILABLE / SCHEMA_VIOLATION / INVALID_INPUT / INTERNAL；
        # LLMClient 不产生领域事件，也不做业务兜底——确定性兜底由调用方按第 12.6 节执行
        # （解析→关键词、叙事→模板、判定→RuleEngine、制造→calculate_craft_result）。
```

#### 21.2.5 CraftService.craft（服务层返回草稿事件，不提交）
```python
class CraftService:
    def craft(self, ctx: ToolContext, req: CraftRequest, *, idempotency_key: str) -> Result:
        # CraftRequest / CraftMode / CraftStateSlice / Recipe / CraftResult / CraftDraft 见第 13 章
        state = self.state_reader.craft_state_slice(ctx.actor_id)      # 只读快照切片（第 6、13 章）
        rng_seed = RandomSource.derive_seed(ctx.save_seed, ctx.turn_id, ctx.actor_id, "craft_item", seq) & 0x7FFF_FFFF_FFFF_FFFF  # 第 10 章类方法
        if req.mode is CraftMode.RECIPE:
            recipe = self.recipes.require(req.recipe_id)               # Recipe（第 13 章 §13.2）
            # 唯一数值权威：domain/crafting.py 纯函数，成功率/消耗/质量/产出全部由它计算
            result = calculate_craft_result(state, recipe, ctx.actor_id, rng_seed)
        else:
            # 自由制造：CraftAgent 只生成草案（模型调用在 Agent 内，第 12 章）；
            # 服务层做 Schema + 状态一致性检查，再用同一纯函数重算数值（第 13 章 §13.4）
            generated = self.craft_agent.generate(req)                 # data["parsed"] 为 CraftDraft
            if not generated.ok:                                       # SCHEMA_VIOLATION / LLM_UNAVAILABLE
                return generated
            draft = CraftDraft.model_validate(generated.data["parsed"])
            if not self.consistency.check(draft, state):
                return Result.fail(ErrorCode.NOT_ALLOWED, "draft violates world state")
            recipe = self.recipes.from_draft(draft)                    # 草案折算为临时 Recipe
            result = calculate_craft_result(state, recipe, ctx.actor_id, rng_seed)
        draft_event = Event(turn_id=ctx.turn_id, actor_id=ctx.actor_id, method="craft_item",
                            params={"recipe_id": req.recipe_id, "mode": req.mode.value,
                                    "proposal_id": self.proposal_id_for(idempotency_key)},
                            result={"craft": result})
        # 不调 GameEngine.submit、不写库：返回 Result(ok=True, events=[草稿 Event], data=CraftResult)，
        # 由 Resolver 取草稿事件、GameEngine 统一盖章提交（第 6、13 章）
        return Result.ok(data={"craft": result}, events=[draft_event])
```

#### 21.2.6 TurnOrchestrator.run（单回合主链路）
```python
class TurnOrchestrator:
    async def run(self, req: TurnInput) -> TurnResult:            # TurnInput / TurnResult 见第 9 章
        state, version = self.state_reader.load_snapshot()        # Snapshot 包装含 version（第 14 章）
        ctx = ToolContext(actor_id=req.player_id,
                          actor_tags=self.actors.tags_of(req.player_id),  # ActorDirectory（第 6、8 章）
                          turn_id=state.turn, save_seed=req.save_seed,
                          idempotency_key=req.idempotency_key, caller="player")
        slice_ = self.query.build_perception_slice(ctx.actor_id, ctx.turn_id, ctx.save_seed)  # 第 6 章
        resp = await self.llm.complete_json(LLMRequest(
            agent="parse", purpose="intent_parse", tier=ModelTier.ECONOMY,
            messages=self.prompt.parse(req.text, slice_), response_model=ParseOutput,
            turn_id=ctx.turn_id, actor_id=ctx.actor_id,
            idempotency_key=ctx.idempotency_key, timeout_s=8.0))
        if not resp.ok:                                           # LLM_UNAVAILABLE / SCHEMA_VIOLATION
            return TurnResult.clarify(self.fallback.parser_prompt(resp.error_code))
        parsed: ParseOutput = resp.data["parsed"]                 # 已过 Schema 校验与修复重试
        if parsed.tool is None:                                   # 只澄清或纯叙事，零写操作
            return TurnResult.clarify(parsed.clarify_question or "你想做什么？")
        proposal = self.tools.build_proposal(parsed.tool.name, parsed.tool.args,
                                             actor_id=ctx.actor_id, actor_tags=ctx.actor_tags,
                                             turn_id=ctx.turn_id,
                                             reason=str(parsed.intent.action), priority=100)
        if proposal is None:                                      # 白名单/参数校验不通过或只读工具
            return TurnResult.clarify("这个行动在当前场景不可用，你想做什么？")
        resolutions = self.resolver.resolve([proposal], state)    # 纯函数裁决
        events = [e for r in resolutions if r.verdict == "applied" for e in r.applied_events]
        if not events:                                            # rejected / deferred / conflict
            return TurnResult.from_resolution(resolutions[0],
                                              self.narrator.explain(resolutions[0]))
        # 1) 叙事候选在提交前生成并校验（第 4、9 章）；narrative_source 由渲染器写入对应 Event
        #    （字段位置以第 4 章为准），与事件一起被 GameEngine 原子提交
        text, _narrative_source = self.narrator.render(events, hint=parsed.candidate_narrative)
        # 2) 提交：最多 3 次提交尝试（首次 + 2 次重试），重试只重裁决、不重生成叙事
        result = self.engine.submit(version, events, ctx.idempotency_key)
        if not result.ok:                                         # CONFLICT / NOT_ALLOWED
            return TurnResult.failed(result)                      # 提交失败：丢弃叙事候选，不返回文本
        return TurnResult(ok=True, text=text, events=result.events, turn_id=ctx.turn_id)
```

#### 21.2.7 ProposalQueue（多角色提案排队，摘要，以第 11 章 §11.2–11.3 为准）
```python
class ProposalQueue:
    def enqueue(self, proposal: Proposal) -> None: ...          # 同 actor 同回合去重；proposal_id 由队列内部派生
    def drain(self, max_items: int = 8) -> list[Proposal]: ...  # 顺序 (priority desc, 入队序号 asc, actor_id asc)，队列内部维护
```

### 21.3 JSON 示例

示例中的 `player_01`、`npc_goblin_01`、`item_rusty_sword` 为示意 ID（非 ULID）；真实实现按第 5 章的 ID 规则生成。Event 的 `id`/`seq` 口径以第 5、14 章定稿为准（本示例按全局 `event_seq` 连续计数书写）。

（1）ActionIntent（解析 Agent 输出，已通过 Schema 校验）
```json
{
  "action": "attack",
  "target": "npc_goblin_01",
  "params": {"weapon_id": "item_rusty_sword"},
  "confidence": 0.91
}
```

（2）Proposal（字段与第 5 章 `Proposal` 基线一致；`proposal_id` 由 ProposalQueue 入队时派生，写入 `Event.params`）
```json
{
  "turn_id": 42,
  "actor_id": "player_01",
  "tool": "attack_target",
  "args": {"target_id": "npc_goblin_01", "weapon_id": "item_rusty_sword"},
  "reason": "玩家输入“用锈剑砍哥布林”，解析为攻击意图，actor_scope 允许",
  "priority": 100
}
```

（3）Event（盖章态：`GameEngine` 提交时由 `EventLog.stamp` 生成 `id=evt_<seq:06d>`、`ts` 为 UTC，`rng_seed` 为 `RandomSource.derive_seed(...) & 0x7FFF_FFFF_FFFF_FFFF` 后的有符号 64 位整数；草稿态见（4））
```json
{
  "id": "evt_000041",
  "turn_id": 42,
  "seq": 41,
  "actor_id": "player_01",
  "method": "attack_target",
  "params": {"target_id": "npc_goblin_01", "weapon_id": "item_rusty_sword",
             "proposal_id": "p_000042_001"},
  "result": {"hit": true, "damage": 5, "target_hp_after": 7},
  "rng_seed": 5483726150938472910,
  "ts": "2026-11-08T12:00:03Z"
}
```

（4）Resolution（`applied_events` 必须是**草稿态** Event：无 `id`/`seq`/`ts`/`rng_seed`，由 GameEngine 提交时统一盖章；`verdict` 取值见 21.1）
```json
{
  "proposal_id": "p_000042_001",
  "verdict": "applied",
  "reason": "确定性判定：d20=14，攻击加值 +3，合计 17 ≥ 目标 AC 12，命中成功；伤害 5 由 calculate_damage 计算",
  "applied_events": [
    {
      "turn_id": 42,
      "actor_id": "player_01",
      "method": "attack_target",
      "params": {"target_id": "npc_goblin_01", "weapon_id": "item_rusty_sword",
                 "proposal_id": "p_000042_001"},
      "result": {"hit": true, "damage": 5, "target_hp_after": 7}
    }
  ]
}
```

（5）Result（`GameEngine.submit` 返回**盖章态** events，可回放；失败时 `data` 为 null、`error_code` 必填）
```json
{
  "ok": true,
  "data": {"event_ids": ["evt_000041"]},
  "error_code": null,
  "message": null,
  "events": [
    {
      "id": "evt_000041",
      "turn_id": 42,
      "seq": 41,
      "actor_id": "player_01",
      "method": "attack_target",
      "params": {"target_id": "npc_goblin_01", "weapon_id": "item_rusty_sword",
                 "proposal_id": "p_000042_001"},
      "result": {"hit": true, "damage": 5, "target_hp_after": 7},
      "rng_seed": 5483726150938472910,
      "ts": "2026-11-08T12:00:03Z"
    }
  ]
}
```

（6）LLM 输出：未校验原始输出（会失败）与修复后通过校验的输出
```json
{
  "intent": {"action": "attack", "target": null, "params": {}, "confidence": 1.7},
  "tool": {"name": "attack_target", "args": {}},
  "candidate_narrative": ""
}
```
```json
{
  "intent": {"action": "attack", "target": "npc_goblin_01",
             "params": {"weapon_id": "item_rusty_sword"}, "confidence": 0.91},
  "tool": {"name": "attack_target",
           "args": {"target_id": "npc_goblin_01", "weapon_id": "item_rusty_sword"}},
  "candidate_narrative": "你握紧锈剑，朝哥布林劈下。"
}
```
校验约定：第一段会因 `confidence=1.7` 超出 `[0,1]` 触发 `SCHEMA_VIOLATION`，进入一次修复重试；第二段通过第 8 章 `ParseOutput` 校验后才允许生成 Proposal。`candidate_narrative` 只是候选叙事，最终正文以裁决结果为准；只澄清时输出 `clarify_question`。

（7）Pydantic 模型与 Schema 生成（示意，字段以第 8 章为准）
```python
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

class ActionIntent(BaseModel):
    action: Literal["move", "look", "attack", "use", "craft", "talk"]
    target: str | None = None
    params: dict = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0)

class ToolCall(BaseModel):
    name: str
    args: dict = Field(default_factory=dict)

class ParseOutput(BaseModel):                      # 第 8 章：extra="forbid"，多一字段即违规
    model_config = ConfigDict(extra="forbid")
    intent: ActionIntent
    tool: ToolCall | None = None
    candidate_narrative: str | None = Field(default=None, max_length=200)
    clarify_question: str | None = Field(default=None, max_length=80)

ACTION_INTENT_SCHEMA = ActionIntent.model_json_schema()   # 交给工具层/LLM 侧使用
PARSE_OUTPUT_SCHEMA = ParseOutput.model_json_schema()
```

### 21.4 错误码与失败分支对照

| ErrorCode | 触发场景 | 处理方式 | 是否写状态 |
| --- | --- | --- | --- |
| `INVALID_INPUT` | 参数类型/范围错误 | 返回结构化错误，提示玩家重述 | 否 |
| `NOT_FOUND` | 目标角色/房间/物品不存在 | 重新构建感知切片后重试一次 | 否 |
| `NOT_ALLOWED` | actor_scope 越权、工具不可见 | 记录审计事件并拒绝 | 否 |
| `OUT_OF_RANGE` | 距离、数量、回合窗口越界 | 返回约束说明，可由叙事 Agent 改写 | 否 |
| `CONFLICT` | CAS 版本不一致或并发写冲突 | 重读快照并重跑裁决（不重调模型），最多 3 次提交尝试（首次 + 2 次重试） | 否 |
| `SCHEMA_VIOLATION` | LLM 输出未通过 JSON Schema | 一次修复重试 → 由调用方确定性兜底（第 12.6 节） | 否 |
| `LLM_UNAVAILABLE` | 超时、限流、供应商不可用 | 回退供应商 → 规则兜底 → “无法处理” | 否 |
| `INTERNAL` | 未预期异常 | 记录完整上下文与事件号，向玩家返回中性提示 | 否 |

注意：上表的 `verdict` 词表与提案审计状态是两个字段——`verdict ∈ {applied, rejected, conflict, deferred}`；审计表状态为 `{committed, conflicted, aborted}`（第 11 章 §11.4），不得混用（例如审计状态不写 `applied`）。

### 21.5 术语表

#### 21.5.1 基线术语（与第 4 章术语约定一致）

| 中文 | English | 说明 |
| --- | --- | --- |
| 回合 | Turn | 一次可提交的游戏推进单位，对应 `GameState.turn` |
| 事件溯源 | Event Sourcing | 以只追加事件重建状态，支持审计与回放 |
| 唯一写入者 | Single Writer | 只有 GameEngine 可提交状态，其他组件只提议 |
| 提案 | Proposal | 角色 Agent 提交的“工具 + 参数 + 理由”请求 |
| 裁决 | Resolution | Resolver 对提案的判定结果（`applied` 时 `applied_events` 为草稿态事件，待 GameEngine 盖章提交） |
| 审计状态 | Audit State | 提案审计表生命周期：committed / conflicted / aborted（与 `verdict` 词表分开，第 11 章 §11.4） |
| 感知切片 | Perception Slice | 角色可见的局部状态与私有记忆，防止全局泄露 |
| 确定性兜底 | Deterministic Fallback | LLM 失败时由规则计算给出可接受结果 |
| 幂等键 | Idempotency Key | 同一操作重复提交只生效一次的去重键 |
| 回放 | Replay | 用固定种子与事件序列重建历史状态 |
| 快照 | Snapshot | 某一回合的完整状态副本，加速回放与恢复 |
| 骰子 | Dice Roll | 由 RandomSource 产生并记录种子的随机判定 |
| 工具白名单 | Tool Whitelist | 仅注册且对当前 actor 可见的工具可被调用 |
| 副作用标签 | Side-effect Tag | READ_ONLY / DESTRUCTIVE / NETWORK 三类标记 |
| 乐观并发控制 | Optimistic Concurrency Control (CAS) | 提交时校验版本，冲突则重试而非加锁 |

#### 21.5.2 核心组件与领域类型

| 中文 | English | 说明 |
| --- | --- | --- |
| 游戏引擎 | GameEngine | 唯一写入者，负责原子提交与幂等 |
| 回合编排器 | TurnOrchestrator | 解析 → 裁决 → 提交 → 叙事的主链路 |
| 提案队列 | ProposalQueue | 多角色提案的排队与去重 |
| 规则引擎 | RuleEngine | 执行确定性规则与骰子判定 |
| 规则集 | Ruleset | 某个玩法版本的规则与数值配置集合 |
| 工具注册表 | ToolRegistry | 工具注册、Schema 生成、可见性过滤 |
| 大模型客户端 | LLMClient | 统一封装调用、重试、缓存、计量 |
| 记忆存储 | MemoryStore | 角色私有记忆与场景记忆的读写 |
| 事件日志 | EventLog | 只追加事件存储与查询；负责 `stamp()` 盖章与 `append()` |
| 草稿态 / 盖章态 | Draft / Stamped Event | 服务层产出草稿态事件（无 id/seq/ts/rng_seed）；经 `EventLog.stamp` 补全后为盖章态 |
| 游戏状态 | GameState | 世界与角色的当前权威状态 |
| 随机源 | RandomSource | 可记录种子的随机数来源 |
| 制造服务 | CraftService | 确定性制造与自由制造的入口 |
| 动作意图 | ActionIntent | 解析 Agent 对玩家输入的结构化理解 |
| 解析输出 | ParseOutput | IntentParseAgent 校验后输出：intent / tool / candidate_narrative / clarify_question |
| 工具上下文 | ToolContext | 第 6 章新增约定：actor_id / actor_tags / turn_id / save_seed / idempotency_key / caller |
| 大模型请求 | LLMRequest | LLMClient 的结构化请求：agent / purpose / tier / messages / response_model / turn_id / idempotency_key 等 |
| 调用记录 | LLMCallRecord | 每次 LLM 调用的模型、token、成本、延迟与 prompt_version |
| 工具规格 | ToolSpec | 名称、描述、Schema、可见范围、副作用、裁决策略 |
| 结果对象 | Result | 写方法统一返回：ok / data / error_code / message / events |

### 21.6 使用方式
- 新人先读 21.2 的六个接口（ToolRegistry 含 register / visible_tools / invoke / build_proposal），再对照第 5–7、11、12 章理解字段来源，最后看第 9 章调用链。
- 评审用 21.3 的七个 JSON 作为“契约样例”：实现与样例字段不一致即阻断合入。
- 新增工具或服务方法时，先改第 6、7 章 → 再改本附录样例 → 最后写代码，禁止反向操作。

### 21.7 本附录涉及的候选轮子

| 仓库 | 定位 | 可复用部分 | 许可证 | 活跃度 | 建议 |
| --- | --- | --- | --- | --- | --- |
| `pydantic/pydantic` | Schema 与校验 | BaseModel、`model_json_schema()`、ValidationError 结构 | MIT | 2026-11 有提交，star 约 29.4k；2.14.3 支持 3.10 | pip 依赖 |
| `python-jsonschema/jsonschema` | JSON Schema 校验器 | Draft 2020-12 校验、`iter_errors` 逐条报错 | MIT | 2026-11 有提交，star 约 5.0k | pip 依赖 |
| `langchain-ai/langgraph` | 状态化 Agent 编排 | StateGraph 节点契约、中断与检查点思想 | MIT | 2026-11 有提交，star 约 44.6k；1.2.20 要求 ≥3.10 | pip 依赖 |
| `pydantic/pydantic-ai` | 类型化 Agent 框架 | “输出类型 → 校验 → 修复重试”模式 | MIT | 2026-11 有提交，star 约 21.1k，open issues 约 1.8k | 参考设计 |

复用判断与拼接边界：接口契约全部用 Pydantic v2 模型定义，Schema 由 `model_json_schema()` 生成后同时交给 LLM 侧与工具层，禁止再手写第二份 JSON Schema；`jsonschema` 只用于第三方工具协议（MCP/OpenAPI）接入时的二次校验，项目内部仍以 Pydantic 校验为主。若选用 LangGraph，节点输入输出必须是本附录类型，不允许把 `GameState` 直接塞进图状态；pydantic-ai 仅参考其校验重试模式，不进入依赖。

### 本章结论
1. 六个核心接口已与正文对齐（第 5–15 章）：GameEngine.submit、Resolver.resolve、ToolRegistry（register/visible_tools/invoke/build_proposal）、LLMClient.complete_json(req: LLMRequest) -> Result、CraftService.craft、TurnOrchestrator.run。
2. 写路径只有一个入口：`GameEngine.submit(expected_version, events, idempotency_key)`（第 11 章）；`Resolver` 按 ProposalQueue 给定顺序裁决、只消费编排层传入的已校验裁判证据、`applied_events` 只能是草稿态 Event；服务层（含 CraftService）只返回 `Result(events=[草稿 Event])`，不提交、不写库；`EventLog.stamp` 负责 id/seq/ts 与种子派生。
3. 叙事顺序：候选叙事在 resolve 之后、submit 之前生成并校验，提交成功后返回，提交失败则丢弃候选（第 4、9 章）。
4. 预算与重试口径：单回合默认 2 次、最多 3 次模型调用；首版 P95 ≤ 8 s，M2 缓存命中路径 ≤ 3 s；CAS 最多 3 次提交尝试（首次 + 2 次重试），重试只重裁决、不重调模型。
5. 所有智能体输出必须被标注：示例（6）区分“未校验原始输出（会失败）”与“通过 `ParseOutput` 校验后的输出”；`LLMClient` 成功返回 `data={"parsed","usage","call"}`，失败错误码限定 `LLM_UNAVAILABLE` / `SCHEMA_VIOLATION` / `INVALID_INPUT` / `INTERNAL`。
6. 词表分离：`verdict` 与审计状态 `{committed, conflicted, aborted}` 不得混用；基线术语沿用第 4 章约定，新增 `ToolContext`、`ParseOutput`、`LLMRequest`、`LLMCallRecord` 等已登记来源章节。
