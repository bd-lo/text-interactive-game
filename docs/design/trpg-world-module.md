# 跑团式世界模组设计（World Module / TRPG）——评估与分期

> 归属：H4 / 需求 B（跑团式实现）。角色：game-designer（内容结构 + 参考对标 + 分期评估）。
> 状态：**设计文档，本轮不实现**（H4-BRIEF §2B：跑团实现工作量大，先写文档，避免半成品）。
> 关联：世界书 MVP（`domain/world_book.py` + `H4-CONTRACT`）、story 场景（`domain/scenario_story.py`）、
> L1 环境伤害（[story-hazard-copy.md](story-hazard-copy.md)）。

## 0. 结论先行

1. **世界书（Lorebook）与跑团模组（World Module）不是同一层东西**：世界书是「事实池」——关键词命中就注入设定，无持久状态（已实现）；跑团模组是「结构与流程」——幕/地点/线索/手札/判定/奖励/结局，**必须有持久化的揭示状态**。两者应叠加，而不是互相替代。
2. 现有代码已具备约 **60% 的跑团骨架**：事件溯源（唯一写入者 + reducer）、`RuleEngine`（DC 5/10/15/20/25 + d20 + 派生种子）、`Resolver`（提案裁决）、`PerceptionSlice`（结构性信息隔离）、`story_goal/story_ending`（读时纯求值目标/结局）、`domain/suggestions.py`（可执行建议）、世界书触发。缺的主要是：**模组数据层、揭示状态持久化、判定结果入事件、GM 隐藏层、手札/线索的玩家可见通道**。
3. 建议分期：**P0（可试玩切片，2–3 人日）→ P1（完整跑团体验，4–6 人日）→ P2（编辑器/打包/LLM，另立里程碑）**。P0 就能让玩家感知「线索逐步揭开 + 有判定 + 有结局」，且对旧 golden 零影响。
4. 本轮（H4）**只交文档**；H5 拍板后再开实现任务，避免半成品。

## 1. 参考实现：借鉴什么、不借鉴什么

| 参考 | 关键概念 | 可借鉴 | 不借鉴/裁剪 |
| --- | --- | --- | --- |
| Foundry VTT | Journal Entries / Compendium Packs / Handouts / Actors / Scenes；GM 与玩家分层 | **信息分层**（GM 隐藏字段、玩家可见视图）、**Handout 一次性揭示**、Actors 卡（NPC/怪物）、Scenes（场景切换） | 世界模拟/网格战斗/宏系统/权限矩阵；Finder 的 UI 与插件体系 |
| Roll20 | Journal + Handout（`reveal to players`）、rollable tables | **handout 逐条揭示**的状态机、玩家可见 vs GM 备注两段式文案 | 角色卡 sheet、地图与 token |
| MapTool | Campaign properties、宏、阵营可见性 | **campaign 级属性**（全局变量/标志位）、可见性开关 | 宏语言（不可安全/不可确定性回放） |
| Owlbear Rodeo | 极简场景 + 观众视图 | 「轻量、零配置、一张图一段文」的产品感 | 地图/图片资产为主 |
| 通用战役书 | 幕/场景/地点/NPC/线索/手札/事件/判定 DC/奖励/结局 | **线性幕结构 + 三线索法则**（每条关键线索至少 3 处可得）、失败也推动剧情 | 大段设定文本直接塞 prompt |

核心借鉴原则：
1. **一切秘密先存在于「模组定义」，不进入 `GameState`**——玩家视角、LLM prompt、`/view` 都不得包含未揭示内容（沿用项目已有 `PerceptionSlice` 结构性防泄漏思路）。
2. **揭示是事件、不是渲染时巧合**——线索/手札一旦揭示必须持久（刷新/重连/回放后仍在），因此必须写事件。
3. **判定由确定性引擎算，LLM 只渲染**——沿用「智能体只提议、框架算数值」的既有不变量。
4. **线索逐步揭示 = 触发器 + 条件谓词**，谓词语言必须是白名单 YAML/JSON，**禁止 eval/脚本**（可回放、可审计）。

## 2. 信息分层模型（本项目的三层）

