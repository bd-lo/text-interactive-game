# 总体进展总览（PROGRESS）

> 本文件与 `docs/progress/` 下的单次进度文档配合使用；每次新建进度文档必须在同一提交或 PR 内更新本文件。
> 统计基准日：2026-11-09 ｜ 当前里程碑：M3 多角色（已完成，出口独立验收 PASS） ｜ 下一个可用编号：015（M4 区间 015–018；M6 前端体验 025–028 已预留）

## 1. 里程碑状态

| 里程碑 | 名称 | 编号区间 | 状态 | 已通过测试任务数/总任务数 | 完成度 | 最近进度文档 | 阻塞项 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| M1 | 骨架 | 001–004 | 已完成 | 4/4 | 100.0% | [004](progress/004-2026-11-08-ci-license-gate.md) | 无 |
| M2 | 单场景 MVP | 005–009 | 已完成 | 5/5 | 100.0% | [009](progress/009-2026-11-08-golden-replay.md) | 无 |
| M3 | 多角色 | 010–014 | 已完成 | 5/5 | 100.0% | [014](progress/014-2026-11-09-conflict-audit.md) | 无 |
| M4 | 制造 | 015–018 | 未开始 | 0/4 | 0.0% | - | 无 |
| M5 | API 调用治理 | 019–024 | 未开始 | 0/6 | 0.0% | - | 无 |
| M6 | 前端体验 | 025–028 | 未开始 | 0/4 | 0.0% | - | 无 |

## 2. 完成度计算规则

- 公式：已通过测试的任务数 ÷ 里程碑总任务数 × 100%，保留 1 位小数；分子只统计有测试或回放证据的任务。
- 任务清单以《开发文档》第 17 章 §17.2 切片明细为唯一来源，本表“总任务数”必须等于该表条目数（M1=4、M2=5、M3=5、M4=4、M5=6、M6=4）；作废任务经 lead 确认后从分母移除并记入第 4 节。
- 状态判定：0.0% 且无进行中任务 = 未开始；0.0% < 完成度 < 100% = 进行中；100% 且验收测试全绿 = 已完成；存在阻塞项 = 阻塞（优先级最高）。

## 3. 单次进度文档索引

| 编号 | 日期 | 里程碑 | 主题 | 一句话结论 | 链接 |
| --- | --- | --- | --- | --- | --- |
| 001 | 2026-11-08 | M1 | walking-skeleton | uv 环境基线 + 最小 FastAPI（`/healthz` 200）+ L0 质量门全绿，独立验收 PASS | [001](progress/001-2026-11-08-walking-skeleton.md) |
| 002 | 2026-11-08 | M1 | domain-models | 第 5 章 23 类领域模型逐字落地，246 单测全绿、domain 零框架依赖 | [002](progress/002-2026-11-08-domain-models.md) |
| 003 | 2026-11-08 | M1 | eventlog-replay | RandomSource/DerivedRandom + EventLog 盖章/追加/派生重放，golden seed 与第 10 章示例一致 | [003](progress/003-2026-11-08-eventlog-replay.md) |
| 004 | 2026-11-08 | M1 | ci-license-gate | GitHub Actions（3.10/3.12 矩阵）+ 许可证门禁（GPL/AGPL 阻断），deps 三件套本地彩排通过 | [004](progress/004-2026-11-08-ci-license-gate.md) |
| 005 | 2026-11-08 | M2 | turn-api | `/api/v1/turns`+`/games` 端点、TurnResult DTO、8 码→HTTP 映射、Idempotency-Key 幂等、GameEngine 唯一写入者落地；17 集成 + 34 单测全绿 | [005](progress/005-2026-11-08-turn-api.md) |
| 006 | 2026-11-08 | M2 | parser-tools | ToolRegistry 白名单 + ToolGate 路由 + IntentParseAgent（Schema 校验/一次修复/确定性兜底）；FakeLLM 三分支 + 注入 0 写入，46 单测 + 9 链路全绿 | [006](progress/006-2026-11-08-parser-tools.md) |
| 007 | 2026-11-08 | M2 | rule-narrate | RuleEngine（L0+d20/DC 判定，派生种子）+ TemplateNarrator 确定性叙事接入回合链；七回合 80–150 字、narrative 落事件，75+14+4 测试全绿 | [007](progress/007-2026-11-08-rule-narrate.md) |
| 008 | 2026-11-08 | M2 | sqlite-snapshot | SQLite 4 表 + CAS 原子提交 + 快照哈希重建接入组合根；重启恢复/续写/幂等跨重启，23 集成测试全绿 | [008](progress/008-2026-11-08-sqlite-snapshot.md) |
| 009 | 2026-11-08 | M2 | golden-replay | 首个 golden（save-42）7 回合回放：event id/seq/rng_seed/state_hash 逐字段钉死；注入 0 写入 7 例 + 篡改检出，4 回放 + 8 安全全绿 | [009](progress/009-2026-11-08-golden-replay.md) |
| 010 | 2026-11-09 | M3 | perception-slice | 角色私有感知切片：玩家/NPC 同规则裁剪、local_events 仅本人、私有记忆隔离、序列化白名单；33 单测全绿 | [010](progress/010-2026-11-09-perception-slice.md) |
| 011 | 2026-11-09 | M3 | actor-scope | 第 7 章 §7.3 矩阵校正 + 越权 100% 拒绝：124 安全用例（30 格矩阵、gate 失败分支零写入、伪造 scope 不放行） | [011](progress/011-2026-11-09-actor-scope.md) |
| 012 | 2026-11-09 | M3 | proposal-queue | `ProposalQueue`：提案入队/同 actor 去重/优先级排序/过期迁移，纯内存零 IO；44 单测全绿 | [012](progress/012-2026-11-09-proposal-queue.md) |
| 013 | 2026-11-09 | M3 | resolver-cas | 纯函数 Resolver + 确定性 CharacterAgent + 多角色编排：冲突一 applied 一 rejected、CAS 重试只重裁决、失败零写入；7 集成 + 61 单元/安全全绿 | [013](progress/013-2026-11-09-resolver-cas.md) |
| 014 | 2026-11-09 | M3 | conflict-audit | 审计只增表/查询端点 + 快照 version + Alembic baseline + 10 回合多角色 golden（final state_hash=df58e51d…、13 事件、16 审计行）；5 回放 + 5 审计 + 36 持久化 + 7 迁移全绿 | [014](progress/014-2026-11-09-conflict-audit.md) |

