# 流式叙事阶段 1 设计与评估（Narrative Streaming, Phase 1）

> 归属：N2 体验轨 **037 streaming-phase1-design**（对应 [D-N2-1..N2-8](../decisions/2026-11-11-streaming-phase1.md)）；
> 路线图 [D-N4](../decisions/2026-11-11-post-m6-roadmap.md)；风险 **R-T15（旧口径 R-T8，2026-11-12 关闭）** / 开放项 Q7-B（2026-11-12 关闭）。
> 状态：**设计/评估完成（037，2026-11-11）；后端 038 + 前端 039 + 验收 040 均已交付（2026-11-12，见 §9）**。
> 运行时边界：旧 `POST /api/v1/turns`、`TurnResult`/事件/`GameState` 与默认离线路径逐字不变（038 只做加法：新端点 + 可选流式方法）。

## 0. TL;DR

1. 当前痛点不是「总耗时」不可接受，而是**整段生成前玩家看不到任何字**（真实 LLM 首回合 7–8s、后续 2–6s，R-T8）。
2. 架构约束决定「直接 token 流式」不能直接用：叙事是**结构化 JSON**（`{"narrative": "…"}`）+ 校验/一次修复，
   事件只存**校验后的最终文本**。因此阶段 1 采用「**JSON 流式 + 服务端增量提取 narrative 字段**」：
   增量只用于显示，最终仍以校验通过的完整文本提交。
3. 真实供应商实测（2026-11-11，DeepSeek `deepseek-flash`）：流式 **首内容 chunk 0.66s**、**首个可显示 narrative
   前缀 0.68s**、总耗时 1.28s；增量提取结果与最终 JSON 文本 **完全一致**。→ 方案可行，收益 = 把「首字可见」
   从 2–8s 提前到 <1s。
4. 阶段 1 边界：独立端点 `POST /api/v1/turns/stream`（NDJSON）、默认仅「LLM 已配置」时前端启用、失败自动
   降级普通 `/turns`（一次）、`TEXTGAME_STREAM_ENABLED` 一键回滚；不改 `TurnResult`/事件/`GameState`。
5. 工作量估算：038 后端 ≈ 1 人日（客户端流式 + 提取器 + 端点 + 测试），039 前端 ≈ 0.5–1 人日，
   040 验收/文档 ≈ 0.5 人日；合计 **2–3 人日**，风险中等（集中在客户端治理与提取器边界）。

## 1. 现状（为什么不能简单 `stream=True` 透传）

| 环节 | 现状 | 对流式的含义 |
| --- | --- | --- |
| 叙事生成 | `LLMNarratorAgent` 调 `complete_json`，要求输出 **JSON** `{"narrative": "80–150 字"}` | 流式 chunk 是 **JSON 片段**，不是可直接显示的正文 |
| 校验 | `complete_json_with_repair`：Schema + 长度校验，最多 1 次修复；失败回退模板 | 增量文本在完整 JSON 到达前**不可信**（可能被修复/回退替换） |
| 提交 | 叙事完成 → `TurnResult` → `GameEngine` 原子提交事件（唯一写入者/CAS/幂等） | 不能「边流边写」；流式只影响显示，不影响提交顺序 |
| 前端 | `TurnTextStream` 对**完整文本**做打字机；`submitTurn` 普通 `fetch` + JSON | 需要「增量追加」通道；不能破坏既有 120s 超时/错误映射 |
| LLM 治理 | 同步客户端集中处理 deadline/tenacity/RPM/熔断/缓存/幂等/计量/记录 | 流式必须继续走同一治理层，不能绕过（D-N2-4） |
| 测试 | 默认禁网（Q9）；真实 LLM 只用 `.work` 手跑取证 | 流式测试必须注入 Fake transport，零 socket |

## 2. 可行性实测（2026-11-11，真实 DeepSeek API）

探针脚本：`.work/stream_probe.py`（纯文本流式）、`.work/stream_json_probe.py`（JSON 流式 + 增量提取）；
不落库、不提交，仅作证据（`.work` 已 gitignore）。

