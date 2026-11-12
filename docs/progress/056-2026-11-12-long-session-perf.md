# 056 · 2026-11-12 · N4 · 长会话性能基线

> 切片目标一句话：给「一局玩很久后是否变慢/涨内存/回放仍一致」建立**可执行、可回归**的离线基线——
> 脚本跑 120 回合 + 601 事件并输出 JSON，测试用宽松阈值钉住数量级。｜ 验收命令：见 §1 ｜ 里程碑 N4 ｜
> 编号区间 049–056 ｜ 执行/验收：release-tech ｜ 文档：[long-session-perf](../design/long-session-perf.md)

## 1. 目标与验收命令

- 目标（≤90 分钟）：`scripts/perf-long-session.py`（生产同构装配 + 固定种子 + 离线模板叙事 + 临时 SQLite 库）
  跑 120 回合/601 事件，采集总耗时、每回合耗时、tracemalloc 峰值、事件数、`state_hash` 与回放一致性；
  `tests/integration/test_long_session_perf.py` 在全量 pytest 内以宽松阈值回归（≤60s）。
- 验收命令/断言（先定后写）：
  - `.venv\Scripts\python.exe -m pytest -q tests/integration/test_long_session_perf.py` → **2 passed（≈12–14s）**
  - `.venv\Scripts\python.exe scripts/perf-long-session.py --turns 120 --out .work/n-batch/perf-056.json`
    → 120/120 回合、601 事件、checks 全 true
  - `.venv\Scripts\python.exe -m ruff format --check scripts/perf-long-session.py tests/integration/test_long_session_perf.py` → 通过
  - `.venv\Scripts\python.exe -m ruff check scripts/perf-long-session.py tests/integration/test_long_session_perf.py` → 通过
  - `.venv\Scripts\python.exe -m mypy src/textgame` → 不回归（本切片未改 `src/`）
- 不做什么：不改运行时代码/事件/`GameState`/golden；不写 `data/textgame.db`（临时库跑完删除）；不新增依赖；
  不跑全量 pytest（按任务约束由 Lead 集成时跑）；不做性能优化（只建立基线 + 回归门禁）。

## 2. 改动与提交

- 文件：`scripts/perf-long-session.py`（新）、`tests/integration/test_long_session_perf.py`（新）、
  `docs/design/long-session-perf.md`（新）、本进度文档
- 提交：未提交（工作区留给 Lead 统一提交）；契约变更：无
- 关键实现：复用生产 M3 多角色装配（SQLite 仓储/`GameEngine`/Resolver/Audit/模板叙事/`DisabledLLMClient`），
  仅把 NPC 计划表扩展为覆盖 120 回合（同一 `PlanStep` 契约，2 名拾荒者×2 条移动/回合）→ 每回合 5 条事件；
  脚本输出 JSON（环境/命令/会话/回放/阈值/checks），测试通过 importlib 复用同一实现、避免口径漂移。
- 护栏检查：零 `src/`/`web/` 改动；事件/状态/golden 零改动；临时库不落生产路径；未改 `pyproject.toml`/`uv.lock`。

## 3. 验证结果（本机实测，2026-11-12，HEAD `553d0a0`）

- 回归测试：`pytest -q tests/integration/test_long_session_perf.py` → **2 passed in 11.36–14.10s**（三次实测；120 回合基线 + 8 回合确定性；
  `slow` 标记，vibe 内循环 `-m "not slow"` 可排除）。
- 长会话（`.work/n-batch/perf-056.json`，含 tracemalloc）：**turns 120/120、events 601（5/回合）、total 8.47–9.57s、
  per-turn mean 70–79ms / p50 58–81ms / p95 115–140ms / max 152–160ms、峰值 tracemalloc 3.83MB、
  DB 1,921,024B、final version/turn/last_seq 121/121/601**。
- 回放一致性：**601 条 / 0.0097–0.0113s（≈0.016–0.019s/1000 事件）**；`seq_continuous=true`、回放重建 `state_hash` == 实时投影哈希；
  `state_hash=2af5fd32fff10ad8…`（固定种子 42 可复算）。
- 增长观测：前 20 回合 46–58ms → 后 20 回合 110–155ms（**2.39–3.35×**，含追踪开销），对应「每回合全量读事件历史」的既有
  O(n) 行为，归 `R-T4` 观测（不在本切片优化）。
- 质量门：ruff format/check 通过（2 文件）；`mypy src/textgame` → **Success: no issues found in 117 source files**（本切片零 `src/` 改动，集成时复跑）。
  同日同口径重复运行总耗时 8.00–9.57s，机器被并行任务占满时 21.15s（阈值已留 ≥5 倍余量）。
- 失败与回滚：无；回滚 = 删除新增 4 个文件（`slow` 测试不影响旧基线）。

## 4. 下一步与风险

- 下一步：058 portable-package-eval（本批）；Lead 集成时回填 PROGRESS §3/§4 与全量 pytest；发布前可用 `--strict` 复跑。
- 风险/开放问题：`R-T4`（事件溯源回放/存储）→ 本切片给出观测口径；120 回合 2.39–3.35× 增长 + 601 事件回放 0.0097–0.0113s，
  距触发信号（1000 回合 >30s / 事件表 >5GB）很远，但真实玩家单存档 >500 回合时应复评「事件尾部读取/快照」方案。
- 需要人工决策：无（阈值宽松、只防数量级退化；优化另立切片）。
