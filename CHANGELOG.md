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
