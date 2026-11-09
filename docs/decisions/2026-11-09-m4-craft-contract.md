# ADR · M4 制造契约与收口决策（D-M4-1..15 回填）

- 状态：已定案（只增不改；D-M4-15 为「已知限制」仍打开）｜决策人：lead｜复核：3 名独立复核委员（领域/运行时/治理，论文见 `.work/checkin-2026-11-09/panel/`）｜日期：2026-11-09（回填 2026-11-10）｜基线：契约定案 `b8a7660`，收口 `0544f76`
- 关联：M4 制造 015–018、`docs/PROGRESS.md` §5、第 13/14/17/18 章、审计 `A-01..A-30`（`.work/status-2026-11-09/audit-docs.md`）
- 说明：本文件只回填**结论**（一句话 + 回写落点 + 证据），决策正文以第 13 章 §13.6 与 PROGRESS §5 为准；D-M4 定案与文档回写提交 `b8a7660`，四切片提交 `77d85fe`/`a072c6c`/`e86274e`/`aa35ad6`，收口 `0166b2e`。

## 1. 决策表

| 编号 | 决策（一句话） | 回写落点 | 状态 |
| --- | --- | --- | --- |
| D-M4-1 | 材料/产物「模板键↔物品实例」分离：配方/草案用 `ItemTemplateId`（`item.<name>`），背包/产出仍是 `ItemId`；产出实例 ID 由 `derive_item_id(save_seed, turn_id, actor_id, craft_kind, event_seq, ordinal)` 确定性派生；事件 `result.produced_items` 记完整 `Item` 定义 | 第 5 章 §5.3/§5.4、第 13 章 §13.2、第 14 章 §14.7 | 已回写 |
| D-M4-2 | 技能系统后移：`skill_levels` 恒 `{}`、`skill_req` 不校验、`Character` 不加字段、不新增技能/经验事件 | 第 13 章 §13.4/§13.5/§13.6 | 已回写 |
| D-M4-3 | 自由制造生成调用在 `CraftService` 内、M4 不接 `ProposalQueue`/`Resolver`（仅草案不入队）；`CraftProposal` 留 M5 契约 | 第 13 章 §13.3/§13.4、第 17 章 §17.2 | 已回写（M5 接线前需迁 pre-queue） |
| D-M4-4 | `rng_seed` 的 `seq` 来源：`CraftStateSlice.next_seq = last_seq + 1` 服务层注入；玩家 craft 事件必须是提交批次首条；不扩 `ToolContext` 六字段 | 第 13 章 §13.2、第 21 章 §21.2.5 | 已回写 |
| D-M4-5 | 制造事件语义：前置校验失败=零事件；掷骰失败=`CRAFT_DETERMINISTIC(success=false)`；成功=`CRAFT_DETERMINISTIC`/`CRAFT_GENERATED`；M4 不产生 `CRAFT_REJECTED`（identity reducer 预留） | 第 13/14/21 章 | 已回写 |
| D-M4-6 | 配方数据 JSON-only + `src/textgame/data/recipes/` + `importlib.resources`；`.gitignore` 加负向规则；`TEXTGAME_FREE_CRAFT_ENABLED` 默认 false | 第 13 章 §13.2、第 16 章 §16.1/§16.5、第 19 章 | 已回写 |
| D-M4-7 | 017 校验最小集：模板在背包且数量足、工具不消耗、工位匹配、名称 1–30/`steps` ≤8、未知/他人实体拒绝；稀有度/材料强度/效果白名单后移 | 第 13 章 §13.4/§13.6 | 已回写 |
| D-M4-8 | 配料语义：`role ∈ {material,tool,catalyst}`；tool 不消耗；`produced` 复用 `CraftInput`（role 固定 material）；副产物只记触发项；`consumed` 记实际扣除 | 第 13 章 §13.2/§13.6 | 已回写 |
| D-M4-9 | 工位 = 房间 `ambient_tags` 中的 `station.<name>`；不新增 Station 实体、不改 `GameState` | 第 13 章 §13.2/§13.6 | 已回写 |
| D-M4-10 | 不新增 `craft_id` 唯一索引/迁移：turn 幂等键 + `events` 复合主键覆盖；`turns`/`event_schema`/`llm_call_log`/inflight 标 M5 计划 | 第 13 章 §13.5、第 14 章 §14.3 | 已回写 |
| D-M4-11 | 加法新增 `scenario="m4"`（缺省仍 `m2`）；M2/M3 golden 字节不变 | 第 6/11/21 章 | 已回写 |
| D-M4-12 | 治理定案：Q4=B（M4 落地）；Q2=C、Q3=B、Q8=B 关闭 | 第 20 章 §20.3/§20.4 | 已回写 |
| D-M4-13 | 生产装配口径澄清：制造复用 M3 编排链路（Proposal→Resolver→ToolGate→CraftService），D-M4-3「不接 Resolver」仅指草案不入队 | 第 9/13/17 章 | 已回写 |
| D-M4-14 | 缺陷修复：`Resolver.resolve` 用 `snapshot.rng_state.save_seed` 构造 `ToolContext`（非默认 seed 的 craft 种子/实例 ID 与 `EventLog.stamp` 一致）；回归 `tests/integration/test_craft_seed_binding.py` 3 例 | 第 13 章 §13.2、PROGRESS §5/§7 | 已修复（`9d0d8aa`） |
| D-M4-15 | 并发同 `Idempotency-Key`：只提交一次，但早到请求可能瞬时返回 `CONFLICT`/`INVALID_INPUT`（非首结果）；串行重试可重放。登记为 M5 治理项（见 `2026-11-10-m5-api-governance.md` D-M5-7） | PROGRESS §5/§7 | 已知限制（M5 关闭） |

## 2. 出口证据

- 出口独立验收 `.work/status-2026-11-09/verify-m4.md`：**PASS，无 P0/P1**；P2×5（文档/口径/证据边界，已在 M5 首轮 docs 提交清理）。
- 实测：全量 1096 passed、`tests/replay` 61（M2/M3/M4 = 4/5/24）、四门质量门全绿（M4 收口后基线；M5-019 起为 1129）。
- 大规模模拟：Lead 批 360 存档/2880 回合 20,138 断言全绿；独立验收批 180 存档/1560 回合 14,112/14,113（唯一失败=并发同键瞬态 → D-M4-15）。脚本/数据在 `.work/`，不入库。

## 3. 变更纪律

任何 D-M4-* 语义变更（事件载荷、配方 schema、种子派生）必须：新开 ADR（supersedes 对应条目）→ 同提交回写第 13/14 章 → 重跑 `tests/replay`（M2/M3/M4 61 例逐值不变，禁止 `--snapshot-update`）→ PROGRESS §5 更新状态。
