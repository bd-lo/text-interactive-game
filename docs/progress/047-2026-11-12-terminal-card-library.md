# 047 · 2026-11-12 · M6 后 · N3 功能轨 · terminal-card-library（终端卡库浏览增强）

> 切片目标一句话：把终端「只读列表」升级为可浏览卡库——`--list-cards` 补关键词过滤（标题/来源）、`--card-limit`（1..100，默认 20）、`--card-all`（上限内全量，单次最多 100 张），新增只读 `--card-detail <卡ID>`（安全子集：标题/来源/属性摘要/背包提示/警告）与玩家可读错误；只读浏览、不建局、不改默认开局路径｜ 验收命令：`.venv\Scripts\python.exe -m pytest -q tests/integration/test_console_cards.py` 等 console 五件套 ｜ 里程碑 M6 后（N3 功能轨，编号区间 045–048）｜ 执行/验收 n3-features

## 1. 目标与验收命令

- 目标（一个垂直小功能）：`--list-cards` 支持 `--card-query`（客户端按标题/来源标签过滤）、`--card-limit`（1..100、默认 20）、`--card-all`（单次最多 100 张），逐卡展示标题/来源标签/更新时间（有则显示）/完整卡 ID，并给按卡开局与单卡详情两行可玩提示；新增 `--card-detail <卡ID>` 复用 `GET /api/v1/character-cards/{id}`，只展示标题/来源/属性摘要/背包提示/警告；不存在的卡/非法 ID/后端不可用给中文可读提示 + 退出码非 0（不显示端点或异常细节）；互斥/范围校验清晰。
- 验收命令/断言（先定后写）：
  - `.venv\Scripts\python.exe -m pytest -q tests/integration/test_console_cards.py` → **29 passed**
  - `.venv\Scripts\python.exe -m pytest -q tests/integration/test_console_cards.py tests/integration/test_console_card.py tests/integration/test_console_scenario.py tests/integration/test_console_story.py tests/integration/test_console_walkthrough.py` → **59 passed**（新增 29 + 旧 30）
  - `.venv\Scripts\python.exe -m textgame.console --check` → exit 0，改动前后逐字一致（SHA256 均 `C1BE0085…CB8F`）
  - `.venv\Scripts\python.exe -m textgame.demo --check` → exit 0，改动前后逐字一致（SHA256 均 `F36D3112…2E72`）
- 不做什么：不改 web/、API 契约/事件/状态/golden；零新增运行时依赖；不改 `--card-id` 开局与无人参默认交互路径；浏览只读（不建局、不写卡库/事件）；不碰 `docs/PROGRESS.md`/`CHANGELOG.md`/`docs/开发文档.md`。

## 2. 改动与提交

- 文件：`src/textgame/console.py`（卡库浏览增强与详情）、`tests/integration/test_console_cards.py`（新建，29 例）、`docs/progress/047-2026-11-13-terminal-card-library.md`（本篇）。
- 提交：未提交（不 commit；改动留工作区由 Lead 统一提交）。
- 关键 diff 摘要：① 列表：查询/限量/全量三参数 + 客户端过滤（标题/来源标签/来源原值，大小写不敏感）+ 更新时间格式化 + 超量/无匹配提示；② 详情：`_card_detail_lines` 渲染安全子集，物品模板 ID 经 `ITEM_TEMPLATES` 映射为可读名，HP 未满给警告；③ `_validate_card_browse_args` 统一互斥/范围校验（详情与 `--list-cards`/`--card-id`/`--list`/`--list-scenarios` 互斥；浏览参数必须搭配 `--list-cards`；`--card-all` 与 `--card-limit` 互斥；limit 1..100）；④ 只读装配失败/后端非 200 转中文可读 `ConsoleError`（不再显示端点路径）；⑤ 交互帮助补一行卡库浏览用法。
- 护栏检查：只读 GET（列表/详情），未建局/未写事件/未写卡库；`--check` 4 轮与 demo 7 屏逐字不变；未改 `run_check`/默认开局路径；无密钥入库；未改 web/API。
- 契约变更：无（复用既有 `GET /character-cards`、`GET /character-cards/{id}`；`--card-query` 为终端客户端过滤，服务端契约零改动）。

## 3. 验证结果

- lint / type：`ruff format --check`/`ruff check` 变更文件通过；`lint-imports` → 2 contracts kept, 0 broken；`mypy src/textgame/console.py` → no issues。全量 `mypy src/textgame` 当前报 3 处错误，全部在并行 038 任务正在修改的 `src/textgame/llm/litellm_client.py`（非本片改动文件），需 Lead 在并行任务收尾后统一复跑。
- 单测：console 五件套 **59 passed**；另跑 `test_r3_polish_acceptance.py -k console` 2 passed（旧 `--list-cards` 断言零改动仍成立）。
- demo/console 自检：`console --check` 4/4 回合 exit 0（哈希前后一致）；`demo --check` 7/7 屏 exit 0（哈希前后一致）。
- 手工验收（临时库 3 张卡：手填/LLM/导入）：列表默认 3 行含来源标签/更新时间/完整卡 ID；`--card-query 导入` 1 行；`--card-limit 2` 2 行 + 更多提示；`--card-detail <手填卡>` 显示 HP 7/18、六维属性、背包治疗药水与「生命值未满」警告；`--card-detail not-a-card` / 未知卡 / `--card-limit 0` / 参数互斥均 exit 1 中文可读。
- 失败与回滚：无。

## 4. 下一步与风险

- 下一步：N3-048 · save-export-import（存档导出/导入），验收命令 `pytest -q tests/integration/test_saves_export_import.py`（由 Lead 追加派单）。
- 风险/开放问题：① `--card-query` 只在后端单次上限 100 张内做客户端过滤（卡库 >100 张时提示去网页版卡库），如后续需要全库检索应改为服务端 `query` + 分页；② 详情为安全子集，不渲染 `background` 自由文本（需完整预览请用网页版卡库）；③ `--help` 走 argparse 默认英文 usage/错误（既有行为，未改）。
- 需要人工决策：无（本轮）。
