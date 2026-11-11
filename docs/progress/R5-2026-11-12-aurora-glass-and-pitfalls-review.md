# R5 · 技术风险审查修复（R-01～R-25）+ Aurora Glass 发布口径收尾

> 编号：`R5`（范围外技术风险修复批次，不占 001–028 编号；按 `docs/progress/README.md` 例外条款登记）
> 日期：2026-11-12（Asia/Shanghai）
> 前置：HEAD `27d85bb`（0.3.0 发布后）；Cline 批次改动为未提交工作树；R4（Aurora Glass 视觉批次）已交付。
> 依据：[技术实现风险审查报告（R-01～R-25）](../audits/2026-11-11-technical-pitfalls-review.md) §7、[ADR · Aurora Glass 双皮肤（D-UI-3）](../decisions/2026-11-11-aurora-glass-theme.md)。
> 关联：R4（视觉批次）、开发文档第 16/23 章、`CHANGELOG.md [Unreleased]`、`docs/PROGRESS.md`、`docs/decisions/README.md`。
> 发布口径：**本批不 bump 版本**（维持 `0.3.0` + `[Unreleased]`）；`0.4.0` 待发布、发布 checklist **未执行**（`RELEASE.md` 保持 0.3.0 已发布历史快照，不改）。

## 1. 目标与判定口径

用户指令（2026-11-11/12）：① 前端改为 **Aurora Glass 日/夜双主题**（R4 已交付，本批收尾发布口径）；② 按技术风险审查报告修 **R-01～R-25**；③ 新增**运行期自由制造开关**（`TEXTGAME_FREE_CRAFT_ENABLED` 需显式接受 Resolver LLM 风险）。

判定口径：

1. **每个 R 编号都有代码修法 + 回归测试**（逐项见审查报告 §7；§0 风险表状态列同步为「已修复/已降低风险」）；保留限制与未消除风险列入 §4。
2. **质量门全绿**：后端 pytest（unit/integration/replay/security）+ 覆盖率门、ruff format/check、mypy、import-linter、check_docs；前端 vitest + typecheck + build + 真实浏览器 e2e。
3. **数字只写实测**：本文件所有测试/覆盖率/e2e 数字均为 2026-11-12 最终工作区本机实跑；未验证项标「待回填」。
4. **发布口径不夸大**：不 bump 版本、不宣称已发布；`0.4.0` 发布动作与 PyPI/tag 留待维护者决定。

## 2. 交付内容

