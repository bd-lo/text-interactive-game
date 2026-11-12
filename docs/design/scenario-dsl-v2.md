# 场景 DSL v2 设计（条件谓词扩展 / flags / 多结局语义 / 场景包 schema v2）

> 状态：设计定稿（2026-11-12，dsl-v2 执行稿；Lead 终审）｜ 切片：N1 **029 设计**（本文件）+ **030 实现**
> ｜ 决策：ADR **D-N1-1**（`docs/decisions/2026-11-12-scenario-dsl-v2.md`）
> ｜ 关联：第 17 章 §17.9.2 N1 行、路线图 ADR D-N5（DSL v2 边界）/ D-N6（历史事件仅 033–034 可加）/ D-N7（不做清单）；
> v1 契约 `docs/design/user-scenario-modules.md`；实现 `src/textgame/domain/user_scenario.py`。

本文定义**用户自上传场景 JSON 的 v2 版本**：在 v1（`schema_version=1`）之上**加法扩展**条件 DSL、
新增 `flags` 派生命题、冻结「多结局选择」的确定性语义。**v1 文件逐字兼容**（旧字段/旧谓词/旧行为/旧测试
零改动）；v2 仍是「纯数据 + 白名单求值」，**无 eval / 无脚本 / 无表达式**，不新增 `EventType`、
不改 `GameState` 字段与 reducer、不改内置 4 场景 golden 与 demo/console 输出。

---

## 1. 背景、目标与非目标

### 1.1 背景

- v1 已交付（`docs/design/user-scenario-modules.md` §5）：JSON 定义 + 白名单条件（`in_room`/`has_item`/
  `has_item_tag`/`npc_state`/`hp_lte`/`hp_gte` + `all/any/not/always`）+ 目标/结局读时求值 + 进入伤害 +
  NPC 对话，导入即玩；P4 遗留项明确写着「条件 DSL 未实现 flag/quest/时间条件」。
- 下一步（N1 031 编辑器 / 032 分享）需要更表达力的条件，但**不得**引入执行面或破坏确定性回放。

### 1.2 目标

1. 条件可表达 **flag（派生命题）/ quest（目标进度串） / 物品（收集数量、物品位置）/ NPC 状态（所在房间、对话态）
   / 时间条件（回合与世界时间阈值）**；
2. **多结局**的选择语义写死为确定性规则（优先级、声明序、兜底、读时求值、结局后拒绝新回合）；
3. 场景包 `schema_version=2` 字段定义与 **v1→v2 兼容矩阵**；
4. 明确「v2 可表达 / 不可表达」边界（不可表达项给出原因与归属 033–034 或不做），避免后续反复讨论。

### 1.3 非目标（明确不做，见 §7）

- 历史语义条件（曾进入某房间 / 曾与 NPC 对话 / 首次进入触发 / 一次性对话 / 限时窗口触发一次）；
- 物品堆叠数量、玩家属性比较、`GameState.quests` 任务状态机、NPC 移动/日程、随机条件；
- 玩家主动「选择结局」的动作/菜单/新事件/新 UI（`endings` 仍是读时视图，不是事件）；
- 编辑器（031）、分享导出（032）、跑团模组（033–034）的实现；本章只定义 DSL 与实现契约。

---

## 2. 求值基础：为什么 v2 不需要改引擎

### 2.1 输入只有三样：定义 + 派生 ID + 既有 `GameState`

`evaluate_condition(condition, state, ids, *, definition=None)` 是**纯函数**：

| 输入 | 内容 | 来源 |
| --- | --- | --- |
| 条件树 | `Condition`（白名单谓词/组合子） | 已校验的 `ScenarioDefinition` |
| 派生 ID | `ScenarioIds`（作者 key → `room_/item_/npc_` ID，`sha256(scenario_id|kind|key)`） | v1 规则不变，确定性 |
| 状态 | `GameState`：`turn` / `world_time` / `characters` / `rooms` / `items` / `npcs` / `quests` / `rng_state` | 事件投影结果（唯一写入者） |
| v2 上下文 | `definition.flags[].when` 与 `goal.objectives[].when` 查表 | 定义本身，与状态无关 |

