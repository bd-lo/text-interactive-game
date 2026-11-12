# 示例场景（可直接导入试玩 / 可分享）

本目录的 `*.json` 都是**用户场景包**（纯数据，无脚本/表达式），通过场景库导入后即可开局。
格式契约见 `docs/design/user-scenario-modules.md`（v1）与 `docs/design/scenario-dsl-v2.md`（v2）。

| 文件 | 版本 | 作者 / 标题 | 规模 | 展示能力 |
| --- | --- | --- | --- | --- |
| `tiny-tower.json` | v1 | 示例 / 小塔 | 2 房间 | 最小可玩：房间/物品/目标/结局 |
| `ember-watch.json` | v1 | 示例 / 余烬哨站 | 5 房间 + NPC | 目标链/进入伤害/NPC 对话/多结局 |
| `lantern-vigil.json` | v2 | 示例 / 灯守之夜 | 3 房间 + NPC | flag / 收集计数 / 目标进度 / 时间条件 |
| `moonlit-market.json` | v2 | 分享示例作者 / 月市奇遇（分享示例） | 2 房间 + NPC | **分享用**：导出再见导入 roundtrip 示例 |

## 怎么进入游戏（三条路）

1. 网页：Home →「导入场景」选择 JSON → 场景库出现条目 →「按此场景开局」；
2. 终端：`.venv\Scripts\python.exe -m textgame.console --scenario-id <scn_...>`（先用
   `--list-scenarios` 查 ID；任意场景 JSON 也可先经 `POST /api/v1/scenarios` 导入）；
3. API：`POST /api/v1/scenarios`（JSON 原文或 multipart `file`，必带 `Idempotency-Key`）→
   `POST /api/v1/games {"scenario_id": "<scn_...>", "seed": 7}` → `POST /api/v1/turns`。

## 怎么分享（文件交换，无云端/市场）

- 导出：`GET /api/v1/scenarios/{scenario_id}/export` → `application/json` 附件
  `attachment; filename="<slug>.json"`；响应体就是场景定义原文（保留 `schema_version`/标题/
  作者/flags 等全部字段），**不含**库内 ID/时间戳/服务器路径；
- 导入：把导出的文件原样发给别人，对方在自己的机器上 `POST /api/v1/scenarios`（或网页导入）
  即可；导入仍走 256KB/白名单/幂等校验，不托管用户内容；
- 注意：每次导入都会生成新的内部 `scenario_id`（slug 可重复），因此同一份文件在不同机器/多次
  导入互不影响已开局存档。

> 说明：JSON 格式本身不支持注释，示例的「作者/标题/进入方式」写在 `meta` 字段与上表里。
