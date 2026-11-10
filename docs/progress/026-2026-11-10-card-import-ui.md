# 026 · 2026-11-10 · M6 前端体验 · card-import-ui

> 切片目标一句话：交付 M6-026 卡库前端 UI——`/cards` 列表（分页/搜索）、`/cards/import` 导入（64KB/UTF-8 预检 + 服务端 validate + multipart 幂等 + 错误映射）、`/cards/:cardId` 详情（9 字段只读）与「按卡开局」（`POST /games {card_id}`）｜ 验收命令：`pnpm --dir web build`、`pnpm --dir web test`、`uv run pytest -q tests/integration/test_frontend_contract.py` ｜ 里程碑 M6（3/4）｜ 编号区间 025–028 ｜ 决策：D-M6-2/10、D-M6-6

## 1. 目标与验收命令

- 目标（第 23 章 §23.5/§23.6/§23.9、D-M6-2）：① 路由 `/cards`（列表 + 分页 + 搜索）、`/cards/import`（导入）、`/cards/:cardId`（详情）；② 列表/详情消费卡库既有 7 端点（`GET ""`/`GET /{id}`/`POST /validate`/`POST /import`），详情只读展示 `CharacterCard` 9 字段；③ 导入端到端：本地文件预检（`.json`、`≤64KB`、UTF-8 可解码）→ 服务端 `POST /validate` → `POST /import`（multipart、必带 `Idempotency-Key`）；错误映射 413（过大）/422（JSON/Schema）/403（内容安全）/409（幂等冲突）/404；④ 详情「开始游戏」→ `POST /games {card_id}`（D-M6-2），成功后进入对局页；⑤ 无 `v-html`/`innerHTML`/自动重试；**不引入 Ajv**（客户端仅基础预检，字段级校验以服务端 `validate` 为权威——D-M6-10）；⑥ 无 `/cards/new`、详情无删除/模板新建（后移，玩家验收口径）。
- 验收命令与真实输出：
  - `pnpm --dir web build` → OK（**61 modules**；JS **139.72kB** / gzip **51.50kB**）
  - `pnpm --dir web typecheck` → OK；`pnpm --dir web test` → **15 files / 62 tests passed**（新增 21）
  - `uv run pytest -q tests/integration/test_frontend_contract.py` → **11 passed**（含卡库端点与 TS 类型对齐）
  - `uv run pytest -q` → **1417 passed in 22.56s**；`uv run pytest tests/replay -q` → **61 passed**
- 不做什么：不做 028 LLM 生成卡 UI（下一片，依赖 generate 端点已交付）；不做 `/cards/new`/详情删除/模板新建（后移）；不引 Ajv/新 UI 库；不做 e2e（Playwright 后移）。

## 2. 改动与提交

- 提交：`18196a7 feat: M6-026 卡库导入 UI（列表/详情/导入/按卡开局）`
- 文件（20 个，+1746/-24）：`web/src/views/{CardLibraryView,CardImportView,CardDetailView}.vue`（新）、`web/src/stores/cards.ts`（+232）、`web/src/utils/cards.ts`（+104）、`web/src/api/client.ts`（+154）/`types.ts`（+106）、`web/src/components/CardFields.vue`（+49 新）、`web/src/router/index.ts`、`web/src/App.vue`、`web/src/stores/game.ts`、`web/src/utils/labels.ts`、`web/src/components/ReplayEventPanel.vue`、`web/src/styles/terminal.css`（+79）；测试 `web/tests/unit/{cardsClient,cardsStore,cardsUtils,cardFields,cardLibraryView}.test.ts`（新增）；`tests/integration/test_frontend_contract.py`（+172）。
- 关键 diff 摘要：`cards` store 管理列表/详情/导入状态与错误码映射；导入用 `FormData` + 每次动作生成一次 `Idempotency-Key`；详情读取 9 字段并显示 `warnings`；「开始游戏」调用 `POST /games {card_id}` 后跳 `/game/:id`；文本插值渲染（无 `v-html`）。
- 已知实现偏差（**D-M6-10**）：不引 Ajv（客户端仅基础预检 + 服务端 `validate` 为权威，减少 1 个 npm 依赖）；无 `/cards/new`、详情无删除/模板新建（后移到后续体验切片）；错误提示按错误码映射（与既有 ErrorBanner 一致）。
- 契约变更：前端新增对卡库 7 端点的消费（契约测试 11 例钉住）；无后端改动、无事件/回放影响。

## 3. 验证结果

| 检查 | 结果 |
| --- | --- |
| 构建/类型 | `pnpm build` OK（61 modules；js 139.72kB / gzip 51.50kB）；`typecheck` OK |
| 前端单测 | vitest **15 files / 62 passed**（新增 21：cards store 分页/导入状态/错误码、utils 预检、CardFields、CardLibraryView） |
| 契约测试 | `test_frontend_contract.py` **11 passed**（卡库端点/字段与 TS 类型一致、按卡开局 body） |
| 全量/回放 | 1417 passed / replay 61 零回归；覆盖率 88.86%；四门（172/91/2 kept）全绿 |
| 安全 | 无 `v-html`/`innerHTML`/自动重试（security-scan 用例保持）；上传 ≤64KB/UTF-8 预检；文本插值渲染 |
| 失败与回滚 | 无 |

## 4. 下一步与风险

- 下一步：**M6-028-llm-card-generate**（M6 最后一片：brief 表单 + 生成草稿预览 + 模板兜底提示 + 保存/开局；依赖 `POST /character-cards/generate` 已交付 + M5 出口接线）→ M6 4/4；后端前置仅剩 **裁判接线（待用户授权）**。
- 已知限制（本批玩家独立验收 PASS，4 条非阻塞，跨切片汇总）：① **离线「生成卡」即模板兜底**，028 生成入口需显著提示；② 终端仍无卡库/按卡开局（M6 前端范围）；③ 限流为进程内滑窗（单机可接受，含 generate 端点）；④ 既有 Web 状态面板未展示 `qty`、失败 hint 通用句、M3 首屏断句（跨批既有项）。
- 需要人工决策：无（本轮）；**裁判接线需用户授权**（另行）。
