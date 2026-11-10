# ADR-lite：R2 玩家体验批次（目标/结局、Web 对局回路、一键网页入口、离线 NLU 与道具修复）

- 日期：2026-11-10（R2 批次）；状态：**已定案并落地**（证据见 `docs/progress/R2-2026-11-10-player-experience.md`）。
- 背景：R1 八角色团队审计一致结论——项目工程底子扎实（1417 tests、四门绿、golden 61 冻结），但「以玩家体验为中心」仍不成立：无目标/结局、内容 3–5 分钟耗尽、玩家自然中文一半不被理解、道具是假的（火把/锻锤被「使用」永久销毁、药水不回血）、NPC 永不出话、Web 只显示最后一屏且无历史/续玩/引导、`web/dist` 未入库导致 fresh clone 没有网页版。
- 决策来源：用户指令「一切以用户的游戏体验为中心（方便用户游玩、游玩体验良好），不计一切代价，全力修复」；八角色团队（player-rep/game-designer/backend-dev/frontend-dev/algorithm-dev/agent-dev/oss-user-rep/qa-tester）Round 1 审计 + Lead 汇总。

## 决策记录

| 编号 | 决策 | 理由 | 状态 |
| --- | --- | --- | --- |
| D-R2-1 | 新增剧情场景 **`scenario="story"`「余烬哨站」**（6 房间/7 物品/1 NPC/3 步目标链/胜利与倒下双结局），新建 `domain/scenario_story.py`；m2/m3/m4 builder 一字不动 | 让首玩是「有开始、有目标、有结局」的 8–14 回合垂直切片；加法实现 → 旧 golden 零风险 | 已落地 |
| D-R2-2 | 目标/结局 **读时纯求值**（`story_goal`/`story_ending`），**不新增 EventType、不改 GameState 字段、不加迁移**；`status=ended` 由 QueryService 从状态派生 | `GameState` 加字段会改旧快照 `state_hash`（实测 500 风险）；事件枚举被单测精确断言；读时求值对 61 例 golden 零影响 | 已落地 |
| D-R2-3 | 结局后 orchestrator 拒绝新回合（固定中文友好文案、零写入），`/view.suggested_actions=[]` | 避免「通关后还能继续空转」；只对 story 状态生效 | 已落地 |
| D-R2-4 | 药水/烈酒在 **reducer** 应用 HP（`heal` 正、`hp_delta` 有符号，clamp 0..max_hp），**不新增事件 result 字段**、HP 未变时返回原状态 | m2 golden 第 5 回合「使用治疗药水」时 HP 已满 → 状态哈希不变；m3 golden 火把无 stats 不变；story 初始 HP 12/20 使回血可观测 | 已落地 |
| D-R2-5 | `use_item` 对 `weapon`/`tool` 标签拒绝（世界内说法），`consumable`/`light` 保持；新增只读工具 **`inspect_item`**（细看物品/NPC，零事件） | 修「假道具」与「观察物品只显示房间」；golden 脚本从未 use 武器/工具 | 已落地 |
| D-R2-6 | story NPC 对话走 `story_reply(npc_id, topic)`，事件 `result` 增 `reply`/`dialogue_state`；首次搭话（含 LLM 普通话题）默认 `briefed` | 修「NPC 永不出话」并保证建议动作「和守夜人老兵说话」能推进目标 1 | 已落地 |
| D-R2-7 | 成功回合填 `suggested_actions`（≤4、场景/房间感知），失败回合 `effects[].hint` 给具体「为什么 + 下一步」（过滤英文/内部 ID）；**`TurnResult.text` 一字不改** | m4 golden 不比对两字段；text 不改 → 旧 golden/console 契约不变 | 已落地 |
| D-R2-8 | 离线 NLU 增强：inspect 意图、背包/方向/日常说法同义词、实体别名 + difflib 保守模糊、**房间名导航**（`PerceptionSlice.exit_refs` 加法字段，`id`=方向英文值/`name`=目标房间名） | 玩家自然中文识别率实测 45%→85%；7 条 golden 输入映射逐字不变 | 已落地 |
| D-R2-9 | `/view` 加法字段 `scenario`/`goal`/`ending_text`/`suggested_actions`/`room.exit_names`；`GET /games/{id}` 加法 `scenario`/`ending_text`；新增 **`GET /api/v1/games`** 存档列表（limit≤50/offset、updated_at 倒序、只读零写入） | Web 目标面板/结局横幅/建议 chips/续玩所需；纯加法并同批同步 TS 契约 | 已落地 |
| D-R2-10 | **`web/dist` 构建产物入库**（fresh clone 无需 Node/pnpm 即可网页版）；`create_app` 静态目录解析顺序 `TEXTGAME_STATIC_DIR` → CWD `web/dist` → 由 `__file__` 推导的仓库根 → 包内 `textgame/web_dist`；不足带扩展名资源 404 JSON、`index.html` no-cache、`/assets/*` immutable | 修「网页版对 fresh clone 等于不存在」；wheel 经 hatch build hook 内嵌产物；CI 增加 dist drift gate | 已落地 |
| D-R2-11 | 一键入口 **`scripts/play.ps1`/`play.sh`**（网页优先；`-Terminal/-Check/-Port/-Llm`）；`quickstart.ps1` 转 UTF-8 BOM；`.gitattributes` 固定 `*.sh eol=lf`/`*.ps1 eol=crlf`/`web/dist -text` | 修 Windows PowerShell 5.1 一键命令 P0（无 BOM 解析失败）；让「拿到仓库→打开网页」一条命令 | 已落地 |
| D-R2-12 | Web 对局回路重做：叙事历史（由 replay 重建 + 失败系统行）、状态面板玩家化（隐藏 ULID、qty、可点 chips）、CONFLICT 200-ok=false 自愈、成功刷新失败不重复执行、120s 超时、人话错误、最近对局/服务端存档列表一键续玩、窄屏/无障碍、路由懒加载 | 直接消灭 R1 前端 P0×2/P1×9；入口 js 体积 139.7KB→101.4KB（gzip 51.5→39.5KB） | 已落地 |

