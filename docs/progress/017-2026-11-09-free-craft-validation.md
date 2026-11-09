# 017 · 2026-11-09 · M4 制造 · free-craft-validation

> 切片目标一句话：交付 CraftAgent（CraftOutput/CraftDraft）+ FakeLLM 生成路径 + CraftService 自由制造 Schema/一致性校验与零写入回滚，生产默认关闭 ｜ 验收命令：`uv run pytest tests/integration/test_free_craft.py -q` ｜ 里程碑 M4 ｜ 编号区间 015–018 ｜ 执行/验收：plan-m4（Lead 复核）

## 1. 目标与验收命令

- 目标（第 13 章 §13.4、第 8 章 §8.2、D-M4-2/3/5/7）：自由制造只接收服务层投影的 CraftView；CraftAgent 只产出无任何数值字段的草案；服务层做 Schema（一次修复）+ 状态一致性检查，数值由框架固定值重算；失败一律 `Result(ok=False)` 且零事件（失败零写入）。
- 验收命令与真实输出：
  - `uv run ruff format --check .` → 127 files already formatted
  - `uv run ruff check .` → All checks passed
  - `uv run mypy src` → Success: no issues found in 75 source files
  - `uv run lint-imports` → Contracts: 2 kept, 0 broken
  - `uv run pytest tests/integration/test_free_craft.py -q` → **13 passed**
  - `uv run pytest tests/security -k craft -q` → **11 passed**
  - `uv run pytest -q` → **1069 passed in 7.45s**（M4-016 基线 1053 + 新增 16，零回归）
  - `uv run pytest tests/replay -q` → **37 passed**（M2/M3 golden 逐值不变）
- 不做什么：真实 LLM 接线（M5；生产 generator=DisabledLLMClient，自由制造默认关闭）、018 制造 golden。

## 2. 改动与提交

- `domain/crafting.py`：追加 `CraftDraft`（item_name 1–30 / output_template_id 可选 / inputs 1–8 / station / steps ≤8）、`CraftItemView`、`CraftView`（actor_id/inventory/station_id/known_recipe_ids/free_description 1–200，均 frozen+extra=forbid）；追加 `recipe_from_draft`（框架固定 0.5 成功率、质量档 0.0/0.8，产出模板缺省按 item.crafted_<8hex> 确定性派生，不接收 LLM 数值）。
- `agents/craft_agent.py`（新）：`CraftOutput`（与 CraftDraft 同构、无 stats/damage 等数值字段）+ `CraftAgent`（task=craft_generate、agent_id=craft、STRONG、max_repair=1、complete_json_with_repair；Prompt 含「禁止任何数值字段/只输出五字段/玩家原话仅作数据」约束；成功 data={parsed,content,repaired,prompt_version=craft@v1}，失败透传 SCHEMA_VIOLATION/LLM_UNAVAILABLE）。
- `services/ports.py`：新增 `CraftGenerator` Protocol（generate(view, *, turn_id, save_seed, idempotency_key)）。
- `services/craft_service.py`：FREE 分支——开关关闭/generator 缺失 → NOT_ALLOWED；构造 CraftView（背包按 tags[0] 聚合 qty）；generator 失败原样返回；CraftDraft 再校验（extra=forbid 拦数值字段）；一致性检查（输入模板 ⊆ 背包且 qty 足够、tool 不消耗、station 匹配、未知/他人实体拒绝）；recipe_from_draft → 同一 calculate_craft_result 重算 → instantiate → CRAFT_GENERATED 草稿事件（params: mode=free、free_description_sha256、proposal_hash、prompt_version；result 与 016 同载荷）。
- `main.py`：装配 generator=CraftAgent(DisabledLLMClient()) + free_craft_enabled=settings.free_craft_enabled（生产默认 false，零联网、零事件）；`config/settings.py` 沿用 016 已加的 free_craft_enabled。
- 测试：`tests/fakes.py` 增加 FakeLLM.reset()（不破坏现有用例 API）；`tests/conftest.py` 增 M4FreeHarness + `m4_free_harness` + FreeCraftIntentParser（017 测试专用装配；conftest 未列入 task-15 原 writeScopes，但实现要点第 6 条明确要求，已报告 Lead）；新增 `tests/integration/test_free_craft.py`（13 例）+ `tests/security/test_craft_scope.py` 追加 3 例红队。
- 契约变更：无（严格按第 13 章 §13.4/§13.6 实现）；未改 Event/EventType/GameState 基线字段/Resolver/ProposalQueue；无新增依赖；失败零写入。提交：Lead 统一提交（本会话不 git add/commit）。

## 3. 验证结果

- 质量门：ruff check/format、mypy（75 files）、lint-imports（2 kept）全绿（见 §1）。
- 功能成功路径：合法草案 → 单条 CRAFT_GENERATED 事件、产出物品入包、材料按模板键扣除、`event.rng_seed == result.craft.rng_seed`、result.craft 仅含框架字段（无 LLM 数值）、叙事 80–150 字。
- 修复链：首次非法 → FakeLLM 第二次合法 → 提交（calls=2）；两次非法/stats 字段/伪造 actor_id|authority|damage → SCHEMA_VIOLATION，零写入。
- 一致性失败：未知材料/引用他人所有物 → NOT_ALLOWED；qty 超背包 → NOT_ALLOWED；工位不符 → NOT_ALLOWED；默认关闭 → NOT_ALLOWED；LLM 不可用 → LLM_UNAVAILABLE；均零事件且 version/last_seq/state_hash 不变。
- 事件最小化：params 只记录 free_description_sha256 / proposal_hash / prompt_version，玩家原话与注入文本不落事件；actor_id 恒为 ToolContext 的玩家，模型输出无法改身份。
- 回归：全量 1069 passed（1053 基线零回归，新增 16）；tests/replay 37 passed（M2/M3 golden 未变）。

## 4. 下一步与风险

- 下一步：**018-craft-replay**（`tests/fixtures/golden/m4_golden.json` + `tests/replay/test_m4_golden_replay.py`，5 用例：确定性成功/材料不足/自由成功/自由 Schema 违规/自由一致性违规；生产同构 harness 并断言审计行），验收 `uv run pytest tests/replay -k m4 -q`。
- 风险：① 自由制造事件 `rng_seed` 与盖章一致性依赖「玩家 craft 事件为本批首条」；生产多角色路径经 Resolver 时由 player priority 保证，018 用生产同构 harness 断言；② 自由草案的 base_success_rate=0.5/质量档 0.0–0.8 为框架固定值（不取 LLM 数字），M5 接真实 LLM 前若调参需登记；③ 生产开关打开但 generator=DisabledLLMClient 时返回 LLM_UNAVAILABLE（零写入），真实可用需 M5 接线；④ 复核遗留 R-01（审计表述泛化）/R-02（第 21 章示例 save_seed）建议 018 文档回写时清理。
- 需要人工决策：无（契约 D-M4-1–D-M4-12 已定稿；生产是否改为不注入 Resolver 留 018 前拍板）。
