# 长会话性能基线（Long-Session Perf Baseline）

> 归属：N4 技术/发布轨 **056 long-session-perf**（第 17 章 §17.9.2）｜ 状态：**基线已建立（2026-11-12）** ｜
> 基线 commit：`553d0a0` ｜ 脚本：`scripts/perf-long-session.py` ｜ 回归测试：`tests/integration/test_long_session_perf.py` ｜
> 进度文档：[056](../progress/056-2026-11-12-long-session-perf.md) ｜ 关联风险：`R-T4`（事件溯源回放变慢/存储膨胀）。

## 0. 结论先行

1. **120 回合 / 601 事件的离线确定性长会话，本机（含 tracemalloc 开销）总耗时 ≈ 8.0–9.6s、单回合 p95 ≈ 98–140ms、
   峰值 traced 内存 ≈ 3.83MB**；事件日志回放 601 条仅 ≈ 0.007–0.011s，回放重建状态哈希与实时投影**完全一致**。
2. **没有内存泄漏信号**（峰值 3.83MB 远低于阈值；120 回合内不随事件数线性增长——峰值主要由当前回合的事件尾部读取决定）。
3. **观察到单回合耗时随事件日志增长而上升**：前 20 回合平均 46–58ms → 后 20 回合 110–155ms（**2.39–3.35×**，含 tracemalloc）。
   这是「每回合全量读事件历史」的既有 O(n) 行为，属 `R-T4` 的**观测口径**，不改变本轮实现；触发升级的信号见 §7。
4. **回归门禁**：`tests/integration/test_long_session_perf.py` 用宽松阈值（总耗时 <60s、p95 <3000ms、峰值 <512MB、
   事件 ≥500、回放哈希一致）钉住数量级，全量 pytest 内约 11–14s。

## 1. 口径与装配（为什么要扩展 NPC 计划表）

- **目标**：单存档跑 **120 回合（100+）**、事件日志 **601 条（500+）**，采集总耗时/每回合耗时/峰值内存/事件数/
  `state_hash`，并做「事件日志回放 → 纯事件重建状态 → 哈希一致」校验。
- **装配**（与生产 `main.py` 组合根同构，除计划表数据外无差异）：
  `SQLiteRepository`（**临时文件库**，不写 `data/textgame.db`）→ `GameEngine`（唯一写入者/CAS/幂等不变）→
  `QueryService`/`ActionService`/`ToolGate`/`RuleEngine`/`ActorDirectory`/`Resolver`（M3 多角色链路）→
  `IntentParseAgent`（`DisabledLLMClient` 强制离线）+ `TemplateNarrator`（确定性模板叙事）+
  `AuditService` → `TurnOrchestrator`；固定 `seed=42`（`save-42`）。
- **为什么扩展计划表**：生产 `M3_NPC_PLANS` 只覆盖 10 回合（M3 golden 口径），单存档 120 回合里 NPC 只在前 10 回合动作，
  事件数不足 500。脚本用**同一 `PlanStep` 契约**生成覆盖 2–121 回合的计划：两名拾荒者每回合各 2 条 `move_to`
  （先北后南，同回合回到入口）→ 玩家 `talk_to` 1 + NPC 2×2 = **5 条事件/回合**；601 = 开局 1 + 120×5。
  这是**压测数据**，不是生产行为变更：不新增事件类型、不改 reducer/状态/契约，生产 `main.py` 的计划表长度不变。
- **为什么玩家每回合 `talk_to`**：入口房间与老哨兵同屋，确定性解析下每回合稳定产生 1 条 `DIALOGUE_EXCHANGED`，
  不会像「拿物品/移动」那样耗尽场景资源；保证 120 回合全是**真实提交**（非只读/失败回合）。

## 2. 环境与命令

- 机器：Windows 11（`Windows-11-10.0.22631-SP0`）、AMD Ryzen 7 7840HS（8C/16T）、56GB RAM；
- Python `3.12.16`（`.venv`）、SQLite `3.53.1`、项目版本 `0.3.0`、基线 HEAD `553d0a0`；
- 命令（本机实测）：

```powershell
# 长会话基线（默认 120 回合；JSON 证据写 .work/，不入库）
.venv\Scripts\python.exe scripts/perf-long-session.py --turns 120 --out .work/n-batch/perf-056.json
# 阈值不达标时 exit 1（本地/发布前可用）
.venv\Scripts\python.exe scripts/perf-long-session.py --turns 120 --strict
# 回归测试（全量 pytest 内也会执行；约 11–14s）
.venv\Scripts\python.exe -m pytest -q tests/integration/test_long_session_perf.py
```

