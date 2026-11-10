# 变更日志（CHANGELOG）

> 本文件登记**开发期**变更；版本号 `0.1.0` 对应 `pyproject.toml` 当前开发版号，项目尚未对外分发，无对外版本承诺。
> 格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；里程碑/切片编号见 `docs/PROGRESS.md` 与开发文档第 17 章。

## [0.1.0] - 开发版（未对外分发）

### 新增

- **M5E-2 裁判 Agent 接线（M5 出口附加项完成，2026-11-11）**：`domain/judge.py` 契约（`JudgeEvidence`/`JudgeOutput`、`verdict ∈ {allow, deny, modify}` + 只读 `allowed`、置信门槛 0.7、证据上限 5）+ `agents/judge_agent.py`（`JudgeAgentLike`/`DeterministicJudgeAgent`/`LLMJudgeAgent`：`task="judge"`、`prompt_version="judge@v1"`、STRONG、`max_repair=1`，输入=脱内部 ID 的私有感知切片+提案+规则空白说明，失败/异常→`None` 交确定性兜底）+ `needs_judge` 生产点（`ToolRegistry.agent_assisted_tools()`；生产 8 工具全 `DETERMINISTIC` → 当前 0 消费者=机制就绪）+ `RuleEngine.verify_agent_override`（严格类型/NaN 校验，非法一律否）/`deterministic_fallback`（恒保守否决、零事件）+ Resolver 懒读取分支（非 needs 零访问 judgements、applied reason 可审计）+ 编排层每回合≤1 次/CAS 重试复用证据；`main.py` 仅 `llm_enabled=true` 构造。**默认离线 `judge=None`、零模型调用、逐字等价**（旧 golden 与 demo/console `--check` 不变）；新增 64 例 + QA 独立红队 52 例 + 5 个手工合成 cassette（零网络）。详见 [M5E-2 进度文档](docs/progress/M5E-2-2026-11-11-judge-wiring.md) 与 [ADR D-M5-14](docs/decisions/2026-11-11-m5-judge-wiring.md)。
- **R3 玩家体验打磨批次（范围外，不占 M1–M6 编号，2026-11-11）**：① **结局体验**——结局屏通关总结「本局 N 回合 · 目标 X/Y · 生命值 HP/MAX」、结局文本进冒险记录（纯派生不重复）、终局回合叙事不再只有普通移动描述；② **新手引导**——新局冒险记录第 1 条「开场」（房间描述+目标进度，首回合后不重复）、冒险记录与回放页只读提示、诊断面板仅 dev 渲染、无事件回合来源徽章改中性「来源未知」；③ **文案去重**——细看（inspect）句级去重修「空气里浮着灰尘与旧木头的气味」连排、story 对话删固定框架重复句、up/down 移动改爬梯自然文案（四方向逐字不变）；④ **可执行建议**——缺材料/缺工具/工位不符时 hint 点名材料与所在房间、`suggested_actions[0]` 当前可执行一步（同屋「拿起X」/相邻「去{房间}拿X」实测解析为 `move_to` 并真的移动）；⑤ **入口与兼容**——console `--card-id/--card` 按卡开局 + `--list-cards`、旧格式存档（无 `scenario`/`quests`）读取/继续/发现兼容；⑥ **内部 ID 脱敏**——生成页保存区/卡库列表/卡详情/回放页不可见 `card_…`/`game_…`/未登记 actor ULID，`item.torch` 等模板键显示中文名。验证：全量 **1683 passed**（+QA 独立 12 = 1695 时点）、replay **68**（旧 golden 零改动）、web vitest **32 files/178 tests**、真实浏览器 e2e **18 步/101 断言**、demo 7 屏与 console `--check` 逐字不变。详见 [R3 进度文档](docs/progress/R3-2026-11-11-player-polish.md) 与 [ADR D-R3-1..5](docs/decisions/2026-11-11-m6-exit-and-r3-closure.md)。
- **M6-028 生成人物卡 UI（M6 4/4 完成，2026-11-11）**：新路由 `/cards/generate`（brief ≤1000 码点 → `POST /character-cards/generate` → 草稿预览/模板兜底徽章 → JSON 编辑+重新校验 → 保存到卡库 → 「查看卡片」/「按此卡开局」两个显式入口）；**显著离线提示**（默认离线生成即确定性模板兜底，`source=manual`/`generated_by=null`/`TEMPLATE_FALLBACK`，M6B-4 已知限制）；429 按 `Retry-After` 给中文等待提示且不自动重试；禁 `v-html`/自动定时重试。新增真实浏览器 e2e：`scripts/e2e-web.mjs`（Node 26 零依赖 CDP，14 步/62 断言 + 负向控制；**不接入 CI**，D-M6-11）。验证：web vitest 29 files/158 tests、契约测试 17 passed、全量 **1647 passed**、replay **68**（旧 golden 零改动）、玩家独立验收 PASS（P0 76/76，2 项展示层 P1 记入 R3）。详见 [028 进度文档](docs/progress/028-2026-11-11-llm-card-generate.md) 与 [ADR D-M6-11](docs/decisions/2026-11-11-m6-exit-and-r3-closure.md)。
- **R2 玩家体验批次（不占 M1–M6 编号，2026-11-11）**：**剧情场景 `story`「余烬哨站」**——6 房间/7 物品/1 NPC/3 步目标链（听老兵 → 造信号火把 → 带火石登塔）/胜利与倒下双结局；目标与结局读时纯求值（不新增 EventType、不改 GameState、不加迁移，旧 golden 61 零改动），结局后拒绝新回合；`/view` 加法 `scenario`/`goal`/`ending_text`/`suggested_actions`/`room.exit_names`，`GET /games/{id}` 加法 `scenario`/`ending_text`，新增只读分页端点 `GET /api/v1/games` 存档列表。**道具/对话真实化**：药水/烈酒按 `heal`/`hp_delta` 改 HP（满血零状态变化）、武器/工具不可被「使用」销毁、主线材料 quest 保护防软锁、新增只读 `inspect_item`（细看物品/NPC）、story 老兵确定性回话（首谈即 briefed）。**反馈/引导**：失败回合给具体中文原因（Resolver/服务层原因过滤后展示）+ 场景化建议，成功回合填 `suggested_actions`。**离线 NLU 增强**：inspect、背包/方向/日常说法同义词、实体别名、difflib 保守模糊、房间名导航（`PerceptionSlice.exit_refs`），88 条真实输入探测识别率 45%→85%，7 条 golden 输入映射逐字不变。**Web 对局回路重做**：replay 重建叙事历史、状态面板玩家化（隐藏内部 ID/qty/可点建议）、CONFLICT（HTTP 200 ok=false）自愈、成功刷新失败不重复执行、120s 超时、人话错误、服务端存档列表 + 最近对局一键续玩、目标面板/结局横幅、375px 窄屏与无障碍、路由懒加载（入口 js 139.7→101.4KB）。**Agent**：LLM 叙事事实供给（物品/NPC/房间/背包/目标入 prompt，内部 ULID 与原话不入 prompt）+ story cassette/wiring 测试（默认仍离线）。详见 [R2 进度文档](docs/progress/R2-2026-11-10-player-experience.md) 与 [ADR D-R2-1..12](docs/decisions/2026-11-10-player-experience.md)。
- **R2 开源入口轨（不占 M1–M6 编号，2026-11-10）**：**网页版一条命令**——`scripts/play.ps1|sh`（网页优先；`-Terminal`/`--terminal` 回终端、`-Check`/`--check` 启动自检 `/healthz`+`/`、`-Port`/`--port`、`-Llm`/`--llm` 真实 LLM 开关，uv 缺失/首次联网/端口占用/dist 缺失均给中文下一步）；**`web/dist` 预构建产物入库**（fresh clone 无需 Node/pnpm 即可玩网页版；`.gitattributes` 固定 `*.sh` LF、`*.ps1` CRLF、`web/dist` 不转换）；wheel 通过 hatch build hook 内嵌 `textgame/web_dist`（`pip install` 后同样有网页版；产物缺失时不阻断 editable 安装）；`quickstart.ps1|sh` 保留为终端别名；**终端支持 `--scenario story`**（首屏展示目标进度/子目标完成态与建议动作，结局时打印结局屏并停止空转）与 `--list`（列出最近存档）；`README.md` 重写（网页优先 + story 推荐首玩 + FAQ：PS5.1/中文路径/首次联网/存档位置/如何开 LLM/端口）、新增 `README.en.md`、`SECURITY.md`、Issue/PR 模板；`web/README.md` 端口口径对齐。
- **M1 骨架（4/4）**：uv 环境与依赖锁定（`uv.lock` + `uv sync --frozen`）、Pydantic v2 领域模型、EventLog 固定种子回放、GitHub Actions 质量门/许可证门禁（3.10/3.12 矩阵 + pip-licenses/pip-audit/SBOM）。
- **M2 单场景 MVP（5/5）**：`POST /api/v1/games` / `POST /api/v1/turns`（幂等键/CAS/错误码映射）、ToolRegistry 白名单与解析 Agent（Schema 校验/修复/确定性兜底）、RuleEngine + 模板叙事、SQLite 事件表/快照与重启恢复、首个 golden 回放（save-42 七回合）。
- **M3 多角色（5/5）**：角色私有感知切片与 `actor_scope` 可见性矩阵、`ProposalQueue` + 纯函数 `Resolver`、确定性 `CharacterAgent`、多角色 CAS 提交、审计只增表与查询端点、10 回合多角色 golden 回放。
- **M4 制造（4/4）**：配方契约（模板/实例分离）、确定性制造与自由制造校验链路、5 个制造 golden 回放与审计。
- **M5 进行中（2/6）**：019 `LLMClient` 同步契约 + `LiteLLMClient` 治理（deadline/tenacity/RPM/幂等/日志/计量）+ 默认离线工厂；020 `llm/config/` JSON 分级 + 进程内 `TTLCache`。022 按用户决定缩范围为并发幂等（成本/预算/`llm_call_log` 不做，D-M5-10）。
- **OSS-1 可玩终端入口（不占 M1–M6 编号）**：`python -m textgame.console`（自然语言连续游玩 + `:help/:inv/:status/:quit`）、`python -m textgame.demo --check`（固定 seed 七屏自检）、`scripts/quickstart.ps1|sh`、`examples/m2-demo.md`、README「5 分钟上手」、只读 `GET /api/v1/games/{id}/view` 与背包意图。
- **CI-1（不占编号）**：修复公开仓库 CI 的 pytest-only 失败（`import litellm` 远端成本表下载被禁网守卫拦截）→ `LITELLM_LOCAL_MODEL_COST_MAP` 离线化 + pytest 失败摘要写入 Check 注解；本地 1179 passed、replay 61、远端 CI 全绿。
- **文档/仓库修正（D1 批）**：ADR `docs/decisions/2026-11-10-022-scope-and-cost-out.md`（D-M5-10）；README 数字/目录树/LLM 配置/联网提示/终端示例；本 CHANGELOG 与 `CONTRIBUTING.md`；quickstart 只装运行时依赖（`--no-dev`）；`.gitignore` 补 `*.db`。

