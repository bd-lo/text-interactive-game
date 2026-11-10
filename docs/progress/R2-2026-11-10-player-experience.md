# R2 · 玩家体验批次（余烬哨站剧情目标结局 / Web 对局回路 / 一键网页入口 / 离线 NLU 与道具修复）

> 编号：`R2`（范围外玩家体验批次，不占 025–028 编号；按 `docs/progress/README.md` 例外条款登记）
> 日期：2026-11-10（Asia/Shanghai）｜基线：`33e620b` → 交付 commit `708c7ea`（=origin/main）
> 角色：Lead + 8 角色团队（player-rep、game-designer、backend-dev、frontend-dev、algorithm-dev、agent-dev、oss-user-rep、qa-tester）；决策见 [ADR D-R2-1..12](../decisions/2026-11-10-player-experience.md)。

## 1. 目标与判定口径

用户指令：**一切以用户的游戏体验为中心（方便用户游玩、游玩体验良好）**，识别并全力修复风险，包括底层。因此本批不看里程碑完成度，只看「一个真实玩家从拿到仓库到玩得下去、玩得明白、玩得有意义」：

1. 一条命令打开网页版（无需 Node/API Key），终端版保留；
2. 首玩即有目标、引导、历史、可点建议；8–14 回合走完主线并看到结局；
3. 自然中文能玩通「看/走/拿/用/说/造/细看」；用错道具不会被无声销毁、药水真的回血、NPC 真的说话；
4. 存档列表/最近对局一键续玩；手机窄屏可读可点；
5. 旧 golden 61 例与 demo 7 屏逐字不变，质量门全绿。

## 2. R1 审计暴露的 P0（修复前，八角色一致）

- 无目标/结局：m2 只有 2 房 4 物品，取完≈第 8 回合无事可做；`status` 硬编码 `active`。
- 失败反馈失去语义：全仓仅 2 种失败文案（61/16 字），`suggested_actions` 成功回合恒空，铁匠铺里建议「对老哨兵问好」。
- 道具是假的：火把/锻锤被「使用」永久销毁，治疗药水不回血；观察物品永远显示房间；NPC 回显玩家指令、永不出话。
- 离线 NLU 覆盖窄：88 条真实输入中可执行动作识别率 47.5%，e2e 32.5%。
- Web 只是调试控制台：只显示最后一屏、无历史、续玩要手输 37 位 game_id、状态面板暴露 ULID、多标签页 CONFLICT 永久卡死。
- 网页版不可发现不可用：README 无网页章节；`web/dist` 被 gitignore，fresh clone 起 uvicorn 后 `/` 是 JSON 404；Windows 默认 PowerShell 5.1 下 quickstart 因 UTF-8 无 BOM 直接解析失败。

## 3. 交付内容（D-R2-1..12）

| 方向 | 交付 | 关键位置 |
| --- | --- | --- |
| 新场景 | `scenario="story"`「余烬哨站」：6 房间/7 物品/1 NPC/3 步目标链/胜利+倒下双结局；读时纯求值（无新 EventType/无 GameState 字段/无迁移）；结局后拒绝新回合 | `domain/scenario_story.py`、`services/query_service.py`、`orchestration/turn_orchestrator.py` |
| 道具/对话 | 药水/烈酒 HP 生效（reducer、满血零状态变化）；武器/工具不可被 use 销毁；主线材料 `quest` 保护防软锁；`inspect_item` 细看；story 老兵确定性回话（首谈 briefed） | `domain/reducers.py`、`services/action_service.py`、`tools/**`、`orchestration/narrator.py` |
| 反馈/引导 | 失败回合具体中文原因（Resolver/ActionService 原因过滤后展示）+ 场景化建议；成功回合 `suggested_actions`；story 目标/结局回填 | `domain/suggestions.py`、`orchestration/turn_orchestrator.py` |
| 离线 NLU | inspect、背包/方向/日常说法同义词、实体别名、difflib 保守模糊、房间名导航（`PerceptionSlice.exit_refs`） | `orchestration/intent.py`、`domain/models.py` |
| API 加法 | `/view`：scenario/goal/ending_text/suggested_actions/room.exit_names；`GET /games/{id}`：scenario/ending_text；新增 `GET /api/v1/games` 存档列表（只读分页） | `api/schemas.py`、`api/routes/games.py`、`persistence/**` |
| Web | 叙事历史（replay 重建 + 失败系统行）、状态面板玩家化（隐藏 ULID/qty/可点 chips）、CONFLICT 200-ok=false 自愈、成功刷新失败不重复执行、120s 超时、人话错误、服务端存档列表+最近对局一键续玩、目标面板/结局横幅、窄屏/无障碍、路由懒加载 | `web/src/**`（25 test files / 117 tests） |
| 一键入口/分发 | `scripts/play.ps1|sh`（`-Terminal/-Check/-Port/-Llm`）、`web/dist` 入库、静态解析 env→CWD→仓库根→包内、wheel hatch hook 内嵌、quickstart UTF-8 BOM、README/FAQ/英文摘要/SECURITY/Issue 模板 | `scripts/**`、`web/dist/**`、`hatch_build.py`、`src/textgame/api/app.py`、`README.md` |
| Agent/LLM | 叙事事实供给（物品/房间/NPC 回话/背包 qty/目标入 prompt；内部 ULID 与玩家原话不入 prompt）、回退可感知单测、story cassette + LLM wiring 测试 | `agents/narrator_agent.py`、`tests/**` |

