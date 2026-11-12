# R7 行动自由度批次验收与自由度复评审计（2026-11-12）

- 审查日期：2026-11-12（Asia/Shanghai）
- 审查基线：HEAD=`269fc03`（R6 收口）+ **R7 未提交工作树**（T1–T4 全部落地，Lead 稳定工作区）
- 审查范围：R7「行动自由度」批次（D-N6 用户批准新增 EventType）的实际行为与自由度变化
- 审查方式：**离线确定性装配实跑**（`DisabledLLMClient`/`FakeLLM`/`DeterministicJudgeAgent`，零真实 LLM、零网络）+ 测试/门禁复跑 + 静态代码核对
- 证据：`.work/r7/t6_verify.py`（11 项探针复跑脚本，启动即封禁 `socket.connect`）→ `.work/r7/t6-evidence.json`（逐项原始结果）；测试证据见 §4
- 关联：R5 自由度审计 `docs/audits/2026-11-12-llm-participation-freedom.md`；R7 ADR `docs/decisions/2026-11-12-r7-action-freedom.md`（D-R7-1..5）；R7 进度 `docs/progress/R7-2026-11-12-action-freedom.md`；契约 `.work/r7/contracts.md`（不入库）
- 声明：**本审计没有发起任何真实 LLM 调用、没有联网、没有改产品代码/测试代码、没有改 golden**；发现项只报告 Lead。

---

## 0. 结论摘要

**R7 达成批次目标：行动自由度从 2/5 提升到 4/5，表达自由度从 3/5 提升到 3.5/5，六维综合从 3.0/5 提升到 3.8/5，其余维度不下降。** 11 项探针复跑 11/11 通过（离线、固定 seed、零网络）；`pytest tests/replay` 69 passed 证明 m2/m3/m4 golden 零漂移；全量 `pytest -q` 2643 passed；`demo --check` 7/7、`console --check` 4/4 逐字不变。

关键数字：

| 项 | R5（批次前） | R7（本批后） | 证据 |
| --- | --- | --- | --- |
| 玩家动作工具数 | 8 | **12**（+ `attack/drop_item/give_item/improvise`） | `src/textgame/tools/builtin/freedom.py:24,38-73`；`tools/builtin/crafting.py:19,53-66` |
| 一条输入可执行动作数 | 1（组合丢弃） | **≤3 步原子执行**（全成功才提交） | `orchestration/turn_orchestrator.py:570-608,1303-1449,1546-1900` |
| 低置信澄清 | 只有通用失败文案 | **确认式澄清：中文 hint + 1–2 条可点击候选**（候选不执行、零写入） | `turn_orchestrator.py:590-658`；`t6-evidence.json` P02 |
| 实体名当 ID | `NOT_FOUND` | **展示名/别名→真实 ID 映射**（未知仍拒绝） | `orchestration/intent.py:576-640`；`turn_orchestrator.py:1149,1546` |
| 战斗/丢弃/给予 | 无工具 | **`COMBAT_RESOLVED`/`ITEM_DROPPED`/`ITEM_GIVEN` 真实改状态** | `domain/events.py:90-93`；`domain/reducers.py:355+`；`services/action_service.py:275,305,340` |
| 临场动作（规则外） | 直接拒绝 | **`improvise` + 裁判生产可达**（离线保守拒绝/allow 才提交） | `tools/builtin/freedom.py:68-73`；`agents/judge_agent.py:236-237`；`main.py:143` |
| 全量测试 | 2521 passed（R6） | **2643 passed**（Lead 实测，本审计复跑一致） | §4 |
| golden 回放 | 69 passed | **69 passed（零漂移）** | §4 |

---

## 1. 审查方法与环境

### 1.1 离线确定性装配（与 `main.py` 同构）

`.work/r7/t6_verify.py` 每个用例都新建一套内存装配：`InMemoryRepository` + 真实 `GameEngine`/`QueryService`/`ActionService`/`ToolGate`/`Resolver`/`AuditService` + 生产 `build_game_tool_registry()`（12 工具）+ `RuleEngine(agent_assisted_tools=registry.agent_assisted_tools())`（`improvise` → `needs_judge`）+ `TemplateNarrator` + 固定 `save_seed="save-42"`；解析侧按用例注入：

- `DisabledLLMClient` + `DeterministicIntentParser`（生产离线路径，真实关键词/多步解析）；
- `FakeLLM`（返回预置 `ParseOutput` JSON，仅模拟 LLM 解析产物，零网络）；
- `_StubParser`（直接构造 `ParseOutcome`，覆盖 `improvise`/多步失败等解析层不易触发的分支）；
- 裁判 `DeterministicJudgeAgent`（固定 `JudgeOutput`；`None` = 裁判缺失）。
- 叙事 `TemplateNarrator`（离线确定性；本批不调真实 LLM）。