| 层 | 玩家可见 | 存哪 | 举例 | 查询/叙事投影 |
| --- | --- | --- | --- | --- |
| L0 公开层 `public` | 是（始终） | 模组定义 + `GameState` | 房间描述、NPC 外观、世界简介 | `/view`、房间视图、叙事 |
| L1 线索层 `clue` | **揭示后**可见 | 揭示状态（事件）；内容在模组定义 | 手札《河谷换防记录》、老兵的往事、铁匠的下落 | 揭示后进 `/view.handouts`、叙事追加、`suggested_actions` |
| L2 GM 层 `gm` | **永不**（服务端） | **只在模组定义**（不入 `GameState`/事件 payload） | 幕后真相、NPC 真实动机、结局判定细节、隐藏奖励 | 仅 GM 调试端点（默认关）/作者工具；**禁止**进 prompt |

揭示状态机：`hidden → hinted（可被玩家察觉但无内容）→ revealed（玩家可见）→ confirmed（玩家主动验证，可有额外奖励）`。
状态迁移只由**确定性触发器**在回合结算时产生事件，玩家输入不直接写状态。

## 3. 世界模组 JSON 定义（草案 v0.1）

顶层结构（文件名 `<module_id>.world.json`，≤1MB；纯数据、无脚本）：

```json
{
  "module_id": "ember-outpost",
  "name": "余烬哨站",
  "version": 1,
  "language": "zh-CN",
  "author": "game-designer",
  "scenario": "story",
  "summary_public": "风雪封路前夜，河谷最后一座哨站的信号灯灭了两夜。",
  "summary_gm": "老兵已经两夜没合眼，他在等一支不会来的商队。",
  "acts": [
    {
      "act_id": "act_1",
      "title": "灯灭之夜",
      "entry_when": {"all": [{"visited_room": "room_…入口"}]},
      "scenes": ["scene_hub", "scene_cellar", "scene_forge", "scene_tower", "scene_well"]
    }
  ],
  "places": [
    {
      "place_id": "place_well",
      "room_id": "room_…K7",
      "name": "枯井",
      "description_public": "井沿结了半指厚的霜……",
      "description_gm": "井底曾是一处暗哨出口，塌方后封死。",
      "exits": {"north": "room_…K3"},
      "on_enter": [
        {"id": "hazard_well_cold", "when": {"always": true},
         "effects": [{"kind": "hp_delta", "value": -6, "text_ref": "hazard.well"}],
         "once": false}
      ]
    }
  ],
  "actors": [
    {
      "actor_id": "npc_…F",
      "kind": "npc",
      "name": "守夜人老兵",
      "public": {"appearance": "旧军装改的厚袄", "attitude": "冷淡"},
      "gm": {"secret": "他在等自己当年的队伍", "lie": "只字不提同袍"},
      "knows": ["lore.watchman_past"],
      "dialogue": [
        {"topic_keys": ["信号", "灯", "任务"], "reply_ref": "reply.briefed",
         "sets_flag": "flag.briefed", "reveal": ["handout.orders"]}
      ]
    }
  ],
  "handouts": [
    {
      "handout_id": "handout.orders",
      "title": "换防手令（残页）",
      "text": "……着守夜人一名留守，直至信号灯重亮……落款处被雪水洇开。",
      "gm_note": "手令上的日期比老兵说的早三年，与他的故事矛盾。",
      "visibility": "clue",
      "reveal_when": {"all": [{"flag": "flag.briefed"}, {"has_item": "item.relic"}]},
      "one_shot": true
    }
  ],
  "clues": [
    {
      "clue_id": "clue_well_traveler",
      "handout_ref": "handout.traveler",
      "hint_text": "井底有人坐过，雪落了一层又化了一层。",
      "reveal_when": {"all": [{"visited_room": "room_…K7"}]},
      "hidden_by": [{"flag": "flag.act3"}],
      "weight": 1
    }
  ],
  "checks": [
    {
      "check_id": "check.signal_ignite",
      "rule_id": "survival.ignite_v1",
      "skill": "wis",
      "dc": 10,
      "when": {"all": [{"room": "room_…K6"}, {"has_item": "item.signal_torch"}]},
      "success": {"reveal": ["handout.signal_manual"], "effects": [{"kind": "set_flag", "flag": "flag.lit"}]},
      "failure": {"effects": [{"kind": "hp_delta", "value": -2}], "hint_ref": "hint.check_failed_ignite"},
      "retry": "after_turn"
    }
  ],
  "rewards": [
    {"reward_id": "reward.medal", "when": {"flag": "flag.lit"}, "grant_item": "item.medal", "inventory": true},
    {"reward_id": "reward.ending_rank", "when": {"flag": "flag.lit"}, "set_flag": "flag.ending_lantern"}
  ],
  "endings": [
    {"ending_id": "ending_lantern", "when": {"all": [{"flag": "flag.lit"}]},
     "title": "灯亮了", "text_ref": "ending.lantern", "rank": "S"},
    {"ending_id": "ending_fallen", "when": {"any": [{"hp_at_most": 0}]},
     "title": "你倒下了", "text_ref": "ending.fallen", "rank": "F"}
  ],
  "lorebook_ref": "story-lorebook.json"
}
```

