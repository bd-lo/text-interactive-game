# 网页可访问性审计清单（WCAG 2.1 AA）· N2 041–042

> 审计范围：`web/src/`（Vue 3 SPA：对局页、首页、卡库/场景/世界书、设置页、全局顶栏与视觉设置、退出入口）。
> 依据：WCAG 2.1 AA（1.4.3 文本对比、1.4.11 非文本对比、2.1.1 键盘、2.4.7 焦点可见、2.4.2 页面标题、
> 4.1.2 名称/角色/值）；第 23 章 §23.4/§23.7/§23.8；决策 D-N7（不引入 Playwright 长期 CI 矩阵）、
> D-M6-11（e2e 零依赖 CDP）、D-LA-4（启动器心跳例外）。审计日期 2026-11-12，基线 `553d0a0`。
> 方法：源码逐项审查 + vitest/jsdom 回归（`web/tests/unit/a11y*.test.ts`）+ 真实 Edge CDP e2e 走查
> （`scripts/e2e-web.mjs` 新增 `a11y.keyboard` / `a11y.reduced-motion` 两步）+ 按 WCAG 相对亮度公式
> 计算对比度（脚本口径写入 `a11yContrastMotion.test.ts`，防 token 回退）。
> 状态标记：**已修** = 本批改动；**已满足** = 现状已达标并加回归断言；**保留** = 不改，附理由。

## 1. 键盘可达性与焦点管理

| 项目 | 现状 / 本批修复 | 验证 | 状态 |
| --- | --- | --- | --- |
| 全局跳过链接 | `App.vue` 首个可聚焦元素 `a.skip-link[href="#main-content"]`，聚焦时可见 | `a11yLandmarks`（firstFocusable=sr skip-link）+ e2e Tab 第一站 | 已满足 |
| 主区域 landmark | `main#main-content[tabindex="-1"]`、`header`/`nav[aria-label]`/`footer` 齐全 | `a11yLandmarks` + e2e landmark 断言 | 已修（补 tabindex） |
| 路由切换焦点 | `router.afterEach`：非首次导航把焦点交给 `main#main-content`；首屏不抢焦点（跳过链接仍是第一站） | `a11yLandmarks` 路由用例 + e2e 跳过链接 Enter→main | 已修 |
| 页面标题 | 每条路由 `meta.title`，切换后写 `document.title=<标题> · TEXTGAME`（2.4.2） | `a11yLandmarks` 断言 `设置 · TEXTGAME` | 已修 |
| 指令输入（Enter） | `Enter` 提交、`Shift/Ctrl/Cmd+Enter` 换行、IME 组字不提交；忙碌用 `readonly` 保持焦点 | `commandInput.test.ts`（含新 6 例）+ e2e 键盘 Enter 提交 turn+1 | 已满足（补 Ctrl/Cmd） |
| 指令输入（历史/Esc/`/`） | `↑/↓` 翻阅本地已发送历史（空或光标首/尾才接管）、`Esc` 取消草稿保持焦点、任意位置 `/` 聚焦指令框 | `commandInput.test.ts` 新增 4 例 + e2e `/` 聚焦 | 已修 |
| 动作 chips / 建议 | 原生 `<button type="button">`，Tab/Enter/Space 可用；`role="group"` + 可读名称；禁用时 `disabled` | `a11yContrastMotion` chips 用例 + `gameView.test.ts` | 已修（补 group 语义） |
| 视觉设置菜单 | `<details>`：展开后焦点进入第一个选项；`Esc` 关闭并把焦点归还 `<summary>` | `visualOverlays.test.ts` 新增 2 例 + e2e 菜单 Esc | 已修 |
| 退出游戏确认 | 确认面板打开即聚焦「确认退出」；`Esc` 取消；关闭后焦点归还「退出游戏」 | `quitGameControl.test.ts` 新增 3 例 + e2e app.exit | 已修 |
| 已退出覆盖层 | `tabindex="-1"` 接管焦点，`aria-labelledby` 关联标题 | `quitGameControl.test.ts` 覆盖层用例 | 已修 |
| 设置页二次确认 | 自由制造风险确认（`role="alertdialog"`）、清除本机 key：打开聚焦确认按钮，`Esc` 取消，焦点归还触发控件 | `settingsView` 手工走查（jsdom 无对应用例）+ 代码审查 | 已修 |
| 键盘陷阱 | 无（不使用焦点锁；面板均为内联/`details`，Tab 可自由离开；覆盖层是终态页面） | 源码审查 | 已满足 |
| 焦点可见 | 全局 `:focus-visible` → `--ring`（2 层：2px 背景色间隙 + 2px 强调环）；输入框额外 `:focus-visible` 强环；`.skip-link:focus-visible` | `a11yContrastMotion` 焦点环对比度断言 | 已修 |

## 2. ARIA 角色与标签 / live region

