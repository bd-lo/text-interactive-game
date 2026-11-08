## 第 12 章 API 调用治理：LLMClient、模型分级、校验修复与成本控制

### 本章检索关键词
- `instructor structured output pydantic retry`、`pydantic-ai github`、`LiteLLM router retry fallback cost tracking`、`tenacity wait_exponential stop_after_attempt`、`cachetools TTLCache`、`python-diskcache`、`jinja2 prompt template`、`tiktoken token count`、`langgraph durable execution checkpoint`

### 实际打开确认的仓库列表
- `567-labs/instructor`（https://github.com/567-labs/instructor）— 打开确认：许可证 MIT、最近提交 2026-11、star 约 14,102、PyPI 1.17.0 要求 `>=3.9,<4.0`（兼容 3.10）；确认要点：README「Built on Pydantic for validation」「Failed validations are automatically retried」；`instructor/__init__.py` 暴露 `from_litellm`，官方重试文档给出 `max_retries` 与 tenacity 组合示例。
- `BerriAI/litellm`（https://github.com/BerriAI/litellm）— 打开确认：LICENSE 原文为根目录 MIT、`enterprise/` 目录另附专有许可（GitHub 因此显示 NOASSERTION）、最近提交 2026-11、star 约 62,897、PyPI 1.108.3 要求 `>=3.10,<3.15`；确认要点：PyPI 描述「Router with retry/fallback logic across multiple deployments」、统一 100+ 供应商 + cost tracking；`docs.litellm.ai/docs/tutorials/instructor` 给出 `instructor.from_litellm(completion)` 用法。
- `pydantic/pydantic-ai`（https://github.com/pydantic/pydantic-ai）— 打开确认：许可证 MIT、最近提交 2026-11、star 约 21,086；PyPI 2.66.0 要求 `>=3.11`，最后一版支持 3.10 的是 2.54.0（2026-10-03）；确认要点：README 的 `output_type` 与 `RepairToolArguments()`，属于 Agent 框架而非纯校验层。
- `jd/tenacity`（https://github.com/jd/tenacity）— 打开确认：许可证 Apache-2.0、最近提交 2026-11、star 约 8,823、PyPI 9.2.1 要求 `>=3.10`；确认要点：文档确认 `wait_exponential`、`wait_exponential_jitter`、`stop_after_attempt`、`retry_if_exception_type`。
- `tkem/cachetools`（https://github.com/tkem/cachetools）— 打开确认：许可证 MIT、最近提交 2026-11、star 约 2,792；PyPI 8.0.0 要求 `>=3.11`，最后一版兼容 3.10 的是 7.3.0（2026-11-05）；确认要点：README 的 `TTLCache`/`LRUCache` 与 `@cached` 装饰器。
- `grantjenks/python-diskcache`（https://github.com/grantjenks/python-diskcache）— 打开确认：LICENSE 为 Apache-2.0（GitHub 检测为 NOASSERTION）、最近提交 2024-03（近 6 个月无提交）、star 约 2,917、Python 3.10 兼容性：PyPI 5.6.3 仅要求 `>=3`（兼容）；确认要点：LICENSE 原文与提交历史（README 未细读）。
- `langchain-ai/langgraph`（https://github.com/langchain-ai/langgraph）— 打开确认：许可证 MIT、最近提交 2026-11、star 约 44,641、PyPI 1.2.20 要求 `>=3.10`；确认要点：README 的 durable execution，回放（Replay）/检查点参考见第 8、14 章。
- `pallets/jinja`（https://github.com/pallets/jinja）— 打开确认：许可证 BSD-3-Clause、最近提交 2025-06、star 约 11,812、PyPI jinja2 3.1.6 要求 `>=3.7`；确认要点：只作为 Prompt 逻辑模板渲染使用。
- `openai/tiktoken`（https://github.com/openai/tiktoken）— 打开确认：许可证 MIT、最近提交 2026-11、star 约 19,619、PyPI 0.14.0 要求 `>=3.9`；确认要点：仅 OpenAI 系模型的 BPE 分词器。

