# R4 · 前端视觉双皮肤（Aurora Glass：日间默认 / 夜间 + 顶栏切换）

> 编号：`R4`（范围外玩家体验批次，不占 001–028 编号；按 `docs/progress/README.md` 例外条款登记）
> 日期：2026-11-11（Asia/Shanghai）
> 决策：[ADR · Aurora Glass 双皮肤](../decisions/2026-11-11-aurora-glass-theme.md)（D-UI-3）
> 关联：开发文档第 23 章 §23.4（整节重写）、第 16 章目录树、`docs/design/frontend-visual-refs.md`

## 1. 目标与判定口径

用户指令（2026-11-11）：当前前端是夜间风格，需要**日间主题 + 切换按钮**，并要求「最炫酷」「不要考虑 2G」。
判定口径：

1. 旧「2G 深绿终端」皮肤**彻底移除**（`terminal.css` 删除，CRT 扫描线 / 暗角 / glitch / 余烬配色不再存在）。
2. 提供 **日间（默认）** 与 **夜间** 两套皮肤，顶栏「外观」按钮一键切换，菜单可选「跟随系统」。
3. 零新增运行时依赖；`security-scan`（禁 `v-html`/`innerHTML`/`eval`/`EventSource`/`setInterval`、禁内联脚本）保持绿。
4. 对比度 ≥4.5:1；`prefers-reduced-motion` 全静态；无 `backdrop-filter` 时回退不透明；375px 不溢出。
5. `web/dist` 重建入库（CI drift 门禁）。

## 2. 交付内容

| 方向 | 交付 | 关键位置 |
| --- | --- | --- |
| 皮肤 token | `:root` = 日间、`:root[data-theme='dark']` = 夜间；语义色 / 玻璃面 / 描边阴影 / 主按钮 / 特效 / 圆角 / 字体 / 动效分层 | `web/src/styles/theme.css`（原 `terminal.css` 删除） |
| 主题状态 | `light|dark|auto` 三态、持久化 `textgame:theme-settings`、解析结果写 `<html data-theme>` + `meta[theme-color]`、`auto` 监听 `prefers-color-scheme` | `web/src/stores/theme.ts` |
| 首帧防闪色 | 外链同步脚本，任何绘制前读 localStorage 写好 `data-theme`（CSP `script-src 'self'` 下唯一可行方案） | `web/public/theme-init.js`、`web/index.html` |
| 切换按钮 | 顶栏「外观」（太阳/月亮图标 + `aria-pressed`）；支持 View Transition 时从点击位置 `clip-path: circle()` 圆形扩散，`reduced-motion` 瞬切 | `web/src/components/ThemeToggle.vue` |
| 氛围层 | 4 团极光（只动 `transform`）+ 细颗粒 + 鼠标聚光（rAF 节流）；Canvas 微光粒子按主题换调色板与混合模式 | `AtmosphereOverlay.vue`、`MoteField.vue`（懒加载） |
| 视觉设置 | 菜单分「外观（日间/夜间/跟随系统）」与「动效（全特效/低功耗/关闭）」两组 | `web/src/components/VisualSettings.vue` |
| 布局/排版 | 玻璃拟态 sticky 顶栏、圆角面板、渐变标题、pill 按钮 + hover 扫光、`--font-ui`/`--font-read`/`--font-mono` 三字体栈 | `theme.css`、`App.vue` |
| 测试 | `theme.test.ts` 9 例、`themeToggle.test.ts` 5 例、`moteField.test.ts` 6 例；`visual.test.ts`/`visualOverlays.test.ts` 同步改名 | `web/tests/unit/**` |

## 3. 验证结果（实跑）

- `pnpm --dir web test`：**58 files / 365 tests 全绿**（基线 56 files/347；后续 R5 技术风险修复批次新增前端用例后，全量为 **60 files / 383 tests**（Cline 当步 379 + R-23 回归修复 4 例），见 R5 进度文档与 `CHANGELOG.md [Unreleased]`）。
- `pnpm --dir web typecheck`：通过（vue-tsc，无输出）。
- `pnpm --dir web build`：通过，`web/dist` 重建；`theme-init.js` 随 `public/` 拷贝到 `dist/` 根。
- 无头 Edge 实拍验证：日间 / 夜间两套皮肤均正常渲染（顶栏玻璃、极光背景、粒子、渐变标题、按钮对比度）。
- 对比度实测：日间 fg/bg 16.63、dim/bg 6.20、accent/bg 5.87、按钮 6.29；夜间 fg/bg 17.71、dim/bg 9.30、accent/bg 9.87、按钮 5.67。
- 安全基线：`security-scan.test.ts` 绿（index.html 仍无内联脚本，主题脚本为外链 `src`）。

## 4. 遗留与后续

1. Playwright/axe 仍后移（与 D-M6-11 一致），本批以 vitest + 无头浏览器截图 + 手工走查验收。
2. 深色下 1px 玻璃描边在部分低端屏可能偏淡（已用 `--border-strong` 兜输入框对比度）；如需再强化可加 ADR 调整 token。
3. 若后续要「第三套皮肤」（如高对比），只需新增一组 token 覆盖 + 菜单项，无需改组件。
