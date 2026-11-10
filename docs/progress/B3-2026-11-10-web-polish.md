# B3 · 2026-11-10 · M6 前端体验（不占编号）· web-polish

> 切片目标一句话：前端展示层打磨——回放元数据中文化（event_type/method/actor_id，未知值回退原值）+ 失败提示分组（`effects[].hint`/`suggested_actions`，只读展示不改原文）+ 状态面板三态（加载中/失败/空/数据）+ 回放面板本地化 ｜ 验收命令：`pnpm --dir web build`、`pnpm --dir web typecheck`、`pnpm --dir web test` ｜ 里程碑 M6（前端打磨，不占 025–028 编号）｜ 决策：第 23 章 §23.3/§23.4、D-M6-6

## 1. 目标与验收命令

- 目标（第 23 章 §23.3/§23.4）：① `utils/labels.ts`——`EventType`/`method`/固定场景 `actor_id` → 中文展示标签，**未知值一律回退原值**（不静默吞掉，便于发现新增枚举）；② `utils/feedback.ts` + `TurnFeedback.vue`——从 `TurnResult.effects[].hint` 与 `suggested_actions` 收集「提示/可以试试」两组文本，去空、去重、限长，仅展示、不拼进 `text`、不改叙事原文；③ `StatusPanel` 三态：加载中 / 加载失败（含错误横幅）/ 无数据（`view` 缺失降级）/ 正常数据，`<dl>` 结构 + `role="status"`；④ 回放面板/时间轴元数据本地化（复用 labels）；⑤ 游戏页接入 `TurnFeedback`，样式收敛到 `terminal.css`。
- 验收命令与真实输出：
  - `pnpm --dir web build` → OK（**51 modules**；JS **122.63kB** / gzip **46.61kB**）
  - `pnpm --dir web typecheck` → OK
  - `pnpm --dir web test` → **10 files / 41 tests passed**（新增 12 例：labels/turnFeedback/statusPanel/gameStore/replayTimeline 扩展等）
  - 契约测试 `uv run pytest -q tests/integration/test_frontend_contract.py` → **8 passed**（展示层改动不影响 API/契约）
  - `uv run pytest -q` → **1376 passed in 19.50s**；`uv run pytest tests/replay -q` → **61 passed**
- 不做什么：不改 API/契约/`TurnResult.text`；不把状态行或提示拼进叙事正文；不引入重型 UI 库；不做 e2e（Playwright 后移）。

## 2. 改动与提交

- 提交：`1174af1 feat: 前端打磨（回放元数据本地化 + 失败提示分组 + 状态面板三态）`
- 文件（15 个，+453/-27）：`web/src/utils/labels.ts`（+79）、`web/src/utils/feedback.ts`（+44）、`web/src/components/TurnFeedback.vue`（+33 新）、`StatusPanel.vue`、`ReplayEventPanel.vue`、`ReplayTimeline.vue`、`views/GameView.vue`、`views/ReplayView.vue`、`stores/game.ts`、`styles/terminal.css`；测试 `labels.test.ts`、`turnFeedback.test.ts`、`statusPanel.test.ts` 新增 + `gameStore.test.ts`/`replayTimeline.test.ts` 扩展。
- 关键 diff 摘要：`ReplayEventPanel` 用 labels 映射展示；`TurnFeedback` 在上一次失败回合后展示两组文本（空则不出现）；`StatusPanel` 用 `v-if` 分支区分 4 种状态；未引入任何 `v-html`/`innerHTML`（安全扫描用例保持）。
- 护栏检查：运行期零 Node；文本插值渲染；契约测试 8 例保持；无新增 npm 依赖（复用既有 vue/vitest/jsdom）。
- 契约变更：无（纯展示层；API 与回放契约不变）。

## 3. 验证结果

| 检查 | 结果 |
| --- | --- |
| 构建/类型 | `pnpm build` OK（51 modules，js 122.63kB / gzip 46.61kB）；`typecheck` OK |
| 前端单测 | vitest **10 files / 41 passed**（新增 12 例：labels 回退、feedback 去空去重、StatusPanel 三态、gameStore feedback、时间轴展示） |
| 契约测试 | `test_frontend_contract.py` **8 passed**（API_PATHS/字段/只读零写入不变） |
| 全量/回放 | 1376 passed / replay 61 零回归；覆盖率 88.93%；四门（167/89/2 kept）全绿 |
| 失败与回滚 | 无 |

## 4. 下一步与风险

- 下一步：**026-card-import**（卡库 UI，依赖 M6B-3 已交付的 7 端点）→ **028-llm-card-generate**（依赖 M5 出口 LLM 接线）；M6 后端前置剩 generate 端点。
- 已知限制（本批玩家独立验收 PASS，5 条非阻塞，跨切片汇总）：① **Web 状态面板未用 `qty`**（m4 制造产物看不到「铁锭×2」，纯前端下批修，后端 `/view` 已带 qty）；② Web 展示原始 `room_id`/`item_id`，可折叠/本地化；③ 终端仍无按卡开局（M6 前端范围）；④ 剧情保护失败 hint 仍通用、M3 首屏断句待重钉（跨批既有项）；⑤ 023 调度器默认未接生产组合根（M5 侧设计口径，默认零调用）。
- 需要人工决策：无（本轮）。
