# 技术实现风险审查报告（坑位清单）

- 审查日期：2026-11-11
- 审查范围：`src/textgame`（后端）、`web/src`（前端）、`scripts/`、`.github/workflows/ci.yml`
- 审查方式：只读静态审查 + 纯对象/内存仓储最小复现（未写库、未跑全量 pytest/vitest）
- 审查基线：当前工作区（未提交改动）
- 结论：项目在分层、确定性、事件溯源、错误码与测试覆盖上整体扎实，但存在 **7 个会直接坏主线/坏状态的高优先级问题**，以及若干并发、LLM 治理、前端状态与安全条件项。本文按风险排序，给出位置、影响、复现证据和修复建议。
- 变更声明：初版审查（§0–§6）只新增文档，**未修改任何运行时逻辑/测试/golden**；§7 修复记录为 2026-11-11 起修复批次的回写。
- 修复状态（2026-11-11）：R-01～R-25 全部已修复/已降低风险，逐项修法与回归测试见 §7；§0 风险表状态列已同步为最终口径。

## 0. 风险总表

| ID | 级别 | 主题 | 状态 | 主要位置 |
| --- | --- | --- | --- | --- |
| R-01 | P0 | story 语义标签被当模板键：背包合并错误 + 主线制造失败 | 已修复 | `services/query_service.py:242`、`domain/crafting.py:55,114,386-388`、`domain/scenario_story.py:301-335` |
| R-02 | P0 | 用户场景导入成功但建局 500（属性/自指出口/物品双放置） | 已修复 | `domain/user_scenario.py:289-298,516-546,706-777`、`domain/models.py:44-53,74-78` |
| R-03 | P0 | commit 成功后快照/幂等写失败被报告为回合失败 | 已修复 | `services/game_engine.py:163-174,243-245`、`orchestration/turn_orchestrator.py:1116-1127,1479-1485,1661-1677` |
| R-04 | P0 | 创建/导入幂等 check-then-act，并发同键造重复实体 | 已修复（单进程范围） | `turn_orchestrator.py:813-820,872-889`、`card_service.py:932-958`、`world_book_service.py:742-768`、`scenario_service.py:510-519` |
| R-05 | P0 | 旧库不会自动迁移，`init_schema` 只建表不补列 | 已修复 | `main.py:61-62`、`persistence/database.py:68-70`、`persistence/tables.py:85` |
| R-06 | P0 | `EventLog.append` 不校验 rng_seed，生产不跑 replay | 已修复 | `domain/events.py:173-200,211-242`、`services/game_engine.py:103-147,219-221` |
| R-07 | P0 | LLM 解析白名单缺少 `inspect_item` / `craft_item` | 已修复 | `agents/parser_agent.py:29-31,92`、`main.py:116-123` |
| R-08 | P1 | BatchSleep 的 `SCENE_ENTRY` 会永久唤醒（当前 latent） | 已修复 | `orchestration/batch_sleep.py:159-200,475-499` |
| R-09 | P1 | 人物卡生成限流可绕过且 `_hits` 无界增长 | 已修复 | `services/card_service.py:205-218`、`api/routes/cards.py:118-122` |
| R-10 | P1 | 快照表缺 `(game_id, snapshot_version)` 唯一约束 | 已修复 | `persistence/tables.py:70-86`、`persistence/sqlite_repository.py:207-235` |
| R-11 | P1 | NaN/Infinity 解析后置失败，最终 500 而非 422 | 已修复 | `domain/hashing.py:18-26`、`world_book_service.py:644-673`、`card_service.py:691-709` |
| R-12 | P1 | 世界书正则键可能 ReDoS | 已修复（保留限制） | `domain/world_book.py:481-525,908-930` |
| R-13 | P1 | LLM 重试等待不受整链 deadline 约束 | 已修复 | `llm/litellm_client.py:462-492` |
| R-14 | P1 | 解析/制造 Agent 未设置 `prompt_version` | 已修复 | `agents/parser_agent.py:170-180`、`agents/craft_agent.py:62-77`、`llm/client.py:69,141-161` |
| R-15 | P1 | LLM 叙事拿不到制造产物名 | 已修复 | `agents/narrator_agent.py:254-258,443-448`、`services/craft_service.py:333-334` |
| R-16 | P1 | `/view.lore` 与下一回合注入 off-by-one | 已修复 | `services/query_service.py:181-187`、`turn_orchestrator.py:696-702`、`domain/world_book.py:1045` |
| R-17 | P1 | `in`/`out` 出口不可导航且英文泄漏 | 已修复 | `domain/types.py:24`、`domain/scenario.py:45-55`、`orchestration/intent.py:140,255-270` |
| R-18 | P1 | 校验端点无请求体上限 | 已修复 | `api/routes/scenarios.py:194-196`、`api/routes/world_books.py:62-70,233-238` |
| R-19 | P1 | 自由制造在 Resolver 内调 LLM（条件） | 已修复（前端运行期 opt-in） | `services/craft_service.py:168-204`、`orchestration/resolver.py:290-295`、`main.py:86-90` |
| R-20 | P2 | Card/WorldBook 把失败响应缓存进幂等表 | 已修复 | `services/card_service.py:960-975`、`world_book_service.py:770-784` |
| R-21 | P2 | 每回合全量读事件 / LLM 调用日志无界 | 已修复 | `game_engine.py:219-221`、`turn_orchestrator.py:1206`、`llm/records.py:105-136` |
| R-22 | P1 | 前端 `loadGame` 失败保留上一个对局状态 | 已修复 | `web/src/stores/game.ts:321-339`、`web/src/views/GameView.vue:148-161` |
| R-23 | P2 | 零事件只读回合的叙事刷新后消失 | 已修复 | `web/src/stores/game.ts:275-277,408-411`、`web/src/utils/history.ts:89-95` |
| R-24 | P2 | 前端幂等键双层封装；create key 缺 world_book_id | 已修复 | `web/src/api/client.ts:145-147`、`web/src/stores/game.ts:297-299` |
| R-25 | 条件 | 无鉴权/Host 校验；`api_base` 任意可 SSRF/密钥外送 | 已修复 | `api/app.py:188-243`、`api/routes/settings.py:66-70`、`llm/credentials.py:97-99` |

> 说明：P0 = 会直接坏主线/坏状态；P1 = 功能、一致性、可靠性或安全条件项；P2 = 体验/性能/可维护性。`条件项` 表示当前默认装配未触发，但一旦打开对应开关或接入对应组件就会成为真问题。
>
> 表中后端路径为简洁写法，省略 `src/textgame/` 前缀（正文为完整路径）；前端路径保留 `web/src/...`。

## 1. P0：会直接坏主线/坏状态

### R-01 story 语义标签被当模板键：背包合并错误 + 主线制造失败

**位置**

- `src/textgame/services/query_service.py:228-248`：`get_inventory()` 用 `item.tags[0]` 分组。
- `src/textgame/agents/narrator_agent.py:325-338`：LLM 叙事背包摘要同样按 `tags[0]` 分组。
- `src/textgame/services/craft_service.py:238-269`：自由制造视图构造 `CraftItemView(template_id=item.tags[0])`。
- `src/textgame/domain/crafting.py:55,114,370-403`：`CraftStateSlice.inventory` 的键类型是 `ItemTemplateId`（正则 `^item[._][a-z]...$`），`build_craft_state_slice()` 直接把 `item.tags[0]` 塞进去。
- `src/textgame/domain/scenario_story.py:295-335`：story 里火把/铜线的 `tags[0]` 是模板键；治疗药水、结霜的烈酒是 `["consumable"]`，火石是 `["quest","material"]`，锻锤是 `["tool"]`。
- `src/textgame/domain/user_scenario.py:733-743`：用户场景物品 tags 原样写入，不强制模板键；`build_craft_state_slice()` 在 `domain/crafting.py:386` 对无 tags 物品直接 `ValueError`，自定义场景作者必须手工把 `item.xxx` 放首位，导入校验不会提示。

