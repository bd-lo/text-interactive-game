# M6B-1 · 2026-11-10 · M6 后端前置（不占编号）· replay-endpoint

> 切片目标一句话：交付 `GET /api/v1/games/{game_id}/replay?from_seq=&limit=`（D-M6-3）——按全局 `seq` 升序返回事件切片、只读零写入，供 027 完整回放 UI 与审计文本回放 ｜ 验收命令：`uv run pytest tests/integration/test_replay_endpoint.py -q` ｜ 里程碑 M6（后端前置 item 2，不占 025–028 编号）｜ 决策：D-M6-3、D-TEAM-3/4（`docs/decisions/2026-11-10-m6-backend-contract.md`）

## 1. 目标与验收命令

- 目标（第 6 章 §6.7、D-M6-3）：① `GET /api/v1/games/{id}/replay` 200 `{events, from_seq, to_seq}`，`events[]` 字段 `{id, seq, turn_id, actor_id, method, event_type, params, result, rng_seed, ts}`，按全局 `seq` 升序；② `from_seq` 缺省 1、`limit` 缺省 200/上限 500；③ `result.narrative` 原样保留（文本回放数据源）；④ 未知 `game_id` → 404 `NOT_FOUND` 统一信封；`from_seq` 非正整数或 `> last_seq + 1` → 422 `INVALID_INPUT`；`from_seq == last_seq + 1` 返回空窗口（`to_seq = from_seq - 1`）；⑤ 只读、零事件、`version`/`snapshot_version` 不变。
- 验收命令与真实输出：
  - `uv run pytest tests/integration/test_replay_endpoint.py -q` → **9 passed**
  - `uv run pytest -q` → **1249 passed in 18.51s**；`uv run pytest tests/replay -q` → **61 passed**（逐值不变）
  - 四门：ruff check/format、mypy strict（84 files）、lint-imports 全绿
- 不做什么：不做分页游标/增量轮询；不返回审计表/幂等表；不改事件表 schema、不加迁移；不接前端（027 完整负责 UI）。

## 2. 改动与提交

- 提交：`9a0ec5f slice: M6 后端前置（replay 端点 + 静态托管/SPA + /view 背包数量）`
- 文件：`src/textgame/api/routes/games.py`（+57：`get_game_replay`、`_replay_event`、`from_seq`/`limit` Query 校验）、`src/textgame/api/schemas.py`（`ReplayEventResponse`/`ReplayResponse`，`extra=forbid`）、`src/textgame/services/query_service.py`（只读 `read_events(game_id)`，按 `seq` 升序）、`tests/integration/test_replay_endpoint.py`（201 行，9 例）。
- 关键 diff 摘要：`events = query_service.read_events(game_id)` → 越界校验（`from_seq > last_seq + 1` → 422）→ `window = [e for e in events if e.seq >= from_seq][:limit]` → `to_seq = items[-1].seq`（空窗口 `from_seq - 1`）。路由仅 GET、不写库。
- 护栏检查：未改 `GameEngine`/Event schema/`ProposalQueue`/golden；只读端点不产生事件；无新增依赖；无密钥。
- 契约变更：无偏离（D-M6-3 按契约落地）；`read_events` 为新增只读服务方法，不改既有查询契约。

## 3. 验证结果

| 检查 | 结果 |
| --- | --- |
| 本片测试 | `test_replay_endpoint.py` → 9 passed：升序/字段完整/`narrative` 保留/`from_seq` 与 `limit` 边界/空窗口/404/422/只读零写入（`version` 不变） |
| 全量 | 1249 passed；replay 61 零回归 |
| 四门 | ruff check/format、mypy strict、lint-imports 全绿 |
| 契约对照 | 与第 6 章 §6.7、第 23 章 §23.3 replay 行一致（`limit` 默认 200/上限 500） |
| 失败与回滚 | 无 |

## 4. 下一步与风险

- 下一步：M6 后端前置 item 3 **character-cards 8 端点 + `card_service` + Alembic 0003**（D-M6-1..3；0003 由 D-M5-10 顺延）；前端 027 完整回放 UI 随后接入本端点。
- 风险：① 本端点尚未有前端调用方（027 完整前仅契约测试覆盖）；② 单窗口上限 500，长存档需前端按 `to_seq + 1` 续拉（前端分页策略在 027 明确）。
- 需要人工决策：无。
