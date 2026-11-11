# 用户自上传场景 JSON（Scenario Module v1）——格式、设计与分期

> 状态：设计定稿（2026-11-11，Lead）｜ 目标：**玩家自己上传一份场景/剧情 JSON 就能开新局**，不需要改代码、不需要我们硬编码新场景。
> 关联：本文件是「用户内容平台」v1 设计；实施进度回写 `docs/PROGRESS.md`，决策落 `docs/decisions/`。
> 参考已有同类能力：`docs/design/trpg-world-module.md`（跑团模组评估，本文的 P0 是其「用户可导入」变体）、
> `docs/design/world-book-format-compat.md`（世界书导入格式）。

---

## 1. 为什么是「上传 JSON」而不是「我们写新场景」

现状：`m2`/`m3`/`m4`/`story` 四个场景都是 `domain/scenario*.py` 里的**代码**（固定 ULID + 场景专属钩子函数），
新增场景 = 改代码 + 加测试 + 可能破 golden。玩家侧无法扩展内容，所以「剧情固定」。

v1 选择**数据驱动 + 用户导入**（与已有的人物卡、世界书导入同构）：

- 场景 = 一份 JSON（房间/物品/NPC/目标/结局/触发/对话），**导入即玩**；
- 内置 4 个场景完全不变（旧 golden、demo/console 逐字不变）；
- 导入的场景走同一套事件溯源/回放/幂等/审计链路，不新增 EventType（复用 `ENVIRONMENT_DAMAGE`、
  `DIALOGUE_EXCHANGED`、`CHARACTER_MOVED`、`ITEM_TAKEN`、`ITEM_USED` 等既有事件）；
- 文本全离线可玩（模板叙事）；配置了 DeepSeek API Key 后由 LLM 叙事（见 §9）。

不做（v1 明确排除，避免范围膨胀）：自定义配方/制造链、战斗系统、脚本/表达式执行（禁止 eval）、
自定义事件类型、多人 GM 层、模组打包分享（P2）。

---

## 2. 场景 JSON 格式 v1（玩家可读格式）

一份场景 JSON 顶层字段：

