# OSS-2 · 2026-11-10 · 入口轨（不占编号）· console-safe-pack

> 切片目标一句话：交付终端入口安全包——console 支持 `--scenario m2|m3|m4`、默认自动继续同库最近存档（`--new`/`--continue` 覆盖）、首屏引导只用只读视图真实数据、失败回合渲染结构化提示；配套剧情/任务物品「使用」保护（不再被永久销毁）｜ 验收命令：`uv run pytest -q tests/integration/test_console_walkthrough.py tests/integration/test_console_scenario.py tests/integration/test_item_use_guard.py` ｜ 里程碑 M6（入口轨，不占 025–028 编号）｜ 决策：D-TEAM-3/5、D-M6-4/6、R1 玩家实测 P0-3 安全子集

## 1. 目标与验收命令

- 目标（R1 玩家/采用实测断点；零文案改动、零 golden 重钉）：① console 可切场景（M3/M4 在「一条命令开玩」路径首次可达）；② 默认继续同库最近存档（可断点续玩），`--new` 显式新开、`--continue` 强制继续；③ 首屏「当前可做」引导与 `【提示】` 命令提示只来自只读视图/固定命令表，不编造剧情；④ 失败回合保留 16 字世界内失败文案 +「（这一回合没有生效）」原文，另渲染服务端 `suggested_actions` 一行「可以试试：…」与 `effects[].hint` 一行「提示：…」；⑤ `use_item` 对剧情/任务标签物品（`quest`/`story` 前缀）拒绝消耗：`INVALID_INPUT`、零事件、背包不变，普通消耗品行为不变。
- 验收命令与真实输出：
  - `uv run pytest -q tests/integration/test_console_walkthrough.py tests/integration/test_console_scenario.py tests/integration/test_item_use_guard.py` → **11 + 10 + 16 passed**
  - `uv run pytest -q` → **1249 passed in 18.51s**；`uv run pytest tests/replay -q` → **61 passed**（逐值不变）
  - `uv run python -m textgame.demo --check` → exit 0，7 屏字数 114/83/81/85/82/84/81 不变
  - `uv run python -m textgame.console --check` → exit 0（**失败回合比 OSS-1 多两行「可以试试/提示」，属 A4 预期新增**；叙事与 16 字失败文案原文未变）
- 不做什么：不改叙事模板/16 字失败文案/golden（D-TEAM-5 红线）；不把状态行写进叙事正文；不接真实 LLM；不重钉 M2/M3/M4 回放。

## 2. 改动与提交

- 提交：`2410d77 slice: OSS-2 终端安全包（切场景/续玩/提示）+ 剧情物品保护`；`249b704`（A4 服务端字段部分，见下）
- 文件：`src/textgame/console.py`（+322：`--scenario`/`--new`/`--continue`、最新存档选择、首屏引导、失败提示渲染、视图降级不伪造）、`src/textgame/services/action_service.py`（`use_item` 前置保护：`_STORY_TAG_PREFIXES=("quest","story")` 前缀匹配 → `INVALID_INPUT`，普通物品零改动）、`tests/integration/test_console_scenario.py`（281 行，10 例）、`tests/integration/test_console_walkthrough.py`（扩展至 11 例）、`tests/integration/test_item_use_guard.py`（275 行，16 例）。
- A4（失败提示字段，提交 `249b704`）：`TurnResult.effects`/`suggested_actions` 为 005 起的恒空加法字段，本批由 `TurnOrchestrator._with_failure_hints` 按 `ErrorCode` 映射固定中文提示填充（只读 `error_code`、不拼接内部错误消息、不改 `text`/事件/version），`tests/integration/test_turn_hints.py` **6 例**。
- 关键 diff 摘要：console 启动时按 `TEXTGAME_DB_URL` 选择「最近存档」→ `GET /games/{id}` 读版本链 → 续玩；`--scenario` 透传 `POST /games`；失败时 `render_turn` 在固定提示后追加服务端提示行；`action_service.use_item` 在背包校验后、事件草稿前拒绝剧情物品。
- 护栏检查：M2/M3/M4 golden 零重钉、`text_len`/16 字失败文案原文不变；`GameEngine`/Event/Resolver/narrator 未改；无新依赖；无密钥。
- 契约变更：`/api/v1/turns` 响应 `effects`/`suggested_actions` 由恒空改为失败回合填充（加法字段，旧客户端忽略）；`use_item` 新增剧情物品拒绝分支（第 7 章工具矩阵语义内的安全前置，登记 PROGRESS §5）。

## 3. 验证结果

| 检查 | 结果 |
| --- | --- |
| console 场景/续玩 | `test_console_scenario.py` 10 passed：m2/m3/m4 切换、默认继续最新存档、`--new`/`--continue`、无存档新开、视图缺失降级不伪造 |
| console 回合 | `test_console_walkthrough.py` 11 passed（原 5 + 新增 6）：失败提示两行、16 字文案不变、`:inv/:status/:help`、状态行 |
| 剧情物品保护 | `test_item_use_guard.py` 16 passed：古物 `use` 端到端被拒（零事件/版本不变/背包不变）、普通药水/火把行为不变、`quest*`/`story*` 前缀参数化、console 路径同口径 |
| A4 提示字段 | `test_turn_hints.py` 6 passed：`effects[].hint`/`suggested_actions` 加法、`text`/事件/version 不变 |
| demo/console 自检 | `demo --check` 7/7 exit 0（字数不变）；`console --check` exit 0（新增两行属预期） |
| 全量/回放 | 1249 passed / replay 61 零回归；四门全绿 |
| 失败与回滚 | 无 |

## 4. 下一步与风险

- 下一步：M6-027-lite（浏览器对局回路，消费 025 骨架 + 本片提示字段）与 M5-021；M5 剩余顺序 021→024→023（D-TEAM-2）。
- 风险：① 剧情物品保护为首版**标签前缀启发式**（`quest`/`story`），内容系统接入后需正规格化「任务物品」标记；② 失败提示为**首版固定文案**（按 `ErrorCode` 映射，不拼接服务端内部消息）；③ `console --check` 输出新增两行属预期，若外部脚本按旧输出精确匹配需同步（OSS-1 验收脚本不受影响）；④ 真实终端中文 IME 仍未由真人验证（延续 OSS-1 遗留）。
- 需要人工决策：无（本轮）。
