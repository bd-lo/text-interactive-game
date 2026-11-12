# ADR · R-19 pre-queue 迁移：自由制造 LLM 调用移出 Resolver（D-R6-3）

- 状态：**已定案**｜决策人：lead｜整理：docs-release｜日期：2026-11-12｜基线：`f937e48` + R6 未提交工作树
- 关联：R5 §4 第 4 条（R-19 Resolver 内 LLM 风险）；[R6 进度文档](../progress/R6-2026-11-12-leftovers.md)；第 11/13 章
- 关联编号：R-19（技术风险审查）、D-M4-3（制造不接 Resolver 的原始口径）

## 1. 背景与红线

自由制造（R-19）此前在 **Resolver 裁决链路内**调 LLM 生成草稿：CAS 冲突重试会重复计费，也违反第 11 章「Resolver 为纯函数、LLM 提案只来自预生成队列」的设计约束。红线：① Resolver 内**零 LLM 调用**（含重试）；② 默认关闭/离线装配行为与 golden 逐字不变；③ 数值/成败仍由框架 `calculate_craft_result` 决定，模型只给名称/材料/工步。

## 2. 决策

1. **两段式**：编排层在玩家提案入队/裁决**之前**预生成一次 `CraftDraft`（`tools/gate.py::prepare_free_craft` / `services/craft_service.py::prepare_free_craft`，内含一次 Schema 修复）。
2. **载荷经数据传递**：`resolver.py::resolve(..., prepared_crafts=...)` 按 `proposal_id` 透传预生成载荷（**不塞 `Proposal.args`**，避免扩大工具 Schema 暴露面）；CAS 重试复用同一载荷。
3. `services/craft_service.py::_execute_prepared_free_craft` 只做纯校验/框架重算（`calculate_craft_result`），不再调生成器。
4. **红线**：`ctx.caller=="resolver"` 且无预生成载荷 → `INVALID_INPUT`，**永不**回落到服务内生成器；旧「服务内生成」路径只保留给 M2/直接 Gate（`caller=player`）。
5. **兼容开关保留**：`TEXTGAME_ALLOW_RESOLVER_LLM` fail-fast 与网页 `accept_resolver_llm_risk` 二次确认**保留为历史兼容**（技术风险已消除，M5 后评估简化）；仅更新文案（main.py/free_craft.py/routes/settings.py/craft_service.py/.env.example + web 设置页）。
6. **NPC 自由制造**：内置确定性 Agent 不产生该提案；需要时扩 pre-queue（当前 NPC 自由制造提案零 LLM、安全拒绝）。
7. **错误码保 golden**：失败对外沿用 `INVALID_INPUT`（具体原因在审计 `verdict_reason`/effects hint），不新增错误码。

## 3. 理由与被否选项

- 理由：把「一次生成」挪到裁决前，CAS 重试天然复用；Resolver 恢复纯函数语义；LLM 成本可预期（每回合最多 1 次自由制造生成）。
- 被否：① 把载荷塞 `Proposal.args`（扩大工具 Schema/暴露面，工具白名单与契约需变）；② Resolver 内缓存生成结果（仍是 Resolver 调 LLM，风险未消除）；③ 直接删掉自由制造（用户需求保留）。
- 代价：pre-queue 在「玩家提案被裁决拒绝」时仍可能产生一次生成调用（成本换纯度，见 §5）。

## 4. 影响面与回写落点

- 实现：新增 `src/textgame/orchestration/pre_queue.py`；`tools/gate.py`/`services/craft_service.py`/`orchestration/resolver.py`/`turn_orchestrator.py`；`services/free_craft.py` 文案。
- 测试：新增 16 例（`tests/integration/test_free_craft_pre_queue.py` 6 + `tests/unit/test_craft_pre_queue.py` 10）：pre-queue 生成 1 次、两次裁决（含 1 次 CAS 冲突重试）LLM 调用恒 1、resolver 无 complete_json/generator 调用、无载荷红线、默认关闭逐字等价、m4 golden 5 例零改动。
- 文档：第 13 章 §13.4、第 12 章 §12.11、第 23 章设置页文案口径；`.env.example` 注释。
- FakeLLM 探针证据（不入库）证明 Resolver 内零 LLM；默认离线/关闭行为零变化。

## 5. 已知问题归属

| 编号 | 摘要 | 归属 | 关闭条件 |
| --- | --- | --- | --- |
| R-19（已收口） | Resolver 内 LLM 调用已消除；pre-queue 在拒绝场景可能多一次生成成本 | 本 ADR | 成本可接受；如需优化在裁决前先做低成本可行性预筛 |
| — | 兼容开关（ALLOW_RESOLVER_LLM/risk 确认）技术风险已消除，属历史兼容 | M5 后评估简化 | 用户确认可移除时删除 env/设置页二次确认并回写文档 |
| — | NPC 自由制造提案零 LLM 拒绝 | 保持（内置 Agent 不产生） | 需要 NPC 自由制造时扩 pre-queue + 新 ADR |

## 6. 出口验收判据

- Resolver 内生成器调用数为 0（FakeLLM 探针）；pre-queue 1 次生成 + CAS 重试仍 1 次真实/假装调用；`caller=resolver` 无载荷 → `INVALID_INPUT` 零写入；默认关闭/离线 golden 零改动；新增 16 例 + 全量 pytest 全绿。

## 7. 复审与变更纪律

- 触发复审：M5 后移除兼容开关、NPC 自由制造接入、或自由制造成本/延迟需要预筛时。
- 本 ADR 只增不改；推翻结论 = 新 ADR + `supersedes: D-R6-3` + 回写第 12/13 章与实现。
