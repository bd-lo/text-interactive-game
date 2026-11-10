# H4 · 世界书（酒馆格式）+ 前端炫酷化 + 失败结局内容批次

> 编号：`H4`（范围外玩家体验批次，不占 001–028；按 `docs/progress/README.md` 例外条款登记）
> 日期：2026-11-11（Asia/Shanghai）｜基线：`a5ddb17`（=origin/main）｜交付 commit：`9869169`（世界书/后端/L1/L3/测试）+ `51ce23e`（前端炫酷化/世界书 UI/dist/截图）+ 热修 `546a577`/`5443b59`（卡库导入 V2 引导接线、e2e CI 启动加固），文档随其后
> 角色：Lead + 8 角色团队（player-rep、game-designer、backend-dev、frontend-dev、algorithm-dev、agent-dev、oss-user-rep、qa-tester）；决策见 [ADR D-H4-1..6](../decisions/2026-11-11-h4-worldbook-and-visual.md)。

## 1. 目标与判定口径

用户指令（2026-11-11 00:0x）：① 前置：另一支团队已开发收官，需要继续检查问题、修遗留风险；② **前端炫酷化**——**不参考 node.js 酒馆（SillyTavern）的 UI（太丑）**，改为参考**炫技类项目的 Vue3 实现并缝合**；③ **世界书导入 + 酒馆式对话 + 跑团式实现**（参考酒馆/跑团的**功能与格式**，UI 不参考酒馆），先评估开发量，量大先写文档；④ 旧 golden 61 零改动、质量门全绿、远端 CI 绿。

判定口径：玩家可见的「更像游戏、更炫但不累眼」+「能把酒馆格式世界书导入并影响对局」+「失败结局可达成」+ 全部旧基线零回归。

## 2. 交付内容