### 12.1 结论先行
业务代码只依赖 `LLMClient`，不得出现 `import litellm`/`import openai`。LLMClient 由 LiteLLM（协议/回退/成本）、instructor（Schema 提取+一次修复）、tenacity（网络重试）、cachetools（进程内缓存）、Jinja2（Prompt 渲染）拼接而成；pydantic-ai 因需 Python ≥3.11 且与 instructor 功能重叠而放弃，diskcache 因停止维护放弃，tiktoken 首版不引入。

### 12.2 LLMClient 完整接口
接口文件：`src/textgame/llm/client.py`。
```python
from pydantic import BaseModel, ConfigDict, Field

class ModelTier(str, Enum):
    STRONG = "strong"      # 场景判定 / 冲突裁决 / 自由制造生成
    ECONOMY = "economy"    # 意图解析 / 角色对话 / 叙事包装

class LLMRequest(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    task: str                        # 任务类型：intent_parse/narration/judge/conflict_resolve/craft_generate/memory_summary/character_card_generate
    output_schema: type[BaseModel] | None = Field(default=None, alias="schema")  # 结构化输出模型；纯文本任务为 None
    agent_id: str                    # 调用者：parse/adjudicate/narrate/memory/role/craft/character_card
    turn_id: int
    idempotency_key: str             # 调用方按 (turn_id, agent_id, task, 参数哈希) 计算
    model_tier: ModelTier
    messages: list[ChatMessage]
    timeout_s: float = 20.0          # 整条链 deadline，单次尝试 ≤ 剩余时间
    cache_ttl_s: int = 0             # >0 时允许进程内缓存
    max_repair: int = 1              # Schema 修复重试次数，固定 1（全队统一）

class LLMCallRecord(BaseModel):
    request_id: str; task: str; agent_id: str; model_tier: ModelTier
    provider: str; model: str; prompt_version: str
    attempt: int; cache_hit: bool
    prompt_tokens: int; completion_tokens: int; cost_usd: Decimal
    latency_ms: int; error_code: ErrorCode | None

class LLMClient:
    async def complete_json(self, req: LLMRequest) -> Result: ...   # data={"parsed","usage","call"}
    async def complete_text(self, req: LLMRequest) -> Result: ...
    def estimate_cost(self, req: LLMRequest) -> Decimal: ...
```
上述 `complete_json(self, req: LLMRequest) -> Result` 为**全队唯一权威签名**（第 21 章附录以此为准）：字段清单见上；`task` 为任务类型、`output_schema` 为结构化模型（附录写作 `schema`，因 Pydantic v2 保留属性冲突改用 `output_schema`+alias）、`agent_id` 为调用者、`model_tier` 为模型分级、`timeout_s` 为整链 deadline。成功 `data={"parsed","usage","call"}`；失败 `error_code ∈ {LLM_UNAVAILABLE, SCHEMA_VIOLATION, INVALID_INPUT, INTERNAL}`、`events=[]`——LLM 调用不产生领域事件，但每次调用（含失败）写 `llm_call_log` 供成本归集与审计。

必须内建九项职责：总超时（整链 deadline，单次 ≤ 剩余时间）、网络重试（tenacity）、限流（按供应商 token bucket 封顶 RPM/TPM）、幂等键（Idempotency Key）去重、结构化日志（只落 prompt hash 与脱敏）、token 计量（优先供应商 usage）、按 `agent_id`/`turn_id` 双维成本归集、供应商回退链、熔断器（Circuit Breaker）。

