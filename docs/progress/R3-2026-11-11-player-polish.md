# R3 · 玩家体验打磨批次（结局体验 / 引导 / 文案去重 / 可执行建议 / 内部 ID 脱敏）

> 编号：`R3`（范围外玩家体验批次，不占 001–028 编号；按 `docs/progress/README.md` 例外条款登记）
> 日期：2026-11-11（Asia/Shanghai）｜基线：`a18d545`（M6-028 完成）→ 交付 commit：`7f1c54a`（后端）+ `c9ab10d`（前端）+ `86de2a0`（QA 验收用例）
> 角色：Lead + 4 角色团队（backend-dev、frontend-dev、qa-tester、player-rep）；决策见 [ADR D-R3-1..5](../decisions/2026-11-11-m6-exit-and-r3-closure.md)。

## 1. 目标与判定口径

用户指令（2026-11-10 23:55）：检查工作区游戏开发任务进展，把接下来的任务交给 agent 团队分阶段开发测试提交 git，直到开发结束。M6-028 完成（M6 4/4）后，本批处理「玩家可感知、低风险、可回归验证」的遗留项：

1. M6-028 玩家独立验收 2 项展示层 P1：草稿「初始物品」显示模板键 `item.torch`；保存成功区显示内部 `card_…` ID。
2. R3 玩家验收（36 条）遗留 P1/P2：结局屏无通关总结、细看/对话文案重复句、终局叙事不含结局、诊断面板常驻、首屏冒险记录为空、只读回合不进历史、缺材料建议不优先、移动方向词生硬。
3. 顺带清零同口径项：卡库列表/卡详情/回放页可见内部 ID、console 无卡库入口（按卡开局）、旧格式存档兼容回归。
4. 硬约束：旧 golden 61 零改动 + provenance 7；`demo --check` 7 屏逐字不变；`console --check` 逐字不变；不新增依赖；e2e 口径维持 D-M6-11。

## 2. 交付内容

| 方向 | 交付 | 关键位置 |
| --- | --- | --- |
| 结局体验（AC-34/AC-10d） | 结局面板「本局 N 回合 · 目标 X/Y · 生命值 HP/MAX」；结局文本进冒险记录（纯派生、刷新不重复、只出现一次） | `web/src/utils/history.ts`、`GameView.vue`、`NarrativeHistory.vue` |
| 引导与状态（P2-1/P2-2/P2-3/P2-6） | 新局冒险记录第 1 条「开场」（房间描述+目标进度，首回合后不重复）；冒险记录与回放页只读提示；诊断面板仅 dev 渲染（`IS_DEV`）；无事件回合来源徽章改中性「来源未知」 | `web/src/utils/env.ts`、`GameView.vue`、`ReplayView.vue` |
| 文案去重（AC-35） | inspect 路径句级去重 + 补句去重（不碰共用 `_fit_text` 本体，m2 火把 80→94 字零重复、story/m2 全对象矩阵重复对数 1/2→0）；story 对话 reply 分支删固定框架句、尾句缩短保留完整回话（m2 旧对话 83 字逐字不变） | `src/textgame/orchestration/narrator.py` |
| 移动叙事（P2-5） | up/down 改爬梯文案（87/94 字）；north/south/east/west **逐字不变**（旧 golden 无 up/down，安全） | `src/textgame/orchestration/narrator.py` |
| 可执行建议（P2-4） | 缺材料/缺工具/工位不符时 hint 点名材料与所在房间（「火把在废弃哨站·入口」）；`suggested_actions[0]` 为可提交一步：同屋「拿起X」/相邻「去{房间}拿X」（实测解析为 `move_to` 并真的移动）/更远「向{方向}走」；成功回合建议零改动 | `src/textgame/domain/suggestions.py` |
| 终端入口 | console `--card-id/--card` 按卡开局 + `--list-cards`（404/422/互斥中文提示；无卡旧 CLI 行为逐字不变） | `src/textgame/console.py` |
| 旧档兼容（AC-33） | 旧格式存档（事件载荷无 `scenario/card_id/snapshot_version`、状态 JSON 无 `quests`）读取 `/games`、`/view`、`/replay`、继续回合、console 自动发现全部通过；登记第 21 章 §21.2.8 `game_created.params.scenario` 文档/实现偏差（D-R3-5） | `tests/integration/test_legacy_save_read.py`、第 21 章 |
| 内部 ID 脱敏（D-R3-4） | 生成页保存区/卡库列表/卡详情/回放页去 `card_…`/`game_…`/未登记 actor ULID；`item.torch` 等模板键显示中文名（12 个内置模板，生成页/卡库一致）；详情 404 不转发可能含 ID 的服务端 message | `web/src/utils/{labels,cards}.ts`、`CardLibraryView.vue`、`CardDetailView.vue`、`ReplayView.vue` |
| 验收工具 | `scripts/e2e-web.mjs` 扩到 **18 步/101 断言/16 截图**（结局总结/结局进历史/开场/徽章/8 页面无内部 ID/375）；QA 新增独立黑盒 `tests/integration/test_r3_polish_acceptance.py` **12 例** | `scripts/e2e-web.mjs`、`tests/integration/` |

