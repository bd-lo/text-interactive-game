## 第 11 章 多角色并发与冲突消解：ProposalQueue、Resolver、唯一写入者

### 本章检索关键词
- `proposal queue resolver event sourcing game python`、`optimistic concurrency row_version sqlalchemy version_id_col`、`evennia strip network layer python mud`、`rmusser01 tldw_server RPG runtime authority.py proposals.py reducer.py`、`github.com/YakmovDenjys/Text-Game-Engine`、`docs.sqlalchemy.org/en/20/orm/versioning.html`

### 实际打开确认的仓库列表
- `YakmovDenjys/Text-Game-Engine`（https://github.com/YakmovDenjys/Text-Game-Engine）— 打开确认：MIT、2026-09 有提交、star 20、Python 3.10 兼容（`>=3.10`）、确认要点（README + SCHEMA.md：`row_version` 是回合提交的乐观 CAS 栅栏；`tge_inflight_turns(campaign_id, actor_id)` 唯一租约；Phase C 的 CAS 不一致会回滚全部写入）。
- `rmusser01/tldw_server`（https://github.com/rmusser01/tldw_server）— 打开确认：`tldw_Server_API/**` 为 GPL-3.0-only（前端 PolyForm Perimeter、OpenAPI 契约 Apache-2.0）、2026-11 有提交、star 约 1.5k、Python 3.10 兼容性未能确认、确认要点（实现计划：事件账本为唯一真相、快照是确定性缓存投影、模型来源变更默认走提案、按服务端推导的事件来源判定 authority、混来源批次拒绝、`expected_last_event_sequence` 与 `current_snapshot_version` 同事务校验、幂等响应重放）。
- `evennia/evennia`（https://github.com/evennia/evennia）— 打开确认：BSD-3-Clause、最近提交 2026-11、star 约 2.1k、Python 3.10 **不兼容**（`requires-python >=3.12`，依赖 Django 6.0.x 与 Twisted 24.x）、确认要点（README 与 pyproject：定位是“解决网络与基础服务器”的 MUD 框架，核心价值绑定 Django/Twisted 栈）。
- `neolaw84/MnesOS`（https://github.com/neolaw84/MnesOS）— 打开确认：无许可证文件、2026-07 有提交、star 0、Python 3.10 不兼容（3.12+）、确认要点（README：无状态后端 + 树状事件源，天然支持分支时间线，可作为“无状态 + 事件账本”的边界参考）。

### 11.1 唯一写入者与数据流

架构固定为：**多个角色 Agent 只生产提案，`ProposalQueue` 收集，`Resolver` 统一裁决，`GameEngine` 原子提交**。整条链路上只有 `GameEngine.submit` 能写 `GameState` 与 `EventLog`；仓储层把写方法设为私有（`_commit`），并加一条静态检查/代码评审规则：`persistence/` 之外禁止调用 `session.commit()`。

```text
玩家输入 ─┬→ 解析 Agent ─→ 提案 ┐
NPC 触发 ─┴→ 角色 Agent ─→ 提案 ┤
                                 ├→ ProposalQueue → Resolver（确定性）
系统事件 ─────────────────→ 提案 ┘        │
                                          ├→ 拒绝：记录理由，零写入
                                          └→ 通过：GameEngine.submit（单事务 CAS）
                                                       │
                                      EventLog（事件溯源）+ 快照 ─→ 下一回合感知切片
```

`authority/proposal` 分层（参考 tldw_server 的 authority 设计，但按本项目组件重写）：

| 提案来源 | authority | 是否可直接提交 | 约束 |
| --- | --- | --- | --- |
| 玩家显式动作 | `player` | 经 Resolver 后可提交 | 仍要被前置条件校验 |
| 规则/系统计算（伤害、掉落、时间推进） | `rule`/`system` | 随所属 Resolution 批量提交 | 必须携带 rule_id 与 rng_seed |
| 角色 Agent / 裁判 Agent | `model` | 不可直提交 | 必须经 Resolver；`reason` 必填 |
| 后台/作者工具 | `admin` | 可提交 | 必须写审计事件 |

关键红线：**authority 由服务端根据“谁产出的提案 + 工具策略”推导，绝不读取模型输出或客户端字段里的 authority/source**。同批次内混来源的提案一律拒绝——tldw_server 实现计划中“Mixed-source event batches are rejected”正是同一原则。

### 11.2 三道门

