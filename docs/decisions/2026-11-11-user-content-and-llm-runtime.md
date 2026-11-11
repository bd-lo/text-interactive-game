# ADR · 用户内容平台（场景 JSON 上传）与 LLM 运行期凭据（D-UC-1..7 / D-LLM-1..5）

- 状态：已定案 ｜ 决策人：lead ｜ 复核人：backend-scenario（场景后端）、frontend-scenario（前端）、worldbook-compat（世界书）｜ 日期：2026-11-11 ｜ 基线：`a2264de`
- 关联：需求「让玩家自己上传场景 JSON 开新局 / 酒馆级世界书 / 允许输入 DeepSeek API Key（本地开发默认用开发 key，git 版让玩家输入）」；
  进度文档 `docs/progress/UC-2026-11-11-user-content-and-llm.md`；设计契约 `docs/design/user-scenario-modules.md`；
  世界书兼容矩阵 `docs/design/world-book-format-compat.md`；PROGRESS §5 偏离登记行 `D-UC-*` / `D-LLM-*`。

## 1. 背景与红线

用户明确要求三件事，并给出优先级「内容 → 体验 → 功能 → 技术 → 文档」：

1. **剧情不固定**：玩家能**自己上传一份 JSON 场景**来玩新剧情（不是我们继续硬编码新场景）；
2. **酒馆式世界书**：像 SillyTavern 一样导入世界书并使用；
3. **API Key 输入**：网页里能填玩家自己的 DeepSeek API Key；**本地开发机默认用 `.env` 里的开发 key**，
   `git clone` 出来的干净环境没 key 时**由玩家在网页输入**，默认离线仍然可玩。

红线（沿用既有决策）：

- 旧 4 场景（m2/m3/m4/story）的 golden 回放、demo/console 逐字输出**零改动**；
- 不给 `GameState` 加字段（否则所有旧 `state_hash` 漂移）；不新增 `EventType`；
- 不执行任何用户提供的脚本/表达式（条件 DSL 白名单，禁 eval）；密钥不入库/不入 git/不进日志；
- 同一提交内回写对应文档（第 17/6/12/23 章 + PROGRESS/CHANGELOG/README）。

## 2. 决策

### D-UC-1 场景 JSON v1 格式与条件 DSL（详见设计文档 §2）

- **决策**：数据驱动场景 = 一份 JSON（`schema_version/slug/meta/player/rooms/items/npcs/goal/endings/suggestions`），
  字段级 `extra="forbid"`、跨引用校验、结构化 `errors/warnings`（path+message）；条件 DSL 只认白名单原子谓词
  （`in_room`/`has_item`/`has_item_tag`/`npc_state`/`hp_lte`/`hp_gte`）与组合子（`all/any/not/always`），**禁 eval**。
- **理由**：玩家可扩展内容又不引入执行面；校验错误可定位，导入体验与人物卡一致。
- **被否**：允许 JS/表达式脚本（安全不可控）；把 `goal` 写成代码钩子（玩家无法作者化）。
- **影响面/验收**：`domain/user_scenario.py`；30 个单测；示例 `examples/scenarios/*.json` 通过校验。

### D-UC-2 存储与迁移（scenarios 表 + Alembic 0005）

- **决策**：新表 `scenarios`（`scenario_id` PK、slug/title/author/description、`definition_json` 原文、
  summary/warnings、created_at/deleted_at 软删）；**不进事件溯源**；每次导入生成新 `scn_` ID（不覆盖），
  派生实体 ID 含 `scenario_id`，故同一 JSON 重复导入互不冲突。
- **理由**：与人物卡/世界书同构的「资料库」；软删保留定义供已开局存档回放。
- **影响面/验收**：Alembic head 0004→0005；legacy_upgrade 同步；迁移/旧库测试全绿。

### D-UC-3 建局契约（`scenario="custom"` + `scenario_id`）

