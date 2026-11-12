# ADR · 场景 DSL v2 设计与实现边界（D-N1-1）

- 状态：已定案（设计+实现） ｜ 决策人：lead ｜ 复核人：独立复核（设计评审 + 四门 + 目标测试；dsl-v2 执行稿） ｜ 日期：2026-11-12 ｜ 基线：`553d0a0`
- 关联：第 17 章 §17.9.2 **N1 内容轨 029（设计）/030（实现）**；路线图 ADR `2026-11-11-post-m6-roadmap.md`
  **D-N5**（DSL v2 边界：纯数据 + 白名单 + 无 eval）、**D-N6**（历史类条件仅 033–034 可新增 `EventType`，需用户拍板）、
  **D-N7**（不做清单）；v1 契约 `docs/design/user-scenario-modules.md`；设计文档 `docs/design/scenario-dsl-v2.md`；
  进度文档 `docs/progress/029-2026-11-12-scenario-dsl-v2-design.md`、`030-2026-11-12-scenario-dsl-v2-impl.md`；风险 `R-T12`（模组/契约子集收窄）。
- 本 ADR 只定 **v2 的 DSL 语义、版本兼容与多结局选择边界**；不改变 v1 文件语义，不触碰引擎（`GameState`/reducer/
  `EventType`/golden/API DTO）。

## 1. 背景与红线

- v1（UC 批次）已交付场景 JSON 上传：白名单条件（`in_room`/`has_item`/`has_item_tag`/`npc_state`/`hp_lte`/
  `hp_gte` + `all/any/not/always`）、目标/结局读时求值、进入伤害、NPC 对话；P4 遗留「flags/quests/时间条件未做」。
- 用户诉求（D-N5）：条件更能写（flag/quest/物品/NPC 状态/时间、多结局分支），但**内容仍是纯数据**；
  下一步 031 编辑器 / 032 分享必须建立在**静态可校验、可确定性回放**的格式上。
- 现状约束：`UserScenarioHooks` 只拿得到 `GameState`（当下快照），状态变化只来自既有事件
  （`CHARACTER_MOVED`/`ITEM_TAKEN`/`ITEM_CONSUMED`/`DIALOGUE_EXCHANGED`/`ENVIRONMENT_DAMAGE` + 回合公共规则）。
- **红线（不放宽）**：
  1. 不新增运行时依赖；**不新增 `EventType`**；不改 `GameState` 字段/reducer/golden；
  2. 用户 JSON 永不执行：无 eval/脚本/表达式，条件只认白名单键；`extra="forbid"`；
  3. v1 文件**逐字兼容**：旧字段/旧谓词/旧行为/旧测试断言零改动；旧 4 场景 golden 与 demo/console 逐字不变；
  4. 确定性：条件求值无随机/时钟/IO，结局是读时视图、不进状态（`state_hash` 不漂移）；
  5. 错误可定位：导入期给结构化 `{path,message}`，不把坏定义带进运行时。

## 2. 决策

### D-N1-1.1 版本声明式：`schema_version` 是特性级别，不做静默升级

- **决策**：`ScenarioDefinition` 接受 `schema_version=1|2`；**1 = 只允许 v1 十个条件键**，出现 `flags` 或任一 v2
  谓词 → 结构化错误（路径精确、消息「需要 schema_version=2」）；**2 = v1 全量 + v2 加法**；其它值（3/`"2"`/
  `true`/`null`）→ `path=schema_version` 的结构化错误。v2 读取器读 v1 文件逐字兼容（`schema_version` 保留 1、
  `flags=[]`）；旧 v1 读取器读 v2 文件按上限明确拒绝。
- **理由**：文件声明什么级别就按什么级别校验，编辑器/分享不会把「只有新程序能读的内容」伪装成 v1；
  同时保住「v1 文件永不被新校验拒绝」的兼容承诺。
