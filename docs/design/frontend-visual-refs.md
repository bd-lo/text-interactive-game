# 前端炫酷化：开源炫技效果调研与许可证矩阵（H4/R4）

> 目标：把「炫技类项目」的视觉**缝**进本项目 Web 端（文字 MUD → **Aurora Glass**：极光渐变 +
> 玻璃拟态 + 微光粒子；2026-11-11 起替代旧的 CRT/深绿终端皮肤），
> 满足 H4-BRIEF §2A 硬约束：**零运行时依赖优先**、首屏 gzip 增量 ≤100KB（特效懒加载）、
> 375px 不溢出、对比度不降、`prefers-reduced-motion` 降级、可一键「关闭特效」并持久化、
> `security-scan`（禁 `v-html`/`innerHTML`/`eval`/`EventSource`/`setInterval`）不破。
>
> **明确不参考 SillyTavern UI（用户两次否决，嫌丑）**。本文只调研视觉/动画技法与许可证。
> 结论日期 2026-11-11；许可证字段以 GitHub API `license.spdx_id`（实测）与站点声明为准。

## 0. 结论速览（给实现者的 60 秒版）

1. **默认零新增依赖**：余烬粒子用**自写 Canvas 2D**（约 120 行，rAF + DPR 上限 + 低功耗降级）；
   CRT 扫描线/辉光/轻微色差/噪点用**纯 CSS**（伪元素 + `mix-blend-mode` + 内联 SVG feTurbulence 噪点）；
   打字机用 CSS `steps()`；面板/历史/目标完成/结局用 CSS keyframes + `TransitionGroup` + `IntersectionObserver`。
2. **不引入 three.js / TresJS**：three 0.187.0 单包 **742.3KB / gzip 185.7KB**（bundlephobia 实测），
   已超首屏预算，即便懒加载也是大 chunk；本项目效果不需要 WebGL 3D。
3. **不引入 @vueuse/motion（gzip 14.4KB + 6 依赖，含 @vueuse/core 187KB 源码）/ anime.js（v4 gzip 36.6KB）/
   tsparticles（engine gzip 14.96+11.35KB）/ vue-kinesis（gzip 4.4KB）/ auto-animate（gzip 2.9KB）**：
   这些库的**思路**可借鉴，但效果用 CSS/原生 API 重写成本更低、包更小、可控性更强。
4. **可直接借鉴技法（MIT）**：retrozone（CRT 显示器参数）、Codrops `CSSGlitchEffect`/`GlitchPerspective`
   （glitch 切片技法；站点声明 demo MIT，但仍建议**重写**）、three.js examples（仅 shader 技法参考）。
5. **许可禁用**：GPL/AGPL/无许可/NC —— SillyTavern（AGPL-3.0）代码**禁止复制**（只读格式语义，见
   `world-book-format-compat.md`）；Awwwards/Webzibition 作品**无代码许可**（仅观感参考）；CCv2 spec 仓库
   **无 LICENSE**（只读字段语义，不复制文本）。

## 1. 许可证判定规则（本项目采用）

| 判定 | 许可证 | 处置 |
| --- | --- | --- |
| 允许 | MIT、Apache-2.0、BSD-2/3-Clause、ISC、CC0 | 可借鉴/内联，需在本文「来源表」登记；内联代码需保留版权与许可声明 |
| 禁止 | GPL-2/3、AGPL-3、LGPL、SSPL、CC-BY-NC(-SA)、UNKNOWN/无 LICENSE | **不得复制代码/文案/素材**；只允许读语义并完全重写（如 SillyTavern 格式语义） |
| 仅观感 | Awwwards/Webzibition、闭源站点 | 只能看「观感/交互节奏」，不得取用代码/素材 |

验证方法：① GitHub API `/repos/{owner}/{repo}` 的 `license.spdx_id`；② 仓库 `LICENSE` 文件全文；
③ 站点 License 页（如 Codrops）。三项冲突时取**更严格**者，并在表中注明冲突。

## 2. 来源表（实测 2026-11-11）