| 指标 | 纯文本探针 | JSON 探针（贴近 narrate 契约） |
| --- | --- | --- |
| 模型 | `deepseek/deepseek-flash` | 同 |
| 首内容 chunk | **0.461s** | **0.655s** |
| 首个可提取 `narrative` 前缀 | — | **0.675s** |
| 总耗时 | 2.158s | 1.280s |
| chunk 数 | 111 | 87 |
| 正文长度 | 140 字 | 109 字（JSON 内 `narrative`） |
| 增量提取可用步数 | — | 83 / 87 |
| 提取文本 = 最终 JSON 文本 | — | ✅ `exact_match=true` |
| usage（末 chunk） | prompt 89 / completion 171（含 reasoning 58）/ cost ≈ $0.000116 | 未单独记录（同 `stream_options.include_usage` 路径可用） |

结论：供应商支持 `stream=True`、`stream_options={"include_usage": True}` 在末 chunk 返回 usage；JSON 模式下
从流式片段增量提取字段**可行且与最终文本一致**。对玩家而言，首字可见延迟从「整段 2–8s」降到 **0.7s 左右**。

> 说明：探针的 1–2s 总耗时是短文本；玩家真实回合含更长的 system prompt/世界设定，首 chunk 可能更晚，
> 但「首字 < 总耗时」的收益结构不变。040 会记录真实回合（含世界书/目标）的数字。

## 3. 方案对比

| 方案 | 做法 | 优点 | 缺点/风险 | 结论 |
| --- | --- | --- | --- | --- |
| **A. JSON 流式 + 服务端增量提取**（选定） | 保持 `narrate` JSON 契约；服务端从 chunk 中提取 `narrative` 字符串前缀下发；完整 JSON 到达后走原校验/修复 | 校验链/回退/事件语义全部不变；实测可行；前端只收正文 | 需写健壮的增量提取器（转义/字段顺序/provider 差异）；提取失败时静默不发增量 | ✅ 阶段 1 |
| B. 纯文本叙事（`narrate@v4`） | 放弃 JSON，直接流文本，只做长度校验 | 实现最简单、最像「原生流式」 | 丢失 Schema 校验/修复链；prompt/叙事口径变化 → m3/story/世界书叙事重基线；回退语义要重做 | ✗（阶段 1 不做；如做需单独立 ADR + 重基线） |
| C. 假流式 | 前端继续对完整文本打字机（现状） | 零风险 | 真实 LLM 首字延迟不改善（R-T8 不变） | ✗（已存在，不解决问题） |
| D. 两段式预览 | 先流一段「预览文本」，结束后再正式生成 JSON 叙事 | 显示最自由 | **双倍 token 成本/双倍延迟**；两段文本可能不一致 | ✗ |
| E. 客户端提取 JSON | 前端拿原始 JSON chunk 自己 extract | 服务端简单 | 未校验半截 JSON 进前端（违反「不裸 body as T」/安全扫描口径）；类型契约难守 | ✗ |

## 4. 选定方案（A）详细设计

### 4.1 总体流程

```text
浏览器                         FastAPI (async? 否：sync generator + 线程桥接)                 LLM 治理层
  │  POST /turns/stream            │                                                       │
  │  Idempotency-Key + body        │ 1) 幂等/CAS/限流前置（与 /turns 相同）                  │
  │ ──────────────────────────────▶│                                                       │
  │                                │ 2) StreamingResponse(media_type=application/x-ndjson) │
  │  ◀── {"type":"start",...}      │                                                       │
  │                                │ 3) 工作线程跑 TurnOrchestrator.run_turn(on_delta) ──────▶ complete_json_stream
  │  ◀── {"type":"delta","text":"你"} │      增量提取器把 narrative 前缀放入 queue          │  每 chunk：提取前缀
  │  ◀── {"type":"delta","text":"沿着…"} │                                                   │
  │                                │ ┄┄ 叙事完整 → 校验/修复（原链）→ 提交事件/状态 ┄┄        │
  │  ◀── {"type":"replace",...}   │ （仅当校验失败/LLM 中途失败回退模板时）                  │
  │  ◀── {"type":"done","result":{TurnResult}} │ 最终结果（权威文本 + version/events）       │
  │                                │                                                       │
  │  ◀── {"type":"error",...}     │ 回合未提交的失败（start 后仍可能发生，如 LLM 不可用）    │
```