### 12.3 重试所有权：避免三层重试叠加
| 层 | 组件 | 是否开启 | 参数 | 职责 |
| --- | --- | --- | --- | --- |
| 供应商 HTTP 重试 | LiteLLM Router | 关闭 | `num_retries=0` | 只做回退，不重试 |
| 网络/超时重试 | tenacity | 开启 | `stop_after_attempt(3)`、`wait_exponential_jitter(initial=1, max=8)`、仅对超时/429/5xx | 唯一的重试计数者 |
| Schema 修复重试 | instructor | 开启 | `max_retries=1`（共 2 次请求） | 只处理校验失败 |
| 业务降级 | 各 Agent 调用方 | 开启 | 确定性兜底（Deterministic Fallback） | 保证回合（Turn）可结束 |

结论：单次请求最多 3 次网络尝试 + 1 次修复，各层不得私自重试。

### 12.4 模型分级与供应商回退
按任务分级、不按角色分级（需求四）。场景判定、冲突裁决（Resolution）、自由制造生成用 `STRONG`；意图解析、角色对话、叙事包装用 `ECONOMY`；固定寒暄与环境描述不调用 API，直接读预生成内容库。模型名与版本**待定**：A. 单供应商双档（运维最简，推荐）；B. 双供应商同档互备（需双份 Prompt 回归）；C. LiteLLM Proxy 托管路由（多一层部署）。首版取「A + 第二供应商同档兜底」，配置写 `config/llm.yaml`，按 `model_tier` 声明 `primary/fallback` 链。

人物卡生成任务（`task=character_card_generate`，v0.2 新增）：`model_tier=STRONG`、`timeout_s=20`、`max_repair=1`、缓存不适用（同 brief 可重复生成）；幂等分两层——HTTP 幂等键用客户端 UUID `Idempotency-Key`（第 6、23 章），服务端另算 `sha256(canonical payload)`（brief/schema_version/options）用于同键异参 `CONFLICT` 与审计；成本按 `agent_id=character_card` 归集，单用户限流 5 次/分钟；失败降级为确定性模板（brief+默认属性草稿），与第 6 章 `POST /api/v1/character-cards/generate` 对齐。

### 12.5 Prompt 模板管理
模板放 `src/textgame/llm/prompts/<agent_id>/<task>@v3.j2`，`manifest.json` 记录 version/sha256/owner/changelog/适用模型。Jinja2 用 `StrictUndefined`，变量必须显式传入，禁止访问环境变量/执行表达式；玩家输入只作数据字段注入，不得拼进指令段（结构性防注入，见第 15 章）。变更须过 Code Review 与固定种子回放（Replay）回归（见第 18 章），`prompt_version` 与模板 hash 写入 `LLMCallRecord` 与 Event，可追溯每回合版本。

### 12.6 结构化输出：校验 + 一次修复 + 确定性兜底
步骤：① 原始输出 → `req.output_schema.model_validate_json`（strict、禁额外字段）；② 失败 → 把 pydantic 错误摘要回传，追加一次修正指令（instructor `max_retries=1`）；③ 仍失败 → 按 Agent 类型走确定性兜底：

| Agent | 确定性兜底 |
| --- | --- |
| 解析 Agent | 关键词/同义词解析器；仍失败 → `ActionIntent(action="unknown")` 并提示玩家重述 |
| 叙事 Agent | 模板叙事（事件事实清单 + 环境描述库） |
| 判定 Agent | RuleEngine 默认判定，不参考 LLM |
| 制造 Agent | 走 `calculate_craft_result`（第 13 章）或返回 `NOT_FOUND` |
| 记忆 Agent | 不更新记忆，保留原始对话 |
| 人物卡生成任务 | 确定性模板：brief 名称/标题 + 默认属性（固定键集合）生成草稿，标 `source=manual`、`generated_by=null`（幂等见 12.4） |

红线：未校验输出不写 Event/GameState，只记录 `SCHEMA_VIOLATION`。

### 12.7 缓存、批量合并与休眠唤醒
| 缓存层 | 内容 | 实现 | 失效 |
| --- | --- | --- | --- |
| 预生成内容库 | 固定寒暄、环境描述 | 打包进资源文件 | 版本更新 |
| 进程内缓存 | 低风险可复用响应 | cachetools `TTLCache(maxsize=1024, ttl=600)` | TTL / 模板版本 |
| 供应商前缀缓存 | 长 System Prompt 前缀 | 经 LiteLLM 透传，需按供应商实测 | 供应商策略 |