求值只读：不写状态、不产生事件、不联网、不读时钟、**不抽随机**（`rng_state` 不暴露给条件，
避免条件绑定事件序号/随机实现细节）。

### 2.2 用户场景里既有状态的全部可变来源（决定了可表达边界）

用户场景复用既有工具链（`move_to`/`take_item`/`use_item`/`talk_to` + 进入环境伤害），因此状态变化只有：

| 事件（既有，不新增） | 状态变化 | 可被 v2 条件读到的语义 |
| --- | --- | --- |
| `CHARACTER_MOVED` | `characters[player].location` | 当前房间（`in_room`） |
| `ITEM_TAKEN` | 房间 `item_ids` 移除 + 玩家 `inventory` 追加 | 持有（`has_item`/`has_item_tag`/`has_item_tag_count`）、物品仍在房间（`item_in_room`） |
| `ITEM_CONSUMED` | 玩家 `inventory` 移除 + HP 按 `stats` 变化 | 消耗（`has_item` 变假）、HP（`hp_lte`/`hp_gte`） |
| `DIALOGUE_EXCHANGED` | `npcs[id].dialogue_state` | NPC 对话态（`npc_state`）→ 可被 `flag` 引用为剧情里程碑 |
| `ENVIRONMENT_DAMAGE` | 玩家 HP（clamp `0..max_hp`） | HP 阈值 |
| 公共规则 | 每回合 `turn = max(turn, event.turn_id)`、`world_time += 1`（每已提交回合 +1） | 时间条件 |

`turn` 初始 1、每个已提交回合 +1（即 `turn = 已提交回合数 + 1`）；`world_time` 初始 0、每个已提交回合 +1
（用户场景中 1 世界分钟/回合）。二者都由 reducer 公共规则确定性推进，**不需要新增字段**。

### 2.3 求值接在哪（零引擎改动）

- `UserScenarioHooks.goal/ending`（`domain/scenario_runtime.py` 分发）内部持有定义与 `_condition_env`，
  调用 `_evaluate_condition`；v1 定义的环境为空表，行为逐字不变；
- `QueryService._ending_of` → `/view.status/ending_text`；`TurnOrchestrator._ending_of` → 结局后拒绝新回合；
- 不新增钩子接口、不改 `ScenarioHooks` 协议、不改 `scenario_service`/`api` 契约（见 §4.3）。

### 2.4 确定性 / 静态可校验论证

- **确定性**：条件求值 = `f(条件树, 定义, 派生 ID, 状态)`，无随机/时钟/IO；同状态同定义必同结果
  （030 单测 `test_v2_evaluation_deterministic_and_state_immutable` 双重求值 + 状态不可变断言）；
  结局是读时视图、不进 `GameState`，故 `state_hash`/事件流/回放不受 v2 影响；
- **静态可校验**：条件在导入时被完整校验（§5）——白名单键、引用存在、类型/范围、版本门禁、
  flag/目标进度依赖无环；运行期不存在「未知谓词」分支；
- **有界**：每节点 `all/any` ≤16、flags ≤16、目标 ≤12、包 ≤256KB、错误 ≤50；求值对每个 flag/目标进度
  **memo 一次**，环上 fail-closed 返回 `False`（导入校验已拒绝环，运行期只是防御）。

---

## 3. 条件 DSL v2（白名单）

### 3.1 节点形状与版本规则

- 一个条件 = **恰好一个键**的 JSON 对象（`Condition._exactly_one`）；两个键/零键都是导入错误；
- 所有条件节点 `extra="forbid"`：未知键 = **结构化错误**（`path` 精确到 `<槽位>.when.<键>`，
  消息含「未知谓词或组合子 … 白名单」）；
- **版本声明式**：`schema_version` 声明特性级别，不静默升级——
  - `1`：只允许 v1 十个键；出现 v2 键/`flags` → 结构化错误（`需要 schema_version=2`）；
  - `2`：v1 全部键（语义冻结）+ 下列 v2 键 + 顶层 `flags`；
  - 其他值（3、`"2"`、`true`、`null`）→ `path=schema_version` 的结构化错误。
