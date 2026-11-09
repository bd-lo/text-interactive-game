## 第 14 章 状态管理与事件溯源：GameState、EventLog、replay、snapshot

### 本章检索关键词
- `python eventsourcing library snapshot`、`sqlalchemy optimistic concurrency row_version`、`alembic migration`、`Text-Game-Engine row_version inflight lease rewind snapshot`、`MnesOS event sourced agentic RPG`、`mnehmos rpg mcp event driven`、`eventsource-py python`

### 实际打开确认的仓库列表
- `pyeventsourcing/eventsourcing`（https://github.com/pyeventsourcing/eventsourcing）— 打开确认：许可证 BSD-3-Clause、最近提交 2026-10、star 约 1,689；PyPI 9.5.6 要求 `>=3.11`，最后一版兼容 3.10 的是 9.5.4（2026-03）；确认要点：README 确认 snapshotting、versioning、upcasting、压缩存储。
- `sqlalchemy/sqlalchemy`（https://github.com/sqlalchemy/sqlalchemy）— 打开确认：许可证 MIT、最近提交 2026-11、star 约 12,275；PyPI 2.1.8 要求 `>=3.11`，最后兼容 3.10 的是 2.0.57（2026-11-04，仍要求 `>=3.7`）；确认要点：ORM、事务、`UPDATE ... WHERE row_version=?` 的 CAS 写法。
- `sqlalchemy/alembic`（https://github.com/sqlalchemy/alembic）— 打开确认：许可证 MIT、最近提交 2026-10、star 约 4,469、PyPI 1.20.1 要求 `>=3.10`；确认要点：迁移脚本版本管理。
- `YakmovDenjys/Text-Game-Engine`（https://github.com/YakmovDenjys/Text-Game-Engine）— 打开确认：许可证 MIT、最近提交 2026-09、star 约 20、Python 3.10 兼容性：`pyproject.toml` 标注 `requires-python = ">=3.10"`；确认要点：README 明确「optimistic CAS (`row_version`) and durable inflight leases」「SQLAlchemy persistence layer」「Rewind + snapshot model with memory visibility watermark」。
- `neolaw84/MnesOS`（https://github.com/neolaw84/MnesOS）— 打开确认：仓库未检测到 LICENSE 文件（许可**未能确认**；PyPI 元数据标 MIT）、最近提交 2026-05、star 0、Python 3.10 兼容性：PyPI 0.7.1 要求 `>=3.12`（不兼容 3.10 基线）；确认要点：README 确认 stateless event-sourced agentic RPG、YARE 确定性规则、LangGraph 双节点、tree-based event source 支持分支。
- `Mnehmos/mnehmos.rpg.mcp`（https://github.com/Mnehmos/mnehmos.rpg.mcp）— 打开确认：许可证 MIT、最近提交 2026-11、star 约 46、Python 3.10 兼容性：仓库未找到 `pyproject.toml`/`requirements.txt`，无法确认 Python 版本声明（仅作参考设计）；确认要点：README 的 Event-Driven Agentic AI Architecture、事件工具与 `subscribe_to_events`。
- `tyevans/eventsource-py`（https://github.com/tyevans/eventsource-py）— 打开确认：许可证 MIT、最近提交 2026-10、star 4、Python 3.10 兼容性：PyPI 0.15.1 要求 `>=3.13`（与 3.10 基线冲突）；确认要点：PyPI 元数据与仓库页（README 未细读，已因版本冲突放弃）。

### 14.1 结论先行
状态管理采用「唯一写入者（Single Writer）+ 事件溯源（Event Sourcing）+ 派生快照（Snapshot）+ 确定性回放」。事件表与快照表用 SQLAlchemy 自建（锁 `>=2.0.57,<2.1`），迁移用 Alembic；`eventsourcing` 库只参考其快照/版本化/upcasting 设计，不引入（它的聚合-仓储模型会引入第二个状态权威，且新版本要求 Python ≥3.11）。`GameState.rng_state` 采用第 10 章的派生式表示（`mode/save_seed/last_seq`，见 14.4），不保存 Python 的 `random.getstate()`。`GameState` 永远可以从 `snapshot + events` 重建，在线状态只是缓存。

