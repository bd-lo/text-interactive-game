# WB2 · 世界书详情字段可见性补齐（范围外小批，不占 001–028 编号）

> 日期：2026-11-11 ｜ 里程碑：范围外「UC 收尾批次」小项 ｜ 状态：**已完成**
> 决策：沿用 `docs/decisions/2026-11-11-user-content-and-llm-runtime.md`（D-WB-1..4）
> 设计：`docs/design/world-book-format-compat.md` §5 **D-11（新增：详情 API 可见性）**

## 1. 目标与判定口径

UC 批次（2026-11-11）交付了酒馆级世界书触发语义（定时效果/正则键/包含组/position-depth），
但 **`GET /api/v1/world-books/{world_book_id}` 详情只返回 H4 固定字段**：`probability`/
`use_probability`/`group*`/`scan_depth`/`sticky`/`cooldown`/`delay` 只存在于 `raw_json`，
玩家在详情页看不到「为什么这条设定会这样触发」。本小批补齐 D-WB 可见性缺口。

判定口径：

- **只增不改**：H4 既有 11 个响应字段逐字保留；新增 14 个字段一律由服务端从 `raw_json`
  还原（`parse_entry_extras`，与激活决策**同一解析函数**），不新增表/迁移/事件；
- **零回归**：激活行为、golden 回放、demo/console `--check` 全部不变（本次不改 `domain/world_book.py`）；
- **展示层零业务判断**：前端只渲染字段与中文标签，不做概率/分组/定时计算；
- **契约同步**：`api/schemas.py` ↔ `web/src/api/types.ts` ↔ `web/src/api/client.ts` 运行时校验 ↔
  `tests/integration/test_frontend_contract.py` 四处同批更新（`extra="forbid"` 漂移即红）。

## 2. 交付内容

### 2.1 后端：详情响应新增 14 字段

`services/world_book_service.py` 新增 `_entry_detail(entry)`：

- 固定列：`case_sensitive`/`match_whole_words`/`exclude_recursion`/`prevent_recursion`（H4 已有列，此前未返回）；
- `raw_json` 还原（`parse_entry_extras`）：`probability`/`use_probability`/`group`/`group_weight`/
  `group_override`/`group_scoring`/`scan_depth`/`sticky`/`cooldown`/`delay`；
- 缺字段的旧数据回退解析默认值：`100`/`False`/`""`/`1.0`/`None`/`0`；
- `api/schemas.py::WorldBookEntryResponse` 同步 14 字段（全部必填，服务端总是给值）。

### 2.2 前端：详情条目表展示触发语义

- `web/src/api/types.ts::WorldBookEntry` 同步 14 字段；`web/src/api/client.ts::WORLD_BOOK_ENTRY_KEYS`
  运行时字段集合校验同步（不裸 `as T`）；
- `utils/worldBooks.ts` 新增 `labelWorldBookPosition`（ST 位置枚举 0..7 中文 + 未知回退）与
  `labelTimedEffect`（粘性/冷却/延迟 N 回合；0/负数/NaN → 空串）；
- `components/WorldBookEntryTable.vue` 增强：头部标签新增「概率 N%」「组「name」」（悬停显示
  权重/强制优先/组内评分）与定时效果标签（`world-tag-timed` 强调色）；正文下方 meta 行显示
  「位置（含 @D 时 depth N）· 扫描深度 N · 区分大小写 · 整词匹配 · 不参与递归 · 阻止向下递归」；
  默认值条目不渲染空标签（无噪音）。

### 2.3 测试与证据

- 后端：`test_world_book_routes.py` 详情字段集合断言扩到 25 键 + 默认值断言；
  `test_world_book_tavern_flow.py` 新增 `test_detail_exposes_p1_trigger_fields_from_raw_json`（1 例）；
- 前端：`worldBookQa.test.ts` 新增 P1 标签用例；`lorebookViews.test.ts` 新增条目表 2 例
  （全字段渲染 / 默认值不渲染），详情页用例补 4 条可见性断言；三处 fixture 补全字段；
- e2e：世界书导入 payload 增 P1 字段（`position/depth/probability/useProbability/scanDepth/group/sticky/cooldown`），
  `wb.import` 步骤新增 3 条断言（概率+包含组 / 定时效果 / position-depth+扫描深度）→ **25 步/122 断言**；
- 契约：`test_frontend_contract.py` 21 passed（TS ↔ OpenAPI 字段集合一致，无需改测试体）。

## 3. 验证结果（Lead 实跑）

| 项 | 结果 |
| --- | --- |
| 全量 pytest | **2156 passed / 0 failed**（基线 2155 + 世界书详情 1 例） |
| 回放 | `tests/replay` **68 passed**（旧 golden 零改动） |
| web | `pnpm --dir web test` **53 files / 329 tests**（+3）；`typecheck` 0；`build` 成功；tracked `web/dist` 重建 |
| 浏览器 e2e | **25/25 步、122/122 断言**（新增 3 条世界书详情可见性断言；23 张截图；`result.json` 实跑 PASS） |
| 契约测试 | `test_frontend_contract.py` **21 passed** |
| 质量门 | ruff format/check、mypy strict、lint-imports（2 kept）、check_docs **PASS** |
| 数据库 | 无迁移（Alembic head 仍 **0005**） |

## 4. 改动文件

- 后端：`src/textgame/services/world_book_service.py`、`src/textgame/api/schemas.py`；
- 前端：`web/src/api/types.ts`、`web/src/api/client.ts`、`web/src/utils/worldBooks.ts`、
  `web/src/components/WorldBookEntryTable.vue`、`web/src/styles/terminal.css`、`web/dist/**`（重建）；
- 测试：`tests/integration/test_world_book_routes.py`、`tests/integration/test_world_book_tavern_flow.py`、
  `web/tests/unit/{worldBookQa,lorebookViews,lorebookClient,lorebookStore}.test.ts`、`scripts/e2e-web.mjs`；
- 文档：`docs/design/world-book-format-compat.md`（§5 D-11）、本进度文档、`docs/PROGRESS.md`、
  `docs/progress/UC-2026-11-11-user-content-and-llm.md` §5 标注已补齐。

## 5. 已知限制/后续

1. 详情页仍为只读（条目编辑/新增另属「场景编辑器」轨道，见第 17 章 §17.9 M6 后计划）；
2. `vectorized`/`outlet`/`characterFilter` 仍不支持（D-11 只解决已有语义的可见性，不扩语义）；
3. 定时效果需要逐回合事件历史，详情页只展示配置值，不预测「本回合是否激活」（激活状态见对局内「世界设定」面板）。