- 组合子 `all`/`any`/`not`/`always` 为 v1/v2 共用，语义不变（`not` 的 JSON 键仍是 `"not"`）。

### 3.2 v1 原子（语义冻结，v2 包内同样可用）

| 谓词 | JSON | 语义 | 数据来源 |
| --- | --- | --- | --- |
| `in_room` | `{"in_room":"hall"}` | 玩家当前房间是该 key | `characters[PLAYER].location` |
| `has_item` | `{"has_item":"flint"}` | 玩家行囊持有该实例 | `inventory` 含派生 item ID |
| `has_item_tag` | `{"has_item_tag":"quest"}` | 行囊任一实例 `tags` 含该标签 | `items[].tags` |
| `npc_state` | `{"npc_state":{"npc":"keeper","state":"briefed"}}` | 该 NPC `dialogue_state` 等于该值 | `npcs[].dialogue_state` |
| `hp_lte` / `hp_gte` | `{"hp_lte":6}` | 玩家 HP ≤ / ≥ 整数 | `characters[PLAYER].hp` |

### 3.3 v2 新增原子（9 个，全部只读既有派生状态）

| 谓词（类别） | JSON | 语义 | 数据来源 | 静态校验 |
| --- | --- | --- | --- | --- |
| `flag`（flag） | `{"flag":"keeper_briefed"}` | 该 flag 的 `when` 求值为真 | 顶层 `flags[]` 派生命题 | flag 必须已声明；依赖无环 |
| `objective_done`（quest/目标进度） | `{"objective_done":"gather"}` | 该目标 `when` 求值为真（缺失 `when` = false） | `goal.objectives[]` 读时求值 | 目标 key 必须存在；依赖无环 |
| `has_item_tag_count`（物品） | `{"has_item_tag_count":{"tag":"quest","at_least":2}}` | 行囊中 `tags` 含该标签的**实例数** ≥ `at_least`（1..64） | `inventory` + `items[].tags` | `tag` 非空 ≤32；`at_least` 1..64 |
| `item_in_room`（物品） | `{"item_in_room":{"item":"oil","room":"shrine"}}` | 该物品**当前仍在**该房间 | `rooms[room].item_ids` | item/room key 必须存在 |
| `npc_in_room`（NPC 状态） | `{"npc_in_room":{"npc":"keeper","room":"hall"}}` | 该 NPC 当前所在房间是该 key | `npcs[].room_id` | npc/room key 必须存在 |
| `turn_gte` / `turn_lte`（时间） | `{"turn_gte":4}` | 当前 `GameState.turn` ≥ / ≤ N（1..1,000,000，含边界） | `GameState.turn` | 整数、≥1 |
| `world_time_gte` / `world_time_lte`（时间） | `{"world_time_gte":6}` | 当前 `GameState.world_time` ≥ / ≤ N（0..1,000,000，含边界） | `GameState.world_time` | 整数、≥0 |

说明：

- `has_item_tag_count` 计的是**不同实例数**（每个 `items[].key` 至多一个实例，v1 导入校验保证唯一放置）。
  用户场景**没有堆叠数量**（见 §7），「收集 N 件」= N 个不同物品；
- `item_in_room` 的实际语义是「还没被拿走」：用户场景没有「放下/丢弃」动作，物品只会从房间单向进入行囊；
- `npc_in_room` 当前玩法下没有 NPC 移动事件，故等价于定义期常量；保留它是因为 `room_id` 是既有状态、
  静态可校验，且作者可用 `not npc_in_room` 表达「NPC 已不在」的可读条件（未来若引入 NPC 移动需另立 ADR）；
- 时间条件**不改**每回合推进规则：用户场景 `world_time` 与 `turn` 由 reducer 公共规则推进（§2.2）。

### 3.4 `flags`：派生命题（不是持久化变量）

