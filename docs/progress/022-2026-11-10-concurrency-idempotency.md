# 022 · 2026-11-10 · M5 API 调用治理 · concurrency-idempotency

> 切片目标一句话：交付缩范围后的 `022-concurrency-idempotency`——关闭 D-M4-15/G-1（并发同 `Idempotency-Key` 回合只提交一次、后到者原样重放首结果）；成本/预算/`llm_call_log`/Alembic 0003 按 D-M5-10 不做 ｜ 验收命令：`uv run pytest tests/integration/test_idempotency_concurrency.py -q` ｜ 里程碑 M5（3/6）｜ 编号区间 019–024 ｜ 决策：D-M4-15/G-1、D-M5-7、D-M5-10（`docs/decisions/2026-11-10-022-scope-and-cost-out.md`）、D-TEAM-2

## 1. 目标与验收命令

- 目标（D-M5-10 缩范围后唯一目标）：竞态窗口为「`run()` 先查幂等表 → 载入存档 → CAS 提交 → 最后写幂等记录」；修复后同 `Idempotency-Key` 请求在**进程内按存储键串行**（`_IDEMPOTENCY_LOCK_STRIPES = 64` 固定分段锁、`zlib.crc32` 稳定哈希），失败路径再 `_replay_idempotent` 重查一次幂等表并复用首结果；不同键基本并行、锁不随键增长。
- 验收命令与真实输出：
  - `uv run pytest tests/integration/test_idempotency_concurrency.py -q` → **6 passed**（真实双线程 + 「首次 parse 阻塞 + Event 放行」放大竞态窗口，不靠 sleep 猜时序）
  - `uv run pytest -q` → **1249 passed in 18.51s**（基线 1179 + 本批新增 70）
  - `uv run pytest tests/replay -q` → **61 passed**（M2/M3/M4 逐值不变）
  - 四门：`ruff check` / `ruff format --check`（154 files）/ `mypy src/textgame`（84 files）/ `lint-imports`（2 kept, 0 broken）全绿
- 不做什么：不做成本归集/预算/价格表/`estimate_cost`/`llm_call_log` 表/Alembic 0003（D-M5-10，用户拍板连成本记录也不做）；不改叙事文本、不改事件结构、不重钉 golden。

## 2. 改动与提交

- 提交：`249b704 slice: 022-并发幂等（G-1）+ 失败提示字段（A1/A4）`（本片为 A1 部分；A4 记入 OSS-2 文档）
- 文件：`src/textgame/orchestration/turn_orchestrator.py`（+90/-1：分段锁、`_idempotency_lock_index`、`_replay_idempotent` 失败重查）、`src/textgame/llm/records.py` / `litellm_client.py`（docstring 同步 D-M5-10，无逻辑改动）、`tests/integration/test_idempotency_concurrency.py`（260 行，6 例）。
- 关键 diff 摘要：`run()` 取 `lock = self._idempotency_locks[_idempotency_lock_index(store_key)]` 后 `acquire()`，在锁内执行原 `_run_once`；失败时重查幂等表，命中则直接返回首结果，否则保留失败并走 A4 提示填充。
- 护栏检查：未改 `domain/`/Event schema/`ProposalQueue`/`Resolver`/narrator/golden；无密钥入库；无新增依赖；`uv.lock`/`pyproject.toml` 零 diff。
- 契约变更：**D-M4-15/G-1 关闭**（PROGRESS §5 状态改「已关闭」）；D-M5-10 缩范围已定案（022 不再含成本治理）。

## 3. 验证结果

| 检查 | 结果 |
| --- | --- |
| 本片单测 | `test_idempotency_concurrency.py` → 6 passed：同键串行/首结果重放/只提交一次/零额外写入/不同键并行/跨重启语义由幂等表保证 |
| 全量 | 1249 passed in 18.51s（基线 1179 零回归，新增 70 含本批其它切片） |
| 回放 | 61 passed（M2/M3/M4 = 4/5/24），未改 golden、未 `--snapshot-update` |
| 四门 | ruff check/format、mypy strict（84 files）、lint-imports 全绿 |
| 交互 | `demo --check` 7 屏字数 114/83/81/85/82/84/81 不变；`console --check` exit 0 |
| 失败与回滚 | 无；异常路径零写入（保持原子性） |

## 4. 下一步与风险

- 下一步：按 D-TEAM-2 顺序 **021-schema-repair**（修复链归属复核，D-M5-2）；C-1/C-2 到期承诺由 Lead 安排到 021/024/023 批次（D-M5-10）。
- 风险：**分段锁为进程内**——单进程部署（Q6=A）下满足 G-1；多 worker/多进程部署退化为「显式失败 + 零状态写入」，不保证跨进程串行（PROGRESS §5 D-M4-15 备注同步登记）；成本/`llm_call_log` 不再承诺（D-M5-10）。
- 需要人工决策：无（缩范围已由用户拍板并经 ADR 定案）。
