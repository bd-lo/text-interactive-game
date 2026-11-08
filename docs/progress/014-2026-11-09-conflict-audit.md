# 014 · 2026-11-09 · M3 多角色 · conflict-audit

> 切片目标一句话：交付冲突审计只增表与查询端点、版本化持久化（Alembic baseline），并用 10 个玩家输入的多角色 golden 回放钉死「两 NPC 冲突提案被裁决 + 事件/哈希/审计」 ｜ 验收命令：`uv run pytest tests/replay -k m3` ｜ 里程碑 M3 ｜ 编号区间 010–014 ｜ 执行/验收：persistence-dev（持久化支撑）、lead（审计/集成/回放/提交）

## 1. 目标与验收命令

- 目标（第 11 章 §11.4、第 14 章 §14.6、第 17 章 M3 出口）：审计状态词固定 `{committed, conflicted, aborted}`（与 `verdict` 分离）、审计只 INSERT；`GET /api/v1/games/{game_id}/audit?turn_id=` 只读查询；冲突回放 golden 固定种子 10 回合逐字段一致；快照 `version` 持久化 + Alembic baseline（R4）。
- 验收命令与结果：
  - `uv run pytest tests/replay -k m3 -q` → **5 passed, 32 deselected**（golden：10 次输入，最终 `state_hash=df58e51d…`、13 条事件、`version=10`、`last_seq=13`；turn 2 冲突回合 applied/rejected 成对、审计 16 行全部 committed）；
  - `uv run pytest tests/integration/test_audit_query.py -q` → **5 passed**（端点字段/过滤/404/422/只增）；
  - 持久化：`uv run pytest tests/integration/test_audit_store.py tests/integration/test_snapshot_restore.py -q` → **36 passed**；`tests/integration/test_alembic_migration.py` → **7 passed**；
  - 全量 **960 passed**，pre-commit 9 hooks 全过。

## 2. 改动与提交

- 审计：`services/audit_service.py`（save/query）、`persistence/tables.py::audit_events`（16 列 + 索引、只 INSERT）、`memory.py`/`sqlite_repository.py` 的 `save_audit/read_audit`、`api/routes/games.py` 审计端点、`api/schemas.py` 读模型。
- 版本持久化：`services/ports.py::SnapshotRecord.version`、`persistence/versioning.py`（重建规则）、`snapshots.version` 列、`GameEngine` 传 `version`。
- Alembic baseline：`alembic.ini` + `alembic/{env.py,script.py.mako,versions/0001_m3_baseline.py}`（空库 5 表；升级幂等/回滚/schema 与 ORM 一致性测试）。
- 回放：`tests/replay/test_m3_golden_replay.py`、`tests/fixtures/golden/m3_golden.json`（Lead 在生产同构装配下一次性生成并核对，禁止 snapshot-update）。
- 提交：`a5f2be3`。

## 3. 验证结果

- 冲突回放：turn 2 同回合两提案一 `applied` 一 `rejected`，审计行 attempt 区分、只增不改；零写入回合（只读 + 全拒）同样留审计行且不覆盖历史。
- 版本重建：删 `game_state_current` 行后由「最新快照 + 后续事件」重建，`version = snapshot.version + distinct turn_id 数`；真实引擎路径（同回合 2 事件）验证 `version=2/last_seq=3`。
- Alembic：`upgrade head` 幂等、`downgrade base` 可回滚、迁移库 schema 与 `Base.metadata` 反射一致；Windows zh-CN GBK 读 ini 坑用纯 ASCII `alembic.ini` 规避并有测试防回归。

## 4. 下一步与风险

- 下一步：M3 出口已满足，进入 M4 制造（015-recipe-model）；M4 前评估 D-008-3 多存档复合主键与 Alembic 接入 CI。
- 风险：① M2 旧库（已存在 snapshots 表）不在 baseline upgrade 范围，开发期删旧库/生产式迁移留待决策（R5）；② 审计 uid 为确定性 `(game, turn, attempt, proposal)` 去重（同 attempt 重投幂等、首次为准），未来若需严格 append 再评估（R6）；③ `events` 主键仍为 `seq` 单列（D-008-3 复合主键延后，M3 单库单存档不触发）。
