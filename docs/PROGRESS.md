# 总体进展总览（PROGRESS）

> 本文件与 `docs/progress/` 下的单次进度文档配合使用；每次新建进度文档必须在同一提交或 PR 内更新本文件。
> 统计基准日：2026-11-10 ｜ 当前里程碑：**M5 API 调用治理（进行中 2/6，019-llmclient、020-model-tier-cache 已交付）** ｜ 下一个可用编号：**021**（M5 019–024；M6 前端体验 025–028 已预留；M6 后端前置为出口附加项、不占编号）

## 1. 里程碑状态

| 里程碑 | 名称 | 编号区间 | 状态 | 已通过测试任务数/总任务数 | 完成度 | 最近进度文档 | 阻塞项 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| M1 | 骨架 | 001–004 | 已完成 | 4/4 | 100.0% | [004](progress/004-2026-11-08-ci-license-gate.md) | 无 |
| M2 | 单场景 MVP | 005–009 | 已完成 | 5/5 | 100.0% | [009](progress/009-2026-11-08-golden-replay.md) | 无 |
| M3 | 多角色 | 010–014 | 已完成 | 5/5 | 100.0% | [014](progress/014-2026-11-09-conflict-audit.md) | 无 |
| M4 | 制造 | 015–018 | 已完成 | 4/4 | 100.0% | [018](progress/018-2026-11-09-craft-replay.md) | 无 |
| M5 | API 调用治理 | 019–024 | 进行中 | 2/6 | 33.3% | [020](progress/020-2026-11-10-model-tier-cache.md) | 无 |
| M6 | 前端体验 | 025–028 | 未开始 | 0/4 | 0.0% | - | 无（出口含「M6 后端前置」出口附加项，D-M6-1..6） |

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
| 015 | 2026-11-09 | M4 | recipe-model | 制造域契约（模板/实例分离）+ JSON-only 内置配方 + RecipeCatalog 加载校验；20 单测全绿、全量 1001 passed、数据入库校验通过 | [015](progress/015-2026-11-09-recipe-model.md) |
| 016 | 2026-11-09 | M4 | deterministic-craft | 确定性制造：纯函数+服务+CRAFT reducer+craft_item 工具/门+scenario=m4；72 新增/相关测试全绿、全量 1053 passed、M2/M3 golden 37 回放零回归 | [016](progress/016-2026-11-09-deterministic-craft.md) |
| 017 | 2026-11-09 | M4 | free-craft-validation | 自由制造：CraftAgent+CraftDraft Schema/一致性校验+失败零写入回滚；13 集成+3 红队新增，全量 1069 passed、M2/M3 golden 零回归 | [017](progress/017-2026-11-09-free-craft-validation.md) |
| 018 | 2026-11-09 | M4 | craft-replay | 生产同构 5 个制造 golden（确定性成功/材料不足/自由成功/自由 Schema 违规/自由一致性违规）+ 24 回放断言；全量 1093 passed、replay 61（M2/M3 零回归）、M4 4/4（D-M4-14 修复后全量 1096） | [018](progress/018-2026-11-09-craft-replay.md) |
| 019 | 2026-11-10 | M5 | llmclient | LLMClient 同步契约 + LiteLLMClient 治理（deadline/tenacity≤3/RPM/幂等/日志/计量/记录）+ 默认离线工厂（启用缺 key fail-fast）+ 37 例单测；全量 1129 passed、replay 61 零回归、四门全绿 | [019](progress/019-2026-11-10-llmclient.md) |
| 020 | 2026-11-10 | M5 | model-tier-cache | `llm/config/` 分层 JSON 配置（包内默认 + `TEXTGAME_LLM_CONFIG` 覆盖、非法 fail-fast）+ `model_tier→(provider, model, api_base)` 分级 + 进程内 `TTLCache`（allowlist 默认空=不缓存、命中 `cache_hit=true`）；23 例单测；全量 1177 passed、replay 61 零回归、四门全绿、覆盖率 88% | [020](progress/020-2026-11-10-model-tier-cache.md) |
| OSS-1 | 2026-11-10 | 入口轨（不占编号） | playable-entry | 一条命令开玩终端入口（`python -m textgame.console` / `demo --check` / quickstart / examples / README 5 分钟上手）+ 只读 `GET /games/{id}/view` 与背包意图安全子集；demo/console `--check` exit 0、全量 1177 passed、replay 61 零回归、玩家独立验收 PASS | [OSS-1](progress/OSS-1-2026-11-10-playable-entry.md) |
| CI-1 | 2026-11-10 | 基础设施（不占编号） | actions-offline-fix | 定位并修复公开仓库 CI 的 pytest-only 失败（`import litellm` 期远端成本表下载被 Q9 禁网守卫拦截）：`LITELLM_LOCAL_MODEL_COST_MAP` 测试/生产双端离线化 + 2 例回归 + CI 失败注解诊断；本地 1179 passed、replay 61、四门绿，远端 CI 全绿（run 见 §7） | [CI-1](progress/CI-1-2026-11-10-actions-offline-fix.md) |

## 4. 编号分配与变更记录

