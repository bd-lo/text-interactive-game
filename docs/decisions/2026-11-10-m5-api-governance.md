# ADR · M5 API 调用治理决策（D-M5-1..9）

- 状态：已定案（只追加）｜决策人：lead｜复核人：3 名独立复核委员（review-modeler / review-runtime / review-governance，Round 1 立场书 + Round 2 交叉复核，论文见 `.work/checkin-2026-11-10/panel/`）｜日期：2026-11-10｜基线：`0544f76`（=origin/main，工作树 clean，1096 passed / replay 61）
- 关联：M5 019–024 + M5 出口附加项（LLM Agent 接线，不新增编号）｜`docs/PROGRESS.md` §5/§7｜第 12 章（权威）、第 14/16/17/18/19/20/21 章｜audit `A-*`（`.work/status-2026-11-09/audit-docs.md`）
- 决策编号：`D-M5-1..9`（议题编号 `M5-D1..D8` 只用于讨论过程，见 §2 映射）

## 1. 背景与红线

- M4 已 4/4 交付并通过出口独立验收（PASS，无 P0/P1，P2×5 见 `2026-11-09-m4-craft-contract.md`）；M5 六切片 019–024 未开始，出口附加项为解析/叙事/角色/裁判 Agent 的 LLM 接线。
- 现状：`src/textgame/llm/` 只有契约/离线客户端/修复辅助/供应商自检；生产默认 `DisabledLLMClient`（零联网）；真实 DeepSeek 凭据已在 `.env`（不入库）并 smoke 自检通过。
- 红线（第 17 章 §17.5、第 12 章 12.1/12.3）：唯一写入者 `GameEngine`、`EventLog`/CAS/`ProposalQueue`/`Resolver` 结构；确定性计算与 LLM 生成分离；`actor_scope`/工具白名单不放宽；密钥不入库/不入日志；**不新增依赖**（`litellm/instructor/tenacity/cachetools/jinja2` 已在锁内；**PyYAML 不得引入**）；M2/M3/M4 golden 零回归；禁 `--snapshot-update`；业务代码只依赖 `LLMClient`。
- 本轮验收基线：M4 遗留 P2 全部为文档/口径层，**不阻断 019 开工**；其中数字陈旧与 `docs/decisions/` 缺位在 019 首个 docs 提交清零（见 §6）。

## 2. 决策（议题映射：M5-D1→D-M5-1、D2→D-M5-6、D3→D-M5-3、D4→D-M5-4、D5→D-M5-5、D6→D-M5-6/8、D7→D-M5-7、D8→D-M5-8，另立 D-M5-2/9）

### D-M5-1 LLMClient 契约：同步签名（议题 M5-D1）

- **决策**：M5 权威签名定为**同步** `def complete_json(req) -> Result` / `def complete_text(req) -> Result`；同提交回写第 12 章 §12.2/结论 1 与第 21 章 §21.2.4；登记本决策；异步化**复评门 = M6 流式叙事/SSE 设计**（第 20 章 Q7 触发时）。
- **理由**：全链路现状同步（FastAPI 路由均为 `def`，在 Starlette 线程池执行；编排/仓储/Agent 同步）；async 方案需改 routes→orchestrator→agents→validation→FakeLLM 并引入 pytest-asyncio（dev 依赖未声明，属隐式/新增依赖），与 M5「治理」主题不匹配，且会重开 M2–M4 已验证面。
- **被否**：A) 019 起 async（需新增测试依赖 + 全链重构，M5 无 async 消费者）；C) 双契约并存（违反单一权威）。
- **影响面**：019 同步实现；`DisabledLLMClient` 同步补 `complete_text`；`tests/fakes.py` 不被追加重写（D-M5-3）。
- **回写落点**：第 12 章 §12.2（代码块 `async def`→`def`、结论 1）、第 21 章 §21.2.4；第 17 章 §17.2 M5 备注。
- **验收断言**：`LLMClient` Protocol 两方法同步；`DisabledLLMClient` 两方法均返回 `LLM_UNAVAILABLE`；无 `pytest-asyncio` 新增依赖。

### D-M5-2 Schema 修复链归属：019 保持 `validation.py`（议题 M5-D5/第 12 章 12.3/12.6）

