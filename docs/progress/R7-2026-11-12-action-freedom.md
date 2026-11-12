# R7 · 行动自由度批次（工具/事件扩张 + 解析与执行链改进）

> 编号：`R7`（范围外玩家体验批次，不占 001–028 与 029–060 编号；按 `docs/progress/README.md` 例外条款登记）
> 日期：2026-11-12（Asia/Shanghai）
> 前置：HEAD `269fc03`（R6 收口，CI 全绿）+ R7 未提交工作树。
> 依据：`.work/r7/contracts.md`（接口契约，不入库）、[ADR · R7 行动自由度（D-R7-1..5）](../decisions/2026-11-12-r7-action-freedom.md)、[验收与自由度复评审计](../audits/2026-11-12-r7-action-freedom-verification.md)、[R5 §4 遗留](../progress/R5-2026-11-12-aurora-glass-and-pitfalls-review.md)（0.6 阈值/实体映射项）。
> 批准：**D-N6 已获用户批准**——允许新增 `EventType` + 受控 golden 重基线；本轮按**纯加法**实现，目标旧 m2/m3/m4 golden **逐字不变（不重基线）**。
> 发布口径：并入 `CHANGELOG.md [0.4.0]` 未发布段；**不 bump 版本**（发布 checklist 未执行）。

## 1. 目标

针对「行动自由度受限」的三个卡点做纯加法扩张：

1. **动作面**：新增 `attack` / `drop_item` / `give_item` / `improvise` 四工具（工具数 8 → 12），配套 4 个新 `EventType` 与 reducer；`improvise` 作为首个 `agent_assisted` 工具，让已接线的裁判链路**生产可达**（advisory only，离线确定性兜底拒绝）。
2. **理解面**：删除单一 0.6 置信阈值，改**分工具阈值**；低置信澄清保留原文案并补 `effects.hint` + 可点击 `suggested_actions`（候选**不自动执行**）；实体名→ID 映射（未知仍 `NOT_FOUND`）。
3. **执行面**：一句多动作改为**≤3 步原子执行**（全部成功才提交，任一步失败整回合零写入）；`parse@v2` 的 `steps` 由白名单校验，不合法即降级为单步（旧行为逐字不变）。

红线不变：唯一写入者 `GameEngine`、CAS/幂等/审计、事件溯源、Resolver 纯函数、LLM/裁判不写状态、actor_scope 白名单只增不放宽、叙事事实白名单、旧 golden 逐字不变（出现漂移必须停止并报告 Lead，禁止 `--snapshot-update`/手改 golden）。

## 2. 关键 diff（按契约，实现由 T1–T4 并行）

| 模块 | 变更 |
| --- | --- |
| 工具层 | 新增 `tools/builtin/freedom.py`（`FREEDOM_TOOL_NAMES`，四工具声明式 `_declarative()`）；`build_game_tool_registry()` 在 `craft_item` 后追加注册；`build_m2_tool_registry()` **不动**；`tools/gate.py` 的 `ActionServicePort` + `_dispatch_write` 补四分支 |
| 事件/投影 | `domain/events.py` 新增 `ITEM_DROPPED`/`ITEM_GIVEN`/`COMBAT_RESOLVED`/`IMPROVISED_ACTION` 并注册 `DEFAULT_REGISTRY`；`domain/reducers.py` 四个严格类型 reducer（非法 `ValueError`），语义见 ADR D-R7-1 |
| 服务层 | `services/action_service.py` 新增 `drop_item/give_item/attack/improvise`（返回草稿事件；`attack` 确定性判定公式见契约；`improvise` result `narrative_hint=action_text[:120]`） |
| 规则层 | `RuleEngine.PRECONDITION_TOOLS` 追加四工具 + 前置分支（不可见/非法参数 → `NOT_FOUND`/`INVALID_INPUT`）；`improvise` 经 `agent_assisted_tools` 自动 `needs_judge=True`；`deterministic_fallback` 对 `improvise` 返回中文保守文案，其余不变 |
| 解析层 | `agents/dto.py`：`ParseStep`、`ParseOutcome.steps/candidate_tool/candidate_args`、`CONFIDENCE_FLOORS` + `confidence_floor_for`；`parser_agent.py`：`PROMPT_VERSION="parse@v2"`、steps 白名单/长度/字符校验与降级、分工具阈值、`_clarify` 带候选；`orchestration/intent.py`：词表加法（阅读/感官/战斗/放下/给予）、多步拆分（全成功且全写才采纳，否则回退旧单步）、`resolve_entity_arg`/`normalize_parse_outcome` |
| 编排层 | 两条链路改用 `confidence_floor_for`；解析后调用 `normalize_parse_outcome`；澄清补 hint/suggested（text 不变、候选不执行）；多步原子执行（多角色：每步 Proposal priority 100/99/98，全部 applied 才提交，否则整回合 `INVALID_INPUT`/`CONFLICT` + 审计 aborted；单角色：逐步 `working_state` 重校验、一次性提交）；只读回合 `narrative_source` 保持 H4-L3 语义（P0-4 经 Lead 裁决暂缓，零事件回合仍为 `None`）；唯一写入者/CAS/幂等/叙事器签名/API DTO 不改 |
| 裁判 | `agents/judge_agent.py::_proposal_payload` 增 `action_text`（裁剪 200）；`judge@v1` system prompt 不改；新增独立集成测试（Fake 裁判 allow/deny/低置信/None；离线拒绝零写入；每回合≤1 次；审计可查）；`test_judge_wiring.py` 生产 registry 断言更新为 `{"improvise"}`/m2 仍空 |