关键点：

- 路由是 **sync**（沿用项目「同步路由 + 线程池」口径，不引入 async 重构）；`StreamingResponse` 的同步
  generator 中启动一个工作线程跑编排，generator 从 `queue.Queue` 读取事件并 yield；回合结束/异常都通过
  queue 传递（含 `done`/`error`），generator 收尾时 join 线程（超时兜底）。
- `run_turn` 增加可选 `on_delta: Callable[[str], None] | None`（默认 None → 与现在完全一致）；
  `LLMNarratorAgent.render_with_meta(..., on_delta=None)` 在检测到客户端支持流式时走
  `complete_json_stream`，否则原样 `complete_json_with_repair`。
- 增量提取器（纯函数）放在新模块 `llm/streaming.py`：
  `extract_narrative_prefix(buffer: str) -> str | None`：定位 `"narrative"` 键后的字符串起始引号，逐字符
  扫描处理 `\"`、`\\`、`\n`、`\t`、`\uXXXX` 转义，返回已解转义前缀；数据上限 `150 + 64` 字符（超出丢弃增量，
  等 `done`）；格式不匹配返回 `None`（不抛异常）。
- 提交时机不变（D-N2-5）：叙事完成后照常提交；断连不撤销。

### 4.2 NDJSON 协议示例

```text
{"type":"start","turn_id":3,"expected_version":2}
{"type":"delta","text":"你沿着"}
{"type":"delta","text":"北边的通道动身"}
{"type":"delta","text":"，脚下的石板缝里积着陈年的灰。"}
{"type":"done","result":{"ok":true,"turn_id":3,"version":3,"text":"你沿着北边的通道动身，……","events":[…],"error_code":null,"narrative_source":"llm"}}
```

校验失败/中途失败时：

```text
{"type":"delta","text":"你沿着北边的通道动身"}
{"type":"replace","text":"你沿着北边的通道动身……（模板叙事全文）","reason":"SCHEMA_VIOLATION"}
{"type":"done","result":{"ok":true,…,"narrative_source":"template"}}
```

失败信封（回合未提交）：

```text
{"type":"start","turn_id":4,"expected_version":3}
{"type":"error","error_code":"CONFLICT","message":"版本已过期"}
```

错误口径：与 `/turns` 一致——**业务失败仍是 HTTP 200**（`error` 事件 + `ok=false` 语义）；协议错误
（缺 `Idempotency-Key`、body 非法）在流开始前返回 4xx 统一信封。重复 `Idempotency-Key` 同参 → 幂等命中，
发送单段 `delta`（完整历史文本）+ `done`（不重放历史增量）。

### 4.3 LLM 客户端流式治理（D-N2-4）

- `LLMClient` 增可选方法 `complete_json_stream(req, on_delta) -> Result`；`LiteLLMClient` 实现，
  `DisabledLLMClient` 不实现（编排层探测 `getattr(client, "complete_json_stream", None)`）。
- 治理顺序：RPM 前置 → 熔断检查 → 尝试链 → **首 chunk 前** tenacity 重试（超时/429/5xx）→ 首 chunk 后任何
  失败 → `LLM_UNAVAILABLE`（不重试、不再换供应商，避免已显示正文被另一模型续写）→ 记录/日志/计量。
- 空闲超时：chunk 间隔 > `timeout_s` 视为超时（deadline 之外的第二道保护）。
- usage：末 chunk（`stream_options.include_usage`）→ `LLMCallRecord` 新增 `streamed: bool = False`（只增）；
  既有非流式记录不变。
- 缓存/幂等命中：`on_delta(完整 content)` 一次 → 正常 `Result`。

### 4.4 前端（D-N2-6）

- `web/src/api/client.ts`：`streamTurn(request, key, { signal, timeoutMs, onEvent })`：
  `fetch` + `AbortController` + `response.body.getReader()` + `TextDecoder("utf-8", {stream:true})`，
  按 `\n` 分帧、`JSON.parse` 逐事件回调；非 2xx/非 NDJSON → 抛 `ApiClientError`（复用错误信封解析）。
