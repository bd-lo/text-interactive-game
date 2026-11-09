# 020 · 2026-11-10 · M5 API 调用治理 · model-tier-cache

> 切片目标一句话：交付 020-model-tier-cache——`llm/config/` 分层 JSON 配置（包内默认 + `TEXTGAME_LLM_CONFIG` 覆盖）、`model_tier → (provider, model, api_base)` 分级选择、进程内 `TTLCache` 响应缓存（默认 allowlist 空=不缓存、命中 `cache_hit=true`）｜ 验收命令：`uv run pytest tests/unit/test_model_tier.py -q` ｜ 里程碑 M5（2/6）｜ 编号区间 019–024 ｜ 决策：D-M5-4 / D-TEAM-2/6（`docs/decisions/2026-11-10-m5-api-governance.md`、`2026-11-10-0450-team-direction.md`）

## 1. 目标与验收命令

- 目标（第 12 章 §12.4/§12.7、D-M5-4、D-TEAM-6）：① 配置形态 JSON-only（不引入 PyYAML）：包内默认 `src/textgame/llm/config/llm.default.json` + `TEXTGAME_LLM_CONFIG` 路径覆盖，文件/JSON/字段/分级/provider 非法一律构造期 fail-fast；② 按 `model_tier`（strong/economy）选择 `provider/model/api_base`，**JSON 为模型名/分级/价格的唯一权威**，`Settings` 只保留密钥/开关/配置路径；③ 进程内 `cachetools.TTLCache`：仅 `cache_ttl_s>0` 且 `task ∈ cache.allowed_tasks`（默认空=不缓存）生效，命中不调 transport、`cache_hit=true`/latency=0，失败不缓存，线程安全、不落盘不落日志；④ `build_llm_client` 默认离线路径不读 JSON、行为字节不变。
- 验收命令与真实输出：
  - `uv run pytest tests/unit/test_model_tier.py -q` → **23 passed**
  - `uv run pytest -q` → **1177 passed in 11.63s**（M5 前基线 1129 零回归；含并行轨新增 48 例）
  - `uv run pytest tests/replay -q` → **61 passed**（M2/M3/M4 逐值不变，未改 golden）
  - 四门：`ruff check` All checks passed；`ruff format --check` 144 files formatted；`mypy src/textgame` 84 files clean；`lint-imports` 2 kept / 0 broken
  - 覆盖率（D-M5-9/C-1 基线采集）：`uv run pytest --cov=textgame --cov-report=term -q` → **TOTAL 88%**（4814 stmts / 455 missed；新增 020/入口代码拉低 2 个百分点，仍高于 M5 末门槛 80%）
- 不做什么：不接真实 LLM/不联网；不迁修复链（D-M5-2，021 复核）；不改 Resolver/Event/GameState/ProposalQueue/narrator；不新增依赖（`cachetools==7.3.0` 已在锁内）；不做价格/成本（022）。

## 2. 改动与提交

- 契约与配置（`src/textgame/llm/`）：
  - 新增 `config/__init__.py`：`LLMConfig`/`ProviderSpec`/`TierSpec`/`CacheSpec` + `load_llm_config(path=None)`（`importlib.resources` 包内默认 → 路径覆盖；`extra=forbid`；两档必须声明且 provider 必须存在）；删除旧 `config.py` 改包，`from textgame.llm.config import LLMClientConfig` 导入路径不变（D-TEAM-6 路径冲突解法）。
  - 新增 `config/llm.default.json`：`schema_version=1`、`providers.deepseek.api_base`、`tiers.strong/economy → deepseek/deepseek-flash`（模型名仍 D-M5-4「待定」，020 只交付机制）、`cache{maxsize:1024, ttl_s:600, allowed_tasks:[]}`。
  - 新增 `cache.py`：`ResponseCache`（`TTLCache` + `threading.Lock` + 逐条 `expires_at` 精确过期、容量上界 1024）；`cache_key()` 为**内容级键**（task/agent/model_tier/prompt_version/messages/output_schema，不含 turn/幂等/超时——回合级幂等仍由 `LLMCallLog` 负责）。
  - 改 `litellm_client.py`：`llm_config` 可选注入（None=019 行为不变）；`_resolve_backend(tier)` 选 provider/model/api_base；缓存命中路径 `_finish_cache_hit` 重建 `cache_hit=true` 记录；成功路径按 allowlist 写缓存；记录中 provider/model 按分级解析。
  - 改 `factory.py`：`enabled=false` → `DisabledLLMClient`（不读 JSON）；`enabled=true` → 先缺 key fail-fast，再 `load_llm_config` 校验，构造注入 `llm_config`。
- 配置与文档：`config/settings.py` 仅新增 `llm_config_path`（env `TEXTGAME_LLM_CONFIG`，`populate_by_name=True`）；`.env.example` 增 JSON 配置说明与 019 兼容字段注释。
- 测试：新增 `tests/unit/test_model_tier.py`（23 例：配置加载/路径覆盖/非法 JSON/未知字段/缺分级/未知 provider/两档选择/api_base 覆盖/缓存启用条件/命中不调 transport/失败不缓存/过期/`len` 上限/工厂默认离线/启用缺 key）。
- 提交：`slice: 020-model-tier-cache（分层配置/模型分级/进程内缓存）`（与 OSS-1/玩家子集同一轮次按文件分组提交）。
- 契约变更：D-M5-4 落地、D-TEAM-6 裁决（JSON 权威、config 包迁移）。

## 3. 验证结果

| 检查 | 结果 |
| --- | --- |
| 单测（`test_model_tier.py`） | 23 passed：默认 JSON 合法/非法 fail-fast、路径覆盖、tier→model/api_base 选择、allowlist 默认空、命中不调 transport 且 `cache_hit=true`、失败不缓存、TTL 过期、线程安全、工厂默认离线不读 JSON、启用缺 key fail-fast |
| 全量 | 1177 passed（11.63s），1129 基线零回归 |
| 回放 | 61 passed（M2/M3/M4 = 4/5/24），未改 golden、未 `--snapshot-update` |
| 四门 | ruff check/format、mypy strict（84 files）、lint-imports 全绿 |
| 覆盖率 | 88%（新增代码拉低 2pp，仍 >80%） |
| 架构红线 | `main.py`/`api/app.py`/narrator/domain/events/golden 零 diff；默认装配 `DisabledLLMClient`；无新增依赖 |

## 4. 下一步与风险

- 下一步：按 D-TEAM-2 顺序 **022-cost-budget**（价格表/`estimate_cost`/按 agent-turn 归集/预算降级 + `llm_call_log` 表与 Alembic 0003 + D-M4-15 并发同键关闭 + C-1/C-2 到期承诺）→ 021-schema-repair → 024-provider-fallback → 023-batch-sleep → M5 出口附加项接线。
- 风险：① 默认 JSON 两档同模型（模型名仍待定，020 只交付机制）；② 缓存 allowlist 默认空 → 020 交付后生产实际不缓存，需接线切片按任务评估后逐项加入；③ `TEXTGAME_LLM_PROVIDER/MODEL/BASE_URL` 仅兼容保留，文档已标注不再驱动请求（第 12/16 章已回写）。
