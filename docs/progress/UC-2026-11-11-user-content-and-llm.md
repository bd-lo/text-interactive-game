# UC · 用户内容平台与运行期 API Key（范围外批次，不占 001–028 编号）

> 日期：2026-11-11 ｜ 里程碑：范围外「用户内容批次」｜ 状态：**已完成**（内容 → 体验 → 功能 → 技术 → 文档）
> 决策：`docs/decisions/2026-11-11-user-content-and-llm-runtime.md`（D-UC-1..7 / D-LLM-1..5 / D-WB-1..4）
> 设计契约：`docs/design/user-scenario-modules.md`（场景 JSON v1）、`docs/design/world-book-format-compat.md`（世界书）

## 1. 目标与判定口径

用户需求（2026-11-11）：

1. 剧情不要再固定：**玩家自己上传场景 JSON 就能开新局**（不是我们继续硬编码场景）；
2. **像酒馆一样导入世界书**；
3. **允许用户输入 DeepSeek API Key**：本地开发默认用 `.env` 的开发 key，`git` 上的干净克隆由玩家在网页输入；
   默认离线仍可玩。执行优先级由用户指定：内容 → 体验 → 功能 → 技术 → 文档。

判定口径：

- **零回归硬门禁**：旧 4 场景（m2/m3/m4/story）golden 回放 68 例零改动、`demo --check` 7 屏与
  `console --check` 4 轮逐字不变；全量 pytest 0 failed；既有世界书/人物卡测试零回归；
- **内容优先**：玩家在网页/终端完成「上传场景 JSON → 开局 → 目标/结局 → 回放」全链路；
- **确定性**：用户场景实体 ID 由 `sha256(scenario_id|kind|key)` 派生；世界书定时效果按事件历史重建；
  不新增 `EventType`、不给 `GameState` 加字段；
- **默认离线可玩**：未配置任何 API Key 时离线模板叙事，零联网。

## 2. 交付内容

### 2.1 内容：用户上传场景 JSON（Scenario JSON v1）

- 域层 `domain/user_scenario.py`：Pydantic v2 定义（`extra="forbid"`）、条件 DSL（白名单原子谓词 + `all/any/not/always`，
  禁 eval）、JSON 解析/结构化 errors(path+message)/warnings/summary、确定性 ULID 派生（玩家固定 `PLAYER_ID`）、
  `build_initial_state`、`UserScenarioHooks`（目标/结局/进入伤害/NPC 对话，含兜底结局）。
- 运行时 `domain/scenario_runtime.py`：`room_id/npc_id → 场景钩子`索引与 `resolve(state)`；**内置场景不注册**，
  未命中时各层回退既有逻辑（旧行为逐字不变）。
- 后端：`scenarios` 表 + **Alembic 0005** + `SqliteScenarioRepository` + `ScenarioService` + 5 端点
  （validate/import/list/detail/delete）+ `POST /games {scenario_id}`（`scenario="custom"`，事件记 `scenario_id`）。
- 钩子接入：query（场景名/目标/结局）、action（NPC 对话）、orchestrator（进入伤害、结局后拒绝新回合、世界书 history）。
- 示例：`examples/scenarios/tiny-tower.json`（2 房间最小）、`ember-watch.json`（5 房间/守夜人对话/枯井 -6 HP/双结局）。

### 2.2 体验：场景库 UI + 终端

- 网页 `/scenarios` 列表（分页/搜索/统计/导入时间）、`/scenarios/import`（拖放 JSON → 校验预览 errors/warnings/summary →
  确认导入）、`/scenarios/:id`（meta/summary/房间·物品·NPC·目标·结局摘要 + 原始 JSON 折叠 + 按此场景开局 + 软删确认）；
- Home「场景」下拉（内置 4 场景 + 已上传）；对局页场景徽章（URL query 带 `scenario_id`，`/view` 不加字段）；
- 终端 `--list-scenarios` 与 `--scenario-id <ID>`（与 `--scenario/--card-id/--continue` 互斥，总是新开）。

### 2.3 功能：DeepSeek API Key 网页输入 + 本地开发免输入

- `llm/credentials.py`：本机 `data/llm.credentials.json`（gitignore；env 可覆盖路径；原子写 + 0600 尽力；损坏降级未配置）；
- `llm/runtime.py`：`RuntimeLLMClient` 可热切换客户端；优先级「显式关闭 → `.env` 开发 key → 本机保存 key → 离线」；
- `main.py`：Agent 常驻 LLM 版本 + 运行期客户端（保存 key 后**当回合生效**，无需重启）；离线自动回退确定性路径；
- 端点：`GET/PUT/DELETE /api/v1/settings/llm`、`POST /api/v1/settings/llm/test`（状态 9 字段、打码 key、无幂等键）；
- 前端 `/settings`（状态卡三来源/保存并启用/测试连接/清除二次确认/安全提示）+ Home 未配置横幅（可关闭）。

