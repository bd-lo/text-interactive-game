# 028 · 2026-11-11 · M6 前端体验 · llm-card-generate

> 切片目标一句话：交付 **M6-028 LLM 生成人物卡 UI**——`/cards/generate` 的 brief 表单、草稿预览（模板/LLM 徽章）、JSON 编辑 + 重新校验、保存到卡库与「按此卡开局」、429 限流可读提示；**M6 4/4 完成** ｜ 验收命令：`pnpm --dir web test`、`pnpm --dir web typecheck`、`pnpm --dir web build`、`node scripts/e2e-web.mjs --base-url <临时 uvicorn>`、`uv run pytest -q tests/integration/test_frontend_contract.py` ｜ 里程碑 M6（4/4）｜ 编号区间 025–028 ｜ 决策：D-M6-11（e2e 验收口径）、D-M6-8/D-M6-9（generate 端点口径，M6B-3/4 已定案）

## 1. 目标与验收命令

- 目标（第 23 章 §23.6/§23.9 F4、D-M6-8/9）：① 路由 `/cards/generate`（注册在 `/cards/:cardId` 之前，避免被详情路由吞掉），卡库页与首页给入口；② `brief` 表单（≤1000 码点，空/超限禁用提交）、可选 `options.tone`；③ 调 `POST /api/v1/character-cards/generate`（写幂等键、手动重试复用同键、**无自动重试**），响应 `{draft, validation, generated_by}` 精确字段校验；④ **离线即模板兜底的显著提示**（M6B-4 已知限制）+ 草稿徽章「模板草稿」/「LLM 草稿」；⑤ 草稿 9 字段只读预览 + JSON 可编辑 + 「重新校验」调 `POST /validate`，校验未过禁用保存；⑥ 显式「保存到卡库」→ `POST /character-cards`（201 `{card_id, card}`），**保存前不建局/不跳转**，保存后仅「查看卡片」「按此卡开局」两个显式入口；⑦ 429 按 `Retry-After` 给中文等待提示（`error_code=OUT_OF_RANGE` 不露英文码）；⑧ 禁 `v-html`/`innerHTML`/`eval`/`EventSource`/自动定时重试，文本插值渲染。
- 验收命令与真实输出：
  - `pnpm --dir web test` → **29 files / 158 tests passed**（基线 25/117，**新增 41**）
  - `pnpm --dir web typecheck` → exit 0；`pnpm --dir web build` → ✓ **82 modules**，`GenerateCardView-*.js` **16.86kB / gzip 5.84kB**（入口 index 101.82kB 基本不变）
  - `node scripts/e2e-web.mjs --base-url http://127.0.0.1:8791` → **14/14 步骤、62/62 断言 PASS**，Edge `Edg/157.0.4341.42` headless，11 张截图，0 console 错误
  - `uv run pytest -q tests/integration/test_frontend_contract.py` → **17 passed**（基线 12，+5 generate/save/深链契约）
  - `uv run pytest -q` → **1647 passed**（基线 1642 + 5，29.67s）；`uv run pytest tests/replay -q` → **68 passed**
- 不做什么：不做真实 LLM 默认开启（默认离线仍是模板兜底）；不接 Playwright/新依赖（e2e 改用零依赖 CDP 脚本，见 D-M6-11）；不改后端 generate/save 端点与契约；不改旧 golden。

## 2. 改动与提交