| # | 来源/仓库 | 链接 | 许可证（实测） | 包体（gzip，bundlephobia 实测） | 可借鉴点 → 结合方式 | 引入依赖？ |
| --- | --- | --- | --- | --- | --- | --- |
| S1 | **TresJS**（Vue3 的 three.js 声明式渲染器） | https://github.com/Tresjs/tres | **MIT** | 依赖 three（见 S2） | 组件化 3D 场景的声明式 API 设计；本项目不用 3D，仅参考「懒加载特效层」组织方式 | ❌ 不引入 |
| S2 | **three.js** | https://github.com/mrdoob/three.js | **MIT** | three@0.187.0 **185.7KB**（742.3KB raw） | WebGL 粒子/shader/postprocessing 技法参考；**超首屏预算**，仅当未来需要真 3D 时按「懒加载 chunk + 预算评审」再议 | ❌ 不引入 |
| S3 | **@vueuse/motion** | https://github.com/vueuse/motion | **MIT** | 14.4KB（6 依赖，含 @vueuse/core） | 声明式 `v-motion` 思路、spring/easing 命名；用 CSS `transition`/`@keyframes` + 交错 `animation-delay` 重写 | ❌ 不引入 |
| S4 | **tsParticles**（含 Vue3 组件） | https://github.com/tsparticles/tsparticles | **MIT** | vue3 wrapper 1.0KB；engine **14.96KB + 11.35KB** | 「粒子参数配置对象」思路（数量/速度/透明度/连线）；**自写 Canvas 2D 余烬粒子**（无连线、低功耗） | ❌ 不引入 |
| S5 | **vue-kinesis** | https://github.com/amineyarman/vue-kinesis | **MIT** | 4.4KB，0 依赖 | 鼠标视差（多层位移）思路；用 `pointermove` + rAF + CSS `transform: translate3d` 自写（仅桌面 hover 启用） | ❌ 不引入 |
| S6 | **@formkit/auto-animate** | https://github.com/formkit/auto-animate | **MIT** | 2.9KB，0 依赖 | FLIP 列表进出场思路；用 `<TransitionGroup>` + CSS transition（叙事历史/条目表/卡库列表） | ❌ 不引入 |
| S7 | **anime.js v4** | https://github.com/juliangarnier/anime | **MIT** | 36.6KB，0 依赖 | 时间轴/stagger/SVG 描边思路；用 CSS keyframes + `animation-delay` 交错重写 | ❌ 不引入 |
| S8 | **Codrops `CSSGlitchEffect` / `GlitchPerspective`** | https://github.com/codrops/CSSGlitchEffect · https://github.com/codrops/GlitchPerspective | Codrops **License 页声明 demo 为 MIT**；但仓库本身**无 LICENSE 文件**（第三方数据 API `license=null`） | 纯 CSS | glitch 标题/结局光晕收束：`clip-path` 切片 + 双伪元素位移 + `steps()` 抖动（**重写**，不复制代码） | ❌ 不引入（仅技法） |
| S9 | **TheMarco/retrozone** | https://github.com/TheMarco/retrozone | **MIT** | 纯 JS/CSS | CRT 显示器参数（扫描线间距、荧光余晖、暗角、轻微弯曲/噪点）；借鉴参数后**重写为纯 CSS 层** | ❌ 不引入 |
| S10 | **MDN Web Docs**（`prefers-reduced-motion`、Canvas 2D、CSS `steps()`） | https://developer.mozilla.org/ | 文档 CC-BY-SA 2.5；**代码示例 CC0** | — | 无障碍降级与 Canvas 动画标准写法（自写实现，不复制文案） | ❌ 不引入 |
| S11 | **Awwwards / Codrops Webzibition** 获奖站 | https://www.awwwards.com/ · https://tympanus.net/codrops/hub/ | **无代码许可**（作品版权归作者） | — | 仅「第一眼气质/节奏」观感参考（暗色 + 单一强调色 + 慢速氛围动效）；**不取用代码/素材** | ❌ 禁用代码 |
| S12 | **VueUse core**（`useReducedMotion`/`useMediaQuery` 等） | https://github.com/vueuse/vueuse | **MIT** | 大（按需 tree-shake） | 组合式 API 习惯：自写 ~20 行 `useReducedMotion()`/`useEffectsEnabled()`（localStorage） | ❌ 不引入 |
| S13 | **Codrops 其他 demo（glitch/scanline 教程源码）** | https://tympanus.net/codrops/licensing/ | 站点声明 **MIT**（免费资源另有「不可再分发」限制） | — | 若确需内联，必须保留 `Copyright (c) Codrops` MIT 声明；**默认仍重写** | ⚠️ 仅按需 |

