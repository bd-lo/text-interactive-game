# 变更日志（CHANGELOG）

> 本文件登记项目变更；**当前开发版本 `0.3.0`（2026-11-11，玩家启动器/退出体验批次）**——
> 普通玩家双击 `启动游戏.bat` 启动、游戏内「退出游戏」优雅退出、关浏览器空闲自动退出。
> **发布状态**：上一发布快照 `0.2.0` 已推送 `main` 并打注记 tag **`v0.2.0`**（指向 `0e52814`，CI run
> [43032265398](https://github.com/bd-lo/text-interactive-game/actions/runs/43032265398) 全绿）；
> `0.3.0` 已 push main，tag/Release/PyPI 待维护者拍板。
> `0.1.0` 为开发期历史版本（未对外分发）。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；
> 里程碑/切片编号见 `docs/PROGRESS.md`、开发文档第 17 章与 M6 后计划表（§17.9）。
> 发布流程/版本号/tag 规则见 [RELEASE.md](RELEASE.md)；分发与 Docker 评估见
> [deployment-and-distribution](docs/design/deployment-and-distribution.md)。

## [0.4.0] - 未发布（N 系列批次，2026-11-12）

> **未发布草案**：M6 后 N 系列批次（2026-11-12）已合入工作区/`main`，但本批**不 bump 版本**——
> `pyproject.toml` 与 `src/textgame/__init__.py::__version__` 仍为 `0.3.0`，未打 tag、未发布。
> 发布前需执行 [RELEASE.md](RELEASE.md) §2 checklist（全量测试/replay/四门/web 构建与 `web/dist` 漂移/e2e/
> `check_docs`/构建冒烟/启动器冒烟/许可证/安全）并由维护者拍板 `git tag` / GitHub Release / PyPI。
> 本段同时收纳 0.3.0 之后已合入的 R5 技术风险修复与 Aurora Glass 双皮肤/氛围层（原 `[Unreleased]` 内容，
> 2026-11-11～12），避免出现两个并行未发布段。

### 新增

- **场景 JSON v2（N1 029/030，ADR [D-N1-1](docs/decisions/2026-11-12-scenario-dsl-v2.md)）**：用户自上传场景新增 `schema_version=2`——9 个白名单谓词（`flag` / `objective_done` / `has_item_tag_count` / `item_in_room` / `npc_in_room` / `turn_gte|turn_lte` / `world_time_gte|world_time_lte`）并复用 `all/any/not/always`；顶层 `flags[]` 为**派生命题**（≤16 个、统一依赖图环检测、读时求值、不写 `GameState`）；多结局冻结「`priority` 降序 → 声明序 → 首个满足 → 目标全完成/HP≤0 兜底」；**v1 文件逐字兼容**，无 eval/脚本、不新增 EventType/状态字段，非法定义给带 JSON 路径的结构化错误。
- **场景单文件分享导出（N1 032）**：新增只读 `GET /api/v1/scenarios/{scenario_id}/export`——响应体就是可**直接再导入**的场景定义 JSON 原文（保留 `schema_version`/标题/作者/flags 等全部字段），`attachment; filename="<slug>.json"`，不含库内 ID/时间戳/服务器路径；v1/v2 导出→再导入 roundtrip 均有测试；分享=文件交换（无云端/账号/市场）。
- **网页场景编辑器（N1 031）**：新增 `/scenarios/editor` 路由——房间/物品/NPC/玩家/目标/结局/建议表单 + v2 `flags`/多结局 `priority`/13 个白名单原子与组合子（递归条件表单，>3 层提示改用 JSON 原文）；表单与 JSON 原文双向同步、600ms debounce 调用 `POST /api/v1/scenarios/validate`（带 JSON 路径的 errors/warnings、序号保护只应用最新结果）；保存走 `POST /api/v1/scenarios`（按内容 FNV-1a 生成稳定 `Idempotency-Key`），成功后跳详情并可「按此场景开局」；载入 v1 文档只提示不自动升级；场景详情新增「导出 JSON」下载（N1-032 网页侧）与场景库/导入页入口。编辑器**只产出 JSON、不旁路服务端校验、不直写库**，无 `v-html`；375px 单列可用。
- **示例内容包与一键体验（N1 035/036）**：新增 `examples/content-pack/`——2 个可玩 v2 场景（「灰烬灯塔」4 房间/6 flags/3 结局；「镜井」3 房间/9 flags/3 结局，含物品选择与时间两类失败分支）、12 条世界书、1 张人物卡与 README；新增 `scripts/quickstart-content-pack.ps1`（PowerShell 5.1/7，UTF-8 BOM）与 `.sh`：临时数据库 + 显式离线启动，自动校验/导入并建一局「灰烬灯塔 + 世界书」后打开 `/game/<id>`，`-Check`/`--check` headless 自检；不读写 `data/textgame.db`、不复制/打印密钥。
- **流式叙事阶段 1（N2 038–040，ADR [D-N2-1..N2-8](docs/decisions/2026-11-11-streaming-phase1.md)）**：新增 `POST /api/v1/turns/stream`（`application/x-ndjson`，5 事件 `start`/`delta`/`replace`/`done`/`error`；`done.result` 与 `/turns` 完全同构的 `TurnResult`）；服务端从 JSON 叙事流增量提取 `narrative` 前缀（转义/跨 chunk/上限 150+64，与最终文本 exact match），完整 JSON 到达后仍走原校验/一次修复链，失败以 `replace` 回退模板；网页对局页流式渲染增量、`replace` 覆盖、`done.result.text` 为权威；传输/协议失败自动降级普通 `/turns` **恰好一次**（业务 `error` 事件按普通回合失败处理、不降级；`fetch` + `ReadableStream`，禁 `EventSource`、无自动重试）；幂等/CAS/唯一写入者/断连不撤销语义与旧端点一致。真实 DeepSeek 验收：端到端首 `delta` **1.79–2.09s**、总 **2.53–2.99s**（R-T8 体感缓解）。
- **网页可访问性（N2 041/042）**：全局跳过链接落点 + 路由切换焦点管理、面板/确认焦点、指令框完整键盘操作（`/` 聚焦、↑↓ 历史、Esc、Ctrl/Cmd+Enter、`aria-busy`/`aria-keyshortcuts`）、对话流 `aria-live` 播报策略；双主题对比度按 **WCAG 2.1 AA** 修正（日间 fg/bg 16.63:1、夜间 17.71:1，按钮 6.29/5.70:1，夜间边界 3.23:1）；打字机/粒子/极光/过渡遵循 `prefers-reduced-motion`，`prefers-reduced-transparency`/`forced-colors` 有降级。
- **移动端 375px 与触控目标（N2 043/044）**：9 个页面 375px 无横向溢出、长文本/长 JSON 换行与滚动容器、对局输入区可达、弹层不超视口且可关；窄屏（≤760px）按钮/chips/导航/输入/摘要/开关/复选统一 ≥44×44 CSS px（内联文本链接按 WCAG 2.5.5 inline 例外），桌面 1280px 视觉密度与既有 e2e 断言零变化。
- **终端卡库浏览（N3 047）**：`--list-cards` 支持 `--card-query`（标题/来源过滤）、`--card-limit`（1..100）、`--card-all`，逐卡展示标题/来源/更新时间/完整卡 ID 与可玩提示；新增 `--card-detail <卡ID>` 只读查看安全子集（标题/来源/属性摘要/背包提示/警告）；非法 ID/未知卡/后端不可用给中文可读提示与非 0 退出码。
- **终端场景包批量导入导出（N3 045）**：`--import-scenarios <目录>`（`--import-limit` 默认 50/上限 100；逐文件 ≤256KB/UTF-8/JSON 对象，幂等键=文件内容 sha256；`[ok]/[skip]/[error]` 报告，有失败退出码 1）与 `--export-scenarios <目录>`（`--export-limit`；只读 list+detail 写 `<slug>.json`，重名追加 ID 后缀，导出文件可直接再导入）。
- **存档单文件导出/导入（N3 048）**：新增只读 `GET /api/v1/games/{game_id}/export`（`export_version`/`exported_at`/`game`/`events`/`snapshot`/`checksum`，不含 API Key/凭据/日志）与 `POST /api/v1/saves/import`（必带 `Idempotency-Key`；字段白名单 + `sha256` checksum + `Event` 模型 + 全量回放 + `state_hash`/版本/回合/序号一致性校验，全过才以新 `game_id` 写库，可 `/view`/`/replay` 并继续 `POST /turns`；上限 8MB/20000 事件；非法包零写入、同键幂等重放）。
- **N4/N5 工具与基线**：新增 `scripts/rebaseline_golden.py`（m3 叙事受控重基线唯一工具：默认 dry-run，`--apply` 需 `--confirm` + `.bak` 备份 + 双 sha256 自检；当前 m3 dry-run **0 diff**、tracked golden 零改动）与 `scripts/perf-long-session.py`（120 回合/601 事件：total 8.47–9.57s、per-turn p95 115–140ms、tracemalloc 峰值 3.83MB，并有宽松阈值回归测试）。
- **网页自由制造开关（R5，R-19 运行期 opt-in）**：设置页新增「自由制造」卡片，开启前二次确认“裁决/重试链路内调 LLM、可能重复计费”；确认后共享 `FreeCraftSwitch` 即时生效（无需重启），偏好持久化到 `data/free_craft.json`；新增 `GET/PUT /api/v1/settings/free-craft`（开启需大模型已启用并确认风险），无头/生产仍可用 env 显式开启。无头/生产开启需**同时**设置 `TEXTGAME_FREE_CRAFT_ENABLED=true` 与 `TEXTGAME_ALLOW_RESOLVER_LLM=true`（显式接受裁决链路调 LLM 的风险，否则启动期 fail-fast）。
- **日间/夜间双皮肤（R5，Aurora Glass）**：默认**日间**；顶栏「外观」按钮一键切换，视觉设置菜单可选「跟随系统」。
  `stores/theme.ts` 维护 `light|dark|auto` 并写 `<html data-theme>` + `meta[theme-color]`；
  `web/public/theme-init.js` 在任何绘制前写好首帧皮肤（外链脚本，满足 CSP `script-src 'self'`），刷新不闪色；
  支持 View Transition 时从点击位置做 `clip-path: circle()` 圆形扩散换肤，`prefers-reduced-motion` 时瞬时切换。
- **氛围层（R5）**：`AtmosphereOverlay.vue`（4 团极光 + 细颗粒 + 鼠标聚光，纯 CSS/合成层）与 `MoteField.vue`
  （Canvas 微光粒子，按主题切调色板与混合模式）。两者均懒加载、`data-visual=off` 不渲染；
  粒子层页面隐藏暂停；系统「减少动态效果」/低功耗档自动降级。
- 主题/外观单测（R5）：`web/tests/unit/theme.test.ts`（9 例）、`themeToggle.test.ts`（5 例）、
  `moteField.test.ts`（6 例，替代 `emberField.test.ts`）。

### 变更

- **流式开关与回滚（N2 038，ADR D-N2-8）**：新增 `TEXTGAME_STREAM_ENABLED`（默认 `true`：后端端点常开、旧客户端不调用即死代码无副作用；`false` → 流式端点返回 404 统一信封，前端自动降级普通 `/turns`）——一键回滚，且**旧 `POST /turns` 契约、`TurnResult`、事件、`GameState`、默认离线/golden/`demo`/`console --check` 零变化**。
- **版本与发布口径**：版本号仍为 `0.3.0`（本段为 0.4.0 未发布草案，**不 bump** `pyproject.toml`/`__version__`）；README/README.en/`RELEASE.md` §2.3/部署与分发评估已同步 N 系列交付状态、0.3.0 当前快照与 0.4.0 待发布口径（N 系列增量门禁与发布 checklist 由维护者在发布时复跑并拍板）。
- **契约仅加法**：新端点（`/turns/stream`、`/scenarios/{id}/export`、`/games/{id}/export`、`/saves/import`）与 LLM 客户端可选流式方法均为加法；v1 场景/世界书/人物卡/旧存档与旧 `/turns` 请求响应形状不变。
- **视觉基线替换（R5）**：旧「2G 深绿终端」皮肤整体退役（用户明确要求「不要考虑 2G」）。
  `web/src/styles/terminal.css` → `web/src/styles/theme.css`，改为三层 token：语义色 / 玻璃面与描边 / 特效；
  叙事正文改用系统衬线字体，UI 用系统无衬线，指令输入与 JSON 保留等宽；面板改玻璃拟态 + 圆角 + 分层阴影。
- **不引入 element-plus（R5）**：玻璃、极光、渐变与双皮肤全部由 token + 原生 CSS 自建，组件库不提供这些能力且需覆盖整套主题变量
  （第 23 章 §23.4 已回写）。
- **可访问性/降级（R5）**：日间 fg/bg 16.63:1、夜间 17.71:1（按钮 6.29 / 5.70），`backdrop-filter` 不可用与
  `prefers-reduced-transparency` 回退不透明底，`forced-colors` 交还系统色。
- `web/dist` 重建入库（039 验收时重复 build 可复现；tracked `web/dist` 漂移由 CI dist 漂移门禁校验）。

### 修复

- **分享/导入边界（N1 032 / N3 048）**：场景导出只读、零写入、未知/已删/库内定义损坏 404、免幂等键且响应不含库内字段；存档导入失败（字段缺失/非法 JSON/checksum 不符/事件模型非法/回放 state_hash 或版本不一致/超 8MB 或 20000 事件）一律结构化错误 + **零写入**，非法包不写幂等表，同键同参可重放。
- **a11y 可读性/焦点（N2 041/042）**：夜间低对比修正（`--border-strong` 日间 α0.22→0.50（1.60→3.38:1）、夜间 α0.20→0.36（1.93→3.23:1））、焦点环增强、打字机/粒子/极光/过渡遵循 reduced-motion、reduced-transparency/forced-colors 降级；双主题 11 条对比度 token 回归 + reduced-motion/live region 用例锁定。
- **测试稳定性（N1 032）**：`tests/unit/test_scenario_service.py` 的 R-04 并发用例改用文件型 SQLite，修复并行负载下共享内存连接导致的偶发 `sqlite3.InterfaceError`（断言零改动）。
- **主线/状态（R5）**：story 物品 `tags[0]` 统一为模板键，背包不再把 `consumable` 当模板合并，制造不再因语义标签 `ValidationError`；用户场景导入期校验未知属性/自指出口/物品双放置并做建局 dry-run（422 取代建局 500）；commit 后的快照/幂等写失败只记 WARNING，不再把成功回合报告为失败；`EventLog.append` 校验 `rng_seed`，建局校验初始状态种子/玩家槽位；出口 `in`/`out` 可导航且中文名本地化；`/view.lore` 与下一回合注入的 turn 对齐（R-16）。
- **并发/升级（R5）**：新增进程级分段锁覆盖创建/导入/回合（单进程；多进程仍为已知限制）；启动 `ensure_schema()` 自动检测并升级旧库，Alembic 迁移随 wheel 分发；快照表新增 `(game_id, snapshot_version)` 唯一约束（Alembic 0006）+ upsert，快照写入按存档串行；幂等表不再缓存 4xx 失败响应（修正输入后同键可直接重试）；批量休眠 `SCENE_ENTRY` 只认本回合移动（不再永久唤醒）；每回合只读事件尾部增量、LLM 调用日志有界。
- **LLM/世界书/安全（R5）**：解析白名单从 `tool_specs` 派生（`inspect_item`/`craft_item` 不再被拒）；解析/制造请求带 `prompt_version`；叙事兼容制造产物 dict；重试等待受整链 deadline 约束；世界书嵌套量词正则（含 `(?:a+){2,}`）导入期拒绝；上传 JSON 拒绝 NaN/Infinity；ASGI 请求体 1MB 上限（Content-Length + chunked 实际计数，存档导入路径单独放宽到 8MB+报文头）；人物卡生成限流改为按 IP 且有界；`TrustedHostMiddleware` + 非 loopback `TEXTGAME_API_TOKEN` 鉴权 + `api_base` 拒绝云元数据/链路本地地址。
- **前端（R5）**：`loadGame` 失败先清空旧对局状态；零事件只读回合不写 history，并新增「最近回应」可见区块（修复零事件回合正文刷新后不可见的回归，+4 例单测）；幂等键改为 store 生成最终 UUID、client 直传，`create-game` 键纳入 `world_book_id`。

### 内部（工程与验证）

- **测试基线（2026-11-12 实测，HEAD `553d0a0` + 工作区）**：后端全量 pytest **2456 passed / 0 failed**（固定顺序与随机 seed 复跑；含 replay **69 passed**、security **172 passed**）；前端 `pnpm --dir web test` **69 files / 481 tests**（031 编辑器 + 043/044 移动端后）；真实浏览器 e2e **32/32 步、205/205 断言**（含编辑器/导出/a11y/流式/移动端步）；`demo --check` **7/7**、`console --check` **4/4**；`mypy src/textgame` **Success（118 files）**、`lint-imports` **2 kept / 0 broken**、变更 py 的 `ruff format/check` 通过。
- **新增 ADR**：[D-N1-1 场景 DSL v2](docs/decisions/2026-11-12-scenario-dsl-v2.md)、[D-N4-1 m3 叙事受控重基线](docs/decisions/2026-11-12-m3-narrative-rebaseline.md)、[D-N4-2 Playwright/a11y 工具口径复评](docs/decisions/2026-11-12-playwright-a11y-eval.md)、[D-N5-1 便携包/PyInstaller/安装包评估](docs/decisions/2026-11-12-portable-package-eval.md)。
- **评估结论**：Playwright 继续零依赖 CDP（`scripts/e2e-web.mjs`；不引入约 19MB npm 依赖、不建长期 CI 浏览器矩阵，D-N4-2）；便携包/exe/安装器本轮不实现、不新增构建依赖与 CI 打包 job、不产出 exe（D-N5-1）。
- **分片验收数字**：v2 目标测试 **132 passed**、分享 **65 passed**、流式后端新增 **70 例** + 验收组合 **126 passed**、a11y 新增 **36 例**、移动端新增 **8 例**、编辑器新增 **30 例**、存档 **15 passed**、终端批量 **21 passed**、卡库 **29 passed**、内容包 **11 passed**、重基线+replay **74 passed**、长会话 **2 passed**；`web/dist` 重建入库且两次 build 逐文件 SHA256 一致。
- **R5 历史验收（2026-11-11）**：后端 **2233 passed**（含 replay **69**、覆盖率 **87.56%**）、前端 **60 files / 383 tests** + typecheck 全绿，本地真实浏览器 e2e **26/26 步、126/126 断言**。

### 文档

- **新增设计与评估文档**：[场景 DSL v2](docs/design/scenario-dsl-v2.md)、[a11y 检查清单](docs/design/a11y-checklist.md)、[长会话性能基线](docs/design/long-session-perf.md)、[存档导出/导入](docs/design/save-export-import.md)、[场景包批量导入导出](docs/design/scenario-pack-batch.md)；[流式叙事阶段 1 设计](docs/design/narrative-streaming.md) 增补 §9 实施结果/11 条偏差登记；章节回写第 6/8/12/17/18/20/23 章（`docs/开发文档.md` 由 Lead 同批重建并逐字节对齐）。
- **进度文档与批次记录**：新增 029/030/031/032/035/036/038/039/040/041/042/043/044/045/047/048/054/055/056/058 共 **20 篇**单次进度文档 + **QA 独立红队报告 1 篇**（`docs/PROGRESS.md` 索引由 Lead 同批回填）；README/README.en 同步 N 系列交付状态与 0.4.0 待发布口径。
- **章节回写（N 系列）**：第 6 章（场景导出/存档导出导入 §6.8）、第 8 章（叙事 Agent 流式渲染 §8.6）、第 12 章（流式补全扩展 §12.12）、第 17 章（N 系列交付状态 §17.9.6）、第 18 章（性能基线 §18.11 + 受控重基线）、第 20 章（R-T15/R-P2/R-T4 等风险状态）、第 23 章（§23.11 a11y/§23.12 流式/§23.13 编辑器/§23.14 移动端/§23.15 内容包）；`docs/开发文档.md` 由 Lead 同批重建。
- **R5 审计与回写**：[LLM 参与与自由度评估审计（2026-11-12）](docs/audits/2026-11-12-llm-participation-freedom.md)（46 次真实 LLM 调用证据 + 15 条探针矩阵 + 六维自由度评分）与 [R5 批次回写](docs/progress/R5-2026-11-12-aurora-glass-and-pitfalls-review.md)；[技术风险审查与修复记录（R-01～R-25）](docs/audits/2026-11-11-technical-pitfalls-review.md) §7。

> **未纳入本草案（待后续批次）**：N1 **033–034 跑团世界模组 P0**（需用户拍板新增 EventType，D-N6）、N3 **046 世界书条目编辑**（可选）——交付后另立条目；网页场景包/存档多文件 UI 亦待后续 web 批次（后端端点已就绪）。
> **发布状态**：本段仅草案；`0.4.0` 发布（版本 bump / tag / GitHub Release / PyPI）由维护者按 [RELEASE.md](RELEASE.md) 拍板执行。

### R6 遗留项收口（2026-11-12）

- **R-19 pre-queue 迁移（ADR [D-R6-3](docs/decisions/2026-11-12-r19-pre-queue-migration.md)）**：自由制造草案移出 Resolver——新增 `orchestration/pre_queue.py`，提案入队/裁决前预生成一次 `CraftDraft` 并经 `prepared_crafts` 按 `proposal_id` 透传，`CraftService._execute_prepared_free_craft` 纯校验/框架重算；**Resolver 内零 LLM**（`caller=resolver` 无载荷→`INVALID_INPUT`，永不回落生成器），CAS 重试复用同一载荷；`TEXTGAME_ALLOW_RESOLVER_LLM`/网页 risk 二次确认保留为历史兼容（文案更新）。新增 16 例。
- **R-25 反代信任（ADR [D-R6-1](docs/decisions/2026-11-12-proxy-trust-and-access.md)）**：`TEXTGAME_TRUST_PROXY` 默认 **false**——未启用时带 `X-Forwarded-For`/`Forwarded` → 403（防同机反代绕过）；启用后取 XFF 首跳（Forwarded 基础 `for=`、非法 fail-closed、XFF 优先），非 loopback 仍要求 `TEXTGAME_API_TOKEN`。6 组测试。
- **R-04/R-05 单进程策略（ADR [D-R6-2](docs/decisions/2026-11-12-single-process-and-migration-lock.md)）**：检测到多 worker（`WEB_CONCURRENCY`/`UVICORN_WORKERS`/`GUNICORN_WORKERS`>1）且未 `TEXTGAME_ALLOW_MULTI_WORKER=true` → 启动 `RuntimeError`；`ensure_schema` 升级加 `*.migrate.lock` 跨进程文件锁（Windows msvcrt / POSIX flock、10s 超时、锁内重判；健康/新库不建锁）。
- **健壮性与 QA P2**：`list_games` 尾部叙事改单条有界 SQL（`read_last_narrative_event`，>200 事件边界测试）+ `_verified_games` 有界 LRU(4096)；R-16 `/view.lore` 与下回合注入 turn 一致性测试；chunked 超限 JSON 端点由 400 修复为 **413**（+2 测试）；v2 条件 `StrictBool`/`StrictInt`（v1 lax 冻结）；终端空库提示去字面 "JSON"；`GET /games/{id}/export` 声明 `response_model=SavePackage`（OpenAPI 断言、响应逐字不变）。
- **配置/凭据/smoke**：`TEXTGAME_FREE_CRAFT_SETTINGS_FILE` 等 3 个 Path 字段空串归一 `None`（validator）；Windows 凭据文件尽力 `icacls` 收紧（失败仅 WARNING，POSIX 0o600）；smoke `max_tokens=128` + `content_len` 输出、空 content 仅 WARN。
- **前端**：夜间暗色 token 提亮（`--fg-dim`→`#c6cce2`、`--accent`→`#c8d3ff`、`--ambient-1..4` 不透明度下调；CDP 字形像素实测最差 accent **2.66→4.89**、fg-dim **2.93→5.34**、渐变标题 **5.82→7.48**，夜间全部 ≥4.5；新 token 纯底 dim/bg **12.47**、accent/bg **13.49**；日间 token 未改）；375/320px「视觉设置」菜单改全宽下拉；e2e 新增 `mobile.visual-menu`。
- **文档**：新增 ADR D-R6-1/2/3 并登记 `docs/decisions/README.md` 索引；第 12 章 §12.11、第 13 章 §13.4、第 16 章、第 18 章、第 23 章回写；R5 §4 20 条最终状态、N 系列 QA P2 状态与文档漂移（e2e 接入 CI、第 18 章 Playwright/axe 结论、RELEASE 031/058、流式 `start.turn_id`、分享导出重序列化、8MB 精确匹配）见 [R6 进度文档](docs/progress/R6-2026-11-12-leftovers.md)。
- **验证**：后端 **2521 passed**（replay **69**、mypy 119 files、ruff format 281 files/ruff check 全过、lint-imports 2 kept）；web **69 files / 481 tests**（typecheck/build、dist 38 文件连续两次 build SHA256 一致）；e2e **33/33 步、211/211 断言**；demo/console `--check` exit 0；`check_docs` PASS；新增测试 **+65**。本批**不 bump 版本**（`0.4.0` 待发布、RELEASE.md checklist 未执行）。
## [0.3.0] - 2026-11-11（玩家启动器与退出体验，N5 057）

### 新增

- **Windows 双击启动器**：仓库根 `启动游戏.bat` → `scripts/play.ps1 -Launcher`：WinForms 启动器小窗口
  （状态/「打开游戏」/「退出游戏」；无 GUI 自动降级控制台）；隐藏启动本机服务、日志追加
  `data/logs/launcher-server.log`、自动打开浏览器；已在运行时只打开浏览器；uv 缺失给中文安装指引
  （不自动下载/安装）；关闭启动器窗口清理服务进程树。`scripts/play.sh --launcher` 对齐 Unix。
- **游戏内「退出游戏」**：网页全局 header 按钮 → 二次确认 → `POST /api/v1/app/shutdown` 优雅退出 →
  全屏「游戏已退出 / 可以关闭此页面了。」覆盖层；非启动器模式显示固定提示
  「当前不是启动器模式：请在运行服务的窗口按 Ctrl+C 退出。」；只关浏览器时，前端每 15s 心跳、
  服务端默认 300s 无心跳自动退出（`TEXTGAME_IDLE_EXIT_SECONDS`，0=关闭）。
- **启动器控制面 API**（仅 `python -m textgame.launcher` 注入控制器时启用）：
  `GET /api/v1/app/status`（launcher_mode/idle_exit_seconds/shutdown_supported/version）、
  `POST /api/v1/app/heartbeat`、`POST /api/v1/app/shutdown`（`{"confirm":true}`；无控制器 404、
  非回环 403、confirm 非 true 422、重复幂等）；新增进程入口 `python -m textgame.launcher`
  （`--host/--port/--idle-exit-seconds/--log-level`）与空闲 monitor。**默认 `uvicorn textgame.main:app`
  行为零变化**（控制面不可用、前端只显示说明面板）。
- **兜底退出**：根目录 `退出游戏.bat` → `play.ps1 -Stop`（按 `netstat` 四列精确匹配 127.0.0.1:port
  监听 PID 后 `taskkill /T /F`，绝不按进程名宽泛匹配）；无实例时提示「游戏没有在运行」。
- **启动器自检**：`scripts/play.ps1 -LauncherCheck` / `play.sh --launcher-check`（headless、临时库+
  空闲端口，断言 status/heartbeat/shutdown、进程退出与端口释放，打印「Launcher 自检通过」）；
  `RELEASE.md` 新增启动器门禁 L1–L3。

### 变更

- 版本 `0.2.0 → 0.3.0`（玩家可见能力批次，按 RELEASE.md 版本策略；N5 发布体验轨 057）。
- 文档：README/README.en 快速开始改为「双击启动器」优先并补退出四通道 FAQ；
  `docs/design/deployment-and-distribution.md` 分发形态 A 更新；`.env.example` 增空闲退出配置；
  ADR [D-LA-1..N6](docs/decisions/2026-11-11-player-launcher-and-exit.md)、第 17 章 §17.9 N5、进度文档 057。

### 验证（Lead 实跑）

全量 pytest **2186 passed**、replay **68**、web **56 files/347 tests**、浏览器 e2e **26 步/125 断言**
（非启动器分支）+ 启动器模式 e2e **26 步/126 断言**（真实「确认退出→服务优雅退出→启动器进程 exit 0」）、
`play.ps1 -LauncherCheck` **PASS**、`play.ps1 -Check` 旧口径 PASS、四门全绿、`demo/console --check` 逐字不变。

## [0.2.0] - 2026-11-11（首个可发布快照）

### 新增

- **WB2 世界书详情字段可见性（2026-11-11）**：`GET /api/v1/world-books/{world_book_id}` 条目新增 14 字段
  （`probability`/`use_probability`/`group`/`group_weight`/`group_override`/`group_scoring`/`scan_depth`/
  `sticky`/`cooldown`/`delay` + `case_sensitive`/`match_whole_words`/`exclude_recursion`/`prevent_recursion`），
  其中 `raw_json` 字段由 `parse_entry_extras` 还原（与激活同一解析函数、无迁移）；网页详情条目表展示
  概率/包含组/定时效果/position-depth/扫描深度/递归开关（中文标签）。契约四处同步（schemas / TS types /
  client 运行时校验 / 契约测试）。全量 pytest **2156 passed**、replay 68、web **53 files/329 tests**、
  e2e **25 步/122 断言**。详见 [WB2 进度文档](docs/progress/WB2-2026-11-11-worldbook-detail-fields.md)。
- **M6 后计划表（2026-11-11）**：第 17 章新增 §17.9（四条轨道 + 029–060 编号区间 + 通用出口门禁），
  ADR [D-N1..N7](docs/decisions/2026-11-11-post-m6-roadmap.md) 定案编号/优先级/发布就绪定义/流式阶段 1
  边界/DSL v2 边界/跑团模组唯一 EventType 例外/不做清单；PROGRESS §4 与第 22 章同步。
- **发布就绪（N4 049–053）**：版本号 `0.2.0`（`pyproject.toml` + `textgame.__version__`）、本 CHANGELOG
  发布段、README 发布段、英文 README 补全、`RELEASE.md` 发布 checklist（含 tag/release notes 流程）、
  Docker 评估落文档（[deployment-and-distribution](docs/design/deployment-and-distribution.md)，
  结论：默认 wheel/zip + `play` 脚本分发，镜像暂不提供），`uv build` wheel 干净环境安装冒烟
  （console/网页入口 + 内嵌 `web/dist`）。
- 自 `0.1.0` 以来的全部玩家可见变更（场景 JSON/世界书/API Key 等）见下节历史记录，不重复登记。

### 变更

- 版本记录：`0.1.0`（开发版）→ `0.2.0`（首个可发布快照）；此后按发布 checklist 打 tag（`v0.2.0`，由维护者执行）。

## [0.1.0] - 开发版（未对外分发，历史记录）

### 新增

- **UC 批次：玩家自上传场景 JSON + 酒馆级世界书 + 运行期 DeepSeek API Key（范围外，2026-11-11）**：
  **玩家内容平台**——场景 JSON v1（`schema_version/slug/meta/player/rooms/items/npcs/goal/endings/suggestions`；
  条件 DSL 白名单谓词 `in_room/has_item/has_item_tag/npc_state/hp_lte/hp_gte` + `all/any/not/always`，禁 eval；
  `extra="forbid"` + 跨引用校验 + 结构化 `errors/warnings`（path+message））；确定性 ULID 派生
  （`sha256(scenario_id|kind|key)`，玩家固定 `PLAYER_ID`）+ `build_initial_state` + `UserScenarioHooks`
  （目标/结局/进入伤害/NPC 对话，兜底结局）；`scenarios` 表 + **Alembic 0005**（软删、每次导入新 `scn_` ID）+
  `ScenarioService` + 5 端点（validate/import/list/get/delete；import 双接受 JSON 原文与 multipart）+
  `POST /games {scenario_id}`（`scenario="custom"`，`game_created.params` 记 `scenario_id`，与 `card_id`/`scenario` 互斥、
  可与 `world_book_id`/`seed` 组合）；网页 `/scenarios` 列表/导入（拖放+校验预览）/详情 + Home「场景」下拉 + 对局场景徽章 +
  终端 `--list-scenarios`/`--scenario-id`；示例 `examples/scenarios/tiny-tower.json`、`ember-watch.json`。
  **酒馆级世界书**——`sticky/cooldown/delay` 定时效果（0..100，按事件历史逐回合模拟，`history=None` 逐字等价旧实现，
  `/view.lore` 与回合注入同口径）、正则键 `/pattern/flags`（Python `re.search`，仅 i/m/s，非法/超长降级字面量+warning，
  输入截断 2000 字符）、包含组（`group/group_weight/group_override`：同组每回合至多 1 条，override→weight→order→uid）、
  `position/depth` 参与 lore 块排序；`world_book_entries` 无新列（`raw_json` 还原）；导入 warnings 形状不变（新增诊断码）。
  **运行期 DeepSeek API Key**——优先级「显式 `TEXTGAME_LLM_ENABLED=false` → `.env`/env 开发 key → 本机
  `data/llm.credentials.json` → 离线」；`RuntimeLLMClient` 热切换（网页保存后**当回合生效**，无需重启）；
  `GET/PUT/DELETE /api/v1/settings/llm` + `POST /api/v1/settings/llm/test`（状态 9 字段、打码 key、无幂等键）；
  网页 `/settings`（保存并启用/测试连接/清除二次确认/安全提示）+ Home 未配置可关闭横幅；demo/console `--check`
  装配前强制离线保证 golden 逐字可复现。验证：全量 pytest **2155 passed**、replay **68**（旧 golden 零改动）、
  web **53 files/326 tests**、typecheck/build OK、浏览器 e2e **25 步/119 断言**、tracked `web/dist` 零漂移、
  `demo --check` 7/7 与 `console --check` 4/4 逐字不变、ruff/mypy/lint-imports/check_docs 全绿、Alembic head 0005。
  详见 [UC 进度文档](docs/progress/UC-2026-11-11-user-content-and-llm.md) 与
  [ADR D-UC/D-LLM/D-WB](docs/decisions/2026-11-11-user-content-and-llm-runtime.md)、
  [场景 JSON 设计](docs/design/user-scenario-modules.md)。
- **H4 批次：世界书 MVP + 前端炫酷化 + 剧情失败结局（不占 M1–M6 编号，2026-11-11）**：
  **世界书**——SillyTavern World Info（entries map/array/裸 map）/ Character Card V2 `data.character_book` / 本项目简化 schema
  三格式导入（multipart ≤256KB、`POST /world-books/validate` 只读预检、列表分页搜索、详情、软删、幂等/413/422/404/409，
  共 5 端点）；`world_books`/`world_book_entries` 两表 + **Alembic 0004**；`POST /games` 加法 `world_book_id`
  （`game_created.params` 仅传入时记录，旧请求逐字不变；未知/已删 404，可与 `card_id` 共存）；确定性触发引擎
  `domain/world_book.py`（keys/keysecondary/selectiveLogic 0–3/constant/order/scanDepth/递归≤2/预算 800 字/≤8 条/
  `sha256` 确定性概率）；`TemplateNarrator` 与 LLMNarrator 注入世界设定（旧场景/无世界书文本逐字不变）；
  `/view` 加法 `world_book`/`lore.active_entries`（`GET /games/{id}` 加法 `world_book_id`）；前端 `/world-books`
  列表/导入/详情 + Home 世界书下拉 + 对局「世界设定」面板。示例：`examples/world-books/story-lorebook.json`（29 条 tavern）、
  `story-character-card.json`（角色卡 V2 + 内嵌 4 条）、`README.md`。**炫酷化**——自写 Canvas 2D 余烬粒子（懒加载
  1.49KB gzip/低功耗 20fps/页面隐藏暂停）+ 纯 CSS CRT 层（扫描线/暗角/辉光/微噪点）+ 打字机（可跳过、读屏完整文本）+
  目标/结局动效 + **三档视觉设置**（全特效/低功耗/关闭，localStorage 持久化，`prefers-reduced-motion` 自动降级）；
  **零新增前端运行时依赖**；首屏 gzip 51.7KB（增量 ≈+8.9KB）；375px/对比度/`security-scan` 保持。**剧情失败结局（L1）**——
  新 EventType `ENVIRONMENT_DAMAGE` + reducer；story 枯井「寒气」每次进入 -6 HP，两次进井 HP→0 可达「倒下」结局，
  药水/烈酒仍可救回；旧场景不产生该事件（旧 golden 零影响）。**其它**——`TurnResult.narrative_source`（llm/template/null）
  加法（L3）；`scripts/e2e-web.mjs` 扩到 22 步/112 断言/19 截图并接入 CI web-build job（临时 uvicorn:8794 + headless Chrome，L6）。
  验证：全量 pytest **2007 passed**（0 failed）、replay **68 passed**（旧 golden 61 零改动 + provenance 7）、web vitest
  **41 files / 241 tests**、frontend_contract **19 passed**、世界书/伤害/激活 QA **47 例**、e2e **22 步/112 断言**；
  `demo --check` 7/7 与 `console --check` 4/4 逐字不变。详见 [H4 进度文档](docs/progress/H4-2026-11-11-worldbook-visual.md) 与
  [ADR D-H4-1..6](docs/decisions/2026-11-11-h4-worldbook-and-visual.md)、[视觉参考](docs/design/frontend-visual-refs.md)、
  [世界书格式兼容](docs/design/world-book-format-compat.md)。
- **M5E-2 裁判 Agent 接线（M5 出口附加项完成，2026-11-11）**：`domain/judge.py` 契约（`JudgeEvidence`/`JudgeOutput`、`verdict ∈ {allow, deny, modify}` + 只读 `allowed`、置信门槛 0.7、证据上限 5）+ `agents/judge_agent.py`（`JudgeAgentLike`/`DeterministicJudgeAgent`/`LLMJudgeAgent`：`task="judge"`、`prompt_version="judge@v1"`、STRONG、`max_repair=1`，输入=脱内部 ID 的私有感知切片+提案+规则空白说明，失败/异常→`None` 交确定性兜底）+ `needs_judge` 生产点（`ToolRegistry.agent_assisted_tools()`；生产 8 工具全 `DETERMINISTIC` → 当前 0 消费者=机制就绪）+ `RuleEngine.verify_agent_override`（严格类型/NaN 校验，非法一律否）/`deterministic_fallback`（恒保守否决、零事件）+ Resolver 懒读取分支（非 needs 零访问 judgements、applied reason 可审计）+ 编排层每回合≤1 次/CAS 重试复用证据；`main.py` 仅 `llm_enabled=true` 构造。**默认离线 `judge=None`、零模型调用、逐字等价**（旧 golden 与 demo/console `--check` 不变）；新增 64 例 + QA 独立红队 52 例 + 5 个手工合成 cassette（零网络）。详见 [M5E-2 进度文档](docs/progress/M5E-2-2026-11-11-judge-wiring.md) 与 [ADR D-M5-14](docs/decisions/2026-11-11-m5-judge-wiring.md)。
- **R3 玩家体验打磨批次（范围外，不占 M1–M6 编号，2026-11-11）**：① **结局体验**——结局屏通关总结「本局 N 回合 · 目标 X/Y · 生命值 HP/MAX」、结局文本进冒险记录（纯派生不重复）、终局回合叙事不再只有普通移动描述；② **新手引导**——新局冒险记录第 1 条「开场」（房间描述+目标进度，首回合后不重复）、冒险记录与回放页只读提示、诊断面板仅 dev 渲染、无事件回合来源徽章改中性「来源未知」；③ **文案去重**——细看（inspect）句级去重修「空气里浮着灰尘与旧木头的气味」连排、story 对话删固定框架重复句、up/down 移动改爬梯自然文案（四方向逐字不变）；④ **可执行建议**——缺材料/缺工具/工位不符时 hint 点名材料与所在房间、`suggested_actions[0]` 当前可执行一步（同屋「拿起X」/相邻「去{房间}拿X」实测解析为 `move_to` 并真的移动）；⑤ **入口与兼容**——console `--card-id/--card` 按卡开局 + `--list-cards`、旧格式存档（无 `scenario`/`quests`）读取/继续/发现兼容；⑥ **内部 ID 脱敏**——生成页保存区/卡库列表/卡详情/回放页不可见 `card_…`/`game_…`/未登记 actor ULID，`item.torch` 等模板键显示中文名。验证：全量 **1683 passed**（+QA 独立 12 = 1695 时点）、replay **68**（旧 golden 零改动）、web vitest **32 files/178 tests**、真实浏览器 e2e **18 步/101 断言**、demo 7 屏与 console `--check` 逐字不变。详见 [R3 进度文档](docs/progress/R3-2026-11-11-player-polish.md) 与 [ADR D-R3-1..5](docs/decisions/2026-11-11-m6-exit-and-r3-closure.md)。
- **M6-028 生成人物卡 UI（M6 4/4 完成，2026-11-11）**：新路由 `/cards/generate`（brief ≤1000 码点 → `POST /character-cards/generate` → 草稿预览/模板兜底徽章 → JSON 编辑+重新校验 → 保存到卡库 → 「查看卡片」/「按此卡开局」两个显式入口）；**显著离线提示**（默认离线生成即确定性模板兜底，`source=manual`/`generated_by=null`/`TEMPLATE_FALLBACK`，M6B-4 已知限制）；429 按 `Retry-After` 给中文等待提示且不自动重试；禁 `v-html`/自动定时重试。新增真实浏览器 e2e：`scripts/e2e-web.mjs`（Node 26 零依赖 CDP，14 步/62 断言 + 负向控制；**不接入 CI**，D-M6-11）。验证：web vitest 29 files/158 tests、契约测试 17 passed、全量 **1647 passed**、replay **68**（旧 golden 零改动）、玩家独立验收 PASS（P0 76/76，2 项展示层 P1 记入 R3）。详见 [028 进度文档](docs/progress/028-2026-11-11-llm-card-generate.md) 与 [ADR D-M6-11](docs/decisions/2026-11-11-m6-exit-and-r3-closure.md)。
- **R2 玩家体验批次（不占 M1–M6 编号，2026-11-11）**：**剧情场景 `story`「余烬哨站」**——6 房间/7 物品/1 NPC/3 步目标链（听老兵 → 造信号火把 → 带火石登塔）/胜利与倒下双结局；目标与结局读时纯求值（不新增 EventType、不改 GameState、不加迁移，旧 golden 61 零改动），结局后拒绝新回合；`/view` 加法 `scenario`/`goal`/`ending_text`/`suggested_actions`/`room.exit_names`，`GET /games/{id}` 加法 `scenario`/`ending_text`，新增只读分页端点 `GET /api/v1/games` 存档列表。**道具/对话真实化**：药水/烈酒按 `heal`/`hp_delta` 改 HP（满血零状态变化）、武器/工具不可被「使用」销毁、主线材料 quest 保护防软锁、新增只读 `inspect_item`（细看物品/NPC）、story 老兵确定性回话（首谈即 briefed）。**反馈/引导**：失败回合给具体中文原因（Resolver/服务层原因过滤后展示）+ 场景化建议，成功回合填 `suggested_actions`。**离线 NLU 增强**：inspect、背包/方向/日常说法同义词、实体别名、difflib 保守模糊、房间名导航（`PerceptionSlice.exit_refs`），88 条真实输入探测识别率 45%→85%，7 条 golden 输入映射逐字不变。**Web 对局回路重做**：replay 重建叙事历史、状态面板玩家化（隐藏内部 ID/qty/可点建议）、CONFLICT（HTTP 200 ok=false）自愈、成功刷新失败不重复执行、120s 超时、人话错误、服务端存档列表 + 最近对局一键续玩、目标面板/结局横幅、375px 窄屏与无障碍、路由懒加载（入口 js 139.7→101.4KB）。**Agent**：LLM 叙事事实供给（物品/NPC/房间/背包/目标入 prompt，内部 ULID 与原话不入 prompt）+ story cassette/wiring 测试（默认仍离线）。详见 [R2 进度文档](docs/progress/R2-2026-11-10-player-experience.md) 与 [ADR D-R2-1..12](docs/decisions/2026-11-10-player-experience.md)。
- **R2 开源入口轨（不占 M1–M6 编号，2026-11-10）**：**网页版一条命令**——`scripts/play.ps1|sh`（网页优先；`-Terminal`/`--terminal` 回终端、`-Check`/`--check` 启动自检 `/healthz`+`/`、`-Port`/`--port`、`-Llm`/`--llm` 真实 LLM 开关，uv 缺失/首次联网/端口占用/dist 缺失均给中文下一步）；**`web/dist` 预构建产物入库**（fresh clone 无需 Node/pnpm 即可玩网页版；`.gitattributes` 固定 `*.sh` LF、`*.ps1` CRLF、`web/dist` 不转换）；wheel 通过 hatch build hook 内嵌 `textgame/web_dist`（`pip install` 后同样有网页版；产物缺失时不阻断 editable 安装）；`quickstart.ps1|sh` 保留为终端别名；**终端支持 `--scenario story`**（首屏展示目标进度/子目标完成态与建议动作，结局时打印结局屏并停止空转）与 `--list`（列出最近存档）；`README.md` 重写（网页优先 + story 推荐首玩 + FAQ：PS5.1/中文路径/首次联网/存档位置/如何开 LLM/端口）、新增 `README.en.md`、`SECURITY.md`、Issue/PR 模板；`web/README.md` 端口口径对齐。
- **M1 骨架（4/4）**：uv 环境与依赖锁定（`uv.lock` + `uv sync --frozen`）、Pydantic v2 领域模型、EventLog 固定种子回放、GitHub Actions 质量门/许可证门禁（3.10/3.12 矩阵 + pip-licenses/pip-audit/SBOM）。
- **M2 单场景 MVP（5/5）**：`POST /api/v1/games` / `POST /api/v1/turns`（幂等键/CAS/错误码映射）、ToolRegistry 白名单与解析 Agent（Schema 校验/修复/确定性兜底）、RuleEngine + 模板叙事、SQLite 事件表/快照与重启恢复、首个 golden 回放（save-42 七回合）。
- **M3 多角色（5/5）**：角色私有感知切片与 `actor_scope` 可见性矩阵、`ProposalQueue` + 纯函数 `Resolver`、确定性 `CharacterAgent`、多角色 CAS 提交、审计只增表与查询端点、10 回合多角色 golden 回放。
- **M4 制造（4/4）**：配方契约（模板/实例分离）、确定性制造与自由制造校验链路、5 个制造 golden 回放与审计。
- **M5 API 调用治理（6/6）**：019 `LLMClient` 同步契约 + `LiteLLMClient` 治理（deadline/tenacity/RPM/幂等/日志/计量）+ 默认离线工厂；020 `llm/config/` JSON 分级 + 进程内 `TTLCache`；021 Schema 修复链终局（不迁 instructor）；022 并发同 `Idempotency-Key` 幂等；023 休眠状态机与批量合并；024 配置化回退链 + 进程内熔断。出口附加项（解析 free 分支/LLM 叙事/LLM 角色/裁判 Agent 接线）见 M5E-1/M5E-2；022 按用户决定缩范围（成本/预算/`llm_call_log` 不做，D-M5-10）。
- **OSS-1 可玩终端入口（不占 M1–M6 编号）**：`python -m textgame.console`（自然语言连续游玩 + `:help/:inv/:status/:quit`）、`python -m textgame.demo --check`（固定 seed 七屏自检）、`scripts/quickstart.ps1|sh`、`examples/m2-demo.md`、README「5 分钟上手」、只读 `GET /api/v1/games/{id}/view` 与背包意图。
- **CI-1（不占编号）**：修复公开仓库 CI 的 pytest-only 失败（`import litellm` 远端成本表下载被禁网守卫拦截）→ `LITELLM_LOCAL_MODEL_COST_MAP` 离线化 + pytest 失败摘要写入 Check 注解；本地 1179 passed、replay 61、远端 CI 全绿。
- **文档/仓库修正（D1 批）**：ADR `docs/decisions/2026-11-10-022-scope-and-cost-out.md`（D-M5-10）；README 数字/目录树/LLM 配置/联网提示/终端示例；本 CHANGELOG 与 `CONTRIBUTING.md`；quickstart 只装运行时依赖（`--no-dev`）；`.gitignore` 补 `*.db`。

### 变更

- **前端视觉主题（H4）**：统一 CRT + 余烬 + 霓虹描边视觉语言；粒子层懒加载（首屏 gzip 增量 ≈+8.9KB，
  预算 ≤100KB），新增**三档视觉设置**（全特效/低功耗/关闭，本机持久化）；系统 `prefers-reduced-motion`
  时粒子停播/打字机瞬显/过渡降级；不引入 three.js/TresJS（bundlephobia 实测 three@0.187.0 gzip 185.7KB 超预算）
  与任何动画库（本轮前端新增运行时依赖 0）。来源/许可证见 [docs/design/frontend-visual-refs.md](docs/design/frontend-visual-refs.md)
  （MIT 技法参考；SillyTavern UI 未参考）。
- **世界书只读注入（H4）**：激活条目为读时纯计算，不写 `GameState`、不新增事件；无世界书存档行为逐字不变；
  未支持字段保留 `raw_json` 并给 warning（不静默丢弃）。
- **CI Actions 升级（2026-11-11）**：GitHub Actions 改为**固定精确语义版本**并升级到 Node24 运行时——`actions/checkout@v7.0.1`、`astral-sh/setup-uv@v10.3.3`（自 v8 起上游不再发布 major/minor tag，必须精确固定）、`actions/upload-artifact@v7.0.2`、`pnpm/action-setup@v6.1.1`、`actions/setup-node@v7.1.0`；消除 runner 上「Node.js 20 is deprecated」警告；既有输入（`python-version`/`node-version`/`cache`/`version`/`name`/`path`/`if-no-files-found`）在新版本均保留，CI 门禁与缓存行为不变。
- **静态托管解析（R2）**：`create_app` 静态目录按 `TEXTGAME_STATIC_DIR` → CWD `web/dist` → 仓库根 `web/dist`（由 `__file__` 推导，不依赖 CWD）→ 包内 `textgame/web_dist` 解析；缺失时 WARNING；缺失的带扩展名路径（`/assets/x.js`、`/favicon.ico`）返回 404 JSON 而非 SPA HTML；`index.html` 响应 `Cache-Control: no-cache`、`/assets/*` 为 `immutable`。
- **终端入口（R2）**：`ensure_utf8_stdio` 同时修复 stdin（管道/重定向输入中文不再抛 `UnicodeEncodeError`）；`--db` 支持 `sqlite:///` DSN 自动转换，非 SQLite DSN/内存库给中文报错；显式 `--scenario` 与同库旧存档场景不一致时不再静默继续（提示 `--new`/`--continue`）。
- 022 缩范围：`022-cost-budget` → `022-concurrency-idempotency`（仅 D-M4-15/G-1 并发同 `Idempotency-Key` 幂等）；成本/预算/价格表/`llm_call_log` 表/Alembic 0003 不做，0003 顺延 M6 卡库（D-M5-10）。
- LLM 配置权威改为 JSON（`src/textgame/llm/config/llm.default.json` + `TEXTGAME_LLM_CONFIG` 覆盖）；历史 `TEXTGAME_LLM_PROVIDER/MODEL/BASE_URL` 仅兼容回退（D-M5-4）。

### 修复

- L1 失败结局不可达（H4 修复）：story 枯井「寒气」环境伤害（每次进入 -6 HP）使「倒下」结局可稳定复现；
  新增 `EventType.ENVIRONMENT_DAMAGE` + reducer（clamp 0..max、旧场景不产生 → 旧 golden 零改动）。
- L3 无事件回合来源徽章（H4）：`TurnResult.narrative_source`（`llm`/`template`/`null`）加法字段，前端徽章读该字段，
  不再依赖事件推断。
- L6 e2e 未进 CI（H4 修复）：真实浏览器 e2e（Node 零依赖 CDP）接入 CI `web-build` job（临时 uvicorn:8794），
  失败上传服务器日志/e2e 产物。
- WB-03b 卡库导入引导（H4 修复）：卡库导入页常驻提示 + `describeCardError` 422 命中 `character_book`/`spec_version`（角色卡 V2 包装）时明确提示「请到世界书 → 导入世界书」，不再只报「人物卡不合法」。
- e2e CI 启动鲁棒性（H4 修复）：Linux runner 的 Chrome 加 `--no-sandbox`、CDP 就绪等待可配（CI 90s）、`DBUS_SESSION_BUS_ADDRESS` 指向不存在 socket 避免探测重试、世界书详情页等待条件改为「标题+条目表可见」以消除 CI 慢环境竞态；run #50 全绿。
- D-M4-14：`Resolver.resolve` 用快照 `rng_state.save_seed` 构造 `ToolContext`/规则前置，修复非默认 seed 存档制造种子与实例 ID 派生不一致。
- D-M4-15（P2 已知限制）：并发同 `Idempotency-Key` 瞬时响应不一致——待 022-concurrency-idempotency 关闭。

### 质量基线（HEAD `cb7863b` 历史实测值，当前值以实际输出为准）

- 历史值：`uv run pytest -q` → **1179 passed**；`uv run pytest tests/replay -q` → **61 passed**（M2/M3/M4 = 4/5/24，零回归）；ruff/format/mypy/lint-imports 四门全绿；覆盖率 88%。后续切片持续新增测试，**不要**把历史数字当当前基线。
- H4 时点基线（2026-11-11，`a5ddb17` + H4 未提交改动）：全量 **2007 passed**、replay **68 passed**（旧 golden 61 零改动 + provenance 7）、web vitest **41 files/241 tests**、frontend_contract **19 passed**；`demo --check` 7/7、`console --check` 4/4 逐字不变。

### 已知限制

- 默认离线确定性叙事（模板）；真实 LLM 解析/叙事/角色已接线（M5E-1），裁判接线进行中（M5E-2），默认仍不启用；M6 前端 4/4 与后端前置已交付（M1–M6 全部完成）；成本观测按 D-M5-10 不实现；M6 出口 e2e 以零依赖 CDP 脚本落地、不接入 CI（D-M6-11）；生成卡限流为进程内滑窗（单机首版）。
