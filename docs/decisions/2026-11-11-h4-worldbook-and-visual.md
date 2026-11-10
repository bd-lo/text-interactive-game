# ADR · H4：世界书 MVP 与前端炫酷化（D-H4-1..6）

- 状态：**已定案**｜决策人：lead｜复核人（8 角色 + 产出路径）：
  - backend-dev：世界书后端（`src/textgame/persistence/**`、`services/world_book_service.py`、`api/routes/world_books.py`、Alembic `0004`）
  - algorithm-dev：触发引擎（`src/textgame/domain/world_book.py` + 激活矩阵测试 + 编排层激活调用）
  - frontend-dev：视觉与页面（`web/src/components/{EmberField,CrtOverlay,TypewriterText,VisualSettings}.vue`、`stores/visual.ts`、世界书页面、`web/dist`、截图）
  - agent-dev：LLM 叙事世界设定注入（`agents/narrator_agent.py` + story cassette）
  - game-designer：示例世界书（`examples/world-books/**`：29 条 tavern + 角色卡内嵌 4 条）、`docs/design/trpg-world-module.md`、`docs/design/story-hazard-copy.md`
  - qa-tester：世界书/伤害/激活测试 47 例、`scripts/e2e-web.mjs`（22 步/111 断言/19 截图）、CI e2e job
  - player-rep：H4 最终独立玩家验收（`.work/team-player-2026-11-10/` 验收报告）
  - oss-user-rep：本 ADR、`docs/design/frontend-visual-refs.md`、`docs/design/world-book-format-compat.md`、README/CHANGELOG/CONTRIBUTING
- 日期：2026-11-11｜基线：`a5ddb17`（H4 实现前）
- 关联：里程碑/切片：H4 世界书 MVP + 前端炫酷化 + L1/L3/L6（不占 M1–M6 编号）；关联编号：D-H4-1..6；
  进度文档 `docs/progress/H4-2026-11-11-worldbook-visual.md`（lead 回填）；`docs/PROGRESS.md` §5（lead 回填）
- 关联文档：`H4-BRIEF.md`（§2A/§2B）、`H4-CONTRACT.md`（冻结契约）、`docs/design/frontend-visual-refs.md`、
  `docs/design/world-book-format-compat.md`、`docs/design/trpg-world-module.md`；第 6/8/9/12/16/17/18/20/23 章
- 只增不改；推翻须新增 ADR（`supersedes: D-H4-x`）。

## 1. 背景与红线

- 用户 H4 要求：① 前端**炫酷化**，明确「不要参考 node.js 酒馆（SillyTavern）UI，太丑」，改为参考**炫技类项目的
  Vue3 实现**并缝合；② 世界书/酒馆式对话/跑团式实现参考其**功能与格式**（UI 完全不参考），工作量大则先写文档。
- 红线（已全部满足）：旧 golden 61 + provenance 7 零改动；`demo --check` 7 屏、`console --check` 逐字不变；
  默认离线路径零模型调用；新增运行时依赖为宽松许可且**前端实际新增 0**；375px 不溢出、对比度不降、
  首屏 gzip 增量 ≤100KB（实测 ≈+8.9KB）；`prefers-reduced-motion` 降级、可关特效；
  `security-scan`（禁 `v-html`/`innerHTML`/`eval`/`EventSource`/`setInterval`）不破。
- 本轮范围：世界书最小切片（导入→绑定开局→每回合确定性激活→叙事注入→`/view` 只读展示）；
  跑团式世界模组只写文档；LLM 角色世界书注入与流式只写评估。

## 2. 决策

### D-H4-1 世界书格式范围（导入兼容）
- 接受三种包装：**SillyTavern World Info**（`{"entries": map|array}` 或裸 map）、**Character Card V2**
  `data.character_book`（及顶层 `character_book`；导入来源标记 `character_card`）、**本项目简化 schema**。
- 实现（最终）：`parse_import_payload` 按上述顺序检测；导入 ≤256KB、JSON-only；`validate` 只读预检；
  列表分页/搜索、详情、软删；`POST /api/v1/world-books/{import,validate}`、`GET /api/v1/world-books`、
  `GET/DELETE /api/v1/world-books/{world_book_id}` 共 **5 端点**（`GET /games/{id}` 可选加法 `world_book_id`）。
- 字段子集与兼容矩阵以 `docs/design/world-book-format-compat.md` 为准：实现契约 §3 的
  `keys/keysecondary/selective/selectiveLogic(0-3)/constant/order/position/depth/probability/useProbability/
  scanDepth/caseSensitive/matchWholeWords/excludeRecursion/preventRecursion/enabled/content/uid`；
  其余字段（timed effects、inclusion group 语义、vectorized、regex keys、outlet、自动化、额外匹配来源等）
  **本轮不实现**，一律保留 `raw_json` 并在 `validate` 给 warning，**不静默丢弃**。