**影响**

1. **背包合并错误**：玩家同时拿到治疗药水 + 结霜的烈酒，`/view.inventory` 会把两者合并成“治疗药水 ×2”，烈酒从状态面板消失。
2. **主线制造失败**：玩家先捡了药水/火石/锻锤，再打造信号火把，`build_craft_state_slice()` 抛 `ValidationError`（`inventory.consumable.[key]`），`CraftService.craft()` 在 `services/craft_service.py:109-112` 把它转成 `INVALID_INPUT`，玩家只看到“动作没有生效”，无法完成 story 目标 2。

**复现证据（纯对象，未写库）**

- story 状态 + 背包 `[火把, 铜线, 药水]` → `build_craft_state_slice()`：
  - `ValidationError: 1 validation error for CraftStateSlice`
  - `inventory.consumable.[key] String should match pattern '^item[._][a-z][a-z0-9_.]{1,63}$'`
- story 状态 + 背包 `[药水, 烈酒]` → `QueryService(None).get_inventory()`：
  - `{'items': [{'item_id': 'item_...', 'name': '治疗药水', 'qty': 2}]}`

**建议修法**

- 首选：把 story 物品 tags 首位统一为模板键：`item.potion`、`item.liquor`、`item.flint`、`item.forge_hammer`；语义标签放后面（`["item.potion","consumable"]`）。这样背包、制造、LLM 三处口径同时正确。
- 备选：聚合函数只在 `tags[0]` 匹配 `ItemTemplateId` 时使用模板键，否则回退 `item.id`；但制造仍需要模板键，所以 story 数据应优先修正。
- 回归测试：`story 背包同时有药水/火石/锻锤时打造信号火把`；`story 背包同时有药水+烈酒时 /view.inventory`；`M4 正常制造不受影响`。

### R-02 用户场景导入成功但建局 500

**位置**

- `src/textgame/domain/user_scenario.py:281-307`：`PlayerSpec.attributes` 是普通 `dict[str,int]`，没有键名/值域限制；`PlayerSpec._hp_within_max` 只比较自身的 `hp/max_hp`。
- `src/textgame/domain/user_scenario.py:478-575`：`_check_references()` 只检查引用存在；没有检查房间自指出口，也没有检查同一物品是否同时放在背包和房间。
- `src/textgame/domain/user_scenario.py:706-777`：`build_initial_state()` 把 `attributes` 直接交给 `Character`；同一物品实例 ID 可以同时写进 `inventory` 和 `room.item_ids`。
- `src/textgame/domain/models.py:44-53`：`Character` 只允许 7 个属性键且值在 `[-10,100]`。
- `src/textgame/domain/models.py:74-78`：`Room` 禁止自指出口。
- `src/textgame/services/scenario_service.py:408-436`：`build_game_payload()` 直接调用 `build_initial_state()`，没有捕获 `ValidationError`。

**复现证据（纯对象）**

| 输入 | 导入 | 建局 |
| --- | --- | --- |
| `player.attributes={"luck":1}` | `parse_definition().ok=True` | `ValidationError: unknown attribute keys: ['luck']` |
| `room.exits={"north":"r"}`（自指） | `ok=True` | `ValidationError: room cannot exit to itself` |
| 同一 item key 同时在 `player.inventory` 和 `room.items` | `ok=True` | 状态里同一 `item_id` 同时在背包和地面；再 `take_item` 会造重复实例 |

当前路由只捕获 `ScenarioServiceError`（`api/routes/games.py:132-141`），`ValidationError` 属于 `ValueError`，最终由通用 `ValueError` handler 返回 500（不是 422）。

**建议修法**

- 在 `ScenarioService.validate()`/`import` 阶段做一次 `build_initial_state()` dry-run，把 `ValidationError` 转成 422 的字段级错误。
- 在 `_check_references()` 显式拒绝：`room.exits` 指向自己；同一 `item.key` 同时出现在 `player.inventory` 和任意 `room.items`；`player.attributes` 不允许的键（或直接复用 `Character` 的校验）。
- 回归测试：三类输入都必须在导入阶段失败，而不是建局时 500。

### R-03 commit 成功后快照/幂等写失败被报告为回合失败

**位置**

- `src/textgame/services/game_engine.py:163-174`：`create_game()` 先 `repo.commit()`，再 `_save_snapshot()`；没有 try。
- `src/textgame/services/game_engine.py:243-245`：`submit()` 先 `repo.commit()`，再 `_maybe_save_snapshot()`；没有 try。
- `src/textgame/orchestration/turn_orchestrator.py:1116-1127,1479-1485,1661-1677`：`engine.submit()` 成功后调用 `_save_idempotent()`；没有 try。
- `src/textgame/persistence/sqlite_repository.py:207-235,256-279`：快照/幂等表是独立事务，不在主提交事务内。

**影响**

SQLite locked、磁盘满、快照 state 含不可 JSON 化值等异常会在**状态已提交之后**冒到 `_run_once`，返回 `INTERNAL`。客户端会认为本回合失败并重试，但服务端版本已经推进：重试同一 `expected_version` → CONFLICT；或者客户端刷新后重发 → 重复执行（取决于具体路径）。

这直接违背“失败零写入/原子提交”的对外语义。`GameEngine._save_snapshot()` 的注释本身也说“快照只是缓存，写失败不影响提交正确性”，但代码并没有兑现这个承诺。

**建议修法**

- 快照写失败：`try/except Exception` + WARNING 日志，不影响 `submit/create_game` 的返回值。
- 幂等写失败：要么吞异常并记录（至少保证响应仍成功），要么把幂等记录与主提交放进同一事务；不能“状态已提交、接口返回失败”。
- 增加 fault-injection 测试：repo 的 `save_snapshot` / `save_idempotent` 抛异常时，回合仍返回成功/至少客户端能明确知道已提交。


### R-04 创建/导入幂等 check-then-act，并发同键造重复实体

**位置**

- `src/textgame/orchestration/turn_orchestrator.py:813-820,872-889`：`create_game()` 先 `get_idempotent()`，再 `engine.create_game()`，最后 `save_idempotent()`；没有 `run()` 里的 striped lock。
- `src/textgame/services/card_service.py:932-958`：`_idempotent()` 先查后做。
- `src/textgame/services/world_book_service.py:742-768`：同样。
- `src/textgame/services/scenario_service.py:510-519`：同样。
- `src/textgame/persistence/sqlite_repository.py:256-279`：`save_idempotent()` 同键无条件覆盖。

**影响**

两个并发请求用同一个 `Idempotency-Key` 时，都可能 miss 幂等表并各自创建：两个 `game_id` 不同的对局（只有一个会被幂等记录引用）；两张人物卡 / 两本世界书 / 两个上传场景；后写覆盖前一个幂等记录，另一个实体永远没有幂等引用。

`CHANGELOG` 已登记 D-M4-15“并发同 Idempotency-Key 瞬时响应不一致”，但该修复只覆盖了 `run()`（回合），没有覆盖创建/导入。

**建议修法**

- 复用 `TurnOrchestrator.run()` 的 per-key 分段锁，覆盖所有写端点；或数据库唯一幂等键先 `INSERT` 抢占，失败就等待/回放；不要让 `save_idempotent()` 无条件覆盖。
- 并发测试：两个线程同 key 同时 `create_game` / `import card`，最终只能有一个实体。