- **决策**：`POST /api/v1/games` 加法 body `scenario_id`；服务先物化初始状态（`GameState.model_dump`）再交引擎，
  引擎 `scenario="custom"` 分支要求 `initial_state`；`game_created.params` **仅在该路径**追加 `scenario_id`
  且 `starting_room_id` 取初始状态玩家位置（旧场景仍写 m2 入口常量，事件逐字不变）。
- **互斥**：`scenario_id` 与 `card_id`/显式 `scenario` 同给 → 422；可与 `world_book_id`/`seed` 组合。
- **理由**：不把「读 JSON」塞进唯一写入者（引擎零 IO），又保证回放/哈希可复算（初始状态里 `save_seed` 与引擎一致）。

### D-UC-4 运行时钩子分发（ScenarioRuntime，`None` = 旧行为）

- **决策**：新增 `domain/scenario_runtime.py`：按房间/NPC 派生 ID 索引用户场景钩子；目标/结局/进入伤害/NPC 对话
  在 query/action/orchestrator 处「先问 runtime，未命中回退内置 story 逻辑」；**内置场景不注册**，
  因此 m2/m3/m4/story 与旧 golden/演示逐字不变。
- **被否**：给 `GameState` 加 `scenario` 字段（破坏旧 `state_hash`）；全局可变注册表（测试隔离差）。
- **影响面/验收**：`tests/integration/test_user_scenario_flow.py` 端到端（导入→开局→目标→伤害→结局→回放）。

### D-UC-5 `/view` 不加法 `scenario_id`；终端入口

- **决策**：`/view.scenario` 对用户场景返回 `"custom"`，**不新增字段**；前端用建局后 URL query
  （`/game/:id?scenario_id=scn_...`）取得标题，取不到回退「自定义场景」。终端新增
  `--scenario-id <ID>` 与 `--list-scenarios`（与 `--scenario/--card-id/--continue` 互斥，总是新开）。
- **理由**：避免契约测试/前端类型精确比对被加法字段牵连；query 方案对刷新/深链同样可用。

### D-UC-6 导入/校验端点口径

- **决策**：`POST /scenarios/validate` body = 场景 JSON 原文，**永远 200**（非法 JSON/超大也给结构化 errors，
  path=`"$"`）；`POST /scenarios` 同时接受 `application/json` 原文与 `multipart/form-data`（前端现状），
  需 `Idempotency-Key`，超 256KB → 413；`GET /scenarios`（q/limit≤100/offset）；`GET/DELETE /scenarios/{id}`
  （DELETE 不要求幂等键、重复 404、204 空 body）。
- **理由**：校验是「预览」不是写操作，200+errors 对前端渲染更友好；导入双格式兼容前后端现状。

### D-UC-7 首版边界

- 不做：模组编辑器/分享市场、自定义配方/制造链、战斗系统、GM 层/多人、表达式与自定义事件类型；
  v1 条件不含 flag/quest/时间条件（只读既有状态事实）。这些进 P4/后续里程碑。

### D-LLM-1 运行期凭据优先级（本地开发免输入 / 干净克隆引导输入）

- **决策**（`llm/runtime.py`）：`TEXTGAME_LLM_ENABLED=false` 显式关闭 → 永远离线；否则按
  **`.env`/环境变量 key（开发）→ 本机保存 key（玩家输入）→ 离线** 的优先级解析；**有 key 即自动启用**，
  不再要求显式 `TEXTGAME_LLM_ENABLED=true`（本地开发机开箱即用开发 key；干净 clone 未配置时离线可玩）。
- **理由**：正是用户要求「本地开发默认用开发时的 api，git 上的需要人输入」；默认离线语义由「无 key」保证。
- **影响面/验收**：`tests/unit/test_llm_runtime.py`（11 例）、`test_llm_settings_api.py`（7 例）。

### D-LLM-2 凭据存储

- **决策**：本机 `data/llm.credentials.json`（`data/` 已 gitignore；env `TEXTGAME_LLM_CREDENTIALS_FILE` 可覆盖）；
  schema 版本 + 原子写（临时文件 + `os.replace`）+ POSIX 0600 尽力；损坏/版本不符 → 视为未配置；
  **不写数据库、不写日志、状态接口只返回打码 key**（如 `sk-4****9c1d`）。
