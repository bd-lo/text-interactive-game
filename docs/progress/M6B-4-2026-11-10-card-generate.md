# M6B-4 · 2026-11-10 · M6 后端前置（不占编号）· card-generate

> 切片目标一句话：交付 `POST /api/v1/character-cards/generate`（028 后端前置）——LLM 可用走 LLM 草稿生成、默认离线确定性模板兜底；限流 5 次/60s（IP+session 进程内滑窗）→ 429 + `Retry-After`；写幂等；不保存/不建局/不写卡库 ｜ 验收命令：`uv run pytest tests/integration/test_card_generate.py -q` ｜ 里程碑 M6（后端前置，不占 025–028 编号）｜ 决策：D-M6-9（模板兜底/限流口径）、D-M6-8

## 1. 目标与验收命令

- 目标（第 6 章 §6.7、第 23 章 §23.6、D-M6-1）：① `POST /character-cards/generate` 200 `{draft, validation, generated_by}`（草稿不保存、不建局、不写卡库）；② 输入校验：`brief` trim 后 1..1000 字符、`schema_version=="1.0"`、`options` 可 JSON 序列化且 ≤4KB；③ **模板兜底**：默认离线/LLM 不可用 → 确定性模板草稿（`source="manual"`、`generated_by=null`、`validation.warnings` 含 `TEMPLATE_FALLBACK`）；LLM 可用时经 `LLMClient`（`task="character_card_generate"`、STRONG、`max_repair=1`）；④ **限流**：按 `client_key`（客户端 IP + 可选 `X-Session-Id`）进程内滑窗 **5 次/60s**，第 6 次 → HTTP **429** + `OUT_OF_RANGE` 信封 + `Retry-After`（限流在幂等判定之前，重放也计数）；⑤ 幂等：必带 `Idempotency-Key`（缺键 422），同键同参回放首次响应（含失败响应），同键异参 409；⑥ 路由本地 DTO（未改 `api/schemas.py`）。
- 验收命令与真实输出：
  - `uv run pytest tests/integration/test_card_generate.py -q` → **25 passed**
  - `uv run pytest -q` → **1417 passed in 22.56s**；`uv run pytest tests/replay -q` → **61 passed**
  - 覆盖率 **88.86%**（≥80）；四门：ruff check 全绿 / format **172 files** / mypy **91 files** / lint-imports **2 kept**
- 不做什么：不保存生成草稿/不自动建局（保存走 `POST /character-cards`）；不做 028 前端（下一片）；限流不落库（进程内滑窗，单机口径）；不改既有 7 端点契约/事件结构/golden。

## 2. 改动与提交

- 提交：`3f39e01 feat: M6 卡生成端点（POST /character-cards/generate，模板兜底+限流+幂等）`
- 文件：`src/textgame/api/routes/cards.py`（+114：`CardGenerateBody`/`CardGenerateValidation`/`CardGenerateResponse`、`_client_key`、generate 路由与 429 分支）、`src/textgame/services/card_service.py`（+357：`generate_card`/`LLMCardDraftGenerator`/`GenerateRateLimiter`（可注入 clock）/模板兜底/幂等存储键 `card:generate:`）、`src/textgame/domain/character_card.py`（+75：模板兜底草稿构造）、`tests/integration/test_card_generate.py`（460 行，25 例）。
- 关键 diff 摘要：`main.py` 在 `llm_enabled=true` 时注入 `LLMCardDraftGenerator`，默认离线不注入（模板兜底）；`CardServiceError` 带 `http_status`/`retry_after`，路由把 429 写成 `Retry-After` 头；幂等复用既有 `idempotency` 表与端口。
- 已知实现偏差（**D-M6-9**）：模板兜底为默认离线口径（`source=manual`/`generated_by=null`/`TEMPLATE_FALLBACK` warning）；限流为**进程内滑窗**（多 worker 各自计数，单机首版可接受）；生成请求体 DTO 定义在路由模块（未进 `api/schemas.py`，因仅此端点使用）。
- 契约变更：新增端点（M6 后端前置 item 3 最后缺口补齐）；无既有接口破坏。

## 3. 验证结果

| 检查 | 结果 |
| --- | --- |
| 本片测试 | `test_card_generate.py` → 25 passed：模板兜底（离线/LLM 不可用，`TEMPLATE_FALLBACK` warning、`source=manual`/`generated_by=null`）；LLM 草稿路径（Fake）成功/修复/失败回退；brief 长度/空值/schema_version/options 校验；限流第 6 次 429 + `Retry-After`（窗口/剩余秒）；幂等同键复用（含失败回放）/异参 409/缺键 422；生成不写库/不建局/不产生事件 |
| 全量/回放 | 1417 passed in 22.56s（1376 基线零回归，本批新增 41）；replay 61 逐值不变 |
| 覆盖/四门 | 88.86%；ruff/format 172 files、mypy 91 files、lint-imports 2 kept 全绿 |
| 失败与回滚 | 无 |

## 4. 下一步与风险

- 下一步：**M6-028-llm-card-generate**（前端生成 UI，依赖本端点已交付 + M5 出口接线）；随后 M6 4/4，后端前置仅剩 **裁判接线（待用户授权）**。
- 风险/已知限制：① **离线「生成卡」即模板兜底**，028 前端需显著提示「当前为模板草稿、未调用 LLM」（玩家验收缺口 ①）；② 限流为进程内滑窗（多 worker 各自计数，单机可接受）；③ 生成不保存/不建局（用户需显式保存/选择开局，属契约设计）；④ 生成端点的 LLM 路径本批仅 Fake/cassette 验证，未联网真实调用。
- 需要人工决策：无（本轮）。
