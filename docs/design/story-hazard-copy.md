# L1 失败结局内容设计：枯井「寒气」环境伤害文案

> 归属：H4 / L1（失败结局不可达）。角色：game-designer（内容设计）；接线：backend-dev（编排/事件）+ algorithm-dev（模板叙事）。
> 状态：**设计定稿，本轮由 backend/algorithm 接线**（本文件不改 `src/**`，只给文案与接线约束）。
> 契约依据：[H4-CONTRACT §6](../../.work/team-player-2026-11-10/H4-CONTRACT.md)、`domain/scenario_story.py::story_room_damage`（枯井 `-6` HP/次）。

## 1. 目标与口径

L1 要解决的问题：`story` 的「倒下」结局（`ending_fallen`）逻辑与测试都在，但**唯一伤害源只有一瓶烈酒（-4 HP）**，玩家不可能真的倒下 → 失败结局不可达、死亡分支不可被玩家看见。

本设计给出**可重复、确定性、玩家可感知**的伤害叙事：

- 机制（已由 backend 定稿）：枯井 `-6 HP/次`，可重复；初始 `12/20`，药水 `+8`、烈酒 `-4`。
- 一个自然旅程：`12 → 进井 6（重伤警告）→ 喝药水 14 / 或再进井 0（倒下）`；喝药水后再进井 `14→8→2→0`，仍可能死，但只要肯喝药就能回来。
- 所有文案 **80–150 字/屏**（项目硬约束），同一状态必得同一文本（可回放）。
- 旧场景（m2/m3/m4）不产生 `ENVIRONMENT_DAMAGE` 事件 → 本设计对旧 golden **零影响**。

## 2. 分档规则（确定性，narrator 侧选择）

`ENVIRONMENT_DAMAGE` 只带事件，不带「是第几次进井」；因此**分档只看结算后的 HP**（`hp_after`），由编排层算好放进 `result_data["environment"]`：

| 档位 | 条件（`hp_after`） | 用哪一屏 | 玩家感受 |
| --- | --- | --- | --- |
| A 轻度 | `hp_after >= 8` | S1 | 「这地方不对劲」，还有余力 |
| B 重度 | `1 <= hp_after <= 7` | S2 | 明确警告：**再下去一次就回不来** |
| C 致命 | `hp_after == 0` | S3 | 与「倒下」结局衔接 |

`hp_after = clamp(player.hp + hp_delta, 0, max_hp)`，纯整数运算所以确定性。回放时叙事已存在事件 `result.narrative` 里，不再重算。

## 3. 文案（可直接落地，已逐条核字数）

### S1 · 进入枯井受伤（`hp_after >= 8`，82 字）

> 你顺着井沿往下挪，脚踩在冻硬的土壁上，一级一级蹭到井底。暖气从四面八方围上来，舒服得让人发懒，可旧伤在肋下抽了一下，提醒你这地方不对劲。井口那一小块天，灰白得刺眼。

### S2 · 重复进入（`1 <= hp_after <= 7`，88 字；本身就是「倒下前提示」）

> 你又翻下井沿。这一次寒气没给你缓的时间，从脚底一路钻到心口，手抖得几乎抓不住井壁，呼吸变得又浅又急。井壁上的霜被你的袖子蹭落，露出底下湿冷的泥——再下去一次，你多半就上不来了。

### S3 · 致命进入（`hp_after == 0`，88 字；随后进结局屏 `ending_fallen`）

> 你翻下井沿时，指尖已经握不住冻硬的土。寒气漫过胸口之前，你看见井口那一小块灰白的天，想起老兵说的那句「别往河边去」。井底那个旅人还坐着，你大概明白他为什么不走了。然后，风停了。

衔接：本屏是**最后一回合的叙事屏**；同一回合 `story_ending()` 返回 `ending_fallen`，前端/R3 结局面板显示既有结局文本（61 字「你倒下了…」）与「故事结束」。两屏语义不重复：S3 写「怎么倒下」，结局屏写「倒下之后」。

### S4 · 状态提示行（`hp_after <= 6` 时追加；33 字，不是屏文本）

> 井底的暖气在吸人，再下去一次就回不来了；先喝下治疗药水，别再下井。

- 通道：`TurnResult.effects=[{"hint": ...}]`（既有加法字段，golden 不比对；`suggested_actions` 由 `domain/suggestions.py` 自动生成，本轮不改）。
- 若玩家行囊里只有烈酒（`hp_delta=-4`），hint 换成：

> 烈酒压不住这种寒气，喝下去只会让你倒得更快；找药水，或者别再下井。