## 3. 验证结果（Lead/QA 实跑）

| 检查 | 结果 |
| --- | --- |
| 全量 pytest | **1695 passed**（HEAD 1683 + QA 独立 12，34.3s），0 failed |
| 回放/golden | **68 passed**（旧 golden 61 + provenance 7）；`tests/fixtures/golden` 零改动；未 `--snapshot-update` |
| demo 逐字 | `demo --check` exit 0，7 屏 114/83/81/85/82/84/81 与 HEAD 基线**字节一致** |
| console 逐字 | `console --check` exit 0，4 回合（含失败样本）与 HEAD 基线**字节一致** |
| 质量门 | ruff format（199 files）/ ruff check / mypy（93 files）/ lint-imports（2 kept）/ `check_docs` PASS |
| 前端 | `pnpm test` **32 files / 178 tests passed**（+20）；`typecheck` exit 0；`build` 83 modules；tracked `web/dist` 与 fresh build **17 文件 SHA256 零漂移** |
| 真实浏览器 e2e | 用 tracked `web/dist` 起服务：**18/18 步骤、101/101 断言 PASS**，16 截图，0 console 错误（1 条为主动 429 network 日志），端口全释放 |
| 独立黑盒（QA） | 新增 12 例全过，含 P2-4 suggested[0] **可执行性**（提交后真的到入口/拿到火把）、AC-35 8 对象去重、P2-5 m2 north 84 字逐字钉死、只读回合零写入、console--card-id 子进程两阶段、AC-33 旧档可读可续 |
| 安全/依赖 | `web/src` 禁入 grep（v-html/innerHTML/eval/EventSource/setInterval）无匹配；无新增/变更依赖；无 `.db`/`.env` 入库 |

## 4. 改动与提交

- `7f1c54a fix(game): R3 后端体验打磨`：`src/textgame/{orchestration/narrator.py,domain/suggestions.py,console.py}` + 6 个新测试文件（34 例）。
- `c9ab10d fix(web): R3 前端体验打磨`：`web/src` 9 改 1 新增 + `web/tests` 6 改 2 新增（净 +20 例）+ `scripts/e2e-web.mjs` + `web/dist` 重建。
- `86de2a0 test(r3): QA 独立黑盒验收 12 例 + e2e 结局历史竞态修复`：`tests/integration/test_r3_polish_acceptance.py`（新 12 例）+ e2e 脚本竞态修复（测试侧）。
- 契约变更：无 API/事件/迁移变更；`TurnResult.effects[].hint`/`suggested_actions` 内容在失败回合更具体（既有加法字段语义不变）；console CLI 加法（`--card-id/--card/--list-cards`）；`game_created.params` 维持现状（D-R3-5 登记第 21 章偏差）。