## 3. 「缝合」方案 → 本项目落点（**H4 已落地**的最终形态）

> 实现结果（2026-11-11）：全部效果层零新增运行时依赖；粒子组件懒加载 chunk **1.49KB gzip**；
> 首屏 gzip **51.7KB**（相对 H4 前增量 **≈+8.9KB**，在 ≤100KB 预算内）；CDP 实测 60fps、
> long task 0、375px 无溢出；`security-scan` 保持绿。下表「落地文件」为最终路径。

| 效果层 | 参考 | 落地文件（最终） | 实现要点 | 验收 |
| --- | --- | --- | --- | --- |
| 全屏余烬/星尘粒子 | S4 思路 + 自写 | `web/src/components/MoteField.vue`（懒加载） | Canvas 2D；rAF 单循环；DPR ≤2；`visibilitychange` 暂停；低功耗档 20fps/粒子按视口面积收缩；移动端更少；**按主题切换调色板与混合模式**（夜间 `lighter` 霓虹 / 日间 `source-over` 淡彩）；`prefers-reduced-motion`/关闭档不渲染 | 懒加载 chunk ~1.6KB gzip；长任务 0；关闭后无 rAF |
| 极光氛围层（4 团极光 + 颗粒 + 聚光） | S9/S10 思路 + 自写 | `web/src/components/AtmosphereOverlay.vue` + `web/src/styles/theme.css` | 纯 CSS：`radial-gradient` 色团只动 `transform`（不上 `filter: blur`）；`feTurbulence` 内联噪点；鼠标聚光由 `pointermove` + rAF 写 CSS 变量（仅全特效档）；关闭档不渲染 | 文字对比度不降；375px 不溢出；无长任务 |
| 日/夜双皮肤与切换 | — | `web/src/stores/theme.ts`、`components/ThemeToggle.vue`、`public/theme-init.js`、`styles/theme.css` | `light|dark|auto` 三态；`<html data-theme>` + `meta[theme-color]`；首帧脚本防闪色；View Transition 圆形扩散换肤 | 刷新无闪色；`reduced-motion` 瞬切；两套皮肤对比度 ≥4.5:1 |
| 打字机叙事 | S10 | `web/src/components/TypewriterText.vue` + `NarrativeHistory.vue` | 视觉逐字层 `aria-hidden`，读屏始终拿 `sr-only` 完整文本；点击/回车跳过；关闭/低功耗/reduced-motion 立即整段显示 | 跳过可用；读屏不重复播报；关闭后无动画 |
| 面板/历史/目标完成/结局动效 | S6/S7 思路 | `NarrativeHistory.vue`、`GoalPanel.vue`、`GameView.vue`（结局面板）、`router` 页面切换 | CSS transition/animation + `TransitionGroup`；目标完成一次高亮；结局光晕收束 | 不阻塞输入；reduced-motion 全静态 |
| 视觉设置开关（三档） | — | `web/src/components/VisualSettings.vue` + `web/src/stores/visual.ts` | 三档：**全特效 / 低功耗 / 关闭特效**；持久化 `localStorage`（`textgame:visual-settings`）；`data-visual`/`data-reduced-motion` 挂 `<html>` 供纯 CSS 降级；检测到系统「减少动态效果」自动降级并提示 | 刷新保持；关闭即时生效；`off` 只留纯文本界面 |
| 鼠标视差（可选） | S5 | —（本轮未落地） | 评估后不做：收益低、触屏不可用、增加输入处理面；保留为 P2 候选 | — |
| 标题/结局 glitch | S8 思路 | 结局面板光晕收束（CSS） | glitch 仅用于极短瞬间，避免持续抖动影响阅读；不做全站故障风 | 不干扰阅读；可关 |

