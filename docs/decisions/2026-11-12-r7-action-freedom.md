# ADR · R7 行动自由度：新增动作工具/事件类型与执行链扩张（D-R7-1..5）

- 状态：**已定案（D-N6 用户批准）**｜决策人：lead（用户批准新增 EventType）｜整理：docs-release｜日期：2026-11-12｜基线：`269fc03`（R6 收口）+ R7 未提交工作树
- 关联：R7 进度文档 `docs/progress/R7-2026-11-12-action-freedom.md`；契约 `.work/r7/contracts.md`（不入库）
- 关联编号：D-N6（唯一允许新增 `EventType` 的例外，用户已批准）、R5 §4 第 16/17 条（0.6 阈值/实体映射，本批改进）
- 发布口径：并入 `[0.4.0]` 未发布段（不 bump 版本；发布 checklist 未执行）

## 1. 背景与红线

玩家反馈「行动自由度受限」：8 个工具只覆盖看/拿/用/聊/走/制造，攻击、丢弃、给予、临场动作均无工具；解析单阈值 0.6 导致大量合法意图被澄清；一句多动作只执行一步；LLM 常把实体名当 ID 导致 `NOT_FOUND`。R7 在 **D-N6 用户批准新增 EventType** 的前提下做「纯加法」扩张。

**红线不变项**：唯一写入者 `GameEngine`；CAS/幂等/审计；事件溯源与 `Event` 基线字段；Resolver 纯函数、LLM/裁判不写状态；actor_scope 白名单只增不放宽；叙事事实白名单；用户 JSON 不执行；旧 m2/m3/m4 golden 逐字不变（`build_m2_tool_registry()` 不改、旧场景不产生新事件 → **不需要重基线**；若出现 golden 漂移必须停止并报告 Lead，禁止 `--snapshot-update`/手改 golden）。

## 2. 决策

### D-R7-1 新增 4 个 EventType（纯加法，旧 golden 零重基线）

| EventType | method | params / result | reducer 语义（严格类型，非法 `ValueError`） |
| --- | --- | --- | --- |
| `ITEM_DROPPED` | `drop_item` | params `{item_id, from_room}` / result `{to_room, item_name}` | actor 存在；item 在 inventory 且 `from_room == actor.location`；从 inventory 移除（保序）、item_id 追加到 `room.item_ids`（不得重复）；只改 characters/rooms |
| `ITEM_GIVEN` | `give_item` | params `{item_id, npc_id}` / result `{npc_name}` | actor/item/npc 存在且同房间；从 inventory 移除；`state.items` 保留（NPC 无背包模型，物品离开玩家持有——文档注明） |
| `COMBAT_RESOLVED` | `attack` | params `{npc_id, room_id}` / result `{hit, roll(1..20), total, dc, damage(>=0)}` | 同房间校验；`hit=True` 从房间 `npc_ids` 与 `state.npcs` 移除；`hit=False` 状态零变化（仅规则推进 turn/world_time）；bool 当 int/roll 越界等 → `ValueError` |
| `IMPROVISED_ACTION` | `improvise` | params `{action_text}` / result `{narrative_hint}` | identity（actor 必须存在），不产生数值/物品；由裁判确认后提交 |

- 四个类型均注册 `DEFAULT_REGISTRY`；只由新工具产生，旧场景永不产生 → 旧 golden 不漂移。
- 验证口径：`pytest tests/replay` 全绿且 golden 零 diff、`demo/console --check` 逐字不变、新增 reducer/事件单测；不执行 `--snapshot-update`。

### D-R7-2 `improvise`：首个 `agent_assisted` 工具，裁判生产可达（advisory only）

- `improvise` 声明 `AGENT_ASSISTED` → `registry.agent_assisted_tools()={"improvise"}` → `RuleEngine(agent_assisted_tools=...)` 自动 `needs_judge=True` → 编排层 `_collect_judgements` → `Resolver` needs_judge 分支（**无编排层改动**，既有链路生效）。
- 裁判仍 **advisory**：`verify_agent_override` 严格校验（verdict ∈ allow/deny/modify、置信 ≥0.7、理由非空、证据落在私有感知切片内），非法一律否；`deterministic_fallback` 恒保守——`improvise` 兜底文案「裁判不可用：这个临场动作没有被世界确认。」，其余工具保持原文案。
- **离线/裁判缺失 → 拒绝、零事件、版本不变**（不因为新增工具放宽离线行为）；每回合 ≤1 次裁判（既有约束）。
- 裁判输入：`_proposal_payload` 增 `action_text`（str，裁剪 200）原样进 payload；仍不传内部 ID；`prompt_version` 保持 `judge@v1`（system prompt 未改）。
- 既有 `test_judge_wiring.py` 的「生产 registry `agent_assisted_tools` 为空」断言按新事实上调：`build_game_tool_registry()` → `{"improvise"}`，`build_m2_tool_registry()` 仍为空。

