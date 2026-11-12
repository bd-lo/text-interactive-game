# ADR · 流式叙事阶段 1 设计与边界（D-N2-1..N2-8）

- 状态：已定案（设计） ｜ 决策人：lead ｜ 复核人：独立复核（设计评审 + 实测探针，`.work` 证据不入库） ｜ 日期：2026-11-11 ｜ 基线：`4afea5e`
- 关联：第 17 章 §17.9 **N2 体验轨 037–040**；路线图 ADR **D-N4**（流式阶段 1 边界）；
  R-T8（真实 LLM 延迟/无流式）、Q7-B（发布期复评）；设计文档 `docs/design/narrative-streaming.md`。
- 本 ADR 只定**阶段 1 的实现边界与协议**；实现分 038（后端）/039（前端）/040（验收）三个切片，
  本批（037）只交付设计与评估，不改任何运行时代码。

## 1. 背景与红线

- 现状：叙事由 `LLMNarratorAgent` 经 `LLMClient.complete_json` 产出 **JSON 文本**（`{"narrative": "…"}`），
  再由 `complete_json_with_repair` 校验/一次修复，失败回退模板；回合提交在叙事完成之后，事件里只存
  **最终文本**。前端 `TurnTextStream` 只是「对完整文本做打字机」。
- 体感问题（R-T8）：真实 LLM 首回合 7–8s、后续 2–6s；玩家在整段生成完前看不到任何字。
- 红线（阶段 1 不放宽）：
  1. **唯一写入者 + CAS + `Idempotency-Key`** 语义与 `POST /turns` 完全一致；
  2. **未校验文本不进状态**：流式增量只用于显示，最终写入的仍是校验通过的完整叙事；校验失败 →
     前端「替换」为模板文本；
  3. **默认离线路径零变化**：`demo --check`/`console --check`/旧 golden/默认 `POST /turns` 逐字不变；
  4. 不新增 `EventType`、不改 `GameState`、不改既有 DTO（`TurnResult` 原样作为最终载荷）；
  5. 前端禁用 `EventSource`（`security-scan.test.ts` 已 forbid；统一 `fetch` + `ReadableStream`）；
  6. 测试套件禁网（Q9）：流式单测必须走可注入 transport/Fake，不得真连。

## 2. 决策

### D-N2-1 端点形态：独立 `POST /api/v1/turns/stream`

- 决策：新增独立端点（不改造 `/turns` 为双形状），请求体/`Idempotency-Key`/错误信封与 `/turns` 一致，
  响应 `Content-Type: application/x-ndjson; charset=utf-8`（`StreamingResponse`）。
- 理由：既有 `/turns` 契约被契约测试/前端运行时校验/回放证据钉死；双形状会让同一 DTO 出现两种校验分支。
  独立端点还可以在 038 期间默认存在但前端不用（灰度），失败时回退旧路径。
- 被否：`?stream=true`（同端点双形状）、SSE（`text/event-stream`；前端 `EventSource` 被禁且不能带
  `Idempotency-Key`，若用 `fetch` 解析 SSE 也等于自造 NDJSON 协议，没有收益）、WebSocket（连接生命周期与
  幂等/重连语义都更复杂）。
- 影响面：`api/routes/turns.py`、`api/app.py`（无需额外 CORS——同源）、contract 测试路径集合。
- 回写落点：第 12 章 §12.2/§12.10、第 23 章 §23.3、第 17 章 §17.9。
- 验收断言：`POST /turns` 旧测试零改动；新端点 404/422/409/200 与 `/turns` 同口径单测。

### D-N2-2 NDJSON 事件协议（阶段 1 固定 5 种事件）

- 决策：每行一个 JSON 对象，`type` 为判别字段，按到达顺序：

| 事件 | 载荷 | 说明 |
| --- | --- | --- |
| `start` | `{type, turn_id, expected_version}` | 回合开始；`turn_id` 是**拟提交回合号**（`expected_version+1` 预告值），只读零事件回合不推进——权威值以 `done.result.turn_id` 为准 |
| `delta` | `{type, text}` | 叙事增量（UTF-8 文本，客户端追加） |
| `replace` | `{type, text, reason}` | 丢弃已显示增量并替换（校验失败/LLM 中途失败回退模板；`reason` 机器可读） |
| `done` | `{type, result}` | `result` = 与 `/turns` **完全相同的 `TurnResult`**（权威文本） |
| `error` | `{type, error_code, message}` | 回合未提交的失败（统一失败信封字段；HTTP 状态仍 200，与业务失败口径一致） |

- 理由：最小协议覆盖「开始/增量/替换/成功/失败」；`done.result` 直接复用既有 DTO，前端无需第二套解析；
  `replace` 让「未校验文本只显示、最终以权威文本为准」有显式通道。