- 提交：`a18d545 feat: M6-028 生成人物卡 UI（brief 表单/草稿预览/模板兜底标识/保存走卡库）`
- 前端业务（+1367 行量级）：`web/src/api/client.ts`（+`generateCard`/`saveCard`、`ApiClientError.retryAfterSeconds` 解析 `Retry-After`、60s 超时、字段精确校验）、`web/src/stores/cardGenerate.ts`（新，生成/保存/限流/错误状态）、`web/src/utils/cardGenerate.ts`（新，码点计数/模板兜底判定/错误中文化）、`web/src/views/GenerateCardView.vue`（新）、`web/src/components/{GenerateCardForm,CardDraftPreview}.vue`（新）、`web/src/router/index.ts`、`web/src/views/{CardLibraryView,HomeView}.vue`、`web/src/styles/terminal.css`、`web/src/api/types.ts`（注释对齐 11 端点）；`web/tests/unit/{cardGenerateClient,cardGenerateStore,cardGenerateUtils,generateCardView}.test.ts`（新 41 例）+ 3 个既有测试补路由断言。
- 契约与验收工具：`tests/integration/test_frontend_contract.py`（+205 行：11 端点登记、generate 实测 5 例、save 方法级、`/cards/generate` SPA 深链）；`scripts/e2e-web.mjs`（新，零依赖 Node 26 CDP 真实浏览器 e2e，参数 `--base-url/--browser/--out/--headful/--keep-open`，负向控制无静态产物时 exit 1）。
- 构建产物：`web/dist` 重建入库（82 modules；旧 hash 删除、新 hash 新增）。
- 契约变更：前端消费端点 10 → **11**（新增 generate；save 与 list 共用 `/character-cards`）；**无后端/事件/回放改动**；无迁移、无新增依赖。

## 3. 验证结果

| 检查 | 结果 |
| --- | --- |
| 前端单测 | vitest **29 files / 158 passed**（+41：client generate/save 字段与错误/429 Retry-After/409 清键、store 模板兜底判定/分支、utils 码点/文案、视图离线提示/字数上限/徽章/禁用保存/保存成功） |
| 构建/类型 | `pnpm build` OK（82 modules；GenerateCardView 16.86kB/gzip 5.84kB）；`typecheck` exit 0 |
| 契约测试 | **17 passed**（11 端点/接口登记、离线 generate 200 模板兜底 `source=manual`/`generated_by=null`/`TEMPLATE_FALLBACK`、同键同参复用、同键异参 409、缺键 422、第 6 次 429+`Retry-After`、save 方法级 201、深链 200+CSP） |
| 真实浏览器 e2e | 14/14 步骤、62/62 断言 PASS：深链→离线提示显著→1000 码点边界→模板草稿 9 字段→JSON 非法禁用保存→保存成功两入口→按卡开局（`/view` hp/max_hp 来自草稿卡）→1 回合叙事更新→卡库/详情→375px 无横向溢出→429 中文提示不自动重试→0 console 错误；负向控制 exit 1（排除假绿） |
| 全量/回放 | **1647 passed** / replay **68 passed**；旧 golden 零改动（`git status tests/fixtures/golden` 空、未 `--snapshot-update`）；ruff format/check、mypy 93 files、lint-imports 2 kept、`check_docs` 全绿 |
| 玩家独立验收 | **PASS**：HTTP 62 检查 P0 40/40·P1 24/24；真浏览器主回路 P0 31/31·P1 12/14；429 限流 P0 5/5·P1 2/2；P0 76/76 全绿，端口/进程已清理。遗留 **2 项展示层 P1**（草稿「初始物品」显示模板键 `item.torch`；保存成功区显示内部 `card_…` ID）→ 记入 R3 打磨批次 |
| 安全 | `web/src` 禁入 grep 无匹配；brief 只作数据渲染、错误技术细节仅折叠 detail；无新依赖（package/pnpm-lock/pyproject/uv.lock 无 diff） |
| 失败与回滚 | 无（功能为加法；回滚点=本提交前的 `0d46e9a`） |

## 4. 下一步与风险

- 下一步：**R3 玩家体验打磨**（028 玩家验收 2 项 P1 + R3 验收 P1/P2 → 批次收尾）→ **M5E-2 裁判 Agent 接线**（默认离线零调用、cassette 测试；Lead 依用户「直到开发结束」授权，硬约束为默认逐字等价）→ 最终全量门禁/独立验收/文档与 push。
- 已知限制（本批登记，不阻塞）：① 默认离线「生成」即模板兜底（页面已显著提示，D-M6-9 口径不变）；② 限流为进程内滑窗 5 次/60s（单机可接受，多用户部署门内已知限制）；③ e2e 脚本不接入 CI（系统浏览器 + uvicorn 组合；CI 侧由契约测试 + vitest/build 兜底）——见 **D-M6-11**；④ 变更 `brief` 后重新生成会换新幂等键（动作级键），同键异参 409 仅在同动作重试语义下出现。
- 需要人工决策：无（本批）；e2e 长期是否接入 CI（Playwright/浏览器矩阵）留发布期评估。