```jsonc
{
  "schema_version": 1,                    // 必填，本期只接受 1
  "slug": "ember-watch",                  // 必填，[a-z0-9-]{2,32}，作者可读标识（不作主键）
  "meta": {
    "title": "余烬哨站（示例）",           // 必填 1..48 字
    "author": "玩家名",                    // 可选 0..32
    "description": "一句话介绍",           // 可选 0..200
    "tags": ["末日", "解谜"]               // 可选 ≤8 个，每个 ≤16 字
  },
  "player": {
    "name": "旅人",                        // 可选，默认「旅人」，1..24 字
    "hp": 12, "max_hp": 20,                // 可选，默认 20/20，0 ≤ hp ≤ max_hp ≤ 999
    "attributes": {"str": 12, "dex": 14, "con": 12, "int": 10, "wis": 11, "cha": 9},  // 可选
    "location": "bridge",                  // 必填，rooms 里的 key
    "inventory": ["potion"]                // 可选，items 里的 key（重复写=多件）
  },
  "rooms": [                               // 必填 1..40
    {
      "key": "bridge",                     // 必填，[a-z0-9_]{1,32}，文件内唯一
      "name": "断桥",                       // 必填 1..32 字
      "description": "风雪把桥面埋了一半。",  // 必填 1..500 字
      "exits": {"east": "hub", "down": "well"},   // 可选，方向 → rooms.key；方向仅 north/south/east/west/up/down/in/out
      "items": ["flint"],                  // 可选，初始放在本房间的 items.key
      "npcs": ["watchman"],                // 可选，初始在本房间的 npcs.key
      "ambient_tags": ["snow", "ruined"],  // 可选 ≤8 个，模板叙事/制造工位用（station.forge 等）
      "on_enter": {                        // 可选：首次或每次进入的触发（v1 只做环境伤害）
        "hp_delta": -6,                    // 可选，-50..0；负=扣血（0 无效果）
        "hint": "寒气顺着领口灌进来。"       // 可选，≤120 字；出现在失败提示/事件文案
      }
    }
  ],
  "items": [                               // 可选 0..80
    {
      "key": "flint",                      // 必填，文件内唯一
      "name": "火石",                       // 必填 1..32
      "description": "一块打火石。",         // 可选 0..500（默认空串）
      "tags": ["quest"],                   // 可选 ≤8；约定：quest=主线材料（不可被 use 销毁）、
                                           //   weapon=武器、material=材料、tool=工具、consumable=消耗品
      "stats": {"heal": 8, "damage": 3},   // 可选；heal/hp_delta 由既有 use_item reducer 生效
      "stackable": false,                  // 可选
      "rarity": "common"                   // 可选 common/uncommon/rare/epic/legendary
    }
  ],
  "npcs": [                                // 可选 0..20
    {
      "key": "watchman",                   // 必填，文件内唯一
      "name": "守夜人",                     // 必填 1..24
      "room": "hub",                       // 必填，rooms.key
      "description": "他靠着断墙，眼睛没离开过楼梯口。",  // 可选，细看（inspect）文案
      "dialogue": {                        // 可选；不给则对话走通用文案
        "default": "他抬了抬下巴，示意你继续往里走。",   // 可选，默认回话 ≤500
        "topics": [                        // 可选 ≤8；按顺序匹配关键词
          {
            "keywords": ["任务", "怎么", "灯"],          // 必填 1..8 个，每个 1..8 字
            "reply": "去地窖找火石，再上塔把灯点起来。",   // 必填 1..500
            "dialogue_state": "briefed"      // 可选，≤32；写入该 NPC 的 dialogue_state，可被目标条件读取
          }
        ],
        "briefed_reply": "灯还没亮，快去。"   // 可选：dialogue_state 已是 briefed 后再说任务时的回话
      }
    }
  ],
  "goal": {                                // 可选；不给则 /view 无目标面板
    "title": "点亮信号灯",                  // 必填 1..48
    "description": "让塔顶的火重新烧起来。", // 可选 ≤300
    "objectives": [                        // 必填 1..12；顺序即 UI 顺序
      {"key": "listen", "text": "向守夜人打听情况", "when": {"npc_state": {"npc": "watchman", "state": "briefed"}}},
      {"key": "light",  "text": "带着火石和火把登上塔顶",
       "when": {"all": [{"in_room": "tower"}, {"has_item": "flint"}, {"has_item_tag": "item.signal_torch"}]}}
    ]
  },
  "endings": [                             // 可选 0..6；按 priority 降序取第一个满足项
    {"key": "victory", "kind": "win",  "title": "灯亮了", "text": "……", "priority": 10, "when": {"all": [{"in_room": "tower"}, {"has_item": "flint"}]}},
    {"key": "fallen",  "kind": "lose", "title": "倒在雪里", "text": "……", "priority": 1,  "when": {"hp_lte": 0}}
  ],
  "suggestions": [                         // 可选 ≤16；只影响建议动作文案排序
    {"when": {"in_room": "hub"}, "text": "去找守夜人聊聊"}
  ]
}
```

### 2.1 条件 DSL（v1）

条件 = 一个 JSON 对象，**唯一键**为原子谓词或组合子；不允许脚本、不允许 eval、未知道具谓词=校验失败：