### 14.2 事件模型与顺序
事件沿用第 5 章 `Event(id: EventId, turn_id, seq, actor_id, method, params, result, rng_seed, ts)`，并统一为**全局事件序号**语义：`seq` 是每个存档内全局单调递增的整数（从 1 起、跨回合连续，不是回合内序号），`id = f"evt_{seq:06d}"`（`EventId`，见第 5 章 5.2/5.3）；`EventLog.stamp()` 在盖章时分配 `seq` 并生成 `id`，`GameEngine` 只在同一事务内调用；同回合事件按 `seq` 升序即为提交顺序。本方案在其上补充四个可选字段：`event_type`（稳定枚举，用于选择 reducer）、`applied`（是否已改变状态）、`prev_hash`/`event_hash`（哈希链，见 14.6）、`idempotency_key`（去重）。事件载荷版本不写入 `Event` 基线（第 5 章方案 B），在 `event_schema(method, version, upcaster_ref)` 登记，回放先 upcast 再校验；快照版本记在 `Snapshot` 上。全局排序**只按 `seq` 升序**（同回合自然按提交顺序），不按时间戳——时间戳只用于审计展示。

示例事件 JSON：
```json
{"id":"evt_001023","seq":1023,"turn_id":42,"event_type":"ITEM_CONSUMED",
 "actor_id":"char.player_1","method":"consume_item",
 "params":{"item_id":"item.copper_wire","qty":2},"result":{"remaining":0},
 "rng_seed":843927401,"applied":true,
 "ts":"2026-11-08T12:00:03+08:00"}
```
示例中的 `char.player_1`、`item.copper_wire` 为示意 ID（非 ULID）；真实实现按第 5 章的 ID 规则生成（`char_`/`item_` + 26 位 ULID）。

### 14.3 SQLAlchemy 表结构
```python
class EventRow(Base):
    __tablename__ = "events"
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)  # 每存档全局单调，EventLog 分配
    event_uid: Mapped[str] = mapped_column(String(16), unique=True)  # = f"evt_{seq:06d}"，即领域 Event.id
    game_id: Mapped[str] = mapped_column(String(36), index=True)
    turn_id: Mapped[int] = mapped_column(Integer, index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    method: Mapped[str] = mapped_column(String(64))
    params: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON)
    rng_seed: Mapped[int] = mapped_column(BigInteger)  # 有符号 64 位：0 <= rng_seed <= 2**63-1
    applied: Mapped[bool] = mapped_column(Boolean, default=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    prev_hash: Mapped[str] = mapped_column(String(64))
    event_hash: Mapped[str] = mapped_column(String(64))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_events_game_turn_seq", "game_id", "turn_id", "seq"),)
```
（`events.seq` 即第 5 章 `Event.seq` 的物理列，由 `EventLog` 全局分配、**不使用数据库自增**；`event_uid` 就是领域 `Event.id`，供 `Resolution.applied_events`、审计与外部引用。单机一存档对应一库，`seq` 直接作主键。**（2026-11-09 M4 前定案「同库多存档」：`events` 主键改为 `(game_id, seq)`、`event_uid` 唯一约束改为 `(game_id, event_uid)`、补 `(game_id, turn_id, seq)` 索引；`seq` 仍为每存档全局序号，Alembic 0002 重建主键，M2 旧库用 `textgame.persistence.legacy_upgrade` 升级——见 PROGRESS D-008-3/R5。）**）配套表：`turns(turn_id, status, started_at, committed_at, rng_seed, snapshot_id)`；`snapshots(id, game_id, turn_id, last_seq, state_json, state_hash, schema_version, created_at)`（`last_seq` 为快照覆盖到的全局事件序号）；`event_schema(method, version, upcaster_ref)`（第 5 章方案 B 的 upcaster 注册表）；`game_state_current(game_id PK, turn_id, state_json, row_version, updated_at)`（CAS 用）；`audit_events(...)`（失败尝试与管理员操作用，见 14.6）；`llm_call_log`（第 12 章）。JSON 列在 SQLite 用 TEXT、PostgreSQL 用 JSONB——部署目标**待定**（三方案：开发期 SQLite / 生产 PostgreSQL / 两者都支持）。

