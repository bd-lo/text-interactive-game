# 文字交互游戏 · Text Interactive Game

复古 2G 文字 MUD 风格的单机 / 异步文本游戏。玩家用自然语言输入行动，LLM Agent 负责理解意图与叙事包装，规则引擎负责一切确定性计算与状态提交。

> 当前阶段：开发文档 v0.3 已定稿；**M1 骨架（4/4）、M2 单场景 MVP（5/5）与 M3 多角色（5/5）均已完成并通过出口独立验收** —— 环境与依赖锁定、领域模型、EventLog 固定种子回放、CI 质量门/许可证门禁、`POST /api/v1/games` + `POST /api/v1/turns` 回合 API（幂等键/CAS/错误码映射）、ToolRegistry 白名单与解析 Agent（Schema 校验/修复/兜底）、RuleEngine + 模板叙事、SQLite 事件表/快照与重启恢复、首个 golden 回放与注入 0 写入用例；M3 交付角色私有感知切片、`actor_scope` 可见性矩阵与越权拒绝安全用例、`ProposalQueue` + 纯函数 `Resolver`、确定性 `CharacterAgent`、多角色 CAS 提交、审计只增表/查询端点与 10 回合多角色 golden 回放；M4 制造进行中（015-recipe-model 已交付 1/4，下一步 016-deterministic-craft）。

## 开发模式（v0.3）

- **vibe coding**：AI 主导编码，人类负责拆任务、给上下文、跑验收与 review；以 30–90 分钟的垂直切片迭代，每个切片必须有可运行 demo + 自动验证（lint/类型/单测/必要回放）。
- **护栏不放松**：唯一写入者 `GameEngine`、事件溯源、确定性计算与 Schema 校验、工具白名单/actor_scope、密钥与许可证决策必须人工确认。
- 详见开发文档第 17 章（开发计划）与第 22 章（轻量进度制度）。

## 核心设计

- **拼接优先于自建**：优先复用 GitHub 上成熟的 Python 开源项目，自建部分必须有明确理由，并记录评估与取舍过程。
- **确定性计算 + 智能体生成**：数值、伤害、掉落、制造等全部在框架内确定性计算；智能体只负责理解意图、选择工具、填写参数、叙事包装。
- **OOP 领域层 + 工具白名单 + 唯一写入者 + 事件溯源**：智能体只是提议者，通过白名单工具方法操作，不直接改状态、不持有对象引用；所有写操作进入 ProposalQueue，由 Resolver 统一裁决、GameEngine 原子提交。
- **结构性防泄漏**：角色 Agent 只接收该角色的私有感知切片与私有记忆，不接收全局状态。
- **可回放**：每个写操作产生 Event（回合、调用者、方法、参数、结果、随机种子），支持固定随机种子回放与审计。
- **防幻觉 / 防越权 / 防作弊**：所有 LLM 结构化输出先过 JSON Schema 与状态一致性校验，校验失败绝不写入状态。

## 技术栈

| 层 | 选型 |
| --- | --- |
| 语言 | Python 3.10+（运行时 3.12，源码保持 3.10 语法兼容） |
| 环境 / 依赖锁定 | uv 0.13.x：`uv.lock` + `uv sync --frozen`（运行时与 dev 依赖同锁） |
| 领域模型 / 校验 | Pydantic v2 |
| 持久化 | SQLAlchemy 2.0.x（锁 `<2.1`） |
| 接入层 | FastAPI |
| 编排 | 自建显式 loop（Q1 定案；LangGraph 仅保留可选封装，第 8 章 §8.4） |
| 模型调用 | 统一 LLMClient 封装（当前已接入 DeepSeek `deepseek-flash` 凭据与连通性自检，完整治理在 M5） |
| 质量门 / CI | GitHub Actions：ruff + mypy + import-linter + pytest（3.10/3.12 矩阵）+ pip-licenses/pip-audit/CycloneDX；本地 pre-commit 钩子 |
| Web 前端（M6） | Vue 3 + TypeScript + Vite + Pinia + Vue Router（构建期 Node，产物由 FastAPI 托管） |

