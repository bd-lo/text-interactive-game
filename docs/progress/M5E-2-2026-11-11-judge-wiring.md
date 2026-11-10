# M5E-2 · 2026-11-11 · M5 出口附加项 · judge-wiring

> 切片目标一句话：完成 M5 出口附加项的最后一块——**裁判 Agent 接线**（`needs_judge` 生产点 + `JudgeAgent`/`LLMJudgeAgent` + `RuleEngine.verify_agent_override/deterministic_fallback` + Resolver 分支 + cassette 测试），保持**默认离线逐字等价、零模型调用**；不占 019–024 编号 ｜ 验收命令：`uv run pytest -q`、`uv run pytest tests/replay -q`、`uv run python -m textgame.demo --check`、`uv run python -m textgame.console --check`、定向 judge 测试 ｜ 决策：D-M5-14（ADR 2026-11-11-m5-judge-wiring）

## 1. 目标与验收命令

- 目标（第 8/9/10/11/12 章、D-M5-14）：① `domain/judge.py` 契约（`JudgeEvidence`/`JudgeOutput`/verdict/置信门槛 0.7/证据上限 5）；② `agents/judge_agent.py`（协议 + 确定性替身 + `LLMJudgeAgent`：`task="judge"`/`prompt_version="judge@v1"`/STRONG/`max_repair=1`/脱内部 ID 私有感知切片/失败→None）；③ `ToolRegistry.agent_assisted_tools()` 生产 `needs_judge`（默认空集、data 逐字不变）；④ `RuleEngine.verify_agent_override`（严格类型/NaN 校验，非法一律否）+ `deterministic_fallback`（恒保守否决、零事件）；⑤ `Resolver` needs_judge 分支（懒读 judgements、applied reason 可审计、非 needs 零访问）；⑥ `TurnOrchestrator` 可选 judge（默认 None、CAS 循环外一次、每回合≤1 次、异常兜底）；⑦ `main.py` 仅 `llm_enabled=true` 构造 LLMJudgeAgent。
- 授权：用户 2026-11-10 指示「直到开发结束」且审批策略不询问；Lead 按 D-M5-14 代授权，硬约束=默认离线逐字等价、零 golden 改动。
- 验收命令与真实输出：
  - `uv run pytest -q` → **1759 passed**（S2/R3 后基线 1695 + 本片 64）
  - `uv run pytest tests/replay -q` → **68 passed**（旧 golden 61 零改动 + provenance 7）；`git status tests/fixtures/golden` 空
  - `demo --check` → exit 0（7/7，屏字数 114/83/81/85/82/84/81 不变）；`console --check` → exit 0（4/4，含失败样本）
  - 定向：`uv run pytest tests/unit/test_judge_agent.py tests/unit/test_rule_engine_judge.py tests/unit/test_resolver_judge.py tests/integration/test_judge_wiring.py -q` → **64 passed**
  - 四门：ruff format 206 files / ruff check / mypy 95 files / lint-imports 2 kept；`check_docs` PASS
- 不做什么：不改默认工具策略（8 工具仍全部 `DETERMINISTIC`，`needs_judge` 生产无消费者，机制就绪）；不改事件/快照/API Schema；不引入新依赖；不做真实 API 调用（cassette 零网络，S4 手动 smoke）。

## 2. 改动与提交

- 提交：`feat(llm): M5E-2 裁判 Agent 接线（needs_judge/JudgeAgent/cassette，默认离线等价）`
- 新增：`src/textgame/domain/judge.py`、`src/textgame/agents/judge_agent.py`；`tests/unit/{test_judge_agent,test_rule_engine_judge,test_resolver_judge}.py`、`tests/integration/test_judge_wiring.py`；`tests/fixtures/cassettes/judge_{allow,deny,low_confidence,repair,invalid_twice}.json`（手工合成、零密钥、结构兼容 `interactions`）。
- 修改：`src/textgame/domain/rules/engine.py`（needs_judge 生产 + verify/fallback + 红队加固）、`src/textgame/orchestration/resolver.py`（needs_judge 分支/懒读取/reason 透传）、`src/textgame/orchestration/turn_orchestrator.py`（judge 注入 + `_collect_judgements`）、`src/textgame/tools/registry.py`（`agent_assisted_tools()`）、`src/textgame/main.py`（组合根）。
- 契约变更：**无生产契约变更**（事件/快照/TurnResult/API 均不变）；新增内部端口 `JudgeAgentLike` 与 domain DTO（D-M5-14 登记层归属）；生产 `needs_judge` 无消费者如实登记。

## 3. 验证结果

| 检查 | 结果 |
| --- | --- |
| 全量/回放 | **1759 passed**；replay **68 passed**；旧 golden 零改动 |
| demo/console 逐字 | demo 7 屏字数 114/83/81/85/82/84/81 不变；console 4/4 含失败样本不变 |
| 默认等价 | `llm_enabled=false → judge=None → _collect_judgements 立即 None`（零切片/零调用）；`judge=None` 与省略参数结果逐字相等（去盖章 ts）；RuleEngine 默认 data 逐字不变；非 needs 提案不读 judgements（ExplodingJudge 零访问） |
| 分支可达（测试注入） | needs_judge applied（reason 透传）/rejected/低置信→fallback/证据缺失→fallback/judge 异常→兜底/LLM 不可用→兜底/每回合≤1 次/CAS 重试复用证据/零写入 |
| 红队 | 非法 Schema、duck 对象、`model_construct` 绕过、confidence 越界/NaN/Inf、reason 空/非 str、verdict 非法 → 一律「否/兜底」、不抛异常、零写入；QA 独立红队 52 例全绿（含 4 例发现的加固） |
| 依赖/安全 | 零新增依赖；cassette 零密钥零网络；prompt 不含内部 ID/完整状态 |
| 失败与回滚 | 无（默认路径零行为变化；回滚点=本提交前 `1c82c47`） |

## 4. 下一步与风险

- 下一步：**S4 收尾**——全量门禁终检、真实 LLM smoke（可选，默认装配仍离线）、最终独立玩家验收、文档回填（本进度文档 + PROGRESS/CHANGELOG/章节）、push 与远端 CI 复核。
- 已知限制（D-M5-14 §5）：生产 `needs_judge` 无消费者（机制就绪）；`deterministic_fallback` 恒保守拒绝、`modify` 保守拒绝（无工具专用规则，未来按 Ruleset 扩展）；裁判调用审计仍为进程内口径；cassette 手工合成。**S4 真实 LLM smoke（2026-11-11，可选、不改变默认装配）**：`python -m textgame.llm.smoke` OK（deepseek-flash、4.73s），story 3 回合真实 LLM 手跑全 ok（事件 `result.narrative_source="llm"`、87–141 字）；默认 `TEXTGAME_LLM_ENABLED=false` 仍零联网。
- 需要人工决策：无（本批）；后续若要「真实启用裁判」，需选定 `agent_assisted` 工具并评估对其 golden/契约的影响（单独 ADR）。
