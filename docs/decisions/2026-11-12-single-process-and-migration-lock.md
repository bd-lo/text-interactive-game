# ADR · 单进程 / 多 worker 与启动迁移策略（D-R6-2）

- 状态：**已定案**｜决策人：lead｜整理：docs-release｜日期：2026-11-12｜基线：`f937e48` + R6 未提交工作树
- 关联：R5 §4 第 1/2 条（R-04 单进程幂等锁、R-05 多 worker 并发启动迁移竞态）；[R6 进度文档](../progress/R6-2026-11-12-leftovers.md)；第 16/17 章
- 关联编号：R-04、R-05

## 1. 背景与红线

R-04 的分段幂等锁与 R-05 的 `ensure_schema()` 自动升级都建立在**单进程假设**上：多 worker 并发启动可能并发迁移旧库；多进程下「查幂等表 → 执行 → 写幂等表」仍可能重复实体（进程内锁不跨进程）。SQLite 单文件也不提供跨进程唯一键抢占语义。红线：不能因为「默认单进程」让多 worker 部署静默坏数据；不能把迁移锁做成会影响健康库启动的常驻锁。

## 2. 决策

1. **多 worker 默认拒绝**：检测到 `WEB_CONCURRENCY` / `UVICORN_WORKERS` / `GUNICORN_WORKERS` >1 且未显式 `TEXTGAME_ALLOW_MULTI_WORKER=true` → 启动期 `RuntimeError`（中文说明单进程假设、迁移/幂等限制与 opt-in 方式）。
2. `TEXTGAME_ALLOW_MULTI_WORKER=true` 仅表示「已知悉并接受风险」，不提供跨进程幂等；SQLite 部署仍建议**单 worker**。
3. **启动迁移跨进程文件锁**：`ensure_schema()` 升级路径加 `*.migrate.lock` 文件锁（Windows `msvcrt` / POSIX `flock`，10s 超时，锁内重判是否仍需升级；健康库/新库不创建锁文件）。锁超时 fail-fast 报中文错误，不静默继续。
4. **手动迁移优先**：生产升级推荐先 `python -m textgame.persistence.legacy_upgrade`（或 Alembic）再启动；自动升级只作为单进程开发/单机便携路径的便利能力。

## 3. 理由与被否选项

- 理由：R6 不改「单进程 + SQLite」产品形态（D-N3/D-N7 既有边界），把风险显式化为启动期 fail-fast + opt-in；迁移锁解决最常见的并发升级竞态而不引入外部依赖。
- 被否：① 直接支持多 worker（需 DB 级唯一键/跨进程锁/迁移编排，超出当前范围）；② 每次启动都取迁移锁（健康库也要锁，增加启动延迟与误锁风险）；③ 只写文档不设门禁（默认 uvicorn `--workers 4` 会静默踩坑）。

## 4. 影响面与回写落点

- 实现：`src/textgame/config/settings.py`（`allow_multi_worker`）、`main.py`/启动入口检测、`persistence/database.py::ensure_schema` 文件锁。
- 测试：多 worker 检测/opt-in 放行、锁路径/排它/超时、升级持有锁、健康库不建锁、fail-fast 文案。
- 文档：第 16 章运行期策略/部署、第 17 章 N 系列部署注意；`RELEASE.md`/README 部署段如需再同步（本批未改）。
- 无迁移变更；单 worker 默认路径行为逐字不变（全量测试与 golden 零改动）。

## 5. 已知问题归属

| 编号 | 摘要 | 归属 | 关闭条件 |
| --- | --- | --- | --- |
| R-04 | 多进程幂等仍需 DB 唯一键抢占 | 若引入 Postgres/多进程部署 | 数据库级唯一约束/事务幂等落地（新 ADR） |
| R-05 | 多 worker 并发迁移已由文件锁覆盖，但仍建议单进程启动 | 保持建议 | 迁移编排/外部迁移工具接入后重评 |

## 6. 出口验收判据

- 默认启动零变化；`WEB_CONCURRENCY>1` 无 opt-in → 启动失败且文案含 `TEXTGAME_ALLOW_MULTI_WORKER`；opt-in 后启动成功；`ensure_schema` 并发进程测试只升级一次、健康库不建锁；相关 pytest 全绿。

## 7. 复审与变更纪律

- 触发复审：引入 Postgres/多进程部署、SQLite 之外的持久化、或需要多实例共享存档时。
- 本 ADR 只增不改；推翻结论 = 新 ADR + `supersedes: D-R6-2` + 回写启动门禁与第 16/17 章。