| 方向 | 交付 | 关键位置 |
| --- | --- | --- |
| 主线/状态（P0） | R-01 story 物品 `tags[0]` 统一模板键、背包不再按 `consumable` 合并；R-02 场景导入期校验未知属性/自指出口/物品双放置 + 建局 dry-run（422 取代建局 500）；R-03 commit 后快照/幂等写失败只记 WARNING、不再把成功回合报失败；R-06 `EventLog.append` 校验 `rng_seed`、建局校验初始状态种子/玩家槽位；R-17 `in`/`out` 出口可导航且中文名本地化 | `domain/crafting.py`、`domain/scenario_story.py`、`domain/user_scenario.py`、`services/query_service.py`、`services/game_engine.py`、`orchestration/turn_orchestrator.py`、`domain/events.py`、`domain/types.py`、`orchestration/intent.py` |
| 并发/升级/一致性 | R-04 新增 `services/idempotency_locks.py`（64 段进程级锁）覆盖创建/导入/回合；R-05 启动 `ensure_schema()` 自动检测并升级旧库、Alembic 随 wheel/sdist 分发；R-10 快照表 `(game_id, snapshot_version)` 唯一约束（**Alembic 0006**）+ upsert + 按存档串行；R-20 幂等表不再缓存 4xx 失败响应；R-08 批量休眠 `SCENE_ENTRY` 只认本回合移动；R-21 每回合只读事件尾部 + `EventLog.resume` 增量、LLM 调用日志有界 | `services/idempotency_locks.py`、`persistence/database.py`、`persistence/legacy_upgrade.py`、`persistence/tables.py`、`persistence/sqlite_repository.py`、`alembic/versions/0006_snapshot_unique.py`、`orchestration/batch_sleep.py`、`services/game_engine.py`、`llm/records.py`、`pyproject.toml` |
| LLM/世界书/安全 | R-07 解析白名单从 `tool_specs` 派生；R-09 人物卡生成限流按 IP 且有界；R-11 上传 JSON 拒绝 NaN/Infinity；R-12 世界书嵌套量词正则（含 `(?:a+){2,}`）导入期拒绝；R-13 重试等待受整链 deadline 约束；R-14 解析/制造带 `prompt_version`；R-15 叙事兼容制造产物 dict；R-16 `/view.lore` 与下一回合注入口径对齐；R-18 ASGI 1MB 请求体上限（Content-Length + chunked 实际计数）；R-25 `TrustedHostMiddleware` + 非 loopback token 鉴权 + `api_base` 拒绝云元数据/链路本地地址 | `agents/parser_agent.py`、`services/card_service.py`、`services/world_book_service.py`、`domain/world_book.py`、`llm/litellm_client.py`、`agents/narrator_agent.py`、`api/app.py`、`api/routes/settings.py`、`llm/credentials.py`、`config/settings.py` |
| 运行期自由制造开关（R-19） | 新增 `FreeCraftSwitch`（`CraftService` 与确定性解析器共享，网页热切换）+ `FreeCraftSettingsStore`（`data/free_craft.json` 原子读写，损坏回退关闭）+ `GET/PUT /api/v1/settings/free-craft`（开启需大模型已启用 + `accept_resolver_llm_risk`，落盘成功后才切内存态）；无头/生产需 `TEXTGAME_FREE_CRAFT_ENABLED=true` + `TEXTGAME_ALLOW_RESOLVER_LLM=true` 双开关，否则启动期 fail-fast；网页设置页「自由制造」卡片（二次确认「裁决/重试链路内调 LLM、可能重复计费」） | `services/free_craft.py`、`persistence/free_craft_settings.py`、`api/routes/settings.py`、`main.py`、`web/src/stores/freeCraft.ts`、`web/src/views/SettingsView.vue` |
| 前端（R-22/R-23/R-24 + Aurora Glass） | R-22 `loadGame` 失败先清空旧对局状态；R-23 零事件只读回合不写 history；R-24 幂等键 store 生成最终 UUID、client 直传、`create-game` 键纳入 `world_book_id`；Aurora Glass 收尾：`terminal.css`→`theme.css` 三层 token、日间默认 + 夜间/跟随系统、`theme-init.js` 首帧防闪色、View Transition 圆形扩散、`AtmosphereOverlay`/`MoteField` 懒加载与主题调色板、`data-visual=off` 不渲染 | `web/src/stores/game.ts`、`web/src/api/client.ts`、`web/src/styles/theme.css`、`web/src/stores/theme.ts`、`web/public/theme-init.js`、`web/src/components/{ThemeToggle,AtmosphereOverlay,MoteField,VisualSettings}.vue` |
| 文档与发布口径 | ADR 索引登记 **D-UI-3**；审查报告补 R-06 标题、§0 风险表状态列 25 项同步、§7 批次汇总回填最终数字；开发文档第 02/16/23 章回写（目录树补 `free_craft*`/`idempotency_locks`/0006/`theme-init.js`，删除不可复算的边框对比度数字、纠正 `web/dist` 入库口径）；README（视觉/Alembic/设计文档链接）与 README.en（主题表述/Alembic）修正；CHANGELOG `[Unreleased]` 覆盖 R-01～R-25 且无夸大；PROGRESS 登记 R5 | `docs/decisions/README.md`、`docs/audits/2026-11-11-technical-pitfalls-review.md`、`docs/开发文档.md` 与第 02/16/23 章、`README.md`、`README.en.md`、`CHANGELOG.md`、`docs/PROGRESS.md` |

## 3. 验证结果（实跑，2026-11-12 最终工作区）

