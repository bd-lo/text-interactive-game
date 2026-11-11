# 037 · 2026-11-11 · N2 · 流式叙事阶段 1 设计与可行性评估

> 切片目标：为「流式叙事阶段 1」定协议/端点/客户端治理/前端降级与验收口径，并用真实供应商探针证明
> 「JSON 叙事增量提取」可行。**本切片只交设计与证据，不改运行时代码**（实现排 038–040）。
> 编号区间：N2 037–044 ｜ 决策：[D-N2-1..N2-8](../decisions/2026-11-11-streaming-phase1.md) ｜
> 设计：[docs/design/narrative-streaming.md](../design/narrative-streaming.md) ｜ 路线图 D-N4

## 1. 目标与验收命令

- 目标：在不放宽「唯一写入者/CAS/幂等/未校验文本不进状态/默认离线零变化/无 EventSource」红线的条件下，
  给出可实现的阶段 1 方案与工作量估计；用实测数据回答「值不值得做」。
- 验收命令（先定后写）：
  - `uv run pytest -q` → 数字不变（本切片零代码变更）
  - `uv run python scripts/check_docs.py` → PASS
  - 探针（真实 DeepSeek，`.work/` 不提交）：`stream_probe.py` 与 `stream_json_probe.py` 输出首 chunk /
    首个可提取前缀 / 总耗时 / 提取一致性
- 不做什么：不改 `POST /turns`、不改 `TurnResult`/事件/`GameState`、不实现 038–040、不引入 async 重构。

## 2. 结论（评估）

1. **可行**：真实 `deepseek-flash` 流式下，JSON 叙事的增量提取结果与最终文本**完全一致**；
   首内容 chunk **0.66s**、首个可显示 narrative 前缀 **0.68s**、总耗时 **1.28s**（109 字，87 chunks）。
2. **收益明确**：把玩家「首字可见」从整段 2–8s（R-T8）提前到 **<1s**；改动只影响显示层，提交/校验/回放不变。
3. **方案选定**：JSON 流式 + 服务端增量提取（方案 A）；被否：纯文本叙事 `narrate@v4`（丢校验/需重基线）、
   假流式（现状无改善）、两段式（双倍成本）、客户端提取（未校验 JSON 进前端）。
4. **阶段 1 边界**：独立端点 `POST /api/v1/turns/stream` + NDJSON 5 事件（start/delta/replace/done/error）；
   增量只显示、最终以 `done.result`（与 `/turns` 同 DTO）为准；校验失败/中途失败发 `replace` 回退模板；
   `TEXTGAME_STREAM_ENABLED` 默认开、前端仅在 LLM 已配置时启用，失败降级普通 `/turns` **一次**。
5. **工作量**：038 后端 ≈1 人日、039 前端 ≈0.5–1 人日、040 验收/文档 ≈0.5 人日 → **2–3 人日**。

## 3. 交付内容

- ADR `docs/decisions/2026-11-11-streaming-phase1.md`（D-N2-1..N2-8，含协议表、治理顺序、回滚开关、测试策略）；
- 设计文档 `docs/design/narrative-streaming.md`（现状约束/可行性实测/5 方案对比/流程与协议示例/风险/切片拆分）；
- 真实探针脚本与证据（`.work/stream_probe.py`、`.work/stream_json_probe.py`，不提交）：
  - 纯文本：首 chunk 0.461s / 总 2.158s / 111 chunks / 140 字；
  - JSON：首内容 0.655s / 首个可提取 narrative 前缀 0.675s / 总 1.280s / 87 chunks / 83 可提取步 /
    `exact_match=true` / 109 字；
- 架构约束梳理：叙事是 JSON + 校验/一次修复链、事件只存最终文本、LLM 治理集中在 `LiteLLMClient`、
  测试禁网——因此不能直接透传 token，需「显示增量 + 最终校验文本」双通道。

## 4. 验证结果（Lead 实跑）

| 项 | 结果 |
| --- | --- |
| 全量 pytest | **2156 passed / 0 failed**（本切片零代码变更，数字与 049 一致） |
| 回放 | `tests/replay` **68 passed** |
| 质量门 | ruff format/check、mypy strict（107 files）、lint-imports（2 kept）、check_docs **PASS** |
| 真实流式探针 | DeepSeek `deepseek/deepseek-flash`：JSON 模式首内容 0.655s、首个可提取前缀 0.675s、总 1.280s、提取与最终文本一致（见 §3 数字） |
| 代码/契约 | 零运行时改动；`/turns`、`TurnResult`、事件、`GameState`、默认离线路径均未触碰 |

## 5. 已知限制/后续

1. 038 需重点验证「首 chunk 前重试 / 首 chunk 后不换供应商」「断连后回合仍提交」「幂等重放单段下发」；
2. 增量提取器对其它供应商（字段顺序/额外包裹）需 FakeStreamTransport 覆盖 + 至少 DeepSeek 真跑；
3. 前端降级必须只做一次（避免双倍扣费），取消/断线恢复留阶段 2；
4. 阶段 1 不引入 async 重构（同步 generator + 线程桥接）；阶段 2 再评估取消/重连与 SSE。
