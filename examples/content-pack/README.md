# 示例内容包（N1-035–036）

一组**纯数据**示例内容：2 个可玩的 v2 用户场景、1 本世界书、1 张人物卡，外加一键体验脚本（脚本在
仓库 `scripts/` 下，不在本目录内，故本目录只含 JSON/README）。全部内容零联网、零可执行代码（用户 JSON
永不执行，条件只走服务端白名单求值），可直接导入试玩，也可作为「场景 DSL v2 / 世界书 / 人物卡」
三种内容的模板。

| 文件 | 类型 | 版本 | 规模 | 展示能力 |
| --- | --- | --- | --- | --- |
| `scenarios/ashen-lighthouse.json` | 用户场景 | v2 | 4 房间 / 4 物品 / 1 NPC / 6 flags / 3 目标 / 3 结局 | 主场景：`flag` 派生剧情开关、`has_item_tag_count` 收集、`item_in_room`/`npc_in_room`、`turn_gte` 时间失败、`hp_lte` 冻伤失败、多结局 |
| `scenarios/mirror-well.json` | 用户场景 | v2 | 3 房间 / 3 物品 / 1 NPC / 8 flags / 3 目标 / 3 结局 | 分支场景：`objective_done` 目标链、`world_time_gte` 入夜失败、物品/房间条件驱动「拿镜子」与「带绳返回」两条互斥走向 |
| `world-books/ash-coast-lore.json` | 世界书 | 简化 schema | 12 条 | 灰烬灯塔 + 镜井的地方志；绑定场景后按房间/物品/NPC/玩家输入关键词激活，叙事侧栏显示「世界设定」 |
| `character-cards/ember-warden-card.json` | 人物卡 | 项目 9 字段格式（`schema_version: "1.0"`） | 7 属性 / 3 起始物品 | 可单独按卡开局（默认 m2 场景）；起始物品引用已注册模板 `item.torch`/`item.potion`/`item.chalk` |

## 一键体验（推荐）

先决条件：仓库内已装好 `uv`（与 `scripts/play.ps1` 相同；首次运行如缺 `.venv` 会自动安装依赖）。

- Windows PowerShell（5.1 或 7）：
  `.\scripts\quickstart-content-pack.ps1`
- Linux / macOS：
  `./scripts/quickstart-content-pack.sh`

脚本会：**用临时数据库**（不写 `data/textgame.db`）以离线模式启动网页服务 → 自动校验并导入本包全部
JSON → 建一局「灰烬灯塔 + 世界书」并打开对局页；同时在终端打印本局 URL、两个场景 ID、世界书 ID、
人物卡 ID。关闭方式：终端里按 `Ctrl+C`（脚本会结束服务并清理临时库）。
常用参数：`-Check` / `--check`（headless 自检，校验+导入+建局全过则退出码 0）、`-Port` / `--port`、
`-NoBrowser` / `--no-browser`、`-KeepDb` / `--keep-db`（保留临时库以便复看）。

## 手动导入（不用脚本）

前端入口**全部复用既有页面，无新增路由**：`/scenarios/import`（导入场景）、`/scenarios`（场景库 /
按场景开局）、`/world-books/import`（导入世界书）、`/cards/import`（导入人物卡）、`/game/:id`（对局页）。

1. 启动：`.\scripts\play.ps1`（网页）或 `uv run --no-dev python -m textgame.console`（终端）；
2. 网页首页 →「场景库 → 导入场景」选择 `scenarios/*.json`；「世界书 → 导入」选择
   `world-books/ash-coast-lore.json`；「角色卡 → 导入」选择 `character-cards/ember-warden-card.json`；
3. 新建对局：选「灰烬灯塔」+ 世界书 → 开局；或选「镜井」单独玩；或选人物卡按卡开局；
4. 终端等价：先取得 ID（`--list-scenarios` / `--list-cards`），再
   `uv run --no-dev python -m textgame.console --scenario-id <scn_...>`（或 `--card-id <card_...>`）。

API 等价（服务已启动；`Idempotency-Key` 需 ≥8 字符）：

```text
POST /api/v1/scenarios/validate      body = 场景 JSON 原文           # 只读预检
POST /api/v1/scenarios               body = 场景 JSON 原文           # 导入（201）
POST /api/v1/world-books/validate    body = {"payload": 世界书 JSON}  # 只读预检
POST /api/v1/world-books/import      multipart file=<世界书 JSON>     # 导入（201）
POST /api/v1/character-cards/validate  body = 人物卡 JSON             # 只读预检
POST /api/v1/character-cards         body = 人物卡 JSON               # 保存（201）
POST /api/v1/games                   body = {"scenario_id": "...", "seed": 7, "world_book_id": "..."}
```

## 玩法提示（不剧透）

- **灰烬灯塔**：先和守塔人说话拿线索；塔梯向下是地窖（每次进入会掉血），灯油和灯芯都带 `quest`
  标签，集齐后回塔梯向上进灯室即判定重燃；拖到第 8 回合还未集齐材料会走向「涨潮」失败。
- **镜井**：和卜者打听后拿麻绳下井；下井会掉 4 点血，**掉过血再带着麻绳回到荒院**即判定成功
  （HP 是「下去过」的状态证据，不需要记住历史）；拿走古镜会立刻走向「镜中的你」失败结局。
  入夜（世界时间 ≥6）还没拿绳也会失败——两条走向都由状态决定，不靠随机。
- 两个场景的结局都是**读时求值**：同一状态必定得到同一结局，结局后拒绝新回合；条件只读既有
  状态，不写事件/存档。

## 约束与说明

- 所有 JSON 为 UTF-8、单文件 ≤256KB；导入走既有 256KB/白名单/幂等校验，非法定义会被结构化错误拒绝；
- 场景 `schema_version=2`，用到的条件谓词见 `docs/design/scenario-dsl-v2.md` §3，**无脚本/表达式**；
- 人物卡为项目人物卡格式（非 SillyTavern 卡）；世界书为项目简化 schema（`{"name", "entries": [...]}`），
  服务端也兼容 SillyTavern `world_info` / Character Card V2 内嵌书；
- 本目录与 `examples/scenarios/`、`examples/world-books/` 的既有示例相互独立，可同时导入；
  修改后请保持 JSON 合法并重跑 `tests/integration/test_content_pack_examples.py`。