| 方向 | 交付 | 关键位置 |
| --- | --- | --- |
| 前端炫酷化（零新增运行时依赖） | Canvas2D 余烬/星尘粒子（懒加载 chunk 1.49KB gzip、30fps、visibilitychange 暂停）＋ 纯 CSS CRT 扫描线/暗角/微噪点/电子束 ＋ 叙事打字机（可跳过、sr-only 全文）＋ 三档视觉设置（全特效/低功耗/关闭，localStorage + `prefers-reduced-motion` 实时降级）＋ reveal/面板/页面/结局/目标/霓虹动效 | `web/src/components/{EmberField,CrtOverlay,TypewriterText,VisualSettings,WorldLorePanel,WorldBookEntryTable}.vue`、`web/src/stores/visual.ts`、`styles/terminal.css` |
| 世界书·数据与 API | `world_books` / `world_book_entries` 两表 + Alembic **0004**（0003→0004 可逆；legacy_upgrade 同步 head）；`GET/POST /api/v1/world-books*` 5 端点（导入 multipart ≤256KB/校验/列表/详情/软删，写幂等、409/413/422/404） | `persistence/{tables,world_book_repository}.py`、`services/world_book_service.py`、`api/routes/world_books.py`、`api/schemas.py`、`alembic/versions/0004_world_books.py` |
| 世界书·格式兼容 | SillyTavern World Info（entries map/array/裸 map）+ Character Card V2 `data.character_book` + 本项目简化 schema；`key/keysecondary` 逗号串规范化、`selectiveLogic` 0-3、`insertion_order`/`position` 映射、uid 生成/查重、非法值报错 | `domain/world_book.py`（parse_import_payload）、`docs/design/world-book-format-compat.md` |
| 世界书·触发与注入 | 确定性激活引擎：constant、主关键词（大小写/整词）、secondary 四模式、`scan_depth`、sha256 确定性概率、递归≤2 轮（exclude/prevent）、order 排序、char_budget 800、最多 8 条；回合把激活条目经 `result_data["lore_entries"]` 传给 narrators；`TemplateNarrator` look/inspect 追加 1 条完整设定句（80–150 字、句末不截断）；`LLMNarratorAgent` narrate@v3 prompt 增「世界设定事实」段；`/view.world_book` 摘要 + `lore.active_entries` | `domain/world_book.py`、`orchestration/{turn_orchestrator,narrator}.py`、`services/query_service.py`、`agents/narrator_agent.py` |
| 世界书·UI | `/world-books` 列表/搜索/分页/空态、`/world-books/import`（文件/拖拽/粘贴、≤256KB 预检、服务端校验预览 + errors/warnings、multipart+幂等、成功跳详情）、`/world-books/:id`（条目表：触发词/关联词/order/常驻/停用/条件触发、二次确认软删、用本书开局）、Home 世界书下拉、GameView「世界设定」侧栏（active entries） | `web/src/views/WorldBook*.vue`、`web/src/stores/worldBooks.ts`、`web/src/api/{client,types}.ts`、`HomeView.vue`、`GameView.vue` |
| 示例内容 | `examples/world-books/story-lorebook.json`（29 条 tavern，34KB）、`story-character-card.json`（卡 V2 + character_book 4 条）、`README.md` | `examples/world-books/` |
| 跑团式设计（只文档） | `docs/design/trpg-world-module.md`：Foundry VTT/Roll20/MapTool/Owlbear 对标、三层信息模型（public/clue/gm）+ 揭示状态机、模组 JSON 字段（places/actors/handouts/clues/checks/rewards/endings）、与 RuleEngine/Resolver/PerceptionSlice/story/世界书映射、分期 P0 2–3 人日 / P1 4–6 人日、风险表 | `docs/design/trpg-world-module.md` |
| L1 失败结局内容 | 新 `EventType.ENVIRONMENT_DAMAGE` + reducer（hp_delta clamp，旧场景零事件）+ story 枯井 -6/次；两次进井 HP→0 触发 `ending_fallen`；narrator S1/S2/S3 三档寒气文案（不拼移动模板）；药水可救回 | `domain/{events,reducers,scenario_story}.py`、`orchestration/{turn_orchestrator,narrator}.py`、`docs/design/story-hazard-copy.md` |
| L3 来源修正 | `TurnResult.narrative_source`（llm/template/null）：零事件只读回合为 null（徽章「来源未知」）、有事件回合 template/llm；前端徽章改读该字段 | `services/dto.py`、`orchestration/turn_orchestrator.py`、`web/src/stores/game.ts` |
| 开源调研/许可证 | `docs/design/frontend-visual-refs.md`（13 条来源，MIT/AGPL 甄别；结论零新增运行时依赖）、`world-book-format-compat.md`（257 行兼容矩阵/语义差异/示例 payload） | `docs/design/`、ADR |
| 验收工具 | `scripts/e2e-web.mjs` 扩到 **22 步/112 断言/19 截图**（世界书 4 步 + 全页面/375px/来源徽章）；新增世界书/激活/L1 QA 测试 **47 例**；`.github/workflows/ci.yml` web-build 增加浏览器 e2e（临时 uvicorn + headless Chrome + 失败上传 artifacts） | `scripts/e2e-web.mjs`、`tests/**`、`ci.yml` |

## 3. 验证结果（Lead/QA/玩家实跑）