| 谓词 | 含义 |
| --- | --- |
| `{"in_room": "bridge"}` | 玩家当前房间是该 key |
| `{"has_item": "flint"}` | 玩家行囊有该 key 的任意实例 |
| `{"has_item_tag": "quest"}` | 玩家行囊有实例的 `tags` 含该标签（也支持模板键如 `item.signal_torch`） |
| `{"npc_state": {"npc": "watchman", "state": "briefed"}}` | 指定 NPC 的 `dialogue_state` 等于该值 |
| `{"hp_lte": 6}` / `{"hp_gte": 12}` | 玩家 HP 比较（整数） |
| `{"all": [cond, ...]}` | 全部满足 |
| `{"any": [cond, ...]}` | 任一满足 |
| `{"not": cond}` | 取反 |
| `{"always": true}` | 恒真（自定义结局缺省胜利条件时可用） |

条件缺失（未提供 `when`）视为**不满足**（避免空条件误判胜利）；`suggestions[].when` 缺失视为恒真。

### 2.2 内置结局兜底（作者不写 endings 也有结局）

- **失败兜底**：玩家 `hp <= 0` → 通用结局「倒下」（文案固定，不写进定义）。
- **胜利兜底**：`goal` 所有 objective 的 `when` 都满足 → 通用结局「完成目标」。
- 作者显式 `endings` 优先于兜底；`endings` 内部按 `priority` 降序、同权按声明顺序取第一个满足项；
  `kind` 只影响 UI 配色/文案前缀，不改变流程。

### 2.3 校验与限额（导入即校验，错误可定位）

- 文件 ≤ **256KB**、UTF-8；顶层 `schema_version` 必须为 1；未知字段**直接报错**（`extra=forbid`，
  错误信息带 JSON 路径，如 `rooms[2].exits.east: 未知房间 key "hub2"`）。
- `rooms/items/npcs` 的 `key` 在各自数组内唯一；跨类型可同名（会映射到不同 ID 前缀）。
- 引用必须存在：`player.location`/`exits` 目标/`player.inventory`/`items[].key`/`npcs[].room`。
- 方向只允许 8 个英文方向（与既有 `move_to` 一致）；`exits` 双向不强制（单向门允许）。
- 文本长度、数量上限见上；所有文本按纯文本处理（前端只用文本插值，禁 `v-html`，与 R-L3 同口径）。
- `warnings`（不阻断）：`schema_version` 高于 1 的未知字段会被拒绝而不是 warning；
  仅「未使用的 key/空 NPC 对话」等给出 warning。
- 同一 `slug` 可重复导入：**每次导入生成新的内部场景 id**（见 §3.1），不覆盖已开局存档。

### 2.4 完整最小示例（可直接导入试玩）

```json
{
  "schema_version": 1,
  "slug": "tiny-tower",
  "meta": {"title": "小塔", "author": "示例", "description": "两房间最小可玩场景"},
  "player": {"name": "旅人", "hp": 10, "max_hp": 10, "location": "gate", "inventory": []},
  "rooms": [
    {"key": "gate", "name": "塔门", "description": "一扇虚掩的木门，门缝里透出风。", "exits": {"north": "hall"}},
    {"key": "hall", "name": "塔厅", "description": "螺旋楼梯向上，墙上挂着一盏没点亮的灯。", "exits": {"south": "gate"}, "items": ["flint"], "on_enter": {"hp_delta": -2, "hint": "冷风从楼梯间灌下来。"}}
  ],
  "items": [{"key": "flint", "name": "火石", "description": "一块干燥的火石。", "tags": ["quest"]}],
  "npcs": [],
  "goal": {"title": "爬上塔顶", "objectives": [{"key": "climb", "text": "带着火石进入塔厅", "when": {"all": [{"in_room": "hall"}, {"has_item": "flint"}]}}]},
  "endings": [{"key": "win", "kind": "win", "title": "你登了上去", "text": "塔顶的风很大，但灯终于亮了。", "priority": 10, "when": {"all": [{"in_room": "hall"}, {"has_item": "flint"}]}}]
}
```

---

## 3. 技术设计（实现契约）

### 3.1 确定性 ID 派生（回放/state_hash 可复算）

领域 ID 必须匹配 `^(char|room|item|npc)_[0-9A-HJKMNP-TV-Z]{26}$`。用户 JSON 的 key 不能直接当 ID：

