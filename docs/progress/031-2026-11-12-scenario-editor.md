# 031 · 2026-11-12 · N1 · 网页场景编辑器（N1-031）

> 切片目标一句话：网页 `/scenarios/editor` 用表单/JSON 双向编辑 v1/v2 场景，实时调用服务端
> `/scenarios/validate`，通过后以幂等导入保存并可开局。｜ 验收命令：见 §1 ｜ 里程碑 N1 内容轨
> （第 17 章 §17.9.2）｜ 编号区间 029–036 ｜ 权威输入：`docs/design/scenario-dsl-v2.md` §4/§5、
> `user-scenario-modules.md` §2/§6 ｜ 执行/验收：web-a11y

## 1. 目标与验收命令

- 目标：表单编辑 房间/物品/NPC/玩家/目标/结局/建议 + v2 字段（`schema_version=2`、`flags`、13 个白名单
  谓词/组合子、多结局 `priority`/`when`）；JSON 原文视图/导入粘贴、下载；debounce 实时服务端校验（错误/警告
  带 JSON 路径）；保存走 `POST /api/v1/scenarios`（multipart + 按内容哈希的 `Idempotency-Key`），成功后跳详情
  （详情页可直接「按此场景开局」）。编辑器只产出 JSON，不旁路校验、不直写库。
- 验收命令/断言（先定后写）：
  - `pnpm --dir web typecheck` → 通过
  - `pnpm --dir web test` → **68 files / 473 tests** 全绿（本批 +4 文件/+30 用例）
  - `pnpm --dir web build` → 通过；连续两次构建 `web/dist` SHA256 一致
  - `pytest tests/integration/test_frontend_contract.py -q` → **27 passed**
  - `node scripts/e2e-web.mjs --base-url http://127.0.0.1:8791` → **31/31 步、171/171 断言** PASS
- 不做什么：不做拖拽式可视化编辑器/条件树任意深度表单（深层用 JSON 原文编辑）；不引入表单/校验依赖；
  不新增后端端点（`validate`/`import` 复用）；不改 `TurnResult`/事件/状态/golden；不做云端分享。

## 2. 改动与提交

- 文件（新增 4 + 修改 8）：
  - 新增 `web/src/views/ScenarioEditorView.vue`（表单 + JSON 双向 + debounce 校验 + 保存/下载 + a11y）、
    `web/src/components/scenario-editor/ConditionEditor.vue`（递归条件表单：原子谓词/`all`/`any`/`not`/`always`）、
    `web/src/utils/scenarioEditor.ts`（草稿类型、`buildScenarioDefinition`/`parseDefinitionToDraft`、v1 版本门禁、
    条件骨架/节点计数、FNV-1a 内容哈希幂等键）、`web/src/utils/download.ts`（Blob 下载）
  - 修改 `web/src/router/index.ts`（`/scenarios/editor` 在 `:scenarioId` 之前）、`web/src/stores/scenarios.ts`
    （`importText` 支持按内容哈希的 `actionName`）、`web/src/views/{ScenarioLibrary,ScenarioImport}View.vue`
    （「新建场景（编辑器）」入口/分享引导）、`web/src/styles/theme.css`（编辑器样式；375px 单列）
  - 测试：新增 `web/tests/unit/{scenarioEditorUtils,conditionEditor,scenarioEditorView}.test.ts`，扩展
    `router.test.ts`；`scripts/e2e-web.mjs` 新增 `scenario.editor` 步
- 提交：未提交（工作区留 Lead 统一提交）；契约变更：无（复用 validate/import；只新增前端路由与客户端复用）。
- 护栏检查：无 `v-html`/富文本注入/脚本执行；无 `setInterval`（debounce 用 `setTimeout`）；零新增依赖；
  保存/校验均经服务端（不直写库）；未改后端 src、PROGRESS/CHANGELOG/`docs/开发文档.md`/decisions。

## 3. 验证结果

- 单测：`pnpm --dir web test` → **68 files / 473 tests 全绿**（本批新增 30 例：草稿↔JSON 映射/v2 字段/v1
  不升级/字段校验/条件编辑器/实时校验错误展示/保存幂等键/下载/v1 载入提示/粘贴 JSON/router 顺序）。
- typecheck/build：通过；`web/dist` 重建；连续两次 build 36 个产物 SHA256 完全一致。
- 契约测试：`pytest tests/integration/test_frontend_contract.py -q` → **27 passed**（新增编辑器路由顺序断言；
  export 路径/只读 GET/无幂等键断言与详情导出 UI 同批）。
- e2e：新增 `scenario.editor` 步——打开编辑器（375px 无横向溢出）→ 粘贴 v2 JSON（flags+多结局）→ 应用表单 →
  等实时校验通过 → 表单改标题（JSON 同步并重新校验通过）→ 保存（跳详情）→ 「按此场景开局」→ 对局徽章/目标
  面板显示编辑器场景；全量 **31/31 步、171/171 断言**，console 0 错误。
- 失败与回滚：无。默认离线路径（内置 4 场景/golden/demo/console）未触碰。

## 4. 下一步与风险

- 下一步：032 web 导出/导入 UI（同批已交付，见 `032-2026-11-12-scenario-share.md` §5）→ 035–036 示例内容包。
- 风险/开放问题：① 条件组合子超过 3 层时表单只读提示改用「JSON 原文」编辑（服务端仍完整校验；后续可做
  可视化深层编辑）；② 表单只编辑 v1/v2 已冻结字段（未知字段在载入时忽略，保存以表单为准——作者可用 JSON
  原文保留手写字段）；③ 实时校验每次编辑后 600ms 发一次只读请求（无自动重试、不写库），快速连续编辑会丢弃
  中间结果（序号保护只应用最新一次）。
- 需要人工决策：无（编辑器/分享拆分与「只产出 JSON」边界按 D-N5 执行）。