### 1.2 零真实 LLM / 零网络证明

- 脚本启动即把 `socket.socket.connect` 替换为抛 `RuntimeError` 的守卫；11 个用例全程未触发（若任何链路尝试联网，脚本会直接失败）。
- 所有客户端均为 `DisabledLLMClient`/`FakeLLM`（`textgame.llm.client.DisabledLLMClient` 返回即时 `LLM_UNAVAILABLE`，`tests/conftest.py` 另有 autouse 禁网守卫）。
- 真实 API 调用次数：**0**（本审计未使用 `.env` key，未启动 uvicorn，未 import litellm 网络路径）。

### 1.3 复现命令

```bash
# 11 项探针离线复跑（零网络；输出 .work/r7/t6-evidence.json）
uv run python .work/r7/t6_verify.py

# 必附：golden 零漂移 / 确定性输出
uv run pytest tests/replay -q                     # 69 passed
uv run python -m textgame.demo --check            # 7/7 回合
uv run python -m textgame.console --check         # 4/4 回合

# R7 相关用例（T1/T2/T3/T4）
uv run pytest tests/unit/test_freedom_actions.py tests/unit/test_parser_agent.py \
  tests/unit/test_intent_multi_step.py tests/unit/test_intent_entity_mapping.py \
  tests/integration/test_orchestrator_action_freedom.py tests/integration/test_judge_improvise.py -q

# 全量
uv run pytest -q                                  # 2643 passed
```

---

## 2. 受损探针逐项复跑结果（11/11 PASS）

> 数据来自 `.work/r7/t6-evidence.json`（脚本 stdout 摘要 + JSON 原始记录）。「零写入」= version / 事件数 / state_hash / last_seq 四元组不变。

| # | 探针（R5 受损点） | 输入/装配 | 实测结果 | 判定 |
| --- | --- | --- | --- | --- |
| 1 | **P02 纯扮演 → 澄清候选** | FakeLLM 返回 `get_room_view` conf=0.0（模拟 R5 真实 LLM 低置信候选） | `ok=false`、`INVALID_INPUT`、**text 仍为 `_UNKNOWN_ACTION_TEXT` 逐字 61 字**；`effects.hint=「你是想环顾四周吗？如果是，直接说「环顾四周」就可以。」`；`suggested_actions=["环顾四周","查看行囊"]`；**零写入** | ✅ 达成 |
| 2 | **P03 多步** | 生产确定性解析「拿起火把，然后向北走」 | `ok=true`；事件顺序 **`ITEM_TAKEN` → `CHARACTER_MOVED`**（2 条一次提交，version+1）；屏文本 81 字、`narrative_source=template`；玩家到地窖 | ✅ 达成 |
| 3 | **P13c 社交 conf=0.55** | FakeLLM 返回 `talk_to(npc_id="老哨兵", topic=...)` conf=0.55（旧全局 0.6 会澄清） | `ok=true`；`DIALOGUE_EXCHANGED` 1 条提交；屏文本 82 字；version+1 → **分工具阈值 0.5 生效** | ✅ 达成 |
| 4 | **P05 名称→ID（正例）** | FakeLLM 返回 `take_item(item_id="药水")` conf=0.95（展示名非 ID） | `ok=true`；`ITEM_TAKEN` 事件的 `params.item_id` = 真实 `item_01J9Z3K4Q7X8Y9Z0ABCDEFGHJR`（治疗药水）；屏文本 85 字 | ✅ 达成 |
| 5 | **P05 名称→ID（对照：未知名）** | FakeLLM 返回 `take_item(item_id="火盆")`（场景不存在） | `ok=false`、`INVALID_INPUT`、**零写入**、通用失败提示 + 建议动作 → 不猜实体、不放宽可见性 | ✅ 安全边界保持 |
| 6 | **P13a 战斗** | 生产确定性解析「攻击老哨兵」 | `ok=true`；`COMBAT_RESOLVED`；`hit=true, roll=11, total=12, dc=10, damage=4`；**NPC 从 `state.npcs` 与房间 `npc_ids` 移除**；屏文本 85 字 | ✅ 达成 |
| 7 | **drop_item 真实易手** | 先「拿起火把」，再「放下火把」 | `ok=true`；`ITEM_DROPPED` 1 条；火把从玩家 inventory 移除、回到当前房间 `item_ids`；version+1 | ✅ 达成 |
| 8 | **give_item 真实易手** | 先「拿起治疗药水」，再「把治疗药水交给老哨兵」 | `ok=true`；`ITEM_GIVEN` 1 条；药水离开玩家 inventory；`result.npc_name` 记录接收者 | ✅ 达成 |
| 9 | **improvise 离线拒绝** | StubParser → `improvise(action_text=...)`，`judge=None` | `ok=false`、`INVALID_INPUT`、**零写入**；审计 `rejected`；兜底文案「裁判不可用：这个临场动作没有被世界确认。」；`registry.agent_assisted_tools()=={"improvise"}` 证明生产可达 | ✅ 达成 |
| 10 | **improvise + 裁判 allow** | 同上，`DeterministicJudgeAgent(allow, conf=0.9, reason 非空, evidence=「火把」)` | `ok=true`；`IMPROVISED_ACTION` 1 条（`narrative_hint`=action_text[:120]）；裁判恰好 1 次调用；审计 `applied`；无任何数值/物品写入 | ✅ 达成 |
| 11 | **多步第 2 步失败** | StubParser → steps=[拿火把, 拿地窖的锈剑] | 第 1 步合法也不提交：`ok=false`、`INVALID_INPUT`、**零写入**；`effects.hint` 含 **「第 2 步没能完成。」** | ✅ 达成 |