```jsonc
"flags": [
  {"key": "keeper_briefed", "when": {"npc_state": {"npc": "keeper", "state": "briefed"}}},
  {"key": "supplies_ready", "when": {"has_item_tag_count": {"tag": "quest", "at_least": 2}}},
  {"key": "ready_to_light", "when": {"all": [{"flag": "keeper_briefed"}, {"flag": "supplies_ready"}]}}
]
```

- **语义**：`{"flag": k}` 的值 = `flags[k].when` 在当前状态下的求值结果；flag 是**命名别名/复用**，
  读时计算、不写 `GameState`、不是事件。改变状态的仍是既有动作（对话/拿取/移动/使用/伤害）；
- **声明约束**：`key` 唯一、≤16 个、`when` 必填；可引用其他 flag 与 `objective_done`；
- **无环**：flag 与目标进度建**统一依赖图**，导入期 DFS 检出环 → 结构化错误（消息含 `flag:a → flag:b → flag:a`）；
- **求值**：一次顶层求值内每个 flag/目标最多算一次（memo）；运行期遇环 fail-closed 返回 false（防御性）；
- **静态价值**：作者可把重复条件收敛成 flag；编辑器（031）可把 flag 作为「剧情开关」表单呈现，
  但底层仍是派生条件，不会绕过服务端校验（031 只产出 JSON）。

### 3.5 组合与上限

`all`/`any`（1..16 项）可任意嵌套上述原子；`not` 取反；`always` 恒真/恒假。求值短路（`all` 遇假即假、
`any` 遇真即真），整体有界（§2.4）。条件树**没有**额外深度限制（由 256KB 包体与节点数量上限自然约束）。

---

## 4. 场景包 schema v2

### 4.1 顶层字段 delta（相对 v1）

| 字段 | v2 状态 | 约束 |
| --- | --- | --- |
| `schema_version` | **必填 1 或 2** | v1 逐字兼容；v2 才允许下列 delta |
| `flags` | **v2 新增可选** | 0..16；每项 `{key, when}`；key 文件内唯一 |
| 其余字段（`slug`/`meta`/`player`/`rooms`/`items`/`npcs`/`goal`/`endings`/`suggestions`） | **不变** | 字段/类型/上限/校验规则与 v1 完全一致 |

所有 `when` 槽位（`goal.objectives[].when`、`endings[].when`、`suggestions[].when`、`flags[].when`）都接受
同一套 v2 条件（v1 包仍只接受 v1 键）。除条件语义外，v2 **不新增**任何字段（避免 031 编辑器与 032 分享
的字段面膨胀；如后续需要 `choice`/`once` 等字段，须新 ADR）。

### 4.2 不变量（v1 已有，v2 保持）

- 文件 ≤256KB、UTF-8、顶层 JSON 对象、`extra="forbid"`、非有限数值拒绝；
- `rooms/items/npcs/flags` 各自 key 唯一；跨类型可同名；引用必须存在（房间/物品/NPC/flag/目标/方向）；
- 派生 ID 规则与建局完全不变（`sha256(scenario_id|kind|key)` + Crockford base32；玩家固定 `PLAYER_ID`；
  同 JSON + 同 `scenario_id` = 同初始状态 = 同 `state_hash`）；
- 内置 4 场景不经过 `user_scenario`，golden/demo/console 逐字不变。

### 4.3 对外契约不变（030 不触碰 API 层）

- `/scenarios/validate`、`/scenarios` 导入/列表/详情/删除、`POST /games {scenario_id}` 的**请求/响应形状全部不变**；
  v2 定义照常入库（`schema_version=2` 原样存/原样回），**summary 仍是冻结的 6 键**
  （`rooms/items/npcs/objectives/endings/suggestions`，不含 flags 数量 → 前端契约测试无需改动）；
- 编辑器若展示 flags，读详情里的原始 `definition` 即可（031 实现时不得新增响应字段，除非同步四处契约）。

### 4.4 完整示例