- **被否选项**：① 单一宽松读取器（v1 文件里也放行 v2 谓词）——会让 v1 语义边界消失，旧读者读到未知键时
  行为不可预期；② 版本号自动升级（读到 v2 字段就改成 2）——静默改写作者原文，违背「原样存/原样回」。
- **影响面**：`domain/user_scenario.py`（`ScenarioDefinition.schema_version` 范围、版本门禁、错误消息）。
- **回写落点**：`docs/design/scenario-dsl-v2.md` §3.1/§8；`docs/design/user-scenario-modules.md` §6（v2 回填，Lead 同批）。
- **验收断言**：v1 旧测试零改动全绿；9 个 v2 谓词逐个参数化断言「v1 声明 → 精确路径错误」；
  `schema_version` 3/字符串/bool/null 各有结构化错误。

### D-N1-1.2 白名单谓词扩展：9 个原子，全部只读既有派生状态

- **决策**：新增 `flag`、`objective_done`、`has_item_tag_count`、`item_in_room`、`npc_in_room`、`turn_gte`、
  `turn_lte`、`world_time_gte`、`world_time_lte`；组合子沿用 `all/any/not/always`；每条件节点仍「恰好一个键」。
  `flag` 是**派生命题**：顶层 `flags[]` 声明 `{key, when}`（≤16、key 唯一、when 必填），`{"flag":k}` 的值为
  `flags[k].when` 的求值结果，**不持久化、不写状态**；flag/目标进度依赖图导入期 DFS **静态检出环**，
  求值期 memo（每个 flag/目标一次）+ 环上 fail-closed 返回 false。
- **理由**：上述谓词的数据来源全部是既有 `GameState` 字段（location/inventory/room.item_ids/npc.room_id/
  npc.dialogue_state/turn/world_time）与目标 `when`，因此零引擎改动即可表达「收集 N 件 / 物品还没拿 / 剧情开关 /
  第 N 回合后 / 目标链」；纯函数求值保证回放可复算。
- **被否选项**：① 持久化 flag（需要新事件/状态字段）——碰 D-N6 红线且破坏旧 golden；② 用 `inventory` 里的
  「标记物品」模拟 flag——无生成物品的动作路径，不可靠；③ 表达式/模板（如 `hp < 6 and flag.x`）——不可静态校验。
- **影响面**：`Condition` 模型（新增 9 字段 + 3 个载荷模型 `ItemTagCountCondition`/`ItemRoomCondition`/
  `NpcRoomCondition`）、`evaluate_condition`（新增可选 `definition` 上下文）、`_condition_env`、`_evaluate_named`。
- **回写落点**：`docs/design/scenario-dsl-v2.md` §3.3/§3.4；`examples/scenarios/lantern-vigil.json`。
- **验收断言**：每个原子有「真/假/边界」单测；`extra="forbid"` 未知谓词给结构化错误；组合子与 v2 原子混用；
  求值不修改入参状态且重复求值一致。

### D-N1-1.3 `objective_done`（quest 类）与跨类型依赖图

- **决策**：`{"objective_done": k}` = `goal.objectives[k].when` 的求值结果（`when` 缺失 = 恒不完成 =
  false）；goal 面板仍按「读时求值」逐 objective 显示 done（与 v1 同构）。flag 与目标进度共用一张依赖图做环检测
  （flag→flag、objective→objective、flag↔objective 均覆盖），环 = 导入错误，避免运行期递归爆炸。
- **理由**：用户场景没有 quest 创建/推进事件（`GameState.quests` 恒空），目标链是唯一可用的「任务进度」来源；
  用「目标的 when」而不是新状态，既表达目标前置关系，又不改引擎。
- **被否选项**：① 暴露 `GameState.quests` 的 `quest_state` 谓词——用户场景无创建路径，属死谓词；
  ② 允许在 `when` 里直接内联重复写前置条件——可读性差且无环保护。
