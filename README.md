# 文字交互游戏 · Text Interactive Game

[![ci](https://github.com/bd-lo/text-interactive-game/actions/workflows/ci.yml/badge.svg)](https://github.com/bd-lo/text-interactive-game/actions/workflows/ci.yml)

复古 2G 文字 MUD 风格的单机 / 异步文本游戏。玩家用自然语言输入行动，LLM Agent 负责理解意图与叙事包装，规则引擎负责一切确定性计算与状态提交。

**默认离线可玩**：不需要 API Key、不需要 Node/pnpm、不需要联网（仅首次安装依赖需联网）。

> 当前状态：M1–M5 已完成，M6 前端体验 3/4；**R2 玩家体验批次已完成**（剧情场景「余烬哨站」目标/结局、Web 对局回路重做、一键网页入口、道具/对话/离线 NLU 修复）。网页版与终端版均为「一条命令」入口；里程碑与逐切片证据见 [docs/PROGRESS.md](docs/PROGRESS.md)，玩家体验批次见 [R2](docs/progress/R2-2026-11-10-player-experience.md)。

![网页版首页：存档续玩 + 场景选择](docs/assets/web-home.png)

| 对局：目标面板 / 冒险记录 / 状态 / 可点建议 | 剧情结局屏 |
| --- | --- |
| ![网页版对局](docs/assets/web-game.png) | ![剧情结局](docs/assets/web-ending.png) |

> 手机 375px 单列布局：![手机布局](docs/assets/web-mobile.png)

## 快速开始（一条命令）

| 前置 | 什么时候需要 |
| --- | --- |
| [uv](https://github.com/astral-sh/uv) | **必需**（Python 3.12 由 uv 自动下载管理） |
| Node 24/26 + pnpm 12.6 | 只有「修改前端源码/重新构建网页产物」时需要；玩游戏不需要 |
| DeepSeek API Key | 只有「开启真实 LLM 叙事」时需要；默认离线不需要 |

### 网页版（推荐）

仓库**已包含预构建的网页产物 `web/dist`**，克隆后无需 Node/pnpm：

```powershell
git clone https://github.com/bd-lo/text-interactive-game.git
cd text-interactive-game
.\scripts\play.ps1
# → 浏览器打开 http://127.0.0.1:8000（按 Ctrl+C 停止）
```

```bash
git clone https://github.com/bd-lo/text-interactive-game.git
cd text-interactive-game
./scripts/play.sh
# → http://127.0.0.1:8000
```

- `-Port 8001` / `--port 8001`：换端口；`-Llm` / `--llm`：启用真实 LLM 叙事（需 Key，见 FAQ）。
- 自检（启动服务 → 校验 `/healthz` 与 `/` 均 200 → 退出）：`.\scripts\play.ps1 -Check`（bash：`./scripts/play.sh --check`）。
- 首页可直接选场景；**推荐首玩「余烬哨站」剧情（story，有目标与结局）**，其它为 m2 单场景 / m3 多角色 / m4 制造。
- 想改前端：`pnpm --dir web install --frozen-lockfile && pnpm --dir web build` 后重新运行 `play` 脚本（详见 [web/README.md](web/README.md)）。

### 终端版

```powershell
.\scripts\play.ps1 -Terminal     # 或旧的 .\scripts\quickstart.ps1
```

```bash
./scripts/play.sh --terminal     # 或旧的 ./scripts/quickstart.sh
```

进入终端后直接输入自然语言（如 `环顾四周`、`拿起火把`、`向北走进地窖`），每回合 80–150 字叙事 + 一行状态（HP/位置/行囊）；`:help` 看命令，`:inv` 看行囊，`:status` 看状态，`:quit` 退出。存档默认写入 `data/textgame.db`（已 gitignore）。

**推荐从剧情开局**（有目标/结局，终端同样支持）：

```bash
uv run --no-dev python -m textgame.console --new --scenario story   # 「余烬哨站」剧情
uv run --no-dev python -m textgame.console --list                   # 列出最近存档
```

也可以用底层命令直接玩：

```bash
uv sync --frozen --no-dev                      # 仅运行时依赖（首次联网）
uv run --no-dev python -m textgame.console     # 终端版
uv run --no-dev uvicorn textgame.main:app --port 8000   # 网页版（仓库根执行）
```

想先看固定 seed 的 7 屏演示而不进入交互：`uv run python -m textgame.demo`（实际输出见 [examples/m2-demo.md](examples/m2-demo.md)）；自动验收：`uv run python -m textgame.demo --check` 与 `uv run python -m textgame.console --check`。

进入交互后的第一屏（seed=42，逐字可复现；完整 7 屏见 [examples/m2-demo.md](examples/m2-demo.md)）：

```text
废弃哨站·入口。废弃哨站的入口塌了一半，风从断裂的门楣间穿过，卷起细碎的石屑。墙角堆着受潮的木箱，地上散落着几件被遗弃的杂物。地上有火把、治疗药水。这里站着老哨兵。可以往北走。你把眼前的景象在心里过了一遍，留意着可能变化的角落。
【状态】HP 20/20 ｜ 位置 废弃哨站·入口 ｜ 行囊：空
```

## 常见问题（FAQ）

- **PowerShell 报「无法加载文件 … 未对文件进行数字签名」/ 执行策略**：用 `powershell -ExecutionPolicy Bypass -File .\scripts\play.ps1` 或 `pwsh -File .\scripts\play.ps1`。仓库脚本已按 **UTF-8 with BOM** 保存，Windows PowerShell 5.1 与 PowerShell 7 都能正确解析中文（历史版本在 5.1 下会 parse error，已修复）。
- **Windows + 中文仓库路径**：支持（本机开发路径即 `D:\ProgramData\游戏开发`）。历史注意点：Python 3.10 下 editable 安装的 `.pth` 会被 GBK `site.py` 读取失败；本地验 3.10 用 `uv sync --frozen --python 3.10 --no-editable` + `PYTHONPATH=src`（CI 的 ubuntu/UTF-8 不受影响）。`pre-commit install` 生成的 hook 在 Git Bash 下可能找不到 `pre-commit`，用 `uv run pre-commit run --all-files` 手动跑。
- **第一次运行为什么慢**：`uv sync` 会下载 CPython 3.12 与 `uv.lock` 锁定依赖（运行时约 98 个包、数百 MB 磁盘）；已同步过的环境之后秒级启动。只玩终端/网页用 `--no-dev` 更快；跑测试/质量门才需要 dev 依赖。
- **存档在哪里**：默认 `data/textgame.db`（已 gitignore，不会误提交）；终端版默认自动继续同库最近存档，`--new` 强制新开、`--continue` 强制继续；`--db <路径>` 或 `--db sqlite:///…` 可指定别处。
- **怎么开真实 LLM 叙事（可选）**：把 [.env.example](.env.example) 复制为 `.env`，填 `TEXTGAME_LLM_API_KEY`，并设 `TEXTGAME_LLM_ENABLED=true`；或直接 `.\scripts\play.ps1 -Llm`。连通性自检：`uv run python -m textgame.llm.smoke`（缺 Key 退出码 2，API 失败退出码 1）。**默认离线**，不配置就一直可玩且零联网。
- **端口被占用 / 网页打不开**：`play` 脚本会提示换端口（`-Port 8001` / `--port 8001`）。若 `/` 返回 JSON 404，通常是 `web/dist` 缺失（源码裁剪或未构建）：启动日志会有 WARNING，按 README「网页版」里的构建命令重建即可；终端版始终可用。
- **数据与隐私**：默认离线路径不联网；`.env`、`data/`、`*.db` 均被 gitignore，不提交密钥与存档。

## 玩法与场景

- 自然语言输入 + 少量本地命令（`:help/:look/:inv/:status/:quit`）；回合失败会给出可重试提示与建议动作。
- 场景选择：**`story`（余烬哨站剧情，有目标与结局，推荐首玩）** / `m2`（单场景，默认，保持旧契约）/ `m3`（多角色：两名拾荒者同回合抢同一瓶药水）/ `m4`（制造：配方与自由制造校验）。
  ```bash
  uv run --no-dev python -m textgame.console --new --scenario story
  ```
- 剧情场景首屏会显示目标与子目标完成态（如「【目标】让信号灯亮起来 · 0/3」）；走到结局时打印结局屏，输入 `:quit` 退出。终端 `--list` 可查看同库最近存档（含场景/回合/是否已结局）。
- HTTP API 也可以直接玩/集成：`POST /api/v1/games`（写操作必带 `Idempotency-Key`）→ `POST /api/v1/turns` → `GET /api/v1/games` / `{id}` / `/view` / `/replay`；交互式文档在服务启动后的 `/api/docs`。

<details>
<summary>HTTP 示例（创建存档 → 提交回合 → 刷新重连）</summary>

```bash
# 1) 创建存档（写操作必带 Idempotency-Key；可选 seed / scenario）
curl --noproxy '*' -X POST http://127.0.0.1:8000/api/v1/games \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: demo-create-0001' -d '{"seed":42}'
# → 201 {"game_id":"game_...","turn":1,"version":1,"snapshot_version":1,"save_seed":"save-42"}

# 2) 提交回合（body = {game_id, input, expected_version}；expected_version 用上次返回的 version）
curl --noproxy '*' -X POST http://127.0.0.1:8000/api/v1/turns \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: demo-turn-0001' \
  -d '{"game_id":"game_...","input":"环顾四周","expected_version":1}'
# → 200 TurnResult {ok, turn_id, version, text(80–150 字), events, error_code}

# 3) 刷新/重连
curl --noproxy '*' http://127.0.0.1:8000/api/v1/games/game_...
```

业务失败（无法理解/版本过期/幂等键冲突）返回 HTTP 200 + `TurnResult(ok=false, error_code)`；协议错误（缺 `Idempotency-Key`、body 非法）返回 4xx 统一失败信封 `{ok:false, error_code, message, violations?}`。

</details>

## 核心设计

- **拼接优先于自建**：优先复用 GitHub 上成熟的 Python 开源项目，自建部分必须有明确理由，并记录评估与取舍过程。
- **确定性计算 + 智能体生成**：数值、伤害、掉落、制造等全部在框架内确定性计算；智能体只负责理解意图、选择工具、填写参数、叙事包装。
- **OOP 领域层 + 工具白名单 + 唯一写入者 + 事件溯源**：智能体只是提议者，通过白名单工具方法操作，不直接改状态、不持有对象引用；所有写操作进入 ProposalQueue，由 Resolver 统一裁决、GameEngine 原子提交。
- **结构性防泄漏**：角色 Agent 只接收该角色的私有感知切片与私有记忆，不接收全局状态。
- **可回放**：每个写操作产生 Event（回合、调用者、方法、参数、结果、随机种子），支持固定随机种子回放与审计。
- **防幻觉 / 防越权 / 防作弊**：所有 LLM 结构化输出先过 JSON Schema 与状态一致性校验，校验失败绝不写入状态。
- **分发即体验**：网页构建产物随仓库入库（fresh clone 一条命令可玩），并随 wheel 内嵌（`pip install` 后同样有网页版）。

## 技术栈

| 层 | 选型 |
| --- | --- |
| 语言 | Python 3.10+（运行时 3.12，源码保持 3.10 语法兼容） |
| 环境 / 依赖锁定 | uv 0.13.x：`uv.lock` + `uv sync --frozen` |
| 领域模型 / 校验 | Pydantic v2 |
| 持久化 | SQLAlchemy 2.0.x（锁 `<2.1`）+ SQLite + Alembic |
| 接入层 | FastAPI |
| 编排 | 自建显式 loop（LangGraph 仅保留可选封装） |
| 模型调用 | 统一 LLMClient 封装（DeepSeek `deepseek-flash`，默认离线） |
| Web 前端 | Vue 3 + TypeScript + Vite + Pinia + Vue Router（构建期 Node，产物由 FastAPI 托管） |
| 质量门 / CI | GitHub Actions：ruff + mypy + import-linter + pytest（3.10/3.12 矩阵 + 覆盖率/文档门）+ pip-licenses/pip-audit/CycloneDX + web-build |

## 开发模式

- **vibe coding**：AI 主导编码，人类负责拆任务、给上下文、跑验收与 review；以 30–90 分钟的垂直切片迭代，每个切片必须有可运行 demo + 自动验证（lint/类型/单测/必要回放）。
- **护栏不放松**：唯一写入者 `GameEngine`、事件溯源、确定性计算与 Schema 校验、工具白名单/actor_scope、密钥与许可证决策必须人工确认。
- 详见开发文档第 17 章（开发计划）与第 22 章（轻量进度制度）。

## 本地开发环境

前置：安装 [uv](https://github.com/astral-sh/uv)；Python 3.12 由 uv 自动下载管理。本仓库**不使用**全局 Python/Anaconda 装依赖，所有命令在仓库根目录执行：

```bash
uv sync --frozen                                          # 按 uv.lock 安装 runtime + dev 依赖（首次会拉取 Python 3.12）
uv run --no-dev uvicorn textgame.main:app --reload --port 8000   # 启动服务（默认 SQLite: data/textgame.db，已 gitignore）
curl --noproxy '*' http://127.0.0.1:8000/healthz          # → 200 {"status":"ok"}
uv run ruff format --check . && uv run ruff check .        # lint / format
uv run mypy src/textgame                                   # 类型检查（strict）
uv run lint-imports                                        # 分层依赖契约
uv run pytest -q                                           # 单元 + 集成 + 回放测试（数字以实际输出为准）
uv run pre-commit install --install-hooks                  # 一次性：安装提交前质量门
```

前端（只在改 Web 时需要）：见 [web/README.md](web/README.md)。**注意**：`pnpm --dir web dev` 的 `/api` 代理目标以 `web/vite.config.ts` 为准（当前 8765；建议改为 8000 与 `play` 脚本默认端口统一，改后需重新 `pnpm --dir web build` 并提交 `web/dist`）。

环境变量见 [.env.example](.env.example)（前缀 `TEXTGAME_`）；`.env` 不入库，禁止提交真实密钥。静态目录可用 `TEXTGAME_STATIC_DIR` 覆盖（默认自动解析 `web/dist` / 包内 `textgame/web_dist`）。

## 质量门与 CI

本地门禁（与 CI 同命令，见 [.github/workflows/ci.yml](.github/workflows/ci.yml)）：

```bash
uv run ruff format --check . && uv run ruff check .   # lint / format
uv run mypy src/textgame                              # strict 类型检查（3.10 语法）
uv run lint-imports                                   # 分层依赖契约（domain 零框架依赖）
uv run pytest -q                                      # 单元 + 集成 + 回放
uv run pre-commit run --all-files                     # 9 个 hook（含 secret-scan、EOL 规范）

# 依赖门禁
mkdir -p artifacts
uv export --frozen --no-hashes --format requirements-txt > artifacts/requirements.lock.txt
uv run pip-licenses --format=json --with-urls > artifacts/licenses.json
uv run python scripts/check_licenses.py artifacts/licenses.json   # GPL/AGPL/UNKNOWN → 阻断
uv run pip-audit -r artifacts/requirements.lock.txt                # 已知漏洞
```

CI job：`quality`（Python 3.10/3.12 矩阵：ruff → mypy → lint-imports → pytest + 覆盖率门 + 文档一致性门）、`deps`（pip-licenses/许可证门禁/pip-audit/SBOM artifact）、`web-build`（lockfile 安装 → typecheck → build → vitest；产物一致性由集成提交保证）。护栏：GPL/AGPL 出现即阻断；项目自身 MIT。

**AI 上下文与网络护栏（Q9/Q10）**：仓库根 [.aiignore](.aiignore) 声明禁止进入 AI 上下文的路径（`.env*`、`.work/`、`artifacts/`、`data/`、`*.db`、`.venv/` 等），secret-scan pre-commit hook 兜底；测试套件默认禁网（`tests/conftest.py` 的 socket guard 只放行本机回环），真实 LLM 必须 FakeLLM/cassette；CI 不注入任何密钥。

## 目录结构

```text
.
├── .github/workflows/ci.yml   # CI：quality（3.10/3.12 矩阵）+ deps（许可证/pip-audit/SBOM）+ web-build
├── alembic.ini、alembic/       # Alembic 迁移：0001 baseline、0002 复合主键、0003 人物卡库
├── docs/                       # 开发文档（23 章）、PROGRESS.md、decisions/（ADR）、progress/
├── examples/m2-demo.md         # seed=42 七回合终端演示（由 demo --markdown 实跑生成）
├── src/textgame/               # 包根：main.py（组合根/uvicorn 入口）、console.py（终端入口）、demo.py
│   ├── api/                    # /healthz、/api/v1/games（+scenario/view/replay/audit）、/api/v1/turns、/character-cards、静态托管
│   ├── orchestration/          # TurnOrchestrator、ProposalQueue、Resolver、关键词解析、模板叙事
│   ├── agents/                 # IntentParseAgent、DeterministicCharacterAgent、CraftAgent、Narrator/Character Agent
│   ├── tools/                  # @tool JSON Schema、ToolRegistry 白名单/actor_scope、ToolGate
│   ├── services/               # GameEngine（唯一写入者）、Query/Action/Audit/Card Service、TurnResult
│   ├── persistence/            # SQLAlchemy SQLite：events/snapshots/current/idempotency/audit_events/character_cards
│   ├── domain/                 # 领域层（零框架依赖）：types/results/events/models/reducers/scenario/rules/perception
│   ├── llm/                    # LLMClient 接口与治理、JSON 分级配置、Schema 修复、litellm 唯一 import 点
│   └── config/                 # pydantic-settings 配置与种子派生
├── tests/                      # unit/、integration/、replay/、security/、fixtures/golden/
├── web/                        # Vue 3 SPA 源码；web/dist 为已入库的预构建产物（运行期零 Node）
├── scripts/                    # play.ps1/.sh（一键入口）、quickstart.ps1/.sh（终端别名）、check_*.py（门禁）
├── pyproject.toml              # 三层依赖分组 + hatch 打包（wheel 内嵌 web/dist）+ ruff/mypy/pytest/import-linter
├── uv.lock                     # 依赖锁定（验收一律 uv sync --frozen）
├── .env.example                # 环境变量样例（复制为 .env；.env 已被忽略、不入库）
├── .gitattributes              # 行尾规范（*.sh LF、*.ps1 CRLF、web/dist 不转换）
├── CHANGELOG.md、CONTRIBUTING.md、SECURITY.md、README.en.md
└── README.md
```

## 文档

- [《文字交互游戏》开发文档 v0.3](docs/开发文档.md)：23 章，覆盖产品愿景、MVP 范围、总体架构、领域模型、服务层 API、工具注册、智能体设计、单回合调用链、判定机制、多角色并发、API 调用治理、制造系统、状态管理与事件溯源、安全防护、vibe coding 开发计划、测试策略、依赖与许可证、风险与开放问题、轻量进度制度，以及 Vue 3 前端设计。
- [按章拆分版](docs/开发文档/) ｜ [开发进度总览](docs/PROGRESS.md) 与 [单次进度文档](docs/progress/) ｜ [决策记录（ADR-lite）](docs/decisions/)
- [web/README.md](web/README.md)：网页版开发/构建说明。

## 贡献与许可证

- [CONTRIBUTING.md](CONTRIBUTING.md)：环境搭建、四门质量门、golden 回放纪律与密钥/数据约束；[SECURITY.md](SECURITY.md)：安全问题的私下报告方式。
- [CHANGELOG.md](CHANGELOG.md)：开发期变更记录（当前 `0.1.0` 开发版，尚未对外分发、无版本承诺）。
- 本项目采用 **MIT License**（见 [LICENSE](LICENSE)）：`pyproject.toml` 以 SPDX 表达式 `license = "MIT"` 声明，构建元数据同时携带 LICENSE 文件。CI 许可证门禁要求依赖侧 GPL/AGPL/LGPL/未知许可证 0 阻断。
