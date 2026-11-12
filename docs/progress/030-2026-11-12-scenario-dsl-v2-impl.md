# 030 · 2026-11-12 · M6 后 N1 内容轨 · scenario-dsl-v2-impl

> 切片目标一句话：`domain/user_scenario.py` 落地 `schema_version=2`（v1 逐字兼容、v2 白名单条件/flags/多结局语义、结构化错误），并补 ≥25 单测与 v2 示例/集成测试 ｜ 验收命令：`pytest -q tests/unit/test_user_scenario.py tests/unit/test_scenario_runtime.py tests/unit/test_scenario_service.py tests/integration/test_scenario_api.py tests/integration/test_user_scenario_flow.py` ｜ 里程碑 M6 后 N1 ｜ 编号区间 029–036 ｜ 执行/验收 dsl-v2（Lead 终审）

## 1. 目标与验收命令
- 目标（一个垂直小功能）：v2 条件 DSL（9 个白名单原子 + flags 派生命题 + 目标进度链）+ v1→v2 版本兼容 + 结构化错误 + 多结局语义锁定；**零引擎改动**（不新增 EventType、不改 GameState/reducer/golden、API 形状不变）。
- 验收命令/断言（先定后写）：
  - `.venv\Scripts\python.exe -m pytest -q <5 个目标测试文件>` → **132 passed**
  - `.venv\Scripts\python.exe -m ruff format --check <变更 py>` → 3 files already formatted
  - `.venv\Scripts\python.exe -m ruff check <变更 py>` → All checks passed
  - `.venv\Scripts\python.exe -m mypy src/textgame` → 本切片文件 Success；全量还剩 2 个错误在并行 N2 流式切片的 `src/textgame/llm/litellm_client.py`（非本切片文件，Lead 收敛后统一跑）
  - `pytest -q tests/replay` → **69 passed**（旧 golden 零改动）
- 不做什么：不改 `api/schemas.py`/`web/`（另一队友在用）；不改 v1 旧测试断言；不跑全量 pytest（Lead 负责）；不新增依赖。

## 2. 改动与提交
- 文件（≤8）：
  - `src/textgame/domain/user_scenario.py`（v2 模型/求值/校验）
  - `tests/unit/test_user_scenario.py`（追加 v2 单测：29 → 66 个测试函数 / 74 collected，新增 37 函数 + 45 case）
  - `tests/integration/test_user_scenario_flow.py`（追加 3 个 v2 端到端/示例/版本门禁用例）
  - `examples/scenarios/lantern-vigil.json`（新增 v2 可玩示例：3 房间/5 flags/3 目标/3 结局）
  - `docs/design/scenario-dsl-v2.md`、`docs/decisions/2026-11-12-scenario-dsl-v2.md`、`docs/progress/029·030`（设计/决策/进度）
- 提交：未提交（按约定留工作区，由 Lead 统一提交）
- 关键 diff 摘要：`Condition` 增 `flag/objective_done/has_item_tag_count/item_in_room/npc_in_room/turn_*/world_time_*`；`FlagSpec`+`ScenarioDefinition.flags`（≤16）；`evaluate_condition(..., definition=None)` 可选上下文 + flag/目标 memo/环 fail-closed；版本门禁（v1 声明禁用 v2 特性）；统一依赖图环检测；未知谓词中文消息 + 精确 JSON 路径；未引用 flag warning；`UserScenarioHooks` 构造期缓存 `_condition_env`（goal/ending 走同一求值器）。
- 护栏检查：未新增 EventType / 未改 GameState/reducer/golden / 未改 API DTO 与 summary 6 键 / 用户 JSON 不执行（白名单+extra=forbid）/ v1 旧测试零改动 / 无密钥入库。
- 契约变更：无（对外 API 形状不变；v2 语义为加法，见 ADR D-N1-1 / 设计文档 §4.3）。

## 3. 验证结果
- lint / type：`ruff format --check` 3 files already formatted；`ruff check` All checks passed；`mypy src/textgame/domain/user_scenario.py` Success（全量 mypy 的 2 个错误属并行切片在途文件，见第 1 节）。
- 单测：`tests/unit/test_user_scenario.py` → passed **74** / failed 0；目标 5 文件合计 passed **132** / failed 0 / skipped 0。
- 回放/集成（涉及状态时）：`tests/replay` **69 passed**（旧 golden 逐字不变）；`tests/integration/test_user_scenario_flow.py` **10 passed**，其中 v2 端到端「导入 lantern-vigil → 开局 → 对话/移动/拿取 5 回合 → 目标链全完成 → 胜利结局 → 结局后新回合 INVALID_INPUT」真实 HTTP 链路通过。
- demo：本切片不触内置 4 场景（`UserScenarioHooks` 仅自定义场景注入），demo/console 不在本切片验收范围（Lead 七门统一跑）。
- 失败与回滚：无。

## 4. 下一步与风险
- 下一步：031 · scenario-editor（只产出 JSON + 实时 `/scenarios/validate` + flags/schema_version 表单，不新增响应字段），验收命令 `<vitest 新增 + e2e 编辑→校验→开局>`
- 风险/开放问题：① 历史类条件（曾进入/一次性/限时）不可表达，归属 033–034（D-N6，需用户拍板）；② `docs/PROGRESS.md` §3/§4、ADR 索引行、v1 设计文档 §6 回填由 Lead 同批（本切片禁改）；③ `npc_in_room` 在当前玩法下等价常量（无 NPC 移动事件），设计文档 §3.3 已注明。
- 需要人工决策：无（本轮）；下一步人工决策点是 033–034 是否新增 EventType 与受控 golden 重基线。