- **影响面**：`Condition.objective_done`、`_condition_slots`（flags 也纳入静态校验）、`_check_dependency_cycles`。
- **回写落点**：`docs/design/scenario-dsl-v2.md` §3.3/§5。
- **验收断言**：目标链「listen→gather→light」面板推进单测；自环/跨类型环均拒绝；未知目标 key 精确报错。

### D-N1-1.4 多结局选择语义冻结（v2 只让 `when` 更能写，不改选择规则）

- **决策**：候选 = `endings[]`（≤6）；顺序 = `priority` 降序 → 声明顺序；首个满足 `when` 者即结局；`when`
  缺失永不触发；都不满足 → 兜底 ①「目标全完成」胜利 ②「hp≤0」倒下（顺序同 v1）；结局是**读时纯函数视图**
  （`{ending_id,title,text,kind}`），不持久化、不写事件；求值非空后 `status=ended`，编排层拒绝新回合
  （`INVALID_INPUT`，零写入）；`kind` 仍只有 `win/lose`。**不新增「选择结局」动作/菜单/事件**；玩家选择通过
  既有动作造成的状态差异表达（互斥 `when` / 优先级覆盖 / 超时软失败三种作者模式）。
- **理由**：改选择规则（如「玩家选结局」）需要新工具+新事件+DTO/UI 变更，超出 D-N5/D-N7 边界；把现有规则
  写死成契约后，031 编辑器与 032 分享可依赖「同状态必同结局」。
- **被否选项**：① 结局事件化（记录 chosen ending）——破「不新增 EventType」与旧 golden；② 玩家选项菜单——
  需新动作/端点/前端 DTO；③ `kind` 扩展 `neutral`——前端契约测试与配色需同步改，收益不足。
- **影响面**：无代码改动（v1 已实现该规则）；仅文档冻结 + 单测锁定 v2 条件下的 same 行为。
- **回写落点**：`docs/design/scenario-dsl-v2.md` §6。
- **验收断言**：v2 flag/目标链参与下的多结局优先级、同权声明序、状态驱动选择、兜底顺序（完成优先于倒下）
  四组单测；`/view.status=ended` 后新回合被拒。

### D-N1-1.5 schema v2 delta 最小化：只加 `flags`，summary/DTO 不变

- **决策**：v2 顶层只新增可选 `flags`（0..16）；其余字段与 v1 完全一致；**summary 仍冻结 6 键**
  （`rooms/items/npcs/objectives/endings/suggestions`，不含 flags 计数）；`/scenarios/*` 与
  `POST /games {scenario_id}` 的请求/响应形状全部不变（v2 定义照常入库，`schema_version=2` 原样存/原样回）。
  编辑器要展示 flags 时读详情里的原始 `definition`，不得新增响应字段（除同步四处契约）。
- **理由**：031/032 与前端契约测试成本最小；v2 的复杂度应落在条件语义而不是传输形状。
- **被否选项**：① summary 加 `flags` 计数——需改 `api/schemas.py` + `web/src/api/types.ts` + 契约测试（超 030 范围）；
  ② 新增 `retry`/`once`/`choice` 等字段——需求未定，先冻结。
- **影响面**：`ScenarioDefinition.flags`、`_collect_warnings`（未引用 flag warning）；服务层/路由零改动。
- **回写落点**：`docs/design/scenario-dsl-v2.md` §4；031/032 实现时对照 §4.3。
- **验收断言**：v2 定义 validate/import/list/detail 全链路测试；summary 键集合断言不变；
  `examples/scenarios/lantern-vigil.json` 可导入可通关。

### D-N1-1.6 求值上下文注入：零引擎改动、v1 调用签名兼容

- **决策**：`evaluate_condition(condition, state, ids, *, definition=None)` 增加**可选关键字** `definition`，
  由 `UserScenarioHooks` 构造期缓存 `_condition_env`（flags/目标 when 查表）并在 goal/ending 内部使用；
  v1 调用方（旧测试/旧钩子）不传 `definition` 时行为与 v1 完全一致；`ScenarioHooks` 协议、
  `scenario_runtime`/`scenario_service`/`api`/`query/action/orchestrator` 装配零改动。