### S5 · 药水救回（可选，story 版使用屏，84 字）

> 药水的苦味从喉咙一路烧到心口，暖意慢慢铺开，冻僵的手指终于能动了。你扶着井壁站稳，抬头看了看井口那片灰白的天，一步一步往台阶上挪。井底的风停在背后，像有人把门轻轻合上。

- 现状：`narrator._used_text` 的药水文案已可用（旧场景逐字不变），**本句为可选升级**：仅对 `is_story_state` 且物品 `stats.heal > 0` 使用；不进则维持现状（不阻塞 L1）。
- 烈酒现有专属文案「烈酒呛得喉咙发烫，寒气被逼退了一些」与 `-4 HP` 有轻微误导，建议下一批改成「烧胃、手抖」的收尾（不在本轮范围）。

## 4. 接线点（给 backend / algorithm）

1. **事件（backend，`turn_orchestrator.py`）**：story 存档中 `CHARACTER_MOVED` 生效后，取 `story_room_damage(target_room)`；非 `None` 时同批次追加草稿事件：
   ```
   Event(turn_id=…, actor_id=PLAYER_ID, method="environment_damage",
         event_type=EventType.ENVIRONMENT_DAMAGE,
         params={"room_id": "<目标房 id>"}, result={"hp_delta": -6})
   ```
   并在本回合 `result_data["environment"] = {"room_id": …,"hp_delta": -6,"hp_after": <clamp 后的整数>,"max_hp": <int>}`。
   `params`/`result` 里**不要**写玩家可读文案（文案归 narrator，避免审计/回放混层）。
2. **叙事（algorithm，`orchestration/narrator.py`）**：`render()` 里若 `events` 同时含 `CHARACTER_MOVED` 与 `ENVIRONMENT_DAMAGE`（顺序：移动在前），走新分支 `_hazard_moved_text(...)` 按 §2 分档返回 S1/S2/S3（**不要**把移动模板与寒气句拼接，会超 150 字）。
   - 缺 `result_data["environment"]` 时保守回退：`hp_delta < 0` → S1（旧路径/手工事件也能渲染）。
   - 新分支只在该事件组合下命中，m2/m3/m4 永不产生该组合 → 旧屏文本逐字不变。
3. **结束回合（backend）**：`hp_after == 0` 时该回合照常提交（伤害事件已入状态），`story_ending` 读时返回 `ending_fallen`；现有 ending 字段/结局面板无需改动。
4. **提示行（P2，可选）**：hazard 回合把 S4 塞进 `TurnResult.effects`（仅 `hp_after <= 6`）。若本轮不做，不阻塞 L1 验收（S2 已含口头警告）。

## 5. 玩家可感知验收（手工，5 分钟）

| 步骤 | 期望 |
| --- | --- |
| 新开 story → 直接往南进枯井（12 HP） | 屏为 S2（88 字，含「再下去一次，你多半就上不来了」），状态行 HP 6/20 |
| 再进一次枯井（6 HP） | 屏为 S3（88 字，写「风停了」），同回合进「你倒下了」结局屏；输入禁用 |
| 新开 → 拿药水 → 喝（12→20）→ 进井 | HP 14/20，屏为 S1（82 字） |
| 新开 → 进井（6）→ 喝药水（14）→ 进井（8）→ 进井（2）→ 进井 | 先 S1 再 S2 再 S3，证明**可救回也仍可选择硬闯** |
| 旧场景 m2/m3/m4 任意移动 | 逐字与 HEAD 一致（无 `ENVIRONMENT_DAMAGE`、无文案变化） |

## 6. 风险

| 风险 | 说明 | 处置 |
| --- | --- | --- |
| 旧 golden | m2/m3/m4 无枯井、无 `ENVIRONMENT_DAMAGE` | 事件/叙事分支均为新组合，零命中；`demo --check`/`console --check` 逐字 |
| 回放 | 文案存在 `narrative` 里；档位由 `hp_after` 决定且写进事件上下文 | 不重算；同 seed 同输入同文本 |
| 伤害过于致命 | 初始 12 HP 第一次进井就 6，第二次即死 | 这是设计意图（失败结局可达）；药水 +8 可救、可绕过枯井；不强制 |
| m3 多角色链路 | story 不启用 NPC Agent（M3 计划表只对 m3） | 无影响 |
| 世界书联动 | 枯井条目（lorebook #21/#22）已有的「暖气不对劲」「没人把他搬上来」与本文案同调 | 不冲突，反而补设定 |