```
scenario_id = "scn_" + ULID         # 导入时生成，一导入一 ID（不可变）
entity_id(kind, key) = f"{kind}_{base32(sha256(scenario_id + "|" + kind + "|" + key))[:26]}"
```

- `base32` = Crockford 字符集（与 ULID 一致），纯函数、跨平台稳定；
- 玩家槽位**固定用全局 `PLAYER_ID`**（查询/叙事/建议/编排全链路按该常量取玩家，不能另起）；
- 同一 `scenario_id` + 同一 JSON = 同一初始状态 = 同一 `state_hash`（回放可复算）；
- 不同场景之间 ID 不冲突（sha256 前缀含 scenario_id）。

### 3.2 场景识别与钩子（不改 `GameState` 字段）

`GameState` 不新增字段（否则所有旧 `state_hash` 漂移）。给 `GameState` 加字段被明确否决。

场景识别：新增 `ScenarioRuntime`（进程内索引，组合根构造、导入后刷新）：

- `room_id → 场景定义`、`npc_id → 场景定义`（由定义派生 ID 建立索引）；
- `resolve(state)`：state 的任一 room 命中自定义索引 → 该自定义场景；否则回退内置判定
  （`is_story_state` / m4 铁匠铺 / m3 拾荒者 / 默认 m2，保持 `scenario_name_of_state` 现有顺序）；
- 软删场景**保留索引**（已开局存档仍可读/回放），仅列表与「新建局」不可见。

钩子（`ScenarioHooks`，内置与用户场景同接口）：

| 钩子 | 内置实现（现状） | 用户场景实现 |
| --- | --- | --- |
| `goal(state)` | `story_goal`（其余内置 None） | 按 `goal.objectives[].when` 求值 |
| `ending(state)` | `story_ending` | 按 `endings[]` 与兜底规则 |
| `room_damage(room_id)` | `story_room_damage` | `rooms[].on_enter.hp_delta` |
| `npc_reply(npc_id, topic)` | `story_reply` | `npcs[].dialogue` 关键词匹配 |

注入点（均**默认 None = 现状行为逐字不变**，只对自定义存档生效）：

- `QueryService(scenario_runtime=...)`：`scenario_name_of_state` / `goal` / `ending` 视图；
- `TurnOrchestrator(scenario_runtime=...)`：进入伤害事件、结局后拒绝新回合判定；
- `ActionService(scenario_runtime=...)`：`talk_to` 的 NPC 回复；
- `LLMNarratorAgent(goal_provider=scenario_runtime)`（可选）：prompt 里带目标（无则不带）。

不改 `domain/suggestions.py` 的场景专属分支：自定义场景走通用建议（look/inventory/拿取/移动），
v1 不承诺「缺材料聚焦建议」的自适应（列入 P1）。

### 3.3 持久化与迁移

新表 `scenarios`（Alembic **0005**，可逆；不进事件溯源、不写 `GameState`）：

| 列 | 说明 |
| --- | --- |
| `scenario_id` TEXT PK | `scn_` + ULID |
| `slug` / `title` / `author` TEXT | 列表展示（slug 不唯一） |
| `schema_version` INTEGER | 定义版本 |
| `definition_json` TEXT | 原样定义（导入时即校验；存原文便于回放/导出） |
| `summary_json` TEXT | 统计（rooms/items/npcs/objectives/endings）与 warnings |
| `created_at` / `deleted_at` | 软删；`deleted_at` 非空=列表不可见、不可建新局 |

`game_created.params` 加法（仅自定义场景）：`"scenario": "custom"`、`"scenario_id": "scn_..."`；
`starting_room_id`/`initial_character_id` 由初始状态派生（旧场景保持现常量，事件逐字不变）。

### 3.4 API 契约（沿用既有信封/幂等/错误码）