### R-05 旧库不会自动迁移，`init_schema` 只建表不补列

**位置**

- `src/textgame/main.py:61-62`：启动只做 `prepare_db()` + `init_schema()`。
- `src/textgame/persistence/database.py:68-70`：`init_schema()` 只是 `Base.metadata.create_all(engine)`。
- `src/textgame/persistence/tables.py:70-86`：新 `SnapshotRow` 有 `version` 列；旧 M2/M3 库没有。
- `src/textgame/persistence/legacy_upgrade.py:1-27`：有显式升级模块，但启动组合根没有调用。
- `src/textgame/main.py:69`：启动还会 `scenario_service.reload()` 查新表。

**影响**

从旧版本升级的用户：`create_all` 会建缺失的新表，但**不会**给已有 `snapshots` 加 `version` 列；`latest_snapshot()` / `_save_snapshot()` 直接 `OperationalError`；README/RELEASE 没有给用户“先运行 legacy_upgrade”的指令。

**建议修法**

- 启动时检测 schema 版本/列，自动跑 `legacy_upgrade`，或 fail-fast 并给出明确升级命令；
- 增加“旧 M2 库启动/建局/读视图”的集成测试；
- 在 README/RELEASE 增加升级说明。

### R-06 `EventLog.append` 不校验 rng_seed，生产不跑 replay

**位置**

- `src/textgame/domain/events.py:173-200`：`append()` 只校验 `seq/id/ts`，不校验 `rng_seed == derive_seed(save_seed, turn_id, actor_id, method, seq)`。
- `src/textgame/domain/events.py:211-242`：只有 `replay()` 才校验 rng_seed。
- `src/textgame/services/game_engine.py:219-221`：`submit()` 只 `append()` 已存事件再 `stamp()`，没有 `replay()`。
- `src/textgame/services/game_engine.py:103-147`：`create_game()` 允许传入与 `save_seed` 不一致的 `initial_state.rng_state.save_seed`，没有断言。

**复现证据（纯对象）**

- `EventLog("seed-b").append()` 一条由 `seed-a` 盖章的事件可以成功；随后 `replay()` 报 `ReplayMismatchError: replay mismatch at seq=1: field=rng_seed expected=... actual=...`。
- `GameEngine.create_game(save_seed="seed-b", initial_state=由 seed-a 构造的状态)` 返回 `ok=True`；用状态里的 `seed-a` 建 `EventLog` 再 `replay()` 立即 mismatch。

**影响**

坏事件/错种子可以进入库；`GET /replay` 只是原样返回事件，所谓“固定种子回放校验”实际上只在测试里发生。数据库被篡改或初始状态 seed 不一致时，运行期不会发现。

**建议修法**

- `append()` 内校验 rng_seed；
- `create_game()` 断言 `initial_state.rng_state.save_seed == save_seed`（以及 `PLAYER_ID` 存在）；
- 启动/健康检查或 `load()` 时跑一次 `EventLog.replay()`。

### R-07 LLM 解析白名单缺少 `inspect_item` / `craft_item`

**位置**

- `src/textgame/agents/parser_agent.py:29-31`：默认白名单只有 M2 六工具。
- `src/textgame/agents/parser_agent.py:85-94`：构造时若没传 `tool_whitelist` 就用这六个。
- `src/textgame/main.py:116-123`：生产组合根只传 `tool_specs=registry.visible_tools(...)`，**没有传 `tool_whitelist`**。
- 实际注册表包含 `inspect_item`（`tools/builtin/m2.py:42`）和 `craft_item`（`tools/builtin/crafting.py:22`）。

**影响**

LLM 即使正确输出 `inspect_item` 或 `craft_item`，也会被当作白名单外工具拒绝，然后走确定性 fallback；prompt 里却把这两个工具的 Schema 交给了模型。默认离线时 fallback 能“救回来”，所以测试不容易发现，但 LLM 路径实际上是残的。

**建议修法**

- `tool_whitelist=frozenset(spec.name for spec in tool_specs)`，或让 `ToolRegistry` 暴露 `names`；
- 补一个 LLM 输出 `inspect_item`/`craft_item` 不 fallback 的单测。


## 2. P1：并发、LLM、世界书与一致性

### R-08 BatchSleep 的 `SCENE_ENTRY` 会永久唤醒（当前 latent）

- `src/textgame/orchestration/batch_sleep.py:475-484`：`_player_entered_room()` 扫描**整个事件历史**最后一次玩家移动，不按 `turn_id` 过滤。
- `src/textgame/orchestration/batch_sleep.py:381`：`collect()` 把全部历史事件传进去。
- `src/textgame/orchestration/batch_sleep.py:486-502`：`_detect_wake()` 只要 `entered_room == npc_room` 就返回 `SCENE_ENTRY`。
- `src/textgame/orchestration/batch_sleep.py:159-200`：`DormancyScheduler` 状态只按 `actor_id` 存，没有 `game_id`。
- 当前 `main.py` 没有注入 `agent_scheduler`（默认 `None`），所以是 latent；一旦接线就是真 bug。

**影响**：玩家曾经进过一次某房间后，该房间 NPC 每回合都会被 `SCENE_ENTRY` 唤醒，ACTIVE 窗口永远不结束，持续多调 Agent/LLM。多个存档共用一个 scheduler 时，A 存档的 ACTIVE 还会影响 B 存档。

**建议**：只取本回合事件（或传入 `turn_id` 过滤）；调度器按 `game_id` 隔离，或每个回合/存档新建实例；补“进入一次后 N 回合休眠”测试。

### R-09 人物卡生成限流可绕过且 `_hits` 无界增长

- `src/textgame/services/card_service.py:205-218`：`_hits.setdefault(key, deque())` 后只 append，从不删除空键。
- `src/textgame/api/routes/cards.py:118-122`：`_client_key()` 使用 `IP + 客户端可控的 X-Session-Id`。

**影响**：每个新 `X-Session-Id` 都创建一个 deque，内存持续增长；轮换 header 即可绕过 5 次/分钟限制。默认 localhost 单机低风险，但通过 `--host 0.0.0.0`/反代暴露后是可用 DoS 面。

**建议**：定时清理空键/有界 LRU；会话标识由服务端签发或只用 IP；加“大量不同 session”测试。

### R-10 快照表没有 `(game_id, snapshot_version)` 唯一约束

- `src/textgame/persistence/tables.py:70-86`：`SnapshotRow` 只有自增 `id`，没有唯一约束。
- `src/textgame/persistence/sqlite_repository.py:207-235`：`save_snapshot()` 先 select 再 insert/update。

**影响**：并发提交时两个事务可能都算出同一个 `snapshot_version`，都插入成功；`latest_snapshot` 按 `version/id` 取一条，重建时可能取到与事件流不一致的快照。

**建议**：加 `UniqueConstraint("game_id","snapshot_version")` 并用 upsert；或让快照版本在单写者锁内递增。

### R-11 NaN/Infinity 会从 500 暴露，而不是 422

- `src/textgame/domain/hashing.py:18-26`：`canonical_json(..., allow_nan=False)`。
- 但世界书/场景导入的 `json.loads` 没有 `parse_constant` 拒绝 NaN：`world_book_service.py:644-673`、`user_scenario.py:412-431`、`scenario_repository.py:100`。
- `src/textgame/services/card_service.py:691-709`：`options` 用默认 `json.dumps`（允许 NaN），随后 `params_hash` 才用 `canonical_json`。

**复现**：场景 `stats={"x": NaN}` → `parse_definition()` 返回 `ok=True`；后续 `canonical_json()` 抛 `ValueError: Out of range float values are not JSON compliant`；路由只捕获业务异常，最终 500。