### 2.4 世界书：酒馆级兼容升级（D-WB-1..4）

- **定时效果**：`sticky/cooldown/delay`（0..100 回合）按事件历史逐回合模拟（`history_from_events`，
  `/view.lore` 与回合注入同口径）；`history=None` 逐字等价旧实现。
- **正则键**：`/pattern/flags`（Python `re.search`，仅 i/m/s；非法/超长降级字面量 + warning；输入截断 2000 字符）。
- **包含组**：`group/group_weight/group_override`——同组每回合至多 1 条（override 优先 → weight 降序 → order 降序 → uid）。
- **position/depth 生效**：最终 lore 块按 `(position 升序, depth 降序, order 升序, uid 升序)` 排序；LLM prompt 消息结构不变。
- 存储无新列：新字段经 `raw_json` 还原；导入 warnings 形状不变（新增诊断码，见兼容文档 §7）。

## 3. 验证结果（Lead 实跑）

| 项 | 结果 |
| --- | --- |
| 全量 pytest | **2155 passed / 0 failed**（基线 2007 → 2118 → 2155；新增场景 30+21+19+7、LLM 11+13+7、世界书 37） |
| 回放 | `tests/replay` **68 passed**（旧 golden 61 + provenance 7 零改动） |
| web | `pnpm --dir web test` **53 files / 326 tests**；`typecheck` 0；`build` 成功；`web/dist` 重建 |
| 浏览器 e2e | **25/25 步、119/119 断言**（新增场景库导入→按场景开局→目标面板、设置页状态/表单 3 步；23 张截图） |
| 终端/演示确定性 | `console --check` 4/4 逐字；`demo --check` 7/7 逐字（两者在装配前强制离线，防开发 key 干扰） |
| 质量门 | ruff format/check、mypy strict（107 files）、lint-imports（2 kept）、check_docs **PASS** |
| 数据库 | Alembic head **0005**（scenarios 表）；legacy_upgrade 0004→0005 同步；迁移/旧库测试全绿 |
| play 自检 | `scripts/play.ps1 -Check` → `/healthz=200`、`/=200`（网页版可玩） |

## 4. 改动与提交

- `1b2cb8c` feat(scenario,llm)：场景域层/后端场景库/迁移 0005/组合根接入/终端 + LLM 运行期凭据/设置端点（后端 40 文件）
- `a2264de` feat(web)：场景库 UI + 设置页 + Home 场景下拉/未配置横幅 + dist 重建 + Python 契约测试同步（81 文件）
- 世界书酒馆级升级与演示/e2e/文档：见随后提交（`feat(worldbook)` / `docs(uc)`）。
- 新增依赖：**零**（场景/凭据/世界书解析全部标准库 + 既有依赖）。

## 5. 已知限制/后续

1. 场景 JSON v1 不支持：自定义配方/制造链、战斗系统、脚本表达式、flag/quest/时间条件、模组编辑器与分享市场；
2. 世界书仍不支持：`vectorized` 向量检索、`outlet` 宏插入、`characterFilter`、`useGroupScoring` 评分（导入给 warning）；
3. 世界书 `GET /world-books/{id}` 详情 schema/前端暂不展示 timed/group 新字段（激活已生效；展示需另开小任务）；
4. 玩家 API Key 存本机明文文件（单机单用户可接受；不要用共享账号 key、不要监听公网，见第 20 章 R-T13/Q6）；
5. 真实 LLM 首回合延迟（无流式）仍在（R-T8）；流式/SSE 按 Q7-B 发布期复评；
6. 场景库/设置页未接 Playwright/a11y（沿用 D-M6-11 口径）；e2e 已覆盖新页面主链路。

## 6. 复跑命令

```powershell
uv run pytest -q                                            # 2155 passed
uv run pytest tests/replay -q                               # 68 passed
uv run python -m textgame.demo --check                      # 7/7 逐字
uv run python -m textgame.console --check                   # 4/4 逐字
uv run python scripts/check_docs.py                         # PASS
uv run ruff format --check ; uv run ruff check ; uv run mypy ; uv run lint-imports
pnpm --dir web test ; pnpm --dir web typecheck ; pnpm --dir web build
# 端到端（先起离线服务）
$env:TEXTGAME_LLM_ENABLED="false"; uv run --no-dev uvicorn textgame.main:app --port 8795
node scripts/e2e-web.mjs --base-url http://127.0.0.1:8795 --out artifacts/e2e-web
```