### 3.1 字段职责与后端落点

| 分区 | 作用 | 玩家可见 | 服务端用途 |
| --- | --- | --- | --- |
| `acts/scenes` | 幕与场景推进 | 标题/公开摘要 | 幕切换触发器、目标链分组 |
| `places` | 房间 + 公开描述 + GM 描述 + 进入效果 | `description_public`/`exits` | 建局映射 `rooms`；`on_enter` 产生事件 |
| `actors` | NPC 公开面 + GM 秘密 + 对话分支 | 公开面 | 对话分支（替代写死的 `story_reply`）、`knows` 关联世界书条目 |
| `handouts` | 手札/信件/残页（一次性揭示） | 揭示后全文 | 揭示后进 `/view`、可写入叙事 |
| `clues` | 线索：提示语 + 揭示条件 + 权重 | `hint_text` | 三线索法则、防止卡关 |
| `checks` | 判定：rule_id/skill/DC/成败后果 | 成败结果叙事 | 映射 `RuleEngine.check_rule`，结果写事件 |
| `rewards` | 奖励：物品/标志/结局解锁 | 获得后可见 | 写事件（`ITEM_TAKEN` 复用或新 `MODULE_REWARD`） |
| `endings` | 结局条件与文本引用 | 触发后可见 | 读时求值（同 `story_ending`） |

### 3.2 条件谓词（白名单，禁止脚本）

`when` 只允许下列原子谓词与 `all/any/not` 组合，全部对 `GameState` + 揭示状态确定性求值：
`visited_room` / `in_room` / `has_item` / `item_tag` / `dialogue_state` / `hp_at_least` / `hp_at_most` /
`flag`（模组标志位，持久化）/ `clue_revealed` / `handout_revealed` / `turn_at_least` / `act`.
未知谓词 = 模组校验失败（`POST /world-modules/validate` 给 `loc/type/msg`），运行期不得静默跳过。

## 4. 与现有实现的映射（关键：复用 vs 需新增）