**建议**：所有上传 JSON 统一 `parse_constant` 拒绝 NaN/Infinity，并把错误映射成 `INVALID_INPUT`/`SCHEMA_VIOLATION`。

### R-12 世界书正则键可能 ReDoS

- `src/textgame/domain/world_book.py:481-505`：只限制 pattern ≤200 字符、输入 ≤2000 字符，然后直接 `re.compile()`/`re.search()`。
- `src/textgame/domain/world_book.py:508-525`：`_regex_key_issue()` 只检查长度、flags 和语法，不拦截灾难性回溯。
- `src/textgame/domain/world_book.py:908-930`：激活阶段对每回合/每次 `/view` 执行。

**影响**：恶意世界书（如 `/(a+)+$/`）可以在每次回合激活或 `/view` 时把 CPU 拉满。本地单机也影响体验，暴露到网络就是 DoS。

**建议**：拒绝嵌套量词/重叠前后缀，或改为 RE2/子进程超时；至少导入时将高风险 pattern 降级为字面量。

### R-13 LLM 重试等待不受整链 deadline 约束

- `src/textgame/llm/litellm_client.py:462-492`：每次 attempt 前检查 `deadline`，但 tenacity 的 `wait_exponential_jitter(initial=1.0, max=8.0)` 在两次 attempt 之间 sleep，sleep 前不检查剩余时间。

**影响**：`req.timeout_s=20` 的调用可能因为等待 + 下一次 attempt 超过 20s，慢请求堆积。

**建议**：自定义 `wait`/`stop` 用 `deadline-now`，或 sleep 前抛 `_DeadlineExceededError`。

### R-14 解析/制造 Agent 未设置 `prompt_version`

- `src/textgame/agents/parser_agent.py:170-180`：`_build_request()` 没传 `prompt_version`。
- `src/textgame/agents/craft_agent.py:62-77`：同样没传；`_PROMPT_VERSION="craft@v1"` 只出现在返回 data 里（`:97`）。
- `src/textgame/llm/client.py:69,141-161`：`LLMRequest.prompt_version` 默认 `"unversioned"`，而 `compose_idempotency_key()` 和 `LLMCallLog` 成功复用都会用它。

**影响**：Prompt 改版后进程内仍可能按同一个 key 复用旧成功结果；日志无法按版本追溯。

**建议**：给两个 Agent 加 `PROMPT_VERSION` 并传入 `LLMRequest`，测试断言请求里的版本。


### R-15 LLM 叙事拿不到制造产物名

- `src/textgame/services/craft_service.py:333-334`：`result.produced_items` 是 `Item` dict 列表。
- `src/textgame/agents/narrator_agent.py:254-258`：却把它当 `item_id` 列表交给 `_item_name()`。
- `src/textgame/agents/narrator_agent.py:443-448`：`_item_name()` 对非 `str` 返回 `None`。

**影响**：打造成功后，LLM 事实里没有产物名，叙事可能不写具体物品；模板叙事器反而正确处理了 dict（`orchestration/narrator.py:279-287`）。

**建议**：兼容 dict（取 `name`）或把事件字段改成 id 列表 + 名称映射。

### R-16 `/view.lore` 与下一回合实际注入的 turn 不一致（off-by-one）

- `src/textgame/services/query_service.py:181-187`：`activate_entries(..., turn_id=state.turn, ...)`。
- `src/textgame/orchestration/turn_orchestrator.py:696-702`：下一回合用 `state.turn + 1`。
- `src/textgame/domain/world_book.py:1045`：定时效果模拟只回放 `turn_id < current_turn_id`。

**影响**：`probability`、`sticky`、`cooldown`、`delay` 在 `/view.lore` 和下一回合注入之间可能不同，但两处 docstring 都声称“同口径”。

**建议**：抽同一 helper，明确 `as_of_turn`；`/view` 使用 `state.turn + 1`，或显式说明展示的是上一回合。

### R-17 `in` / `out` 出口不可导航且英文泄漏

- `src/textgame/domain/types.py:24`：`Direction` 包含 `in`/`out`。
- `src/textgame/domain/scenario.py:45-55`：`DIRECTION_KEYWORDS` / `DIRECTION_NAMES` 只有北南东西上下。
- `src/textgame/orchestration/intent.py:140,255-270`：`_DIRECTION_VALUES` 只含那 6 个方向，`exit_refs` 里 `id="in"/"out"` 会被跳过。
- `src/textgame/domain/suggestions.py:50-57`：`_direction_cn()` 未知方向回退原值，会生成“向in走”。

**影响**：用户场景 `RoomSpec.exits` 允许 `in/out`（`domain/user_scenario.py:63-65`），但确定性解析/建议不可用，只有 LLM 直接给 `direction="in"` 时才能走；中文提示里还可能露出英文。

**建议**：补“里/外/进/出”的映射与 `DIRECTION_NAMES`，或在用户场景校验里禁止 `in/out`。

### R-18 校验端点没有请求体上限

- `src/textgame/api/routes/scenarios.py:194-196`：`await request.body()` 直接读全文，`parse_definition()` 之后才检查 256KB。
- `src/textgame/api/routes/world_books.py:62-70,233-238`：`WorldBookValidateBody.payload: Any`，没有大小/深度限制，`validate_payload()` 也不检查。
- `turns`/`games` 等同样没有全局 body size 中间件。

**影响**：本地单机低风险，暴露到局域网/反代时是内存/CPU DoS 面。

**建议**：加 ASGI body limit 或统一 `Content-Length` 预检，validate 与 import 使用同一上限。


### R-19 自由制造在 Resolver 内部调 LLM（条件性）

- `src/textgame/main.py:86-90`：把 `CraftAgent(llm_client)` 注入 `CraftService`。
- `src/textgame/services/craft_service.py:168-204`：`_craft_free()` 调 `generator.generate()`。
- `src/textgame/orchestration/resolver.py:290-295`：该调用位于 `Resolver.resolve()` 的 `services.call()` 内。

**影响**：只有 `TEXTGAME_FREE_CRAFT_ENABLED=true` 时可达，但这会破坏 Resolver“纯函数、重试只重裁决不重调模型”的契约；CAS 冲突重试可能重复调用 LLM/扣费。项目文档 K4 已登记该风险，但代码仍按默认路径接线。

**建议**：M5 前迁到 pre-queue/编排层生成，或显式禁止生产开启。

### R-20 Card/WorldBook 把失败响应缓存进幂等表

- `src/textgame/services/card_service.py:960-975`、`src/textgame/services/world_book_service.py:770-784`：把校验失败/404/413 以 `__error__` 形式写进幂等表。
- `src/textgame/services/card_service.py:932-958`、`world_book_service.py:742-768`：同 key 修正文件再提交时先比较 `params_hash`，不同就直接 409，不会重新解析。

**影响**：用户修正文件后用同一 `Idempotency-Key` 再提交，通常要多点一次（先收到 409，前端清键后才能重试）；容易被误认为“第二次导入又失败”。`ScenarioService` 已经不缓存校验失败，可对照。

**建议**：不缓存 4xx 校验失败，或 409 时自动 rotate。

### R-21 每回合全量读事件 / LLM 调用日志无界

- `src/textgame/services/game_engine.py:219-221`：每次 `submit()` 都 `read_events()` 全量事件。
- `src/textgame/orchestration/turn_orchestrator.py:1206`：多角色链路每回合全量读历史；`:1282` 调度器再读。
- `src/textgame/services/query_service.py:439`：`list_games()` 对每个存档全量读事件找最后叙事。
- `src/textgame/llm/records.py:105-136`：`InMemoryLLMCallLog._success` 无上限，进程内成功记录一直增长。