`examples/scenarios/lantern-vigil.json`（v2，可导入试玩）：3 房间 / 3 物品 / 1 NPC / 5 flags / 3 目标 /
3 结局 / 2 建议。覆盖 `flag`、`objective_done`、`has_item_tag_count`、`item_in_room`、`npc_in_room`、
`turn_gte`、`hp_lte`、`not`、`all`；集成测试真实跑「导入→开局→对话→移动→拿取→目标链→胜利结局→
结局后拒绝新回合」。

---

## 5. 静态校验与错误模型（导入即失败，错误可定位）

| 校验 | 错误 `path` 示例 | 阻断 |
| --- | --- | --- |
| JSON 语法/编码/超限/非有限数值 | `$` | 是 |
| `schema_version` 非法（3/字符串/bool/null） | `schema_version` | 是 |
| 未知谓词/组合子（含拼写错误） | `goal.objectives[0].when.teleport` | 是 |
| 条件节点两个键/零键 | 该 `when` 槽位 | 是 |
| 未知房间/物品/NPC 引用（含 v2 子字段） | `endings[0].when.item_in_room.room` | 是 |
| 未知 flag 引用 | `flags[2].when.flag` | 是 |
| 未知目标 key（`objective_done`） | `goal.objectives[1].when.objective_done` | 是 |
| flag/目标进度依赖环 | 首次出现环的槽位（消息列出环） | 是 |
| v1 声明使用 v2 字段/谓词 | `flags` / `<槽位>.when.flag` | 是 |
| 字段类型/长度/数量/范围（Pydantic） | 精确到字段（如 `flags[16]`） | 是 |
| 声明但未被引用的 flag | `flags[i]` | 否（warning） |

- 错误/警告形状固定 `{path, message}`（中文消息，路径为 JSON 风格：`rooms[2].exits.east`），errors ≤50；
- `parse_definition` **不抛异常**，返回 `ParsedDefinition(errors, warnings, summary)`；导入接口只需判 `ok`；
- 同一错误只报一次口径：版本门禁先于引用校验（v1+v2 特性混用时只给版本错误，不淹没玩家）。

---

## 6. 多结局选择语义（v2 冻结）

### 6.1 选择规则（与 v1 完全一致，v2 只是让 `when` 更能写）

1. 候选集 = 顶层 `endings[]`（0..6）；
2. 排序 = **`priority` 降序 → 声明顺序升序**（稳定排序，完全确定）；
3. 依序求值 `when`，**第一个满足者**即结局；`when` 缺失的结局**永不触发**（v1 口径）；
4. 显式结局都不满足时走兜底：① `goal` 存在且**全部** objective 的 `when` 均满足 → 「目标完成」胜利；
   ② 玩家 `hp <= 0` → 「倒下」失败；顺序 ①→② 与 v1 相同（00 单测锁定）；
5. 结局视图结构 `{ending_id, title, text, kind}` 不变（`ending_id = ending_<slug>_<key>` / `_complete` / `_fallen`）；
6. `kind` 仍只有 `win`/`lose`（改 `kind` 集合 = 前端/DTO 契约变更，不做）。

### 6.2 结局是「读时视图」，不是事件

- 每次 `/view` 与每回合前置检查都重新求值；**不持久化**「已选结局」，不写 `GameState`、不写事件 →
  回放/`state_hash` 零影响（旧 golden 零改动）；
- 一旦求值非空，`/view.status="ended"`；`TurnOrchestrator` 拒绝新回合（`INVALID_INPUT`，零状态写入）；
- 同一状态重复求值必得同一结局（纯函数）；无 RNG、无时钟。

### 6.3 「玩家选择」怎么表达

v2 **不新增「选择结局」动作/菜单**（那需要新工具 + 新事件 + DTO/UI 契约，超出 D-N5/D-N7 边界）。
选择语义 = **状态驱动**：玩家通过既有动作（移动/拿取/使用/对话）造成状态差异，不同状态命中不同结局。
可落地的三种作者模式：