## 明确不做 / 已知限制（记录，防范围蔓延）

1. **失败结局（`ending_fallen`）逻辑已实现但当前内容不可达**：唯一伤害源是 1 瓶「结霜的烈酒」（-4），story 初始 12/20 HP → 最低 8；不做战斗/陷阱/死亡系统（本轮范围外），成就「倒下」留给后续内容系统。
2. **m3 首屏「与此同时」句中硬切、拾荒者不可交互**：修 m3 合成句会破坏 m3 golden 的 `text_len`，Character/NPC 建模重构不在本轮；记录为已知限制。
3. **无真实浏览器 E2E（Playwright）**：用系统 Edge headless/CDP 做截图与溢出校验，不做长期 CI e2e。
4. **LLM 默认仍离线**；真实 LLM 为可选一键开启（`play -Llm` / `TEXTGAME_LLM_ENABLED=true`），首回合 7–8s、后续 2–6s，无流式；`无事件回合`（看/背包）的叙事来源徽章显示「离线模板」（TurnResult 未加来源字段）。
5. **M6-028 生成卡 UI** 仍未做（人物卡库/回放/导入已可用）；终端无卡库。
6. `m4` 的 `recipe.signal_torch` 依赖 `item.torch` 模板材料，而 m2/m4 火把 `tags[0]` 为 `light`（历史数据契约），故 m4 无法造出信号火把；story 火把已按模板键修正，旧场景不改。

## 影响

- 玩家：拿到仓库一条命令打开网页版 → 选「余烬哨站」→ 有目标、有历史、有建议、有结局；道具/对话/细看真实生效；手机窄屏可读可点。
- 工程：旧 golden 61 例零改动（replay 现 68 = 61 旧 golden + 7 provenance pin）；质量门全绿；schema 无迁移。
- 后续（下一批建议）：失败结局内容（陷阱/战斗/死亡）、真实浏览器 E2E 入 CI、流式叙事、M6-028 生成卡 UI、m3 建模重构。