- **决策**：019 的 `complete_json` **不做** Schema 校验/修复，只返回原始文本（data 保留 `{"content","repaired":False,"usage"}` 并增补 `call`）；修复仍由 `agents` 显式调用 `validation.complete_json_with_repair`；**不引入 instructor 到运行时**（019–020 src 零引用）。021-schema-repair 切片再评估「修复链迁入客户端 / instructor 启用」，届时必须保留 `max_repair=1`（共 2 次请求）语义与既有 FakeLLM 调用次数断言。
- **理由**：现有修复链有 7 例单测 + 链路测试（`test_parser_tool_chain.py` 断言 `calls[1].max_repair == 0`；`FakeLLM` 全仓 52 处引用/8 文件）；迁入客户端需同步重写 FakeLLM 为 transport 级替身，回归面大且与 019「客户端治理」主题无关；第 12 章 12.3 的实质约束是「修复只做一次」，已由 `max_repair≤1` + 修复请求 `max_repair=0` 保证。
- **被否**：B) 019 迁修复链入 client + 重写 FakeLLM（契约委员方案，S1/S2/S3 三步回归）；C) 全量启用 instructor（新增未验证运行路径）。
- **回写落点**：第 12 章 §12.6 注「M5-019 修复链仍在 validation.py（D-M5-2），021 复核」；第 21 章 §21.2.4 同步说明。

### D-M5-3 请求契约对齐：`output_schema: type[BaseModel] | None`（议题 M5-D1 关联）

- **决策**：019 把 `LLMRequest.output_schema` 从「JSON Schema dict」改为**Pydantic 模型类**（字段名保持 `output_schema`；不使用第 12 章附录的 `schema` alias——`schema` 是 Pydantic v2 保留属性，ADR 记录偏离）；Agent 传 `ParseOutput`/`CraftOutput` 类；新增 `LLMRequest.safe_dump()`（`exclude={"output_schema"}` + `output_schema_sha256`）供日志/哈希使用，禁止对 LLMRequest 直接 `model_dump()`。
- **理由**：仅 7 处引用（2 个 Agent + 1 测试 + 请求模型），无序列化/哈希依赖；对齐第 12 章类型口径，为 021 的「客户端内修复」铺路。
- **被否**：保持 dict（留下文档-实现漂移）；`alias="schema"`（mypy strict 下构造参数不匹配，需额外 plugin 配置）。
- **回写落点**：第 12 章 §12.2 `output_schema` 类型与说明、第 21 章 §21.2.4 伪代码。

### D-M5-4 配置形态：Settings + JSON（议题 M5-D4）

- **决策**：密钥/开关/单机单部署参数走 `Settings`（环境变量/`.env`，`SecretStr`）；模型分级/供应商回退链/价格表用**包内 JSON**（`src/textgame/llm/config/llm.default.json`，标准库 `json` + Pydantic 校验，可用 `TEXTGAME_LLM_CONFIG` 覆盖路径），020 起落地。**不引入 PyYAML**（uv.lock 中 pyyaml 仅为 litellm 传递依赖，pyproject 未声明；`tomllib` 需 Py≥3.11 而项目锁 3.10）。
- **被否**：A) 照第 12 章 `config/llm.yaml`（触发新增依赖红线）；B) 全部环境变量（回退链/价格表不适合 env 表达）。
- **回写落点**：第 12 章 §12.4「config/llm.yaml」→「config/llm.json（包内默认 + 路径覆盖）」；第 16 章 §16.5 环境变量表补 `TEXTGAME_LLM_ENABLED`；第 19 章注「M5 不新增依赖、PyYAML 不引入」。

### D-M5-5 测试隔离与回放（议题 M5-D5）

- **决策**：三层——① 单测注入 `FakeTransport`（零网络、可编排异常/时钟）；② 集成/出口用 `pytest-recording`+vcrpy cassette（`--record-mode=none`，缺 cassette 即 fail、绝不回退真实网络；cassette 存 `tests/fixtures/cassettes/`，录制仅本机手动）；③ 真实 API 只允许手动 `python -m textgame.llm.smoke`，不进 CI。`tests/conftest.py` autouse socket 禁网 guard 保持不变。
- **依赖**：`pytest-recording>=0.14.1` 已在 dev 依赖；vcrpy 9.4.2（Py≥3.11）/8.5.0（Py3.10）在 uv.lock，含 httpx patcher，**无新增依赖**。
- **回写落点**：第 18 章 §18.5/§18.6 注 cassette 边界与记录纪律（脱敏授权头，不录密钥/玩家原文）。

### D-M5-6 组合根与真实接线边界（议题 M5-D2/M5-D6）