## 4. 编号分配与变更记录

| 里程碑 | 区间 | 已用编号 | 下一个可用 | 备注 |
| --- | --- | --- | --- | --- |
| M1 | 001–004 | 001–004 | - | M1 已完成（4/4）；编号区间已用尽，`005` 起进入 M2 |
| M2 | 005–009 | 005–009 | - | M2 五切片全部交付并通过出口独立验收（PASS，无 P0/P1）；`010` 起进入 M3 多角色 |
| M3 | 010–014 | 010–014 | - | M3 五切片全部交付：两 NPC 冲突提案被裁决 + 审计、越权/冲突/CAS 安全用例全绿、10 回合多角色 golden 一致；`015` 起进入 M4 制造 |
| M6 | 025–028 | - | 025 | v0.2 新增「前端体验」里程碑（M1–M5 完成后再启动编号分配） |

## 5. 接口偏离登记（对照开发文档第 6、7 章）

| 偏离编号 | 接口/工具 | 文档定义 | 本次实现 | 决策 | 状态 |
| --- | --- | --- | --- | --- | --- |
| D-005-1 | `POST /api/v1/turns` | 第 21 章 §21.3 失败统一 4xx/5xx 信封 | 业务失败仍 HTTP 200 + `TurnResult(ok=false)`；仅缺 `Idempotency-Key`/body 校验失败走 422 信封 | 第 6 章 §6.7「既有 turns 例外」落地 | 已登记 |
| D-005-2 | `EventType` | 第 14 章 §14.7 核心表未列 `DIALOGUE_EXCHANGED` | 新增 `DIALOGUE_EXCHANGED`（`talk_to` 写事件） | 第 6 章 §6.4 动作表需对话事件 | 已登记 |
| D-005-3 | `game_created` 载荷 | 第 14 章 §14.7：`card_id`/`card_snapshot`/`starting_room_id`/`initial_character_id`/`snapshot_version`/`save_seed` | result 增 `initial_state` 整状态快照（M2 无人物卡库，回放据此重建） | M2 简化，基线字段不变；M6 人物卡库接入后评估回退 | 已登记 |
| D-005-4 | `GameState.apply` | 第 5 章：占位 `NotImplementedError`，投影由 persistence/replay 提供 | `domain/reducers.py` 纯投影 + `apply` 委托；persistence/回放复用同一实现 | 第 14 章 §14.7 reducer 落地；`domain` 零框架依赖不变 | 已登记 |
| D-005-5 | `POST /api/v1/games` | 第 6 章 §6.7 v0.2：body `{card_id}` | M2 body 可选 `{seed}`（无人物卡库） | M2 最小开局；M6 人物卡库接入后按 v0.2 契约补 `card_id` | 已登记 |
| D-M2-1 | 写操作路由 | 第 7 章 §7.4 DESTRUCTIVE → ProposalQueue/Resolver | M2 单角色直接 gate → 服务层 → GameEngine（仍过白名单/scope/Schema/规则前置/唯一写入者），ProposalQueue/Resolver 留 M3 | 第 17 章 M2 范围不含多角色 | 已登记 |
| D-006-1 | 工具矩阵 | 第 7 章 §7.3 可见性矩阵未列 `use_item` | 新增 `use_item`（DESTRUCTIVE，`ITEM_CONSUMED` 事件）满足 M2「用」动作 | M2 五类动作需求；M2 结束回写第 7 章 | 已登记 |
| D-006-2 | 工具执行 | 第 7 章工具函数直接执行 | builtin 仅承载 `ToolSpec`/Schema/白名单，执行统一经 `ToolGate` 路由服务层 | 工具函数无法持有 `GameState`（分层与不变量约束） | 已登记 |
| D-006-3 | M2 默认 LLM | 第 12 章 LLMClient 真实调用 + 修复 | M2 默认 `DisabledLLMClient` 离线、确定性兜底；LLM 路径仅由 FakeLLM 覆盖 | 第 3 章 §3.6 方案 A（MVP 前段确定性） | 已登记 |
| D-007-1 | 判定顺序 | 第 9 章 §9.2：判定在提案/裁决前（步骤 6/9） | M2 单角色为 gate → RuleEngine L0 → 叙事 → 提交；L0 在服务层与 RuleEngine 各一份，规则拒绝在生产链路不可达（替身测试验证接线） | M2 无 ProposalQueue；M3 Resolver 接入后恢复文档顺序 | 已登记 |
| D-007-2 | 叙事来源 | 第 4 章：`narrative_source ∈ {llm, template}`，M5 接 LLM | M2 固定 `template`（`TemplateNarrator`），LLM 叙事留 M5 | 第 3 章 §3.6 方案 A | 已登记 |
| D-008-1 | 持久化迁移 | 第 14/16 章：Alembic 管迁移 | M2 用 `Base.metadata.create_all` 建表，无迁移脚本 | M2 单机 SQLite 起步；M3 前补 Alembic 版本化迁移（R4） | 已登记 |
| D-008-2 | 重建版本号 | 第 14 章：version 由 `game_state_current.row_version` 提供 | `current` 行缺失重建时 `version = last_seq`（M2 每次提交恰 1 条事件） | M2 语义；M3 多事件提交需把 version 持久化到快照 | 已登记 |
| D-009-1 | golden fixture | 第 18 章 §18.3：golden 用例 + `state_hash` 比对 | `tests/fixtures/golden/m2_golden.json` 首字段 `_comment` 承载变更纪律（JSON 无注释语法）；逐值精确断言、无 snapshot-update | 第 18 章禁止 `--snapshot-update` 掩盖回归 | 已登记 |
| D-008-3 | 存档/库关系 | 第 14 章 §14.3：单机一存档对应一库；`events.seq` 全局主键 + `event_uid` 唯一 | M2 组合根一个 SQLite 库只支持一个存档：同库第二次 `POST /games` → 409 `CONFLICT`（消息已明确“每库单存档”）；多存档需 M3 改 `(game_id, seq)` 复合主键 | 与第 14 章设计一致，M2 单场景范围接受；M3 首切片前评估复合主键（M3 结论：单库单存档不触发，复合主键留 M4 前评估） | 已登记 |
| D-M3-1 | 编排层 | 第 8 章 §8.4：主流程优先 LangGraph | M3 继续自建显式 loop（M2 已内建；动态多角色提案循环用普通 while 调度） | 第 8 章 §8.4 明确允许「图无法覆盖动态多角色循环则转自建调度」；不新增依赖 |
| D-M3-2 | CharacterAgent | 第 8 章 §8.1：LLM `CharacterOutput`（≤2 提案/回合） | M3 为 `domain.scenario.M3_NPC_PLANS` 计划表驱动的 `DeterministicCharacterAgent`（零 LLM、可回放） | 方案 A（离线确定性）；真实 LLM 角色 Agent/裁判 Agent 留 M5 与 API 治理一并接线 |
| D-M3-3 | `POST /api/v1/games` | 契约 body 仅 `{seed}` | 新增可选 `scenario`（`"m2"`/`"m3"`，缺省 `"m2"`） | M3 多角色演示需要；默认 m2 保证 M2 契约、事件序列与 golden 回放不变 |
| D-M3-4 | 冲突演示 actor 建模 | 第 5 章 `NPC` 模型无背包字段 | 两名「拾荒者」以 `Character`（非玩家角色）建模，用于「抢同一物品」的 reducer；`NPC` 实体（老哨兵）保留对话/感知用 | M3 冲突演示最小改法；M5 人物卡/LLM 角色接入后复评 |
| D-M3-5 | 工具 `actor_scope` | 第 7 章 §7.3 矩阵 | `move_to` 校正为 `{player, npc, system}`；`take_item` 新增细标签 `npc:scavenger`；`use_item` 行补登（player/system ✅） | 按文档矩阵校正 + §7.3「差异化 NPC 加细标签」扩展；默认 `npc`/`npc:blacksmith` 未放宽（124 安全用例逐格断言） |
| D-M3-6 | 快照/迁移 | 第 14 章：Alembic 管迁移；`version` 由 current 行提供 | 快照增 `version` 列并落地重建规则；交付 Alembic baseline（空库 5 表）；`events` 复合主键延后 | R4 前半关闭；D-008-3 多存档复合主键留 M4 前评估 |
| D-M3-7 | 审计 | 第 11 章 §11.4 / 第 14 章 §14.6：审计明细单独存表 | 新增 `audit_events` 只增表 + `AuditService` + `GET /api/v1/games/{id}/audit` | 审计只 INSERT；状态词 `{committed, conflicted, aborted}` 与 verdict 分离 |
| D-M3-8 | `Event.params` | 第 11 章 §11.4：`proposal_id`/`resolution_id` 写入事件 `params` | Resolver 对 applied 草稿事件 `params` 追加两键，`Event` 基线字段不变 | 第 11 章口径落地（D-M3-8 与 M2 的 D-005-2 同属事件载荷扩展） |
| D-M3-9 | M2 兼容 | M2 契约 / golden | 默认 `scenario="m2"`、M2 单角色链路保持不变；M2 golden 未复算、未 `--snapshot-update` | 出口回归：M2 638 基线全绿，M3 合并后全量 960 passed |
| D-013-1 | 多角色路径错误码 | 第 6 章 §6.7：gate 细分码（NOT_FOUND/NOT_ALLOWED/OUT_OF_RANGE…） | 玩家提案被 Resolver 拒绝时对外收敛为 `INVALID_INPUT`/`CONFLICT`（细分理由进审计 `verdict_reason`） | M3 多角色路径简化；M2 单角色路径保持细分码不变 |
| D-013-2 | Resolver 接口 | 第 21 章 §21.2.2：`needs_judge`/`verify_agent_override`/`deterministic_fallback` | M3 无裁判分支：`judgements` 参数保留但不读取（ExplodingJudge 测试证明零访问） | M3 判决定性兜底在规则集内闭合；裁判链路留 M5 |
| D-014-1 | 审计 uid | 第 11 章 §11.4 审计表 | 确定性 uid `(game_id, turn_id, attempt, proposal_id)` 去重（同 attempt 重投幂等、先写者保留）；`attempt` 跨「零写入复用回合」递增 | 审计可回放且只增；严格 append 语义与旧库升级留后续决策 | 已登记 |

