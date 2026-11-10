# M5E-1 · 2026-11-10 · M5 出口附加项（不占编号）· llm-agent-wiring

> 切片目标一句话：M5 出口附加项接线第一片——解析 free 分支（默认 fail-closed）+ LLM 叙事 Agent + LLM 角色 Agent（cassette 离线回放、失败回退确定性/模板、默认离线装配逐字等价）；裁判未接（需用户授权改 Resolver）｜ 验收命令：`uv run pytest tests/integration/test_llm_wiring.py -q` ｜ 里程碑 M5 出口附加项（不占 019–028 编号）｜ 决策：D-M5-13（接线口径 + cassette JSON 偏离）、D-M5-6

## 1. 目标与验收命令

- 目标（第 8/9/12 章、M5 出口附加项）：① 解析 **free 分支**：`DeterministicIntentParser(free_craft_enabled=…)` 由组合根传入 `TEXTGAME_FREE_CRAFT_ENABLED`（**默认 False = fail-closed**），开启时制造关键词未命中配方名才产出 `craft_item(mode="free")`；关闭时行为与 M2–M4 完全一致（P2-4(a) 解析白名单派生配套）；② `agents/narrator_agent.py::LLMNarratorAgent`（`prompt_version="narrate@v1"`）：输入=本回合已提交事件事实清单 + 房间/环境描述，输出 `NarrativeOutput`（80–150 字服务端二次校验），经 `complete_json_with_repair` 一次修复，失败/离线/两次不合格/异常 → 回退 `TemplateNarrator`；编排层写 `narrative_source="llm"|"template"`（D-007-2 两值），**不写状态**；③ `agents/llm_character_agent.py::LLMCharacterAgent`（`prompt_version="character@v1"`）：输入**仅该角色私有感知切片 + 私有记忆**，输出 `CharacterOutput`（≤2 提案、工具名/参数 Schema），失败/离线 → 回退确定性 `CharacterAgent`；与 023 批量互斥（消费私有切片，只能串行）；④ `main.py` 按 `settings.llm_enabled` 装配（false 分支逐字等价：`TemplateNarrator` + 确定性角色 Agent；true 分支注入 LLM Agent + `LLMCardDraftGenerator`）；⑤ cassette：6 个**手工合成** JSON（`tests/fixtures/cassettes/`）+ `tests/cassette_transport.py`（零网络回放，缺 cassette 即失败）；⑥ **裁判接线未做**（需用户授权改 Resolver，D-M5-7/D-TEAM-2 门控）。
- 验收命令与真实输出：
  - `uv run pytest tests/integration/test_llm_wiring.py -q` → **13 passed**
  - `uv run pytest -q` → **1417 passed in 22.56s**（第三批 1376 + 新增 41）；`uv run pytest tests/replay -q` → **61 passed**（逐值不变）
  - `uv run python -m textgame.demo --check` → exit 0（7 屏字数 114/83/81/85/82/84/81 不变，默认离线路径不变）
  - 覆盖率 **88.86%**（≥80）；四门：ruff check 全绿 / format **172 files** / mypy **91 files** / lint-imports **2 kept**
- 不做什么：不接裁判 Agent（Resolver 改动需授权）；不联网真实调用（仅 Fake/cassette）；不改默认离线装配/事件结构/golden；不引入 PyYAML（cassette 用 JSON）。

## 2. 改动与提交

- 提交：`c20b577 feat: M5 出口附加项接线（解析 free 分支 + LLM 叙事 + LLM 角色，cassette 离线）`
- 文件：`src/textgame/agents/narrator_agent.py`（+240 新）、`src/textgame/agents/llm_character_agent.py`（+140 新）、`src/textgame/main.py`（+36 装配分支）、`src/textgame/orchestration/intent.py`（+49 free 分支）、`src/textgame/orchestration/turn_orchestrator.py`（+53 `narrative_source`/LLM 叙事器）、`tests/cassette_transport.py`（+72 回放器）、`tests/fixtures/cassettes/{intent_parse_free,narrate_success,narrate_repair,narrate_invalid_twice,character_success,character_invalid_twice}.json`（6 个）、`tests/integration/test_llm_wiring.py`（488 行，13 例）。
- 关键 diff 摘要：Agent 只返回候选文本/提案，不写状态/不建局；cassette 回放等价于「真实网络不可用」的网络边界；默认 `llm_enabled=false` 时装配与 M4 逐字等价（golden 零回归）。
- 已知偏离（**D-M5-13**）：cassette 用 **JSON** 而非 vcrpy YAML（PyYAML 非声明依赖，第 19 章不引入；结构与 vcrpy `interactions` 兼容，供测试回放器读取）；手工合成、非真实 API 录制，只含合成内容、无密钥/玩家隐私。
- 契约变更：无（`narrative_source` 取值仍 `llm`/`template`；`TurnResult`/事件不变）；接线口径登记 PROGRESS §5。

## 3. 验证结果

| 检查 | 结果 |
| --- | --- |
| 本片测试 | `test_llm_wiring.py` → 13 passed：叙事成功用 LLM 文本并标 `llm`；一次修复后成功；修复耗尽回退模板；离线回退零网络；角色提案进入既有 Resolver 链；角色失败回退确定性计划；角色输入只含私有感知；free 分支默认 fail-closed；LLM free 意图走 cassette；生产 HTTP free 开关两态；`main` 默认离线装配逐字等价；cassette 为手工/无密钥 |
| 全量/回放 | 1417 passed in 22.56s（1376 基线零回归，本批新增 41）；replay 61 逐值不变 |
| demo/console | `demo --check` exit 0（7 屏字数不变）；默认离线装配零改变 |
| 覆盖/四门 | 88.86%；ruff check/format 172 files、mypy 91 files、lint-imports 2 kept 全绿 |
| 失败与回滚 | 无 |

## 4. 下一步与风险

- 下一步：**裁判 Agent 接线**（需用户授权改 `Resolver`，D-M5-7 门控）；其余出口附加项（叙事/角色）已接，解析 free 已接；随后做 **M5 真实 LLM 端到端演示**（默认仍离线、cassette/Fake 验收）。
- 风险/已知限制：① **裁判未接**（待用户授权，Resolver 当前 `judgements` 不读取、无 `needs_judge` 分支）；② LLM=true 路径目前仅 Fake/cassette 验证，**未联网真实调用**（按 D-M5-5 纪律）；③ 023 调度器仍未注入生产组合根（默认零调用）；④ cassette 为手工 JSON，真实 API 录制不在本批范围。
- 需要人工决策：**是否授权接裁判 Agent（改 Resolver）** —— 需用户拍板。