- **决策**：019 交付 `llm/factory.py::build_llm_client(settings)` + `Settings.llm_enabled`（默认 **false**）：false → `DisabledLLMClient`（零联网、行为与 M4 默认装配一致）；true → `LiteLLMClient`，缺 key/配置非法**构造期 fail-fast**。`main.py` 改为经工厂获取**同一** `llm_client` 实例（解析 Agent 与 CraftAgent 共享），不接真实调用。真实 LLM 的解析/叙事/角色/裁判接线 = **M5 出口附加项**，不在 019–024 切片内。
- **理由**：既保住「默认离线、M2–M4 golden 零回归」，又让「切换点」从第一切片起被测试覆盖（M4 教训：文档说接、代码没接）；组合根只改工厂调用一处，风险最小。
- **被否**：纯 A（019 不改装配 → 切换点无验证）；B（019 即默认切真实 → 回退链/预算未就绪，成本与可用性风险）。
- **回写落点**：第 12 章 §12.1/§12.2、第 16 章 §16.1（llm 文件清单）/§16.5、第 17 章 §17.2 M5 备注。

### D-M5-7 已知问题归属（议题 M5-D7）

| 编号 | 问题 | 归属 | 关闭条件 |
| --- | --- | --- | --- |
| D-M4-15 | 并发同 `Idempotency-Key` 瞬时响应不一致（只提交一次） | **M5 治理项 G-1，owner=022-cost-budget**（幂等/持久化治理同批）；019 不实现 | `tests/integration/test_idempotency_concurrency.py` 并发同键响应一致（或显式 `CONFLICT` + 零额外写入），PROGRESS 状态改「已关闭」 |
| P2-4 | 生产解析器不产 free 制造意图 | (a) `tool_whitelist` 由 registry 派生 + `DeterministicIntentParser` free 分支 → **M5 出口附加项「解析接线」前置**；(b) 打开 `TEXTGAME_FREE_CRAFT_ENABLED=true` → **出口前置**（晚于 pre-queue 迁移） | 生产 HTTP free 意图端到端用例全绿 + 第 13/17 章回写 |
| D-M4-3 | 自由制造生成调用在 `CraftService` 内（pre-queue 迁移） | **非 M5 硬前置**；写为「打开自由制造开关前」硬门 + fail-closed 守卫 | `Resolver` 路径下 generator 调用计数 0 的用例 + pre-queue 迁移回写；未完成前生产保持 `free_craft_enabled=false` |

### D-M5-8 决策落档与切片纪律（议题 M5-D8）

- **决策**：新建 `docs/decisions/`（本文件 + `README.md` 规范 + M4 回填）；每切片**同一提交/PR** 强制四件套：`docs/progress/0NN-YYYY-MM-DD-<topic>.md`（≤60 行、第 22 章四节）、`PROGRESS.md` §1/§3/§4/§5/§7 同步、涉及契约的章节回写、实测数字/证据（命令+输出+日期，`.work` 不得作为唯一证据）。决策内容只在本目录维护，章节/PROGRESS 只引用编号。
- **回写落点**：第 22 章 §22.7 补一句「ADR 存放 `docs/decisions/`，PROGRESS §5『决策』列填 `D-M5-x`」；第 20 章 §20.4 复用本目录。
- **M5 红队出口判据（024/出口）**：样本入库 `tests/fixtures/redteam/inputs.jsonl` ≥50 条（第 18 章八类 + LLM 特有 4 类：幻觉工具名/夹带数值字段/prompt 注入型状态改写/超长深嵌套），入口 `uv run pytest tests/security -k redteam -q`；判据 = 0 未校验写入（`events==[]` 且 version/last_seq/state_hash 三不变）+ 0 真实网络 + 计数与类别覆盖写进进度文档。

### D-M5-9 M5 前到期承诺处置（新增，议题 N-9）

| 编号 | 文档承诺（位置） | 处置 |
| --- | --- | --- |
| C-1 | 第 18 章 §18.7「覆盖率 ≥80% 作为 M2 起门禁」 | **M5 末启用**：019 起采集基线（`uv run pytest --cov=textgame --cov-report=term-missing -q`，数字记入进度文档），M5 出口前在 CI/pytest addopts 落 `--cov-fail-under=80`（若基线不足先补测试或按实测校准阈值并回写本章） |
| C-2 | 第 22 章「进度文档 CI 校验」 | **M5 末实现**：把 `.work` 中已验证的 `rebuild_doc.py verify` / `check_doc.py` 提炼为入库脚本（`scripts/check_docs.py`）并在 CI 增一步；019–024 期间以本地脚本执行 |
| C-3 | 第 16 章「`python -m textgame.persistence.replay` 回放 CLI（M5 前评估）」 | **改口径**：回放实现口径 = pytest 内 `tests/replay`（禁止 snapshot-update）；CLI 作为可选运维工具**后移 M6 前评估**并回写第 16 章 |