| 门 | 位置 | 检查内容 | 失败错误码 |
| --- | --- | --- | --- |
| 工具门 | `ToolRegistry`（第 7 章） | 工具存在、`actor_scope` 允许、参数 JSON Schema、`side_effect` 标签 | `NOT_FOUND`/`NOT_ALLOWED`/`SCHEMA_VIOLATION`/`INVALID_INPUT` |
| 裁决门 | `Resolver` | 前置条件对快照成立、排序、冲突检测、代理裁决证据与置信度、预算配额 | `NOT_ALLOWED`/`CONFLICT`/`OUT_OF_RANGE` |
| 提交门 | `GameEngine` | `expected_version` CAS、领域不变量（hp≥0、背包非负、位置合法）、幂等键、事件全局序号单调（跨回合连续）、Schema | `CONFLICT`/`SCHEMA_VIOLATION`/`INTERNAL` |

```python
class ProposalQueue:
    def enqueue(self, proposal: Proposal) -> None: ...
    def drain(self, max_items: int = 8) -> list[Proposal]: ...

class RuleEngine:
    def verify_agent_override(self, ctx: ToolContext, proposal: Proposal,
                              judge: JudgeOutput) -> Outcome: ...
    def deterministic_fallback(self, ctx: ToolContext, proposal: Proposal,
                               snapshot: GameState) -> Outcome: ...

class Resolver:
    def resolve(self, proposals: Sequence[Proposal], snapshot: GameState,
                judgements: Sequence[JudgeOutput] | None = None) -> list[Resolution]: ...

class GameEngine:
    def submit(self, expected_version: int, events: list[Event],
               idempotency_key: str) -> Result: ...
    def create_game(self, ctx: ToolContext, *, card_id: CardId,
                    seed: int | None = None, idempotency_key: str,
                    scenario: Literal["m2", "m3", "m4"] = "m2") -> Result: ...

class EventService:
    def replay_range(self, ctx: ToolContext, *, game_id: GameId,
                     from_seq: int = 0, limit: int = 200) -> Result: ...
```

`GameEngine.submit` 签名与第 21 章 §21.2.1 一致：`submit(expected_version: int, events: list[Event], idempotency_key: str) -> Result`；`expected_version` 为持久化层状态行的版本列 `version`（文档概念亦称 `row_version`，接口字段 `expected_version`；第 14 章 §14.3/§14.5），不写入 `GameState` 基线字段。`GameEngine.create_game` 用已保存的 `card_id` 创建存档，返回 `Result.data={game_id, turn, version, snapshot_version, scenario, save_seed}`（`version` 为下一次提交的 `expected_version`），并在同一事务内提交 `game_created` 系统事件（事件类型见第 14 章 §14.7：`game_created`）；写操作必带 `Idempotency-Key`。`EventService.replay_range(ctx, *, game_id, from_seq=0, limit=200)` 为只读分页回放，签名与第 6 章 §6.7 一致，按全局 `seq` 升序返回。`scenario` 是 M3/M4 追加的可选场景装配参数（`m2` 缺省单场景、`m3` 多角色、`m4` 制造场景，见 D-M4-11），缺省 `m2` 且 M2/M3 初始状态字节不变，既有 golden 不回归。`Resolver.resolve` 必须是纯函数：不调模型、不写库、不读时钟；同一输入必得同一 `Resolution` 列表，否则回放会漂移。

`Resolver.resolve` 的 `judgements` 由编排层在第 9 章步骤 6 调用 `JudgeAgent` 并完成 Schema 校验后传入（与 `proposals` 顺序对齐，可为空）；`needs_judge` 分支只对 `confidence >= 0.7` 且 `reason` 非空的证据调用 `RuleEngine.verify_agent_override(ctx, proposal, judge)` 复核，证据缺失或低置信时调用 `RuleEngine.deterministic_fallback(ctx, proposal, snapshot)` 做确定性兜底（保守、不产生有利效果）。这两个 `RuleEngine` 方法均为纯计算、零事件，返回 `Outcome`；`Resolver` 本身仍不调模型、不写库、不读时钟。

**M5E-2 实现口径（2026-11-11，D-M5-14）**：`needs_judge` 由 `RuleEngine(agent_assisted_tools=ToolRegistry.agent_assisted_tools())` 生产（默认空集 → preconditions `data` 逐字不变）；`Resolver` **懒读取** judgements——只有 needs_judge 槽位被访问，非 needs_judge 提案完全不读取（既有 ExplodingJudge 零访问安全测试保持）；`judge=None` 时走原路径；applied 结果的 `verdict_reason` 携带裁判理由（审计可追溯）；`RuleEngine.verify_agent_override` 入口严格校验 `JudgeEvidence` 类型 + `reason` 非空 str + `confidence` 有限数值（拒 NaN/Inf）且 verdict 必须 `allow`，非法一律「否」，`deterministic_fallback` 恒保守否决；编排层 `_collect_judgements` 在 CAS 重试循环外每回合最多 1 次，重试复用同批证据（只重裁决、不重调模型）。默认离线 `llm_enabled=false` 时 `judge=None`、零模型调用，行为逐字等价。

