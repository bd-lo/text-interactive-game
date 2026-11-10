# ADR · 022 缩范围与成本记录不做（D-M5-10）

- 状态：已定案（只追加）｜决策人：lead｜复核人：Round 2 四角色交叉质询（backend-dev / frontend-dev / player-rep / oss-user-rep；立场书 `.work/team-next/`，不入库）+ Lead 复测（HEAD `cb7863b`，全量 1179 passed、replay 61）｜日期：2026-11-10｜基线：`cb7863b`（=origin/main，工作树 clean）
- 关联：M5 022 切片、M5 出口判据、M6 后端前置卡库（Alembic 0003）；第 12 章（权威）、第 17 章 §17.2、第 20 章 §20.2/§20.4；`docs/PROGRESS.md` §5/§7；D-M4-15/G-1、D-M5-9（C-1/C-2）、D-M6-3（卡库持久化迁移编号）
- 触发：用户 2026-11-10 拍板——**M5 不做成本记录**（原文口径：连成本记录也不做），022 只保留并发幂等；Round 2 四方一致同意该缩范围（backend-dev 提出 022 拆片，frontend-dev/player-rep/oss-user-rep 接受；oss-user-rep 补充：离线公开演示不依赖成本治理）。

## 1. 背景与红线

- 原 `022-cost-budget` 范围（第 12 章 §12.4/§12.7/§12.8、第 17 章 §17.2 M5 表）：自建价格表 + `estimate_cost` + 按 `agent_id`/`turn_id` 成本归集 + 预算降级（80% 告警/100% 强制降级）+ `llm_call_log` 表 + Alembic 0003 + 关闭 D-M4-15 并发同键（G-1）。
- 019 现状：`LLMCallRecord.cost_usd` 恒 0、无价格表、幂等缓存为进程内 `InMemoryLLMCallLog`（`llm/records.py`）；仓库仅 Alembic 0001/0002（0003 未占用）；`LLMClient` 实际无 `estimate_cost` 实现（仅第 12 章接口草案）。
- 用户决定后，成本/预算/价格表/持久化调用日志均不进入承诺；**并发同 `Idempotency-Key` 幂等（D-M4-15/G-1）仍是 M5 治理项**，且是 UI 自动重试与公网部署的硬门（D-TEAM-2 门控不变）。
- 红线不变：唯一写入者、事件溯源/回放契约、`actor_scope`、密钥不入库、不新增依赖、M2–M4 golden 零回归（1179 passed / replay 61 为本 ADR 基线）。

## 2. 决策（D-M5-10，逐条）

1. **022 更名缩范围**：`022-cost-budget` → **`022-concurrency-idempotency`**，唯一目标 = 关闭 D-M4-15/G-1（并发同 `Idempotency-Key` 的响应一致性/只提交一次；串行重试可重放首结果）。验收命令 `uv run pytest tests/integration/test_idempotency_concurrency.py`（并发同键响应一致，或显式 `CONFLICT` + 零额外写入）。
2. **成本/预算/价格表不做**：不建自建价格表、不实现 `estimate_cost`、不按 Agent/回合归集成本、不做预算降级与 80%/100% 阈值告警、不写「每回合成本环比」之类判据。第 12.8 成本 SLO（单回合 ≤¥0.20 / 单会话 ≤¥3 / 单日 ≤¥200）**登记为用户决定不实现**：保留为设计口径/后续复评参考，不得作为任何切片或 M5 出口的验收判据。
3. **`llm_call_log` 表与 Alembic 0003 不做**：不新增持久化调用日志、不建唯一索引；019 的进程内 `LLMCallLog`（含失败记录）保留，仅用于进程内幂等/审计/调试，**跨重启不保留**；`LLMCallRecord.cost_usd` 字段保留但恒 0，不新增语义。
4. **Alembic 0003 编号顺延给 M6 卡库**：原 D-M6-3 写的「卡片持久化 Alembic 0004（若 022 先占 0003 则顺延）」调整为 **M6 `character_cards` 使用 0003**；仅迁移编号调整，D-M6-1..6 的端点/DTO/验收契约不变（契约正文由 M6 ADR 持有，本 ADR 只改编号归属）。即「**D-M6-3 原文 0004，按 D-M5-10 仅编号顺延为 0003**」——不构成契约语义变更，按只增不改纪律**不另开 supersede ADR**、不改 D-M6-3 原文。
5. **M5 出口判据调整**：M5 6 个切片口径不变（019–024 仍 6/6 计），但 022 验收从「成本归集与预算」改为「并发同键幂等」；第 17 章 M5 Demo/出口条件中的「成本报表按 Agent/回合拆分」删除，替换为「并发同 `Idempotency-Key` 无重复提交/响应一致」；不创建 `tests/unit/test_cost.py`。
6. **D-M5-9 到期承诺处置不变但不再折入 022**：C-1（覆盖率 ≥80% 门禁）与 C-2（进度文档 CI 校验 `scripts/check_docs.py`）保持「M5 出口前到期承诺」，由 Lead 安排到 021/024/023 批次（不因本 ADR 取消、也不并入 022）；D-M5-9 C-3 口径不变。
7. **可观测替代最小集**（非成本）：保留 019 已有 token 计量、`latency_ms`、`cache_hit`、错误码与结构化日志；保留单回合调用数上限（默认 2、最多 3）与 timeout 设计口径。若将来默认开真实 LLM 或部署公网，先按第 20 章风险流程评估是否恢复成本观测（需新 ADR `supersedes: D-M5-10`）。
8. **遗留文本**：`src/textgame/llm/records.py` 模块 docstring 仍写「022：`llm_call_log` 表 + Alembic 0003……」，属历史承诺文本；本批为文档轨、禁改 src，登记为「待后续代码切片清理」的已知残留（不改变运行行为，不影响验收）。

