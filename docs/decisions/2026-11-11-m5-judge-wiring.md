# ADR · M5 出口附加项：裁判 Agent 接线（D-M5-14）

- 状态：已定案（只追加）｜决策人：lead（依用户 2026-11-10 23:55「直到开发结束」指示代授权；用户审批策略已改为不询问）｜复核：backend-dev 实现报告 + qa-tester 独立红队（52 例全绿）｜日期：2026-11-11｜基线：`1c82c47`（S2/R3 已提交，M6 4/4 完成）
- 关联：M5 出口附加项（不占 019–024 编号）；D-M5-13（解析/叙事/角色接线口径）、第 8/9/10/11/12/17/21 章；PROGRESS §5；进度文档 `docs/progress/M5E-2-2026-11-11-judge-wiring.md`

## 1. 背景与红线

- M5 出口附加项中「裁判 Agent 接线」此前登记为**待用户授权改 Resolver**（D-TEAM-2、PROGRESS §5 D-M5-13）。用户本轮指示「检查进展并把接下来的任务交给团队，分阶段开发测试提交 git，直到开发结束」，且审批策略改为不询问；Lead 据此代授权本切片。
- 红线（不可放宽）：① 默认离线（`TEXTGAME_LLM_ENABLED=false`）行为**逐字等价、零模型调用**；② 旧 golden 61 + provenance 7 零改动；③ `demo --check` 7 屏、`console --check` 逐字不变；④ Resolver 保持纯函数（不调模型、不写库、不读时钟）；⑤ 不新增运行时依赖；⑥ 非 `needs_judge` 提案**完全不读取** judgements（既有 ExplodingJudge 零访问安全测试保持）。
- 事实前提：生产 8 个注册工具全部 `resolution_policy=DETERMINISTIC`，`needs_judge` 当前**无消费者**；本切片是「机制就绪 + 测试注入证明分支可达」，不改变任何生产行为。

## 2. 决策

1. **契约与分层**：`domain/judge.py` 定义 `JudgeEvidence`（域层复核 DTO）/`JudgeOutput`（Agent 输出契约，同构，含 `verdict ∈ {allow, deny, modify}` + 只读 `allowed`）、`JUDGE_CONFIDENCE_FLOOR=0.7`、`MAX_EVIDENCE_REFS=5`（第 8 章 §8.2 原写 `agents/schemas.py` 不存在且会破坏分层，改为 domain DTO + agents 层再导出）。
2. **Agent**：`agents/judge_agent.py` 提供 `JudgeAgentLike` 协议、`DeterministicJudgeAgent`（测试替身）、`LLMJudgeAgent`（`task="judge"`、`prompt_version="judge@v1"`、STRONG、`max_repair=1`；输入=**脱内部 ID 的私有感知切片 + 提案 + 规则空白说明**，证据必须落在切片内；任何失败/异常→`None` 交由确定性兜底）。
3. **`needs_judge` 生产点**：`ToolRegistry.agent_assisted_tools()`（只读派生：`resolution_policy=AGENT_ASSISTED` 的工具名集合）→ `RuleEngine(agent_assisted_tools=...)` → `evaluate_preconditions` 的 `data["needs_judge"]`。默认空集时 data 仍逐字 `{"allowed": True, "reason": ""}`（既有 `test_rules.py` 不改通过）。当前生产 0 消费者（如实登记，非缺陷）。
4. **RuleEngine 复核**：`verify_agent_override(ctx, proposal, judge)` + `deterministic_fallback(ctx, proposal, snapshot)` 纯计算、零事件、零写入；`verify` 入口**严格类型校验**（`isinstance(JudgeEvidence)`、`reason` 必须非空 `str`、`confidence` 必须有限数值且非 bool（拒绝 NaN/Inf）、`verdict` 必须 `allow`），非法一律「否」，不抛异常；`fallback` 恒保守否决（不产生有利效果）。
5. **Resolver**：打开 `needs_judge` 分支——**懒读取** judgements（只有 needs_judge 槽位被访问；非 needs 提案零访问，ExplodingJudge 口径不变）；证据缺失/低置信/拒绝 → `deterministic_fallback`；applied 结果 `verdict_reason` 携带裁判理由（审计可追溯）；`judge=None`/judgements 为空时走原路径。
6. **编排层**：`TurnOrchestrator` 可选 `judge` 注入（默认 `None`）；`_collect_judgements` 在 CAS 重试循环**外**构建一次、每回合最多 1 次裁判调用、CAS 重试复用同批证据（「只重裁决、不重调模型」）；`judge=None` 直接返回 None（不构建感知切片、零模型调用）；裁判异常兜底为 None。
7. **组合根**：`main.py` 中 `RuleEngine(agent_assisted_tools=registry.agent_assisted_tools())`；仅 `llm_enabled=true` 时构造 `LLMJudgeAgent` 并注入编排器，false → `judge=None`。
8. **测试**：`tests/unit/{test_judge_agent,test_rule_engine_judge,test_resolver_judge}.py` + `tests/integration/test_judge_wiring.py` 共 64 例；5 个手工合成 cassette（零密钥零网络，沿用 D-M5-13 口径）；QA 独立红队 `tests/integration/test_judge_redteam.py`（52 例，独立文件不 import backend 测试）。

