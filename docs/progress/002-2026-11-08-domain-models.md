# 002 · 2026-11-08 · M1 骨架 · domain-models

> 切片目标一句话：按第 5 章定稿落地 Character/GameState/Result/ErrorCode/Event 领域模型 ｜ 验收命令：`uv run pytest tests/unit/test_domain_models.py` ｜ 里程碑 M1 ｜ 编号区间 001–004 ｜ 执行/验收：domain-dev（AI）/ lead（复核）

## 1. 目标与验收命令

- 目标（垂直小功能，≤90 分钟）：逐字落地第 5 章 §5.2/§5.4/§5.5 的 Pydantic v2 领域模型，为 003 事件日志提供状态与事件契约；不写业务规则。
- 验收命令/断言（先定后写）：
  - `uv run pytest tests/unit/test_domain_models.py -q` → **246 passed**
  - `uv run ruff format --check .` / `uv run ruff check .` → 通过
  - `uv run mypy src/textgame` → `Success: no issues found in 25 source files`（strict，python_version=3.10）
  - `uv run lint-imports` → `Contracts: 2 kept, 0 broken`（domain 零框架依赖）
  - 独立 AST 比对：第 5 章 23 个类 + 10 个类型别名与文档逐字一致（字段名/顺序/注解/默认值/model_config/校验器）
- 不做什么：不做 EventLog（003）、不做 RuleEngine/Ruleset/检查纯函数（M2）、不做 CharacterCard DTO（M6）、不建数据库。

## 2. 改动与提交

- 文件：`src/textgame/domain/types.py`、`src/textgame/domain/results.py`、`src/textgame/domain/events.py`、`src/textgame/domain/models.py`、`tests/unit/test_domain_models.py`
- 提交：`7b3f815`（`slice: 002 domain-models`）
- 关键 diff 摘要：Event 草稿/盖章 all-or-none + ts 强制 UTC；Result `ok`/`error_code` 互斥且失败不得带 events；GameState 引用完整性校验；GameState.apply 保留 `NotImplementedError`（投影 M2/M3 落地）；ToolSpec 六字段不变量（NETWORK 拒绝、schema 根必须 object）。
- 护栏检查：字段与第 5 章逐字一致（AST 比对 PASS，零自创字段）；`domain/` 只 import 标准库 + pydantic（import-linter KEPT）；未改 Event 基线字段（第 14 章 §14.2 的四个可选字段按第 5 章 §5.9 方案 B 不进入基线，M2 事件表落地时评估）；无密钥、无新依赖。
- 契约变更：无

## 3. 验证结果

- lint / type：ruff format/check 通过；mypy strict 通过（25 源文件）；lint-imports 2/2 契约 KEPT。
- 单测：`pytest tests/unit/test_domain_models.py -q` → passed 246 / failed 0 / skipped 0（以失败分支与不变量为主，非只测 happy path）。
- 回放/集成（涉及状态时）：不适用（本切片只定义模型，无事件写入）。
- demo：模型层无 HTTP 变化；全量 `pytest -q` → 402 passed（含 003/004 切片，见对应文档）。
- 失败与回滚：无回滚。过程：文档 §5.2 整块代码按职责拆到 types/results/events 三文件（字段/校验器零改动，AST 比对证明）。

## 4. 下一步与风险

- 下一步：003 · eventlog-replay，验收命令 `uv run pytest tests/replay/test_m1_event_replay.py`。
- 风险/开放问题：第 14 章 §14.2 提到事件上可补 `event_type/applied/prev_hash/event_hash/idempotency_key`，但第 5 章 §5.2/§5.9 方案 B 明确 Event 基线不动；M2 设计 `events` 表时需决策这些字段归 Event 还是 EventLog/表层（属契约变更，须人工 review）。
- 需要人工决策：无（上述挂账至 M2）。