| 里程碑 | 区间 | 已用编号 | 下一个可用 | 备注 |
| --- | --- | --- | --- | --- |
| M1 | 001–004 | 001–004 | - | M1 已完成（4/4）；编号区间已用尽，`005` 起进入 M2 |
| M2 | 005–009 | 005–009 | - | M2 五切片全部交付并通过出口独立验收（PASS，无 P0/P1）；`010` 起进入 M3 多角色 |
| M3 | 010–014 | 010–014 | - | M3 五切片全部交付：两 NPC 冲突提案被裁决 + 审计、越权/冲突/CAS 安全用例全绿、10 回合多角色 golden 一致；`015` 起进入 M4 制造 |
| M4 | 015–018 | 015–018 | 019 | M4 制造已完成（4/4，全量 1096 passed、replay 61）；制造契约 D-M4-1–D-M4-15 已定案/回填（见 §5 与 `docs/decisions/2026-11-09-m4-craft-contract.md`）；`019` 起进入 M5 API 调用治理 |
| M5 | 019–024 | 019–020 | 021 | 019/020 已交付（全量 1177 passed、replay 61）；M5 决策 D-M5-1..9 见 `docs/decisions/2026-11-10-m5-api-governance.md`；顺序 020→022→021→024→023（D-TEAM-2）；出口附加项（LLM Agent 接线，不新增编号）见第 17 章 §17.2 |
| M6 | 025–028 | - | 025 | 编号已预留（M1–M5 完成后启动）；**M6 后端前置**（replay/卡库 8 端点/静态托管/SPA fallback，D-M6-1..6）登记为出口附加项、不占编号；04:50 轮次已交付其中 `GET /games/{id}/view` 只读端点 |

> 范围外入口轨（OSS-1 终端可玩入口）不占 001–028 编号，进度文档以 `OSS-1-*` 单独命名并登记于 §3。

## 5. 接口偏离登记（对照开发文档第 6、7 章）