### 变更

- **静态托管解析（R2）**：`create_app` 静态目录按 `TEXTGAME_STATIC_DIR` → CWD `web/dist` → 仓库根 `web/dist`（由 `__file__` 推导，不依赖 CWD）→ 包内 `textgame/web_dist` 解析；缺失时 WARNING；缺失的带扩展名路径（`/assets/x.js`、`/favicon.ico`）返回 404 JSON 而非 SPA HTML；`index.html` 响应 `Cache-Control: no-cache`、`/assets/*` 为 `immutable`。
- **终端入口（R2）**：`ensure_utf8_stdio` 同时修复 stdin（管道/重定向输入中文不再抛 `UnicodeEncodeError`）；`--db` 支持 `sqlite:///` DSN 自动转换，非 SQLite DSN/内存库给中文报错；显式 `--scenario` 与同库旧存档场景不一致时不再静默继续（提示 `--new`/`--continue`）。
- 022 缩范围：`022-cost-budget` → `022-concurrency-idempotency`（仅 D-M4-15/G-1 并发同 `Idempotency-Key` 幂等）；成本/预算/价格表/`llm_call_log` 表/Alembic 0003 不做，0003 顺延 M6 卡库（D-M5-10）。
- LLM 配置权威改为 JSON（`src/textgame/llm/config/llm.default.json` + `TEXTGAME_LLM_CONFIG` 覆盖）；历史 `TEXTGAME_LLM_PROVIDER/MODEL/BASE_URL` 仅兼容回退（D-M5-4）。

### 修复

- D-M4-14：`Resolver.resolve` 用快照 `rng_state.save_seed` 构造 `ToolContext`/规则前置，修复非默认 seed 存档制造种子与实例 ID 派生不一致。
- D-M4-15（P2 已知限制）：并发同 `Idempotency-Key` 瞬时响应不一致——待 022-concurrency-idempotency 关闭。

### 质量基线（HEAD `cb7863b` 历史实测值，当前值以实际输出为准）

- 历史值：`uv run pytest -q` → **1179 passed**；`uv run pytest tests/replay -q` → **61 passed**（M2/M3/M4 = 4/5/24，零回归）；ruff/format/mypy/lint-imports 四门全绿；覆盖率 88%。后续切片持续新增测试，**不要**把历史数字当当前基线。

### 已知限制

- 默认离线确定性叙事（模板）；真实 LLM 解析/叙事/角色已接线（M5E-1），裁判接线进行中（M5E-2），默认仍不启用；M6 前端 4/4 与后端前置已交付（M1–M6 全部完成）；成本观测按 D-M5-10 不实现；M6 出口 e2e 以零依赖 CDP 脚本落地、不接入 CI（D-M6-11）；生成卡限流为进程内滑窗（单机首版）。