## 4. 性能/无障碍/安全红线（验收口径）

1. 首屏 gzip 增量 ≤100KB：特效层**不得**进首屏 chunk；粒子组件用 `defineAsyncComponent` 懒加载，
   或纯 CSS 部分直接进样式（CSS 不计入 JS 预算但需控制 <8KB gzip）。
2. 无长任务（PerformanceObserver longtask >50ms 为 0 或极少）；无掉帧（rAF 12s 平均 ≥50fps 桌面）。
3. `prefers-reduced-motion: reduce` → 粒子停播（静态一帧）/打字机瞬显/过渡 0.01ms。
4. 对比度：文字 `--fg` 对 `--bg` 不低于现状 WCAG AA（≥4.5:1）；辉光不得降低正文可读性。
5. 375px 不溢出；触控目标 ≥44px 不因装饰层改变。
6. 安全：禁 `v-html`/`innerHTML`/`eval`/`EventSource`/`setInterval`（`web/tests/unit/security-scan.test.ts` 保持绿）；
   装饰层 `aria-hidden`；`security-scan` 新文件需同风格。
7. dist 入库：改完 `pnpm --dir web build` 并提交 `web/dist`（CI drift 门禁）。

## 5. 依赖与许可证策略（与 ADR D-H4-5 一致）

- **本轮不新增任何前端运行时依赖**；上述库仅作为技法参考。
- 未来若必须引入：必须同时满足 ① 许可证 ∈ {MIT, Apache-2.0, BSD, ISC, CC0}；
  ② `pnpm-lock.yaml` 锁版本并过许可证门禁；③ 懒加载且提供首屏 gzip 增量实测证据；
  ④ 新增 ADR 或在本文更新并署名。
- **禁止**：GPL/AGPL/LGPL 代码、无 LICENSE 仓库代码、CC-BY-NC 素材、从闭源/Awwwards 站点取代码/素材。
- SillyTavern 仅作为**数据格式语义**参考（AGPL-3.0，代码零复制）；UI 不参考（用户明确否决）。

## 6. NOTICE / 署名段（若后续内联任何第三方代码，必须随代码保留）

> 本项目前端视觉实现以原创 CSS/Canvas 为主，仅在技法层面参考以下 MIT 项目（未内联其代码）：
> - Codrops demo（https://tympanus.net/codrops/ ，MIT）：glitch 切片技法参考。
> - TheMarco/retrozone（https://github.com/TheMarco/retrozone ，MIT）：CRT 扫描线/辉光参数参考。
> - tsParticles（https://github.com/tsparticles/tsparticles ，MIT）/ @vueuse/motion（MIT）/
>   auto-animate（MIT）/ anime.js（MIT）/ vue-kinesis（MIT）/ TresJS（MIT）/ three.js（MIT）：
>   动效与粒子组织方式参考。
>
> 若未来内联上述任一项目的代码片段，需在此处与 `README` 致谢段保留对应
> `Copyright (c) <年份> <作者>` 与 MIT 许可声明；Codrops 站点另有「免费资源不可再分发」限制，
> 内联前需复核。

## 7. 未核实/限制

- Codrops 各 demo 仓库多数无 `LICENSE` 文件，站点 License 页声明 demo 为 MIT；两者冲突时本文按
  「放宽为 MIT 但坚持重写」处理（保守）。
- 包体数据为 bundlephobia（2026-11-11）实测，随版本变化；实现时以构建产物为准。
- 部分粒子/CRT 参数需真机/低端机实测后调优（player-rep/qa 在验收阶段复核）。
- Awwwards 仅作观感参考，不产生任何可复制素材。