| 偏离编号 | 接口/工具 | 文档定义 | 本次实现 | 决策 | 状态 |
| --- | --- | --- | --- | --- | --- |
| D-005-1 | `POST /api/v1/turns` | 第 21 章 §21.3 失败统一 4xx/5xx 信封 | 业务失败仍 HTTP 200 + `TurnResult(ok=false)`；仅缺 `Idempotency-Key`/body 校验失败走 422 信封 | 第 6 章 §6.7「既有 turns 例外」落地 | 已登记 |
| D-005-2 | `EventType` | 第 14 章 §14.7 核心表未列 `DIALOGUE_EXCHANGED` | 新增 `DIALOGUE_EXCHANGED`（`talk_to` 写事件） | 第 6 章 §6.4 动作表需对话事件 | 已登记 |
| D-005-3 | `game_created` 载荷 | 第 14 章 §14.7：`card_id`/`card_snapshot`/`starting_room_id`/`initial_character_id`/`snapshot_version`/`save_seed` | result 增 `initial_state` 整状态快照（M2 无人物卡库，回放据此重建） | M2 简化，基线字段不变；M6 人物卡库接入后评估回退 | 已登记 |
| D-005-4 | `GameState.apply` | 第 5 章：占位 `NotImplementedError`，投影由 persistence/replay 提供 | `domain/reducers.py` 纯投影 + `apply` 委托；persistence/回放复用同一实现 | 第 14 章 §14.7 reducer 落地；`domain` 零框架依赖不变 | 已登记 |
| D-005-5 | `POST /api/v1/games` | 第 6 章 §6.7 v0.2：body `{card_id}`，响应 `{game_id, turn, version, snapshot_version, scenario}` | M2–M4 body `{seed?, scenario?}`、响应含 `save_seed`（无人物卡库） | **2026-11-10 定案（D-M6-2，`docs/decisions/2026-11-10-m6-backend-contract.md`）**：M6 后端前置实现时按加法兼容 —— body `{card_id?, seed?, scenario?}`（同给 `card_id` 与 `seed/scenario` → 422），无 `card_id` 保持现行为；响应以实际 6 字段（含 `save_seed`）为前端类型源，第 6 章已按实际写法回写 | 已定案 |
| D-M2-1 | 写操作路由 | 第 7 章 §7.4 DESTRUCTIVE → ProposalQueue/Resolver | M2 单角色直接 gate → 服务层 → GameEngine（仍过白名单/scope/Schema/规则前置/唯一写入者），ProposalQueue/Resolver 留 M3 | 第 17 章 M2 范围不含多角色 | 已登记 |
| D-006-1 | 工具矩阵 | 第 7 章 §7.3 可见性矩阵未列 `use_item` | 新增 `use_item`（DESTRUCTIVE，`ITEM_CONSUMED` 事件）满足 M2「用」动作 | M2 五类动作需求；M2 结束回写第 7 章 | 已登记 |
| D-006-2 | 工具执行 | 第 7 章工具函数直接执行 | builtin 仅承载 `ToolSpec`/Schema/白名单，执行统一经 `ToolGate` 路由服务层 | 工具函数无法持有 `GameState`（分层与不变量约束） | 已登记 |
| D-006-3 | M2 默认 LLM | 第 12 章 LLMClient 真实调用 + 修复 | M2 默认 `DisabledLLMClient` 离线、确定性兜底；LLM 路径仅由 FakeLLM 覆盖 | 第 3 章 §3.6 方案 A（MVP 前段确定性） | 已登记 |
| D-007-1 | 判定顺序 | 第 9 章 §9.2：判定在提案/裁决前（步骤 6/9） | M2 单角色为 gate → RuleEngine L0 → 叙事 → 提交；L0 在服务层与 RuleEngine 各一份，规则拒绝在生产链路不可达（替身测试验证接线） | M2 无 ProposalQueue；M3 Resolver 接入后恢复文档顺序 | 已登记 |
| D-007-2 | 叙事来源 | 第 4 章：`narrative_source ∈ {llm, template}`，M5 接 LLM | M2 固定 `template`（`TemplateNarrator`），LLM 叙事留 M5 | 第 3 章 §3.6 方案 A | 已登记 |
| D-008-1 | 持久化迁移 | 第 14/16 章：Alembic 管迁移 | M2 用 `Base.metadata.create_all` 建表，无迁移脚本 | M2 单机 SQLite 起步；M3 前补 Alembic 版本化迁移（R4） | 已登记 |
| D-008-2 | 重建版本号 | 第 14 章：version 由 `game_state_current.row_version` 提供 | `current` 行缺失重建时 `version = last_seq`（M2 每次提交恰 1 条事件） | M2 语义；M3 多事件提交需把 version 持久化到快照 | 已登记 |
| D-009-1 | golden fixture | 第 18 章 §18.3：golden 用例 + `state_hash` 比对 | `tests/fixtures/golden/m2_golden.json` 首字段 `_comment` 承载变更纪律（JSON 无注释语法）；逐值精确断言、无 snapshot-update | 第 18 章禁止 `--snapshot-update` 掩盖回归 | 已登记 |
| D-008-3 | 存档/库关系 | 第 14 章 §14.3：单机一存档对应一库；`events.seq` 全局主键 + `event_uid` 唯一 | M2 组合根一个 SQLite 库只支持一个存档：同库第二次 `POST /games` → 409 `CONFLICT`（消息已明确“每库单存档”） | **2026-11-09（M4 前）已落地「同库多存档」**：`events` 主键 `(game_id, seq)`、`event_uid` 唯一 `(game_id, event_uid)`、索引 `(game_id, turn_id, seq)`；Alembic 0002 重建主键；M2 旧库用 `textgame.persistence.legacy_upgrade` 升级（备份/失败恢复/幂等）；仅同 `game_id` 重复创建返回 CONFLICT | 已关闭 |
| D-M3-1 | 编排层 | 第 8 章 §8.4：主流程优先 LangGraph | M3 继续自建显式 loop（M2 已内建；动态多角色提案循环用普通 while 调度） | **2026-11-09（M4 前）追认为正式决策**：多角色动态循环 + CAS 单事务 + 冲突重试只重裁决，图只包流程外壳收益低；第 8/20 章已回写，LangGraph 仅保留第 16 章 `graph.py` 可选封装（M6 后或出现 HITL/跨进程 durable execution 再评估） | 已定案 |
| D-M3-2 | CharacterAgent | 第 8 章 §8.1：LLM `CharacterOutput`（≤2 提案/回合） | M3 为 `domain.scenario.M3_NPC_PLANS` 计划表驱动的 `DeterministicCharacterAgent`（零 LLM、可回放） | 方案 A（离线确定性）；真实 LLM 角色 Agent/裁判 Agent 留 M5 与 API 治理一并接线 | 已登记 |
| D-M3-3 | `POST /api/v1/games` | 契约 body 仅 `{seed}` | 新增可选 `scenario`（`"m2"`/`"m3"`，缺省 `"m2"`） | M3 多角色演示需要；默认 m2 保证 M2 契约、事件序列与 golden 回放不变；M4 再加法新增 `m4`（D-M4-11） | 已登记 |
| D-M3-4 | 冲突演示 actor 建模 | 第 5 章 `NPC` 模型无背包字段 | 两名「拾荒者」以 `Character`（非玩家角色）建模，用于「抢同一物品」的 reducer；`NPC` 实体（老哨兵）保留对话/感知用 | M3 冲突演示最小改法；M5 人物卡/LLM 角色接入后复评 | 已登记 |
| D-M3-5 | 工具 `actor_scope` | 第 7 章 §7.3 矩阵 | `move_to` 校正为 `{player, npc, system}`；`take_item` 新增细标签 `npc:scavenger`；`use_item` 行补登（player/system ✅，第 7 章矩阵行由 A-09 回写补齐） | 按文档矩阵校正 + §7.3「差异化 NPC 加细标签」扩展；默认 `npc`/`npc:blacksmith` 未放宽（124 安全用例逐格断言） | 已登记 |
| D-M3-6 | 快照/迁移 | 第 14 章：Alembic 管迁移；`version` 由 current 行提供 | 快照增 `version` 列并落地重建规则；交付 Alembic baseline（空库 5 表）；`events` 复合主键延后 | R4 前半关闭；D-008-3 多存档复合主键已于 2026-11-09 关闭（Alembic 0002） | 已关闭 |
| D-M3-7 | 审计 | 第 11 章 §11.4 / 第 14 章 §14.6：审计明细单独存表 | 新增 `audit_events` 只增表 + `AuditService` + `GET /api/v1/games/{id}/audit` | 审计只 INSERT；状态词 `{committed, conflicted, aborted}` 与 verdict 分离 | 已关闭 |
| D-M3-8 | `Event.params` | 第 11 章 §11.4：`proposal_id`/`resolution_id` 写入事件 `params` | Resolver 对 applied 草稿事件 `params` 追加两键，`Event` 基线字段不变 | 第 11 章口径落地（D-M3-8 与 M2 的 D-005-2 同属事件载荷扩展） | 已关闭 |
| D-M3-9 | M2 兼容 | M2 契约 / golden | 默认 `scenario="m2"`、M2 单角色链路保持不变；M2 golden 未复算、未 `--snapshot-update` | 出口回归：M2 638 基线全绿，M3 合并后全量 960 passed（M4 前定案后 HEAD 为 981） | 已关闭 |
| D-013-1 | 多角色路径错误码 | 第 6 章 §6.7：gate 细分码（NOT_FOUND/NOT_ALLOWED/OUT_OF_RANGE…） | 玩家提案被 Resolver 拒绝时对外收敛为 `INVALID_INPUT`/`CONFLICT`（细分理由进审计 `verdict_reason`） | M3 多角色路径简化；M2 单角色路径保持细分码不变 | 已登记 |
| D-013-2 | Resolver 接口 | 第 21 章 §21.2.2：`needs_judge`/`verify_agent_override`/`deterministic_fallback` | M3 无裁判分支：`judgements` 参数保留但不读取（ExplodingJudge 测试证明零访问） | M3 判决定性兜底在规则集内闭合；裁判链路留 M5 | 已登记 |
| D-014-1 | 审计 uid | 第 11 章 §11.4 审计表 | 确定性 uid `(game_id, turn_id, attempt, proposal_id)` 去重（同 attempt 重投幂等、先写者保留）；`attempt` 跨「零写入复用回合」递增 | 审计可回放且只增；严格 append 语义与旧库升级留后续决策 | 已登记 |
| D-M4-1 | 材料/产物引用 | 第 13 章 §13.2：`CraftInput.item_id: ItemId`（实例）、配方示例 `item.*` | 材料引用改为 `ItemTemplateId`（复用第 5 章 §5.7，规范写法 `item.<name>`）；产物/副产物实例 ID 由 `derive_item_id(save_seed, turn_id, actor_id, craft_kind, event_seq, ordinal)` 确定性派生，事件 `result.produced_items` 记录完整 `Item` 定义；实例 `Item.tags` 首项为模板键、服务层据此把背包聚合为 `dict[ItemTemplateId,int]`（N-11） | 材料模板/物品实例分离（D-M4-1，裁决 A-01） | 已回写 |
| D-M4-2 | 技能系统 | 第 13 章 §13.4/§13.5：`skill_levels`/`skill_req` 校验与技能经验更新 | M4 `skill_levels` 恒 `{}`、`skill_req` 不校验、`Character` 不加 `skills` 字段、不新增技能/经验事件 | 技能系统后移 M5/内容系统（D-M4-2，裁决 A-02） | 已回写 |
| D-M4-3 | 自由制造裁决 | 第 13 章 §13.3/§13.4：`CraftProposal` 交 Resolver 裁决 | M4 不接 `ProposalQueue`/`Resolver`：`scenario="m4"` 单角色走 `CraftAgent → CraftDraft → CraftService 校验/重算 → GameEngine.submit` | `CraftProposal` 仅留 M5 契约；M5 接线前须迁到 pre-queue（D-M4-3，裁决 A-03） | 已回写 |
| D-M4-4 | `rng_seed` 派生 | 第 13 章 §13.2、第 21 章 §21.2.5：`derive_seed(..., seq)` 的 `seq` 未定义 | `CraftStateSlice` 增 `next_seq = last_seq + 1`，服务层派生后传入；`craft_item` 事件必须是本批首条；不扩 `ToolContext` 六字段 | 方案 A（D-M4-4，裁决 A-04） | 已回写 |
| D-M4-5 | 制造事件语义 | 第 13 章 §13.3、第 14 章 §14.7：CRAFT_* 事件类型与失败语义未定义 | 前置校验失败=零事件；掷骰失败=`CRAFT_DETERMINISTIC(success=false)`；成功=`CRAFT_DETERMINISTIC`/`CRAFT_GENERATED`（params 含 mode/recipe_id/version 或 free_description_sha256/proposal_hash/prompt_version）；M4 不产生 `CRAFT_REJECTED` | 方案 A（D-M4-5，裁决 A-06） | 已回写 |
| D-M4-6 | 配方数据/开关 | 第 13 章 §13.2：JSON/YAML 存放 `src/textgame/data/recipes/` 并注册 CraftService | JSON-only + `importlib.resources`；`.gitignore` 加负向规则 `!src/textgame/data/`；开关 `TEXTGAME_FREE_CRAFT_ENABLED=false` | JSON-only，无新增依赖（D-M4-6，裁决 A-07） | 已回写 |
| D-M4-7 | 017 校验范围 | 第 13 章 §13.4：稀有度上限/材料强度映射/效果 ID 白名单/技能等一致性校验 | M4 只实现模板在背包且数量足、工具存在且不消耗、工位匹配、名称 1–30 字、steps ≤8、未知/他人实体拒绝 | 最小集，其余后移 M5/内容系统（D-M4-7，裁决 A-08） | 已回写 |
| D-M4-8 | 配料/产物语义 | 第 13 章 §13.2：`CraftResult.consumed/produced` 复用带 `role` 的 `CraftInput` | tool 不消耗、material/catalyst 消耗；`produced` 的 `role` 固定 material（无语义）；副产物只记实际触发项；`consumed` 记实际扣除 | 定案（D-M4-8，裁决 A-26④） | 已回写 |
| D-M4-9 | 工位建模 | 第 13 章 §13.2：`station`/`station_id` 语义未定义 | 工位=房间 `ambient_tags` 中的 `station.<name>` 标签（如 `station.forge`）；不新增 Station 实体、不改 GameState | 定案（D-M4-9） | 已回写 |
| D-M4-10 | 持久化/幂等/恢复 | 第 13 章 §13.5：`craft_id` 唯一索引、`turns.status`/inflight 租约恢复 | 不新增 `craft_id` 与迁移：turn 幂等键 + `events` 复合主键覆盖；`turns`/`event_schema`/`llm_call_log`/inflight 标 M5 计划 | 定案（D-M4-10，裁决 A-15） | 已回写 |
| D-M4-11 | `scenario="m4"` | 第 6 章 §6.7、第 11 章、第 21 章 create_game：仅 `m2`/`m3` | 新增 `scenario="m4"`（缺省 `m2`）；M2/M3 golden 字节不变；第 6/11/21 章补可选项 | 加法新增（D-M4-11，裁决 A-10） | 已回写 |
| D-M4-12 | 治理项（Q2/Q3/Q4/Q8） | 第 20 章 §20.3：Q2/Q3/Q4/Q8 未定案 | Q4=B（M4 落地）；Q2=C、Q3=B、Q8=B 关闭 | 见第 20 章 §20.3/§20.4（D-M4-12，裁决 A-05） | 已回写 |
| D-M4-13 | 生产装配口径 | 第 13 章 §13.4/§13.6、D-M4-3：M4 制造「不接 ProposalQueue/Resolver」 | 生产装配复用 M3 编排链路：Player Proposal→ProposalQueue→确定性 Resolver→ToolGate→CraftService→GameEngine 单事务；m4 无 NPC Agent→无跨角色冲突、无 Judge 覆盖；失败提案照常写 `audit_events`（committed/aborted/conflicted，逐提案 verdict=applied/rejected） | D-M4-3 仅指「草案不入队」；018 golden 用生产同构 harness（`m4_prod_harness`）生成，9 章文档已回写 | 已回写 |
| D-M4-14 | 制造种子绑定（缺陷修复） | 第 13 章 §13.2 / D-M4-4：`ctx.save_seed` 来源未显式绑定存档 | `Resolver.resolve` 改用 `snapshot.rng_state.save_seed` 构造 `ToolContext`/规则前置（不再用组合根默认 save-42）；修复显式非 42 seed 的 m4 存档 `event.rng_seed == result.craft.rng_seed` 与实例 ID 派生 | 生产装配修复（2026-11-10，大规模模拟数据测试发现；新增 `tests/integration/test_craft_seed_binding.py` 3 例） | 已修复 |
| D-M4-15 | 并发幂等占位（P2 已知限制） | 第 6 章 §6.7 幂等键语义：同键并发首次请求未定义 | 同 `Idempotency-Key` 并发提交时，未命中缓存的竞争请求可能瞬时返回 CONFLICT/INVALID_INPUT 而非首结果；串行重试可重放成功响应，只提交一次（大规模模拟测试 P2，非 D-M4-14 引入） | M5 治理项 **G-1**，owner=022-cost-budget（幂等/持久化治理同批）；019 不实现（D-M5-7） | 已知限制（M5-022 关闭） |
| D-M5-1 | `LLMClient` 签名 | 第 12 章 §12.2、第 21 章 §21.2.4：`async def complete_json/complete_text` | M5 权威签名改为**同步** `def complete_json/complete_text`（全链路同步、路由在线程池执行）；异步化复评门=M6 流式/SSE | D-M5-1（ADR 2026-11-10；第 12/21 章已回写） | 已回写 |
| D-M5-2 | Schema 修复链归属 | 第 12 章 12.3/12.6：修复由 instructor 在 LLMClient 内做 1 次 | 019 修复链仍由 `validation.complete_json_with_repair` 承担、`LLMClient` 只返回原始文本；`instructor` 未启用运行路径，021 复核迁移 | D-M5-2（ADR 2026-11-10；第 12 章已回写） | 已回写 |
| D-M5-3 | `LLMRequest.output_schema` 类型 | 第 12 章 §12.2 / 第 21 章：`type[BaseModel] 或 None = Field(alias="schema")` | 字段改为 `type[BaseModel] 或 None`（Pydantic 模型类）；不使用 `schema` alias（Pydantic v2 保留属性名），新增 `safe_dump()` 供日志/哈希 | D-M5-3（ADR 2026-11-10；第 12/21 章已回写） | 已回写 |
| D-M5-4 | LLM 配置文件格式 | 第 12 章 12.4：配置写 `config/llm.yaml` | 改 **JSON-only**：`src/textgame/llm/config/llm.default.json`（包内默认）+ `TEXTGAME_LLM_CONFIG` 覆盖；不引入 PyYAML（非声明依赖），020 起落地分级/回退/价格 | D-M5-4（ADR 2026-11-10；第 12/16/19 章已回写） | 已回写 |
| D-M5-6 | LLM 默认装配 | 第 3 章 §3.6 方案 A / 第 17 章：M5 出口附加项才接线 | `Settings.llm_enabled` 默认 false → `DisabledLLMClient`（零联网）；`llm/factory.build_llm_client` 在 true 时构造真实客户端且缺 key 构造期失败；`main.py` 经工厂注入同一实例；真实 Agent 接线留 M5 出口附加项 | D-M5-6（ADR 2026-11-10；第 12/16 章已回写） | 已回写 |