- **被否**：写 SQLite（增加密钥落库面与迁移成本）；localStorage（终端/多浏览器不一致且 XSS 面更大）。

### D-LLM-3 组合根：Agent 常驻 LLM 版 + 热切换客户端

- **决策**：`RuntimeLLMClient` 实现 `LLMClient` 协议，内部委托当前真实/离线客户端；保存 key 后 `refresh()`
  当回合生效（无需重启）。`main.py` 改为**总是**构造 LLM 版 Agent（叙事/角色/裁判/卡生成/制造），
  离线时由各 Agent 内置回退产出确定性文案（与旧离线装配逐字等价，测试钉住）。
- **理由**：干净克隆玩家在网页填 key 后应立刻生效，而不是要求重启服务。
- **被否**：保存 key 后提示「请重启」（体验差）；按 key 动态重建 orchestrator（改动面大）。
- **验收**：`test_llm_wiring.py`/`test_story_llm_wiring.py`/`test_judge_*` 更新断言（LLM Agent + 离线回退 +
  `llm_runtime.status().enabled is False`），demo/console `--check` 逐字不变。

### D-LLM-4 设置端点（无 Idempotency-Key，按值幂等）

- **决策**：`GET /api/v1/settings/llm`（9 字段状态，打码）、`PUT`（`api_key` 必填，provider/model/api_base
  可覆盖，`run_test` 可选）、`POST /api/v1/settings/llm/test`（最小请求测连通，离线零网络直接返回原因）、
  `DELETE`（清本机 key，回退 env/离线）。写端点**不要求 `Idempotency-Key`**：同值重复保存结果一致，
  与游戏回合的幂等语义无关。422 走统一失败信封。
- **理由**：设置页是玩家手动动作，不是会产生领域副作用的写操作；错误信息可读（不泄漏密钥）。

### D-LLM-5 确定性自检强制离线

- **决策**：`demo --check` 与 `console --check` 在装配前强制 `TEXTGAME_LLM_ENABLED=false`
  （即使开发者 `.env` 有 key），保证 7 屏/4 回合 golden 逐字可复现；CI 的浏览器 e2e 也已显式离线。
- **理由**：本轮改造后开发机默认启用 LLM，若自检不锁离线会读到真实模型导致 golden 失败（已实测修复）。

### D-WB-1..4 世界书酒馆级语义（用户需求「像酒馆一样导入世界书」）

- **D-WB-1 定时效果**：解析 `sticky/cooldown/delay`（0..100 回合，越界收敛+warning）；激活 = 关键词命中 +
  时间窗约束。定时状态需要**逐回合历史**：新增纯函数 `history_from_events(events)`（按 `turn_id` 升序、
  每回合取该回合事件 `result.narrative`、去空去重、最多最近 100 回合），`activate_entries(..., history=...)`
  逐回合模拟；`history=None` **逐字等价旧实现**（硬门禁）。`/view.lore` 与回合注入同一口径。
- **D-WB-2 正则键**：`key` 形如 `/pattern/flags` → Python `re.search`（仅 i/m/s；g/u/y 等 → warning + 按字面量）；
  非法 pattern 或 >200 字符 → warning + 字面量；匹配输入截断 2000 字符防灾难性回溯；默认自动 IGNORECASE。
  与 JS 正则的差异登记在兼容文档 §5 D-5。
- **D-WB-3 包含组**：`group/group_weight/group_override`；同组每回合至多激活 1 条，选择顺序
  `group_override` 优先 → `group_weight` 降序 → `order` 降序 → `uid` 升序；无 group 行为不变；
  `useGroupScoring` 仅解析存储并告警（不参与选择）。
- **D-WB-4 排序与存储**：激活结果先按 `(order, uid)` 预算截断，再按
  `(position 升序, depth 降序, order 升序, uid 升序)` 返回（LLM prompt 消息结构不变，避免破 cassette）；
  `world_book_entries` **不加列**，新字段经 `raw_json` 由 `parse_entry_extras` 还原（导入 warnings 形状不变）。

