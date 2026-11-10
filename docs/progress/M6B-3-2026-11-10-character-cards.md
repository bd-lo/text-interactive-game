# M6B-3 · 2026-11-10 · M6 后端前置（不占编号）· character-cards

> 切片目标一句话：交付 M6 后端前置人物卡库——7 个 `character-cards` 端点 + `character_cards` 表/Alembic 0003（可逆）+ `POST /games` 的 `card_id` 加法语义（D-M6-2，按卡映射初始 `Character`）｜ 验收命令：`uv run pytest tests/integration/test_character_card_api.py tests/integration/test_create_game_with_card.py -q` ｜ 里程碑 M6（后端前置，不占 025–028 编号）｜ 决策：D-M6-1/2/5、D-M5-10（0003 顺延）、D-M6-8（实现偏差）

## 1. 目标与验收命令

- 目标（第 5 章 §5.7、第 6 章 §6.7、D-M6-1/2）：① `domain/character_card.py`：`CharacterCard` 9 字段 DTO（`extra=forbid`、`schema_version="1.0"`、属性边界/`hp<=max_hp`/物品模板校验）+ `build_character_card_schema()` 输出 **draft 2020-12** JSON Schema（前端 Ajv2020 复用）+ 内置物品模板目录；② `services/card_service.py` + `persistence/card_repository.py` + `character_cards` 表（独立于事件溯源，卡库写零 `Event`）；③ **7 个端点**：`GET /schema`、`GET ""`、`GET /{card_id}`、`POST /import`、`POST /validate`、`POST ""`（save）、`DELETE /{card_id}`；`POST /generate` 留 028（依赖 M5 LLM 接线）；④ Alembic **0003**（head 0002→0003，`legacy_upgrade._HEAD_REVISION` 同步；纯加法、可逆）；⑤ `POST /games` 加法：body `{card_id?, seed?, scenario?}`（同给 `card_id` 与 `seed`/`scenario` → 422），按卡映射 `PLAYER_ID` 槽位的 name/attributes/hp/inventory（`starting_items`→确定性实例 ID、`tags[0]=模板键`；`background`/`appearance` 只留卡库），未知卡 404，**无卡路径逐字不变**；`game_engine.create_game` 可选 `initial_state`/`card_id`（默认 None ⇒ 事件/哈希不变）。
- 验收命令与真实输出：
  - `uv run pytest tests/integration/test_character_card_api.py -q` → **59 passed**
  - `uv run pytest tests/integration/test_create_game_with_card.py -q` → **18 passed**
  - `uv run pytest -q` → **1376 passed in 19.50s**；`uv run pytest tests/replay -q` → **61 passed**（逐值不变）
  - Alembic：`head = 0003`（0001→0002→0003 升级/回滚用例通过）；覆盖率 **88.93%**；四门 167/89/2 kept 全绿
- 不做什么：不做 `POST /character-cards/generate`（留 028）；不改既有 5 张表/事件结构/golden；不新增 Python 依赖（不引入 python-multipart）；不做前端卡库 UI（026）。

## 2. 改动与提交

- 提交：`c9b7348 feat: M5-023 batch-sleep + M6 后端前置（character-cards + card_id）`
- 文件：`src/textgame/domain/character_card.py`（286）、`src/textgame/services/card_service.py`（652）、`src/textgame/api/routes/cards.py`（311）、`src/textgame/persistence/card_repository.py`（172）、`src/textgame/persistence/tables.py`（+30）、`alembic/versions/0003_character_cards.py`（48）、`src/textgame/persistence/legacy_upgrade.py`（+16）、`src/textgame/api/{schemas.py,errors.py,app.py}`、`src/textgame/api/routes/games.py`（+40 card_id）、`src/textgame/services/game_engine.py`（+29 可选 initial_state/card_id）、`src/textgame/main.py`（+10 装配）、`web/src/api/types.ts`（+5 card_id?）；新增测试 `test_character_card_api.py`（850 行，59 例）、`test_create_game_with_card.py`（469 行，18 例）、`test_alembic_migration.py`/`test_legacy_upgrade.py` 扩展。
- 关键 diff 摘要：卡库路由前缀 `/api/v1/character-cards`；multipart 用**标准库 email 解析**（不新增 python-multipart，OpenAPI 以 `openapi_extra` 补 requestBody）；写端点走既有 `idempotency` 表（同键同参回放含失败响应、同键异参 409）；错误码：>64KB→413、XSS/HTML/JS-URI/控制符→403、非法 JSON/类型/重复键→422、模板或未知卡→404、冲突→409。
- 已知实现偏差（**D-M6-8**）：① 不新增 python-multipart（标准库 multipart + OpenAPI 手补）；② `import` 强制 `source=upload`（客户端给其他 source 时记 `SOURCE_OVERRIDDEN` warning）；③ `list` 默认 `limit=20`/上限 `100`；④ `starting_items` 重复模板合并（`DUPLICATE_ITEM_MERGED` warning）。
- 契约变更：D-M6-2 落地（加法）；`character_cards` 表与 0003 迁移落地（D-M5-10 顺延编号落实）；D-M6-8 实现偏差登记 PROGRESS §5。

## 3. 验证结果

| 检查 | 结果 |
| --- | --- |
| 卡库 API | 59 passed：schema/list/get/import/validate/save/delete 全路径；大小/类型/字段白名单/禁 HTML/幂等/软删除/404/409/413/403/422；卡库写零 events |
| card_id 建局 | 18 passed：按卡映射 name/attributes/hp/inventory（确定性实例 ID、`tags[0]` 模板键）；`card_id`+`seed/scenario`→422；未知卡 404；无卡路径逐字不变（事件/哈希/golden 零回归） |
| 迁移 | Alembic head=0003；0003 升级/回滚可逆；`legacy_upgrade` 字面量同步；既有 5 表不变 |
| 全量/回放 | 1376 passed / replay 61 零回归；覆盖率 88.93%；四门 167/89/2 kept 全绿 |
| 失败与回滚 | 无 |

## 4. 下一步与风险

- 下一步：**028-llm-card-generate**（`POST /character-cards/generate`，依赖 M5 出口 LLM 接线）与 **026-card-import**（前端卡库 UI，依赖本片 7 端点）；M6 后端前置剩余项 = generate 端点（无新迁移，0003 已占用）。
- 风险/已知限制：① 卡库**不参与事件溯源**（设计如此；只有 `POST /games` 写入 `game_created`）；② 上传用标准库 multipart 解析，后续若接大文件/多文件需重评（当前 ≤64KB 够用）；③ `player_id` 槽位沿用固定 `PLAYER_ID`，卡内没有角色 ID（前端展示用卡库 `card_id`）；④ generate 端点未做期间 028 不可开工。
- 需要人工决策：无（本轮）。
