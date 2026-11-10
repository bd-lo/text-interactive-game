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

## 5. R3 玩家体验打磨批次决策（D-R3-1..5，2026-11-11 追加）

> 批次性质：**范围外玩家体验批次，不占 001–028 编号**（与 R2 同口径）。输入＝M6-028 玩家独立验收 2 项展示层 P1 + R3 玩家验收 P1/P2 清单（`.work/team-player-2026-11-10/round3-player-acceptance.md` §4/§5）；基线 `a18d545`（M6 完成后）；提交见 R3 进度文档。

### D-R3-1 批次范围与边界

- **决策**：本批处理「玩家可感知、低风险、可回归验证」的打磨项：结局通关总结、结局文本进冒险记录、细看/对话文案去重、移动方向词、开场历史、只读回合提示、诊断面板生产隐藏、无事件回合来源徽章、缺材料建议可执行、console 按卡开局、旧档兼容、玩家可见内部 ID 脱敏（含 028 的 `item.torch` 与保存区 `card_…`）。
- **不做**（登记不阻塞）：① 战斗/陷阱/死亡系统与失败结局内容（R2 §5 已定，复用 `hp_delta` reducer 可后续补内容）；② m3 首屏断句等会破旧 golden 的叙事文本重钉（需一次性受控重基线，见 R2 §6）；③ 流式叙事/SSE、Playwright/a11y 引入、终端卡库浏览（超出本批价值/风险比）。
- **理由**：R3 验收结论「可面向玩家发布，遗留均 P1/P2 打磨」；本批以玩家价值排序逐项清零，同时保持旧 golden 与 demo/console 逐字不变。

### D-R3-2 缺材料建议口径（P2-4）

- **决策**：craft 失败（缺材料/缺工具/工位不符）时：① hint 点名材料名与所在房间（如「火把在废弃哨站·入口」）；② `suggested_actions[0]` 必须是**可提交的一步**——材料在当前房间→「拿起X」，在相邻房间→「去{房间}拿X」（经确定性解析器实测解析为 `move_to` 并真的到达），更远→「向{方向}走」；不要求一条指令同时移动+拾取。
- **实现边界**：生产多角色链路把世界内失败统一映射 `INVALID_INPUT`（聚焦解析同时接受 NOT_FOUND/OUT_OF_RANGE/NOT_ALLOWED，匹配不中即静默退化）；「材料不够：X还差 N 个」分支内含 ASCII 数字会被编排层 `_safe_player_reason` 过滤，生产不可达，仅直调单测覆盖（登记不阻塞）；成功回合建议逐字不变，失败回合不写事件、不影响叙事 text_len 与旧 golden。

### D-R3-3 叙事文案去重口径（AC-35/P2-5）

- **决策**：① inspect 路径做句级去重 + 补句去重（`_dedupe_adjacent_sentences`/`_pad_text_no_repeat`），**不改共用 `_fit_text` 本体**；② story 对话 reply 分支删除固定框架句（「沉默片刻，抬眼看你」等）并缩短尾句以保留完整回话；③ up/down 移动改爬梯文案，north/south/east/west **逐字不变**。
- **护栏**：旧 golden 61 零改动、provenance 7 全过、`demo --check` 7 屏字数不变、m2 `向北走` 84 字与 talk 83 字逐字不变；m2/m3/m4 golden 无 up/down 移动，故新分支安全。

### D-R3-4 玩家可见内部 ID 脱敏口径（R2 AC-23 延伸）

- **决策**：玩家可见文本不出现 `card_`/`game_`/`char_`/`npc_`/`item_`/`room_` 等内部 ID 或未登记 actor ULID：生成页保存成功区不显示卡片 ID；卡库列表 meta 不显示 ID；详情标题/404 文案不显示 ID（错误映射不透传可能含 ID 的服务端 message）；回放页标题不显示 `game_` ID；未知 actor 显示「其他角色」。URL href、dev-only 诊断折叠面板不视为可见文本（生产构建不渲染诊断面板）。
- **验收**：正则 `/(?:card|game|char|npc|item|room)_[0-9A-Za-z]{6,}/` 在生成页/保存成功区/对局页/冒险记录/卡库列表/卡详情/回放页/结局页 **8 个页面场景 0 命中**（e2e 硬断言 + vitest）。

### D-R3-5 旧档兼容与文档偏差登记（AC-33）

- **事实**：当前 `game_created.params` 实现**不含 `scenario` 字段**（场景由初始状态/读取时求值体现），与第 21 章 §21.2.8 v0.2 伪代码示例不一致；`POST /games` 响应为实际 5 字段（D-005-5 已登记）。第 21 章以「目标形态」示例存在，实现口径以第 16 章与 PROGRESS §5 为准；本批在 §21.2.8 尾部加注说明，不重写伪代码。
- **兼容证据**：构造旧格式存档（事件载荷去 `scenario/card_id/snapshot_version`、状态 JSON 去 `quests` 并重算快照哈希）后，`GET /games/{id}`、`/view`、`/replay`、继续回合、console 自动发现与 `--list` 全部可读；新增回归测试钉住。
- **变更纪律**：后续若把 `scenario` 正式写入 `game_created.params`，须走加法兼容 + 旧档读取测试 + PROGRESS §5 登记。