**影响**：长局/多存档时每回合 O(n) 放大；LLM 调用日志随进程运行时间增长。

**建议**：`read_events(after_seq=...)` 增量读、列表接口限制/缓存、LLM 调用日志加 TTL/LRU。


## 3. 前端：玩家可见状态错误

### R-22 `loadGame` 失败会保留上一个对局的 view/history

- `web/src/stores/game.ts:321-339`：`this.gameId = gameId` **先赋值**，然后才 `getGame()`/`refreshView()`/`loadHistory()`；失败路径没有清空 `view`/`history`。
- `web/src/views/GameView.vue:148-161`：`ensureLoaded()` 只要 `store.gameId === id && store.version > 0 && store.view !== null` 就提前 return。

**影响**：从对局 A 打开一个不存在/加载失败的对局 B，页面仍显示 A 的房间、回合和历史；如果 `getGame()` 成功但 `refreshView()` 失败，`applySnapshot` 会把 version 更新成 B 的，而 view 仍是 A 的，`ensureLoaded` 还会认为“已加载”。

**建议**：进入 `loadGame` 时先清空派生状态，或先请求到本地变量、成功后再整体赋值；`ensureLoaded` 额外校验 `view.game_id === id`；补“无效 game id / view 失败”组件测试。

### R-23 零事件只读回合的叙事刷新后会消失

- `web/src/stores/game.ts:408-411`：成功回合会把 `text` 追加进本地 history（包括“环顾四周/查看背包”这类零事件回合）。
- `web/src/stores/game.ts:275-277`：刷新时用 replay 重建；`web/src/utils/history.ts:89-95` 的 `mergeReplayHistory()` 只保留 `notice`，会丢掉本地 narrative。
- 零事件回合没有 Event，replay 里没有叙事，所以刷新后“看/背包”的正文消失。

**影响**：同一回合在刷新前后显示不一致；玩家会以为记录丢了。

**建议**：不要把零事件回合写进 history，或单独持久化客户端叙事；明确“冒险记录 = 事件记录”的契约。

### R-24 前端幂等键双层封装；create key 缺 world_book_id

- `web/src/api/client.ts:145-147`：`request()` 对 `options.actionKey` 再调用一次 `getOrCreate()`。
- 但各 store 先自己 `getOrCreate()` 生成 UUID 再传入：`web/src/stores/game.ts:389`、`cardGenerate.ts:202,316`、`cards.ts:197`、`scenarios.ts:203`、`worldBooks.ts:203`。
- 实际发送的 `Idempotency-Key` 是第二个 UUID，清理的却是 namespace 下的实际键；当前手动重试能工作，但语义很脆弱，后续改动很容易把“重试复用/成功后清键”弄坏。
- `web/src/stores/game.ts:297-299`：`create-game:${card_id}:${seed}:${scenario}:${scenario_id}` 没有包含 `world_book_id`；如果创建响应丢失后只改世界书重试，可能命中旧幂等记录并 409。

**建议**：让 `request()` 只接受最终 key，由 store 统一生成/清理；create 的 action key 纳入所有创建参数。

## 4. 安全/部署条件项

### R-25 默认只适合 `127.0.0.1`：无鉴权/Host 校验，且 `api_base` 任意

- `src/textgame/api/app.py:188-243`：没有 `TrustedHostMiddleware`、没有鉴权；默认 launcher 绑 `127.0.0.1`（`launcher.py:37`），但 `--host 0.0.0.0` 或反向代理/DNS rebinding 时所有写端点（建局、回合、导入、设置）都会裸露。
- `src/textgame/api/routes/app_control.py:49-54`：只对 `/shutdown` 做了回环限制，其他端点没有。
- `src/textgame/api/routes/settings.py:66-70`：接受任意 `api_base`；`src/textgame/llm/credentials.py:97-99` 只检查 `http(s)://` 前缀。
- `src/textgame/llm/runtime.py:287-318`：运行期用它构造 LiteLLM，并在测试连接时把 key 发过去。

**影响**：能访问设置端点的人可以把玩家 key 指向任意 HTTP 端点（SSRF/密钥外送），也能读走对局数据；DNS rebinding 可以绕过“默认 localhost”的假设。

**建议**：非 loopback 强制 token/鉴权 + `TrustedHostMiddleware`；`api_base` 限定供应商域名或至少拒绝私网/云元数据地址；默认保持 loopback。

## 5. 复现记录与验证边界

### 5.1 已做的最小复现（纯对象/内存仓储，不写库）

| 编号 | 复现内容 | 结果 |
| --- | --- | --- |
| R-01 | story 背包 `[火把, 铜线, 药水]` → `build_craft_state_slice()` | `ValidationError: inventory.consumable.[key]` |
| R-01 | story 背包 `[药水, 烈酒]` → `QueryService.get_inventory()` | `治疗药水 qty=2`（烈酒被合并） |
| R-02 | 场景 `attributes={"luck":1}` | 导入 `ok=True`，建局 `ValidationError: unknown attribute keys` |
| R-02 | 场景房间自指出口 | 导入 `ok=True`，建局 `ValidationError: room cannot exit to itself` |
| R-02 | 同一 item key 同时在背包和房间 | 导入 `ok=True`；状态中同一 `item_id` 出现在两处 |
| R-06 | `EventLog("seed-b").append()` 一条 `seed-a` 盖章事件 | `append` 成功，`replay()` 抛 `ReplayMismatchError` |
| R-06 | `GameEngine.create_game(save_seed="seed-b", initial_state=seed-a)` | 返回 `ok=True`，用 `seed-a` replay 立即 mismatch |
| R-11 | 场景 `stats={"x": NaN}` | `parse_definition().ok=True`，`canonical_json()` 抛 `ValueError` |

### 5.2 未覆盖/未验证

- 未跑全量 pytest/vitest/ruff/mypy/import-linter；本报告不代替质量门结果。（2026-11-11 修复批次已补跑，见 §7「批次验证汇总」。）
- R-03/R-04/R-05 是代码路径确认，未做 fault-injection/并发压测/旧库构造；建议在修复时补测试。
- R-08/R-19/R-25 是条件项：默认装配不触发；不要把它们当成当前已发生的线上故障。
- 前端 R-22/R-23/R-24 未跑真实浏览器 e2e；仅按 store/组件代码路径审查。

## 6. 修复顺序与回归测试建议

### 6.1 建议顺序

1. R-01 story tags / 背包聚合 / 制造键
2. R-02 自定义场景导入→建局校验
3. R-03 commit 后快照/幂等写失败
4. R-04 创建/导入幂等竞态
5. R-05 旧库升级
6. R-06 EventLog rng/replay 校验
7. R-07 LLM 白名单
8. R-22 前端 `loadGame` 失败状态
9. 其余 P1/P2（R-08 起）

### 6.2 最小回归测试清单

- `story` 背包同时有药水/火石/锻锤时打造信号火把成功；同时有药水+烈酒时 `/view.inventory` 不合并。
- 自定义场景：未知属性 / 房间自指 / item 双放置 → 导入阶段 422（不是建局 500）。
- `repo.commit` 成功后 `save_snapshot` 抛异常 → 回合仍返回成功（或明确“已提交”状态）。
- 两个线程同 `Idempotency-Key` 同时 create/import → 只产生一个实体。
- 旧 M2 库直接启动/建局/读视图 → 自动升级或明确 fail-fast。
- `EventLog` append 错 seed → 立即拒绝；`GameEngine.create_game` seed 不一致 → 422/SCHEMA_VIOLATION。
- LLM 解析输出 `inspect_item`/`craft_item` → 不走 fallback；两个 Agent 请求都带 `prompt_version`。
- 前端无效 game id、`getGame` 成功但 `/view` 失败 → 不显示上一局 view/history。
- 世界书正则 `/(a+)+$/` 导入 → 被拒绝或降级为字面量。
- 人物卡生成限流：轮换 `X-Session-Id` 不能绕过，`_hits` 不随不同 session 无界增长。

