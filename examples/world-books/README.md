# 示例世界书（World Book / Lorebook）

本目录提供两个**可直接导入**的示例文件，配套本项目 `story` 场景「余烬哨站」：

| 文件 | 格式 | 内容 | 导入方式 |
| --- | --- | --- | --- |
| `story-lorebook.json` | SillyTavern `world_info`（`entries` 为 uid map） | **29 条**世界设定：哨站历史、老兵往事、信号灯机制、断桥河谷、地窖/铁匠铺/瞭望塔/枯井、火石/铜线/药酒等 | 世界书 → 导入（`POST /api/v1/world-books/import`） |
| `story-character-card.json` | Character Card V2（`spec_version: 2.0`）+ `data.character_book` | 守夜人老兵人物卡；内嵌 **4 条**人物设定；顶层同时保留本项目人物卡 9 字段 | 世界书 → 导入（自动识别 `data.character_book`，来源标记 `character_card`） |

## 试用（默认离线，无需 API Key）

1. 启动网页版：`.\scripts\play.ps1`，打开 `http://127.0.0.1:8000`。
2. 世界书页导入 `story-lorebook.json`（或角色卡文件）→ 在「新建对局」中选择该世界书 + 场景 `story`。
3. 进入「余烬哨站」后随便走走看看：房间描述、老兵、枯井、铁匠铺会按关键词命中世界设定，叙事中会出现设定补充；对局侧栏「世界设定」显示本回合激活条目。
4. 终端玩家等价命令：`uv run python -m textgame.console --scenario story`（世界书功能以网页/API 为准）。

## 设计说明（触发语义）

- 每条条目 `key` 为 2–6 个中文触发词；`constant` 条目（2 条：世界总览、信号灯机制）无条件命中，`selective` 条目（4 条）用 `keysecondary` 约束话题，其余按主关键词命中。
- 触发上下文 = 当前房间名/描述 + 可见物品与 NPC + 最近几条叙事 + 玩家本回合输入；管理员可用 `scanDepth` 限制窗口（本示例有 4 条使用 `scanDepth: 2`）。
- 文本为 2G 文字 MUD 冷峻笔触，80–200 字，只描述世界事实，不含玩家状态数值与技术术语；内容与 `src/textgame/domain/scenario_story.py` 的房间/NPC/物品设定一致（信号灯已灭两夜、老兵在等、枯井暖得不对劲）。
- 本示例**不追求**覆盖 SillyTavern 全字段（`sticky`/`cooldown`/`group` 等保留原生缺省值），格式兼容范围见 `docs/design/` 的设计文档与 H4 契约。

> 这两份文件是人读内容示例，玩家可自由导入试用；修改后请保持 JSON 合法、UTF-8 编码、≤256KB。