`rng_seed` 的落库约束（与第 5、10 章一致）：必须是**有符号 64 位整数**（`0 <= rng_seed <= 2**63-1`）。`RandomSource.derive_seed(...)` 与 `RandomSource.mask_seed(...)` 均为**类方法**（Protocol 见第 5 章 5.6、第 10 章 10.3）：`derive_seed` 用 blake2b 取 8 字节转整数，返回值**已包含 `& 0x7FFF_FFFF_FFFF_FFFF` 掩码**并落在 `[0, 2**63-1]`，调用方不得重复掩码；`mask_seed` 仅用于外部传入种子/历史数据的兜底归一化，禁止各服务自行截断。`EventRow.rng_seed` 用 SQLAlchemy `BigInteger`（映射 PostgreSQL `BIGINT` / SQLite 有符号 64 位 INTEGER），掩码失败会导致 PostgreSQL/SQLite 写库失败、回放链路断裂。

### 14.4 replay 与 snapshot
快照策略：每 20 回合或每 500 事件取其先到者；快照保存 `GameState.model_dump_json()`（压缩后入库）+ `state_hash` + `last_seq`（快照覆盖到的全局事件序号）+ `schema_version`；保留最近 5 个快照，更旧的归档。快照边界统一用全局 `seq` 表达，不依赖回合编号。

`GameState.rng_state` 按第 10 章采用**派生式**：`{"mode": "derived", "save_seed": "...", "last_seq": n}`，快照只持久化这三个字段，**不保存 `random.getstate()`**。理由：① 回放时用 `save_seed + turn_id + actor_id + method + seq` 即可重算任一事件的子种子，可在任意事件边界恢复，不依赖 RNG 调用顺序；② 保存 `random.getstate()` 会在任何随机调用顺序变化（新增一次掷骰、调整判定顺序）时失效，且跨 Python 版本不稳定；③ 派生式让快照与事件表解耦，快照仍只是可重建的缓存。若未来出现无法派生的第三方随机源，再评估第 10 章的混合方案 C（派生 + 局部状态），首版不做。

回放（Replay）算法：① 取 `≤ target_turn` 的最新快照（尚无 `game_created` 的空存档返回 `EmptySave` 占位，见 14.7）；② 校验 `state_hash`；③ 读取 `seq > snapshot.last_seq` 且 `turn_id ≤ target_turn` 的事件，按全局 `seq` 升序；④ 每条事件先按 `event_schema` upcast 到当前版本，再按 `event_type` 查 `ReducerRegistry` 执行纯 reducer，未知类型立即失败（`INTERNAL`），禁止跳过；⑤ 计算新 `state_hash` 并与每回合持久化的哈希对比；⑥ 返回重建状态与差异列表。任何不一致按审计事故处理，不允许「以数据库当前状态为准」掩盖问题。

断点恢复：启动时扫描 `turns.status='IN_PROGRESS'` 且 inflight 租约过期的回合 → 标记 `ABORTED` 并写审计 → 加载最新快照 + 按全局 `seq` 重放已提交事件 → 恢复服务。崩溃中未提交的事务由数据库回滚，已提交事件幂等重放不会重复生效（reducer 以 `event_uid`/`seq` 去重）。