### D-R7-3 分工具置信阈值 + 确认式澄清（候选不自动执行）

- 删除单一 `_CONFIDENCE_FLOOR=0.6`，改 `CONFIDENCE_FLOORS` 分工具阈值：`get_room_view/get_inventory/move_to 0.45`；`inspect_item/take_item/use_item/talk_to/drop_item/give_item 0.5`；`craft_item/attack/improvise 0.55/0.55/0.55`（`craft_item 0.6`）；未知/None → **0.6**。解析/编排两条链路统一用 `confidence_floor_for(tool)`。
- 低置信澄清：`tool is None` 时 `text=_UNKNOWN_ACTION_TEXT` **逐字不变**；仅补 `effects=[{"hint": 中文澄清句}]` + `suggested_actions=[1–2 条可点击动作句]`（仅在 `candidate_tool` 存在且能确定目标时；否则走既有 `_with_failure_hints`）。
- **候选不自动执行**：`candidate_tool/candidate_args` 只是给玩家的建议；澄清回合零写入。动作句按工具模板（`拿起{名}`/`使用{名}`/`细看{名}`/`向{方向}走`/`和{名}说话`/`攻击{名}`/`放下{名}`/`把{名}交给{人}`/`环顾四周`/`查看行囊`）用状态内展示名生成。

### D-R7-4 多步原子执行（≤3 步、整回合回滚、仅写动作）

- `ParseOutcome` 增 `steps`（0 或 2–3 步；1 步只填 `tool/args`）。`parse@v2`（Schema 新增 steps → `PROMPT_VERSION` 必须递增）；system prompt 增「多步 2–3、首步与 tool/args 相同、仅写操作、不确定省略」。
- 校验：每步 tool ∈ 白名单、args 过 `_args_are_sane`、总 JSON ≤1000 字符；任一不合格 → **丢弃 steps（按单步处理，不整句失败）**；`len(steps)>=2` 且首步与顶层一致才采纳。仅允许写工具（`side_effect=DESTRUCTIVE` 派生）。
- 确定性兜底解析器：词表加法（阅读/感官/战斗/放下/给予）不改旧输入映射；文本含分隔符/连接词时先拆 ≤3 段逐段单步解析——**全部段都成功且都是写工具**才返回多步，否则回退旧整句单步（旧行为逐字不变）。
- 编排：多角色链路为每步建玩家 Proposal（priority 100/99/98），**所有玩家提案必须 applied**，任一非 applied → 整回合失败（`INVALID_INPUT`/`CONFLICT` 按该 verdict）、零提交、审计 `status=aborted`，失败 hint 含「第 N 步没能完成。」；成功时草稿按步骤序合并后与 NPC 草稿一起提交，`action=第一步` 传给叙事器。单角色链路按步序 `gate.dispatch` + `evaluate_preconditions`，每步以 `working_state = state.apply(已收集草稿)` 重校验；任一步失败 → 整回合零提交；成功一次性提交全部草稿（含末尾环境伤害）。

### D-R7-5 实体名→ID 映射（未知仍拒绝）

- 新增 `resolve_entity_arg(kind, value, perception)`（精确 ID → 全名 → 名称片段别名 → difflib 模糊）与 `normalize_parse_outcome(outcome, perception)`：对 `item_id`/`npc_id`（含 steps 内）做映射；解析后、校验前在单/多角色两条链路调用。
- 映射只改善「名称→ID」，不改变可见性/授权：未知/越界值原样保留 → 交 gate 报 `NOT_FOUND`（失败零写入）。`orchestration/intent.py` 不 import `tools`；现有 15+ 解析单测与 7 条 golden 输入映射不变。

## 3. 工具与事件清单（本批交付）