- 注（2026-11-12 QA P2-1 明示）：`start.turn_id` = **拟提交回合号**（`expected_version + 1` 的预告值），`done.result.turn_id` = 实际提交回合；只读零事件回合两者可不同，客户端不得用 `start.turn_id` 作为权威回合号。
- 被否：只发 `delta`+`done`（无法表达中途回退）；`error` 放 `done`（与 `TurnResult(ok=false)` 重复，
  但流开始前的失败无法给 `TurnResult`，故保留 `error`）。
- 回写落点：第 23 章 §23.3、前端 client 类型、契约测试路径。
- 验收断言：解析器单测覆盖 5 种事件与未知事件（忽略 + warning，不崩）；`done.result` 字段集合 =
  `TurnResult`（契约测试自动校验）。

### D-N2-3 服务端增量：从 JSON 叙事中**增量提取** `narrative` 字段

- 决策：叙事仍走 `complete_json`（`narrate@v3` 的 JSON 契约与校验链不变）。流式实现上，LiteLLM 的
  chunk 是 JSON 片段（`{"narrative":"……`），服务端用**白名单增量提取器**解析已到达的 `"narrative"` 字符串
  前缀（处理 `\"`/`\\`/`\n` 等转义、UTF-8 跨 chunk 的字符边界由 transport 层保证 str 完整），通过
  `delta` 下发；**完整 JSON 到达后仍走原校验/修复链**，校验通过→`done` 用最终文本（与增量前缀一致）；
  失败→`replace` 模板文本。
- 实测依据（2026-11-11，`.work/stream_json_probe.py`，真实 `deepseek-flash`）：
  - 首内容 chunk **0.66s**，首次可提取 narrative 前缀 **0.68s**，总耗时 **1.28s**（87 chunks、109 字）；
  - 增量提取结果与最终 JSON 的 `narrative` **完全一致**（`exact_match=true`），说明该方案在真实供应商上可行。
- 理由：保持 JSON 契约 = 保持既有校验/修复/回退链与 golden 口径；只增加「显示层增量」。
- 被否：① 改成纯文本叙事（`narrate@v4`，放弃 JSON Schema 校验）——降低安全/一致性，且需重基线叙事；
  ② 两段式（先流一段预览文本，再正式生成 JSON）——双倍成本；③ 客户端提取 JSON——把未校验/半截 JSON
  暴露给前端，违反「不裸 body as T」与安全扫描口径。
- 风险：provider 可能先输出无关文本/字段顺序不同 → 提取器容忍前缀垃圾、只匹配 `"narrative"` 键；
  超长数据 → 提取上限 `150 + 安全余量` 字符，超出丢弃（最终仍以校验结果为准）。
- 回写落点：`llm/streaming.py`（038 新增）、第 12 章 §12.6。
- 验收断言：提取器单测（转义/跨 chunk/超长/乱序/无字段/非法 JSON）；完整 JSON 未到前不发 `replace`；
  校验失败必发 `replace` 且 `done.result.text` = 模板文本。

### D-N2-4 LLM 客户端：新增可选的 `complete_json_stream(req, on_delta)`

- 决策：`LLMClient` Protocol 增加**可选**方法（`@runtime_checkable` 不适用，采用 `getattr` 探测）：
  `complete_json_stream(req, on_delta: Callable[[str], None]) -> Result`。`LiteLLMClient` 实现：
  1. RPM 令牌桶**前置一次**（流式不按 chunk 计费限流）；熔断器同非流式；
  2. tenacity 重试仅作用于「**首 chunk 之前**」的启动阶段（超时/429/5xx 可在未产生任何 delta 时重试）；
     首 chunk 之后失败 → 不重试、不静默换供应商，返回 `LLM_UNAVAILABLE`（`MidStreamFallbackError`
     已在 `providers._RETRYABLE_EXCEPTION_NAMES`，分类可复用）；
  3. 整链 deadline 与 chunk 间空闲超时（空闲 > `timeout_s` 视为失败）；
  4. usage 从末 chunk（`stream_options.include_usage`）取，记录 `LLMCallRecord.streamed=True`（字段只增，
     默认 False → 旧记录/断言不变）；token 计量口径不变；
  5. 缓存/幂等命中 → 把完整内容**一次** `on_delta` 下发后按普通成功返回（客户端不感知差别）；
  6. `DisabledLLMClient` 不实现该方法 → 编排层按「不支持流式」走模板单段（默认离线路径零变化）。
