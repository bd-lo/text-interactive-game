# 决策记录（ADR-lite）索引

> 本目录保存**已定案**的架构/契约/治理决策，是决策的**单一权威来源**；开发文档章节与
> `docs/PROGRESS.md` 只引用决策编号，不复制决策正文（避免 M4 期 §13.6 与正文各写一份
> 导致的漂移）。本目录不属于「单次进度文档」，不占 015–028 编号，不参与 `split_doc.py`
> 的章节拆分（修改本目录不影响 `docs/开发文档.md`）。

## 1. 适用范围

- 跨章节契约（领域模型、事件载荷、接口签名、回放口径）；
- 偏离登记与裁决（PROGRESS §5 中状态为「已定案」的偏离）；
- 里程碑级范围/顺序/验收口径（例如 M5 切片边界、出口附加项）；
- 已写入文档、但实现口径需要澄清或收窄的承诺（到期承诺处置）。

日常进度与测试数字仍写 `docs/progress/` 与 `docs/PROGRESS.md`；本目录只写「为什么、定了什么、
影响面与验收」。

## 2. 命名与编号

- 文件名：`YYYY-MM-DD-<kebab-topic>.md`（日期为决策定案日）。
- 决策条目编号：`D-M<里程碑号>-<序号>`（如 `D-M5-1`），只增不改；被取代时新条目写
  `supersedes: D-M5-x`，旧条目状态改「已取代（被 D-M5-y）」。
- **跨里程碑方向决策**（团队共识/里程碑顺序/轨道编排）用 `D-TEAM-<序号>`（如 `D-TEAM-1`），
  仍只增不改；里程碑内契约决策继续用 `D-M<里程碑>-<序号>`，跨里程碑决策可引用并约束它们。
- 议题编号（如 `D1–D11`、`M5-D1`）只用于讨论过程，最终落档一律用 `D-TEAM-*` / `D-M*-*`。

## 3. 状态机

`草案` → `已定案` → （`已取代` | `已归档`）。本目录内所有记录默认 `已定案`；
只允许追加新记录或给旧记录加「取代关系」，禁止改写历史结论。

## 4. 必备字段（每条 record 文件头部）

| 字段 | 说明 |
| --- | --- |
| 状态 | 已定案 / 已取代（被 D-x） |
| 决策人 | lead（团队 Lead） |
| 复核人 | 独立复核委员（列出名字与产出路径，`.work` 论文不入库） |
| 日期 | YYYY-MM-DD |
| 基线 commit | 决策时的 HEAD（短 hash） |
| 关联里程碑 / 切片 | 如 M5 019–024 + 出口附加项 |
| 关联编号 | 审计问题 `A-*`、开放问题 `Q*`、风险 `R-*`、偏离 `D-M*-*` |

## 5. 与其它文档的衔接

- **第 22 章 §22.7**：除「改代码 / 回改文档」外的第三种变更走「新增 ADR」，即本目录。
- **PROGRESS §5**：只登记「偏离实现」一行（编号 + 一句话 + 决策列指向本目录文件与条目）。
- **进度文档**：每切片第 2 节末尾写「契约变更：D-M5-x / 无」，第 3 节写实测验收数字。
- **开发文档**：契约变更在同一提交内回写对应章节；`docs/开发文档.md` 为权威源，章文件由
  `split_doc.py` 生成（修改流程：改权威源 → 重新生成本目录，或按分章文件重建权威源后
  `verify`/`roundtrip` 校验）。

## 6. 索引