- **理由**：v2 上下文只属于「某份用户定义」，不应进 `GameState`/全局单例；构造期缓存避免每回合重建。
- **被否选项**：① 把 flags 塞进 `ScenarioIds`（key→派生 ID 映射的类型语义被污染）；② 改钩子协议加参数
  （要同步 query/action/orchestrator 三处与内置场景函数）。
- **影响面**：`domain/user_scenario.py`（`_condition_env`/`_evaluate`/`UserScenarioHooks._match`）。
- **回写落点**：`docs/design/scenario-dsl-v2.md` §2.3。
- **验收断言**：v1 旧测试零改动；v2 钩子 goal/ending 在 flag/目标链下正确；`evaluate_condition` 旧 3 参调用可用。

### D-N1-1.7 「不可表达」清单与归属（防止后续反复讨论）

- **决策**：历史条件（曾进入/曾对话/物品曾使用/首次进入/一次性/限时一次性）不可表达——`GameState` 无历史字段，
  钩子只拿得到当下快照；归 **N1 033–034（D-N6：唯一允许新增 `EventType` 的切片，需用户拍板 + 独立 ADR
  D-N1-2 + 受控 golden 重基线）**，v2 不硬改引擎。其余不做：物品堆叠数量（`Item` 无数量字段/无堆叠动作）、
  玩家选择结局菜单（需新动作/事件/DTO）、`GameState.quests` 状态机（无创建路径）、NPC 移动/日程/阵营、
  玩家属性比较（无属性变更事件 → 恒真/恒假）、随机条件、跨存档/跨场景/世界书条件。
- **理由**：把「做不了/不归我做」写进契约，避免 031/032/035 期间反复试探引擎边界；与 D-N7 不做清单一致。
- **被否选项**：用事件历史即时计算（需把事件日志喂给钩子）——改钩子协议+只读链路，且历史语义应事件化持久
  （刷新/重连/回放才不丢），正是 033–034 的立项理由。
- **影响面**：无代码；文档冻结 + 风险 R-T12 收窄说明。
- **回写落点**：`docs/design/scenario-dsl-v2.md` §7；第 17 章 §17.9.5（按 D-N7 已有口径）。
- **验收断言**：设计文档 §7 两表齐全（可表达/不可表达 + 原因 + 归属）；§17.9.2 N1 的 033–034 行仍是历史类
  条件的唯一归属，未被 030 提前消费。

### D-N1-1.8 静态校验与错误模型（导入即失败、可定位）

- **决策**：`parse_definition` 返回 `ParsedDefinition(errors, warnings, summary)` 不抛异常；顺序 =
  JSON/体积/编码 → `schema_version` 预检 → Pydantic（`extra="forbid"` + 未知谓词中文消息 + 路径）→
  版本门禁 → 语义引用/环；errors ≤50、形状固定 `{path,message}`；warning 只用于不阻断项（未引用 flag 等）。
- **理由**：玩家手写 JSON 的修错体验是 v1 已有能力（路径可定位），v2 的引用/环必须同样可定位，
  不能把坏定义带进运行时（否则求值期才炸）。
- **被否选项**：只在运行期兜底（try/except 返回 false）——错误不可定位、条件静默失效，破坏可玩性预期。
- **影响面**：`_schema_version_issue`、`_issue_of_validation_error`、`_version_gate_errors`、
  `_check_conditions`、`_check_dependency_cycles`、`_collect_warnings`。
- **回写落点**：`docs/design/scenario-dsl-v2.md` §5。
- **验收断言**：未知谓词/未知字段/未知引用/环/版本错配/非法版本各有结构化错误单测；v1 既有错误路径测试零改动。

## 3. 契约→切片映射

