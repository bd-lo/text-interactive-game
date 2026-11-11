# ADR · 前端视觉：Aurora Glass 双皮肤（日间默认）

- 状态：**已定案**｜决策人：用户（产品）｜实现：frontend-dev｜日期：2026-11-11
- 关联：第 23 章 §23.4；`docs/design/frontend-visual-refs.md`；`CHANGELOG.md [Unreleased]`
- 取代：旧「2G 深绿终端」皮肤（`web/src/styles/terminal.css` 已删除）

## 1. 背景

用户要求前端「更炫酷」，明确表示**不再考虑 2G 文字风格**；同时需要一个**日间主题**与**切换按钮**。
现状是零 UI 组件库（`vue` + `pinia` + `vue-router`），全部样式集中在单个全局 CSS。

## 2. 决策

1. **皮肤**：自建 **Aurora Glass**。日间 = 冷白底 + 粉彩极光；夜间 = 深靛底 + 霓虹极光；
   面板统一玻璃拟态（半透明 + 1px 高光边 + `backdrop-filter`，不可用时回退不透明）。
2. **默认日间**；顶栏「外观」按钮一键切换，视觉设置菜单另提供「跟随系统」。
3. **不引入 element-plus**：玻璃/极光/渐变/双皮肤均需自绘，组件库不提供对应能力，且其主题变量会形成第二套皮肤系统。
4. **token 化**：语义色 / 玻璃面 / 描边与阴影 / 主按钮 / 特效 / 圆角 / 字体 / 动效，全部在 `theme.css`；组件只读 token。
5. **首帧防闪色**：外链 `web/public/theme-init.js`（服务端 CSP `script-src 'self'`，且 `security-scan` 禁内联脚本）。
6. **换肤动效**：View Transition API + `clip-path: circle()`；不支持或 `prefers-reduced-motion` 时瞬时切换。
7. **字母系统**：`--font-ui` 系统无衬线（UI）、`--font-read` 系统衬线（叙事）、`--font-mono`（指令/JSON）。

## 3. 理由

- 现有 20 个组件与 15 个页面视图（合计 230 个 class，HEAD `27d85bb` 时点）全部依赖全局样式，**无需改模板**即可换肤，改动面可控。
- 玻璃 + 极光只需 CSS 与已有 Canvas 层，**新增运行时依赖 0**，首屏 gzip 保持在预算内。
- 双皮肤用 CSS 变量切换，`data-theme` 只写 `light|dark`，`auto` 在 JS 解析，CSS 无需理解三态。

## 4. 影响

- 旧 CRT 扫描线/暗角/glitch/余烬粒子组件删除（`CrtOverlay.vue`、`EmberField.vue`），
  由 `AtmosphereOverlay.vue`、`MoteField.vue` 替代；`stores/visual.ts` 的 `crtEnabled`/`glitchEnabled`
  改名 `atmosphereEnabled`/`spotlightEnabled`。
- 文档：第 23 章 §23.4 整节重写；`docs/design/frontend-visual-refs.md` 落地表更新。
- 测试：新增 `theme.test.ts`/`themeToggle.test.ts`/`moteField.test.ts`；`visual.test.ts`、`visualOverlays.test.ts` 同步更新（覆盖 `atmosphereEnabled`/`spotlightEnabled` 新口径）。

## 5. 红线（验收）

对比度（实测）：日间 fg/bg 16.63、dim/bg 6.20、accent/bg 5.87、按钮 6.29；
夜间 fg/bg 17.71、dim/bg 9.30、accent/bg 9.87、按钮 5.67。
`prefers-reduced-motion` 全静态；`prefers-reduced-transparency`/无 `backdrop-filter` 回退不透明；
`forced-colors` 交还系统色；`security-scan` 保持绿；`web/dist` 重建入库。