- **后端全量**：`uv run pytest -q` → **2233 passed**（collect 口径：unit **1327** + integration **665** + replay **69** + security **172**）。
- **覆盖率门**：`--cov-fail-under=80` → **TOTAL 87.56%**（>80% reached）。
- **回放**：`tests/replay -q` → **69 passed**（旧 golden 61 + provenance 7 + 新增 R-06 回归 1；**未改 golden、未 `--snapshot-update`**）；demo/console `--check` 逐字不变。
- **静态门**：`ruff format --check .` → 257 files already formatted；`ruff check .` 全过；`mypy src/textgame` → 113 files 无问题；`lint-imports` → 2 kept；`python scripts/check_docs.py` → PASS。
- **前端**：`pnpm --dir web test` → **60 files / 383 tests 全绿**（Cline 当步 379 + R-23 回归修复新增 4 例）；`typecheck` 通过；`build` 通过、`web/dist` 重建且**连续两次 build 逐文件 SHA256 一致（36 文件，无 drift）**。
- **真实浏览器 e2e**：**26/26 步、126/126 断言 PASS**（离线 `TEXTGAME_LLM_ENABLED=false` + headless Edge；证据 `.work/e2e-web-r23/result.json`，不入库）。
- **视觉复核**：对比度实测（WCAG AA）日间 fg/bg 16.63、dim/bg 6.20、accent/bg 5.87、按钮 6.29；夜间 fg/bg 17.71、dim/bg 9.30、accent/bg 9.87、按钮 5.67；主题单测 `theme.test.ts` 9 例 + `themeToggle.test.ts` 5 例 + `moteField.test.ts` 6 例；`MoteField` 懒加载 chunk 3,182B（gzip **1,622B**）；`theme-init.js` 外链脚本，满足 CSP `script-src 'self'`。
- **口径说明**：R4 文档 §3 的「58 files / 365 tests」是 R4 视觉批次快照；Cline 修复批次当步为 60 files / 379 tests；本轮评审发现并修复 R-23 回归（零事件回合正文刷新后不可见 → 新增「最近回应」可见区块 + 4 例单测）后，最终为 **60 files / 383 tests**（CHANGELOG `[Unreleased]` 已按最终数字回填）。
- **LLM 参与度实跑证据**（真实 DeepSeek key，**46/50 次真实 API 请求**；证据 `.work/llm-eval/evidence.md`，`.work/` 已 gitignore，不入库）：
  - **真实链路**：`parse@v1` / `narrate@v3` / `character@v1` / `craft@v1` 均产生真实调用与上屏输出；`judge@v1` 已接线但生产 **0 调用**（`agent_assisted_tools=∅`、`needs_judge` 不可达）。
  - **用量/延迟**：合计 46 次（smoke 1 + m2 真实 run 3 回合 6 + 在线探针 13/15 条 39 次），prompt **59,930** / completion **12,209** tokens，平均约 2.5s，全部 `attempts=1`、`cache_hit=false`；在线探针按任务：`intent_parse` 24（41,266/9,444tok，均 3.1s）、`narrate` 12（8,461/1,177，1.3s）、`character_act` 2（1,488/475，2.0s）、`craft_generate` 1（972/348，3.0s）。
  - **真实 run（m2/seed42，3 回合）**：6/6 调用成功（`parse@v1`+`narrate@v3`），`narrative_source=llm`、1708 字符叙事、单回合 2.6–6.5s；离线同输入 20–23ms、3/3 模板叙事；同 seed 两局屏文本/事件/view 逐字一致。
  - **降级/安全边界**：解析失败→关键词兜底、叙事失败→模板、NPC 失败→确定性计划、自由制造关闭→fail-closed；越权/提示注入/超纲/重复操作一律零事件零写入；自由制造数值由框架重算（P04b 实测 `success=false`，叙事如实叙述失败）。
  - **离线保证**：15/15 探针零网络、0 崩溃；58 次客户端尝试全部 0–1ms `LLM_UNAVAILABLE`；smoke 退出码 0（LLM 延迟 4.54s / 进程 5.65s，`content=''` 因 `max_tokens=32` 用尽，游戏内正常）。
  - **已知偏差**：见 §4 第 15–20 条（judge 生产不可达、0.6 置信阈值、参数名≠实体 ID、Windows 凭据权限、smoke 内容空、零事件来源口径）。
