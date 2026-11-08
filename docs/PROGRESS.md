# 总体进展总览（PROGRESS）

> 本文件与 `docs/progress/` 下的单次进度文档配合使用；每次新建进度文档必须在同一提交或 PR 内更新本文件。
> 统计基准日：2026-11-08 ｜ 当前里程碑：M2 单场景 MVP（进行中） ｜ 下一个可用编号：006（M2 区间 005–009；M6 前端体验 025–028 已预留）

## 1. 里程碑状态

| 里程碑 | 名称 | 编号区间 | 状态 | 已通过测试任务数/总任务数 | 完成度 | 最近进度文档 | 阻塞项 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| M1 | 骨架 | 001–004 | 已完成 | 4/4 | 100.0% | [004](progress/004-2026-11-08-ci-license-gate.md) | 无 |
| M2 | 单场景 MVP | 005–009 | 进行中 | 3/5 | 60.0% | [007](progress/007-2026-11-08-rule-narrate.md) | 无 |
| M3 | 多角色 | 010–014 | 未开始 | 0/5 | 0.0% | - | 无 |
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

## 4. 编号分配与变更记录

| 里程碑 | 区间 | 已用编号 | 下一个可用 | 备注 |
| --- | --- | --- | --- | --- |
| M1 | 001–004 | 001–004 | - | M1 已完成（4/4）；编号区间已用尽，`005` 起进入 M2 |
| M2 | 005–009 | 005–007 | 008 | 005/006/007 已完成（3/5）；下一步 008-sqlite-snapshot |
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
| R4（M2 新开） | SQLite 交付前无迁移工具（建表用 `create_all`）；current 行丢失时 `version` 取 `last_seq`（M2 每次提交恰 1 事件） | 中 | 缓解中（008 交付后登记细节；Alembic 迁移与多事件提交的 version 列在 M3 前补） | lead |

## 7. 当前状态与下一步

- 当前状态：**M2 单场景 MVP 进行中（3/5 = 60.0%）**。005 · turn-api（回合端点/错误码/幂等/唯一写入者）、006 · parser-tools（ToolRegistry + ToolGate + IntentParseAgent 的 Schema 校验/修复/兜底）、007 · rule-narrate（RuleEngine L0+d20/DC 判定 + TemplateNarrator 确定性叙事接入回合链）已交付；全量 619 passed，质量门全绿。
- 环境追加项（不计入切片完成度）：DeepSeek API（`deepseek-flash`）凭据与连通性自检已配置——密钥仅存 `.env`（gitignored，不入库）；自检命令 `uv run python -m textgame.llm.smoke`。M2 默认装配不联网（`DisabledLLMClient`），真实 LLM 在 M5 接入。
- CI：M1 代码已 push 到 `origin/main`；M2 每个切片单独提交并 push 触发 Actions（quality 3.10/3.12 矩阵 + deps 许可证/pip-audit/SBOM）。
- 当前阻塞：无。
- 下一步：008 · sqlite-snapshot（SQLite 事件表/快照 + 重启恢复接入组合根），验收命令 `uv run pytest tests/integration/test_snapshot_restore.py`；随后 009 golden-replay 与 M2 出口验收。
- 需要 lead 决策：① 项目许可证决策（发布前，对应 R2）；② 第 20 章 Q9 CI 沙箱 / Q10 上下文白名单需在后续里程碑前定案。