- `web/src/api/types.ts`：新增 `TurnStreamEvent` 联合类型（start/delta/replace/done/error）。
- `stores/game.ts::submitTurn`：当「LLM 可用」（`llmSettings` store 状态 `enabled=true`）时走流式，
  `streamingText` 状态累积增量并驱动 `TurnTextStream`；`replace` 覆盖；`done` 用 `result.text` 覆盖并走既有
  成功处理（刷新 view/replay/历史）；`error` 转玩家可读错误；**流式失败降级一次**普通 `submitTurn`（不自动重试）。
- 离线/未配置 LLM：完全走现有 `/turns`（零变化；e2e/golden 不受影响）。
- 「无自动重试」「无 `EventSource`」底线不变（`security-scan.test.ts` 已 forbid）。

### 4.5 配置与回滚

- `TEXTGAME_STREAM_ENABLED`（bool，默认 `true`）：`false` 时 `/turns/stream` 返回 404（前端自动降级）或
  统一 4xx（二选一在 038 定；推荐 404，前端按「端点不存在」降级）；`.env.example` 增加说明。
- 默认离线（`TEXTGAME_LLM_ENABLED=false`）不改变任何现有行为：端点仍可调用，但叙事是模板单段 → 单条 delta
  + done；前端未配置 LLM 时不走流式，因此正常离线体验与现在逐字一致。

## 5. 任务拆分与验收

| 切片 | 内容 | 主要文件 | 验收命令/证据 |
| --- | --- | --- | --- |
| **038** | `llm/streaming.py` 提取器 + `complete_json_stream` + `run_turn(on_delta)` + `POST /turns/stream` NDJSON + 开关 + 断连/幂等/错误测试 | `llm/{streaming,client,litellm_client,providers}.py`、`orchestration/turn_orchestrator.py`、`api/routes/turns.py`、`config/settings.py`、`.env.example` | 新增单测/集成 ≥25；`POST /turns` 既有测试零改动；`demo/console --check` 逐字；断连后回合仍提交 |
| **039** | `streamTurn` 客户端 + 事件类型 + game store/GameView 流式与降级 + vitest | `web/src/api/{client,types}.ts`、`stores/game.ts`、`views/GameView.vue`、`components/TurnTextStream.vue`（如需） | vitest 新增（分帧/转换/降级/replace）；typecheck/build；dist 重建 |
| **040** | 真实 LLM 手跑（首字/总耗时，含世界书/目标）+ 离线回退 + e2e 1 步 + 文档回写（第 12/23 章、README FAQ、PROGRESS） | `scripts/e2e-web.mjs`、docs | 全量 pytest 数字、e2e 步数/断言、手跑证据（`.work/`）写入进度文档 |

## 6. 风险与缓解

| 风险 | 等级 | 缓解 |
| --- | --- | --- |
| 增量提取器对 provider 差异（字段顺序/额外字段/非 JSON 包裹）不鲁棒 | 中 | 提取失败静默不发 delta（最终仍 done）；FakeStreamTransport 覆盖乱序/转义/超长；040 至少 DeepSeek 真跑 |
| 流式中途失败导致已显示正文与最终不一致 | 中 | `replace` 显式覆盖；`done.result.text` 为权威；首 chunk 后不换供应商 |
| 断连后台线程与幂等表并发 | 中 | 复用 `/turns` 的幂等/分段锁路径；断连不写新协议；集成测试覆盖 |
| 前端流式降级导致双倍请求/成本 | 中 | 只在「流式失败一次」时降级；不自动重试；UI 提示一次 |
| 测试/CI 禁网与真实 LLM 证据冲突 | 低 | Fake transport 单测；真实手跑证据只进 `.work`/进度文档 |
| 同步 generator + 线程桥接的复杂度 | 低 | 用 `queue.Queue` + join 超时；路由实现尽量薄；错误统一转 `error` 事件 |

## 7. 不在阶段 1 范围