## 3. 测试证据（最终实跑，2026-11-12 稳定工作区）

> 权威验收审计：[docs/audits/2026-11-12-r7-action-freedom-verification.md](../audits/2026-11-12-r7-action-freedom-verification.md)（T6 验收与自由度复评；T7 安全红队；离线零真实 LLM/零网络）。

- **后端全量**：`uv run pytest -q` → **2696 passed in 93.20s**（R6 基线 2521 → **+175**）。
- **golden 回放**：`uv run pytest tests/replay -q` → **69 passed**（m2/m3/m4 golden **逐字不变、零重基线**）。
- **新增测试分布**：T1 动作包 **39**、T2 解析层（`test_intent_multi_step`/`test_intent_entity_mapping`/parser 更新）、T3 编排 **11**、T4 裁判 **8**、T7 安全红队 **51**；**T7 红队零真实缺陷**，仅 1 条低危加固已修（`ReducerRegistry.get` 对非 `EventType` 输入统一抛 `ValueError`）。
- **静态门**：`ruff format --check .` → **288 files** already formatted；`ruff check .` → All checks passed；`mypy src/textgame` → Success **120 files**；`lint-imports` → **2 kept / 0 broken**；`uv run python scripts/check_docs.py` → **PASS**。
- **确定性基线**：`python -m textgame.demo --check` → **7/7**；`python -m textgame.console --check` → **4/4**；旧 m2/m3/m4 golden 零改动（无 `--snapshot-update`、golden 文件无 git 变更）。
- **T6 验收审计结论**：**11/11 探针 PASS**（P02 澄清给候选且零写、P03 两事件按序、P13c conf 0.55 社交成功、P05 名称映射成功/未知零写、P13a 战斗移除 NPC、drop/give 真实易手、improvise 离线拒绝+裁判 allow 提交、多步第 2 步失败整回合零写）；**零真实 LLM、零网络**。
- **六维自由度复评（R5 → R7）**：表达 3→**3.5**、行动 2→**4**、叙事 4、世界响应 3→**3.5**、公平 4、失败优雅 4；**综合 3.0→3.8/5**（(3.5+4+4+3.5+4+4)/6=3.83）。
- **兼容性/前端**：本批无 HTTP DTO 形状变更；浏览器 UI/多步展示由前端批次或 Lead e2e 覆盖（本批未跑浏览器 e2e，审计 §6 已登记局限）。
## 4. 护栏检查

- [x] 唯一写入者 `GameEngine`；Resolver 纯函数；失败零写入（多步任一步失败整回合回滚）。
- [x] LLM/裁判不写状态；`improvise` advisory only，离线/裁判缺失确定性拒绝。
- [x] 未放宽 actor_scope/白名单；`build_m2_tool_registry()` 不变（7 工具）。
- [x] 未改 `Event` 基线字段；新 EventType 只由新工具产生；旧 golden/ demo/console 逐字不变。
- [x] 无新增依赖；真实网络禁用（测试用 `DisabledLLMClient`/`FakeLLM`/`DeterministicJudgeAgent`）。
- [x] 文档不写未实现能力/真实 LLM 调用（本批全部离线验收；测试证据已回填 §3）。

## 5. 遗留与后续

1. **已收口**：T1–T4 实现、T6 验收审计（11/11 探针）、T7 安全红队（51 例、零真实缺陷）、T8 独立复核（PASS、全量门禁复跑一致）；最终数字见 §3。
2. **真实 LLM 路径未验证（P1 条件项）**：`parse@v2` steps 采纳率、裁判 allow 率/延迟/成本、新事件 LLM 叙事事实核对均未跑真实模型（按纪律本批零真实 LLM/零网络，≤50 调用预算留给后续批次）。
3. **单角色链路边界**：单角色多步失败分支无专门单测（T8 独立探针已验证零写入，建议后续补 2–3 例）；单角色链路不消费 `needs_judge`（仅自定义单角色装配可达，生产 `main.py` 恒多角色、不可达）。
4. **确定性取舍**：`attack` 派生种子固定 `("attack", 0)`，同回合内两次 `attack` 掷骰相同（可复现、契约如此）；`ITEM_GIVEN` 后 `state.items` 保留但无归属（NPC 无背包模型）。
5. **本轮未做**：交易/经济、NPC 背包、潜行/仇恨、环境物联（点火/机关）、自由制造向新工具扩展、多回合战斗状态机、P0-4（零事件回合来源徽章口径）。
6. **已裁决/未启用**：多步失败 hint 已带「第 N 步没能完成」；`improvise` 的 `modify` verdict 未启用（仍按既有保守路径处理）。
7. **R5 §4 关联项**：第 16 条（0.6 单阈值）由 D-R7-3 收口；第 17 条（参数名≠实体 ID）由 D-R7-5 缓解（未知仍拒绝）。