---

**报告结论**：当前代码的主干设计（确定性规则、事件溯源、单向写入、分层、离线 golden）是可靠的；上述问题主要集中在**内容数据与通用聚合口径不一致（R-01/R-02）**、**提交后旁路写（R-03）**、**幂等/并发（R-04）**、**升级/回放完整性（R-05/R-06）** 以及 **LLM/前端接线细节**。建议按 §6.1 的顺序逐项修复并补对应回归测试。


## 7. 修复记录（2026-11-11 起，按批次回写）

> 状态：`已修复` = 代码 + 回归测试落地；`部分修复` = 已降低风险但保留已知限制；`待处理` = 尚未开始。
> 验证命令与结果见每项末尾；完整清单见 `CHANGELOG.md [Unreleased]`。

### R-01 story 语义标签被当模板键（已修复）

- **修法**：
  1) `domain/crafting.py` 新增 `item_template_key(item)`：仅当 `tags[0]` 匹配 `ItemTemplateId` 正则时返回模板键，否则 `None`；`build_craft_state_slice()` 跳过无模板键物品（语义标签不再触发校验失败）。
  2) `QueryService.get_inventory()`、`NarratorAgent._inventory_summary()`、`CraftService._build_craft_view()` 统一改用该函数；背包展示对无模板键物品回退实例 id，语义标签不再互相合并。
  3) `domain/scenario_story.py` 物品 `tags[0]` 统一为模板键：`item.potion` / `item.liquor` / `item.flint` / `item.forge_hammer`，语义标签后置。
- **回归测试**：`tests/unit/test_scenario_story.py::test_inventory_does_not_merge_semantic_tags`、`::test_story_craft_succeeds_with_semantic_items_in_inventory`、`::test_item_catalog_matches_spec`。
- **验证**：`pytest tests/unit/test_scenario_story.py tests/unit/test_crafting.py` 全绿；`test_game_view` / `test_craft_deterministic` 无回归。

### R-02 用户场景导入成功但建局 500（已修复）

- **修法**：
  1) `PlayerSpec.attributes` 增加键白名单（`str/dex/con/int/wis/cha/max_hp`）与 `[-10,100]` 值域校验。
  2) `_check_references()` 新增：房间自指出口、同一 item 同时在背包与房间、玩家背包重复 key 的阻断错误。
  3) `parse_definition()` 增加建局 dry-run（`derive_ids + build_initial_state`），把 `Character`/`Room` 模型约束提前转成带 JSON 路径的导入错误。
  4) `ScenarioService.build_game_payload()` 捕获 `ValueError` → `ScenarioValidationError`（422），旧库存定义的建局失败不再 500。
- **回归测试**：`tests/unit/test_user_scenario.py::test_unknown_player_attribute_rejected_at_import`、`::test_room_self_exit_rejected_at_import`、`::test_item_in_inventory_and_room_rejected`、`::test_duplicate_inventory_item_rejected`；`tests/integration/test_scenario_api.py::test_import_unknown_attribute_422_with_field_paths`。
- **验证**：`pytest tests/unit/test_user_scenario.py tests/integration/test_scenario_api.py` 全绿。

### R-03 commit 成功后快照/幂等写失败被报告为回合失败（已修复）

- **修法**：`GameEngine._save_snapshot()` / `_maybe_save_snapshot()` 对快照读取与写入做 `try/except Exception` + WARNING；`TurnOrchestrator._save_idempotent()` 同样吞异常并记 WARNING。状态主提交成功后响应恒为成功。
- **回归测试**：`tests/unit/test_game_engine.py::test_create_game_snapshot_failure_does_not_fail_commit`、`::test_submit_snapshot_failure_does_not_fail_commit`；`tests/integration/test_idempotency_concurrency.py::test_turn_succeeds_when_idempotent_write_fails`。
- **验证**：上述用例全绿且事件只写一次。

### R-04 创建/导入幂等 check-then-act（已修复，单进程范围）

- **修法**：新增 `services/idempotency_locks.py`（64 段进程级锁 + `idempotency_lock(store_key)`）。`TurnOrchestrator.create_game()`/`run()`、`CardService._idempotent()`、`WorldBookService._idempotent()`、`ScenarioService._idempotent()` 全部进入同一分段锁；`_idempotent` 保留原逻辑为 `_idempotent_unlocked`，锁只包住“查表 → 执行 → 写幂等”。
- **已知限制**：与 D-M4-15 一致，仅解决单进程；多进程部署仍需数据库唯一键抢占（已在锁模块与各调用方 docstring 标注）。
- **回归测试**：`tests/integration/test_idempotency_concurrency.py::test_concurrent_same_key_create_game_makes_one_entity`；`tests/unit/test_scenario_service.py::test_concurrent_same_key_import_creates_one_scenario`；既有 `run()` 并发用例全绿。
- **验证**：`pytest tests/integration/test_idempotency_concurrency.py tests/unit/test_scenario_service.py` 全绿。

### R-05 旧库不会自动迁移（已修复）

- **修法**：
  1) `persistence/database.py` 新增 `ensure_schema()`：内存/空库走 `create_all`；检测 `snapshots.version` 缺失、`events` 非复合主键或 `alembic_version` 落后 → 释放连接后调用 `upgrade_legacy_database`（先迁移再 create_all，避免 Alembic `op.create_table` 撞已存在表）；健康库只补缺失新表、不跑 Alembic。
  2) `main.py` 启动改用 `ensure_schema(engine, db_url=settings.db_url)`；失败抛 `RuntimeError` 并给出手动升级命令。
  3) `legacy_upgrade` 增加迁移目录定位回退（源码仓库根 → wheel 内 `textgame/alembic`）；`pyproject.toml` wheel/sdist 打包 `alembic/` 与 `alembic.ini`。
- **回归测试**：`tests/integration/test_legacy_upgrade.py::test_ensure_schema_auto_upgrades_m2_database`、`::test_ensure_schema_is_noop_for_head_database`。
- **验证**：`pytest tests/integration/test_legacy_upgrade.py tests/integration/test_sqlite_app_boot.py` 全绿；`mypy` 全绿（当步 111 个源文件，最终 113 见「批次验证汇总」）。

### R-06 `EventLog.append` 不校验 rng_seed（已修复）

- **修法**：
  1) `EventLog.append()` 逐条校验 `rng_seed == derive_seed(save_seed, turn_id, actor_id, method, seq)`，不一致立即拒绝（原子性保持）。
  2) `GameEngine.create_game()` 校验 `initial_state.rng_state.save_seed == save_seed` 且存在 `PLAYER_ID`，不满足返回 `SCHEMA_VIOLATION` 且零写入。
- **回归测试**：`tests/replay/test_m1_event_replay.py::test_append_rejects_tampered_rng_seed`、`::test_replay_detects_tampered_rng_seed`（改为白盒注入，保留回放防线）；`tests/unit/test_game_engine.py::test_create_game_rejects_initial_state_seed_mismatch`。
- **验证**：`pytest tests/replay/test_m1_event_replay.py tests/unit/test_game_engine.py` 全绿。

### R-07 LLM 解析白名单缺少 inspect_item / craft_item（已修复）

