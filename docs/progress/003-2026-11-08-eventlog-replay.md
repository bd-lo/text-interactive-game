# 003 · 2026-11-08 · M1 骨架 · eventlog-replay

> 切片目标一句话：EventLog 盖章/追加 + derive_seed 派生重放，证明「固定种子可复现」 ｜ 验收命令：`uv run pytest tests/replay/test_m1_event_replay.py` ｜ 里程碑 M1 ｜ 编号区间 001–004 ｜ 执行/验收：replay-dev（AI）/ lead（复核）

## 1. 目标与验收命令

- 目标（垂直小功能，≤90 分钟）：落地第 10 章 §10.3 的派生式随机（RandomSource/DerivedRandom）与第 5 章 §5.3、第 14 章 §14.2 的 `EventLog.stamp/append/read/replay`，支撑 M1 出口「1 条事件可重放」。
- 验收命令/断言（先定后写）：
  - `uv run pytest tests/replay/test_m1_event_replay.py -q` → **28 passed**
  - `uv run pytest tests/unit/test_rules_types.py tests/unit/test_derived_random.py -q` → **105 passed**
  - golden 断言：`DerivedRandom.derive_seed("demo-save-2026", 43, "player_1", "sneak_move", 50) == 853400723015425181`（与第 10 章 §10.2 示例 JSON 完全一致）
  - `uv run pytest -q` → 402 passed；`ruff`/`mypy`/`lint-imports` 全绿
  - M1 出口 demo：`.work/m1_demo.py`（本地）→ seqs `[1,2,3]`、ids `evt_000001..3`、两次不同 EventLog 实例的 `(seq,id,rng_seed)` 完全一致，`REPLAY_OK`
- 不做什么：不做 SQLAlchemy 事件表/快照（M2 008）、不做 upcaster/reducer（M2/M3）、不做 RuleEngine 判定（M2）、不做游戏逻辑直接调用随机。

## 2. 改动与提交

- 文件：`src/textgame/domain/rules/__init__.py`、`domain/rules/types.py`、`domain/rules/dice.py`、`config/rng.py`、`domain/events.py`（追加 EventLog）、`tests/replay/test_m1_event_replay.py`、`tests/unit/test_rules_types.py`、`tests/unit/test_derived_random.py`
- 提交：`cc3eef7`（`slice: 003 eventlog-replay`）
- 关键 diff 摘要：`EventLog.stamp` 分配全局 `seq`/`id=f"evt_{seq:06d}"`/`rng_seed`（已掩码，不二次掩码）/UTC `ts`，并做一次 JSON 可序列化校验；`append` 先全量校验后一次性写入（零部分写入）；`replay` 逐条重算种子，首个偏差抛 `ReplayMismatchError(seq, field, expected, actual)`。
- 护栏检查：`rng_seed ∈ [0, 2**63-1]`（BIGINT 上界）；`random` 仅出现在 `domain/rules/dice.py`（有 AST 扫描单测）；domain 不依赖框架（lint-imports KEPT）；Event/EventType 基线零改动。
- 契约变更：无（EventLog 为新增内部能力；接口形状见第 5、14 章）

## 3. 验证结果

- lint / type：ruff format/check 通过；mypy strict 通过；lint-imports 2/2 KEPT。
- 单测：replay 28 passed + 规则值对象/随机 105 passed；全量 `pytest -q` → 402 passed / failed 0。
- 回放/集成（涉及状态时）：固定 `save_seed="save-42"`，stamp→append→replay 全一致；篡改 `rng_seed` 被 `ReplayMismatchError` 检出；NaN/Inf 与不可 JSON 对象在 stamp 被拒。
- demo：M1 出口 demo 见 §1（`REPLAY_OK events=3`）；uvicorn `/healthz` 200 `{"status":"ok"}`（M1-001 能力无回归）。
- 失败与回滚：无回滚。有意偏离 1 处：JSON 校验用 `model_dump()`（python 模式）而非 `mode="json"`——Pydantic v2 默认 `ser_json_inf_nan="null"` 会把 NaN 静默转 null，使 `allow_nan=False` 失效；改为 python 模式后可正确拦截，代码内有注释。

## 4. 下一步与风险

- 下一步：004 · ci-license-gate，验收命令 `uv run lint-imports && uv run ruff check . && uv run pytest -q`。
- 风险/开放问题：EventLog 为 M1 内存实现（无持久化/哈希链/upcaster），M2-008 落 SQLite 事件表时必须保持「seq 全局连续 + stamp 语义」不变；`stamp` 用 `datetime.now()`，回放不依赖时间戳（只审计展示），M2 需保证回放断言不引入时间耦合。
- 需要人工决策：无。
