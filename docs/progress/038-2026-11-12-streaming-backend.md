# 038 · 2026-11-12 · N2 · 流式叙事后端（NDJSON 端点 + 增量提取 + 客户端流式治理）

> 切片目标：按 ADR D-N2-1..N2-8 落地流式叙事阶段 1 后端——独立 NDJSON 端点、JSON 叙事增量提取、
> LLM 客户端流式治理与断连不撤销；**旧 `/turns`/`TurnResult`/事件/`GameState`/默认离线路径零改动**。
> 编号区间：N2 037–044 ｜ 决策：[D-N2-1..N2-8](../decisions/2026-11-11-streaming-phase1.md) ｜
> 设计：[narrative-streaming.md](../design/narrative-streaming.md)（§9 实施结果/偏差） ｜ 上游 037
## 1. 目标与验收命令

- 目标（垂直小功能）：真实 LLM 叙事在生成过程中把已提取的 `narrative` 前缀经 NDJSON `delta`
  发给客户端，回合完整结束后照常提交并以 `done.result`（同 `TurnResult`）为权威；失败/回退用
  `replace`/`error` 表达，分不清就回退旧 `/turns`。
- 验收命令/断言（先定后写）：
  - `pytest -q tests/unit/test_streaming_extractor.py tests/unit/test_llm_streaming.py tests/unit/test_streaming_settings.py tests/integration/test_turn_stream_api.py` → **70 passed**
  - `pytest -q tests/unit/test_llm*.py` → **103 passed**；回合/接线定向集成 → **52 passed**；`tests/replay` → **69 passed**
  - `ruff format --check` / `ruff check`（变更文件）→ 通过；`mypy src/textgame` → Success（117 files）；`lint-imports` → 2 kept
  - 契约断言：`POST /turns` 旧测试零改动；`done.result` 与 `/turns` 同 DTO（模板叙事下逐字段相等，仅事件盖章墙钟 `ts` 除外）；
    客户端提前 close 后回合仍提交；`TEXTGAME_STREAM_ENABLED=false` → 404 统一信封
- 不做什么：不改 `web/`（039 前端）、不改第 6/7 章服务/工具契约、不新增 EventType、不改 golden；
  不做取消/重连恢复与 async 重构（阶段 2）；不跑全量 pytest/demo-console（Lead 出口门禁统一跑）。

## 2. 改动与提交
- 文件（全部为加法/兼容修改）：`src/textgame/llm/{streaming,client,providers,litellm_client,records,runtime,validation}.py`、
  `src/textgame/agents/narrator_agent.py`、`src/textgame/orchestration/turn_orchestrator.py`、
  `src/textgame/config/settings.py`、`src/textgame/api/{schemas.py,routes/turns.py}`、`.env.example`、
  `tests/unit/{test_streaming_extractor,test_llm_streaming,test_streaming_settings}.py`、
  `tests/integration/test_turn_stream_api.py`、`docs/design/narrative-streaming.md`（§9）、本文件。
- 提交：**未提交**（按团队约定改动留工作区，由 Lead 统一提交）。
- 关键 diff 摘要（≤5 行）：新端点 `POST /turns/stream` 用 sync generator + `queue.Queue` + 工作线程
  桥接 `TurnOrchestrator.run(on_delta)`，5 事件 NDJSON；`LiteLLMClient.complete_json_stream` 复用
  deadline/tenacity/RPM/缓存/幂等/计量/记录并新增 `streamed` 字段；`NarrativeDeltaExtractor` 纯函数级
  转义/跨 chunk 提取（与最终文本 exact match）；`TEXTGAME_STREAM_ENABLED` 默认开、`false`→404。
- 护栏检查：未改 `domain/`、EventType、golden、旧 DTO 形状、旧 `/turns` 契约；测试全离线（Q9 禁网，
  Fake/FakeStreamTransport 注入）；无密钥入库；未触碰 `web/`。
- 契约变更：**加法兼容**（新增端点 + 可选方法，无破坏）

  | 变更项 | 第 6/7/8/12 章定义 | 本次实现 | 兼容性 | 偏离标注 |
  | --- | --- | --- | --- | --- |
  | 回合端点 | 仅 `POST /api/v1/turns`（TurnResult 同步） | 新增 `POST /api/v1/turns/stream`（NDJSON 5 事件，`done.result` 复用 TurnResult） | 加法 | D-N2-1/2 已定案 |
  | LLMClient | 同步 `complete_json`/`complete_text` | 追加可选 `StreamingLLMClient.complete_json_stream`（`getattr` 探测，不并入 Protocol） | 加法 | D-N2-4 已定案 |

## 3. 验证结果
- lint / type：`ruff format --check` 24 files formatted、`ruff check` All checks passed；`mypy` 117 files Success；`lint-imports` 2 kept / 0 broken。
- 单测：新增 70 passed / 0 failed（提取器 34、客户端治理 18、开关 4、端点集成 14）；
  `tests/unit/test_llm*` 103 passed；回合/接线定向集成 52 passed；更广定向批次 198 passed。
- 回放/集成：`tests/replay` **69 passed**（无 golden 改动/重基线）；流式端点集成含「生成器 close 断连后仍提交」（version=2、事件 `result.narrative`=LLM 全文）。
- 契约/离线门：`python -m textgame.demo --check` **7/7**、`python -m textgame.console --check` **4/4** 通过（默认离线路径逐字不变）；全量 pytest 按 Lead 分工留出口统一跑。
- 失败与回滚：无（未 commit，无回滚点需求）；全量 unit 批次中 `test_scenario_service` 1 例 SQLite 并发 flake（单跑/重跑通过，与 038 改动文件无关）。

## 4. 下一步与风险
- 下一步：**039 · streaming-frontend**，验收命令 `pnpm --dir web test && pnpm --dir web typecheck && pnpm --dir web build`；
  随后 **040 · streaming-acceptance**（真实 LLM 手跑 + e2e + 文档回写）。
- 风险/开放问题：R-T8（真实首字延迟收益待 040 手跑数字）；流式中途失败 → 不重试、双倍成本控制靠
  「首 chunk 后不换供应商/不二次调用」；修复链为第二次非流式请求（成本口径 040 记录）；
  同步 generator 无法抢占阻塞读取（空闲超时在 chunk 边界检测，阶段 2 取消/重连再评估 async）。
- 需要 Lead 处理：① `docs/PROGRESS.md` §1 N2 行/§3 索引/§4「已用编号 037、038；下一个可用 039」
  （新增本文件后 `check_docs` 要求 §3 索引同批更新；本切片按分工未改 PROGRESS）；② 章节回写建议文本
  见最终报告（第 8/12/23 章、README FAQ、第 20 章 R-T8）。