## 3. 第 12 章契约 → 切片映射（防止只按第 17 章一行目标实施而漏章内要求）

| 第 12 章要求 | 落点切片 | 说明 |
| --- | --- | --- |
| 12.1 唯一 `LLMClient` 入口 / SDK 只在 `llm/` | 019 | 已满足：`litellm` 仅 `providers.py`（AST 门禁用例） |
| 12.2 `LLMRequest` 完整字段 + `LLMClient` 接口 + 九项职责 | 019（字段/超时/重试/限流/幂等/日志/计量/回退骨架/熔断骨架） | `estimate_cost` 与价格表→022；缓存→020；TPM→022 |
| 12.3 重试所有权单一（tenacity≤3、LiteLLM `num_retries=0`） | 019 | `num_retries=0` 单测锁死；tenacity `stop_after_attempt(3)` |
| 12.4 模型分级（STRONG/ECONOMY）与供应商回退链 | 020（分级/缓存）+ 024（回退/熔断演练） | 配置形态见 D-M5-4 |
| 12.5 Prompt 模板/版本可追溯（`prompt_version`） | 019 字段 + 020/021 渲染层 | 019 由 Agent 常量传入 |
| 12.6 校验一次修复 + 确定性兜底 | 021 | 019 保持现状（D-M5-2） |
| 12.7 缓存/批量/休眠/幂等缓存 | 020（缓存、`cache_hit`）、023（批量/休眠） | 幂等复用 019 内存端口；022 持久化 |
| 12.8 成本/延迟预算、SLO 与熔断阈值 | 022（预算/成本归集）+ 024（熔断演练） | 019 只埋点（cost=0） |
| 12.9 失败降级决策树 + 四类错误码 | 019（映射表 + 逐条测试）；其余切片复用 | `ErrorCode` 不扩：429/限流/熔断→`LLM_UNAVAILABLE` |
| 结论 1–6（双方法、依赖组合、单一重试、校验兜底、批量约束、SLO） | 019 起逐条落地 | 每切片进度文档第 3 节记录 |

## 4. 019-llmclient 实施结果（2026-11-10，提交见仓库 `slice: 019-llmclient`）

- 新增：`llm/{errors,ratelimit,records,config,litellm_client,factory}.py`；改造 `llm/{client,providers}.py`、`config/settings.py`、`main.py`、`.env.example`、两个 Agent 的 `output_schema` 传参；新增 `tests/unit/test_llm_client.py`（37 例）+ `test_llm_providers.py` 扩充（异常分类/`num_retries=0`/SDK 导入边界）。
- 实测：`uv run pytest -q` → **1129 passed**（M5 前 1096 + 33 新增，零回归）；`tests/replay` **61 passed**（M2/M3/M4 = 4/5/24，零回归）；四门全绿（ruff/format/mypy 81 files/lint-imports 2 kept）。
- 语义：默认 `DisabledLLMClient`（`llm_enabled=false`）；`llm_enabled=true` 缺 key fail-fast；幂等复用/在飞判定、token 计量、结构化日志（prompt sha256，不含原文/密钥）、`LLM_UNAVAILABLE/INVALID_INPUT/INTERNAL` 映射均已单测覆盖。
- 偏差登记：D-M5-1（sync）、D-M5-2（修复链位置）、D-M5-3（`output_schema` 不用 alias）、D-M5-4（JSON 非 YAML，020 落地分层）；PROGRESS §5 同步登记。

## 5. 后移与复审

- 020-model-tier-cache：tier→model JSON 配置、`cachetools.TTLCache`、`cache_hit` 生效。
- 021-schema-repair：修复链迁入客户端/instructor 评估（必须保持一次修复语义）。
- 022-cost-budget：价格表/`estimate_cost`/按 agent/turn 归集/预算降级 + `llm_call_log` 表 + Alembic 0003 + D-M4-15 关闭。
- 023-batch-sleep：批量合并（≤4 NPC/公共感知）、休眠状态机、`agent_invocation_log`。
- 024-provider-fallback：回退链/熔断（60s/50%/5 次）故障演练 + 关闭第 20 章 Q5。
- M5 出口附加项：解析→叙事→角色→裁判 LLM 接线（cassette 回放、默认仍离线）；裁判接线需 Lead 授权改 Resolver（当前 `judgements` 被忽略、无 `needs_judge` 分支）。

## 6. 变更纪律

决策只增不改：推翻任一条目须新增 ADR（`supersedes`）→ 回写对应章节 → 重跑全量 + `tests/replay` → 更新 PROGRESS §5 与本文件索引。禁止在章节或进度文档中复制决策正文。