| 跑团概念 | 现有锚点 | 差距 | 期 |
| --- | --- | --- | --- |
| 世界简介/公开层 | `Room.description`、story 房间文案、`/view` | 无「幕」「场景」概念 | P0（用房间+目标近似） |
| Places / Scenes | `GameState.rooms` 图（story 6 房）、`Room.ambient_tags` | 无进入效果、无场景切换 | P0（进入效果复用 L1 `ENVIRONMENT_DAMAGE` 模式） |
| Actors / NPC 卡 | `GameState.npcs` + `CharacterCard` + `story_reply`（`action_service.talk_to`） | 对话分支写死在场景函数；无 `knows` 关联 | P1（数据驱动 `dialogue` 分支） |
| Journal（公开/线索） | **H4 世界书**：`domain/world_book.py` 关键词触发 + `lore.active_entries` | 世界书无揭示状态（纯读时激活）；不区分 public/clue | P0：world book + `visibility` 字段 |
| Handouts / 揭示 | 无 | 无揭示事件、无手札视图 | **P0 核心新增** |
| Clues / 逐步揭示 | `domain/suggestions.py`（可执行建议）、`TurnResult.effects.hint` | 无「线索池/权重/防卡关」 | P0/P1 |
| 判定 DC | `domain/rules/engine.py::RuleEngine.check_rule`（DC 5/10/15/20/25、d20、派生种子）+ `ruleset.py` | M2 裁剪：判定结果**不写事件**、不接叙事 | **P1 核心新增**（`CHECK_RESOLVED` 事件） |
| Resolver / 多角色 | `orchestration/resolver.py`（提案裁决、CAS） | 模组事件非角色提案；无「事件触发器」 | P2（触发器走提案队列） |
| 信息隔离 / GM 层 | `PerceptionSlice` 结构性防泄漏、`query_service.get_player_view` | 无「模组 GM 字段」概念，需新增视图过滤 | **P0 安全红线** |
| 目标/结局 | `scenario_story.story_goal/story_ending`（读时纯求值）、`/view.goal.status` | 目标写死在场景模块；结局无 rank/多结局 | P0（数据化 goal/ending refs） |
| 奖励 | `ITEM_TAKEN`/`CRAFT` reducer、`ENVIRONMENT_DAMAGE`（H4 新范例） | 无「奖励发放」通用事件 | P1 |
| 重放/审计 | 事件溯源 + golden/provenance | 新 `EventType` 需登记 + 集合断言测试同步 | 全期约束 |

### 4.1 揭示状态怎么存（三方案，推荐 B）

| 方案 | 做法 | 优点 | 缺点 |
| --- | --- | --- | --- |
| A 新事件 | 新增 `LORE_REVEALED` + `HANDOUT_REVEALED` 事件 + reducer | 语义清晰、审计好、回放稳 | 需登记 EventType/契约、事件膨胀 |
| **B 复用 Quest 模型**（推荐） | 揭示 = `GameState.quests` 的 `Stage.completed` / `QuestState`（模型已存在且闲置，`models.py:116-151`）；一个 handout/clue 对应一个 `Stage` | 零新事件类型、模型已校验（completed 必须前缀）、可回放 | 语义借用（需在文档/契约注明 quest=揭示进度） |
| C 读时派生 | 不持久，按当前状态重算「揭示」 | 零存储 | 刷新/重连/回退后揭示丢失，**不可接受** |

方案 B 的落点：新增纯函数 `domain/world_module.py::evaluate_reveals(module, state) -> RevealResult`（返回应揭示的 handout/clue/flag 集合），编排层据此**产生一次** `ADMIN_CORRECTION` 风格的既有事件？——不，Quest 更新需要事件；**更稳妥**：新增一个专用事件 `MODULE_FLAGS_UPDATED`（payload 只有 flag/quest 增量），仅在模组存档产生 → 旧场景 golden 零影响。若 H5 拍板选 B，需要在 `EventType` 加 1 个成员并同步 `test_domain_models` 集合断言（H4 的 `ENVIRONMENT_DAMAGE` 已有同样先例）。
> 结论：**A 与 B 二选一，建议 B（1 个通用事件 + Quest 复用）**；不采用 C。

## 5. 一回合的运行流程（目标形态）

```
玩家输入 → 解析 → 工具门/规则前置 → 现有事件（移动/拿取/对话/制造/判定）
        → 模组引擎：① 进入效果 ② 触发器求值（谓词） ③ 判定（如需）
        → 生成模组事件（flags/handout/check 结果，同回合批次提交）
        → 读时投影：/view 得到 public + revealed 线索/手札 + 目标/结局
        → 叙事：模板分支（确定性）+ 世界书 active entries（H4 已实现）；LLM 只渲染
```

约束：模组事件与既有事件同批提交（复用 `GameEngine.submit` 的原子性与 CAS）；确定性引擎决定「揭示什么」，LLM 只决定「怎么说」。

## 6. 分期实现计划