## 6. 风险与阻塞摘要（与开发文档第 20 章同一编号）

| 风险编号 | 摘要 | 等级 | 状态 | 负责人 |
| --- | --- | --- | --- | --- |
| R-L1 | 许可证污染（AGPL/NOASSERTION 混入） | 高 | 缓解中（CI run #1（commit `5ec780b`）deps job 实跑通过；本地真实环境 157 包扫描 0 阻断；`scripts/check_licenses.py` 门禁生效） | lead |
| R-L3 | 上传恶意 JSON / XSS 注入 | 高 | 打开（M6 范围，尚未进入） | lead |
| R-L4 | 复制粘贴许可证污染（AI 生成代码夹带 GPL 片段） | 高 | 缓解中（M1 无外部代码拷贝；check_licenses 仅扫依赖许可证，代码片段来源仍靠 review 清单） | lead |
| R-L5 | 上下文 / 提交泄漏密钥（.env、token、真实数据） | 高 | 缓解中（`.env` gitignored + `check_secrets.py` pre-commit 门禁 + `Settings.llm_api_key` SecretStr；审计确认密钥零入库） | lead |
| R-T7 | AI 幻觉 API / 依赖 | 高 | 缓解中（uv.lock 锁定 + mypy strict + 单元/回放测试 + import-linter 门禁） | lead |
| R1（M1 新开） | Python 3.10 兼容：CI 矩阵未实跑 | 中 | 已关闭（GitHub Actions run #1（commit `5ec780b`）3.10/3.12 矩阵实跑通过；本地亦用 3.10.22 实跑 402 passed） | lead |
| R2（M1 新开） | 项目自身许可证未定（`textgame` 在 pip-licenses 中为 UNKNOWN） | 低（发布前） | 已关闭（2026-11-09 定案 **MIT**：`pyproject.toml` `license = "MIT"` + LICENSE 文件；构建元数据 License-Expression=MIT/License-File=LICENSE；`check_licenses.py` 默认不再豁免自身，门禁对 `textgame` 报告 MIT 放行） | lead |
| R3（M1 新开） | MPL-2.0 传递依赖（orjson/certifi/pathspec/tqdm/hypothesis）文件级义务未登记 | 低 | 缓解中（CI deps job 已生成 CycloneDX SBOM artifact；文件级义务登记随发布流程） | lead |
| R4（M2 新开） | SQLite 交付前无迁移工具（建表用 `create_all`）；current 行丢失时 `version` 取 `last_seq`（M2 每次提交恰 1 事件） | 中 | 已关闭（M3 交付 Alembic baseline 迁移（空库 5 表，可升级/回滚）+ `snapshots.version` 持久化与重建规则；多存档复合主键 D-008-3 留 M4 前评估） | lead |
| R5（M3 新开） | M2 旧库升级：已存在的 `snapshots` 表缺 `version` 列，不在 Alembic baseline（空库建表）范围 | 低 | 已关闭（2026-11-09 交付 `textgame.persistence.legacy_upgrade`：备份 → 补 `version` 列 → 建 `audit_events` → stamp 0001 → upgrade head（0002 主键重建）；失败恢复备份并报明确错误、重复执行为 noop；12 个旧库样本/失败注入用例；README 已写升级指引） | lead |
| R6（M3 新开） | 审计 uid 为确定性去重 `(game, turn, attempt, proposal)`：同一 attempt 重投以首写为准（严格 append 语义未启用） | 低 | 缓解中（编排层每 attempt 每提案只写一次；如需严格 append 再改 uuid 主键，persistence-dev 可 5 分钟切换） | lead |