| 项目 | 现状 / 本批修复 | 验证 | 状态 |
| --- | --- | --- | --- |
| 导航/主区域 | `nav[aria-label="主导航"]`、`main`、`header`、`footer` 语义标签（原生 landmark） | e2e + `a11yLandmarks` | 已满足 |
| 叙事文本流 | 冒险记录 `role="log"` + `aria-live="polite"` + `aria-relevant="additions"`（只播报追加行） | `a11yContrastMotion` + `narrativeHistory` 用例 | 已修（补 role=log） |
| 调试文本流 | `TurnTextStream` `role="log"` + `aria-live="polite"`（仅 dev 诊断面板内） | `a11yContrastMotion` | 已修 |
| 错误提示 | `ErrorBanner` `role="alert"`（断言式，仅在插入时播报一次） | `a11yContrastMotion` + `errorBanner` 用例 | 已满足 |
| 状态提示 | 加载/空态用 `role="status"`（隐式 polite），无自定义 aria-live 冗余 | 各视图源码审查 | 已满足 |
| 重复播报治理 | 结局屏去掉 `aria-live`（结局正文由冒险记录 role=log 播报一次），改为 `role="region" aria-labelledby` | `gameView.test.ts` 新增结局屏用例 | 已修 |
| 按钮状态 | 主题切换/特效开关 `aria-pressed`；自由制造 `role="switch"` + `aria-checked`；忙碌表单 `aria-busy` | `themeToggle`/`visualOverlays`/`commandInput` 用例 | 已满足（补 aria-busy） |
| 焦点/快捷键提示 | 指令框 `aria-keyshortcuts="Enter Escape ArrowUp ArrowDown"`、`aria-describedby` 指向操作提示 | `commandInput` 用例 | 已修 |
| 面板名称 | 各 `section` 有 `aria-label`（冒险记录/当前目标/玩家状态/最近回应/可以试试等） | 源码审查 | 已满足 |
| 装饰隔离 | 极光/粒子 `aria-hidden="true"`；品牌词视觉层 `aria-hidden` + `sr-only` 全名 | `visualOverlays`/`moteField` 用例 | 已满足 |

## 3. `prefers-reduced-motion`（系统「减少动态效果」）

| 动效 | 降级行为 | 验证 | 状态 |
| --- | --- | --- | --- |
| 叙事打字机 | 命中偏好 → 立即整段显示完整文本、不启动 rAF、无跳过按钮；运行中偏好变化也立即补全 | `a11yContrastMotion` 2 例 | 已满足（补运行中降级用例） |
| Canvas 粒子（MoteField） | `particlesEnabled=false` → App 不渲染该层；组件内部也拒绝启动 | `visual` store 用例 + CSS/源码 | 已满足 |
| 极光 / 聚光 | `html[data-reduced-motion='true'] .aurora-blob{animation:none}`、聚光层 `display:none` | e2e reduced-motion（computed animation-name=none / display=none） | 已满足 |
| reveal / 页面过渡 / 按钮扫光 | 全局 `@media (prefers-reduced-motion: reduce)` 把所有 animation/transition 压到 0.001ms；`v-reveal` 直接可见 | `visualOverlays` reveal 用例 + CSS 断言 | 已满足 |
| 换肤 View Transition | `stores/visual.systemReducedMotion` 命中时不调用 `startViewTransition`，立即换肤 | `themeToggle.test.ts` | 已满足 |
| 系统偏好监听 | `matchMedia('(prefers-reduced-motion: reduce)')` init 探测 + `change` 实时监听 + `<html data-reduced-motion>` | `visual.test.ts`/`a11yContrastMotion` | 已满足 |
| 设置页告知 | 视觉设置内 `role="status"` 说明「已自动降级、正文仍完整」，不误导玩家以为设置失效 | `visualOverlays` 用例 | 已修（补 role=status） |
| 透明度/强制颜色 | `prefers-reduced-transparency` 回退不透明底；`forced-colors: active` 交还系统色 | CSS 断言 | 已满足 |

## 4. 双主题对比度清单（WCAG 2.1 AA：正文 ≥4.5:1，UI 边界/焦点 ≥3:1）

计算口径：半透明色先按 sRGB 合成到实际底色，再按 WCAG 相对亮度公式算比值；日间底 `#f6f7fb`、
面板 `#fefeff`；夜间底 `#070814`、面板 `#1f1f2a`、输入框底 `#24252f`。