- 完全 async 化（D-M5-1 的异步化复评门留到阶段 2；阶段 1 用同步 generator + 线程桥接）；
- 取消/断线重连恢复增量（阶段 2）；
- SSE（`text/event-stream`）——NDJSON 已满足且不受 `EventSource` 禁令影响；
- 多供应商流式差异策略（先单供应商 DeepSeek，其它随 N4 055/后续）；
- 模板叙事的逐字流式（模板是单段短文本，打字机已足够，阶段 1 不做）。

## 8. 与文档/轨道的对应

| 文档 | 回写内容 |
| --- | --- |
| 第 12 章 §12.2/§12.6/§12.7/§12.8 | `complete_json_stream` 契约、增量提取、重试/超时/计量口径 |
| 第 23 章 §23.3/§23.4 | NDJSON 客户端、流式渲染与降级、无 EventSource |
| 第 17 章 §17.9.2 N2 | 037–042 状态更新（2026-11-12：037–042 已交付，§17.9.6 有交付状态表） |
| README / FAQ | 「流式叙事」说明与开关（039/040 已交付；README 回写归 Lead 统一批次） |
| 第 20 章 R-T15（旧口径 R-T8） | **已更新（2026-11-12，040 验收后关闭）**；见 §20.4 |

## 9. 实施结果（038 流式叙事后端，2026-11-12）

> 本切片已实现并自验：独立 NDJSON 端点、JSON 叙事增量提取、客户端流式治理、`on_delta` 注入与
> 断连不撤销测试。设计边界（D-N2-1..N2-8）未放宽；**旧 `/turns`、`TurnResult`、事件、`GameState`、
> 默认离线路径零改动**。下表登记实现口径与设计文本的差异（实现细节以代码 docstring 为准）。

### 9.1 交付清单

| 文件 | 内容 |
| --- | --- |
| `src/textgame/llm/streaming.py`（新增） | 纯函数 `extract_narrative_prefix(buffer)` + 有状态 `NarrativeDeltaExtractor`：JSON 转义/`\uXXXX`/UTF-16 代理对/跨 chunk 不完整转义/`150+64` 提取上限/非字符串值/结束 flush；增量拼接与最终文本 `exact match` |
| `src/textgame/llm/client.py` | 可选 `StreamingLLMClient` Protocol + `DeltaCallback` + `supports_streaming()`（不并入 `LLMClient`，`DisabledLLMClient`/Fake 无需实现） |
| `src/textgame/llm/providers.py` | `chat_completion_stream()`（`stream=True` + `stream_options.include_usage` + `num_retries=0`，仍是全仓唯一 SDK import 点） |
| `src/textgame/llm/litellm_client.py` | `complete_json_stream(req, on_delta)`：RPM 只按次前置、首 chunk 前 tenacity 重试/回退、**首 chunk 后不重试不换供应商**、整链 deadline + chunk 间空闲超时、末 chunk usage、缓存/幂等命中一次下发、`streamed=True` 记录 |
| `src/textgame/llm/records.py` | `LLMCallRecord.streamed: bool = False`（只增字段，旧记录/断言不变） |
| `src/textgame/llm/runtime.py` | `RuntimeLLMClient.complete_json_stream` 热切换委托；内部客户端不支持流式时退回 `complete_json` 并一次下发 |
| `src/textgame/llm/validation.py` | 公开 `build_repair_request`（流式完整 JSON 不合格后复用原一次修复链） |
| `src/textgame/agents/narrator_agent.py` | `render_with_meta_stream`：chunk → 增量提取器 → 只把 narrative 前缀交给 `on_delta`；完整 JSON 到达后走原校验/一次修复；失败回退模板 |
| `src/textgame/orchestration/turn_orchestrator.py` | `run(request, on_delta=None)` 及内部链路透传；叙事器实现可选 `render_with_meta_stream` 时走流式，提交时机/唯一写入者/CAS/幂等不变 |
| `src/textgame/config/settings.py`、`.env.example` | `TEXTGAME_STREAM_ENABLED`（默认 `true`；`false` 时端点 404 一键回滚） |
| `src/textgame/api/schemas.py`、`api/routes/turns.py` | 5 个 NDJSON 事件模型 + `POST /api/v1/turns/stream`（sync generator + `queue.Queue` + 工作线程桥接；断连只停止消费、提交继续） |
| `tests/unit/test_streaming_extractor.py` 等 3 个单测文件 + `tests/integration/test_turn_stream_api.py` | 新增 **70** 例（见 §9.4） |