## 5. 玩家独立验收

> 验收人：player-rep（黑盒，默认离线；HEAD `86de2a0`，tracked `web/dist` 为 `c9ab10d` 重建产物、预检 `dist_fresh=True`；证据 `.work/r3-team/s2/player/`，`.work` 不入库）。

- **结论：PASS（P0 60/60、P1 15/15；无阻断）**。HTTP P0 19/19·P1 9/9；真浏览器 CDP P0 31/31·P1 3/3；旧档兼容 P0 4/4；console P0 6/6·P1 3/3；截图 16 张；端口/进程已清理。
- 逐条：结局屏「本局 13 回合 · 目标 3/3 · 生命值 12/20」与 `/view` 一致 +「结局」行进冒险记录恰 1 条 + 375 无溢出；细看（火把/铜线/火石/老兵）无相邻重复句、对话无固定框架重复；up/down 为「顺着窄梯往上/往下走」；新局第 1 条「开场」（断桥描述+目标 0/3，首回合后不重复）；对局+回放只读提示；生产无诊断面板；无事件回合徽章「来源未知」（有事件恢复「离线模板」）；缺材料 hint 点名火把+「废弃哨站·入口」、suggested[0] 提交后**真的移动到入口**并可再拿起；console `--card-id` 成功（HP/行囊来自卡）+ `--list-cards`；物品中文名「火把」；**8 个页面可见文本无内部 ID（正则 hits={}）**；无 JS/console 错误、无横向溢出；S1 时代旧档 3/3 可读。
- 验收过程中一次假红（CRAFT-UI-00/01）定位为 **probe 状态污染**（HTTP 局与浏览器局复用导致材料已齐、打造成功），拆局后全绿；非产品缺陷，已在报告中记录。
- P1/P2 记录（不阻塞）：失败建议实测 4 条（与 D7 上限 4 一致；任务书写「≤3」为文档口径差，按现状记录）；只读回合不进历史/回放（设计使然，已加提示）；失败结局内容不可达（D-R3-1 不做）。
- **S4 终版复验（2026-11-11，最终代码 HEAD `9ae61b4`）**：PASS（P0 **67/67**、P1 **18/18**、17 张截图、13 回合通关、结局总结/结局进历史/目标 3/3、缺材料建议可执行、8 页面无内部 ID）；判定「作为玩家，这个版本能发布」。

## 6. 已知限制/后续

1. **失败结局（倒下）内容不可达**：不做战斗/陷阱/死亡系统（D-R3-1），复用 `hp_delta` reducer 可后续补伤害源。
2. **「材料不够：X还差 N 个」提示分支生产不可达**：编排层 `_safe_player_reason` 过滤 ASCII 数字，仅直调单测覆盖（D-R3-2 记录）。
3. **第 21 章 §21.2.8 伪代码与实现偏差**：`game_created.params` 实际不含 `scenario`（D-R3-5 已加注，读写兼容由测试钉住）。
4. **卡库/详情游戏内 ID**：仅 href 与 dev 诊断面板保留，不构成玩家可见文本（D-R3-4）。
5. 流式叙事/SSE、Playwright/a11y 套件、终端卡库浏览仍后移（S4/发布期评估）。

## 7. 复跑命令

```powershell
uv run pytest -q                                    # 1695 passed
uv run pytest tests/replay -q                       # 68 passed
uv run pytest tests/integration/test_r3_polish_acceptance.py -q   # 12 passed（QA 独立黑盒）
uv run python -m textgame.demo --check              # 7/7 屏逐字
uv run python -m textgame.console --check           # 4/4 回合逐字
pnpm --dir web test                                 # 32 files / 178 tests
node scripts/e2e-web.mjs --base-url http://127.0.0.1:8791   # 18 步 / 101 断言（先起临时 uvicorn）
```
