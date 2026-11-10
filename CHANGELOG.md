# 变更日志（CHANGELOG）

> 本文件登记**开发期**变更；版本号 `0.1.0` 对应 `pyproject.toml` 当前开发版号，项目尚未对外分发，无对外版本承诺。
> 格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；里程碑/切片编号见 `docs/PROGRESS.md` 与开发文档第 17 章。

## [0.1.0] - 开发版（未对外分发）

### 新增

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

- 默认离线确定性叙事（模板），真实 LLM 解析/叙事/角色/裁判接线属 M5 出口附加项，未默认开启；M6 前端 0/4、后端前置未交付；成本观测按 D-M5-10 不实现。