### 9.2 实测事件序列（示例，均来自集成测试输出）

成功（LLM 叙事增量可用；`done.result` 即权威 `TurnResult`）：

```text
{"type":"start","turn_id":2,"expected_version":1}
{"type":"delta","text":"你沿着北边的通道动身"}
{"type":"delta","text":"，脚下的石板缝里积着陈年的灰"}
{"type":"done","result":{"ok":true,"turn_id":2,"version":2,"text":"你沿着北边……","events":[…],"narrative_source":"llm"}}
```

校验失败/修复后文本变化（或首 chunk 后 LLM 失败回退模板）：

```text
{"type":"start","turn_id":2,"expected_version":1}
{"type":"delta","text":"太短"}
{"type":"replace","text":"……权威完整文本……","reason":"NARRATIVE_REVISED"}
{"type":"done","result":{"ok":true,"turn_id":2,"version":2,"text":"……权威完整文本……","narrative_source":"llm"}}
```

业务失败（回合未提交，HTTP 仍 200；无 `done`）：

```text
{"type":"start","turn_id":2,"expected_version":0}
{"type":"error","error_code":"CONFLICT","message":"你的对局版本已经过期，请刷新对局视图后重试。"}
```

模板叙事/幂等重放（无增量收益时服务端补一条完整 `delta`，不是 `replace`）：

```text
{"type":"start","turn_id":2,"expected_version":1}
{"type":"delta","text":"你弯下腰，把火把从地上拾起来……"}
{"type":"done","result":{"ok":true,…,"narrative_source":"template"}}
```

### 9.3 与设计的偏差（实现口径登记）

| # | 设计文本 | 实际实现 | 理由/影响 |
| --- | --- | --- | --- |
| 1 | `complete_json_stream` 的 `on_delta` 语义未明确到层 | `on_delta` 交付**供应商原始 JSON 文本片段**；增量提取在 `LLMNarratorAgent`（`llm/streaming.py` 纯函数） | 客户端只做治理（限流/重试/计量/记录），不感知叙事 Schema；缓存/幂等命中下发完整 JSON 由提取器统一处理，前端永不接触原始 JSON |
| 2 | `start.turn_id` = 回合开始 | 取 `expected_version + 1` 的**预告值**；只读回合不推进，权威值以 `done.result.turn_id` 为准 | 流开始于 CAS 前置校验之前（设计 §4.2 示例本身也含 start 后 error）；不额外读仓储/不改变唯一写入者 |
| 3 | `replace.reason` 示例 `SCHEMA_VIOLATION` | 固定枚举 `TEMPLATE_FALLBACK`（回退模板）/ `NARRATIVE_REVISED`（修复/多角色拼接后文本变化） | 机器可读原因需覆盖「LLM 中途失败」等非 Schema 场景；前端只消费 `text` |
| 4 | 流内失败口径 | 业务失败只发 `error`，**不发 `done`**（HTTP 200）；协议错误（缺 `Idempotency-Key`/body 非法）在流开始前 4xx 统一信封 | 与 D-N2-2/设计 §4.2 失败示例一致；`error` 已表达「回合未提交」 |
| 5 | 开关默认 + 关闭行为（404 或 4xx 二选一） | 默认 `true`（端点常开）；`false` → **404** 统一失败信封 | 前端 039 按「端点不存在」降级普通 `/turns`；旧客户端不调用 = 死代码无副作用 |
| 6 | chunk 间空闲超时 | 在 chunk 边界用注入时钟检测（`> config.timeout_s` 视为失败）；同步迭代器无法抢占阻塞中的读取，单次读取超时仍由 transport timeout 兜底 | 阶段 1 同步 generator + 线程桥接；取消/抢占留阶段 2 |
| 7 | 「完整 JSON 到达后仍走原校验/修复链」 | 校验失败后的一次修复请求走**非流式** `complete_json`（复用 `build_repair_request`）；修复成功→`replace` 权威文本 | 修复是第二次供应商调用，无需再流式；避免已显示增量与修复文本混排 |
| 8 | 模板叙事单 chunk 回退 | 无已显示增量时发单条完整 `delta`（不标 `replace`）；仅当已有部分增量后回退模板才 `replace` | 与 D-N2-5「幂等重放返回完整结果（单段 delta + done）」一致 |
| 9 | 多角色叙事 | m3 下 LLM 屏文本 + NPC 确定性短句拼接后若与已显示增量不同，发 `replace`（`NARRATIVE_REVISED`）；`done.result.text` 权威 | 不改变 `_compose_text` 既有长度收敛/提交口径；m2/m4 不受影响 |
| 10 | 「记录 `streamed=True`」（字段只增） | `LLMCallRecord.streamed` 默认 `False`；流式成功/失败/缓存命中均 True | 旧非流式记录/断言不变 |
| 11 | 未覆盖「CAS 重试时叙事器被再次调用」 | 多角色链路 CAS 冲突重试会重新渲染叙事 → 已有增量可能与最终文本不同，服务端以 `replace`（`NARRATIVE_REVISED`）兜底 | 罕见并发路径；提交语义（重试只重渲染/不写半截事件）不变；阶段 1 不做增量去重 |