- 理由：治理职责（限流/重试/熔断/计量/记录）必须继续集中在客户端；把流式塞进 Agent 会破坏第 12 章口径。
- 被否：让 Agent 直接 `import litellm`（破坏唯一 import 点）；async 重构（D-M5-1 明确异步化复评门在
  流式设计——但阶段 1 可先不异步：FastAPI `StreamingResponse` 的同步 generator 在线程池中运行，
  编排线程 + `queue.Queue` 桥接即可）。
- 回写落点：第 12 章 §12.2/§12.7/§12.8、`llm/litellm_client.py`、`llm/client.py`。
- 验收断言：FakeStreamTransport 单测（正常/首 chunk 前失败可重试/首 chunk 后失败不重试/空闲超时/usage/
  缓存命中一次下发/记录 `streamed`）；既有 `LLMTransport` 单测零改动。

### D-N2-5 写入时机与生命周期：回合完整结束后提交，断连不撤销

- 决策：流式端点内部调用与 `/turns` **同一个**`TurnOrchestrator.run_turn`（唯一写入者不变）；事件/状态提交
  发生在叙事完整生成与校验之后。客户端断连只停止显示，不撤销回合（后台线程继续完成提交）；重连后通过
  `GET /games/{id}/view` + `/replay` 看到已提交回合（与普通回合一致）。
- 理由：若「断连即回滚」会引入新的取消/补偿协议，触碰唯一写入者与审计语义；断连不撤销是单机单用户下
  最简单且可解释的语义（回合已消耗 LLM 成本，提交更符合直觉）。
- 被否：断连回滚（需要事务外取消 + 幂等记录改写）；「生成完先不提交，等客户端确认」（多一次往返、可被
  恶意不确认）。
- 回写落点：第 9 章 §9.2、第 11 章、第 17 章 §17.9。
- 验收断言：客户端提前断开后回合仍提交（集成测试用断开的 TestClient/生成器 close 验证）；幂等重放返回
  完整结果（单段 delta + done）。

### D-N2-6 前端：`fetch` + `ReadableStream` 解析 NDJSON，默认仅在 LLM 可用时使用

- 决策：`web/src/api/client.ts` 新增 `streamTurn(...)`（复用 `request` 的超时/错误解析思路，但用
  `AbortController` + `response.body.getReader()` + `TextDecoder(stream:true)` 按行解析）；`GameView`/
  `game` store 在「`GET /settings/llm` 显示 `enabled=true`」时优先流式，否则走普通 `/turns`
  （模板叙事单段没有收益且要保证离线 e2e/回放路径零变化）；流式失败（网络/端点 404/非 NDJSON）自动降级为
  普通 `/turns`（**只降级一次，不自动重试**，避免双倍扣费）；收到 `replace` 时用其文本覆盖打字机内容；
  `done` 时以 `result.text` 为权威。取消按钮阶段 1 不做（可后续）。
- 理由：离线模板路径必须零变化（golden/e2e/演示稳定性）；LLM 路径才是延迟痛点。
- 被否：无条件流式（离线也会多一次端点切换与失败路径）；EventSource（禁）；自动重试（违反「无自动重试」）。
- 回写落点：第 23 章 §23.3/§23.4、`web/src/api/types.ts`/`client.ts`、`GameView.vue`。
- 验收断言：vitest 覆盖 NDJSON 解析/分块边界/`replace`/错误降级；e2e 离线路径断言 `/turns` 仍被调用
  （非流式），LLM 不可用时 console 无错误；contract 测试纳入新路径。

### D-N2-7 测试与证据策略（禁网、可回放）

- 决策：新增 `FakeStreamTransport`（逐 chunk 产出预置 JSON 片段）+ 单元测试，零 socket；集成测试用
  `TestClient` + 注入 FakeStreamTransport 断言 NDJSON 事件序列与最终提交；e2e 只覆盖离线路径（模板单段
  `/turns`）与新端点不可用时的降级；真实 LLM 手跑证据放 `.work/`（不入库、不进 CI），数字写入进度文档。
- 理由：Q9 禁网守卫 + CI 无密钥；真实流式只能手跑取证（与 M5 真实 LLM smoke 同口径）。
- 回写落点：第 18 章、`tests/`、`scripts/e2e-web.mjs`（+1 步）。
- 验收断言：全量 pytest 数字只增不减（新测试独立计价）；e2e 步数/断言数更新并写文档。

### D-N2-8 开关、回滚与阶段划分

- 决策：阶段 1 **后端端点常开**（只读语义、无副作用前置校验），前端按 D-N2-6 的「LLM 可用」条件启用；
  新增环境变量 `TEXTGAME_STREAM_ENABLED`（默认 `true`；`false` 时端点返回 404 或直接 reject，前端自动走
  非流式），用于出问题时一键回滚。阶段 2（未排期）：默认全量流式、取消/重连恢复、SSE 评估。