> 编号映射（2026-11-09 回写）：R1–R6 为里程碑内历史编号，归口第 20 章 `R-*` 体系——R1/R4/R5/R6 → `R-T*`（技术），R2/R3 → `R-L*`（合规/许可证）；第 1 章 §1.3 的 R1–R4 为需求约束编号，与风险编号不同域。R5/R6 已补入第 20 章 §20.2.2；R-L3 状态词按第 20/22 章统一为「打开」。

## 7. 当前状态与下一步

- 当前状态：**M1–M4 已完成（4/4、5/5、5/5、4/4 = 100.0%，出口独立验收 PASS）；M5 API 调用治理进行中（2/6，019-llmclient、020-model-tier-cache 已交付）**；M5 决策 D-M5-1..9、04:50 四角色团队方向决策 D-TEAM-1..7、M6 后端契约冻结 D-M6-1..6 均已定案并落档 `docs/decisions/`；M6 前端体验 0/4（后端前置已立项，04:50 轮次交付 `GET /games/{id}/view` 只读端点与终端可玩入口 OSS-1）。**HEAD `1f74de2`（01:50 交接）实测全量 1129 passed；04:50 轮次交付后全量 1177 passed（新增 48 例，零回归）；CI 修复轮新增 2 例回归后全量 1179 passed**、replay 61 passed（M2/M3/M4 = 4/5/24，零回归）、覆盖率 88%，四门质量门全绿（ruff check/format 144 files、mypy 84 files、lint-imports 2 kept）。M3 出口证据（合并提交 `a5f2be3` 时点复算，**全量 960 passed**）：
  - 冲突 Demo：两名拾荒者同回合提交冲突提案（turn 2 抢同一瓶药水），Resolver 只让 actor_id 升序靠前者 `applied`、后者 `rejected` 且理由可审计，事件全局 `seq` 连续、`version` 每次提交恰好 +1；
  - 10 回合多角色 golden 回放（`tests/replay/test_m3_golden_replay.py`）：final `state_hash=df58e51d…`、13 条事件、16 行审计；逐回合事件 id/seq/rng_seed/state_hash 精确一致，禁止 snapshot-update；
  - 越权/冲突/CAS 安全用例全绿：124 actor_scope 安全用例（30 格矩阵 + 伪造 scope 零写入）、20 Resolver 安全用例、CAS 过期版本零写入、并发冲突最多 3 次「只重裁决不重解析」；
  - 审计只增表 + `GET /api/v1/games/{id}/audit` 查询；快照 `version` 持久化 + Alembic baseline；M2 638 基线零回归（M2 golden 未复算）。
