# 045 · 2026-11-12 · M6 后 · N3 功能轨 · scenario-pack-batch（场景包批量导入/导出，终端 CLI 部分）

> 切片目标一句话：把场景库从「网页单文件导入」扩展到终端**批量**操作——`--import-scenarios <目录>` 一次导入整个目录的场景文件（逐文件 ≤256KB/UTF-8/对象预检 + 稳定幂等键 + 逐文件 ok/skip/error 中文报告 + 部分失败退出码 1）、`--export-scenarios <目录>` 只读导出场景库为 `<slug>.json`（可再导入 roundtrip）｜ 验收命令：`.venv\Scripts\python.exe -m pytest -q tests/integration/test_console_scenario_pack.py` ｜ 里程碑 M6 后（N3 功能轨，编号区间 045–048）｜ 执行/验收 n3-features

## 1. 目标与验收命令

- 目标（一个垂直小功能）：终端批量导入 `--import-scenarios <目录>`（可选 `--import-limit` 默认 50/上限 100；只读扫描 `*.json`，逐文件 ≤256KB、UTF-8、JSON 对象；逐个走既有 `POST /api/v1/scenarios`，幂等键 = 文件内容 sha256（稳定可重放）；报告 `[ok]/[skip]/[error]` + 汇总，有失败退出码 1、全成/全跳过 0）；终端批量导出 `--export-scenarios <目录>`（可选 `--export-limit` 默认 50/上限 100；只读 list+detail 取原始 definition 写 `<slug>.json`，slug 重名追加 `-<scenario_id 后 8>`，导出文件可直接再导入）；帮助文本补两行用法。网页多文件 UI 留给后续 web 批次（同 045 切片，不阻塞本进度登记）。
- 验收命令/断言（先定后写）：
  - `.venv\Scripts\python.exe -m pytest -q tests/integration/test_console_scenario_pack.py` → **21 passed**
  - `.venv\Scripts\python.exe -m pytest -q tests/integration/test_console_scenario_pack.py tests/integration/test_console_scenario.py tests/integration/test_console_cards.py` → **61 passed**
  - 全部 console 相关测试（pack/card/scenario/cards/story/walkthrough）→ **80 passed**
  - `.venv\Scripts\python.exe -m textgame.console --check` → exit 0，改动前后 SHA256 均 `C1BE0085…CB8F`（逐字一致）
  - `.venv\Scripts\python.exe -m textgame.demo --check` → exit 0，改动前后 SHA256 均 `F36D3112…2E72`（逐字一致）
- 不做什么：不改既有 `--list-scenarios`/`--scenario-id` 行为；不改其他 src 模块/事件/状态/golden/web；用户 JSON 永不执行；导出只读不写库；不新增运行时依赖；不 commit。

## 2. 改动与提交

- 文件：`src/textgame/scenario_pack.py`（新：扫描/上限/幂等键/导出文件名/错误文案纯逻辑）、`src/textgame/console.py`（新增 `--import-scenarios`/`--export-scenarios`/`--import-limit`/`--export-limit` 与两个批量入口、参数互斥校验、交互帮助两行）、`tests/integration/test_console_scenario_pack.py`（新，21 例）、`docs/design/scenario-pack-batch.md`（新：CLI 语义/上限/幂等/错误口径/web UI 待接）、本篇。
- 提交：未提交（不 commit；改动留工作区由 Lead 统一提交）。
- 关键 diff 摘要：① 导入：目录扫描（不递归、文件名稳定排序）→ 逐文件预检（≤256KB、UTF-8(BOM 容忍)、JSON 对象）→ `POST /api/v1/scenarios`（`Idempotency-Key=console-scenario-pack-<sha256>`）→ 用「导入前已存在 ID / 本次已创建 ID」判定 `[skip]`；② 导出：`GET /scenarios?limit=N` → 逐个 `GET /scenarios/{id}` 取 `definition` → `<slug>.json`（重名 `-<id 后 8>`；超 256KB 回退紧凑编码）；③ 参数互斥/范围校验（导入导出互斥、limit 1..100、不能与浏览/开局参数同用）。
- 护栏检查：只读导出（不建局/不写事件）；导入只写场景资料库（场景库不参与事件溯源）；逐文件失败隔离、退出码语义明确；错误文案不泄漏端点/异常/Traceback；未改 `api/schemas.py`、`api/routes/saves.py`、web、既有 examples/scenarios（并发任务文件）。
- 契约变更：无（只复用既有 `GET/POST /api/v1/scenarios` 与 `GET /{id}`；未新增端点）。

## 3. 验证结果

- lint / type：`ruff format --check`/`ruff check` 变更文件通过；`mypy src/textgame` → **118 source files no issues**；`lint-imports` → 2 contracts kept / 0 broken；`scripts/check_secrets.py` exit 0。
- 单测：新文件 **21 passed**（v1+v2 导入、重跑 skip 不重复、同内容去重、非法 JSON/非对象/超 256KB/Schema 非法逐文件 error、部分失败退出码 1、空/不存在目录、数量上限与越界校验、导入导出互斥、导出 roundtrip 定义逐字段一致、slug 重名后缀、空库/导出上限、帮助两行、`--list-scenarios` 旧行为不变）。
- 回放/集成：console 全套 **80 passed**；`console --check` 4 轮 exit 0（SHA256 前后一致）；`demo --check` 7/7 屏 exit 0（SHA256 前后一致）。
- 手工验收（临时库）：目录 2 好 1 坏 → `[ok]/[ok]/[error]`、汇总 成功 2/失败 1、exit 1；重跑 → 全部 `[skip]`、exit 0、库内 2 条不变；导出 2 文件 → 新库再导入 → 2 条、定义一致。
- 失败与回滚：无。

## 4. 下一步与风险

- 下一步：同 045 切片的**网页多文件 UI**（后续 web 批次：多选/拖拽逐个 `POST /scenarios` multipart `file` 部件；导出按钮走 `GET /scenarios/{id}/export`，N1-032 已交付单文件分享端点）；验收沿用本设计 §6。
- 风险/开放问题：① 目录扫描不递归（子目录需分别导入）；② 单次上限 100（超大目录分批，稳定幂等键保证重跑自动跳过）；③ 批量非事务：部分失败时已成功项保留、重跑跳过已成功项（逐文件报告）；④ 同 slug 允许重复建档（与既有单文件导入一致，不做去重策略）。
- 需 Lead 统一处理：`scripts/check_docs.py` 的「新进度文档未在 PROGRESS §3 索引」与「章节拼接需重建 docs/开发文档.md」为批次收尾项（`docs/PROGRESS.md`/`docs/开发文档.md` 为我禁改文件）。
- 需要人工决策：无（本轮）。