| 用途（token） | 日间 | 夜间 | 结论 / 状态 |
| --- | --- | --- | --- |
| 正文 `--fg` / 页面底 | **16.63** | **17.71** | 远超 4.5 — 已满足 |
| 次要文字 `--fg-dim` / 页面底 | **6.20** | **9.30** | ≥4.5 — 已满足 |
| 次要文字 / 玻璃面板 | 6.59 | 7.61 | ≥4.5 — 已满足 |
| 强调色文字 `--accent` / 底 | 5.87 | 9.87 | ≥4.5 — 已满足 |
| 危险色 `--danger` / 底 | 6.07 | 8.88 | ≥4.5 — 已满足 |
| 警告色 `--warn` / 底 | 5.54 | 14.18 | ≥4.5 — 已满足 |
| 主按钮文字 / 渐变两端 | 6.29 / 5.70 | 5.67 / 7.32 | ≥4.5 — 已满足 |
| 输入框占位符（dim@85%） | **4.61** | **5.54** | ≥4.5 — 已满足（日间余量小，勿再降 dim） |
| 世界书 key 徽章文字 | 2.59（旧 `--accent-3`）→ **5.71**（`--accent`） | 11.95 → 6.56 | **已修**：日间 accent-3 不达标，改用强调色 |
| 主题太阳图标（状态信息） | 2.89（旧 `#d97706`）→ **4.56**（`#b45309`） | 7.93（`#fbbf24`） | **已修**：旧色在日间 <3:1 |
| 输入框/次要按钮/胶囊边界 `--border-strong` | 1.60 → **3.38**（α .22→.50） | 1.93 → **3.23**（α .20→.36） | **已修**：1.4.11 非文本对比 ≥3 |
| 键盘焦点环 `--ring`（2 层第二层；取最差相邻面） | 2.46 → **4.76–5.12**（55%→90%） | 3.9–5.20 → **5.64–8.12** | **已修**：可见焦点更明确 |
| 错误横幅文字 / `danger-soft` 面 | 5.59 | 6.48 | ≥4.5 — 已满足 |
| 警告条文字 / `warn-soft` 面 | 5.12 | 9.56 | ≥4.5 — 已满足 |
| 装饰性描边 `--border`（卡片/分隔线） | 1.5–1.7 | 1.5–1.7 | **保留**：仅装饰分隔，不承担「识别控件」职责（1.4.11 不适用）；控件边界已由 `--border-strong` 达标 |
| 禁用内容（`.btn:disabled` 45%、`.world-entry-disabled` 55%） | ≈2.45 | ≈3.4 | **保留**：WCAG 1.4.3 明确豁免「未启用控件」；禁用态不承载必须阅读的信息 |
| 渐变字/品牌词（`.app-wordmark` 等） | accent 5.87 / accent-2 5.32 | 9.87 / 10.79 | **保留**：品牌字属 1.4.3 logo 豁免；正文标题为常规渐变文字且两端均 ≥5.3，达标 |

## 5. 兼容与降级（保留项）

- **无玻璃模糊**：`@supports not (backdrop-filter)` 回退 `--panel-solid`；`prefers-reduced-transparency` 同步回退（对比度按实心底计算仍达标）。
- **强制颜色模式**：渐变文字交还 `CanvasText`，控件边界交还 `ButtonBorder`，不覆盖系统高对比。
- **中文 IME**：组字期间 Enter/Esc 不劫持；中文候选取消交浏览器。
- **无 IntersectionObserver / 无 matchMedia**：`v-reveal` 直接可见、视觉 store 保守按「减少动态」处理（绝不因探测失败播放动画）。
- **未做的自动化**：Playwright + axe 自动扫查按 D-N7 不引入长期 CI 矩阵（055 已评估）；本清单以 vitest 回归 + 真实浏览器 CDP 走查为权威证据。

## 6. 验收证据（本批实测）

- `pnpm --dir web test` → **62 files / 419 tests** 全绿（本批新增 2 个测试文件、扩展 4 个文件，+36 用例）。
- 新增 a11y 断言 ≥80 条，覆盖键盘交互、aria 角色/live region、焦点管理、reduced-motion 分支、双主题对比度回归。
- `pnpm --dir web typecheck`、`pnpm --dir web build` 通过（`web/dist` 重建，见进度文档 042）。
- 真实浏览器 e2e（Edge 157 headless + uvicorn 127.0.0.1:8791）→ **28/28 步、145/145 断言 PASS**；
  新增 `a11y.keyboard`（Tab 首站跳过链接→Enter 聚焦 main、landmark、视觉设置 Esc 焦点归还、键盘 Enter 提交回合）
  与 `a11y.reduced-motion`（模拟 reduce：粒子不渲染、极光 animation-name=none、聚光 display=none、打字机不启动、
  `/` 聚焦与发送按钮仍可用）。
- 安全基线不回退：`security-scan.test.ts` 既有禁令（`v-html`/`innerHTML`/`eval`/SSE/CDN/内联脚本）未放宽。

## 7. 后续

- 043–044 移动端打磨可沿用本清单：触控目标 ≥44px 已在 `@media (max-width:480px)` 有基础，需补 375px 键盘/触控走查。
- 若后续用户要求公开部署/跨浏览器支持，再按 055 ADR 第 6 节触发 Playwright + axe 复评；在此之前新增 UI 必须保持本清单的 vitest 断言全绿。
