# 001 · 2026-11-08 · M1 骨架 · walking-skeleton

> 切片目标一句话：建立 uv 可复现环境 + 最小 FastAPI 可启动骨架（`/healthz` 200），为 002 提供绿点 ｜ 验收命令：`uv sync --frozen && uv run pytest -q && uv run lint-imports` ｜ 里程碑 M1 ｜ 编号区间 001–004 ｜ 执行/验收：lead（AI 执行）、verifier（独立验收）

## 1. 目标与验收命令

- 目标（垂直小功能，≤90 分钟）：按第 16 章 16.5 / 第 17 章 §17.2 切片 001 落地「环境可复现 + 服务可启动 + 质量门可执行」。
- 验收命令/断言（先定后写）：
  - `uv --version` / `uv lock --check` → uv 0.13.7；lock 与 pyproject 一致（176 包）
  - `uv sync --frozen` → 通过；`.venv` 为 Python 3.12.16，161 包装入
  - `uv run ruff format --check . && uv run ruff check .` → `19 files already formatted` / `All checks passed!`
  - `uv run mypy src/textgame` → `Success: no issues found in 15 source files`
  - `uv run lint-imports` → `Contracts: 2 kept, 0 broken`（Analyzed 19 files, 8 dependencies）
  - `uv run pytest -q` → 3 passed（unit 2 + integration 1）；junit `tests=3 failures=0`
  - `uv run uvicorn textgame.main:app` + `curl /healthz` → HTTP 200，body 恰为 `{"status":"ok"}`；openapi paths 恰为 `["/healthz"]`
  - `pip-licenses`（161 包）→ GPL/AGPL/LGPL = 0；`pip-audit` → `No known vulnerabilities found`
- 不做什么：不写 domain 模型（002）、不写 EventLog/回放（003）、不写 CI workflow（004）、不建数据库、不接 LLM、不建 `web/`。

## 2. 改动与提交

- 文件：`pyproject.toml`、`uv.lock`、`.python-version`、`.gitignore`；`src/textgame/`（main + api + config + 7 个占位包）、`tests/`（unit/integration + conftest）、`.env.example`、`.pre-commit-config.yaml`；`README.md`、`docs/PROGRESS.md`、本文件。
- 提交：`e2fef8f`（uv 环境配置）、`7b1b00c`（uv.lock 单独提交）、`46b91b0`（walking-skeleton 代码/测试/README）；本文件与 PROGRESS.md 随 `progress: 001 walking-skeleton` 提交。
- 关键 diff 摘要：三层依赖分组（runtime 14 / dev 17 / reference-only 注释登记）；`/healthz` 根路径；`Settings(TEXTGAME_)`；import-linter `layers` + `forbidden` 两契约；src layout + hatchling。
- 护栏检查：未改 domain/Event/第 5–7 章契约（尚未建立）；无密钥入库（`.env` 忽略、`.env.example` 仅占位）；依赖扫描无 GPL/AGPL；pre-commit 与 import-linter 配置经人工 review；未放宽红线。
- 契约变更：无（`/healthz` 为运维探活端点，不属第 6、7 章服务/工具契约）。

## 3. 验证结果

- lint / type：ruff format/check 通过（19 文件）；mypy strict 通过（15 源文件）；lint-imports 2/2 契约 KEPT。
- 单测：`pytest -q` → passed 3 / failed 0 / skipped 0（unit 2 + integration 1；3 次不同随机种子运行稳定）。
- 回放/集成（涉及状态时）：不适用（本切片无状态写入与事件）。
- demo：`uv run uvicorn textgame.main:app --port 8765` + `curl --noproxy '*' /healthz` → `HTTP/1.1 200 OK` + `{"status":"ok"}`；验收后进程已终止、端口释放。
- 失败与回滚：无最终回滚。过程修复：ruff 首跑报 72 处 RUF002（中文全角标点）→ 定策略为 ignore `RUF001–RUF003`；`lint-imports` 首跑因缺 `include_external_packages=true` 失败 → 补配置后通过（均经独立复核）。
- 独立验收：verifier 9 大项 PASS（证据 `.work/verification-report.md`，本地工作区文件，不入库）；已知偏离 D1–D6 逐项核实可接受。

## 4. 下一步与风险

- 下一步：002 · domain-models，验收命令 `uv run pytest tests/unit/test_domain_models.py`；字段严格按第 5 章 5.2–5.5，不得自创。
- 风险/开放问题：
  - R1（中）：Python 3.10 仅有静态保障（mypy/ruff target + `requires-python`），3.10 CI 矩阵未实跑 → 004 落地双矩阵。
  - R2（低，发布前）：项目自身许可证未定，pip-licenses 将 `textgame` 标为 UNKNOWN → 004 许可证 CI 需 `--ignore-packages textgame` 或先决策。
  - R3（低）：MPL-2.0 传递依赖（orjson/certifi/pathspec/tqdm/hypothesis）需在 004 生成 SBOM 时登记文件级义务。
  - ENV1（已缓解）：仓库中文路径导致 pre-commit 生成 hook 在 Git Bash 下无法启动 `.venv` → 本机 hook 改为 `uv run pre-commit` 调用，README 已记录。
- 需要人工决策：无（许可证与 Q9/Q10 已在总览第 7 节挂账，不阻塞 002）。
