# 042 · 2026-11-12 · N2 · 网页 a11y：双主题对比度与 reduced-motion

> 切片目标一句话：把 Aurora Glass 日/夜两套皮肤的可读性与动效偏好收口到 WCAG 2.1 AA，并让系统
> 「减少动态效果」完整降级但功能不减。｜ 验收命令：见 §1 ｜ 里程碑 N2（第 17 章 §17.9.2）｜ 编号区间 037–044 ｜
> 执行/验收：web-a11y ｜ 关联：041（键盘/焦点）、`docs/design/a11y-checklist.md` §3–§4

## 1. 目标与验收命令

- 目标（一个垂直小功能，≤90 分钟）：夜间低对比文本/边框修正 + 焦点环增强 + 打字机/粒子/极光/过渡在
  `prefers-reduced-motion` 下静态降级；`web/dist` 重建；不改视觉基调与零新增依赖。
- 验收命令/断言（先定后写）：
  - `pnpm --dir web typecheck` → 通过
  - `pnpm --dir web test` → 62 files / 419 tests 全绿（含对比度 token 回归 + reduced-motion 分支）
  - `pnpm --dir web build` → 通过；重复构建产物 hash 稳定（tracked `web/dist` 无二次漂移）
  - `node scripts/e2e-web.mjs --base-url http://127.0.0.1:8791` → 28/28 步、145/145 断言 PASS
- 不做什么：不引入 Playwright/axe；不改主题结构/token 分层；不调整 043–044 移动端布局。

## 2. 改动与提交

- 文件：`web/src/styles/theme.css`（对比度 token/焦点环/图标/世界书 key；无结构调整）、
  `web/src/components/VisualSettings.vue`（减少动态说明 `role="status"`）、
  `web/tests/unit/a11yContrastMotion.test.ts`（新，18 例）、`web/tests/unit/visualOverlays.test.ts`、
  `scripts/e2e-web.mjs`（新增 `a11y.reduced-motion` 步 + 键盘/CDP 模拟助手）、`web/dist`（重建）。
- 关键 diff：`--border-strong` 日间 α0.22→0.50（1.60→3.38:1）、夜间 α0.20→0.36（1.93→3.23:1）；
  `--ring` 日间 55%→90%（2.46→4.76:1）、夜间 70%→90%（3.9–5.20→5.64–8.12:1）；
  太阳图标 `#d97706`→日间 `#b45309`（2.89→4.56:1）/夜间 `#fbbf24`（7.93:1）；
  `.world-key` 文字 `--accent-3`（2.59:1）→`--accent`（5.71:1）；输入框补 `:focus-visible` 强环。
- 护栏检查：仅 token/局部选择器与降级说明；`prefers-reduced-motion`/`reduced-transparency`/`forced-colors`
  既有降级链保留并加断言；无新依赖、无 `v-html`、未动后端/契约。
- 契约变更：无。

## 3. 验证结果

- 对比度（WCAG 相对亮度公式，半透明先合成）：日间 fg/bg 16.63、dim/bg 6.20、accent 5.87、按钮 6.29/5.70、
  占位符 4.61、边界 **3.38**、焦点环 **4.76**、世界书 key **5.71**、太阳图标 **4.56**；
  夜间 fg/bg 17.71、dim/bg 9.30、accent 9.87、按钮 5.67/7.32、占位符 5.54、边界 **3.23**、
  焦点环 **5.64**、太阳图标 **7.93**。完整清单见 `docs/design/a11y-checklist.md` §4。
- 单测：`pnpm --dir web test` → **62 files / 419 tests 全绿**；新文件 `a11yContrastMotion` 18 例
  （双主题 11 条对比度/回归 + reduced-motion 4 条 + live region/chips 语义 3 条）。
- typecheck/build：`pnpm --dir web typecheck` 通过；`pnpm --dir web build` 产物 `dist/assets/index-*.js`
  60.23kB（gzip 23.52kB）、CSS 配套文件重建，未引入新 chunk 依赖。
- 真实浏览器 e2e：新增 `a11y.reduced-motion` 步骤 10 条断言——`Emulation.setEmulatedMedia` 模拟 reduce 后
  `matchMedia=true` 且 `html[data-reduced-motion=true]`、`.mote-field` 不渲染、`.aurora-blob` computed
  `animation-name=none`、聚光 `display=none`、冒险记录正文完整、打字机不启动、发送按钮可用、`/` 仍能聚焦、
  恢复默认偏好后 `matchMedia=false`；全流程 console 0 错误。
- 失败与回滚：无。

## 4. 下一步与风险

- 下一步：043–044 · mobile-polish（375px 回归/触控目标/长文本换行），验收命令 `node scripts/e2e-web.mjs`。
- 风险/开放问题：`--border` 装饰描边与禁用态透明度按 WCAG 豁免**保留**（理由见清单 §4），若未来把描边用于
  「识别控件边界」需同步抬到 3:1；渐变字依赖 `background-clip:text`，强制颜色模式已交还系统色。
- 需要人工决策：无；若用户要求公开部署/跨浏览器，按 055 ADR §6 复评 Playwright + axe。