| 决策 | 权威文档落点 | 实施切片 | 验收断言 |
| --- | --- | --- | --- |
| D-N1-1.1 版本声明 | `scenario-dsl-v2.md` §3.1/§8 | 030 | v1 零回归 + 9 谓词版本门禁参数化 |
| D-N1-1.2 白名单扩展 | §3.3/§3.4 | 030 | 每原子真/假/边界单测 + 未知谓词错误 |
| D-N1-1.3 objective_done | §3.3/§5 | 030 | 目标链面板推进 + 环/未知引用拒绝 |
| D-N1-1.4 多结局语义 | §6 | 030（文档+单测冻结） | 优先级/同权/状态选择/兜底顺序 |
| D-N1-1.5 schema v2 delta | §4 | 030 | summary 6 键 + API 形状不变 |
| D-N1-1.6 上下文注入 | §2.3 | 030 | v1 旧签名兼容 + v2 钩子正确 |
| D-N1-1.7 不可表达归属 | §7 | 033–034 | 历史类条件不在 030 消费 |
| D-N1-1.8 错误模型 | §5 | 030 | 结构化错误矩阵 |

## 4. 已知问题归属

| 编号 | 摘要 | 归属 | 关闭条件 |
| --- | --- | --- | --- |
| R-T12（收窄） | 跑团模组/历史揭示仅设计；世界书剩余字段不做 | N1 033–034 + §17.9.5 | 跑团 P0 交付或用户改口；历史条件随 D-N1-2 落地 |
| — | 历史/首次/一次性条件不可表达 | N1 033–034（D-N6） | 新增事件类型 + reducer 派生 + 受控 golden 重基线 + 用户拍板 |
| — | 「选择结局」菜单/动作 | 不做（D-N5/D-N7） | 用户明确要求 → 新 ADR + 031 同批 |
| — | v1 设计文档 v2 回填（`user-scenario-modules.md` §6） | Lead（030 禁改） | 文档同批 |
| — | PROGRESS §3/§4 与决策索引行同步 | Lead（030 禁改） | `check_docs` PASS |

## 5. 出口验收判据与到期承诺处置

- 本 ADR 出口 = **设计+实现一致**：`docs/design/scenario-dsl-v2.md` 与 `domain/user_scenario.py` 语义逐条对应；
  目标测试集全绿；v1 旧测试零改动；replay 无回归；`ruff format --check`/`ruff check`/`mypy` 对变更文件通过
  （全量 mypy 由 Lead 在并行切片收敛后统一跑）；进度文档 029/030 数字实测。
- 到期承诺处置：v1 文档 §5「P4 条件 DSL 扩展（flags/quests）未做」由 029/030 **关闭**（标注 v2 已交付）；
  D-N5 验收断言「v2 解析/求值单测 ≥25」由 030 实测覆盖（45 个 collected case）；
  「历史类条件」明确移交 033–034，不再作为 v2 挂账。

## 6. 后移与复审

- 首轮复审点：031 编辑器开工前（确认 flags/schema_version 表单与 §4.3 无响应字段新增）；
  032 分享开工前（导出保留 `schema_version`，导入走同一 validate）；
- 若用户要求「选项式结局」：先在 D-N7 边界外新立 ADR（新动作/事件/DTO + 030 语义扩展），不得在 030 内偷偷加；
- 若 033–034 定案新增揭示事件：届时评估 v2 是否升 `schema_version=3`（历史 flag 谓词）——必须新 ADR + 受控
  golden 重基线，不在本 ADR 内改写结论。

## 7. 变更纪律

- 本 ADR 一经定案不改写结论；后续调整 = 新 ADR + `supersedes: D-N1-1`，并同步第 17 章 §17.9、设计文档与本目录索引；
- 白名单谓词集合是**安全边界**：新增/修改谓词必须同批更新设计文档 §3、校验器、单测与 `docs/decisions/README.md` 索引；
- 任何「执行用户内容」的倾向（eval/脚本/表达式/模板）一律拒绝，按 D-N5/D-N7 处理；
- 029 设计与 030 实现的语义偏离（若有）必须在进度文档第 2 节契约对照表登记，不允许静默偏离。