补充观察（同批证据）：

- 新事件（4 类）在 `TemplateNarrator` 下均能产出 **80–150 字**屏文本（实测 81–85 字），未出现空屏/超长/内部 ID。
- 失败回合文案均为中文世界内语言，无 error_code/英文诊断/内部 ULID 直出（沿用 R5 失败提示链，未回归）。
- 攻击判定确定性：`DerivedRandom.derive_seed(save_seed, turn_id, actor_id, "attack", 0)`（`services/action_service.py:340-387`），同 seed/回合/角色必得同 `roll/total/damage`；本审计固定 seed 下 `roll=11,total=12,hit,damage=4`，可由 T1 单测 `test_action_service_attack_is_deterministic_for_same_seed`（`tests/unit/test_freedom_actions.py:447`）复核。

---

## 3. 六维自由度复评（R5 → R7）

> 评分口径与 R5 审计一致（1–5 分；允许 0.5 分粒度）。「依据」均来自 §2 实测或 §4 测试/门禁；「剩余缺口」进入 §5 遗留。

| 维度 | R5 | R7 | 变化 | 依据 | 剩余缺口 |
| --- | --- | --- | --- | --- | --- |
| **表达自由** | 3 | **3.5** | +0.5 | ① 一句多动作可执行（P03：2 事件原子提交）；② 低置信时给**可点击候选**而非通用失败（P02：hint+建议、text 不变、零写）；③ 玩家可用展示名说话（P05「药水」→真实 ID）；④ 确定性解析新增阅读/感官/战斗/放下/给予词表（`orchestration/intent.py`）；⑤ 分工具阈值让合法社交 0.55 通过（P13c） | 纯扮演仍不执行（只给候选建议）；无零事件表达工具（emote/shout）；多语言/Long 输入依赖真实 LLM（本批未测）；候选动作不自动执行（产品选择） |
| **行动自由** | 2 | **4** | **+2** | ① 工具 8→12：`attack/drop_item/give_item/improvise`（`tools/builtin/freedom.py:24,38-73`）；② 战斗产生 `COMBAT_RESOLVED` 并真实改世界（P13a：NPC 移除）；③ 丢弃/给予真实易手（P13a 同批 drop/give 证据）；④ 多步 ≤3 步原子执行（P03）；⑤ 临场动作有裁判通道（improvise allow 提交/离线拒绝）；⑥ 事件/reducer/前置/裁决链路完整接入（`domain/events.py:90-93`、`reducers.py:355+`、`rules/engine.py:34-55,161-181`） | 无交易/经济系统；NPC 无背包（give 后物品离开世界，不可再交互）；战斗是「一次判定即结束遭遇」，无玩家受伤/多回合/仇恨；无潜行、环境物联（点火/机关）；多步仅写动作且目标需初始感知可见或已知 ID |
| **叙事自由** | 4 | **4** | 0（不下降） | 新事件 4 类离线模板叙事均 80–150 字、无内部 ID（§2）；既有叙事链/`narrative_source` 未改（P0-4 本批作废、`turn_orchestrator.py:1242,1859` 保持 None）；replay/demo/console 逐字不变 | **新 EventType 的真实 LLM 叙事质量/事实核对未验证**（R7 只测离线模板；真实 LLM 需另批）；玩家原话仍不进叙事（R5 P0-3 未做） |
| **世界响应** | 3 | **3.5** | +0.5 | 攻击改变 NPC 存在（房间 `npc_ids`/`state.npcs`）；drop 改变房间物品；give 改变持有关系；improvise 经裁判确认后产生可审计事件；这些都在 `/replay` 事件流中可查 | NPC 自主行动仍仅 m3（未变）；NPC 对话仍确定性回复；交易/多轮战斗/环境机关无响应；用户上传场景对新工具的兼容未验证 |
| **公平可复现** | 4 | **4** | 0（不下降） | 离线全链路确定性：11/11 复跑结果稳定、固定 seed；attack 派生种子确定；全部失败/拒绝零写入（§2 五项）；`pytest tests/replay` 69 passed、golden 零漂移；全量 2643 passed | 真实 LLM 裁判/解析路径随模型版本非确定（设计上可接受，但线上「同一句能不能做」仍受模型影响）；本批未做真实 LLM 回归 |
| **失败优雅度** | 4 | **4** | 0（不下降） | 多步失败 hint 精确到步「第 N 步没能完成。」；improvise 离线兜底为世界内中文；未知名称/越权/非法参数仍零写入 + 可读提示；失败屏不泄漏 error_code/内部 ID（既有链未改） | 失败屏仍为短句（16 字）+ hint 拼接，与第 2 章「每屏 80–150 字」口径张力（R5 已登记）；步骤级失败只给序号、不解释具体哪一步为何失败（可后续增强） |

