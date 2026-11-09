# 016 · 2026-11-09 · M4 制造 · deterministic-craft

> 切片目标一句话：交付 calculate_craft_result 纯函数 + CraftService 确定性路径 + craft_item 工具/门/reducer/叙事 + scenario=m4 场景，实现「常规配方同 seed 同结果、失败零写入」 ｜ 验收命令：`uv run pytest tests/unit/test_crafting.py tests/integration/test_craft_deterministic.py -q` ｜ 里程碑 M4 ｜ 编号区间 015–018 ｜ 执行/验收：plan-m4（Lead 复核）

## 1. 目标与验收命令

- 目标（第 13 章 §13.2–§13.6、D-M4-1/D-M4-4/D-M4-5/D-M4-8/D-M4-11）：纯函数数值权威 + 服务层草稿事件 + CRAFT_* reducer + craft_item 工具矩阵/门路由 + m4 场景（铁匠铺工位/材料）；M2/M3 golden 零回归。
- 验收命令与真实输出：
  - `uv run ruff check .` → All checks passed
  - `uv run ruff format --check .` → 125 files already formatted
  - `uv run mypy src` → Success: no issues found in 74 source files
  - `uv run lint-imports` → Contracts: 2 kept, 0 broken（Analyzed 102 files / 400 dependencies）
  - `uv run pytest tests/unit/test_crafting.py tests/unit/test_scenario_m4.py tests/unit/test_reducers.py tests/integration/test_craft_deterministic.py tests/security/test_craft_scope.py -q` → **72 passed**
  - `uv run pytest -q` → **1053 passed in 7.15s**（M4-015 基线 1001 + 新增 52，零回归）
  - `uv run pytest tests/replay -q` → **37 passed**（M2/M3 golden 逐值不变）
- 不做什么：自由制造生成路径（CraftAgent/FakeLLM，017）、golden 回放（018）；不接真实 LLM。

## 2. 改动与提交

- `domain/crafting.py`：新增 `calculate_craft_result`（成功判定→质量档→副产物固定掷骰顺序）、`derive_item_id`（blake2b→Crockford 26 位，匹配 ItemId 正则）、`instantiate_craft_result`（模板级结果不变，实例 ID/定义列表）、`build_craft_state_slice`（按 `Item.tags[0]` 聚合、station 前缀、next_seq=last_seq+1）。
- `domain/reducers.py`：`_reduce_craft`（扣材料/入包/写 items、模板键顺序校验、失败零变化）+ CRAFT_DETERMINISTIC/CRAFT_GENERATED 注册 + CRAFT_REJECTED identity。
- `domain/rules/engine.py`：`PRECONDITION_TOOLS` 增 craft_item（actor/mode/recipe_id 最低形状检查，深度校验留服务层）。
- `domain/scenario.py`：m4 常量（固定 ULID）+ `build_m4_state`（forge 房间 station.forge、入口 east 出口、材料×2/铜线×2、实例 tags 首项=模板键）；**不改** build_initial_state/build_m3_state。
- `services/craft_service.py`（新增）：`list_recipes`/`craft`（RECIPE 路径；未知配方 NOT_FOUND、工位不符/缺工具 NOT_ALLOWED、材料不足 OUT_OF_RANGE/NOT_FOUND、FREE 暂 NOT_ALLOWED；只返回 Result+草稿事件，不 commit/不调 GameEngine）。
- `tools/builtin/crafting.py`（新增）+ `tools/builtin/__init__.py`：craft_item 声明（player/system/npc:blacksmith、DESTRUCTIVE、deterministic）+ `build_game_tool_registry()`；`tools/gate.py` 增 `CraftServicePort` 与 craft_item 路由（ValidationError→INVALID_INPUT）。
- `orchestration/narrator.py`（CRAFT_* 成功/失败模板 80–150 字）、`orchestration/intent.py`（recipe_names 注入 + 打造/锻造/制作/制造/合成/做个 关键词；缺省 None 行为不变）、`config/settings.py`（free_craft_enabled=false）、`main.py`（catalog→CraftService→完整 registry→gate/parser 装配）、`services/game_engine.py`+`api/schemas.py`+`orchestration/turn_orchestrator.py`（scenario="m4"）。
- 测试：新增 `tests/unit/test_crafting.py`（16 例，纯函数/实例化/视图）、`tests/unit/test_scenario_m4.py`（6 例）、`tests/integration/test_craft_deterministic.py`（13 例）、`tests/security/test_craft_scope.py`（8 例）；`tests/unit/test_reducers.py` 追加 9 例 craft 投影；`tests/conftest.py` 增 m4_game_harness。
- 契约变更：无（严格按第 13 章 §13.2–§13.6 实现）；未改 Event/EventType/GameState 基线与 Resolver/GameEngine 结构；无新增依赖。提交：Lead 统一提交（本会话不 git add/commit）。

## 3. 验证结果

- 质量门：ruff check/format、mypy（74 files）、lint-imports（2 kept）全绿。
- 功能：m4 建档 → 向东走进铁匠铺 → 打造铁剑：单条 CRAFT_DETERMINISTIC 事件、`event.rng_seed == result.craft.rng_seed`、seed=0 产出铁剑+铁渣（masterwork）、材料按 consumed 模板键扣除、工具不消耗；seed=19 掷骰失败仅推进 turn/world_time（背包/items 零变化）。
- 失败分支零写入：材料不足/工位不符/缺工具/未知配方/自由制造关闭均 Result(ok=False)+零事件，version/last_seq/state_hash 三不变。
- 回归：全量 1053 passed（基线 1001 零回归）；tests/replay 37 passed（M2/M3 golden 未变）。
- 护栏：CraftService 不提交；LLM 未接线（FREE 返回 NOT_ALLOWED）；actor_scope 未放宽（M2 六工具注册表不变，安全用例逐格断言）。

## 4. 下一步与风险

- 下一步：**017-free-craft-validation**（CraftAgent + FakeLLM 生成路径、Schema/一致性校验、失败回滚），验收 `uv run pytest tests/integration/test_free_craft.py -q`（待新建）；随后 018 制造 golden（5 用例）。
- 风险：① m4 单角色链路在主组合根仍注入 Resolver/多角色编排（文档 D-M4-3 口径为不接 Resolver），主应用 path 与测试 harness path 存在差异，需 M4 收尾/018 前统一登记；② 复核遗留 R-01（审计表述泛化）、R-02（第 21 章示例 save_seed）建议 018 文档回写时清理；③ 自由制造未接线，开关打开但不生效（生产默认 false，M5/017 再评估）。
- 需要人工决策：m4 生产装配是否改为不注入 Resolver（与 D-M4-3 一致），或接受多角色编排路径（当前 main 行为）；建议 018 出口前拍板。
