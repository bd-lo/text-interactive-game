# ADR · Playwright/a11y 工具口径复评（D-M6-11 复审；D-N4-2）

- 状态：已定案（N4-055 评估结论） ｜ 决策人：lead ｜ 复核人：release-tech（脚本/CI 实测 + 成本口径核对，`.work` 证据不入库） ｜ 日期：2026-11-12 ｜ 基线：`553d0a0`
- 关联：第 17 章 **§17.9.2 N4-055**（「默认仍 CDP；不增 CI 复杂度除非收益明确」）与 §17.9.5；
  [D-M6-11](2026-11-11-m6-exit-and-r3-closure.md)（M6 出口 e2e 验收口径，本 ADR 为其「发布期复评」）；
  [D-N7](2026-11-11-post-m6-roadmap.md)（不默认引入 Playwright 长期 CI 矩阵）；
  [D-H4-1..6](2026-11-11-h4-worldbook-and-visual.md)（L6：CDP e2e 接入 CI `web-build`）；
  N2 041–042 a11y-keyboard（并行批次，本 ADR 不评估其实现结果，只定工具口径）；风险 `R-P2`（浏览器兼容/可访问性）。

## 1. 背景与红线

- **现状工具**：`scripts/e2e-web.mjs`（评估时约 1950 行，Node 26，**零 npm 依赖**：只用内置 `child_process`/`http` 等 +
  全局 `fetch`/`WebSocket` 直连 Chrome DevTools Protocol；headless 驱动系统 Edge/Chrome；临时
  `--user-data-dir` 自动清理；输出 `result.json` + 截图；内置负向控制排除假绿）。
- **现状覆盖（2026-11-12 评估时基线）**：真实浏览器 **26 步 / 125 断言**（非启动器 CLI 口径；启动器模式 26 步/126 断言；N2 041–042 并行批次可能追加键盘/reduced-motion 步骤，步数只增不减，不影响本 ADR 结论），
  含「确认退出→覆盖层→`/healthz` 不可达→服务进程 exit 0」）；覆盖深链 SPA、生成卡→按卡开局→回合、375px 无横向溢出、
  429 限流 UI、结局链、0 console 错误、玩家可见文本无内部 ID 等。
- **现状 CI**：`.github/workflows/ci.yml` 的 `web-build` job（Node 26 + pnpm 12.6.0）在 build/vitest 之后启动临时
  uvicorn（`:8794`、`TEXTGAME_LLM_ENABLED=false`）并跑同一份 CDP 脚本，失败上传 `artifacts/e2e-web/`（H4-L6 落地；
  e2e 步骤 `timeout-minutes: 10`）。**即：当前已是「单 OS + 系统 Chrome」的 CI 浏览器验证，只是不用 Playwright。**
- **D-M6-11 的口径漂移**：原第 2 条「e2e 脚本不接入 CI」已被 H4-L6 事实修订（第 23 章/PROGRESS §5 的旧表述尚未回写）。
  本 ADR 以现状为准，不改写 D-M6-11 历史；工具选型结论（零依赖 CDP vs Playwright）即本次复评对象。
- **a11y 现状**：R4/R5 已做手工对比度（日间 fg/bg 16.63:1、夜间 17.71:1，按钮 6.29/5.67）、`prefers-reduced-motion`、
  `prefers-reduced-transparency`/`backdrop-filter` 降级、`forced-colors` 交还系统色、键盘操作（`Ctrl+Enter`/`↑↓` 历史）；
  N2 041–042 正在做系统化键盘/aria/对比度审计与修复。**当前无 axe 或其它自动化 a11y 门禁。**
- 红线（不放宽）：零新增运行时/构建期依赖；离线可复跑；不把浏览器二进制入库或要求本机预先下载；不降低现有 e2e 强度
  （步数/断言/负向控制）；a11y 不能只靠工具替代人工清单；默认 dev/CI 行为不因本评估改变。

## 2. 决策

### D-N4-2 结论：继续零依赖 CDP 为唯一 e2e 工具与 CI 口径，不引入 Playwright 长期 CI 浏览器矩阵

