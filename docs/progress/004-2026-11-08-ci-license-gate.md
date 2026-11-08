# 004 · 2026-11-08 · M1 骨架 · ci-license-gate

> 切片目标一句话：把质量门 + 许可证门禁落成 CI（3.10/3.12 矩阵 + deps job），GPL/AGPL 即阻断 ｜ 验收命令：`uv run lint-imports && uv run ruff check . && uv run pytest -q` ｜ 里程碑 M1 ｜ 编号区间 001–004 ｜ 执行/验收：ci-dev（AI）/ lead（复核）

## 1. 目标与验收命令

- 目标（垂直小功能，≤90 分钟）：第 17 章 §17.2 切片 004——GitHub Actions 质量门与许可证门禁落地，本地可执行的许可证检查脚本。
- 验收命令/断言（先定后写）：
  - `uv run lint-imports` → 2 kept / 0 broken；`uv run ruff check .` → All checks passed；`uv run pytest -q` → 402 passed
  - `uv run pytest tests/unit/test_check_licenses.py -q` → 12 passed（干净/GPL/AGPL/LGPL/UNKNOWN/ignore/stdin/非法输入）
  - 真实依赖门禁：`uv run pip-licenses --format=json --with-urls` → 157 包；`check_licenses.py` → 检查 156、忽略 1（`textgame` 自身 license 待定）、**0 阻断、exit 0**
  - `uv run pip-audit -r requirements.lock.txt` → No known vulnerabilities found
  - SBOM：`uv run --with cyclonedx-bom==8.0.1 cyclonedx-py environment --of JSON` → exit 0，230,099 B，CycloneDX 1.6 / 174 components
  - 3.10 兼容：`uv sync --frozen --python 3.10` + `--no-editable`/`PYTHONPATH=src` → Python 3.10.22，**402 passed**
- 不做什么：不 push 仓库、不跑真实 LLM 调用、不引入新依赖、不改 lock、不实现集成/回放 job 的夜间调度（M2+）。

## 2. 改动与提交

- 文件：`.github/workflows/ci.yml`、`scripts/check_licenses.py`、`tests/unit/test_check_licenses.py`
- 提交：`24e15c5`（`slice: 004 ci-license-gate`）
- 关键 diff 摘要：`quality` job = Python 3.10/3.12 矩阵（`setup-uv@v6` + `uv sync --frozen --python ${{ matrix.python-version }}` + `uv run python -VV` 证据 + ruff format/check + mypy + lint-imports + pytest）；`deps` job = `uv export` → pip-licenses JSON → 许可证门禁 → pip-audit → CycloneDX SBOM → upload-artifact；触发 push main + PR。
- 护栏检查：GPL/AGPL/LGPL 出现即阻断（优先级高于宽松关键词，双许可也不放行）；`textgame` 自身 UNKNOWN 默认 ignore（第 19 章 §19.5 / R2）；无 `pip install`、无真实 LLM；未新增依赖、未改 pyproject/uv.lock。
- 契约变更：无

## 3. 验证结果

- lint / type：ruff format/check、mypy strict（25 源文件）、lint-imports 全绿；`pre-commit run --all-files` 9 hooks 全 Passed。
- 单测：`pytest tests/unit/test_check_licenses.py -q` → 12 passed；全量 `pytest -q` → 402 passed。
- 回放/集成（涉及状态时）：不适用（CI 配置与许可证脚本，无状态）。
- demo：本地全量彩排 deps job 各步 exit 0（见 §1 真实依赖门禁/SBOM）；3.10 矩阵本地 402 passed（中文路径绕过方式见 §4）。
- 失败与回滚：无回滚。偏离 2 处：① `cyclonedx-py` 不在锁定依赖（`cyclonedx-python-lib` 只有库无 CLI），用 `uv run --with 'cyclonedx-bom==8.0.1'` 临时覆盖环境（已 PyPI 验证，不写 lock）；② 本地 3.10 因 Windows 中文路径 + editable `.pth` 的 GBK 读取缺陷无法直接启动，用 `uv sync --python 3.10 --no-editable` + `PYTHONPATH=src` 实跑 402 passed（CI ubuntu UTF-8 不受影响）。

## 4. 下一步与风险

- 下一步：M1 出口验收通过，进入 M2 单场景 MVP：005 · turn-api，验收命令 `uv run pytest tests/integration/test_turn_api.py`。
- 风险/开放问题：R1 Python 3.10 兼容已本地实跑（3.10.22 402 passed），但 GitHub Actions 端待首次 push 后确认；R3 MPL-2.0 传递依赖（orjson/certifi/pathspec/tqdm/hypothesis）已由 SBOM 覆盖，文件级义务登记待 M1 后；SBOM 工具版本靠 workflow 内 pin 保证可复现。
- 需要人工决策：项目许可证（发布前；`check_licenses.py` 已默认忽略自身，正式发布前需定 license 字段）。
