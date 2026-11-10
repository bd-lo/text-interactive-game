# ADR · M6 出口 e2e 验收口径（D-M6-11）

- 状态：已定案（只追加）｜决策人：lead｜复核：qa-tester 门禁报告 + player-rep 独立验收（`.work/r3-team/s1/qa-s1-gate.md`、`.work/r3-team/s1/player/028-acceptance-report.md`，`.work` 不入库）｜日期：2026-11-11｜基线：`0d46e9a`（M6-028 开发前）
- 关联：M6 025–028 + M6 出口附加项；第 17 章 §17.2 M6 表、第 23 章 §23.8/§23.9；PROGRESS §5 D-M6-11；028 进度文档

## 1. 背景与红线

- 第 17 章 §17.1/§17.2 原定 M6 每片验收含 `pnpm --dir web exec playwright test e2e/*`，第 23 章 §23.8 亦把 Playwright e2e/a11y 列为 M6 出口前补齐项；但 025/026/027 三片均以「**Playwright 后移**」落地（浏览器二进制下载/CI 组合不可控；项目约束「不新增依赖、不做无法离线验收的产物」——D-TEAM-3/4、D-M6-10）。
- M6-028 是 M6 最后一片，出口验收必须给 e2e 一个**可复跑、可证伪**的落地口径，否则「M6 出口条件」名存实亡。
- 红线：不新增运行时/构建期依赖；不把代理/密钥/外部网络写进仓库或 CI；真实浏览器验收必须**可本地一键复跑**且能证明脚本会失败（负向控制）；旧 golden 与既有门禁零回归。

## 2. 决策

1. **M6 出口 e2e 验收 = 「零依赖真实浏览器 CDP 脚本 + 契约测试 + vitest + 静态托管深链」四件套**，不再以 Playwright 为出口前置：
   - `scripts/e2e-web.mjs`（Node 26，零依赖，使用全局 `fetch`/`WebSocket` 直连 Chrome DevTools Protocol）：参数 `--base-url/--browser/--out/--headful/--keep-open`，系统 Edge/Chrome headless，覆盖 028 主回路（生成卡→草稿→保存→按卡开局→1 回合→卡库/详情）、375px 无横向溢出、429 限流提示、0 console 错误；输出 `result.json` + 截图（`.work/`，不入库）；内置**负向控制**：静态产物缺失时脚本 exit 1 且 `result.json.ok=false`，排除假绿。
   - `tests/integration/test_frontend_contract.py`（Python，不依赖 Node）：11 端点/接口字段与 live OpenAPI 逐项比对 + generate/save 实测（模板兜底字段、幂等 409/422、第 6 次 429、深链 SPA 200+CSP）。
   - `pnpm --dir web test`（29 files/158 tests）+ `typecheck` + `build`（CI `web-build` job 含 dist 漂移门）。
   - 静态托管深链断言（契约测试 + e2e 各一）。
2. **e2e 脚本不接入 CI**：CI 无「系统浏览器 + uvicorn」组合（`quality` job 不装 Node，`web-build` job 不装 uv/浏览器）；CI 侧由契约测试 + vitest/build 兜底。这是**已登记的偏差**，不是遗漏。
3. 验收命令口径回写：第 17 章 §17.2 M6 表 028 行验收命令由 `pnpm --dir web exec playwright test e2e/generate` 改为「`node scripts/e2e-web.mjs` + `pnpm --dir web test` + 契约测试」；第 23 章 §23.8 e2e 行注明「M6 出口以零依赖 CDP 脚本落地，Playwright/a11y 留发布期评估」。

**理由**：① 真实浏览器验收是玩家价值最高、最接近发布形态的证据，不能因工具不可用而放弃；② CDP 协议是 Chromium 稳定接口，Node 26 内置 WebSocket 使零依赖可复跑成立（本机 Edge 与 CI 之外的开发机均可用）；③ 脚本一键复跑 + 负向控制满足「可证伪」要求；④ 不新增依赖/不引入浏览器下载，符合全仓依赖与离线纪律。

**被否**：A 引入 Playwright + `pnpm add -D` + 浏览器下载（违反零新增依赖/离线验收约束，CI 组合需新增 job，收益与风险不匹配）；B 完全放弃真实浏览器验收、只留 vitest（无法覆盖路由深链/布局/限流 UI 的真实渲染，玩家验收缺位）；C 把浏览器二进制入库（仓库体积不可接受、跨平台失败）。

**影响面**：M6 出口验收判据；CI 不新增 job；`scripts/e2e-web.mjs` 进入仓库（开发/验收工具，非运行时依赖）；第 17/23 章与 028 进度文档回写；PROGRESS §5 登记本偏差。28 的验收证据见 028 进度文档 §3（14/14 步骤、62/62 断言、玩家独立验收 P0 76/76）。

## 3. 回写落点

- 第 17 章 §17.2 M6 表 028 行验收命令与 M6 实施备注；第 23 章 §23.6/§23.8/§23.9；`docs/PROGRESS.md` §5；`docs/progress/028-2026-11-11-llm-card-generate.md`。
- 后续复审触发：发布形态评估（PyPI/Docker/CI 浏览器矩阵）时重新评估是否引入 Playwright；若引入，本 ADR 由新 ADR 取代并保留历史。

## 4. 变更纪律

- 只增不改；推翻须新增 ADR（`supersedes: D-M6-11`）→ 回写章节 → 重跑全量 + e2e → 更新 PROGRESS §5。