- **自由度结论**（审计文档：[`docs/audits/2026-11-12-llm-participation-freedom.md`](../audits/2026-11-12-llm-participation-freedom.md)，freedom-assessment 撰写，本文件只引用）：
  - **一句话**：LLM 真实参与「理解、讲述、少量生成」，但不制定规则——`parse@v1`/`narrate@v3`/`character@v1`/`craft@v1` 四条链路真实调用并产生玩家可见结果，`judge@v1` 生产不可达（0 调用）；所有状态变更仍由确定性 Resolver/框架完成。
  - **自由度评分（综合 3/5）**：叙事自由 4/5、表达自由 3/5、世界响应 3/5、公平可复现 4/5、失败优雅 4/5、**行动自由仅 2/5**；上限由 8 个确定性工具而非模型能力决定，最大损失点 =「解析 → 8 工具白名单 → 0.6 置信阈值」。
  - **真实 LLM 相对离线的增量**：小说化叙事、英文/多语言动作句、m3 NPC 自主提案、开启开关后的自由制造草案；规则/数值/状态自由度不变。
  - **证据局限**：15 条探针（离线 15/15、在线 13/15）、单模型 `deepseek-flash`、单机单玩家、UI 未跑；详见审计 §7。

## 4. 遗留与后续

1. **R-04 单进程锁限制**：`idempotency_locks` 只解决单进程；多 worker/多进程部署仍需数据库唯一键抢占（docstring 已标注）。
2. **R-05 多 worker 并发启动迁移竞态**：多 worker 同时启动时 `ensure_schema()` 可能并发迁移；建议生产以单进程启动（或启动期迁移互斥 + 健康检查后再扩 worker）。
3. **R-12 非完整 RE2**：正则键安全是「解析树嵌套重复拒绝 + 启发式」加固，不等价 RE2；极端重叠分支仍有慢匹配可能（输入长度 2000 上限兜底）。
4. **R-19 Resolver 内 LLM 风险仍在**：自由制造仍会在裁决/重试链路内调 LLM（可能重复计费），本批只做到「默认关闭 + 网页二次确认运行期 opt-in + 无头双开关 fail-fast」；M5 前应迁移到 pre-queue 架构。
5. **R-25 同机反代 loopback 绕过**：默认仅信任本机 Host/非 loopback 需 token，但同机反向代理（请求源自 loopback）可绕过该判定；部署到反代时需 `X-Forwarded-For` 检测或强制 `TEXTGAME_API_TOKEN`。
6. **R-08 调度器隔离范围**：`BatchSleepScheduler` 仅按 `actor_id` 隔离；生产组合根未注入（latent），接入前需补场景级隔离与并发预算。
7. **测试覆盖缺口**：R-16（`/view.lore` off-by-one）无新增专项测试（仅既有 lore 用例回归）；R-18 的 chunked 请求体分支未独立测试；R-21 `list_games` 只读尾部 200 条事件的边界（>200 事件存档）未专项覆盖——建议后续补。
8. **`TEXTGAME_FREE_CRAFT_SETTINGS_FILE` 空串误配置**：空字符串会被解析为 `Path("")`→当前工作目录，属误配置风险；建议后续对空串做显式校验（fail-fast 或回退默认）。
9. **夜间接管极光叠层文本对比度**：极光叠层可见时正文对比度实测降至 **3.77–4.22**（低于 4.5:1，仅在「接管」叠层场景）；建议提亮 `--fg-dim` 或降低极光不透明度（需设计确认后调 token）。
10. **375/320px 窄屏「视觉设置」菜单右溢出**：建议移动端改为全宽下拉（修复配方已由 frontend-review 给出，下一批次落地）。
11. **Playwright/axe 仍后移**：与 D-M6-11 一致；本批以 vitest + 无头浏览器截图 + 对比度实测 + 手工走查验收。
12. **RELEASE/PyPI 未发布**：本批不 bump 版本，`0.4.0` 发布 checklist 未执行；tag/GitHub Release/PyPI 待维护者决定（`RELEASE.md` 保持 0.3.0 历史快照）。
13. **m3 真实 LLM 调用预算超设计**：真实 LLM 下 m3 每回合 **1 解析 + 2 NPC + 1 叙事 = 4 次调用**，超出第 9/12 章「默认 2 次、最多 3 次」设计预算；建议 M6 更新预算口径或限制 NPC 并发调用（先记为遗留，不在本批改设计）。
14. **R-23 回归（本轮评审发现并已修复）**：零事件只读回合正文在刷新后不可见 → 新增「最近回应」可见区块（web/src/utils/history.ts::lastResponseText + GameView），含 4 个新单测（vitest 379→383）；属本批评审发现并修复，无需后续动作。
15. **judge@v1 生产不可达（机制就绪）**：`agents/judge_agent.py` 与 `judge@v1` 已接线，但 `registry.agent_assisted_tools()=∅` → `needs_judge` 不可达、生产 0 调用；后续接入 agent-assisted 工具时需复验裁判链路。
16. **0.6 置信阈值是自由度瓶颈**：在线探针中合法社交（P13c conf=0.55）、多步组合/超纲/状态追问等大量意图在解析阶段被澄清/丢弃；建议下一批次评估阈值或两段式澄清（属设计变更，先记遗留）。
17. **参数名≠实体 ID**：LLM 可能把「火盆」直接当 `inspect_item.item_id` → NOT_FOUND（零写入安全，但玩法上动作失败）；建议补名称→ID 友好映射/提示。
18. **Windows 凭据文件权限**：`llm/credentials.py` 的 `chmod 0o600` 在 Windows 无效（实测文件模式 0o666），只能依赖 `data/` 目录 ACL；建议文档提示或后续评估 DPAPI。
19. **smoke `content` 为空**：`python -m textgame.llm.smoke` 退出码 0（LLM 延迟 4.54s / 进程 5.65s，35+32 tokens）但 `content=''`（自检 `max_tokens=32` 恰好用尽，疑似推理型模型）；游戏内 `complete_json` 不设 max_tokens 正常，建议 smoke 放宽预算或提示，避免误判。
20. **零事件回合 `narrative_source=null`**：零事件只读回合的文本实际来自 LLM，但来源字段为空（L3 设计口径），前端显示「来源未知」；建议文档/UI 说明或后续补来源口径。

