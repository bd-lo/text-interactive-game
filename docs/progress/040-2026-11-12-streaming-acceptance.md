# 040 · 2026-11-12 · N2 · 流式叙事阶段 1 验收

> 切片目标一句话：为 038 后端 + 039 前端交付阶段 1 **验收证据**——离线回退等价/回滚/断连不撤销的
> 端到端测试 + 真实 DeepSeek 流式探针 + 真实浏览器 e2e，并回写第 8/12 章实施口径。｜ 验收命令：见 §1 ｜
> 里程碑 N2（第 17 章 §17.9.2）｜ 编号区间 037–044 ｜ 边界：ADR [D-N2-1..N2-8](../decisions/2026-11-11-streaming-phase1.md)、
> 设计 [narrative-streaming](../design/narrative-streaming.md) §9 ｜ 执行/验收：release-tech（Lead 终验）

## 1. 目标与验收命令

- 目标（≤90 分钟）：① 后端双路径验收测试（离线等价/开关回滚/断连不撤销）；② 尽力跑真实 LLM 流式
  探针（首 delta/总耗时/`narrative_source=llm`/`streamed=true`/增量与最终一致）；③ 第 8/12 章回写；
  ④ 039 落地后补跑真实浏览器 e2e。**不改运行时代码/web/事件/golden/第 23 章**。
- 验收命令/断言（先定后写）：
  - `.venv\Scripts\python.exe -m pytest -q tests/integration/test_streaming_acceptance.py tests/integration/test_turn_stream_api.py tests/unit/test_streaming_extractor.py tests/unit/test_streaming_settings.py tests/replay` → **126 passed**
  - `.venv\Scripts\python.exe -m textgame.demo --check` → **7/7**；`.venv\Scripts\python.exe -m textgame.console --check` → **4/4**
  - 真实 LLM 探针（`.work/n-batch/probe-llm-stream-040.py`）→ `ok=true`，数字见 §3
  - `node scripts/e2e-web.mjs --base-url http://127.0.0.1:8791` → **29/29 步、157/157 断言 PASS**
  - `ruff format --check` / `ruff check` 新增测试文件；`mypy src/textgame` → Success
- 不做什么：不改后端契约/端点/事件/`GameState`/golden；不改 `web/`（039 队友）与第 23 章；不新增依赖；不 commit；不改 PROGRESS/CHANGELOG/`docs/开发文档.md`/`decisions/README.md`。

## 2. 改动与提交

- 文件：`tests/integration/test_streaming_acceptance.py`（新，5 例）；第 8 章新增 §8.6（叙事 Agent 流式渲染）、
  第 12 章新增 §12.12（`complete_json_stream` 契约/治理/记录/端点与证据）；本进度文档。
  提交：未提交（工作区留给 Lead 统一提交）；契约变更：无（仅文档回写实施口径）。
- 关键实现：验收测试用**生产组合根** `main._build_app`（tmp SQLite、`TEXTGAME_LLM_ENABLED=false`）
  验证离线单 delta/done 与 `/turns` 等价、`TEXTGAME_STREAM_ENABLED=false` → 404 回滚可逆；HTTP 层
  断连用 `client.stream` 读到 `start` 即关闭 → 回合仍提交，再用 gate 住的脚本化 LLM 做**因果证明**
  （断连时版本仍 1，释放后才提交为版本 2）。
- 护栏检查：零运行时代码改动；未改事件/状态/golden；未改 `web/`/第 23 章；未新增依赖；探针产物只写 `.work`。

## 3. 验证结果（2026-11-12 本机，HEAD `553d0a0`）

- **离线回退（验收测试 5 例全绿）**：`start → 单条 delta → done`；`done.result.ok/version=2/
  narrative_source=template/text 80–150/事件 ITEM_TAKEN narrative 落库`；同 seed 两局 `done.result`
  与 `/turns` 的 `TurnResult` **除事件 ts 外逐字段相等**；`TEXTGAME_STREAM_ENABLED=false` → 404
  信封 + **零写入**（版本仍 1）、旧 `/turns` 正常、重新启用后流式恢复；断连（读到 start 即关闭）→
  版本推进到 2、回放事件 narrative 完整；gate 因果用例：断连时版本仍 1 → 释放后版本 2、
  事件 `narrative_source=llm`、`narrative` 与 LLM 文本一致。
- **真实 LLM 探针**（`.work/n-batch/llm-stream-probe-040.json`，DeepSeek `deepseek-flash`，key 只读不打印）：
  客户端直连首 raw chunk **7.658s** / 首 narrative 前缀 **7.676s** / 总 **8.367s** / 77 chunks /
  `LLMCallRecord.streamed=true`（latency 8375ms、260 tokens）/ 增量拼接 == 最终 narrative（89 字）；
  端到端 turn1 首 delta **2085.2ms**、总 **2985.7ms**、122 字、`narrative_source=llm`、增量==最终文本、
  事件 `ITEM_TAKEN` 的 `result.narrative_source=llm`；turn2 首 delta **1790.5ms**、总 **2528.2ms**、106 字、
  `narrative_source=llm`、事件 `CHARACTER_MOVED`；探针 `ok=true`，产物无密钥（无 `sk-`/`api_key`）。
- **真实浏览器 e2e（039 已落地，本批实跑）**：临时 uvicorn `127.0.0.1:8791`（离线、tmp DB）+
  `node scripts/e2e-web.mjs --out .work/n-batch/e2e-040` → **29/29 步、157/157 断言 PASS**（27 张截图，
  `result.json.ok=true`，exit 0）；证据 `.work/n-batch/e2e-040/`。
- **回归与回滚**：上述组合 **126 passed in 4.10s**（含 replay **69**）；`demo --check` 7/7、`console --check` 4/4；
  `ruff` 通过；`mypy src/textgame` **Success（118 files）**。失败/回滚：无；回滚 = 删除新增文件
  （`TEXTGAME_STREAM_ENABLED=false` 为运行期回滚开关，已测）。

## 4. 下一步与风险

- 下一步：N2 剩余 **043–044 移动端打磨**；阶段 2（取消/断线重连/异步化）未排期；Lead 集成时回填 PROGRESS §3/§4、`docs/decisions/README.md` 索引、重建 `docs/开发文档.md`（第 6/8/12/18/23 章当前不一致）并跑全量 pytest。
- 风险/开放问题：① **R-T8**（LLM 延迟/无流式体感）——真实首 delta 1.79–2.09s，较整段 2.5–3.0s 明显改善；
  直连探针首 chunk 7.66s 属冷启动/网络抖动个例，非稳定基线；② 修复链为第二次非流式调用（流式首轮不合格时
  无增量收益）；③ 039 前端真实浏览器路径已验收，移动端 375px 流式布局由 043–044 覆盖；④ 端到端 2 回合
  中解析/叙事均走 LLM（真实调用预算口径见第 12 章 §12.11，不改变 12.8 设计预算）。
- 需要人工决策：无（阶段 1 边界与 038/039 一致；阶段 2 排期待 Lead/用户）。
