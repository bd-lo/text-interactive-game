# 032 · 2026-11-12 · M6 后 N1 内容轨 · scenario-share

> 切片目标一句话：场景分享 = 单文件文件交换——新增只读 `GET /api/v1/scenarios/{id}/export`（可直接再导入、零写入、不泄漏库内字段），导入沿用既有 `POST /scenarios`，示例扩充 1 个 v2 分享示例并回填设计文档 §6 ｜ 验收命令：`pytest -q tests/integration/test_scenario_api.py tests/integration/test_user_scenario_flow.py tests/unit/test_scenario_service.py` ｜ 里程碑 M6 后 N1 ｜ 编号区间 029–036 ｜ 执行/验收 dsl-v2（Lead 终审）

## 1. 目标与验收命令
- 目标（一个垂直小功能）：导出→再导入 roundtrip 可用（v1 + v2 各一），分享不引入云端/账号/市场；后端先行，网页导出/导入按钮与 e2e 归后续前端批次（本批不改 `web/`）。
- 验收命令/断言（先定后写）：
  - `.venv\Scripts\python.exe -m pytest -q tests/integration/test_scenario_api.py tests/integration/test_user_scenario_flow.py tests/unit/test_scenario_service.py` → **65 passed**
  - `.venv\Scripts\python.exe -m pytest -q tests/replay` → **69 passed**（旧 golden 零回归）
  - `ruff format --check` / `ruff check <变更 py>` → 通过；`mypy src/textgame` → **Success（117 files）**
  - `python -m textgame.demo --check` 与 `python -m textgame.console --check` → 逐字不变（本批只加路由/服务方法，不触内置 4 场景）
- 不做什么：不改 `api/schemas.py`（另一队友在改，本批导出无需新 DTO）；不改 `web/`；不做云端托管/账号/市场；不做批量导入导出（N3 045）；不改 EventType/GameState/golden。

## 2. 改动与提交
- 文件（≤8）：
  - `src/textgame/services/scenario_service.py`：`export_scenario()` + `ExportedScenario`（只读、确定性序列化、库内定义损坏→404、体积守卫）
  - `src/textgame/api/routes/scenarios.py`：`GET /api/v1/scenarios/{scenario_id}/export`（JSON 附件；其余 5 端点契约不变）
  - `tests/integration/test_scenario_api.py`（+6 函数/7 case：v1 roundtrip、v2×2 roundtrip、只读确定性、不泄漏、404、免幂等键；OpenAPI 端点断言补导出）
  - `tests/unit/test_scenario_service.py`（+5：可再导入 payload、v2 flags 保留、404、零写入确定性、脏定义 404；另把既有 R-04 并发用例改用文件型 SQLite，修复并行负载下共享内存连接导致的偶发 `sqlite3.InterfaceError`，断言零改动）
  - `tests/integration/test_user_scenario_flow.py`（+1：4 个示例全量校验 + 规范化再解析）
  - `examples/scenarios/moonlit-market.json`（v2 分享示例）、`examples/scenarios/README.md`（作者/标题/进入方式/分享步骤）
  - `docs/design/user-scenario-modules.md` §6（v2/分享回填）、本进度文档
- 提交：未提交（按约定留工作区，由 Lead 统一提交）
- 关键 diff 摘要：导出体 = 入库定义重新序列化（`schema_version`/标题/作者/房间/flags 等全字段保留，键序取入库规范化顺序 → 同场景多次导出字节一致）；文件名 `<slug>.json`（slug 正则天然 ASCII 安全）；响应头 `Content-Disposition: attachment; filename="..."`；导出前 `parse_definition(canonical_json(definition))` 守卫库内脏数据。
- 护栏检查：未新增 EventType/GameState/golden；零新增依赖；未改 `api/schemas.py`（无新增 DTO）；导入侧 256KB/白名单/幂等校验逐字不变；未改 `web/`、PROGRESS、CHANGELOG、`docs/开发文档.md`。
- 契约变更：新增第 6 个端点（v1 五端点保持）；导入/校验/列表/详情/删除与 `POST /games` 形状不变。