**综合**：R5 = 3.0/5（叙事自由型+动作受限型）→ **R7 = 3.8/5（中等偏上；表达/行动明显改善，可信维度保持）**。计算：(3.5+4+4+3.5+4+4)/6 = 3.83。**批次目标「行动 2→3.5–4、表达 3→3.5–4、其余不下降」全部满足。**

---

## 4. 测试与门禁证据（本审计复跑，2026-11-12）

> 注：本审计运行于 T7 红队与低危加固之前；T7 后最终门禁为全量 **2696 passed**、`ruff format --check` **288 files**、`mypy src/textgame` **120 files**、`tests/replay` **69 passed**、`check_docs` PASS（数字以 R7 进度文档/CHANGELOG 为准，六维评分不变；本表内数字保留本审计运行时点口径）。

| 项 | 命令 | 结果 |
| --- | --- | --- |
| 全量测试 | `uv run pytest -q` | **2643 passed**（87.80s；R6 基线 2521 → +122） |
| golden 回放 | `uv run pytest tests/replay -q` | **69 passed**；`tests/fixtures`/`tests/replay` 无改动（`git status` 无 golden 变更） |
| R7 聚焦用例 | `tests/unit/test_freedom_actions.py`(37) + `test_parser_agent.py`+`test_intent_multi_step.py`+`test_intent_entity_mapping.py`(86) + `tests/integration/test_orchestrator_action_freedom.py`(11) + `tests/integration/test_judge_improvise.py`(8) | **142 passed** |
| demo 逐字 | `uv run python -m textgame.demo --check` | **7/7 回合**，屏字数/确定性断言通过 |
| console 逐字 | `uv run python -m textgame.console --check` | **4/4 回合**（含失败样本） |
| 静态门 | `ruff format --check .`（287 files）/ `ruff check .` / `mypy src/textgame`（120 files）/ `lint-imports`（2 kept, 0 broken）/ `check_docs` | 全 PASS（Lead 实测；本审计仅重跑与自由度相关的 pytest/demo/console） |
| 离线证据脚本 | `uv run python .work/r7/t6_verify.py` | **11/11 PASS**，0 真实 LLM、0 联网（socket 守卫） |

> 说明：`.work/r7/t6_verify.py` 与 `t6-evidence.json` 在 `.gitignore` 的 `.work/` 下（不入库）；复现命令见 §1.3。产品代码/测试代码/golden 本审计均未修改。

---

## 5. 新遗留与未做项（如实登记）