## 5. 后续动作

1. **回填完成、待 Lead 确认**：LLM 证据与自由度结论均已回填 §3（自由度引用审计文档 §0 两段结论摘要）；待 Lead 确认最终口径后 **complete task-3**；发布口径相关项（`RELEASE.md`/README 当前状态数字）按「不 bump 版本」保持不动。
2. **0.4.0 发布时**：更新 README「当前状态/实测数字」、CHANGELOG 版本段、RELEASE.md checklist、tag/GitHub Release/PyPI 决策；本批不动这些发布快照。
3. **文档同步已完成**（本批）：ADR 索引 D-UI-3、审查报告 §0/§7 回填、第 02/16/23 章与开发文档权威源同步、README/README.en 过期表述修正、CHANGELOG `[Unreleased]` 覆盖与数字回填、PROGRESS 登记 R5。
4. **README 截图**：README 七张截图已随 Aurora Glass 主题更新（`docs/assets/` 同文件名覆盖；首页/移动端为 1–2 条存档的清洁数据，其余为 game/ending/world-books 系列），README 引用不变（本文件不重复修改二进制附件）。
5. **遗留项排期**：R-25 反代场景、夜间接管对比度、移动端视觉设置菜单、m3 LLM 预算口径建议列入下一发布批次评估（N4/N5 轨道或独立小批）。