- **修法**：`IntentParseAgent.__init__` 在未显式传 `tool_whitelist` 时从 `tool_specs` 派生白名单（`frozenset(spec.name for spec in tool_specs)`）；无 specs 才回退 M2 六工具。`main.py` 现有装配自动生效。
- **回归测试**：`tests/unit/test_parser_agent.py::test_whitelist_is_derived_from_tool_specs`。
- **验证**：`pytest tests/unit/test_parser_agent.py` 全绿。

### R-14 解析/制造 Agent 未设置 prompt_version（已修复）

- **修法**：`parser_agent.PROMPT_VERSION = "parse@v1"`、`craft_agent._PROMPT_VERSION = "craft@v1"` 均传入 `LLMRequest.prompt_version`，幂等键与调用日志可按版本追溯。
- **回归测试**：`tests/unit/test_parser_agent.py::test_request_carries_prompt_version`（制造侧既有 `test_free_craft` 已断言 `craft@v1`）。
- **验证**：`pytest tests/unit/test_parser_agent.py tests/integration/test_free_craft.py` 全绿。

### R-15 LLM 叙事拿不到制造产物名（已修复）

- **修法**：`NarratorAgent` 处理 `event.result.produced_items` 时兼容完整 `Item` dict（取 `name`），再回退旧的 `item_id` 查询。
- **回归测试**：既有 `tests/unit/test_llm_narrator_facts.py` + `test_m4_golden_replay`（事件结构不变）。
- **验证**：`pytest tests/unit/test_llm_narrator_facts.py tests/replay/test_m4_golden_replay.py` 全绿。

### R-22 前端 loadGame 失败保留上一个对局状态（已修复）

- **修法**：`web/src/stores/game.ts` 新增 `resetForLoad()`，`loadGame()` 先清空 `view/history/version/turn/lastText/narrativeSource/...` 再赋 `gameId`；`GameView.ensureLoaded()` 增加 `store.view.game_id === id` 校验。
- **回归测试**：`web/tests/unit/gameStore.test.ts::载入失败：不保留上一个对局的 view/history/version`、`::getGame 成功但 /view 失败：不显示旧对局状态`。
- **验证**：`pnpm --dir web test -- tests/unit/gameStore.test.ts` 21+2 例全绿。


### R-08 BatchSleep 的 SCENE_ENTRY 永久唤醒（已修复）

- **修法**：`BatchSleepScheduler._player_entered_room(events, *, turn_id)` 只扫描 `event.turn_id == turn_id` 的玩家移动；旧回合的移动不再每回合唤醒同一房间 NPC。
- **回归测试**：`tests/integration/test_batch_sleep.py::test_scene_entry_ignores_previous_turn_moves`（并修正既有用例的事件 turn_id）。
- **验证**：`pytest tests/integration/test_batch_sleep.py` 21 例全绿。

### R-09 人物卡生成限流可绕过且 _hits 无界（已修复）

- **修法**：`_client_key()` 只按客户端 IP 计（`X-Session-Id` 不再参与配额）；`RateLimiter._hits` 上限 1024 个主体，新增 `_evict_if_needed()` 清理过期/空键并按插入顺序淘汰最旧键。
- **回归测试**：`tests/integration/test_card_generate.py::test_rate_limit_is_per_ip_and_cannot_be_bypassed_by_session`。
- **验证**：`pytest tests/integration/test_card_generate.py` 全绿。

### R-10 快照表缺唯一约束（已修复）

- **修法**：
  1) `SnapshotRow` 增加 `UniqueConstraint("game_id", "snapshot_version")`；
  2) 新增 Alembic 0006：先去重历史行，再 batch 重建 SQLite 表加约束；
  3) `SqliteRepository.save_snapshot()` 改 `INSERT ... ON CONFLICT DO UPDATE`（跨进程 upsert）；
  4) `legacy_upgrade` head 升到 0006，`_verify_head` 校验唯一约束；`ensure_schema` 检测缺失约束并自动升级；
  5) `snapshot_lock(game_id)` 仍保留，减少同进程无谓冲突。
- **回归测试**：`tests/integration/test_alembic_migration.py`（schema 快照与 ORM 逐表比对含唯一约束）、`test_legacy_upgrade.py`（head=0006）、`test_snapshot_restore.py`。
- **验证**：上述 51 例 + 全量集成/replay 全绿。

### R-11 NaN/Infinity 解析后置失败（已修复）

- **修法**：场景/人物卡/世界书上传 JSON 统一 `json.loads(parse_constant=_reject_non_finite)`；人物卡 `options` 与世界书 `validate_payload` 使用 `json.dumps(..., allow_nan=False)`；世界书 validate 额外检查 payload 序列化后 ≤256KB。错误映射 422 `INVALID_INPUT` / 200 `valid=false`。
- **回归测试**：`tests/unit/test_user_scenario.py::test_parse_json_non_finite_rejected`；世界书/人物卡既有 JSON 错误用例。
- **验证**：`pytest tests/unit/test_user_scenario.py tests/unit/test_world_book_tavern.py tests/integration/test_character_card_api.py` 全绿。

### R-12 世界书正则键可能 ReDoS（已修复：解析树 + 启发式）

- **修法**：在原有嵌套量词启发式之外，新增 `_has_nested_repeat()`：用 `re` 解析树遍历 `SUBPATTERN/BRANCH/REPEAT`，任何「重复内部又含重复」直接拒绝（覆盖 `(?:a+){2,}` 等启发式漏网写法）；导入期给 `REGEX_KEY_INVALID` 告警并按字面量匹配。
- **保留限制**：不是完整 RE2 语义；极端重叠分支仍可能慢，但输入长度限制 2000 字符且拒绝嵌套重复已覆盖主要 ReDoS 形态。
- **回归测试**：`tests/unit/test_world_book_tavern.py::test_parse_rejects_nested_quantifier_regex_key`（含 `(?:a+){2,}`）。
- **验证**：`pytest tests/unit/test_world_book_tavern.py tests/integration/test_world_book_tavern_flow.py` 全绿。

### R-13 LLM 重试等待不受 deadline 约束（已修复）

- **修法**：`LiteLLMClient._call_backend` 用自定义 `_wait_within_deadline`：等待前计算 `deadline - now`，剩余不足 `_MIN_REMAINING_S` 直接抛 `_DeadlineExceededError`；等待时长不超过剩余预算。
- **验证**：`pytest tests/unit/test_llm_client.py`（25 例）全绿。


### R-16 `/view.lore` 与下一回合 off-by-one（已修复）

- **修法**：`QueryService._world_book_fields()` 的 `activate_entries(turn_id=...)` 改为 `state.turn + 1`，与 `TurnOrchestrator._lore_entries_for()` 的下一回合注入口径一致；docstring 同步说明。
- **验证**：`pytest tests/integration/test_world_book_flow.py tests/integration/test_story_llm_lore_wiring.py` 全绿。

### R-17 in/out 出口不可导航且英文泄漏（已修复）

- **修法**：`DIRECTION_NAMES` 补 `in: "内"` / `out: "外"`；新增 `DIRECTION_ALIASES`（里面/内部/进入/进去/进屋/外面/出去/走出）；`intent._DIRECTION_VALUES` 从 `DIRECTION_NAMES` 派生，`exit_refs` 的 in/out 可导航；纯方向短语字符集补里/外/进/出/内；不做单字宽泛误配。
- **回归测试**：`tests/unit/test_intent_coverage.py::test_in_out_alias_maps_to_move`、`::test_in_out_exit_ref_navigation`。
- **验证**：`pytest tests/unit/test_intent_coverage.py tests/unit/test_suggestions.py` 全绿。

### R-18 校验端点无请求体上限（已修复）

