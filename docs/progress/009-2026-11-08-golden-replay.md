# 009 · 2026-11-08 · M2 单场景 MVP · golden-replay

> 切片目标一句话：首个 golden 固定种子（save-42）7 回合回放用例——逐字段钉死 `state_hash`/事件 id/seq/rng_seed/文本长度，并交付注入 0 写入用例 ｜ 验收命令：`uv run pytest tests/replay -k m2` ｜ 里程碑 M2 ｜ 编号区间 005–009 ｜ 执行/验收：replay-dev（AI）/ lead（复核、提交）

## 1. 目标与验收命令

- 目标（第 17 章 §17.2 切片 009）：首个 golden 回放用例；护栏=禁止 `--snapshot-update` 掩盖回归。
- 验收命令/断言（先定后写）：
  - `uv run pytest tests/replay -k m2 -q` → **4 passed**
  - `uv run pytest tests/security -q` → **8 passed**（1 正向对照 + 7 注入）
  - `uv run pytest -q` → **636 passed**；`ruff format/check`、`mypy src/textgame`（60 files）、`lint-imports`（2 kept）全绿
  - golden 逐回合文本 114/83/81/85/82/84/81 字（80–150）；只读回合零事件、version 不变；写回合 `turn_id==version==seq` 1..7；最终 `turn=7/version=7/last_seq=7`
- 不做什么：不用 syrupy `--snapshot-update`、不做模糊比较、不改生产实现（发现缺陷只报告）。

## 2. 改动与提交

- 新增：`tests/fixtures/golden/m2_golden.json`（3,342 B；`_comment` 登记一次性生成/变更须 Lead 审核/禁止 snapshot-update）、`tests/replay/test_m2_golden_replay.py`、`tests/security/test_turn_injection.py`
- 提交：`7f0d6f8`（`slice: 009 golden-replay`）
- golden 内容：`save_seed="save-42"`；`game_created` seq=1/id=`evt_000001`/rng_seed=2288003755081153120（用 `DerivedRandom.derive_seed` 独立复算一致）；7 回合 state_hash 前 12 位依次 `1aa77bf9b008 / d6b82f3b7f5b / bd7b8bea46fe / c2479cd2277a / 236776448d93 / c1f378f9f271 / 0e0b0073d5ae`。
- 关键实现点：`test_m2_golden_seven_turn_replay` 逐回合精确断言 ok/turn_id/version/last_seq/event_ids/rng_seeds/state_hash/text_len；`test_m2_replay_state_hash_from_events_only` 只用盖章事件重建 `state_hash` + `EventLog.replay()` 校验 id/seq/rng_seed；`test_m2_golden_detects_tampered_rng_seed` 篡改 seq=4 的 rng_seed → `ReplayMismatchError(seq=4, field="rng_seed")`；`test_m2_same_seed_same_inputs_same_hash` 双 harness 逐字一致。
- 护栏检查：golden 精确断言、无模糊比较 ✅；禁止 snapshot-update（`_comment` + 无 syrupy）✅；注入用例证明恶意模型输出 0 状态写入 ✅；golden 一次性生成并人工核对 ✅。
- 契约变更（已登记 PROGRESS §5）：golden fixture 为 JSON 无注释语法，用首字段 `_comment` 承载变更纪律（不影响数据本身）。

## 3. 验证结果

- 回放：4 passed（逐回合精确回放、事件重建 `state_hash` 一致、篡改检出、同种子双实例一致）。
- 注入：7 条恶意模型输出（越权工具 `admin_override`、根级/参数级额外字段、>1000 字符参数、>200 字叙事、Prompt 注入文本、两次非 JSON）全部 `TurnResult(ok=false)`、`error_code ∈ {NOT_ALLOWED, INVALID_INPUT, SCHEMA_VIOLATION}`、事件数/version/state_hash/last_seq 零变化；正向对照证明断言非假通过。
- 全量：636 passed（619 基线 + 本切片 12 + 008 在途 5，无回归）；ruff/mypy/lint-imports 全绿。
- 失败与回滚：无回滚。过程记录：`ruff format` 被显式传入 `.json` 时会按 Python dict 重排成非法 JSON（工具坑），已用同一确定性生成器重新生成、逐字段复算一致；仓库级 `ruff format --check .` 只扫 `.py`，不受影响。

## 4. 下一步与风险

- 下一步：M2 出口独立验收（task-17）——五切片验收命令、真实 uvicorn 5 回合、红线审计、回放/注入、进度文档、Python 3.10；验收 PASS 后更新 PROGRESS 为 M2 已完成，并进入 M3 多角色（010）。
- 风险/开放问题：① golden 只覆盖确定性方案 A（默认 `DisabledLLMClient`），M5 接入真实 LLM 后需补 cassette 回放（第 18 章 §18.3）；② 未来修改 golden 必须 Lead 审核并在进度文档登记（禁止用 snapshot-update 掩盖）；③ 建议后续抽 `build_orchestrator()` 公共装配，避免测试重复 main 同构构造（非必须）。
- 需要人工决策：无新增。