| 模式 | 写法 | 例子 |
| --- | --- | --- |
| 互斥条件 | 两个结局 `when` 用不同 flag/物品/房间 | 拿蜡烛走「烛」结局 / 拿灯油走「油」结局（030 单测 `test_v2_ending_choice_driven_by_player_state`） |
| 优先级覆盖 | 高优先级特殊结局 + 低优先级通用结局 | 特殊胜利（priority 10）优先于普通完成（兜底） |
| 超时/软失败 | `turn_gte`/`world_time_gte` + `not` 进度 flag | 8 回合还没集齐材料 → 「空手」失败（示例 `empty_handed`） |

---

## 7. v2 可表达 vs 不可表达（原因 + 归属）

### 7.1 可表达（全部来自既有派生状态，零引擎改动）

| 作者想表达 | v2 写法 |
| --- | --- |
| 到过/在某个房间 | `in_room` |
| 拿到某物/某类物/收集 N 件 | `has_item` / `has_item_tag` / `has_item_tag_count` |
| 某物还没被拿走 | `item_in_room` |
| 和 NPC 聊过（对话态推进） | `npc_state`（可包成 flag） |
| NPC 在哪个房间 | `npc_in_room` |
| 残血/满血阈值 | `hp_lte`/`hp_gte` |
| 第 N 回合后、世界时间阈值 | `turn_gte`/`turn_lte`/`world_time_gte`/`world_time_lte` |
| 剧情开关/里程碑（当前可判定的） | `flags[].when` + `{"flag": key}`（派生命题） |
| 目标链/前置完成 | `objective_done` + `all` |
| 多个结局分支 | `endings[].when` + priority/声明序（§6） |

### 7.2 不可表达（原因 + 归属）

| 不可表达 | 原因 | 归属 |
| --- | --- | --- |
| **历史条件**：曾进入过某房间、曾与某 NPC 说过话、物品曾被使用/曾丢弃、首次进入触发、一次性对话、限时窗口内触发一次 | `GameState` 是**当下快照**，没有历史字段；`UserScenarioHooks` 只拿得到 `state`。要表达必须新增事件类型 + reducer 派生历史，属于「揭示/线索持久化」问题 | **N1 033–034 跑团世界模组 P0**（D-N6：唯一允许新增 `EventType` 的切片，需用户拍板 + 独立 ADR + 受控 golden 重基线）；v2 不硬改引擎 |
| 物品堆叠数量（「3 瓶药水」） | `Item` 无数量字段，用户场景一 key 一实例、无丢弃/堆叠动作；事件也只增减实例 | 不做（无动作路径；若做需改领域模型/事件，属跨里程碑决策） |
| 玩家主动选择结局（选项菜单） | 需要新动作/工具 + 新事件 + `/view`/前端 DTO 变更 + UI；`endings` 设计为读时视图 | 不做（D-N5/D-N7）；用户确有需求时另立 ADR + 031 编辑器同批 |
| `GameState.quests` 任务状态机（`quest_state`） | 用户场景没有创建/推进 quest 的路径（无事件）；暴露 `quests` 谓词会成为恒假的死谓词 | 不做；若 033–034 引入任务系统则随动（新 ADR） |
| NPC 移动/日程/阵营/状态列表 | 用户场景无 NPC 移动事件、`faction`/`schedule` 不可由 JSON 定义 | 不做（033–034 模组若引入日程需新 ADR） |
| 玩家属性比较（`str>=12`） | 用户场景没有属性变更事件 → 条件恒真/恒假，无玩法意义 | 不做 |
| 随机条件（概率分支） | 条件求值必须确定性、可回放；不应在求值里抽随机 | 不做（D-N7 确定性口径） |
| 跨存档/全局变量 | 无全局状态，单机单存档 | 不做 |
| 跨场景/世界书条件 | 场景定义不引用其他场景/世界书（032 分享 = 文件交换） | 不做（032/后续另议） |

---

## 8. v1→v2 兼容矩阵