| 日期 | 主题 | 状态 | 关联切片 | 链接 |
| --- | --- | --- | --- | --- |
| 2026-11-09 | M4 制造契约与收口决策（D-M4-1..15 回填） | 已定案 | M4 015–018 | [2026-11-09-m4-craft-contract.md](2026-11-09-m4-craft-contract.md) |
| 2026-11-10 | M5 API 调用治理决策（D-M5-1..9） | 已定案 | M5 019–024 + 出口附加项 | [2026-11-10-m5-api-governance.md](2026-11-10-m5-api-governance.md) |
| 2026-11-10 | 04:50 四角色团队方向决策（D-TEAM-1..7） | 已定案 | M5 + M6 双轨（含 OSS-1 入口轨） | [2026-11-10-0450-team-direction.md](2026-11-10-0450-team-direction.md) |
| 2026-11-10 | M6 后端前置契约冻结（D-M6-1..6） | 已定案 | M6 025–028 + M6 出口附加项 | [2026-11-10-m6-backend-contract.md](2026-11-10-m6-backend-contract.md) |
| 2026-11-10 | 022 缩范围与成本记录不做（D-M5-10） | 已定案 | M5 022 + M6 卡库迁移（Alembic 0003） | [2026-11-10-022-scope-and-cost-out.md](2026-11-10-022-scope-and-cost-out.md) |
| 2026-11-10 | R2 玩家体验批次决策（D-R2-1..12） | 已定案 | 范围外玩家体验批次（story/Web/一键入口/NLU/道具） | [2026-11-10-player-experience.md](2026-11-10-player-experience.md) |
| 2026-11-11 | M6 出口 e2e 验收口径（D-M6-11）＋ R3 收尾决策（D-R3-1..5） | 已定案 | M6 025–028 + M6 出口附加项；R3 打磨批次 | [2026-11-11-m6-exit-and-r3-closure.md](2026-11-11-m6-exit-and-r3-closure.md) |
| 2026-11-11 | M5 出口附加项：裁判 Agent 接线（D-M5-14） | 已定案 | M5 出口附加项（M5E-2，不占 019–024 编号） | [2026-11-11-m5-judge-wiring.md](2026-11-11-m5-judge-wiring.md) |
| 2026-11-11 | H4 世界书 MVP + 前端炫酷化（D-H4-1..6） | 已定案 | 范围外玩家体验批次（世界书/炫酷化/L1/L3；跑团仅文档） | [2026-11-11-h4-worldbook-and-visual.md](2026-11-11-h4-worldbook-and-visual.md) |
| 2026-11-11 | 用户内容平台（场景 JSON 上传）+ LLM 运行期凭据（D-UC-1..7 / D-LLM-1..5） | 已定案 | 范围外玩家内容批次（场景库/设置页/运行期 API Key） | [2026-11-11-user-content-and-llm-runtime.md](2026-11-11-user-content-and-llm-runtime.md) |
| 2026-11-11 | **M6 后计划表与四条轨道（D-N1..N7）**：编号 029–060、发布就绪定义、流式阶段 1 边界、DSL v2/编辑器/分享边界、跑团模组 EventType 例外、不做清单 | 已定案 | N1 内容轨 029–036 / N2 体验轨 037–044 / N3 功能轨 045–048 / N4 技术发布轨 049–056（第 17 章 §17.9） | [2026-11-11-post-m6-roadmap.md](2026-11-11-post-m6-roadmap.md) |
| 2026-11-11 | **流式叙事阶段 1 设计与边界（D-N2-1..N2-8）**：独立 NDJSON 端点、增量提取 narrative 字段、客户端流式治理、提交时机、前端降级、开关与回滚 | 已定案（设计） | N2 037（设计）→ 038 后端 / 039 前端 / 040 验收 | [2026-11-11-streaming-phase1.md](2026-11-11-streaming-phase1.md) |
| 2026-11-11 | **玩家启动器与退出体验（D-LA-1..N6）**：双击启动器、控制面三端点（status/heartbeat/shutdown）、游戏内退出/空闲自动退出/兜底脚本、回环安全、默认零变化、版本 0.3.0 | 已定案 | N5 057（发布体验轨；058 便携包评估未排期） | [2026-11-11-player-launcher-and-exit.md](2026-11-11-player-launcher-and-exit.md) |
| 2026-11-11 | **前端视觉双皮肤 Aurora Glass（D-UI-3）**：日间默认 / 夜间 / 跟随系统；旧「2G 深绿终端」皮肤退役；自建 token 化玻璃拟态，零 UI 组件库 | 已定案 | 范围外玩家体验批次 R4（视觉基线）+ R5 发布口径 | [2026-11-11-aurora-glass-theme.md](2026-11-11-aurora-glass-theme.md) |
| 2026-11-12 | **场景 DSL v2 设计与实现边界（D-N1-1）**：schema_version=2、9 个白名单原子谓词 + `all/any/not`、flag 为派生命题（依赖图环检测/无持久化变量）、多结局 priority 确定性、v1 逐字兼容、无 eval/脚本；历史类条件明确归 033–034（D-N6 需用户拍板） | 已定案（设计+实现） | N1 029 设计 / 030 实现（兼容矩阵见 `docs/design/scenario-dsl-v2.md`） | [2026-11-12-scenario-dsl-v2.md](2026-11-12-scenario-dsl-v2.md) |
| 2026-11-12 | **m3 叙事受控重基线（D-N4-1）**：受控重基线定义/审批/双跑对比/证据格式/回滚；本轮只交付工具与流程（`scripts/rebaseline_golden.py` dry-run 0-diff + 临时副本受控 apply 演练），不改变任何 golden/叙事文本；真实触发点=m3 建模重构批次 | 已定案（流程就绪） | N4 054 | [2026-11-12-m3-narrative-rebaseline.md](2026-11-12-m3-narrative-rebaseline.md) |
| 2026-11-12 | **Playwright/a11y 工具口径复评（D-N4-2；D-M6-11 复审）**：结论继续以零依赖 CDP（`scripts/e2e-web.mjs`）为唯一 e2e 工具与 CI 口径，不引入 Playwright、不建长期 CI 浏览器矩阵；含三选项成本/回滚/复评触发条件 | 已定案 | N4 055 | [2026-11-12-playwright-a11y-eval.md](2026-11-12-playwright-a11y-eval.md) |
| 2026-11-12 | **便携包 / PyInstaller / 安装包评估（D-N5-1）**：本轮不实现、不新增构建依赖/CI 打包 job/exe；维持「仓库/zip + uv + 启动器 + wheel」分发；保留方案对比与复评触发条件 | 已定案（评估：不实现） | N5 058 | [2026-11-12-portable-package-eval.md](2026-11-12-portable-package-eval.md) |
| 2026-11-12 | **反代信任与访问控制（D-R6-1）**：`TEXTGAME_TRUST_PROXY` 默认 false（带 XFF/Forwarded 头→403 fail-closed）；启用后 XFF 首跳判定、Forwarded 基础支持、非 loopback 仍要求 token；单进程默认只绑 127.0.0.1 | 已定案 | R6 遗留项收口（R-25） | [2026-11-12-proxy-trust-and-access.md](2026-11-12-proxy-trust-and-access.md) |
| 2026-11-12 | **单进程 / 多 worker 与启动迁移策略（D-R6-2）**：多 worker 默认启动拒绝（`TEXTGAME_ALLOW_MULTI_WORKER` opt-in）、`ensure_schema` 升级加跨进程文件锁（10s 超时、锁内重判）、手动迁移优先；SQLite 仍建议单 worker | 已定案 | R6 遗留项收口（R-04/R-05） | [2026-11-12-single-process-and-migration-lock.md](2026-11-12-single-process-and-migration-lock.md) |
| 2026-11-12 | **R-19 pre-queue 迁移决策（D-R6-3）**：自由制造草案移出 Resolver（pre-queue 预生成 + `prepared_crafts` 透传 + 纯校验重算，Resolver 零 LLM 调用）；CAS 重试复用载荷；兼容开关保留为历史兼容（M5 后评估简化）；NPC free craft 零 LLM 拒绝、失败错误码保 golden | 已定案 | R6 遗留项收口（R-19） | [2026-11-12-r19-pre-queue-migration.md](2026-11-12-r19-pre-queue-migration.md) |
| 2026-11-12 | **R7 行动自由度（D-R7-1..5）**：D-N6 用户批准新增 4 个 EventType（`ITEM_DROPPED`/`ITEM_GIVEN`/`COMBAT_RESOLVED`/`IMPROVISED_ACTION`，纯加法、旧 golden 零重基线）；`improvise` 首个 `agent_assisted` 工具（裁判生产可达、advisory、离线确定性兜底拒绝、每回合≤1）；分工具置信阈值+确认式澄清（候选不自动执行）；多步 ≤3 原子执行；实体名→ID 映射（未知仍拒绝） | 已定案（用户批准 D-N6） | R7 行动自由度批次（工具 8→12） | [2026-11-12-r7-action-freedom.md](2026-11-12-r7-action-freedom.md) |

## 7. 记录模板

```markdown
# ADR · <主题>（D-M<里程碑>-<序号>）

- 状态：已定案 ｜ 决策人：lead ｜ 复核人：<角色+论文路径> ｜ 日期：YYYY-MM-DD ｜ 基线：<commit>
- 关联：里程碑/切片、A-*、Q*、R-*、PROGRESS §5 行

## 1. 背景与红线
## 2. 决策（逐条：决策 / 理由 / 被否选项 / 影响面 / 回写落点 / 验收断言）
## 3. 契约→切片映射（防止只按里程碑表一行目标实施而漏章内要求）
## 4. 已知问题归属（编号 / owner 切片 / 关闭条件）
## 5. 出口验收判据与到期承诺处置
## 6. 后移与复审
## 7. 变更纪律
```
