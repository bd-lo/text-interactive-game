# 024 · 2026-11-10 · M5 API 调用治理 · provider-fallback

> 切片目标一句话：交付配置化供应商回退链与进程内熔断演练（第 12 章 §12.4/§12.9）——`fallbacks` 按声明顺序回退、每 provider 独立熔断（60s/50%/连续 5 次/open 60s/half-open 1 次探测）、默认 JSON 回退链为空 ⇒ 019/020 行为不变 ｜ 验收命令：`uv run pytest tests/integration/test_fallback.py -q` ｜ 里程碑 M5（5/6）｜ 编号区间 019–024 ｜ 决策：D-M5-4、D-TEAM-2

## 1. 目标与验收命令

- 目标（第 12 章 §12.4/§12.9）：① `llm/config` 新增 `FallbackSpec` / `TierSpec.fallbacks`（≤8、声明顺序即回退顺序）/ `CircuitSpec`（`window_s=60`、`error_rate_threshold=0.5`、`consecutive_failures=5`、`open_s=60`、`half_open_probes=1`）+ `LLMConfig.backends_for(tier)`；非法配置构造期 fail-fast；② `LiteLLMClient` 按尝试链逐供应商执行：超时/429/5xx/网络错误回退；400/422 → `INVALID_INPUT`、401 等不可重试错误**不回退**；③ 每 provider 独立熔断（closed→open→half_open；open 期跳过直试下一家；half-open 探测成功关闭、失败重新 open；错误率规则最小样本 4 避免小样本误开）；④ 全链耗尽 → `LLM_UNAVAILABLE` 不抛裸异常；记录实际后端；⑤ deadline/RPM/幂等/缓存/日志脱敏语义不变；**默认 JSON 回退链为空 ⇒ 019/020 行为不变**。
- 验收命令与真实输出：
  - `uv run pytest tests/integration/test_fallback.py -q` → **14 passed**（FakeTransport + 伪时钟，离线）
  - 定向回归：62 passed（含 019/020）、113 passed（含 021/006/017）
  - `uv run pytest -q` → **1278 passed in 18.42s**；`uv run pytest tests/replay -q` → **61 passed**
  - 四门：ruff check/format（158 files）、mypy strict（84 files）、lint-imports（2 kept）全绿
- 不做什么：不引入新供应商密钥/不联网真实调用；不做跨进程熔断共享；不改业务重试所有权（tenacity 唯一计数者，D-M5-3 口径不变）；成本/预算仍按 D-M5-10 不做。

## 2. 改动与提交

- 提交：`71357dd slice: 024-provider-fallback（配置回退链 + 熔断演练）`
- 文件：`src/textgame/llm/config/__init__.py`（+71：`FallbackSpec`/`CircuitSpec`/`backends_for`/引用校验扩展）、`src/textgame/llm/config/llm.default.json`（+13：`fallbacks: []`、`circuit` 默认块）、`src/textgame/llm/litellm_client.py`（+406/-107：尝试链、熔断状态机、不可重试错误分类、记录实际后端）、`tests/integration/test_fallback.py`（443 行，14 例）。
- 关键 diff 摘要：`complete_json/complete_text` 内部由「单 backend」改为「`backends_for(tier)` 尝试链」；熔断器按 `provider` 键维护进程内状态；命中缓存/幂等复用不进入回退链；全链失败统一 `LLM_UNAVAILABLE`。
- 护栏检查：默认配置行为不变（回退链空）；`LLMRequest`/事件/golden 未改；无新增依赖；日志不含密钥/Prompt 原文。
- 契约变更：无接口偏离；新增配置字段为加法（JSON 缺省 = 019/020 行为）。

## 3. 验证结果

| 检查 | 结果 |
| --- | --- |
| 本片测试 | `test_fallback.py` → 14 passed：回退顺序、不可重试错误不回退、熔断 open→half-open→恢复、连续失败/窗口错误率（最小样本 4）、open 期跳过、全链耗尽 `LLM_UNAVAILABLE`、记录实际后端、缓存/幂等命中不触发回退 |
| 全量 | 1278 passed（1249 基线零回归，本批新增 29）；replay 61 零回归 |
| 四门 | ruff check/format（158 files）、mypy strict（84 files）、lint-imports 全绿 |
| 默认行为 | 默认 JSON `fallbacks=[]`、`circuit` 默认块 ⇒ 019/020 路径与测试零变化 |
| 失败与回滚 | 无 |

## 4. 下一步与风险

- 下一步：**023-batch-sleep**（M5 最后一片，批量合并 vs 串行 + 休眠策略）；此后 M5 出口附加项（LLM Agent 接线，含解析白名单派生 P2-4(a)）。
- 风险/已知限制：① **熔断为进程内**，多 worker 各自状态、不跨进程共享；② **回退链复用单 `api_key`**（跨供应商密钥治理未做，回退到第二家仍用同一凭据）；③ 生产默认回退链为空 ⇒ 真实回退能力需接线切片显式配置；④ `liteLLM=0`/`MAX_SUBMIT_ATTEMPTS=3` 等重试所有权口径未变（tenacity 唯一计数者）。
- 需要人工决策：无（本轮）。