### 14.5 并发一致性与幂等
单写入者由三层保证：① 进程内 `GameEngine.submit` 串行化（单写线程/单事务）；② 数据库层对 `turns` 行加锁或对 `game_state_current` 做 CAS：`UPDATE game_state_current SET ..., row_version = row_version + 1 WHERE game_id=? AND row_version=?`，影响行数为 0 即冲突；③ CAS 冲突统一为**最多 3 次提交尝试（首次 + 2 次重试）**，仍失败返回 `CONFLICT`。`idempotency_key` 唯一索引保证重复提交返回首次结果；重复事件由 `seq` 主键与 `event_uid` 唯一索引兜底，`turns.turn_id` 由写入者保证单调。

多进程部署**待定**：① 单实例 + 单写线程（首版，推荐）；② PostgreSQL advisory lock 跨进程单写；③ 按 game_id 分片写入（多实例各管一批，需路由层）。

### 14.6 审计与纠错
写入口三条禁令：`events` 只 INSERT；禁止 UPDATE/DELETE（DB 权限 + 迁移脚本建触发器）；`applied=false` 的事件不参与状态重建。纠错只能追加补偿事件（如 `ADMIN_CORRECTION`，必须带 operator、原因、审批单号），再回放验证对齐。

防篡改分层**待定**：① 每条事件哈希链（`event_hash = sha256(prev_hash + canonical(payload))`，可检出任意篡改，成本低）；② 每回合一个 Merkle root 并写入下一回合首个事件（存储省，验证粒度粗）；③ 不做（单机模式可接受）。推荐 ①，并在快照中存 `state_hash` 交叉验证；回放不一致 → 告警并停止写入，人工审计。审计查询提供 `replay_turn(turn_id)` 与 `explain_state(entity_path)`（反查影响该实体的全部事件）。

### 14.7 事件类型枚举与 reducer（含开局 `game_created`）
`event_type` 是 reducer 路由键：新增类型必须同时在 `domain/events.py` 的 `EventType` 登记、注册 reducer、并写 `event_schema` 版本。核心类型如下（`game_created` 为 v0.2 定稿名，其余为大写下划线）：

| event_type | 来源 method | reducer 要点 |
| --- | --- | --- |
| `game_created` | `GameEngine.create_game` | 从空状态按卡建角色、初始房间与物品实例，置 `turn=1` 并初始化 `rng_state` |
| `CHARACTER_MOVED` | `move_to` | 更新角色 `location` |
| `ITEM_TAKEN` / `ITEM_CONSUMED` | `take_item` / `consume_item` | 背包增减 |
| `CRAFT_DETERMINISTIC` / `CRAFT_GENERATED` / `CRAFT_REJECTED` | `craft_item` | 制造结算（第 13 章） |
| `ADMIN_CORRECTION` | 运营/审计 | 追加补偿（见 14.6） |

- `game_created` 载荷（按第 5、6 章口径）：`card_id`、`card_snapshot`（开局卡面快照，回放据此重建，避免后编辑影响回放）、`starting_room_id`、`initial_character_id`、`snapshot_version`（开局快照版本，=1）、`save_seed`（`RandomSource` 存档种子）。
- 提交：`create_game` 与回合提交同属唯一写入者 `GameEngine` 的**单事务**：校验卡存在/Schema → 建 `turns(turn=1)` → `EventLog.stamp` 分配 `seq=1`（仍走全局 `seq`）并写 `game_created` 首事件 → 写初始快照 → 提交；任一步失败整体回滚，不留半开局存档。
- 回放/快照：每次开局必生成 `snapshot_version=1`、`last_seq=1` 的初始快照，正常回放从它开始；若初始快照缺失，则以「空存档」为起点重放 `game_created`。空存档是「无 `game_created`、无事件」的逻辑状态（`turn=0`、`snapshot_version=0`、实体集合为空），**不构造 `GameState`**（其 `TurnId>=1`，第 5 章）而以 `EmptySave` 占位表达；回放空存档是合法结果，但若存在事件却缺 `game_created`，视为损坏（`INTERNAL`）。

