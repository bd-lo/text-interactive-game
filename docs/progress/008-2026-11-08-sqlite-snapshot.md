# 008 · 2026-11-08 · M2 单场景 MVP · sqlite-snapshot

> 切片目标一句话：SQLite 事件表/快照落地并接入组合根，进程重启后存档可恢复、事件 seq 连续、幂等表跨重启生效 ｜ 验收命令：`uv run pytest tests/integration/test_snapshot_restore.py` ｜ 里程碑 M2 ｜ 编号区间 005–009 ｜ 执行/验收：domain-dev（AI）/ lead（复核、提交）

## 1. 目标与验收命令

- 目标（第 17 章 §17.2 切片 008）：SQLite 事件表/快照与重启恢复；护栏=仓储不承载业务规则。
- 验收命令/断言（先定后写）：
  - `uv run pytest tests/integration/test_snapshot_restore.py -q` → **18 passed**（建库/建表、5 回合端到端、重启恢复、删 current 行→快照重建并续写、CAS 双实例冲突、幂等跨重启、事件完整性、无 `GAME_CREATED`/快照哈希不符 `ValueError`）
  - `uv run pytest tests/integration/test_sqlite_app_boot.py -q` → **5 passed**（真实 `main._build_app()` 按 `TEXTGAME_DB_URL` 装配、重启后视图/state_hash/seq 一致、续写、幂等回放、`prepare_db` 目录/内存/非 sqlite 分支）
  - `uv run pytest -q` → **636 passed**；`ruff format/check`、`mypy src/textgame`（60 files）、`lint-imports`（2 kept）、`pre-commit --all-files`（9 hooks）全绿
  - 手工 uvicorn（8767 + `.work/m2app.db`）：建局 201 → 1 回合 version=2 → 杀进程 → 同 DB 重启 → `GET /games` turn=2/version=2/last_text 一致 → 再回合成功；已清理临时库、仓库无 `data/`
- 不做什么：不引入 Alembic 迁移（M2 简化）；不支持 sqlite 以外后端（`NotImplementedError`）；不改 005/006/007 组件。

## 2. 改动与提交

- 新增：`persistence/{tables,database,sqlite_repository}.py`、`tests/integration/{test_snapshot_restore,test_sqlite_app_boot}.py`
- 修改：`main.py`（装配 `prepare_db(settings.db_url)` → `init_schema` → `SqliteRepository`；其余组件零改动）
- 提交：`d6ce599`（`slice: 008 sqlite-snapshot`）
- 关键实现点：4 张表（`events` seq 主键非自增/`event_uid` 唯一/params·result JSON、`snapshots`、`game_state_current`、`idempotency`）；`commit` 单事务 CAS（`UPDATE ... WHERE game_id=? AND version=?` 影响行数 0 → 回滚 False）+ seq 连续校验，失败零部分写入；`load` current 优先，缺失时「最新快照 + 之后事件」经 `GameState.apply` 重建并校验 `state_hash`；`prepare_db` 自动建父目录（默认 `data/` 落 gitignore）。
- 护栏检查：仓储不承载业务规则 ✅（只 import `domain`/`services.ports`，不 import 服务实现、不调 GameEngine）；唯一写入者不变 ✅；快照是派生物（可删可重建）✅；`rng_state` 派生式持久化 ✅。
- 契约变更（已登记 PROGRESS §5）：① M2 建表用 `create_all`、无 Alembic 迁移（第 14/16 章要求 Alembic 管迁移，M3 前补）；② `game_state_current` 行丢失时 `version = last_seq`（M2 每次提交恰 1 条事件；M3 多事件需把 version 持久化到快照）。

## 3. 验证结果

- lint / type：ruff format/check 89 files 全过；mypy strict **60 source files** Success；lint-imports 2 contracts KEPT。
- 单测/集成：23 passed（snapshot_restore + sqlite_app_boot）+ 全量 636 passed（619 基线无回归）。
- 重启证据：`GET /games/{id}` 重启前后完全一致（turn=3/version=3/snapshot_version=1/last_text 一致）；`state_hash(repo.load(..).state)` 一致；事件 seq `[1,2,3,4]` 连续且历史事件逐字段不变；最新 version 续写成功（3→4）；过期 version（1）→ `TurnResult(ok=false, CONFLICT, version=3)` 零写入；幂等键跨重启同参完全一致回放、异参 `CONFLICT`。
- 失败分支证据：双实例 CAS 冲突第二写者 `commit=False` 且零变化；重复 `event_uid` → False；无 `GAME_CREATED` 事件流 → `ValueError`；快照 `state_hash` 不符 → `ValueError`。
- 失败与回滚：无回滚。测试为释放 tmp DB 句柄访问 `app.state.orchestrator._repo._engine`（仅测试辅助）；`data/` 目录只在 `prepare_db` 建，测试/冒烟都走 tmp，仓库无 `data/`。

## 4. 下一步与风险

- 下一步：009 · golden-replay（固定种子 7 回合 `state_hash`/事件/rng_seed 回放 + 注入 0 写入），验收 `uv run pytest tests/replay -k m2`；完成后进入 M2 出口独立验收。
- 风险/开放问题：① 无 Alembic 迁移（R4），M3 前补版本化迁移；② `current` 行丢失时 `version=last_seq` 的 M2 语义（网关风险已登记）；③ 测试私有访问组合根内部 Engine 释放句柄，后续可加 lifespan/shutdown hook 替代。
- 需要人工决策：无新增。