- 明确 **不支持 JS 正则 key 与任何可执行语义**（确定性/安全/可回放优先）。
- 理由：格式兼容层只借鉴**数据结构与触发语义**（SillyTavern AGPL-3.0 代码零复制；CCv2 规范仓库无 LICENSE →
  只按公开字段语义独立实现）；UI 不参考酒馆（用户否决）。被否：A 直接内嵌酒馆前端（UI 被否 + AGPL 污染）；
  B 只支持自家 schema（放弃生态导入价值）。

### D-H4-2 触发语义（确定性版本，已实现）
- 纯函数 `domain/world_book.py`：`constant` 恒激活；主词大小写不敏感子串匹配（`match_whole_words` 仅对 ASCII
  词加边界，中文按子串）；`selective` + `selectiveLogic`（0 AND_ANY / 1 NOT_ALL / 2 NOT_ANY / 3 AND_ALL，与 ST 同值）；
  `order` 升序 + uid 升序；`scanDepth` 取最近 N 条上下文（缺省 4）；递归 ≤2 轮（`exclude/prevent` 遵守）；
  预算 `chars/2.5` 近似 token、默认 800 chars、最多 8 条；`useProbability` 用
  `sha256(save_seed:turn_id:uid:context_hash)` 的**确定性伪随机**，可回放。
- 与 ST 语义差异登记为 D-1..D-7（见兼容文档 §5，含 `order` 方向、概率确定性、scanDepth 缺省、regex key、
  预算口径等），不阻塞 MVP；后续要对齐须单独 ADR。
- 旧场景/无世界书存档：**不计算、不注入、文本逐字不变**（`/view.world_book` 与 `lore` 为 null）。

### D-H4-3 存储与迁移（已实现）
- 新增 `world_books` / `world_book_entries` 两表 + **Alembic 0004**（`0004_world_books.py`，head 0003→0004，
  单写者 backend-dev，`test_alembic_migration` pin 已同步）；软删 `deleted_at`；`raw_json` 保留原始条目；
  `entry_uid` 来自格式 uid（缺失按数组下标），`UniqueConstraint(world_book_id, entry_uid)`。
- 绑定：`POST /api/v1/games` 加法 `world_book_id?`（未知/已删除 404；与 `card_id` 可共存）；**仅当传入时**才在
  `game_created.params` 增 `world_book_id`/`world_book_name`（旧请求 params 逐字不变）。
- 回合内激活结果**不写 GameState、不新增事件**：只读计算（`TurnResult`/`/view` 加法字段承载，
  `lore.active_entries`）；世界书不参与事件溯源/回放（MVP 口径）。
- 理由：加法 + 零旧行为变更；避免 golden 与快照结构变化。被否：A 把激活条目写事件（改 golden 面）；
  B 世界书并入人物卡表（软删/分页/来源语义混淆）。

### D-H4-4 UI 方向：炫技缝合，**不参考**酒馆（已实现）
- 视觉参考 `docs/design/frontend-visual-refs.md` 的 MIT 来源（retrozone CRT 参数、Codrops glitch 技法、
  tsParticles/auto-animate/anime.js/vue-kinesis/@vueuse/motion 思路），**零新增运行时依赖**：
  自写 Canvas2D 余烬粒子（`EmberField.vue`，懒加载 1.49KB gzip）+ 纯 CSS CRT（`CrtOverlay.vue`）+
  打字机（`TypewriterText.vue`）+ `TransitionGroup`/`IntersectionObserver` 动效 + **三档视觉设置**
  （全特效/低功耗/关闭，`VisualSettings.vue` + `stores/visual.ts`，localStorage 持久化 + 系统 reduced-motion 自动降级）。
- 硬约束实测：首屏 gzip 51.7KB（增量 ≈+8.9KB）；CDP 60fps/long task 0/375px 无溢出；对比度不降；信息架构不变。
- 不引入 three.js/TresJS（bundlephobia 实测 three@0.187.0 **gzip 185.7KB**，超首屏预算）。
- **UI 完全不参考 SillyTavern**（用户两次明确否决）。被否：A 引入 three.js 全屏 3D 背景（超预算/低端机风险）；
  B 引入 @vueuse/motion/anime.js 等动画库（收益低于自写 CSS，增加依赖与许可证面）。

### D-H4-5 依赖与许可证策略
- 允许：MIT、Apache-2.0、BSD-2/3、ISC、CC0；新增前端依赖须过 `pnpm-lock` + 许可证门禁 + 懒加载/包体证据。
- 禁止：GPL/AGPL/LGPL、无 LICENSE、CC-BY-NC(-SA)、从闭源/Awwwards 站点取代码或素材。
  SillyTavern（AGPL-3.0）只读格式语义、代码零复制；CCv2 规范仓库无 LICENSE → 只按公开字段语义独立实现。
- **本轮实际新增运行时依赖：0**（前端零依赖、后端沿用现有依赖）。借鉴记录与署名要求见
  `frontend-visual-refs.md` §6 NOTICE 段（当前无内联第三方代码，仅技法参考）。