## 3. 本机实测基线（2026-11-12）

命令：`perf-long-session.py --turns 120 --out .work/n-batch/perf-056.json`（tracemalloc 默认开启）。

| 指标 | 实测 | 说明 |
| --- | --- | --- |
| 回合成功/请求 | **120/120** | 全部 `ok`，无失败回合 |
| 事件总数 | **601** | 开局 1 + 120×5（每回合 5 条，min=max=5） |
| 总耗时 | **9.31s** | 同日 6 次同口径运行 8.00–9.57s（中位 ≈8.7s）；机器被并行任务占满时一次 21.15s（慢机余量证据） |
| 每回合耗时 | mean **70–79ms** / p50 **58–81ms** / p95 **115–140ms** / max 152–160ms / min 37–39ms | **含 tracemalloc 开销**（见下）；同日 6 次运行区间 |
| 单回合增长 | 前 20 回合 **46–58ms** → 后 20 回合 **110–155ms**（**2.39–3.35×**） | 每回合全量读事件历史导致的 O(n) 增长，归 `R-T4` 观测 |
| 峰值内存（tracemalloc） | **3.829MB** | 6 次运行 3.826–3.831MB，无泄漏信号 |
| 临时库体积 | **1,921,024B（≈1.83MiB）** | ≈3.2KB/事件；跑完即删 |
| 最终版本/turn/last_seq | **121 / 121 / 601** | 与「120 个成功回合」一致 |
| 最终 `state_hash` | `2af5fd32fff10ad8e758dda0df73fd3761cf433452f653bb4c0ab009b0d88b54` | 固定种子可复算（6 次运行一致） |
| 事件回放 | **601 条 / 0.0094s**（≈0.016s/1000 事件） | `EventLog.replay` + 纯事件重建状态；6 次运行 0.007–0.011s |
| 回放一致性 | `hash_matches_live_state=true`、`seq_continuous=true` | 回放哈希 == 实时投影哈希 |

**tracemalloc 开销标定**（同一脚本装配、同 120 回合）：

| 运行方式 | 总耗时 | 说明 |
| --- | --- | --- |
| 纯墙钟（探针，未开 tracemalloc） | **4.0s** | 真实玩家体感口径（本机） |
| 开 tracemalloc（本基线） | **8.0–10.8s** | 约 **2–2.7×** 追踪开销；§3 表格数字均为此口径 |

> 说明：本基线以「可复现的内存/事件口径 + 宽松阈值」为目的，不追求微基准精度；机器负载会显著影响绝对耗时
> （同代码 8.0s ↔ 21.1s），因此阈值刻意留 ≥5 倍余量。JSON 中的 `per_turn_ms`/`latency_growth` 可直接复查增长趋势。

## 4. 阈值与依据（`tests/integration/test_long_session_perf.py`）

| 断言 | 阈值 | 依据 |
| --- | --- | --- |
| 总耗时 | **< 60s** | 本机含 tracemalloc 8.0–9.6s（慢机 21s）→ 留 ≈3–7 倍余量；只防数量级退化 |
| 单回合 p95 | **< 3000ms** | 本机 p95 115–140ms → ≈21–26 倍余量；CI 慢机/负载抖动安全 |
| tracemalloc 峰值 | **< 512MB** | 本机 3.83MB → ≈130 倍余量；只抓无界缓存/全量累积类泄漏 |
| 事件数 | **= 601（且回放 ≥500）** | 固定脚本 + 固定计划表 → 事件数稳定可复算；056 验收「回放 500+ 事件」 |
| 回放 | **601 条、seq 连续、重建哈希 == 实时哈希** | 事件溯源权威；哈希不一致即回归 |
| 哈希稳定 | 固定种子在两个独立临时库重跑 8 回合 → `state_hash` 完全一致 | 防随机性/时钟/ID 泄漏进状态 |
| 测试自身耗时 | **≤60s**（实测 ≈11–14s） | 056 验收「全量 pytest 内 ≤60s」；标记 `slow`，vibe 内循环 `-m "not slow"` 可排除 |