| 组合 | 结果 | 说明 |
| --- | --- | --- |
| **v1 文件 + v1 读取器（基线）** | 逐字不变 | 旧测试/旧 golden 全绿；本次 030 不改任何 v1 断言 |
| **v1 文件 + v2 读取器** | **逐字兼容** | 字段/类型/上限/引用校验/求值语义一致；`schema_version` 保留 1、`flags=[]`；`evaluate_condition` 旧调用签名可用（`definition` 为可选关键字参数） |
| **v2 文件 + v2 读取器** | 正常解析/建局 | v2 谓词与 `flags` 生效；summary 6 键不变；派生 ID/初始状态规则不变 |
| **v2 文件 + v1 读取器（旧版本程序）** | 明确拒绝 | `schema_version` 上限 1 → 校验错误（不静默降级、不半渲染） |
| **v1 声明 + v2 字段/谓词** | **结构化错误** | 版本门禁：`flags` 槽位 + 每个 v2 谓词的精确路径，消息含「需要 schema_version=2」 |
| **非法 schema_version（3/字符串/bool/null）** | 结构化错误 | `path=schema_version`；`true` 不被当成 1 |
| 未知谓词/未知字段 | 结构化错误 | `extra="forbid"`；路径含未知键名 |
| 未知 flag/目标/物品/房间/NPC 引用 | 结构化错误 | v2 子路径精确（如 `item_in_room.room`） |
| flag/目标依赖环 | 结构化错误 | 统一依赖图 DFS；消息列出环 |
| 事件流 / `GameState` / reducer / golden | **零改动** | 无新 `EventType`、无新状态字段、无 golden 迁移 |
| API DTO / summary / 前端契约 | **零改动** | v2 只改 `definition_json` 内容与 `schema_version` 值 |
| `/scenarios/validate`、导入、列表、详情、删除、`POST /games` | 形状不变 | 行为对 v2 定义同样成立（沿用 v1 服务层） |
| 编辑器（031）/ 分享（032） | 需按本文实现 | 编辑器只产出 JSON、走同一 validate；分享导入仍走 256KB/白名单/幂等 |

---

## 9. 验收、测试与护栏

- **030 实现验收（本地实测数字见 `docs/progress/030-2026-11-12-scenario-dsl-v2-impl.md`）**：
  目标测试集 5 文件全绿；`tests/unit/test_user_scenario.py` 由 29 个用例扩到 74 个 collected（v2 新增
  37 个测试函数 / 45 个 collected case，≥25 要求）；示例 `lantern-vigil.json` 通过校验并有端到端集成测试；
- 单测覆盖矩阵：v2 每个原子（flag/objective_done/has_item_tag_count/item_in_room/npc_in_room/turn_*/
  world_time_*）+ 组合子混用 + v1→v2 兼容 + 版本门禁（9 谓词参数化）+ 未知谓词/字段 + 非法
  schema_version + 引用校验 + 环检测 + 多结局优先级/同权声明序/状态驱动选择/兜底顺序 + 警告 + 确定性/不可变；
- **护栏（不放宽）**：不新增运行时依赖；不新增 `EventType`；不改 `GameState`/reducer/golden；
  用户 JSON 永不执行（白名单 + `extra="forbid"` + 无 eval）；v1 旧测试断言零改动；
  `ruff format --check`/`ruff check`/`mypy` 对变更文件通过。

## 10. 后续回写落点与待办（不属本切片代码）

| 落点 | 内容 | 责任 |
| --- | --- | --- |
| `docs/design/user-scenario-modules.md` | 追加「§6 v2 回填」指向本文件（v1 文档不改正文） | Lead（或 032 同批） |
| `docs/decisions/README.md` | ADR 索引行（本目录索引，030 未改） | Lead（禁止 030 改） |
| `docs/PROGRESS.md` §3/§4 | 029/030 索引行 + N1 已用编号/下一编号 | Lead（禁止 030 改） |
| 第 17 章 §17.9.2 N1 行 / 第 6 章场景契约 | 标注 029/030 已交付、v2 条件清单 | Lead |
| 031 编辑器 | schema_version 选择器 + flags 表单 + 调 `/scenarios/validate`；不新增响应字段 | N1 031 |
| 032 分享 | 导出保持 `schema_version`；导入走同一校验 | N1 032 |
| 033–034 | 历史/揭示类条件：新增事件 + 派生 + 受控 golden 重基线 + D-N1-2 用户拍板 | N1 033–034 |