1. **交易/经济未做**：`give_item` 只转移物品，没有货币/交易工具；NPC 无背包模型（给予后物品离开玩家持有但 NPC 不"拥有"它，不可再取回）——ADR D-R7-5 已明确列为未做。
2. **战斗是极简遭遇**：一次判定即结束（命中移除 NPC、未命中状态不变），无玩家受伤/HP 结算/多回合/仇恨/逃跑；`attack` 对 DESTRUCTIVE 白名单外 NPC 无法使用（actor_scope 未放宽）。
3. **多步限制**：≤3 步、仅写工具、`improvise` 不参与多步（T2 收紧避免一步多次裁判）；跨房间目标需在初始感知可见或已知道具 ID，否则第 2 步会因 `NOT_FOUND` 整回合失败。
4. **真实 LLM 路径未验证**：本批验收全部离线（裁判 `DeterministicJudgeAgent`、解析 `DisabledLLMClient`/`FakeLLM`）。真实 LLM 下 `parse@v2` 的 steps 采纳率、裁判 allow 率/延迟/成本、新事件 LLM 叙事事实核对，均为后续批次复评项（成本/延迟预算沿用 R5 结论，无新数据）。
5. **P0-4 未做（Lead 裁决暂缓）**：零事件只读回合 `narrative_source` 仍为 `None`（H4-L3 口径，`turn_orchestrator.py:1242,1859` 未改、`test_world_book_flow.py` 未动）；前端来源徽章仍会显示「来源未知」——R5 §6 P0-4 留作后续。
6. **R5 §6 其他未做项**：`candidate_narrative` 仍未接入叙事/合并调用（P0-3）；自由制造 pre-queue 已由 R6 完成（R-19 关闭）但与新工具无交互；世界书采用策略（P1-3）、narrate 长度修复前置（P1-4）、失败屏文案长度（P1-2 部分：多步步骤提示已做、失败屏 80–150 未做）。
7. **兼容性未验证**：用户上传场景/人物卡/世界书与新工具（attack/drop/give/improvise）的兼容（如自定义场景 NPC 能否被攻击、场景 items 的 drop/give 约束）——本批无专项测试。
8. **前端 UI 未跑**：多步一次提交在 UI 上表现为一条回复（事件 2–3 条），建议 chips/行动面板未验证；由前端批次/Lead e2e 覆盖。
9. **`improvise` modify verdict**：`RuleEngine.verify_agent_override` 仍以 allow/deny 为主（`modify` 走既有保守路径），ADR 已登记待后续。
10. **世界书 / m3 NPC 与新工具**：m3 NPC 的 actor_scope 未获得新工具权限（保持最小授权），NPC 不会 attack/drop/give；m3 多角色 + 多步组合未做端到端（T3 单/多链路均测，但 m3 NPC 同回合 + 玩家多步无专项用例）。

---

## 6. 证据局限

1. **离线样本**：11 项复跑用固定 seed=42、m2 场景为主（战斗/丢弃/给予/临场动作均在 m2 入口）；未覆盖 story/m3/m4/custom 场景的新动作（除 T1 单测的事件/reducer 层）。
2. **FakeLLM 不是真实模型**：P02/P05/P13c 的低置信候选/名称映射用预置 JSON 驱动，验证的是「解析层输出→编排层行为」，不代表真实 LLM 的置信分布/语言覆盖。
3. **单次观测**：每项探针 1 次；攻击结果（roll=11 命中）是固定 seed 下的确定值，换 seed 会得到不同 hit/damage（属设计内确定性）。
4. **无真实 LLM / 无网络 / 无 UI**：真实调用 = 0；网页与终端 UI 只做 `--check` 逐字验证，未跑浏览器 e2e；真实延迟/成本无新增数据（R5 数字仍适用）。
5. **`.work` 不入库**：证据脚本与 JSON 被 `.gitignore` 覆盖；复现依赖工作区（命令见 §1.3），文档中的原始数字来自本次运行。

---

## 7. 结论（可直接引用）

1. **「R7 批次把玩家动作自由度从 2/5 提升到 4/5：工具 8→12（攻击/丢弃/给予/临场动作）、一条输入最多 3 步原子执行、低置信可点击澄清、实体名→ID 映射；六维综合从 3.0/5 到 3.8/5，可信维度（叙事 4/公平 4/失败 4）不下降。」**
2. **「11 项 R5 受损探针离线复跑 11/11 通过：P02 澄清给候选且零写、P03 两事件按序、P13c conf 0.55 社交成功、P05 名称映射成功/未知零写、P13a 战斗移除 NPC、drop/give 真实易手、improvise 离线拒绝+裁判 allow 提交、多步第 2 步失败整回合零写；零真实 LLM、零网络、无 golden 漂移（replay 69 passed，全量 2643 passed，demo 7/7、console 4/4）。」**
3. **「自由度的下一批瓶颈已从『动作词汇表』转移到『内容/规则深度』：交易/经济、NPC 背包、多轮战斗、环境物联、以及真实 LLM 解析/裁判的稳定性是新遗留（详见 §5），本批未做且不应宣传为已支持。」**