脚本默认跑完打印一行摘要并返回 0；`--strict` 时阈值不达标 exit 1（供发布前手工门禁用）。

## 5. 复跑方法

1. 默认 120 回合：见 §2 命令；JSON 字段：`environment`/`command`/`session`/`replay`/`thresholds`/`checks`/`ok`；
2. 换规模：`--turns 60`/`--turns 200`（脚本按同样比例生成计划表；`--turns` 越大 per-turn 增长越明显，可直接观察 `latency_growth`）；
3. 换种子：`--seed 7`（会改变 `state_hash`/事件数据，写进证据时注明 seed；默认 42 与 M2/M3 golden 同源）；
4. 复跑后对比：`total_seconds`/`per_turn_ms.p95`/`peak_tracemalloc_mb`/`events_total`/`state_hash` 五项；
   事件数或哈希变化先查是否有意改动（事件结构/场景/演练脚本），无解释的变化按回归处理。

## 6. 与既有性能基线的分工

| 基线 | 层级 | 规模 | 关注点 |
| --- | --- | --- | --- |
| `tests/integration/test_perf_baseline.py`（R2 QA-1） | HTTP（TestClient）→ 编排 | 3 seed × 10 回合（30 样本） | 单回合 p95<200ms、单存档 DB 增量 ≤64KiB、同幂等键并发只提交一次 |
| 本基线（N4-056） | 编排器（生产同构装配，SQLite 临时库） | 1 seed × 120 回合、601 事件 | 长会话总耗时/p95、tracemalloc 峰值、事件回放吞吐/哈希一致、单回合增长趋势 |

两者互补：前者守「单回合体感/容量」，后者守「玩久了不变慢/不涨内存、回放仍一致」。

## 7. 风险与归属（`R-T4` 事件溯源回放变慢/存储膨胀）

- **观测**：601 事件回放仅 0.007–0.011s，存储 ≈3.2KB/事件；距离 R-T4 触发信号（1000 回合回放 >30s 或事件表 >5GB）极远；
  真正需要关注的是**每回合耗时随事件数增长**（本基线 120 回合 2.39–3.35×）：`TurnOrchestrator` 多角色路径每回合
  `read_events(game_id)` 读全量历史（世界书激活/回合建议需要），事件越多单回合越慢。
- **定性**：这是**已知设计取舍**（世界书定时效果/激活上下文要逐回合历史），不是本切片引入的问题；120 回合量级
  绝对耗时仍远低于玩家可感知门槛（含追踪开销 p95 115–140ms）。
- **升级条件（触发再评估）**：真实玩家单存档 >500 回合；或 `latency_growth.growth_ratio` 在 120 回合显著超过 3×
  且绝对 p95 接近 200ms；或 R-T4 的 1000 回合 >30s 被触发。届时可评估「只读事件尾部/快照后再取增量」的优化
  （不改事件结构；需单独立项与回归）。
- **归属**：R-T4（技术风险，第 20 章）+ N4 056 的观测口径；本切片只建立基线与回归测试，**不做优化**（056 护栏：只读压测、
  不写生产库、不改运行时代码/事件）。

## 8. 局限

1. 单机单次测量：绝对数值受 CPU 频率/后台负载影响（已见 8.0s ↔ 21.1s 波动），结论以「数量级 + 宽松阈值」为准；
2. NPC 计划表是压测数据（覆盖 120 回合的确定性移动），不模拟真实玩家内容差异；真实 LLM 叙事路径不在本基线内
   （离线模板叙事；LLM 延迟见 R-T8/流式 N2-037–040）；
3. `tracemalloc` 计入的是 Python 分配峰值，不含 SQLite 原生内存；玩家体感耗时看 §3 的纯墙钟标定；
4. 未覆盖多 worker/多进程部署（本项目单进程，R-04 已知限制）。

## 9. 证据与文件

- 脚本：`scripts/perf-long-session.py`（标准库 + 项目既有依赖；临时 SQLite 库；不联网）；
- 测试：`tests/integration/test_long_session_perf.py`（`slow` 标记，全量 pytest 内 ≈11–14s）；
- 本机 JSON 证据：`.work/n-batch/perf-056.json`（`.work/` 不入库；字段与 §5 一致）；
- 关联：`R2 test_perf_baseline`（§18 测试策略）、`R-T4`（第 20 章风险表）、RELEASE 发布 checklist（发布前可 `--strict` 复跑）。