- `FREEDOM_TOOL_NAMES = ("attack", "drop_item", "give_item", "improvise")`；`build_game_tool_registry()` 在 `craft_item` 后追加注册 → 工具数 **8 → 12**；`build_m2_tool_registry()` 保持 7 工具不变。
- 工具 Schema/副作用：`attack(npc_id)`、`drop_item(item_id)`、`give_item(item_id,npc_id)` = DESTRUCTIVE/DETERMINISTIC/`{player,system}`；`improvise(action_text 1..200)` = DESTRUCTIVE/**AGENT_ASSISTED**/`{player,system}`。函数体只声明（`@tool` + `_declarative()`），执行经 `ToolGate.dispatch` → `ActionService`。
- `ActionService` 新增写方法（返回草稿 Event，失败零事件）：`drop_item/give_item/attack/improvise`；`attack` 确定性判定：`seed=derive_seed(save_seed,turn_id,actor_id,"attack",0)`、`roll=DerivedRandom(seed).randint(1,20)`、`mod=(str-10)//2`、`dc=10`、`total=roll+mod`、`hit=roll==20 or (roll!=1 and total>=10)`、`damage=0 if not hit else max(1,3+mod)`，恒产一条 `COMBAT_RESOLVED`。
- `RuleEngine.PRECONDITION_TOOLS` 追加四工具 + 前置分支（`attack/drop_item/give_item` 不可见目标 → `NOT_FOUND`；`improvise` 参数非法 → `INVALID_INPUT`）。

## 4. 理由与被否选项

- 理由：用「工具 + 事件 + reducer + 前置/裁决」既有骨架加动作，不新架构、不碰唯一写入者；`improvise` 让已有裁判链路首次在生产可达（advisory，不写状态）；分阈值/多步/实体映射直接对应玩家反馈的三个卡点；纯加法保住 golden 与回放资产。
- 被否：① 不加 EventType、把新动作塞进 `IMPROVISED_ACTION`（破坏事件语义与回放可审计）；② 给 `improvise` 接自由制造式 LLM 生成数值（违反「数值由框架决定」红线）；③ 多步逐条独立提交（部分成功留下半回合状态，违反失败零写入）；④ 放宽 actor_scope/自动执行候选动作（安全边界不可降）。

## 5. 本轮未做（明确清单）

交易/经济系统、NPC 背包、潜行/仇恨、环境物联（点火/机关等需要新状态机的动作）、自由制造向新工具扩展、攻击的多回合战斗状态机（当前一次判定即结束遭遇）、`improvise` 的 `modify` verdict 落地（仍以 allow/deny 为主，modify 走既有保守路径）。 此外（验收审计如实登记）：**零事件只读回合 `narrative_source=None`（P0-4，Lead 裁决暂缓）**；**真实 LLM 下的 `parse@v2` steps 采纳率、裁判 allow 率、新事件 LLM 叙事事实核对未验证**（本批验收全部离线、零真实 LLM/零网络；成本/延迟沿用 R5 结论）。

## 6. 影响面与回写落点

- 实现（T1–T4 并行）：`tools/builtin/freedom.py`（新）、`domain/events.py`/`reducers.py`、`services/action_service.py`、`domain/rules/engine.py`、`agents/{dto,parser_agent,judge_agent}.py`、`orchestration/{intent,turn_orchestrator}.py`、`tools/gate.py`、`main.py`（无需改，自动派生 agent_assisted）。
- 测试：新增工具/事件/reducer/前置/裁决/解析/多步/实体映射/裁判接线用例；`test_judge_wiring.py` 断言更新；**旧 golden 零改动**。
- 文档：本 ADR、`docs/decisions/README.md` 索引、R7 进度文档、`PROGRESS.md`、`CHANGELOG.md`、开发文档第 07/08/09/12/17 章（`docs/开发文档.md` 重建）。
- 契约同步：工具白名单/`ToolSpec` 派生、解析 DTO、前端 `types.ts` 契约（若解析 DTO 暴露）与 `test_frontend_contract.py` 由实现 owner 评估；本批不新增 HTTP DTO 形状。

## 7. 已知问题归属 / 出口验收 / 复审

- **需要 Lead 决策**：多步执行在「第 N 步失败」时的失败文案是否需要步骤级详情（当前 hint 含序号）；`improvise` modify verdict 是否在后续批次启用。
- 出口验收（2026-11-12 最终实测，审计 [2026-11-12-r7-action-freedom-verification](../audits/2026-11-12-r7-action-freedom-verification.md)）：工具数 **12** 且 `build_m2_tool_registry()` **7** 不变；四事件 reducer 严格校验；`improvise` 裁判 allow/deny/缺失三态正确且零写入；分阈值/多步/实体映射用例全绿；全量 **2696 passed**（+175）、replay **69 passed**（golden 零漂移）、demo 7/7、console 4/4、`check_docs` PASS；T6 审计 11/11 探针 PASS（零真实 LLM/零网络）、六维自由度 **3.0→3.8/5**；T7 安全红队 51 例零真实缺陷（1 条低危加固已修）。
- 复审触发：用户要求交易/NPC 背包/战斗状态机；多步需要跨回合或非写动作；裁判从 advisory 升级为强制。
- 变更纪律：本 ADR 只增不改；推翻结论 = 新 ADR + `supersedes: D-R7-x`；新增 EventType 必须继续遵守 D-N6 受控流程。