## 6. 风险与阻塞摘要（与开发文档第 20 章同一编号）

| 风险编号 | 摘要 | 等级 | 状态 | 负责人 |
| --- | --- | --- | --- | --- |
| R-L1 | 许可证污染（AGPL/NOASSERTION 混入） | 高 | 缓解中（CI run #1（commit `5ec780b`）deps job 实跑通过；本地真实环境 157 包扫描 0 阻断；`scripts/check_licenses.py` 门禁生效） | lead |
| R-L3 | 上传恶意 JSON / XSS 注入 | 高 | 未开始（M6 范围，尚未进入） | lead |
| R-L4 | 复制粘贴许可证污染（AI 生成代码夹带 GPL 片段） | 高 | 缓解中（M1 无外部代码拷贝；check_licenses 仅扫依赖许可证，代码片段来源仍靠 review 清单） | lead |
| R-L5 | 上下文 / 提交泄漏密钥（.env、token、真实数据） | 高 | 缓解中（`.env` gitignored + `check_secrets.py` pre-commit 门禁 + `Settings.llm_api_key` SecretStr；审计确认密钥零入库） | lead |
| R-T7 | AI 幻觉 API / 依赖 | 高 | 缓解中（uv.lock 锁定 + mypy strict + 单元/回放测试 + import-linter 门禁） | lead |
| R1（M1 新开） | Python 3.10 兼容：CI 矩阵未实跑 | 中 | 已关闭（GitHub Actions run #1（commit `5ec780b`）3.10/3.12 矩阵实跑通过；本地亦用 3.10.22 实跑 402 passed） | lead |
| R2（M1 新开） | 项目自身许可证未定（`textgame` 在 pip-licenses 中为 UNKNOWN） | 低（发布前） | 打开（许可证门禁已默认忽略自身；发布前需决策） | lead |
| R3（M1 新开） | MPL-2.0 传递依赖（orjson/certifi/pathspec/tqdm/hypothesis）文件级义务未登记 | 低 | 缓解中（CI deps job 已生成 CycloneDX SBOM artifact；文件级义务登记随发布流程） | lead |
| R4（M2 新开） | SQLite 交付前无迁移工具（建表用 `create_all`）；current 行丢失时 `version` 取 `last_seq`（M2 每次提交恰 1 事件） | 中 | 已关闭（M3 交付 Alembic baseline 迁移（空库 5 表，可升级/回滚）+ `snapshots.version` 持久化与重建规则；多存档复合主键 D-008-3 留 M4 前评估） | lead |
| R5（M3 新开） | M2 旧库升级：已存在的 `snapshots` 表缺 `version` 列，不在 Alembic baseline（空库建表）范围 | 低 | 打开（开发期建议删旧库；生产式 ALTER 迁移策略待 lead 决策，M4 前定案） | lead |
| R6（M3 新开） | 审计 uid 为确定性去重 `(game, turn, attempt, proposal)`：同一 attempt 重投以首写为准（严格 append 语义未启用） | 低 | 缓解中（编排层每 attempt 每提案只写一次；如需严格 append 再改 uuid 主键，persistence-dev 可 5 分钟切换） | lead |