**自由制造的 M4 边界（D-M4-3 / D-M4-13）**：M4 制造（含自由路径）作为普通 `DESTRUCTIVE` 工具复用既有 M3 编排链路——玩家 `craft_item` 仍是 Proposal → `ProposalQueue` → 确定性 `Resolver` → `ToolGate` → `CraftService` → `GameEngine`，`scenario="m4"` 无 NPC Agent → 无跨角色冲突、无 Judge 覆盖；失败提案照常写 `audit_events`。D-M4-3 的「不接 Resolver」仅指自由制造草案本身不经裁决、`CraftProposal` 不进入 `ProposalQueue`（仅作为 M5 契约保留）。M5 接真实 LLM 前必须把草案生成迁到 pre-queue（入队前完成），否则「同一输入必得同一 `Resolution`」的纯函数约束被破坏。

**M5-023 批量/休眠的调用审计口径（2026-11-10，D-M5-12）**：角色 Agent 的批量合并与休眠调度在编排层 `orchestration/batch_sleep.py` 实现，**不改变**本章的唯一写入者/事件提交路径（提案仍经 `ProposalQueue`/`Resolver`/`GameEngine`）。口径：① 休眠状态机 `DORMANT→ARMED→ACTIVE→DORMANT`，唤醒仅三种（玩家动作指向 NPC / 进入同场景 / 计划触发回合），ACTIVE 窗口=唤醒回合+2，其余回合零调用、只写 `SKIPPED` 审计；② 批量合并只对组合根声明的「同场景公共群演」开放（每批 ≤4，输入只含公共感知、无私有记忆）；角色 Agent 的私有记忆与批量互斥（有私有记忆一律串行）；单 NPC 失败只丢该 NPC（`DROPPED`），整批异常降级逐 NPC 串行；③ 调用审计用**进程内可注入 `InvocationLog` 端口 + 结构化日志**（不新增 `agent_invocation_log` 表/迁移，D-M5-12），`turn_orchestrator` 通过可选 `agent_scheduler` 注入、默认 `None` 保持原逐 Agent 循环；④ 生产组合根当前未注入调度器（默认零调用）。验收 `tests/integration/test_batch_sleep.py` 20 例。

### 11.3 CAS 乐观锁与冲突重试

持久化用 SQLAlchemy 的版本计数器（version counter）实现 CAS，行为已在 SQLAlchemy 2.0 文档《Configuring a Version Counter》确认：UPDATE 会附带 `WHERE game_id=? AND version=?`（实现列名 `version`；文档概念称 `row_version`），匹配 0 行时抛 `StaleDataError`；版本列建议 `NOT NULL`。

```python
class GameStateCurrentRow(Base):
    __tablename__ = "game_state_current"
    game_id = mapped_column(String(64), primary_key=True)
    version = mapped_column(Integer, nullable=False)   # 实现列名 version（文档概念 row_version）
    state_json = mapped_column(JSON)
    __mapper_args__ = {"version_id_col": version}
```

表名与版本列与第 14 章 §14.3 一致（`game_state_current.version`，实现列名；文档概念称 `row_version`，接口字段 `expected_version`）；实现口径同第 14 章 §14.5 的显式 CAS：`UPDATE game_state_current SET ..., version = version + 1 WHERE game_id=? AND version=?`，影响行数为 0 即冲突（与上面 `version_id_col` 自动追加的 `WHERE` 条件等价）。

提交循环（注意：**重试只重裁决，不重调模型**）：

```python
def run_turn(proposals: list[Proposal], idempotency_key: str) -> Result:
    for attempt in range(3):
        snapshot = repo.load_snapshot()
        resolutions = resolver.resolve(proposals, snapshot)   # 纯函数
        events = [e for r in resolutions if r.verdict == "applied"
                  for e in r.applied_events]
        try:
            return engine.submit(snapshot.version, events, idempotency_key)
        except StaleDataError:                 # 被并发回合抢先提交
            proposals = revalidate(proposals, snapshot)  # 仅按新快照重校验
            continue
    return Result(ok=False, error_code=ErrorCode.CONFLICT,
                  message="state changed, retry later")
```

每回合最多 3 次尝试（首次 + 2 次重试）。重试语义：读快照 → 裁决 → 提交，冲突则重新读快照、用**同一批提案参数**重新裁决；若新状态下前置条件已不成立，把对应 `Resolution.verdict` 置为 `rejected`（理由 `state_changed`），而不是报错。LLM 调用在事务外（第 9 章），因此重试不会产生额外 token 成本，也不会重复叙事。

排序与冲突规则（保证回放确定性）：

1. 排序键 `(priority desc, enqueue_seq asc, actor_id asc)`，玩家动作默认 `priority=100`，NPC 默认 50；
2. 资源竞争（两人抢同一物品）：先裁决者得，后者 `rejected` 并附理由；
3. 目标状态冲突（治疗 vs 伤害同一目标）：按排序顺序依次应用，后者的前置条件基于前者结果重新校验；
4. 同一 actor 的多提案合并为一次裁决，避免自我竞争；
5. 死锁预防：`Resolver` 不调模型、不开嵌套事务、不等待外部 IO。

