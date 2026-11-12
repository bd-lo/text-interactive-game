# 055 · 2026-11-12 · N4 · Playwright/a11y 工具口径复评

> 切片目标一句话：复评 D-M6-11「e2e 用零依赖 CDP、不进 CI」，用依赖/CI/维护/a11y 成本证据决定是否引入
> Playwright 或长期浏览器矩阵。｜ 验收命令：见 §1 ｜ 里程碑 N4（第 17 章 §17.9.2）｜ 编号区间 049–056 ｜
> 决策：[D-N4-2](../decisions/2026-11-12-playwright-a11y-eval.md) ｜ 执行/验收：release-tech

## 1. 目标与验收命令

- 目标（≤90 分钟）：产出一份可执行的评估 ADR（不是实现），明确「继续 CDP / 引入 Playwright / 折中」三选一、
  成本量级、被否理由、回滚与复评触发条件，并给后续批次可引用的验收断言。
- 验收命令/断言（先定后写）：
  - `Select-String -Path web/package.json -Pattern playwright` → **0 命中**；`web/node_modules` 无 playwright 包（`pnpm-lock.yaml` 仅有 vitest 的 optional peer 元数据，未安装）
  - `git status --short` → 本切片只新增 ADR + 本进度文档（零代码/CI/依赖改动）
  - `docs/decisions/2026-11-12-playwright-a11y-eval.md` 存在且含 §2 决策/§3 对比/§4 归属/§6 复评触发
- 不做什么：不引入 Playwright/axe 依赖、不改 `web/package.json`/`pnpm-lock.yaml`/`ci.yml`、不重写 `scripts/e2e-web.mjs`、
  不跑真实浏览器 e2e（零代码变更；现有 26 步/125 断言由 057 验收）、不改第 23 章/PROGRESS/CHANGELOG/ADR 索引。

## 2. 改动与提交

- 文件：`docs/decisions/2026-11-12-playwright-a11y-eval.md`、`docs/progress/055-2026-11-12-playwright-a11y-eval.md`
- 提交：未提交（工作区留给 Lead 统一提交）；契约变更：无
- 关键结论：**继续零依赖 CDP，不引入 Playwright 长期 CI 矩阵**（与 D-N7 一致）；a11y 自动化本轮不做门禁，
  权威证据 = N2 041–042 的键盘/aria/对比度清单 + vitest。
- 成本要点：Playwright npm 侧约 19MB（`playwright` 5.06MB + `playwright-core` 13.97MB + `@playwright/test` 0.03MB，
  npm registry 1.65.0 unpacked）×浏览器下载约 150–200MB/版本（估算）；CI 需安装/缓存/矩阵 ×N 与 lockfile/版本 pin；
  迁移期双工具或重写 1950 行脚本；a11y（axe）需先完成人工分诊基线，否则既有违规阻塞 CI。
- 护栏检查：零依赖/零 CI/零运行时代码改动；未改事件/状态/golden；`docs/decisions/README.md` 索引未改（Lead 统一追加）。

## 3. 验证结果（评估证据）

- 现状核对：`scripts/e2e-web.mjs` 约 1950 行、零 npm 依赖（Node 26 内置 `fetch`/`WebSocket` + CDP）；057 基线
  **26 步/125 断言**（057 基线；启动器模式 26/126；041–042 可能追加步骤）；CI `web-build` job 已在临时 uvicorn 上跑该脚本
  （H4-L6；失败上传 `artifacts/e2e-web/`）——即「单 OS + 系统 Chrome」CI 验证已存在，D-M6-11 原「不进 CI」表述已过时。
- a11y 现状：R4/R5 已做对比度（日间 16.63:1、夜间 17.71:1，按钮 6.29/5.67）、`prefers-reduced-motion`、
  `forced-colors`、键盘操作基线；041–042 并行批次做系统化审计；当前无 axe 门禁。
- 依赖检查：`web/package.json` playwright **0 命中**；`web/node_modules` 无 `playwright`/`@playwright`/`@vitest/browser-playwright`；`pnpm-lock.yaml` 只在 vitest 5 的 `peerDependenciesMeta` 保留 optional peer 名（未安装/不下载浏览器）；`.github/workflows/ci.yml` 无 Playwright 步骤。
- 全量 pytest/e2e：本切片零代码改动，**未跑全量**（按任务约束由 Lead 集成时统一跑）；e2e 数字引用 057 已验收基线。
- 失败与回滚：无（纯文档；回滚 = 删除本切片新增两文件）。

## 4. 下一步与风险

- 下一步：056 long-session-perf（本批）→ 058 portable-package-eval（本批）；Lead 集成时回填 PROGRESS §3/§4/§5
  与 `docs/decisions/README.md` 索引。
- 风险/开放问题：`R-P2`（a11y）归 041–044；两处文档漂移（第 23 章 + PROGRESS §5 仍写「e2e 不接入 CI」、
  第 18 章仍列 Playwright/axe 规划）已在 ADR §4 登记归属后续文档回写批次，本切片不改。
- 需要人工决策：无（结论按 D-N7 倾向；若用户要求跨浏览器/公开部署再按 ADR §6 触发复评）。
