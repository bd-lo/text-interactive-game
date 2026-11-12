# 029 · 2026-11-12 · M6 后 N1 内容轨 · scenario-dsl-v2-design

> 切片目标一句话：为「场景 DSL v2」定稿白名单条件扩展、flags 派生命题、多结局选择语义、schema v2 字段与 v1→v2 兼容矩阵，并产 ADR D-N1-1 ｜ 验收命令：设计评审 + `docs/decisions` 格式 + 030 实现逐条对照 ｜ 里程碑 M6 后 N1 ｜ 编号区间 029–036 ｜ 执行/验收 dsl-v2（Lead 终审）

## 1. 目标与验收命令
- 目标（一个垂直小功能）：只交**设计**——`docs/design/scenario-dsl-v2.md` + `docs/decisions/2026-11-12-scenario-dsl-v2.md`（D-N1-1），给 030 实现与 031 编辑器/032 分享/033–034 模组提供权威边界；本切片不改代码。
- 验收命令/断言（先定后写）：
  - 设计评审（白名单谓词、无 eval/脚本、静态可校验、确定性回放）→ `scenario-dsl-v2.md` §2.4/§3/§8 逐条可核对
  - `ADR D-N1-1` 格式 → 状态/关联/背景/决策/被否选项/影响面/回写落点/验收断言/变更纪律 齐全
  - 与 D-N5/D-N6/D-N7 一致性 → 「历史类条件」明确归 033–034（需用户拍板），其余不做
  - 本切片不动代码；v1 场景目标测试基线 84 passed（设计冻结时的仓库事实）
- 不做什么：不实现 DSL（归 030）；不做编辑器/分享/跑团模组；不新增 `EventType`/`GameState` 字段；不改 v1 设计文档正文（回填由 Lead 同批）。

## 2. 改动与提交
- 文件：`docs/design/scenario-dsl-v2.md`（新增）、`docs/decisions/2026-11-12-scenario-dsl-v2.md`（新增）
- 提交：未提交（按任务约定，工作区改动由 Lead 统一提交）
- 关键 diff 摘要：v2 白名单 9 个新原子（flag/objective_done/has_item_tag_count/item_in_room/npc_in_room/turn_*/world_time_*）+ 组合子复用；`flags[]` 派生命题（≤16、when 必填、统一依赖图环检测 + 求值 memo）；schema v2 只加 `flags`、summary 6 键/DTO 不变；多结局冻结「priority 降序→声明序→首个满足→兜底两级、读时视图、结局后拒回合、玩家选择只由状态差异表达」；v1→v2 兼容矩阵 10 行。
- 护栏检查：纯文档；未改 domain/Event/契约；未新增依赖；无密钥入库。
- 契约变更：无（v1 契约不变，v2 为加法；对外 API 形状不变，见设计文档 §4.3）。

## 3. 验证结果
- lint / type：本切片无代码变更，不触发代码门禁（030 已跑变更文件门禁）
- 单测：设计切片不新增代码；设计冻结时 v1 场景目标测试基线 `84 passed`（030 实施后同口径变 `132 passed`，见 030）
- 回放/集成（涉及状态时）：本切片不改状态/事件；设计文档明确「v2 不改 `GameState`/reducer/golden」
- demo：不涉及
- 失败与回滚：无

## 4. 下一步与风险
- 下一步：030 · scenario-dsl-v2-impl，验收命令 `pytest -q tests/unit/test_user_scenario.py tests/integration/test_scenario_api.py ...`（见 030 文档）
- 风险/开放问题：① 历史/首次/一次性条件不可表达，归 033–034（D-N6，需用户拍板新增 EventType）；② v1 设计文档 §6 v2 回填、`docs/PROGRESS.md` §3/§4 与 ADR 索引行由 Lead 同批（029 不改这些禁改文件）；③ 031 编辑器须按 §4.3 不新增响应字段。
- 需要人工决策：无（本轮）；033–034 是否新增 EventType 需用户另行拍板。