- M4 出口证据（4/4，2026-11-09）：配方契约/确定性制造/自由制造校验/5 个制造 golden 全绿；`tests/replay/test_m4_golden_replay.py` 24 例在生产同构装配（D-M4-13）上逐字段断言事件/种子/审计/state_hash；失败回合 version/last_seq/state_hash 三不变且审计 rejected/aborted；M4 replay 24 passed、M2/M3 golden 4/5 passed 零回归；全量 1093 passed。
- M5 出口证据（019–020/6 切片，2026-11-10）：019 交付 LLMClient 同步契约 + LiteLLMClient（整链 deadline、tenacity≤3 仅超时/429/5xx、RPM 令牌桶、幂等复用与在飞、结构化日志脱敏、token 计量、`LLMCallRecord`）+ `build_llm_client` 默认离线/启用缺 key fail-fast（D-M5-6），37 例单测；020 交付 `llm/config/` 分层 JSON 配置（包内默认 + `TEXTGAME_LLM_CONFIG` 覆盖、非法 fail-fast）+ `model_tier → (provider, model, api_base)` 分级 + 进程内 `TTLCache`（allowlist 默认空=不缓存、命中 `cache_hit=true`），23 例单测；全量 **1177 passed**、replay 61 零回归、四门全绿、覆盖率 88%。
- OSS-1 入口轨证据（2026-11-10，不占 001–028 编号）：`python -m textgame.demo --check` 7/7 回合 exit 0（屏字数 114/83/81/85/82/84/81，两次运行逐字一致）、`python -m textgame.console --check` exit 0（4 回合，状态行 `HP 20/20 ｜ 位置 ｜ 行囊`）；只读 `GET /api/v1/games/{id}/view`（hp/max_hp/inventory/room，零事件、version 不变）+ 意图背包关键词（背包/行囊/物品栏/身上/带了/装备 → `get_inventory`）；`tests/integration/test_console_walkthrough.py` 5 例进 CI 长期烟测；README「5 分钟上手」、`scripts/quickstart.{ps1,sh}`、`examples/m2-demo.md`（实跑生成）；零新增依赖。
- 出口审计要点：M3 五切片验收命令全部执行并通过；M2 默认路径（`scenario="m2"`）行为/事件/幂等与 M2 契约一致；审计状态词与 verdict 分离、审计只 INSERT；旧库升级与复合主键已在 M4 前定案落地（D-008-3/R5 关闭）。
- 环境追加项（不计入切片完成度）：DeepSeek API（`deepseek-flash`）凭据与连通性自检已配置——密钥仅存 `.env`（gitignored，不入库）；自检命令 `uv run python -m textgame.llm.smoke`。M5-019 起默认装配仍不联网（`build_llm_client` 返回 `DisabledLLMClient`，`TEXTGAME_LLM_ENABLED=false`）；真实 Agent 接线属 M5 出口附加项。
- CI：M1–M4 与 M5-019 已 push 到 `origin/main`（M4 收口和 D-M4-14 修复后 HEAD `9d0d8aa`；最新提交见 `git log`）；push 触发 Actions（quality 3.10/3.12 矩阵 + deps 许可证/pip-audit/SBOM），远端运行结果需 GitHub 页面复核（本机无 gh/凭据）。
- 当前阻塞：无。
- M4 前定案（2026-11-09，用户拍板，**已全部定案**）：① **R2 关闭**——项目采纳 **MIT**（`pyproject.toml` `license = "MIT"` + LICENSE + 构建元数据 License-Expression/License-File；`check_licenses.py` 默认不再豁免自身）；② **D-008-3 已关闭**——`events` 主键 `(game_id, seq)`、`event_uid` 唯一 `(game_id, event_uid)`，同库多存档；Alembic 0002 + `textgame.persistence.legacy_upgrade` 旧库升级脚本（备份/失败恢复/幂等，12 用例）；③ **Q9 取 B-lite / Q10 取 B**——CI `timeout-minutes`、quality job 无密钥、测试 socket 禁网 guard、`.aiignore` + secret-scan，第 20 章已回写关闭；④ **M5 LLM Agent 接线**定为「M5 出口附加项，不新增编号」（第 17 章已回写）；⑤ **Alembic 不新增 CI 专属 job**（pytest 已覆盖升级/回滚/schema 一致性）；⑥ **Q1 编排层追认自建 loop**（第 8/4/20 章已回写，D-M3-1 升级为正式决策；LangGraph 仅保留可选封装）；⑦ **M4 制造契约定案**——D-M4-1–D-M4-12（材料模板/实例分离、技能后移、自由制造不接 Resolver、`CraftStateSlice.next_seq`、CRAFT_* 事件语义、JSON 配方、017 校验最小集、工位标签、不加 `craft_id`/`turns` 表、`scenario="m4"` 加法新增、Q2/Q3/Q4/Q8 定案），见 `docs/PROGRESS.md` §5 与 `docs/decisions/2026-11-09-m4-craft-contract.md`（D-M4-1–D-M4-15），相关章节与 PROGRESS 已完成回写；⑧ **M5 API 调用治理决策**——D-M5-1..9（同步契约、修复链归属、`output_schema` 模型类、JSON-only 配置、测试/回放策略、默认离线工厂、已知问题归属、ADR 落档、到期承诺处置），见 `docs/decisions/2026-11-10-m5-api-governance.md`。
- 04:50 四角色团队决策（2026-11-10）：**D-TEAM-1..7**（定向双轨 B″、M5 顺序 020→022→021→024→023→出口接线、可玩/采用入口轨、玩家体验修复分级、020 配置裁决、本轮交付与不做项）见 `docs/decisions/2026-11-10-0450-team-direction.md`；**D-M6-1..6**（M6 后端前置立项、`POST /games` 加法兼容、replay/view DTO、静态托管/SPA 与分发、Q6=A+C、Q7=A、错误/幂等契约）见 `docs/decisions/2026-11-10-m6-backend-contract.md`。
- CI 修复（2026-11-10，仓库公开后首个 CI 轮次）：`ci` workflow 的 `quality (3.10/3.12)` 只在 `uv run pytest -q` 失败，根因是 `import litellm` 期从 `raw.githubusercontent.com` 拉取模型成本表，CI（ubuntu、无代理）真连接被 Q9 禁网守卫拦截后 `RuntimeError` 逃逸（本地被 `NO_PROXY=[::1]` 的 httpx `InvalidURL` 掩盖）。修复：`tests/conftest.py` 强制 + `llm/providers.py` 默认 `LITELLM_LOCAL_MODEL_COST_MAP=True`（离线优先、尊重用户显式 False）+ 2 例回归；`ci.yml` 保留 pytest 失败摘要写入 Check 注解（公开仓库无需 token 即可定位失败用例）；远端复核 **run `42819330386`（sha `efe1471`）与 `42819577507`（sha `fa70b9c`，当前 HEAD）均 conclusion=success**，3 个 job（deps 3.12 / quality 3.10 / quality 3.12）全 success 且 quality 无 failure 注解（badge `ci - passing` 一致）；本地全量 **1179 passed**、replay 61、四门绿。CI-only 失败纪律已回写第 18 章 §18.6、第 12 章 §12.4，证据见 `docs/progress/CI-1-2026-11-10-actions-offline-fix.md`。
- M4 出口后修复（2026-11-10，大规模模拟数据测试发现）：**D-M4-14** `Resolver.resolve` 改为用 `snapshot.rng_state.save_seed` 构造 `ToolContext`/规则前置，修复非默认 seed 存档 craft `rng_seed`/实例 ID 与 `EventLog.stamp` 不一致；新增 `tests/integration/test_craft_seed_binding.py` 3 例（全量 1096 passed）。
- 大规模模拟数据测试（2026-11-10，mock 脚本/数据全部在 `.work/`，**不入库**）：Lead 批次 360 存档/2880 回合、**20,138 断言全绿**（版本链/失败零写入/幂等/回放哈希/审计只增/对抗输入/并发/制造确定性）；独立验收员另批 180 存档/1560 回合 **14,112/14,113**（唯一失败为并发同键瞬态，P2→D-M4-15）。测试发现并修复 **D-M4-14**（Resolver 种子绑定存档），修复后复测 P1 关闭、全量 1096 passed。
- 下一步：**M5 022-cost-budget**（价格表/`estimate_cost`/按 agent-turn 归集/预算降级 + `llm_call_log` 表/Alembic 0003 + D-M4-15 并发同键关闭 + C-1/C-2 到期承诺）→ 021-schema-repair（修复链归属复核，D-M5-2）→ 024-provider-fallback → 023-batch-sleep → M5 出口附加项（解析白名单派生 P2-4(a) → 叙事 → 角色 → 裁判；裁判需 Lead 授权改 Resolver）；**M6 轨道**：M6 后端前置（replay → 卡库 8 端点 + `card_service` + Alembic 0004 → 静态托管/SPA → `web-build` CI）→ 前端 025 骨架 → 027-lite → 027 完整 → 026 → 028；顺序与门控见 `docs/decisions/2026-11-10-0450-team-direction.md`（D-TEAM-2/3）与 `docs/decisions/2026-11-10-m6-backend-contract.md`（D-M6-1..6）。
- M5 接线注意（M4 验收 P2）：生产 HTTP 解析器目前只产出 recipe 制造意图（`DeterministicIntentParser(recipe_names=...)`），自由制造的自由句解析仅测试注入 `FreeCraftIntentParser`；M5 打开自由制造开关前需补生产解析器 free 意图并在第 13/17 章回写。
- 需要 lead 决策：① M6 前端是否提供「存档列表/继续游戏」UI（默认在 027 回合界面内做，D-M6-5）；② 发布形态与分发渠道（D-TEAM-3 已定：先 tag/CHANGELOG 后评估 PyPI，Docker 等 `web/dist` 与 `web-build` CI 就绪后再做；MIT 署名/NOTICE 实践随之落地，非阻塞）。