## 3. 契约 → 回写落点

| 位置 | 现状 | 本 ADR 后 |
| --- | --- | --- |
| 第 12 章 §12.2 接口 | `estimate_cost` 注「022 落地」、`llm_call_log` 建表 | 注「用户决定不实现（D-M5-10）」；调用记录仅进程内 |
| 第 12 章 §12.4/§12.7 | 价格字段由 022 消费、`llm_call_log` 唯一索引 | 仅 024 消费回退字段；幂等为进程内，不建表/不建索引 |
| 第 12 章 §12.8 | 成本预算/SLO 表 | 表保留、加「用户决定不实现（D-M5-10）」声明，不作验收判据 |
| 第 12 章 结论 6/7 | 含成本 SLO 与成本归集 | 结论 6 改为「设计口径、用户决定不实现」；结论 7 补 D-M5-10 |
| 第 17 章 §17.2 M5 表 | 022-cost-budget 行 + M5 出口 Demo 含成本报表 | 022-concurrency-idempotency 行；Demo 换「并发同键一致」；M6 备注卡库迁移改 0003 |
| 第 20 章 §20.2/§20.4/结论 3 | R-C1/R-C2 含成本归集缓解、结论 3 要求「每回合成本环比」 | R-C1/R-C2 成本缓解降级为「接受风险（不归集）」，保留非成本缓解；结论 3 改为延迟/存储埋点 + 调用数/timeout/token 可观测 |
| `docs/PROGRESS.md` §5/§7 | 022-cost-budget（价格表/llm_call_log/0003）为下一步 | §5 增 D-M5-10 行、D-M4-15 owner 改 022-concurrency-idempotency；§7 下一步改为 022-concurrency-idempotency |
| `README.md` 状态段/LLM 配置段 | 「成本/预算归 M5 后续切片」 | 指向 D-M5-10；README 不得出现未发布版本的 tag/Release 表述 |

## 4. 已知问题归属

| 编号 | 问题 | 归属 | 关闭条件 |
| --- | --- | --- | --- |
| D-M4-15 / G-1 | 并发同 `Idempotency-Key` 瞬时响应不一致 | **022-concurrency-idempotency**（本 ADR 后唯一 022 目标） | `test_idempotency_concurrency.py` 并发用例全绿，PROGRESS §5 状态改「已关闭」 |
| M6 卡库迁移 | 原 D-M6-3 写 0004 | **M6 后端前置**（**D-M6-3 原文 0004，按 D-M5-10 仅编号顺延为 0003**） | 卡库切片迁移脚本编号 0003 + 升级/回滚用例 |
| 成本/预算/`llm_call_log` | 原 022 内容 | **不做**（用户决定，本 ADR 记录）；将来恢复需新 ADR | 无（不承诺） |
| `records.py` docstring | 022 历史承诺文本 | 后续代码切片（本批禁改 src） | docstring 与 D-M5-10 一致 |
| C-1/C-2 到期承诺 | D-M5-9 挂账 | Lead 在 M5 出口前安排（不并入 022） | 覆盖率门禁落地/`scripts/check_docs.py` 入 CI |

## 5. 出口验收判据与到期承诺处置

- 本 ADR 自身验收：文档回写四处（第 12/17/20 章 + `docs/开发文档.md` 汇总 + PROGRESS）一致；`git diff --stat` 不含 `src/`/`tests/`/`web/`；`git ls-files` 无 `0003_*` 迁移；第 12.8 含「用户决定不实现」声明；chapters 与 `docs/开发文档.md` 经 `rebuild_doc.py verify` PASS。
- M5 出口（口径调整后）：019/020/021/022-concurrency-idempotency/023/024 六切片验收全绿 + 红队 n≥50 无未校验写入 + cassette 不依赖真实网络；**不含**成本报表/成本 SLO/`llm_call_log`。
- 到期承诺：C-1/C-2 仍在 M5 出口前到期（见 D-M5-9，不因本 ADR 后移）；D-M5-10 成本恢复无到期点（用户已决定不做）。

## 6. 后移与复审

- 后移：成本观测/预算（不承诺，复评触发 = 默认开真实 LLM 或公网多用户部署）；`llm_call_log` 持久化（不承诺，复评触发同上）。
- 复审：M5 出口前复核 022 并发用例是否真正覆盖「并发同键」而非仅串行幂等；M6 卡库迁移若已占用 0003 则本文编号条款随之确认。

## 7. 变更纪律

- 只增不改：推翻本 ADR 任一条目须新增 ADR（`supersedes: D-M5-10`）→ 回写第 12/17/20 章与 PROGRESS → 重跑全量（1179）+ replay（61）→ 更新本文件索引。禁止在章节或进度文档中复制决策正文；章节只引用 D-M5-10。