### 9.4 测试与证据（038 自验）

- 新增 **70** 例全通过：提取器 34（含参数化：转义/代理对/任意 chunk 大小/超长/非字符串）、
  流式客户端治理 18（首 chunk 前重试/后不重试不换供应商/空闲超时/usage/缓存/幂等/RPM/回调异常）、
  开关配置 4、NDJSON 端点集成 14（事件顺序、`done.result` 与 `/turns` 等价、模板单段、`error`、
  幂等重放、同键异参、`replace`、转义跨 chunk exact match、开关 404、**生成器 close 断连后仍提交**）。
- 相关回归：`tests/unit/test_llm*` **103 passed**；回合/接线相关集成 **52 passed**；
  更广定向批次 **198 passed**；`tests/replay` **69 passed**（无 golden 改动）；
  `python -m textgame.demo --check` **7/7**、`python -m textgame.console --check` **4/4** 自检通过
  （默认离线路径逐字行为未变）。
- 质量门：`ruff format --check` / `ruff check` 变更文件通过；`mypy src/textgame` Success（117 files）；
  `lint-imports` 2 contracts KEPT。全量 pytest 由 Lead 在合并前统一跑。

### 9.5 状态与下一步（2026-11-12 更新）

**状态：038/039/040 均已交付，阶段 1 闭环**（039 前端 `web/src/api/client.ts::streamTurn`；040 验收见下）。

1. **039 前端（已交付）**：`streamTurn`（`fetch`+`ReadableStream`、逐事件运行时校验、幂等键/`AbortSignal`/超时、
   `replace` 覆盖、失败降级 `/turns` 一次、可取消）；进度文档
   [039](../progress/039-2026-11-12-streaming-frontend.md)；
2. **040 验收（已交付）**：离线双路径（生产组合根 `TEXTGAME_LLM_ENABLED=false` 单 delta 与 `/turns` 等价、
   `TEXTGAME_STREAM_ENABLED=false` 404 回滚、断连不撤销）+ 真实 DeepSeek 探针：**首 delta 1.79–2.09s、
   总 2.53–2.99s、`narrative_source=llm`、增量拼接 == 最终文本、`LLMCallRecord.streamed=true`**；真实浏览器
   e2e **29/29 步、157/157 断言**。进度文档 [040](../progress/040-2026-11-12-streaming-acceptance.md)；风险
   R-T15（旧口径 R-T8）已在第 20 章 §20.4 关闭；
3. 修复链仍为第二次非流式调用：真实 LLM 下「流式首轮不合格 + 修复」的成本口径由 040 手跑记录（本次未触发修复场景）；
4. 阶段 2（未排期）：取消/断线重连恢复、异步化、SSE vs NDJSON 复评（见 ADR §6）。