### 11.4 审计与恢复

**状态词分两套，不得混写**：`Resolution.verdict` 是裁决结论，取值固定 `{applied, rejected, conflict, deferred}`；提案/审计状态是队列与提交层状态——提案生命周期 `pending → resolved / expired`（`resolved` 的最终含义由对应 `verdict` 表达，`expired` 仅表示回合终止时未裁决），审计状态固定 `{committed, conflicted, aborted}`（提交成功/提交冲突/回合中止）。三套词各归其位：`verdict` 只出现在 `Resolution`；提案状态只出现在 `ProposalQueue`/提案表；审计状态只出现在审计表与回合状态。事件基线字段不扩展，`proposal_id`/`resolution_id` 写入 `Event.params`，`seq` 为全局事件序号（每存档从 1 起、跨回合连续，`id = f"evt_{seq:06d}"`，由 `GameEngine` 在提交事务内分配）；审计明细单独存表，供第 14 章回放与第 15 章作弊分析使用。幂等键作用域为 `(turn_id, actor_id, tool, args_hash)`，重复提交直接返回上次 `Result`。

恢复策略（待定项，3 方案）：A 队列仅在内存，进程重启后未完成回合的审计状态标记为 `aborted`，玩家重发（MVP 推荐，最简单且不会提交半成品）；B 持久化 `pending` 提案，重启后重新裁决（M3 推荐，成本是提案表与恢复器）；C 持久化 + 租约（M5，多进程/多副本时必要）。三者不冲突，按里程碑演进。

### 11.5 候选轮子

| 仓库 | 定位 | 可复用部分 | 许可证 | 活跃度 | 建议 |
| --- | --- | --- | --- | --- | --- |
| `YakmovDenjys/Text-Game-Engine` | 回合引擎 CAS + 租约 | `row_version` CAS、inflight 租约、Phase A/B/C 事务切分 | MIT | 2026-09 有提交，star 20 | 参考设计 |
| `rmusser01/tldw_server` | RPG 后端实现计划（提案/权威分层） | authority vs proposal 决策、事件序列/快照版本同事务校验、混来源批次拒绝、幂等重放 | GPL-3.0-only（服务端代码） | 2026-11 有提交，star 约 1.5k | 参考设计 |
| `evennia/evennia` | Python MUD 框架 | 剥离网络层只取对象/持久化（评估结论：不划算） | BSD-3-Clause | 2026-11 有提交，star 约 2.1k | 放弃 |
| `neolaw84/MnesOS` | 无状态事件溯源 RPG | 无状态后端 + 分支时间线的事件账本边界 | 无许可证文件 | 2026-07 有提交，star 0 | 参考设计 |

复用判断与拼接边界：CAS 自建，用 SQLAlchemy `version_id_col`（约 120 行含重试与审计），只参考 Text-Game-Engine 的“三段事务 + 租约 + 冲突回滚”模式，不拷贝其运行时（MIT 允许拷贝，但其代码与 Discord/LLM 后端耦合，维护成本高于自写）。tldw_server 的 authority/proposal 分层与序列校验是最贴近本项目的设计，但服务端代码为 GPL-3.0-only，只能参考设计、禁止拷贝或作为依赖。Evennia 的结论是**放弃网络层剥离**：其价值绑定 Django/Twisted 栈，且要求 Python 3.12+，剥出来的对象层会与 `GameEngine` 形成第二套状态权威，违背“唯一写入者”；只有其“持久化对象 + 类型化命令”思路可作为常识性参考。MnesOS 无许可证，仅参考其无状态事件账本。

### 本章结论

1. 唯一写入者是硬约束：只有 `GameEngine.submit` 能写状态，`ProposalQueue`/`Resolver` 均为纯内存、纯函数组件。
2. 三道门缺一不可：工具门管“能不能调”，裁决门管“该不该生效”，提交门管“写得对不对”；每道门返回基线的 `ErrorCode`。
3. CAS 用 SQLAlchemy `version_id_col`，冲突最多 3 次尝试（首次 + 2 次重试），只重裁决不重调模型；重试后失效的提案降级为 `rejected`（`state_changed`），不报 500。
4. authority 必须服务端推导、禁止来自模型输出，混来源批次拒绝；所有提案状态迁移写审计表，`proposal_id` 写入 `Event.params` 以保持事件基线字段不变。
5. 执行建议：M3 交付“并发两回合抢同一物品”的集成测试（断言只有一个 `applied`、另一个 `rejected` 且事件全局序号连续），并把队列恢复策略 A→B 列入 M3/M5 里程碑验收。