## 3. 验证结果
- lint / type：`ruff format --check` 5 files already formatted；`ruff check` All checks passed；`mypy src/textgame` Success（117 files）。
- 单测：`tests/unit/test_scenario_service.py` **27 passed**（原 22，+5）；目标 3 文件合计 **65 passed / 0 failed**（原 49 → +16 case，其中本批 +13：api +7、service +5、flow +1）。
- 回放/集成：`tests/replay` **69 passed**（旧 golden 零改动）。导出 roundtrip 断言：v1（tiny-tower）与 v2（lantern-vigil `schema_version=2`/5 flags、moonlit-market 3 flags）导出→再导入后 `definition` 逐字段相等、`schema_version`/summary/title/author 一致、导出体再次 `/scenarios/validate` 通过、再导入后仍可 `build_game_payload` 建局；导出两次字节一致、列表/详情零变化、events 表 0 行；未知/格式非法/已软删 404；导出体不含 `scenario_id`/`scn_`/summary/时间戳/仓库路径。
- demo：`python -m textgame.demo --check` → 通过；`python -m textgame.console --check` → 通过（本批无 console/demo 改动）。
- 失败与回滚：无。

## 4. 下一步与风险
- 下一步：031 · scenario-editor（编辑器只产出 JSON + 实时 `/scenarios/validate`，详情页「导出」按钮复用 032 端点）；网页场景库「导出/下载」按钮与导入页分享入口 + e2e 1 步 → 后续前端批次（D-N5：编辑器/分享拆分，本批后端先行）。
- 风险/开放问题：① `web/` 未接导出按钮，玩家暂需经 API/终端或浏览器地址栏下载（契约已稳定，可直接接）；② 导出为「入库定义重新序列化」，键序/空白与作者原始文件不逐字一致（字段/值逐字保留；如需字节级原样需存原文，涉及迁移，暂不做）；③ 035–036 示例内容包可复用本批 README/示例。
- 需要人工决策：无；若用户要求网页端立即可见，需另排前端批次（不改变本批后端契约）。

## 5. 网页交付补齐（N1-032 web，2026-11-12 同批）

> 本小节由 web 批（N1-031/032）追加，补齐 §1/§4 中「web 按钮与 e2e 归后续前端批次」的承诺；后端契约未变。

- 客户端：`web/src/api/client.ts::exportScenario(id)` 只读 GET `/api/v1/scenarios/{id}/export`（带超时/取消，
  非 2xx 复用统一失败信封；响应体必须是 JSON 对象，否则契约错误），从 `Content-Disposition` 取 `<slug>.json`，
  缺失回退 `<scenario_id>.json`；`API_PATHS.exportScenario` 与契约测试 `EXPECTED_FRONTEND_PATHS` 同步。
- UI：场景详情页「导出 JSON」按钮（Blob + `<a download>` 触发浏览器下载，成功提示「已导出 xxx.json，可直接在
  「导入场景」页再次导入」；失败进 ErrorBanner）+ 「以此为基础编辑」（跳 `/scenarios/editor?from=<id>`）；
  场景库/导入页补「新建场景（编辑器）」入口与「从详情导出的 `<slug>.json` 可直接再导入」分享引导。
- 测试：`web/tests/unit/scenarioExport.test.ts`（6 例：客户端文件名/404/非 JSON、详情页下载/失败/编辑入口）；
  `pytest tests/integration/test_frontend_contract.py` 加入 export 路径/只读 GET/无幂等键断言 → **27 passed**。
- e2e：新增 `scenario.export` 步（HTTP 断言 200 + `application/json` + `attachment; filename="<slug>.json"` +
  导出体保留 v2 定义且不含库内字段；浏览器详情页点击导出命中 GET `/export` 且无错误横幅；导入页分享引导），
  全量 e2e **31/31 步、171/171 断言**（较此前 29 步/157 断言只增不减）。
- 护栏：无云端/账号/市场；导出仍只读零写入、无 `Idempotency-Key`；未改后端导出契约与示例文件。