## 4. 验证证据（Lead 与 QA 独立实跑）

| 项 | 结果 |
| --- | --- |
| Python 全量 | **1642 passed**（HEAD `828d349`） |
| 回放 | **68 passed** = 旧 golden m1/m2/m3/m4 **61 例零改动** + golden provenance sha256 pin 7 例 |
| Web | `pnpm test` **25 files / 117 tests passed**；`typecheck` 0 错；`build` 74 modules / dist 14 文件 / 172,710B；入口 js 101.37KB（gzip 39.51KB，较 R1 -27%） |
| 契约 | `test_frontend_contract` **12 passed**（含 GET/POST `/games` 方法级断言、Goal/GameList 字段） |
| 质量门 | ruff format（192 files）/ ruff check / mypy（93 files）/ lint-imports（2 kept）/ check_docs 全绿 |
| 远端 CI | **run #38 @828d349 全绿**：quality 3.10/3.12 + web-build（含 dist drift 门）+ deps 四 job success（run #35 @708c7ea 曾因 golden provenance 跨平台行尾差异失败，已由 b1e1300 修复） |\n| 可玩烟测 | `demo --check` 7/7 屏逐字不变；`console --check` 4/4；`python -m textgame.console --scenario story` 14 回合通关 |
| story HTTP 主线 | 12–14 回合：目标 0/3→3/3、`status=ended`、`ending_text` 非空、结局后回合被拒且零写入；heal 12→20、inspect、软锁回归（用火把/铜线被拒）均通过 |
| 一键入口 | `play.ps1 -Check` 在 pwsh 与 Windows PowerShell 5.1 双通 exit 0（`/healthz`+`/` 200）；PS5.1 解析 0 error；无 dist 时自动降级终端 |
| 分发 | `uv build` wheel 369KB，含 `textgame/web_dist/index.html` + 13 assets；异 CWD 起服务仍托管网页；`/assets/nope.js` 404 JSON、`/api/v1/nope` 404 JSON |
| 移动端 | Edge CDP 375px：`scrollWidth == innerWidth`、0 溢出元素；可见文本无内部 ID |
| 性能 | 离线单回合 p50 16–31ms / p95 18–49ms；DB 增量 24–40KiB/10 回合；2 线程×20 同幂等键 events_delta=1 |
| 真实 LLM（可选） | 43 次真实调用：story 8 回合全 ok，事实正确、80–150 字稳定（仅 1 次修复）；首回合 7.8s、后续 2.3–5.8s；默认仍离线 |

## 5. 风险/遗留（已登记，非阻塞）

1. **失败结局不可达**：`ending_fallen` 逻辑与单测已实现，但当前唯一伤害源只有 1 瓶烈酒（-4），story 12 HP 最低 8；不做战斗/陷阱/死亡系统（范围外，见 ADR §已知限制）。
2. **m3 既有体验问题**：首屏「与此同时」句中硬切、拾荒者不可交互（模型重构会破 m3 golden text_len，明确不做）。
3. **LLM 延迟与无流式**：真实 LLM 首回合 7–8s、后续 2–6s；README 已写明预期，默认离线 10–22ms。
4. **无事件回合来源徽章**：看/背包回合 TurnResult 无事件，前端来源徽章显示「离线模板」（TurnResult 未加来源字段）。
5. **M6-028 生成卡 UI** 仍未做；终端无卡库。
6. `web/dist` 入库依赖 CI `web-build` 的 dist 漂移门禁（已加）保证产物与源码一致。
7. P2 文案打磨：细看模板偶有重复填充句、移动叙事方向词（「上边/东边」）略生硬、枯井可选支线可发现性一般。

## 6. 后续建议

1. 失败结局内容：陷阱/寒冷/战斗至少一种伤害源（可复用 reducer 的 `hp_delta`，不新增事件类型）；
2. 真实浏览器 E2E 进入 CI（Playwright 或复用系统 Edge CDP + 截图比对）；
3. 流式叙事（SSE/分块）+ LLM 首回合预热；
4. M6-028 生成卡 UI 与终端卡库；
5. m3 建模重构（角色/NPC 统一可见性）需一次性受控重基线；上一批如果做，走 QA-2 的受控重基线流程。
