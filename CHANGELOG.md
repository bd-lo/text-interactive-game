# 变更日志（CHANGELOG）

> 本文件登记**开发期**变更；版本号 `0.1.0` 对应 `pyproject.toml` 当前开发版号，项目尚未对外分发，无对外版本承诺。
> 格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)；里程碑/切片编号见 `docs/PROGRESS.md` 与开发文档第 17 章。

## [0.1.0] - 开发版（未对外分发）

### 新增

- **M1 骨架（4/4）**：uv 环境与依赖锁定（`uv.lock` + `uv sync --frozen`）、Pydantic v2 领域模型、EventLog 固定种子回放、GitHub Actions 质量门/许可证门禁（3.10/3.12 矩阵 + pip-licenses/pip-audit/SBOM）。
- **M2 单场景 MVP（5/5）**：`POST /api/v1/games` / `POST /api/v1/turns`（幂等键/CAS/错误码映射）、ToolRegistry 白名单与解析 Agent（Schema 校验/修复/确定性兜底）、RuleEngine + 模板叙事、SQLite 事件表/快照与重启恢复、首个 golden 回放（save-42 七回合）。
- **M3 多角色（5/5）**：角色私有感知切片与 `actor_scope` 可见性矩阵、`ProposalQueue` + 纯函数 `Resolver`、确定性 `CharacterAgent`、多角色 CAS 提交、审计只增表与查询端点、10 回合多角色 golden 回放。
- **M4 制造（4/4）**：配方契约（模板/实例分离）、确定性制造与自由制造校验链路、5 个制造 golden 回放与审计。
- **M5 进行中（2/6）**：019 `LLMClient` 同步契约 + `LiteLLMClient` 治理（deadline/tenacity/RPM/幂等/日志/计量）+ 默认离线工厂；020 `llm/config/` JSON 分级 + 进程内 `TTLCache`。022 按用户决定缩范围为并发幂等（成本/预算/`llm_call_log` 不做，D-M5-10）。
- **OSS-1 可玩终端入口（不占 M1–M6 编号）**：`python -m textgame.console`（自然语言连续游玩 + `:help/:inv/:status/:quit`）、`python -m textgame.demo --check`（固定 seed 七屏自检）、`scripts/quickstart.ps1|sh`、`examples/m2-demo.md`、README「5 分钟上手」、只读 `GET /api/v1/games/{id}/view` 与背包意图。
- **CI-1（不占编号）**：修复公开仓库 CI 的 pytest-only 失败（`import litellm` 远端成本表下载被禁网守卫拦截）→ `LITELLM_LOCAL_MODEL_COST_MAP` 离线化 + pytest 失败摘要写入 Check 注解；本地 1179 passed、replay 61、远端 CI 全绿。
- **文档/仓库修正（D1 批）**：ADR `docs/decisions/2026-11-10-022-scope-and-cost-out.md`（D-M5-10）；README 数字/目录树/LLM 配置/联网提示/终端示例；本 CHANGELOG 与 `CONTRIBUTING.md`；quickstart 只装运行时依赖（`--no-dev`）；`.gitignore` 补 `*.db`。

### 变更

- 022 缩范围：`022-cost-budget` → `022-concurrency-idempotency`（仅 D-M4-15/G-1 并发同 `Idempotency-Key` 幂等）；成本/预算/价格表/`llm_call_log` 表/Alembic 0003 不做，0003 顺延 M6 卡库（D-M5-10）。
- LLM 配置权威改为 JSON（`src/textgame/llm/config/llm.default.json` + `TEXTGAME_LLM_CONFIG` 覆盖）；历史 `TEXTGAME_LLM_PROVIDER/MODEL/BASE_URL` 仅兼容回退（D-M5-4）。

### 修复

- D-M4-14：`Resolver.resolve` 用快照 `rng_state.save_seed` 构造 `ToolContext`/规则前置，修复非默认 seed 存档制造种子与实例 ID 派生不一致。
- D-M4-15（P2 已知限制）：并发同 `Idempotency-Key` 瞬时响应不一致——待 022-concurrency-idempotency 关闭。

### 质量基线（HEAD `cb7863b` 实测）

- 全量 `uv run pytest -q` → **1179 passed**；`uv run pytest tests/replay -q` → **61 passed**（M2/M3/M4 = 4/5/24，零回归）；ruff/format/mypy(84 files)/lint-imports 四门全绿；覆盖率 88%。

### 已知限制

- 默认离线确定性叙事（模板），真实 LLM 解析/叙事/角色/裁判接线属 M5 出口附加项，未默认开启；M6 前端 0/4、后端前置未交付；成本观测按 D-M5-10 不实现。
