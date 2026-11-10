# 021 · 2026-11-10 · M5 API 调用治理 · schema-repair

> 切片目标一句话：定稿结构化输出修复链归属（**D-M5-2 终局：保持 `llm/validation.py`**，不迁 `LiteLLMClient`、不启用 instructor 运行路径、零新增依赖），并补齐「一次修复 → 确定性兜底 → 零写入」验收 ｜ 验收命令：`uv run pytest tests/integration/test_repair_fallback.py -q` ｜ 里程碑 M5（5/6）｜ 编号区间 019–024 ｜ 决策：D-M5-2（终局）、D-M5-1

## 1. 目标与验收命令

- 目标（第 12 章 §12.3/§12.6、D-M5-2）：① 归属复核结论落地——修复链保留在 `src/textgame/llm/validation.py::complete_json_with_repair`，由 `IntentParseAgent`/`CraftAgent` 显式调用；**不迁入 `LiteLLMClient`、不启用 `instructor` 运行路径**（迁入需把 FakeLLM 重写为 transport 级替身、回归面大，M5 无此需求）；② 修复次数上限固定 1（共 2 次请求，`max_repair=1`、修复请求 `max_repair=0`）不变；③ 验证失败路径「恰好一次修复 → 确定性兜底 → 未校验输出零写入」。
- 验收命令与真实输出：
  - `uv run pytest tests/integration/test_repair_fallback.py -q` → **7 passed**
  - `uv run pytest -q` → **1278 passed in 18.42s**（第一批 1249 + 本批新增 29）；`uv run pytest tests/replay -q` → **61 passed**（逐值不变）
  - 四门：`ruff check` / `ruff format --check`（158 files）/ `mypy src/textgame`（84 files）/ `lint-imports`（2 kept, 0 broken）全绿
- 不做什么：不迁修复链、不引入 instructor 运行路径、不新增依赖、不改事件/`text`/golden。

## 2. 改动与提交

- 提交：`1bc46f1 slice: 021-schema-repair（修复链归属定稿 + 一次修复/兜底测试）`
- 文件：`src/textgame/llm/validation.py`（+6：模块 docstring 登记「唯一实现 + 最终归属」结论）、`tests/integration/test_repair_fallback.py`（325 行，7 例）。
- 关键 diff 摘要：仅模块注释（归属终局）与新增测试；无运行逻辑改动、无依赖改动、`uv.lock`/`pyproject.toml` 零 diff。
- 护栏检查：未改 `LLMClient`/Agent 调用链/事件结构/`Resolver`/narrator/golden；无密钥入库；修复次数上限断言保持。
- 契约变更：**D-M5-2 终局**（修复链归属结论，第 12 章已回写；不再有「021 再评估」悬项）。

## 3. 验证结果

| 检查 | 结果 |
| --- | --- |
| 本片测试 | `test_repair_fallback.py` → 7 passed：首次违规→**恰好 1 次修复**（修复请求 `max_repair=0`）→提交；两次违规→确定性兜底且第 3 条响应不被消费；兜底零写入（version/events/state_hash 三不变）；LLM 不可用不触发修复；m4 制造链一次修复提交 / `SCHEMA_VIOLATION` 零写入；instructor import 护栏 |
| 全量 | 1278 passed in 18.42s（1249 基线零回归，本批新增 29 含 021/024/027/CI） |
| 回放 | 61 passed（M2/M3/M4 = 4/5/24），未改 golden、未 `--snapshot-update` |
| 四门 | ruff check/format（158 files）、mypy strict（84 files）、lint-imports（2 kept）全绿 |
| 失败与回滚 | 无 |

## 4. 下一步与风险

- 下一步：**024-provider-fallback**（已交付，见 024 进度文档）→ 按 D-TEAM-2 顺序 **023-batch-sleep**（M5 最后一个切片）。
- 风险：修复链为「Agent 显式调用」模式，`LLMClient` 不内建修复（终局）；若将来迁移到客户端/instructor 须新增 ADR 并重写 FakeLLM 回归面（当前明确不做）。
- 需要人工决策：无（D-M5-2 已定案）。