| 方法+路径 | 说明 |
| --- | --- |
| `POST /api/v1/scenarios/validate` | 只读预检：body=场景 JSON 原文；200 `{ok, errors:[{path,message}], warnings:[], summary}`（不写库、无需幂等键） |
| `POST /api/v1/scenarios` | 导入（multipart `file` ≤256KB 或 `application/json` 原文）；需 `Idempotency-Key`；201 `{scenario_id, slug, title, summary, warnings}`；同键幂等、异参 409；非法 422 |
| `GET /api/v1/scenarios` | 列表（`limit`≤100/`offset`/`q` 搜索；不含已软删） |
| `GET /api/v1/scenarios/{id}` | 详情（含原始定义、summary、warnings） |
| `DELETE /api/v1/scenarios/{id}` | 软删（不影响已开局存档；重复删除 404） |
| `POST /api/v1/games` 加法 | body 可带 `scenario_id`：与 `card_id`/`scenario` 互斥（同给 422）；未知/已删 404；`seed` 可同时给 |

### 3.5 前端与终端

- 前端新增 `/scenarios`（列表：标题/摘要/目标数/结局数/「按此场景开局」/删除）、`/scenarios/import`
  （拖放/选择 JSON → 校验预览 errors/warnings → 导入）、`/scenarios/:id`（详情 + 原始 JSON 折叠 + 开局）；
  Home 新增「场景」下拉（内置 4 个 + 已导入），对局页 scenario 徽章与目标面板复用既有 `/view` 字段；
- 终端：`--list-scenarios`、`--scenario-id <id>`（与 `--scenario` 互斥），无卡库式浏览（v1 只做开局）。

### 3.6 测试与护栏

- 单元：ID 派生稳定性、条件求值矩阵、校验错误路径、结局优先级、兜底结局、对话关键词；
- 集成：导入→建局→移动/拿取/对话/触发伤害→目标推进→结局→事件/审计/回放一致；软删后旧存档仍可读；
- 契约：`test_frontend_contract.py` 加场景库 JSON 断言；`check_docs` 不受影响；
- 护栏：**旧 4 场景 golden 与 demo/console 逐字不变**（自定义路径只在 `scenario="custom"` 分支生效）；
- e2e：`scripts/e2e-web.mjs` 增加「导入示例场景→开局→走两步→出现目标面板」步骤（P0 可先手动）。

---

## 4. 分期与优先级（按用户要求：内容 → 体验 → 功能 → 技术 → 文档）

| 期 | 内容 | 验收 |
| --- | --- | --- |
| **P0（内容主干，先做）** | 格式 v1 + 校验 + 确定性 ID/建局 + 场景存储 + 钩子（目标/结局/伤害/对话）+ API（validate/import/list/get/delete + create_game）+ `examples/scenarios/*.json` + 终端 `--scenario-id` | 上传示例 JSON 能在网页/终端开局并通关；旧 4 场景 2007 测试零回归 |
| P1（体验） | 场景库/导入/详情 UI + Home 下拉 + 对局徽章；错误定位优化；LLM 叙事 prompt 带目标 | 玩家全程网页完成「上传→选场景→玩」 |
| P2（世界书联动） | 建局时可绑定已导入世界书（已支持 `world_book_id`）；世界书字段语义补强（timed effects/inclusion group/position-depth，按 compat 文档） | 上传世界书 + 场景组合可玩；酒馆用户导入无感 |
| P3（功能） | DeepSeek API Key 网页输入（本地 env 优先/干净克隆引导）、连接测试、来源状态显示 | 干净克隆首次打开能看到「未配置→去设置」并可对话 |
| P4（技术债/文档） | 条件 DSL 扩展（flags/quests/时间）、编辑器、建议自适应、文档回写（ADR/PROGRESS/CHANGELOG/README/格式文档） | 四门全绿 + 文档数字校准 |

> 本文件即 P0 的实现契约；实施中的偏离在 `docs/decisions/` 记 ADR 后回写本文件。