| 检查 | 结果 |
| --- | --- |
| 全量 pytest | **2007 passed**（HEAD 基线 1811 + H4 新增约 196；0 failed） |
| 回放/golden | **68 passed**（旧 golden 61 零改动 + provenance 7）；`tests/fixtures/golden` 零改动，未 snapshot-update |
| demo/console 逐字 | `demo --check` 7/7 屏逐字（114/83/81/85/82/84/81）；`console --check` 4/4 |
| 质量门 | ruff format 225 files / ruff check / mypy 99 files / lint-imports 2 kept / `check_docs` PASS |
| 前端 | `pnpm test` **41 files / 241 tests**；`typecheck` 0；`build` 105 modules / dist 24 文件 / 249,370B；frontend_contract **19 passed**；dist 零漂移 |
| 炫酷性能 | 首屏 gzip 51.7KB（增量≈+8.9KB）；CDP 1280 对局页 **60.3fps / longtask 0 / console 异常 0**；375px 60.4fps、`scrollWidth==innerWidth==375` |
| 世界书链路 | 导入 `examples/world-books/story-lorebook.json`（tavern，29 条）→ 201 → story 建局绑定 → `/view.world_book` 摘要 + `lore.active_entries` 8 条 → 环顾 142 字（完整句 + 设定句，无句中截断）→ `GET /games/{id}` 200；旧 m2 存档两字段 null、环顾 114 字无设定句 |
| L1 | story 进枯井 12→6→0 → `status=ended` + 倒下结局文本；旧 m2 永不产生 `ENVIRONMENT_DAMAGE`；药水可救回 6→14 |
| L3 | 只读回合 `events=[]`、`narrative_source=None`（徽章来源未知）；写回合 `template` |
| 浏览器 e2e | **22/22 步、112/112 断言、19 截图 PASS**（含世界书导入/绑定/375px/删除、全页面、来源徽章、结局总结） |
| 玩家独立验收 | **PASS（P0 43/43；HEAD `5443b59` clean 树）**：炫酷 13/13、世界书 15/16→16/16（WB-03b 热修后）、L1 7/7、回归 7/7；首屏 gzip 增量 +6.2KB、对比度 12.72:1、0 JS 错误 |
| 远端 CI | run **#50 @`5443b59` 全绿**（quality 3.10/3.12 + web-build（含浏览器 e2e 步骤）+ deps 四 job success） |

## 4. 决策/文档

- ADR：[D-H4-1..6](../decisions/2026-11-11-h4-worldbook-and-visual.md)（格式范围、确定性触发、存储/0004 迁移、UI=炫技缝合不参考酒馆、依赖/许可证、跑团仅文档；状态已定案）。
- 设计文档：`docs/design/{frontend-visual-refs,world-book-format-compat,trpg-world-module,story-hazard-copy}.md`。
- 契约：`.work/team-player-2026-11-10/H4-CONTRACT.md`（`.work` 不入库）。

## 5. 已知限制/后续

1. **跑团式世界模组本轮只设计不实现**（P0 2–3 人日 / P1 4–6 人日，见 trpg-world-module.md）。
2. 世界书字段子集：`group` 仅存不参与分组评分；timed effects（sticky/cooldown/delay）/regex keys/vectorized/characterFilter 不支持（矩阵见 compat 文档）；`order` 方向/概率确定性为本项目口径（语义差异 D-1..D-7 已记录）。
3. 软删世界书后，已绑定存档的 lore 读路径返回 None（不破坏存档，但不再注入）。
4. LLM 角色/裁判 Agent 世界书注入与真 token 流式仅写评估（`h4-agent-lore-streaming-eval.md`），本轮不实现。
5. m3 首屏硬切/拾荒者建模仍未处理（需受控重基线，另开）。
6. `web/dist` 入库 + CI dist drift gate 继续生效；e2e 已在 CI web-build 中运行。

## 6. 复跑命令

```powershell
uv run pytest -q                                          # 2007 passed
uv run pytest tests/replay -q                             # 68 passed
uv run python -m textgame.demo --check                    # 7/7 屏逐字
uv run python -m textgame.console --check                 # 4/4
pnpm --dir web test && pnpm --dir web typecheck && pnpm --dir web build
uv run pytest tests/integration/test_frontend_contract.py -q   # 19 passed
# 浏览器 e2e（先起临时服务）
uv run --no-dev uvicorn textgame.main:app --port 8794 &
node scripts/e2e-web.mjs --base-url http://127.0.0.1:8794 --out artifacts/e2e-web
```