### D-H4-6 跑团式世界模组：本轮只写文档
- 参考 Foundry VTT（Journal/Compendium/Handout/Actors/Scenes）、Roll20 Handout、MapTool、Owlbear Rodeo 的
  信息分层（玩家可见 vs GM 隐藏）与线索逐步揭示；设计「世界模组 JSON」字段与现有 `RuleEngine/Resolver/
  场景 story` 的映射、P0/P1/P2 分期与工作量评估，写入 `docs/design/trpg-world-module.md`（game-designer）。
- 本轮**不实现**跑团 GM 隐藏信息/线索系统，避免半成品；世界书 MVP 是其前置数据层。

## 3. 实现落地证据（验收口径）

| 项 | 结果 |
| --- | --- |
| 测试 | 全量 pytest **2007 passed**（0 failed）；replay **68 passed**（旧 golden 61 零改动 + provenance 7）；世界书/伤害/激活 QA **47 例**；web vitest **41 files / 241 tests**；frontend_contract **19 passed** |
| 质量门 | ruff/format/mypy/lint-imports/check_docs 全绿；`demo --check` 7/7 逐字；`console --check` 4/4 |
| 世界书 | 5 端点 + `POST /games world_book_id` + `/view.world_book|lore` + Alembic 0004；示例 29 条 tavern + 角色卡内嵌 4 条（`examples/world-books/`） |
| 炫酷化 | 零新增运行时依赖；粒子懒加载 1.49KB gzip；首屏 gzip 51.7KB（≈+8.9KB）；CDP 60fps/longtask 0/375px 无溢出；三档视觉设置 |
| L1 | story 枯井「寒气」每次进入 -6 HP，两次进井 HP→0 触发「倒下」结局；新 EventType `ENVIRONMENT_DAMAGE`；旧场景零影响 |
| L3 | `TurnResult.narrative_source`（llm/template/null）加法字段落地 |
| L6 | `scripts/e2e-web.mjs` 22 步/111 断言/19 截图（世界书 4 步）；CI `web-build` job 增临时 uvicorn:8794 + headless Chrome e2e 步骤（失败上传日志） |

## 4. 影响面

- 后端：2 表 + 0004 迁移 + 世界书 API 5 端点 + `POST /games` 加法 + `/view`/`GET /games/{id}` 加法；
  旧请求/旧场景/旧 golden 零影响。
- 算法：`domain/world_book.py` 纯函数 + 编排层每回合激活（`repo.read_first_event` 端口加法）；
  `TemplateNarrator` 仅 world-book 存档追加设定句（旧场景逐字不变）。
- Agent：`LLMNarratorAgent` prompt 增「世界设定事实」段（默认离线不触发）；LLM 角色注入留文档。
- 前端：炫酷化主题 + `/world-books` 列表/导入/详情 + Home 世界书下拉 + GameView「世界设定」面板 +
  来源徽章读 `narrative_source`；`web/dist` 重建入库（drift 门禁）；`docs/assets/` 7 张截图。
- 测试：世界书 API/激活矩阵/环境伤害测试 + e2e 进 CI；旧 golden 61 + replay 68 零改动。
- 文档：本 ADR + 视觉参考 + 格式兼容 + TRPG 文档 + 进度文档；README/CHANGELOG/CONTRIBUTING 已更新。

## 5. 回写落点

- 第 6 章（world_books API 契约/`world_book_id` 加法）、第 8/9 章（激活与注入链路、`narrative_source`）、
  第 12 章（LLM prompt 世界设定段）、第 16/18/20 章（目录/测试/风险）、第 23 章（视觉与页面）；
  `docs/PROGRESS.md` §3/§5/§7；`docs/progress/H4-2026-11-11-worldbook-visual.md`；`CHANGELOG.md`；
  `README.md`（世界书功能与截图）；`CONTRIBUTING.md`（纪律）；`web/README.md`（视觉与构建）。
- `docs/decisions/README.md` §6 索引行由 lead 回填（本文件为正文）。

## 6. 已知限制与后续

1. ST 语义差异（`order` 方向/概率确定性/scanDepth 缺省/regex key/预算口径）见兼容文档 §5；导入 ST 世界书
   不报错但激活集合可能不同；严格对齐需单独 ADR。
2. 世界书版本未进事件溯源（MVP 可接受）；如需回放「世界书内容版本」须新增 ADR。
3. 未实现字段：inclusion group 语义、timed effects、vectorized、characterFilter/triggers、additional matching
   sources、outlet/自动化——保留 `raw_json` + warning。
4. LLM 角色 Agent 的世界书注入与对话流式（SSE）仅评估，不在本轮。
5. 跑团 GM 隐藏信息/线索系统仅文档（`docs/design/trpg-world-module.md`）；实现前需先定「GM 视图」权限边界。

## 7. 变更纪律

- 只增不改；推翻须新增 ADR（`supersedes: D-H4-x`）→ 回写章节 → 重跑全量 + replay + demo/console + e2e →
  更新 PROGRESS §5。任何会改变默认离线行为或旧 golden 的改动必须单独 ADR + 影响评估。