## 7. 当前状态与下一步

- 当前状态：**M3 多角色已完成（5/5 = 100.0%，出口独立验收 PASS）**。010 perception-slice、011 actor-scope、012 proposal-queue、013 resolver-cas、014 conflict-audit 全部交付；全量 **960 passed**，四门质量门 + pre-commit 9 hooks 全绿。出口证据（合并提交 `a5f2be3` 复算）：
  - 冲突 Demo：两名拾荒者同回合提交冲突提案（turn 2 抢同一瓶药水），Resolver 只让 actor_id 升序靠前者 `applied`、后者 `rejected` 且理由可审计，事件全局 `seq` 连续、`version` 每次提交恰好 +1；
  - 10 回合多角色 golden 回放（`tests/replay/test_m3_golden_replay.py`）：final `state_hash=df58e51d…`、13 条事件、16 行审计；逐回合事件 id/seq/rng_seed/state_hash 精确一致，禁止 snapshot-update；
  - 越权/冲突/CAS 安全用例全绿：124 actor_scope 安全用例（30 格矩阵 + 伪造 scope 零写入）、20 Resolver 安全用例、CAS 过期版本零写入、并发冲突最多 3 次「只重裁决不重解析」；
  - 审计只增表 + `GET /api/v1/games/{id}/audit` 查询；快照 `version` 持久化 + Alembic baseline（R4 关闭）；M2 638 基线零回归（M2 golden 未复算）。