幂等缓存：`llm_call_log.idempotency_key` 唯一索引，同键成功复用结果；执行中等待或返回 `LLM_UNAVAILABLE`，不重复扣费。

串行 vs 批量：默认串行。仅「同场景公共群演 NPC」允许一次调用批量生成（每批 ≤4 个 NPC 回应，输入只含公共感知），因一次调用含多个 NPC 私有记忆会破坏第 11 章结构性防泄露；角色 Agent 的私有记忆与批量合并互斥。批量把 4 次 × 0.8 s 压到 1.2 s，但失败影响面从 1 个 NPC 扩大到 4 个，需配「单个解析失败只丢该 NPC 的回复」的降级。

休眠（Dormancy）：状态机 `DORMANT → ARMED → ACTIVE → DORMANT`；唤醒仅三种：被玩家触发、进入同场景、计划触发点到达；活跃窗口 = 本回合 + 2 回合，其余零调用。`agent_invocation_log` 断言「无唤醒即无调用」。

### 12.8 成本与延迟预算（全队权威口径）
预算口径（同第 9 章）：单回合关键路径默认 **2 次**调用（解析+叙事），最多 **3 次**（含裁判）；P95 首版 ≤8 s，M2 缓存命中目标 ≤3 s。

| 指标 | 口径 | 超限动作 |
| --- | --- | --- |
| 单回合 LLM 调用数 | 默认 2 次；最多 3 次（含裁判） | 跳过生成路径，走确定性兜底 |
| 单回合端到端 P95 | 首版 ≤ 8 s；M2 缓存命中目标 ≤ 3 s | 告警 + 缩短超时 |
| 单回合成本 | ≤ ¥0.20 | 降级到性价比模型 |
| 单会话成本 | ≤ ¥3 | 关闭自由制造生成路径 |
| 单日成本 | ≤ ¥200 | 全局只走缓存/确定性路径 |

熔断：单供应商 60 秒错误率 >50% 或连续 5 次失败 → open 60 秒 → half-open 探测 1 次；预算 80% 告警、100% 强制降级。

### 12.9 失败降级决策树
1. 输入解析失败 → `INVALID_INPUT`，不发起调用；
2. 预算/熔断超限 → 确定性兜底，不发起调用；
3. 幂等键命中成功记录 → 直接复用结果；
4. 缓存命中 → 返回（`cache_hit=true`）；
5. LiteLLM 调用（tenacity 包裹，≤3 次）→ 超时/5xx/429 → 重试；
6. 重试耗尽 → 供应商回退链 → 全部失败 → `LLM_UNAVAILABLE` → 确定性兜底；
7. 输出 Schema 校验失败 → instructor 一次修复重试；
8. 修复仍失败 → `SCHEMA_VIOLATION` → 确定性兜底或「无法处理」；
9. 状态一致性检查失败（第 15 章）→ 视为校验失败，回到第 8 步；
10. 全程写 `LLMCallRecord`（含失败与 token/成本）并告警；通过校验的数据才进入 ProposalQueue（提案队列）。