### 候选轮子表
| 仓库 | 定位 | 可复用部分 | 许可证 | 活跃度 | 建议 |
| --- | --- | --- | --- | --- | --- |
| `pyeventsourcing/eventsourcing` | Python 事件溯源库 | snapshot/versioning/upcasting 设计 | BSD-3-Clause | 2026-10 有提交；约 1.7k star | 参考设计 |
| `sqlalchemy/sqlalchemy` | ORM 与事务 | 表结构、事务回滚、CAS 更新 | MIT | 2026-11 有提交；约 1.2 万 star | pip 依赖 |
| `sqlalchemy/alembic` | 数据库迁移 | 事件表/快照表迁移脚本 | MIT | 2026-10 有提交；约 4.5k star | pip 依赖 |
| `YakmovDenjys/Text-Game-Engine` | 回合引擎 | `row_version` CAS、durable inflight lease、rewind+snapshot | MIT | 2026-09 有提交；约 20 star | 参考设计 |
| `Mnehmos/mnehmos.rpg.mcp` | 事件驱动 RPG 运行时 | 事件订阅/审计回放的事件分类 | MIT | 2026-11 有提交；约 46 star | 参考设计 |
| `neolaw84/MnesOS` | 无状态事件溯源 RPG | 树形事件源与分支回放 | 未能确认（无 LICENSE 文件） | 2026-05 有提交；0 star | 参考设计 |
| `tyevans/eventsource-py` | 事件溯源库 | 事件流/投影 | MIT | 2026-10 有提交；4 star | 放弃 |

复用判断与拼接边界：`eventsourcing` 的 Aggregate/Repository 模型与「单一 GameState + 唯一写入者」冲突——若引入会形成第二套状态权威，且其快照是聚合级、我们是全局回合级，因此只参考其「快照 + 版本化 + upcasting」三件事，表与 reducer 全部自建。Text-Game-Engine 规模小但结构同构，借鉴 `row_version` CAS、inflight 租约与 rewind 语义即可，不依赖其代码（20 star，不应作为生产依赖）。MnesOS 的树形事件源与分支回放可作为「多存档/平行时间线」的远期参考，但许可证未能确认，不得拷贝代码。

边界：`EventLog` 只负责追加、盖章与读取（`stamp/append/query/replay/snapshot`，接口见第 5、6 章），`persistence/replay.py` 的重放器只读，`GameEngine` 是唯一写入者；快照是派生物，删掉不影响正确性；失败模式：`CONFLICT`（CAS 失败）、`INTERNAL`（回放不一致/未知事件类型）、`NOT_FOUND`（目标回合无快照且事件缺失）。审计失败不可降级——必须停写并告警。

### 本章结论
1. 状态权威唯一：只有 `GameEngine` 写 `game_state_current` 与 `events`；服务层与智能体一律只读或提案。
2. `Event.seq` 为每存档全局单调递增的事件序号（从 1 起、跨回合连续），`Event.id = evt_<seq:06d>`；每存档 `seq=1` 必为 `game_created`（由 `create_game` 单事务写首事件）；事件表以 `seq` 作主键、`event_uid` 唯一，回放与快照按全局 `seq`；`rng_seed` 为 `0..2**63-1` 有符号 64 位整数（`RandomSource.derive_seed` 返回值已含掩码，禁止重复掩码）；回放逐条按 upcaster + reducer 执行，未知事件类型立即失败。
3. 快照每 20 回合/500 事件生成一次、保留 5 个，用 `last_seq` 记录边界；`GameState` 可随时由 `snapshot + events` 重建，在线状态只是缓存。
4. SQLAlchemy 锁 `>=2.0.57,<2.1`、Alembic 锁 `>=1.20,<2`（Python 3.10 基线）；`eventsourcing`/`eventsource-py` 均不作为依赖。
5. 首版并发按「单实例 + 单写线程 + CAS + 幂等键」实现，多进程方案留待定项；事件哈希链推荐启用，回放不一致即停写告警。
6. 纠错只允许追加补偿事件，禁止改删历史事件；失败尝试写 `audit_events`，保证「没成功也有记录」。