- 出口审计要点：M3 五切片验收命令全部执行并通过；M2 默认路径（`scenario="m2"`）行为/事件/幂等与 M2 契约一致；审计状态词与 verdict 分离、审计只 INSERT；旧库升级与复合主键已登记风险 R5/D-008-3。
- 环境追加项（不计入切片完成度）：DeepSeek API（`deepseek-flash`）凭据与连通性自检已配置——密钥仅存 `.env`（gitignored，不入库）；自检命令 `uv run python -m textgame.llm.smoke`。M3 默认装配仍不联网（`DisabledLLMClient` + 确定性角色 Agent），真实 LLM 在 M5 接入。
- CI：M1/M2 代码已 push 到 `origin/main`；M3 合并提交 `a5f2be3` 与进度文档提交将一并 push 触发 Actions（quality 3.10/3.12 矩阵 + deps 许可证/pip-audit/SBOM）。
- 当前阻塞：无。
- 下一步：**M4 制造（015–018）**，首切片 015-recipe-model（配方数据模型与加载），验收 `uv run pytest tests/unit/test_recipes.py`；M4 开工前评估 D-008-3（多存档复合主键）与 R5（旧库升级策略），并决定 Alembic 是否接入 CI。
- 需要 lead 决策：① 项目许可证决策（发布前，对应 R2）；② 第 20 章 Q9 CI 沙箱 / Q10 上下文白名单需在后续里程碑前定案；③ D-008-3 多存档复合主键（M4 前评估）、R5 M2 旧库升级策略；④ M5 前决定 LLM CharacterAgent/JudgeAgent 接线方式（当前确定性计划驱动，D-M3-2）。