| 期 | 范围 | 玩家可感知验收 | 工作量 |
| --- | --- | --- | --- |
| **P0 最小可玩**（建议 H5 首批） | 模组 JSON schema + 导入/校验；`places/actors(public)/handouts/endings` 映射到 story 场景；**1 个通用模组事件** + 揭示状态；`/view` 加法 `handouts[]/clues[]`（GM 层过滤）；1 个「揭示手札」触发器 + 1 个进入效果 | 玩 story 时：与老兵对话 → 手札《换防手令》进面板；进枯井触发寒气屏（L1）；点亮灯 → 结局 + 通关 rank；刷新/续玩后手札仍在 | 6–9 切片 / **2–3 人日** |
| **P1 完整跑团** | `checks` 判定入事件（DC/属性/成败叙事/重试）；`rewards`；`clues` 权重与三线索法则；`acts` 幕切换；数据驱动 NPC 对话分支；模组编辑/预览 CLI | 玩家遇到「点火判定」：d20 结果可见、失败可重试；每幕目标刷新；收集 3 条线索开新结局 | 12–18 切片 / **4–6 人日** |
| **P2 生态/智能** | 模组 Compendium 打包与分享；GM 标注视图；LLM 动态线索（只渲染）；多人/异步；内容编辑器 | 导入第三方模组即玩；GM 与玩家分离视图 | 另立里程碑（**不建议本轮启动**） |

P0 的旧场景零影响策略：模组数据只在 `game_created.params.world_module_id` 存在的存档生效；旧场景不加载模组、不产生新事件、`/view` 新字段为 `null`（同 H4 世界书做法）。

## 7. 工作量与风险

| 项 | 估计 | 说明 |
| --- | --- | --- |
| 模组 schema + 校验/导入 | 1–1.5 人日 | 比世界书多「GM 层过滤 + 条件谓词校验」 |
| 揭示状态（1 事件 + reducer + 视图） | 1–1.5 人日 | 方案 B 可借 Quest 模型；含 golden/provenance 检查 |
| story 适配 P0 | 0.5–1 人日 | 现有场景已接近模组；主要是数据化目标/结局 refs |
| 判定接线 P1 | 2–3 人日 | 判定结果事件 + 叙事 + 测试矩阵（DC/属性/失败重试） |
| NPC 对话数据化 P1 | 1.5–2 人日 | 替代 `story_reply` 写死分支，兼容旧场景 |
| 内容作者成本 | 每个模组 0.5–1 人日 | 文案为主；示例模组可复用 story 内容 |

风险清单：

| 风险 | 级别 | 处置 |
| --- | --- | --- |
| **GM 层泄漏**（秘密进 `/view`/prompt/日志） | P0 安全 | GM 字段只在模组定义；`/view` 只投影已揭示；单测断言「GM 文本不出现在任何玩家响应」 |
| 新 EventType 影响旧 golden | P0 | 只在模组存档产生；集合断言测试同步；replay 68/provenance 复跑 |
| 条件谓词语言膨胀成脚本引擎 | P1 | 白名单原子谓词，禁 eval/循环；未知谓词=校验失败 |
| 判定入事件破坏回放确定性 | P1 | 掷骰走既有 `DerivedRandom`（seed/turn/actor/rule/seq 派生），结果入事件 |
| 与世界书职责重叠 | P2 | 明确：世界书=事实注入（无状态），模组=结构/揭示（有状态）；模组引用 `lorebook_ref` |
| LLM 提前剧透未揭示线索 | P1 | prompt 只注入已揭示 + public 事实；加「禁止推测未揭示内容」硬约束 + cassette 测试 |
| 内容量撑不起多幕 | P2 | 先做 1 个 8–14 回合切片模组（story 即雏形），再评估第二模组成本 |

## 8. 本轮不实现声明

- 本文件**只是设计**：不新增 API/表/事件/前端页面，不写半成品代码。
- H4 已交付的相关能力（世界书导入/触发/注入、story 目标与结局、L1 环境伤害）**继续按 H4 契约推进**，与本设计不冲突。
- 下一步（H5 拍板项）：①是否采用方案 B（Quest 复用 + 1 通用事件）存揭示状态；②P0 是否以 story 场景为第一个模组；③世界书 `visibility` 字段（public/clue）是否并入 H4 现有契约（加法）。