- 决策：
  1. **工具**：e2e 继续使用 `scripts/e2e-web.mjs`（零依赖 CDP），不新增 `@playwright/test`/`playwright`/
     `@axe-core/playwright` 等依赖，`web/package.json` 与 `web/pnpm-lock.yaml` 保持不变；
  2. **CI**：维持现状——`web-build` job 用系统 Chrome 跑同一份 CDP e2e（单 OS、单浏览器），**不建**
     Linux/Windows/macOS × Chromium/Firefox/WebKit 矩阵；
  3. **a11y**：本轮不引入 axe 自动化门禁；a11y 的权威证据 = 041–042 的键盘/aria/对比度清单与 vitest 断言（见 §4 归属）。
     若将来需要自动化回归，优先评估「本地可选、不入 CI、不改 lockfile 默认安装」的 axe 用法，并按 §6 触发条件另立 ADR；
  4. **文档**：第 23 章/PROGRESS §5 中「e2e 不接入 CI」的旧表述建议由后续文档批次回写为「已接入 CI web-build」；
     本切片只交 ADR/进度文档，不改这些文件（第 18/23 章回写见 §5 边界）。
- 理由（收益 vs 成本，评估证据见 §3 表）：
  - **CDP 方案成本已沉没、且当前稳定**：零依赖、零浏览器下载、脚本在开发机与 CI 同源；H4-L6 起 CI 多次运行全绿，
    失败有 `result.json` + 日志 artifact。当前 26 步/125 断言已覆盖玩家主回路与启动器退出分支。
  - **Playwright 的增量收益有限**：其核心优势是（a）跨浏览器矩阵、（b）自动等待/重试、（c）trace/录像、
    （d）与 axe 集成的 a11y 扫描。本项目是**单机单玩家、浏览器访问 `127.0.0.1`、分发不要求跨浏览器**，
    Chromium 系（Edge/Chrome）即目标；现有脚本的显式等待在 CI 已被证明够稳（仅需 DBUS/超时加固）。
    a11y 自动化在 041–042 尚未收口时引入会被既有问题淹没、需人工分诊，收益不能解释成本。
  - **成本量级**（npm 元数据 + 现有 CI 结构核对，浏览器体积为量级估算、未在本机下载验证）：
    | 成本项 | 零依赖 CDP（现状） | 引入 Playwright |
    | --- | --- | --- |
    | npm 体积 | 0 | `playwright` 5.06MB + `playwright-core` 13.97MB + `@playwright/test` 0.03MB ≈ **19MB**（unpacked，npm registry 1.65.0） |
    | 浏览器二进制 | 0（用系统 Chrome） | Chromium 下载约 **150–200MB/版本**，需缓存；首次 `playwright install` 与 CI cache 未命中显著变慢（估算） |
    | lockfile/依赖治理 | 不变 | `pnpm-lock.yaml` 变更 + 第 19 章依赖登记 + 精确版本 pin + 升级窗口 |
    | CI 时间 | 现有 e2e 步骤已计入 `web-build`（10min 超时内） | +安装/浏览器下载（缓存命中约秒级–1min，未命中约 1–3min 量级）+ 矩阵 ×N |
    | 维护/迁移 | 1 套脚本 | 迁移期双工具或一次性重写 1950 行；两套等待/断言语义并存易漂移 |
    | flaky 风险 | 已用显式轮询；CI 稳定 | 自动等待降低时序 flaky，但新增浏览器下载/字体/视口差异类 flaky |
    | a11y 自动化 | 无（人工清单 + vitest，041–042） | axe 可接入，但需先完成人工分诊基线，否则误报/既有违规阻塞 CI |
    | 新增红线/门禁 | 无变化 | 新增构建期依赖与浏览器获取步骤，违背「零新增依赖 + 离线可验收」纪律 |
    > 体积口径：npm 元数据（[playwright 1.65.0](https://registry.npmjs.org/playwright/latest)、
    > [playwright-core 1.65.0](https://registry.npmjs.org/playwright-core/latest)、
    > [@playwright/test 1.65.0](https://registry.npmjs.org/@playwright/test/latest)、
    > [axe-core 4.14.0](https://registry.npmjs.org/axe-core/latest)，2026-11-12 读取，unpacked size）；
    > 浏览器二进制 150–200MB/版本为**量级估算**（未在本机下载验证），CI 时间同理为量级估算。
- 被否选项：
  - **A. 全面迁移 Playwright + Linux×Windows 浏览器矩阵**：成本（依赖 ~19MB + 浏览器 ~150–200MB/版本 + 矩阵 CI + 迁移 1950 行
    + lockfile/许可证登记）远大于收益（跨浏览器不是分发目标；trace/自动等待为体验优化非能力缺口）；
    且直接违反 D-N7「不默认引入 Playwright 长期 CI 矩阵」。**否**。
  - **B. CDP 为主 + 本地可选 Playwright（devDependency 不进 CI）**：比 A 温和，但仍需把 Playwright 写进
    `package.json`/lockfile 并让所有开发机 `pnpm install` 承担体积；双工具维护与「本地能跑/CI 不跑」的口径漂移风险；
    当前无人需要跨浏览器调试。**本轮否**，仅在 §6 触发条件出现后重新评估「独立可选脚本、不入默认安装」的折中。
  - **C. 放弃真实浏览器 e2e、只留 vitest + 契约测试**：真实渲染/路由深链/布局/限流 UI/启动器退出链路无证据，
    玩家验收与发布 checklist 缺位（D-M6-11 已论证）。**否**。
- 影响面：`scripts/e2e-web.mjs`（不变）、`.github/workflows/ci.yml`（不变）、`web/package.json`/`pnpm-lock.yaml`（不变）、
  第 17 章 §17.9.5（D-N7 继续成立）、第 18/23 章历史「Playwright/axe」规划表述（建议后续回写，非本切片）；
  N2 041–042 的 a11y 交付物（工具口径与本 ADR 不冲突）。
- 回写落点：本 ADR + 进度文档 055；`docs/decisions/README.md` 索引由 lead 统一追加（本切片不改索引）；
  D-M6-11「发布期复评」承诺在本 ADR 关闭。
- 验收断言（本切片实测）：
  1. `web/package.json` 中 `playwright` **0 命中**；`web/pnpm-lock.yaml` 仅在 vitest 5 的 `peerDependenciesMeta` 里保留 `@vitest/browser-playwright` **optional peer 元数据**（未安装、不解析、不下载浏览器），`web/node_modules` 无 `playwright`/`@playwright`/`@vitest/browser-playwright`（保持零依赖）；
  2. `node scripts/e2e-web.mjs` 在 CI/本地仍可复跑（057 基线 26 步/125 断言；本轮不新增/删步骤）；
  3. 本 ADR 与 055 进度文档落盘、命名/编号符合第 22 章（`055-2026-11-12-playwright-a11y-eval.md`）；
  4. §6 复评触发条件与回滚条件写全，后续批次可据此拒绝「顺手引入 Playwright」。

## 3. 选项对比（评估证据）

| 选项 | 依赖/体积 | CI 成本 | 跨浏览器 | a11y 自动化 | flaky/维护 | 结论 |
| --- | --- | --- | --- | --- | --- | --- |
| **继续零依赖 CDP（选定）** | 0 新依赖；用系统 Chrome | 现状（web-build 内 e2e 步骤） | 仅 Chromium（目标） | 无（人工清单 + vitest） | 低（已加固） | ✅ |
| Playwright + 单浏览器 | ~19MB npm + ~150–200MB 浏览器 | +下载/安装 + 缓存维护 | Chromium | axe 可接 | 中（新 flaky 源） | ❌ 收益不足 |
| Playwright + CI 浏览器矩阵 | 同上 × 矩阵 | +矩阵 ×N（Linux/Windows × 浏览器） | 全 | axe 可接 | 高（矩阵 flaky/维护） | ❌ 明确不做（D-N7） |
| CDP + 可选本地 Playwright（折中） | 同单浏览器 | 不变（本地可选） | Chromium | 本地 axe | 中（双工具漂移） | ⏸ 触发条件后再评估 |
| 只留 vitest/契约 | 0 | 最低 | 无 | 无 | 低 | ❌ 真实渲染证据缺位 |

## 4. 已知问题归属

| 编号 | 摘要 | 归属 | 关闭条件 |
| --- | --- | --- | --- |
| R-P2 | 浏览器兼容与可访问性 | N2 041–042（a11y-keyboard）+ 后续移动端 043–044 | 键盘全流程 + 对比度/reduced-motion 清单 + vitest/e2e 断言验收 |
| — | 第 23 章/PROGRESS §5 仍写「e2e 不接入 CI」（与 H4-L6 现状不符） | 后续文档回写批次（Lead 统一） | 两处表述改为「已接入 CI web-build（H4-L6）」 |
| — | 第 18 章 §18.9/本章结论仍列 Playwright/axe 为规划工具 | 后续文档回写批次（Lead 统一） | 改为「零依赖 CDP 为 e2e 口径；a11y 自动化非门禁（D-N4-2）」 |
| — | 本机/CI 浏览器版本漂移导致 CDP 行为差异 | 055 遗留（低） | CI 固定 runner 镜像 + 脚本探测失败信息已足够；出现 flaky 再评估 |

## 5. 出口验收判据与到期承诺处置

- 055 出口：本 ADR 定案（结论=继续 CDP、不引入 Playwright 长期矩阵）+ 进度文档 055 落盘 + 第 22 章命名/编号一致；
  无代码/CI/依赖改动（验收断言见 §2 末）。实测：`web/package.json` playwright 0 命中，`pnpm-lock.yaml` 仅 vitest 的 optional peer 元数据（未安装/不下载浏览器），`node_modules` 无 playwright 包；
  e2e 基线为 26 步/125 断言（057 已验收数字，本轮不重跑全量 e2e；041–042 可能追加步骤——见进度文档 §3）。
- **到期承诺处置**：D-M6-11 §3「发布形态评估（PyPI/Docker/CI 浏览器矩阵）时重新评估是否引入 Playwright」——
  本 ADR 即该复评的结论：**不引入**；D-N7「055 只评估」履行完毕。历史表述不删，只标注已复评。
- 边界：本切片**不改** `docs/开发文档.md`、`docs/PROGRESS.md`、`CHANGELOG.md`、第 23 章与 `docs/decisions/README.md`；
  相关回写由 Lead 在集成时统一处理（§4 已登记）。

## 6. 后移与复审（触发条件）

- 用户/分发渠道明确要求**跨浏览器支持**（如公开网页部署、Firefox/Safari 用户反馈真实兼容问题）→ 重评 Playwright（A 方案）；
- 现有 CDP 脚本在 CI 连续 flaky（建议阈值：近 10 次运行 ≥2 次失败）或维护成本明显超过迁移成本 → 重评；
- CI 环境不再提供系统 Chrome（ubuntu-latest 移除预装浏览器）→ 需评估「受控浏览器获取」方案（Playwright 或手工缓存）；
- 041–042 完成且**明确需要把 a11y 违规纳入自动化回归门禁** → 先评估本地可选 axe（零默认依赖、不入 CI），
  再按 §2 决策 3 的边界决定是否升级为门禁；
- 引入任何新工具必须：新增 ADR（`supersedes: D-M6-11`/本 ADR）+ 第 19 章依赖/许可证登记 + 全量/e2e 证据 + 回滚到 CDP 的路径。

**回滚条件与路径**：本 ADR 未改变任何实现，回滚 = 无代码动作；若未来引入 Playwright，回滚 = 删除依赖/CI 步骤、
恢复 `scripts/e2e-web.mjs` 为唯一 e2e 口径（该脚本在此期间必须保留可用，不得删除）。

## 7. 变更纪律

- 本 ADR 只增不改；推翻结论 = 新 ADR + 明确 `supersedes` + 回写第 17 章 §17.9.5/§17.9.2 与 PROGRESS §5；
- 后续批次的 e2e/a11y 实现不得静默替换工具或删除 `scripts/e2e-web.mjs` 的负向控制/失败证据能力；
- 本 ADR 与 D-N7「不默认引入 Playwright 长期 CI 矩阵」一致；若冲突，以 D-N7 为准并先改 D-N7。