**理由**：① 裁判是第 10/11 章「规则优先、智能体兜底」的最后一块出口接线，缺它则 LLM Agent 接线不完整；② 以「机制就绪 + 测试注入」落地可在**零生产行为改变**前提下完成契约/安全/回放验证，风险最小；③ 严格类型校验堵住 duck 对象/`model_construct` 绕过 pydantic 的防御性缺口（QA 红队发现，已修复）；④ 懒读取 judgements 保持既有安全测试语义。

**被否**：A 直接给某工具改 `AGENT_ASSISTED` 以「启用」裁判（会改变生产语义与 golden，且无工具专用 fallback 规则，属范围外）；B 裁判调用放在 CAS 重试循环内（重试会重复调模型，违反「只重裁决」）；C 去掉 `needs_judge` 分支只保留接口（无法证明分支可达，M5 出口不可验收）；D 在 `agents/schemas.py` 放 DTO（破坏分层且文件不存在）。

**影响面**：无生产行为变化（默认离线逐字等价）；新增 2 个源文件 + 5 个改动源文件；事件/快照/API/JSON Schema 零变更；新增 64 + 52 例测试与 5 个 cassette；后续接入 `agent_assisted` 工具时零代码改动即可启用（工具声明即可，视需要扩展 fallback 规则）。

## 3. 默认离线逐字等价证明链（验收断言）

| 层 | 证明 | 证据 |
| --- | --- | --- |
| 数据契约 | 事件/快照/TurnResult/API Schema 零变更 | 全量 **1759 passed**；`tests/fixtures/golden` 零改动 |
| RuleEngine | 默认空集 → preconditions data 逐字 `{"allowed":True,"reason":""}` | `test_default_engine_preconditions_data_is_unchanged` + 既有 `test_rules.py` 不改通过 |
| Resolver | 非 needs 提案完全不读 judgements（ExplodingJudge 零访问）；`judge=None` 走原路径 | 既有 2 条 ExplodingJudge 测试不改通过 + 新增 V1 |
| 编排层 | `judge=None` → `_collect_judgements` 立即 None（零切片/零调用）；与省略参数结果逐字相等（去盖章 ts） | `test_judge_none_is_bitwise_equivalent_to_omitted` |
| 组合根 | `llm_enabled=false` → `_judge is None`；`true` → 构造但集合空 → `task=="judge"` 调用数 0 | `test_default_offline_assembly*`、`test_production_llm_enabled_assembly*` |
| 回归门 | replay 68（旧 golden 61 零改动）、demo 7 屏字数、console --check、四门 | 全绿 |

## 4. 回写落点

- 第 8 章（JudgeAgent 设计/DTO 层归属/prompt v1）、第 11 章 §11.2（`needs_judge` 实现口径与懒读取）、第 12 章（`task="judge"`/`prompt_version="judge@v1"`/cassette 测试口径）、第 17 章 M5 实施备注；`docs/PROGRESS.md` §3/§5/§7；CHANGELOG；进度文档 `M5E-2-2026-11-11-judge-wiring.md`。

## 5. 已知限制/后续

1. 生产 `needs_judge` 无消费者（8 工具全 deterministic；机制就绪，测试注入可达）——后续接入 agent_assisted 工具时启用。
2. `deterministic_fallback` 当前恒保守拒绝（无工具专用规则）；`modify` 语义保守拒绝（不含可机械应用的数值，符合「不产生有利效果」）。
3. 裁判调用为进程内每回合最多 1 次，跨进程/持久化调用审计仍归后续（D-M5-12 口径）。
4. cassette 为手工合成；真实 API smoke 归 S4（默认仍离线，不改变默认装配）。

## 6. 变更纪律

- 只增不改；推翻须新增 ADR（`supersedes: D-M5-14`）→ 回写章节 → 重跑全量 + replay + demo/console → 更新 PROGRESS §5。任何会改变默认离线行为的改动必须单独 ADR + 旧 golden 影响评估。