### 候选轮子表
| 仓库 | 定位 | 可复用部分 | 许可证 | 活跃度 | 建议 |
| --- | --- | --- | --- | --- | --- |
| `567-labs/instructor` | 结构化输出 + Pydantic 校验 + 失败重试 | `from_litellm`、`response_model`、`max_retries=1` | MIT | 2026-11 有提交；约 1.41 万 star | pip 依赖 |
| `BerriAI/litellm` | 多供应商统一调用、回退、成本追踪 | `Router`/`completion` 入口、usage/cost 字段 | MIT（`enterprise/` 目录专有，不使用） | 2026-11 有提交；约 6.29 万 star | pip 依赖 |
| `jd/tenacity` | 重试与退避 | `wait_exponential_jitter` + `stop_after_attempt` | Apache-2.0 | 2026-11 有提交；约 8.8k star | pip 依赖 |
| `tkem/cachetools` | 进程内 TTL/LRU 缓存 | `TTLCache`、`@cached` | MIT | 2026-11 有提交；约 2.8k star | pip 依赖 |
| `pallets/jinja` | 模板引擎 | `StrictUndefined` 渲染 Prompt 模板 | BSD-3-Clause | 最近提交 2025-06；约 1.18 万 star | pip 依赖 |
| `langchain-ai/langgraph` | Agent 编排、持久执行/检查点 | 持久执行与检查点设计（编排选型见第 8 章） | MIT | 2026-11 有提交；约 4.46 万 star | 参考设计 |
| `pydantic/pydantic-ai` | Agent 框架 + 结构化输出校验 | 输出校验-修复循环的设计思路 | MIT | 2026-11 有提交；约 2.1 万 star | 放弃 |
| `grantjenks/python-diskcache` | 磁盘持久缓存 | 跨进程缓存 | Apache-2.0 | 2024-03 后无提交；约 2.9k star | 放弃 |
| `openai/tiktoken` | BPE 分词器 | 仅 OpenAI 系模型的 token 估算 | MIT | 2026-11 有提交；约 1.96 万 star | 放弃 |

复用判断与拼接边界：LiteLLM 只负责「供应商协议统一 + 回退 + usage/cost 采集」，不得让它持有业务重试语义；instructor 只负责「Schema 提取 + 一次修复」，不得让它写状态；tenacity 只包在网络调用外层；cachetools 只做进程内缓存，跨进程幂等交给数据库唯一键；Jinja2 只渲染已通过审查的模板。拼接后状态权威仍唯一属于 GameEngine：LLMClient 的返回值永远是「候选数据」，失败模式统一收敛为 `Result.error_code`，不向上抛裸异常。

运行时依赖锁定（以下五个包只允许出现在 `src/textgame/llm/` 内部，架构门禁见第 16 章；第 19 章依赖清单据此补齐）：`litellm>=1.108,<2`、`instructor>=1.17,<1.18`、`tenacity>=9.2,<10`、`cachetools>=7.3,<8`（3.10 兼容上限为 7.3.0）、`jinja2>=3.1.6,<4`。若项目后续把最低版本提到 3.11，可解锁 cachetools 8.x 与 pydantic-ai，但本文件按 3.10 给结论。

### 本章结论
1. 业务代码只允许调用 `LLMClient.complete_json` / `complete_text`，所有 SDK（litellm/openai）只能在 `src/textgame/llm/` 内出现。
2. 依赖组合固定为 LiteLLM + instructor + tenacity + cachetools + Jinja2（均为运行时依赖，只允许在 `src/textgame/llm/` 内部 import），全部为 MIT/Apache-2.0/BSD-3-Clause，可闭源分发；LiteLLM 的 `enterprise/` 目录不得使用。
3. 重试必须单一所有权：LiteLLM `num_retries=0`，网络重试只由 tenacity 计数（≤3 次），Schema 修复只由 instructor 做 1 次，避免成本失控。
4. 校验失败一律走「一次修复 → 确定性兜底 → 无法处理」，未校验输出绝不写入状态；兜底路由按 Agent 类型固定，写入代码常量表。
5. 批量合并只对无私有记忆的公共群演 NPC 开放（每批 ≤4 个），角色 Agent 默认串行；休眠策略用 `agent_invocation_log` 做可审计断言。
6. 第一版预算按「单回合默认 2 次、最多 3 次调用；P95 首版 ≤8 s（M2 缓存命中目标 ≤3 s）；单回合成本 ≤¥0.20」设 SLO，实测两周后校准，并把「模型名/供应商」以配置化「待定」项留给 M5。