- **修法**：`api/app.py` 新增纯 ASGI `_BodyLimitMiddleware`：Content-Length 预检 + 实际 `receive` 字节计数（覆盖 chunked），>1MB 在路由/解析前返回 413 信封；场景导入仍保持 256KB 细粒度上限，世界书 validate payload 增加序列化后 ≤256KB 检查。
- **回归测试**：`tests/integration/test_healthz.py::test_request_body_over_limit_returns_413`。
- **验证**：`pytest tests/integration/test_healthz.py tests/integration/test_scenario_api.py tests/integration/test_turn_api.py` 全绿。

### R-19 自由制造在 Resolver 内调 LLM（已修复：前端运行期 opt-in）

- **修法**：
  1) 新增 `FreeCraftSwitch`，`CraftService` 与 `DeterministicIntentParser` 共享同一实例，网页设置页可热切换（无需重启）；
  2) 新增 `GET/PUT /api/v1/settings/free-craft`：开启要求大模型已启用 + 前端二次确认 `accept_resolver_llm_risk=true`（等价运行期 opt-in），关闭立即生效；
  3) 偏好持久化到 `data/free_craft.json`（`TEXTGAME_FREE_CRAFT_SETTINGS_FILE` 可覆盖），启动时优先于 env；写入用临时文件 + `os.replace` 原子替换，损坏文件安全回退关闭；
  4) 无头/生产仍可用 `TEXTGAME_FREE_CRAFT_ENABLED=true` + `TEXTGAME_ALLOW_RESOLVER_LLM=true` 显式开启；
  5) 架构风险本身未消除（仍会在裁决/重试链路调 LLM），设置页明确提示“可能重复计费、建议单人本地使用”，M5 前仍应迁移到 pre-queue。
- **回归测试**：`tests/unit/test_free_craft_settings.py`（共享 switch / store）、`tests/integration/test_free_craft_settings_api.py`（409/持久化/落盘失败）、`tests/integration/test_sqlite_app_boot.py::test_free_craft_preference_is_restored_and_wired`、`web/tests/unit/{freeCraftClient,freeCraftStore,settingsView}.test.ts`。
- **验证**：后端新增 11 例 + 前端 targeted 17 例全绿（全量门禁见汇总）。

### R-20 Card/WorldBook 把失败响应缓存进幂等表（已修复）

- **修法**：`CardService._save_error_idempotent()` / `WorldBookService._save_error_idempotent()` 对**全部 4xx**（含 403/404/413/422）不再写幂等表；只有 5xx 瞬时故障保留可重放缓存。修正输入后同键可直接重试。
- **回归测试**：`tests/integration/test_character_card_api.py::test_import_failure_is_not_replayed_with_same_key`、`::test_import_validation_failure_is_not_cached`。
- **验证**：`pytest tests/integration/test_character_card_api.py` 60 例全绿。

### R-21 每回合全量读事件 / LLM 调用日志无界（已修复）

- **修法**：
  1) `InMemoryLLMCallLog` 成功复用表改 `OrderedDict` LRU（上限 1024），失败记录也有界（1024）；
  2) `GameEngine` 每存档首次提交做一次全量 `append + replay` 完整性校验，之后用 `EventLog.resume(last_seq)` 只读尾部增量盖章；
  3) `GameRepository.read_recent_events(game_id, limit)` + `QueryService.list_games` 只读尾部 200 条事件找最近叙事，不再逐存档全量读取。
- **回归测试**：`tests/unit/test_llm_client.py::test_in_memory_call_log_success_table_is_bounded`、`tests/integration/test_game_list.py::test_read_recent_events_returns_tail_in_seq_order`。
- **验证**：`pytest tests/integration/test_game_list.py tests/replay` 全绿。

### R-23 零事件只读回合叙事刷新后消失（已修复）

- **修法**：`game.ts` 仅在 `result.events.length > 0` 时把叙事追加进 history；零事件回合只更新 `lastText`，明确“冒险记录 = 事件记录”。该修复随后被发现引入「零事件回合正文刷新后不可见」回归 → 补「最近回应」可见区块（`web/src/utils/history.ts::lastResponseText` + `GameView`）+ 4 例单测。
- **回归测试**：`web/tests/unit/gameStore.test.ts::零事件只读回合不写入 history`。
- **验证**：`pnpm --dir web test` 当步 368 例全绿（最终全量 60 文件 / 383 例，含 R-23 回归修复新增 4 例，见「批次验证汇总」）。

### R-24 前端幂等键双层封装（已修复）

- **修法**：`api/client.ts::request()` 直接把 `actionKey` 作为 `Idempotency-Key`，不再二次 `getOrCreate`；各 store 统一用**逻辑动作名** `getOrCreate`/`clear`（card-import / scenario-import / world-book-import / card-generate / card-save / turn:<game>:<version>）；`create-game` 的 action key 追加 `world_book_id`。
- **回归测试**：`apiClient` / `cardsClient` / `lorebookClient` / `scenarioClient` 的键断言；store 409 换键用例。
- **验证**：`pnpm --dir web test` 当步 368 例全绿；`pnpm --dir web typecheck` 通过（最终全量 60 文件 / 383 例，见「批次验证汇总」）。

### R-25 无鉴权/Host 校验、api_base 任意（已修复）

- **修法**：
  1) `api/app.py` 增加 `TrustedHostMiddleware`，默认仅允许 `127.0.0.1/localhost/[::1]/testserver`，可用 `TEXTGAME_ALLOWED_HOSTS` 覆盖；
  2) 新增 `_AccessTokenMiddleware`：非 loopback 客户端必须带 `Authorization: Bearer <TEXTGAME_API_TOKEN>` 或 `X-API-Token`；未配置 token 时非 loopback 一律 403（默认只适合 127.0.0.1）；
  3) `llm/credentials.py::_validate_fields()` 拒绝云元数据主机名、链路本地/组播/保留 IP（loopback/私网仍允许本地 Ollama/LM Studio）。
- **回归测试**：`tests/integration/test_healthz.py::test_untrusted_host_header_rejected`、`::test_non_loopback_requires_api_token`、`::test_non_loopback_without_configured_token_is_forbidden`；`tests/unit/test_llm_credentials.py` 新增元数据地址用例。
- **验证**：`pytest tests/integration/test_healthz.py tests/unit/test_llm_credentials.py` 全绿。

### 批次验证汇总

- 后端：`uv run pytest -q` → **2233 passed**（collect 口径：unit 1327 + integration 665 + replay 69 + security 172 全绿）；覆盖率 `--cov-fail-under=80` → **TOTAL 87.56%**；`tests/replay -q` → **69 passed**（61 + provenance 7 + 新增 R-06 回归 1；未改 golden、未 `--snapshot-update`）。
- 静态：`ruff format --check .` 257 files already formatted；`ruff check .` 全过；`mypy src/textgame` 113 个源文件无问题；`lint-imports` 2 kept；`python scripts/check_docs.py` PASS。
- 前端：`pnpm --dir web test` **60 文件 / 383 例**全绿（`379` 为 Cline 当步快照；R-23 回归修复 +4 例后最终 383）；`pnpm --dir web typecheck` 通过；`pnpm --dir web build` 成功、`web/dist` 重建且连续两次 build 逐文件 SHA256 一致（36 文件，无 drift）；本地真实浏览器 e2e **26/26 步、126/126 断言 PASS**（离线 `TEXTGAME_LLM_ENABLED=false` + headless Edge，证据 `.work/e2e-web-r23/result.json`）。
- 备注：以上数字均为 2026-11-12 本机最终工作区实测（未提交工作树）；逐项改动清单见 `CHANGELOG.md [Unreleased]` 与本文 §7。