- 理由：端点在旧客户端上是死代码（无调用者），常开不影响任何既有路径；前端条件启用已足够灰度。
- 被否：端点默认关闭（前端需探测 + 404 分支，反而更复杂）；默认全量流式（离线路径风险不值当）。
- 回写落点：`.env.example`、README FAQ（「流式叙事」）、第 20 章 R-T8 状态。
- 验收断言：`TEXTGAME_STREAM_ENABLED=false` 时 `/turns/stream` 行为有单测；前端降级路径有 vitest。

## 3. 契约→切片映射

| 决策 | 实现切片 | 主要文件 | 验收断言 |
| --- | --- | --- | --- |
| D-N2-1/2 端点+协议 | 038 | `api/routes/turns.py`、`api/schemas.py`（新增流事件模型，不改旧 DTO） | 事件序列单测 + contract 路径 |
| D-N2-3 增量提取 | 038 | `llm/streaming.py`（纯函数） | 提取器单测（转义/跨 chunk/超限） |
| D-N2-4 客户端流式 | 038 | `llm/client.py`、`llm/litellm_client.py`、`llm/providers.py` | FakeStreamTransport 单测 |
| D-N2-5 提交时机 | 038 | `orchestration/turn_orchestrator.py`（注入 `on_delta`）、`api/routes/turns.py` | 断连后仍提交 + 幂等重放 |
| D-N2-6 前端 | 039 | `web/src/api/client.ts`、`stores/game.ts`、`GameView.vue` | vitest + e2e |
| D-N2-7 证据 | 038–040 | `tests/`、`scripts/e2e-web.mjs` | 全量/e2e 数字 |
| D-N2-8 开关 | 038 | `config/settings.py`、`.env.example`、README | 开关单测 + 降级 vitest |

## 4. 已知问题归属

| 编号 | 摘要 | 归属切片 | 关闭条件 |
| --- | --- | --- | --- |
| R-T8 | 真实 LLM 无流式、首回合 7–8s | 038–040 | 流式端点 + 前端渲染验收；手跑数字写入进度文档 |
| — | 增量提取对 provider 差异的鲁棒性 | 038 | 提取器单测 + 至少 DeepSeek 真实手跑证据；提取失败静默不发 delta |
| — | 流式中途失败的双倍成本 | 038 | 首 chunk 后不重试；失败回退模板（不二次调用） |
| — | 断连语义 | 038 | 集成测试：断开后回合仍提交 |
| — | 前端降级只做一次 | 039 | vitest：流式失败一次后走 `/turns`，不循环 |
| Q7-B | 流式复评 | 037（本 ADR） | **设计已完成**；实现随 038–040 |

## 5. 出口验收判据与到期承诺处置

- 037 出口（本批）：本 ADR + `docs/design/narrative-streaming.md` + 真实探针证据 + PROGRESS 登记；
  **不改运行时代码**，全量测试数字不变。
- 038 出口：`/turns/stream` 事件协议 + 增量提取 + 客户端治理 + 断连/幂等测试全绿；`POST /turns` 既有
  测试零改动；`demo/console --check` 逐字不变。
- 039 出口：前端流式/降级 + vitest；e2e 增加 1 步（离线走 `/turns`，端点存在不崩）。
- 040 出口：真实 LLM 手跑（首字延迟/总耗时）与离线双路径证据 + 文档回写；R-T8 状态更新。
- 到期承诺处置：Q7-B（「流式/SSE 发布期复评」）→ **本 ADR 关闭评估部分**，实现排入 038–040；
  D-M5-1「异步化复评门 = M6 流式叙事/SSE 设计」→ 阶段 1 采用**同步 generator + 线程桥接**，暂不异步化，
  阶段 2 若做取消/重连再评估 async。

## 6. 后移与复审

- 阶段 2（未排期）：默认全量流式、取消/重连恢复、SSE vs NDJSON 复评、多供应商流式差异适配；
- 若 038 实测增量提取在其它 provider 上不稳定 → 退化为「首 chunk 后不发送增量、仅最后 `done` 一次性显示」
  （用户仍看到加载态，不回归安全）；
- 本 ADR 后续调整 = 新 ADR + `supersedes: D-N2-x`，并同步第 17 章 §17.9 与第 12/23 章。

## 7. 变更纪律

- 实现必须遵守红线（§1）；任何「把未校验文本写进事件/状态」或「改 `TurnResult` DTO」的提案先改本 ADR；
- 038–040 每个切片的进度文档需引用本 ADR 与实测数字；`TEXTGAME_STREAM_ENABLED` 开关与前端降级必须同批落地。