## 本地开发环境（已就绪）

前置：安装 [uv](https://github.com/astral-sh/uv)（本机 0.13.7，位于 `%USERPROFILE%\.local\bin` 并已在 PATH）。Python 3.12 由 uv 自动下载管理；本仓库**不使用**全局 Python 或全局 Anaconda 环境装依赖，所有命令都在仓库根目录执行：

```bash
uv sync --frozen                                          # 按 uv.lock 安装 runtime + dev 依赖（首次会拉取 Python 3.12）
uv run uvicorn textgame.main:app --reload --port 8000     # 启动服务（默认 SQLite: data/textgame.db，已 gitignore）
curl --noproxy '*' http://127.0.0.1:8000/healthz          # → 200 {"status":"ok"}
uv run ruff format --check . && uv run ruff check .        # lint / format
uv run mypy src/textgame                                   # 类型检查（strict）
uv run lint-imports                                        # 分层依赖契约（第 16 章 16.2）
uv run pytest -q                                           # 单元 + 集成 + 回放测试（当前 1001 passed，M4-015 后）
uv run pre-commit install --install-hooks                  # 一次性：安装提交前质量门
```

环境变量见 [.env.example](.env.example)（前缀 `TEXTGAME_`，默认值见开发文档第 16 章 16.5 表）；`.env` 不入库，禁止提交真实密钥。

> **Windows + 非 ASCII 仓库路径提示**：本仓库路径含中文（`D:\ProgramData\游戏开发`）时，`pre-commit install --install-hooks` 生成的 `.git/hooks/pre-commit` 在 Git Bash 下可能报 `pre-commit` not found（Win32 路径编码问题）。本机 hook 已改为优先 `uv run pre-commit` 调用；若重新 install 覆盖后提交失败，用 `uv run pre-commit run --all-files` 手动执行，或恢复该 hook 的 uv 调用分支即可。

> **Python 3.10 本地验证提示**：中文路径下 editable 安装的 `.pth` 会被 Python 3.10 的 `site.py` 用 GBK 读取而失败；本地验 3.10 用 `uv sync --frozen --python 3.10 --no-editable` + `PYTHONPATH=src`（M2 期已验证 3.10.22 全量 638 passed，属历史值；当前 HEAD 全量 1001 passed）。CI 在 ubuntu（UTF-8）上不受影响。

## M2 单场景 MVP 快速试玩（已交付）

服务默认使用 SQLite 存档（`TEXTGAME_DB_URL`，默认 `sqlite:///data/textgame.db`，`data/` 已 gitignore；M4 前已解除「每库单存档」限制：同一 SQLite 库支持多个存档，各自独立的 turn/version/事件序，仅相同 `game_id` 重复创建返回 409）。启动后：

```bash
# 1) 创建存档（写操作必带 Idempotency-Key；可选 seed 用于固定种子）
curl --noproxy '*' -X POST http://127.0.0.1:8000/api/v1/games \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: demo-create-0001' -d '{"seed":42}'
# → 201 {"game_id":"game_...","turn":1,"version":1,"snapshot_version":1,"save_seed":"save-42"}

# 2) 提交回合（body = {game_id, input, expected_version}；expected_version 用上次返回的 version）
curl --noproxy '*' -X POST http://127.0.0.1:8000/api/v1/turns \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: demo-turn-0001' \
  -d '{"game_id":"game_...","input":"环顾四周","expected_version":1}'
# → 200 TurnResult {ok, turn_id, version, text(80–150 字), events, error_code}

# 3) 刷新/重连：GET /api/v1/games/{game_id} → {turn, version, snapshot_version, status, last_text}
```

- 业务失败（无法理解/版本过期/幂等键冲突）仍返回 HTTP 200 + `TurnResult(ok=false, error_code)`；协议错误（缺 `Idempotency-Key`、body 非法）返回 4xx 统一失败信封 `{ok:false, error_code, message, violations?}`。
- 演示脚本（可复现）：环顾四周 → 对老哨兵问好 → 拿起火把 → 拿起治疗药水 → 使用治疗药水 → 向北走进地窖 → 拿起锈剑；固定种子（save-42）7 回合 golden 回放见 `tests/replay/test_m2_golden_replay.py`。

## M3 多角色快速试玩（已交付）

创建存档时 body 加 `"scenario":"m3"` 即进入多角色场景（M2 单场景 + 两名「拾荒者」非玩家角色；缺省 `m2` 保持 M2 契约与回放不变）：

```bash
# 1) 创建 M3 多角色存档
curl --noproxy '*' -X POST http://127.0.0.1:8000/api/v1/games \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: demo-m3-create-0001' \
  -d '{"seed":42,"scenario":"m3"}'

# 2) 回合 2（expected_version=1，输入「环顾四周」）会触发两名拾荒者同回合抢同一瓶药水：
#    Resolver 只让 actor_id 升序靠前者 applied，后者 rejected；返回 events 只含生效事件
curl --noproxy '*' -X POST http://127.0.0.1:8000/api/v1/turns \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: demo-m3-turn-01' \
  -d '{"game_id":"game_...","input":"环顾四周","expected_version":1}'

# 3) 审计查询（只读）：冲突回合的 applied/rejected 提案与裁决理由
curl --noproxy '*' "http://127.0.0.1:8000/api/v1/games/game_.../audit?turn_id=2"
# → 200 {"records":[{proposal_id, actor_id, tool, verdict, verdict_reason, status, ...}]}
```

- 10 回合多角色固定种子 golden 回放见 `tests/replay/test_m3_golden_replay.py`（final `state_hash=df58e51d…`、13 条事件、审计 16 行）；越权/冲突/CAS 安全用例见 `tests/security/test_actor_scope.py`、`tests/security/test_resolver_security.py`、`tests/integration/test_resolver_cas.py`。
- 审计状态词固定 `{committed, conflicted, aborted}`，与裁决 `verdict` 分离；审计表只 INSERT、不 UPDATE/DELETE（第 11 章 §11.4）。

## 数据库迁移与多存档（M4 前已交付）

- **同库多存档**：`events` 主键为 `(game_id, seq)`（`seq` 仍是每存档全局事件序号、`id=evt_{seq:06d}` 仍是每存档编号），`event_uid` 唯一约束为 `(game_id, event_uid)`；同一 SQLite 库可创建多个存档，仅相同 `game_id` 重复创建返回 409。
- **新库**：测试/开发由 `init_schema`（`create_all`）建表；部署建议以 Alembic 管理 schema（`alembic/versions/0001_m3_baseline.py` → `0002_composite_event_key.py`）。
- **旧库升级（M2 时代 schema：`snapshots` 无 `version` 列、无 `audit_events`、`events` 单主键）**：先停服务，再执行

```bash
uv run python -m textgame.persistence.legacy_upgrade --db-url sqlite:///data/textgame.db
```

  脚本会**先备份** `<db>.bak-<时间戳>`，补 `snapshots.version` 列、建 `audit_events` 表、`stamp 0001` 后 `upgrade head`（Alembic 0002 重建 `events` 复合主键），并校验 head 结构；重复执行为 `noop`。失败会自动恢复备份并报明确错误。注意：`downgrade 0002→0001` 仅对单存档库安全（多存档会因旧全局唯一约束报错而非静默丢数据）。

## LLM API 配置（DeepSeek）

意图解析/叙事包装以后要经 LLM（第 8、12 章）。当前已接入 DeepSeek API：模型 `deepseek-flash`（DeepSeek-V4.1-Flash、1M 上下文）、兼容端点 `https://api.deepseek.com`。本阶段只做「凭据 + 连通性」配置；完整 LLMClient（超时/重试/校验修复/成本归集/供应商回退）在 M5 切片 019 实现。

- **密钥只从环境注入**：把 [.env.example](.env.example) 复制为 `.env`（已被 [.gitignore](.gitignore) 忽略）并填写 `TEXTGAME_LLM_API_KEY`。**禁止**把真实密钥写进任何会被 git 跟踪的文件（README、docs、代码、测试、prompt）。
- **密钥入库门禁**：`.pre-commit-config.yaml` 的 `secret-scan` hook 调用 [scripts/check_secrets.py](scripts/check_secrets.py)（标准库自建、无新增依赖），任何 staged 文本命中 `sk-` 长串即阻断提交（第 20 章 R-L5）。
- **配置项**：`TEXTGAME_LLM_PROVIDER=deepseek`、`TEXTGAME_LLM_MODEL=deepseek-flash`、`TEXTGAME_LLM_BASE_URL=https://api.deepseek.com`、`TEXTGAME_LLM_API_KEY=<真实密钥，只写 .env>`。
- **连通性自检**（真实调用、消耗少量 token）：`uv run python -m textgame.llm.smoke` → `[OK] LLM API 配置可用 ...`；缺密钥退出码 2，API 失败退出码 1。实现见 [src/textgame/llm/providers.py](src/textgame/llm/providers.py) 与 [src/textgame/llm/smoke.py](src/textgame/llm/smoke.py)；LiteLLM 仅在 `llm/providers.py` 内 import（第 12 章 12.1）。

## 质量门与 CI（已就绪）

本地门禁（与 CI 同命令，见 [.github/workflows/ci.yml](.github/workflows/ci.yml)）：

```bash
uv run ruff format --check . && uv run ruff check .   # lint / format
uv run mypy src/textgame                              # strict 类型检查（3.10 语法）
uv run lint-imports                                   # 分层依赖契约（domain 零框架依赖）
uv run pytest -q                                      # 单元 + 集成 + 回放
uv run pre-commit run --all-files                     # 9 个 hook（含 secret-scan）

# 依赖门禁（第 19 章 19.5）
mkdir -p artifacts
uv export --frozen --no-hashes --format requirements-txt > artifacts/requirements.lock.txt
uv run pip-licenses --format=json --with-urls > artifacts/licenses.json
uv run python scripts/check_licenses.py artifacts/licenses.json   # GPL/AGPL/LGPL/UNKNOWN → 阻断
uv run pip-audit -r artifacts/requirements.lock.txt               # 已知漏洞
```

CI 两个 job：`quality`（Python **3.10/3.12** 矩阵：ruff → mypy → lint-imports → pytest，矩阵用 `uv sync --frozen --python <ver>` 验证解释器）与 `deps`（`uv export` → pip-licenses → 许可证门禁 → pip-audit → CycloneDX SBOM → artifact）。护栏：GPL/AGPL 出现即阻断；项目自身已声明 MIT（R2 关闭，门禁不再默认豁免 `textgame`）。

**AI 上下文与网络护栏（Q9/Q10 定案，2026-11-09）**：仓库根 [.aiignore](.aiignore) 声明禁止进入 AI 上下文的路径（`.env*`、`.work/`、`artifacts/`、`data/`、`*.db`、`.venv/` 等），secret-scan pre-commit hook 继续兜底；测试套件默认禁网（`tests/conftest.py` 的 socket guard 只放行本机回环，真实 LLM 必须 FakeLLM/cassette）；CI job 设置 `timeout-minutes` 且 quality job 不注入任何密钥。

## 目录结构（M3 完成态）

```text
.
├── .github/workflows/ci.yml   # CI：quality（3.10/3.12 矩阵）+ deps（许可证/pip-audit/SBOM）
├── alembic.ini、alembic/       # M3：Alembic baseline 迁移（events/snapshots/current/idempotency/audit_events）
├── docs/                       # 开发文档（23 章）、PROGRESS.md、progress/ 单次进度文档 001–014
├── src/textgame/               # 包根：main.py（组合根/uvicorn 入口）
│   ├── api/                    # 接入层：/healthz、/api/v1/games（+scenario）、/api/v1/turns、/api/v1/games/{id}/audit、错误映射、Idempotency-Key
│   ├── orchestration/          # TurnOrchestrator（M2 单角色 / M3 多角色）、ProposalQueue、Resolver、关键词解析、模板叙事
│   ├── agents/                 # IntentParseAgent（Schema 校验/修复/兜底）、DeterministicCharacterAgent、ParseOutcome 契约
│   ├── tools/                  # @tool JSON Schema、ToolRegistry 白名单/actor_scope、ToolGate、M2 六工具
│   ├── services/               # GameEngine（唯一写入者）、Query/Action/Audit Service、ActorDirectory、仓储端口、TurnResult
│   ├── persistence/            # SQLAlchemy SQLite：events/snapshots/current/idempotency/audit_events、版本重建、内存实现
│   ├── domain/                 # 领域层（零框架依赖）：types/results/events/models/reducers/scenario（M2/M3 场景）/rules/perception
│   ├── llm/                    # LLMClient 接口、Schema 修复、供应商适配（litellm 唯一 import 点）与自检
│   └── config/                 # pydantic-settings 配置与种子派生
├── tests/                      # unit/、integration/、replay/、security/、fixtures/golden/
├── scripts/                    # check_secrets.py（密钥门禁）、check_licenses.py（许可证门禁）
├── pyproject.toml              # 三层依赖分组 + ruff/mypy/pytest/coverage/import-linter 配置
├── uv.lock                     # 依赖锁定（验收一律 uv sync --frozen）
├── .env.example                # 环境变量样例（复制为 .env；.env 已被忽略、不入库）
└── README.md
```

## 文档

- [《文字交互游戏》开发文档 v0.3](docs/开发文档.md)：23 章，覆盖产品愿景、MVP 范围、总体架构、领域模型、服务层 API、工具注册机制、智能体设计、单回合调用链、判定机制、多角色并发、API 调用治理、制造系统、状态管理与事件溯源、安全防护、目录结构、**vibe coding 开发计划**、测试策略、依赖与许可证、风险与开放问题、附录、**轻量进度制度**，以及 **Vue 3 前端设计（人物卡 JSON 上传 + LLM 生成 JSON）**。
- [按章拆分版](docs/开发文档/)：由权威单文件自动生成，便于按章阅读与 review。
- [开发进度总览](docs/PROGRESS.md) 与 [单次进度文档目录](docs/progress/)：按开发文档第 22 章制度执行（M1 001–004、M2 005–009、M3 010–014 已完成；M6 前端体验编号区间 025–028）。

## 开发进度文档制度

- 每次开发会话结束，在 `docs/progress/` 新建 `NNN-YYYY-MM-DD-<主题>.md`（NNN 三位递增）；
- 同时更新 `docs/PROGRESS.md`（总体进展总览），不得只写单次文档而不更新总览；
- 完成度只按「已通过测试的任务数 / 里程碑总任务数」计算，不得凭感觉填写。

## 许可证

本项目采用 **MIT License**（见 [LICENSE](LICENSE)）：`pyproject.toml` 以 SPDX 表达式 `license = "MIT"` 声明，构建元数据同时携带 LICENSE 文件。CI 许可证门禁（[scripts/check_licenses.py](scripts/check_licenses.py)）默认**不再豁免项目自身**（R2 于 2026-11-09 关闭），依赖侧仍要求 GPL/AGPL/LGPL/未知许可证 0 阻断（第 19 章 §19.5）。