## 3. 契约 → 交付映射（防漏）

| 决策 | 代码 | 测试 | 文档 |
| --- | --- | --- | --- |
| D-UC-1/2 | `domain/user_scenario.py`、`persistence/scenario_repository.py`、`alembic/versions/0005_user_scenarios.py` | `test_user_scenario.py`(30)、`test_scenario_service.py`(21) | 设计文档 §2/§3、兼容矩阵 |
| D-UC-3/4 | `services/scenario_service.py`、`services/game_engine.py`、`orchestration/turn_orchestrator.py`、`services/query_service.py`、`services/action_service.py` | `test_scenario_api.py`(19)、`test_user_scenario_flow.py`(7) | 第 6/9 章回写、PROGRESS §5 |
| D-UC-5/6 | `api/routes/scenarios.py`、`console.py`、`api/routes/games.py` | 同上 + `test_console_*` 30 | 第 23 章、README |
| D-LLM-1/2/3 | `llm/runtime.py`、`llm/credentials.py`、`main.py` | `test_llm_runtime.py`(11)、`test_llm_credentials.py`(13) | 第 12 章、README/FAQ |
| D-LLM-4/5 | `api/routes/settings.py`、`demo.py`/`console.py` 自检 | `test_llm_settings_api.py`(7) | 第 12/23 章、README |
| D-WB-1..4 | `domain/world_book.py`、`services/world_book_service.py`、`orchestration/turn_orchestrator.py`、`services/query_service.py` | `test_world_book_tavern.py`(32)、`test_world_book_tavern_flow.py`(5)、旧世界书 138 例零回归 | 兼容文档 §5 D-5/D-8/D-9/D-10、第 6/12 章、README |

## 4. 已知问题归属

- ~~P2：定时效果/正则键/包含组/position-depth 排序（世界书）~~ → **本批已交付（D-WB-1..4）**；
  剩余：`vectorized`/`outlet`/`characterFilter`/`useGroupScoring` 仍不支持（导入 warning）；
  世界书详情 API/前端暂不展示 timed/group 新字段（激活已生效，展示可另开小任务）
- P2：场景条件 DSL 扩展（flag/quest/时间）、场景编辑器、场景分享 → 后续里程碑（需先补第 17 章计划表）
- P2：LLM 成本/预算/`llm_call_log` 按 D-M5-10 用户决定不做；真实 LLM 接入后的延迟体感仍待流式（R-T8/Q7）
- P3：密钥明文落本机文件（单机本地场景可接受，已在 README 安全说明与第 20 章 R-T13 登记）

## 5. 出口验收判据与到期承诺

- 全量 pytest **2155 passed / 0 failed**；replay **68** 不变；web **53 files/326 tests** 全绿；typecheck/build OK；
  tracked `web/dist` 零漂移；ruff/mypy/lint-imports/check_docs 全绿；Alembic head **0005**；
- 旧 4 场景 demo/console 逐字不变；浏览器 e2e **25/25 步、119/119 断言**（含场景库与设置页新步骤）；
- 干净克隆（无 `.env`）：未配置 → 首页横幅 → 设置页填入 key → 当回合生效；本地开发机（有 `.env`）零输入直接启用
  （`play.ps1 -Check` 与 dev 库实测 `source=env, enabled=True`）。

## 6. 后移与复审

- 世界书剩余缺口（vectorized/outlet/characterFilter/useGroupScoring）与详情展示：后续小任务；
- 流式叙事（SSE）、Playwright/a11y、终端卡库浏览仍后移（R3 §6、D-M6-11）；
- 场景 JSON 编辑器与分享市场：需先定第 17 章「M6 后计划表」，不在本批。

## 7. 变更纪律

- 本批结束时本 ADR 不再改写；后续修改 = 新增 ADR + `supersedes`；
- 任何新增端点/字段须同提交更新 `tests/integration/test_frontend_contract.py` 与对应章节；
- 旧 4 场景 golden / demo / console 逐字断言是硬门禁，破坏即回滚。
