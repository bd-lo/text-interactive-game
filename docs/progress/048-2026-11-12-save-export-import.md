# 048 · 2026-11-12 · M6 后 · N3 功能轨 · save-export-import（存档导出/导入，后端优先）

> 切片目标一句话：玩家把一局游戏导出为**单个 JSON 文件**、导入本机库后从同一进度继续玩——导出只读；导入先验包结构/checksum/事件回放一致性（失败零写入）再经唯一写入者 `GameEngine` 重放为新存档，回放 `state_hash` 与导出端一致，同键同包幂等、同键异包 409｜ 验收命令：`.venv\Scripts\python.exe -m pytest -q tests/integration/test_saves_export_import.py` ｜ 里程碑 M6 后（N3 功能轨，编号区间 045–048）｜ 执行/验收 n3-features

## 1. 目标与验收命令

- 目标（一个垂直小功能）：新增 `GET /api/v1/games/{game_id}/export`（只读单文件包：`export_version/exported_at/game/events/snapshot/checksum`，不含 API Key/凭据/日志）与 `POST /api/v1/saves/import`（必带 `Idempotency-Key`；字段白名单 + checksum + 事件 `Event` 模型 + `EventLog`/`apply_events` 全量回放 + `state_hash`/版本/回合/序号一致性校验，全过才写库；成功新 `game_id`，可 `/view`/`/replay` 并继续 `POST /turns`；上限 8MB/20000 事件）。
- 验收命令/断言（先定后写）：
  - `.venv\Scripts\python.exe -m pytest -q tests/integration/test_saves_export_import.py` → **15 passed**
  - `.venv\Scripts\python.exe -m pytest -q tests/replay` → **69 passed**（旧 golden 零改动）
  - console 五件套 **59 passed**；API 周边（healthz/frontend_contract/app_control/error_envelope）**49 passed**；create_game_with_card/world_book_flow/static_spa **45 passed**
  - `.venv\Scripts\python.exe -m textgame.console --check` → exit 0，前后 SHA256 一致（`C1BE0085…CB8F`）
  - `.venv\Scripts\python.exe -m textgame.demo --check` → exit 0，前后 SHA256 一致（`F36D3112…2E72`）
- 不做什么：不新增 EventType/存储/迁移/运行时依赖；不改 `GameState`/旧 golden/`/turns` 契约；不改 web/；网页 UI 与终端入口由后续批次接；不做云分享/签名（checksum 仅完整性校验）。

## 2. 改动与提交

- 文件：`src/textgame/services/save_service.py`（新：包 DTO/校验和/校验流水线/唯一写入者重放/幂等）、`src/textgame/api/routes/saves.py`（新：2 端点 + 依赖装配）、`src/textgame/api/save_schemas.py`（新：`SaveImportResponse`；按 Lead 要求独立模块，避免与并行改动的 `api/schemas.py` 冲突）、`src/textgame/api/app.py`（注册路由；R-18 请求体中间件**仅对导入路径**放宽到 8MB+64KB，其余端点 1MB 不变；组合根经编排器已持有的 repo/engine 装配 `SaveService`）、`tests/integration/test_saves_export_import.py`（新，15 例）、`docs/design/save-export-import.md`（新，包 schema/校验/安全/限制）、`docs/开发文档/第06章-…md`（§6.8 回写）、本篇。
- 提交：未提交（不 commit；改动留工作区由 Lead 统一提交）。
- 关键 diff 摘要：导出端只读事件表/当前状态/最新快照 → 组包并写 checksum（`sha256(canonical_json(包去掉 checksum))`）；导入端校验流水线见 §1 目标；写入 = `create_game(initial_state=首事件.result.initial_state)` + 按回合把事件转草稿逐次 `submit`（`seq/rng_seed` 同种子重派生故逐值一致，仅 `ts` 为导入时刻、不参与状态哈希）；幂等存储键 `save:import:<key>`，非法包不写幂等表。
- 护栏检查：导出只读（零事件/零快照/零幂等写）；导入校验失败零写入（测试断言 `games total` 不变）；未改 domain/EventType/旧 golden；无密钥入库；未改 web；未触碰 `api/schemas.py`、`main.py`、`orchestration/*`（与并行流式任务无共享文件冲突）。
- 契约变更：**新增 2 端点**（第 6 章 §6.8 已同批回写）；既有端点契约零改动；前端类型暂未接（后续前端批次按 §6.8 契约接入，无需改动既有类型）。

## 3. 验证结果

- lint / type：`ruff format --check`/`ruff check` 变更文件通过；`mypy src/textgame` → **117 source files no issues**；`lint-imports` → 2 contracts kept / 0 broken。
- 单测：新文件 **15 passed**（导出白名单/无密钥/404；导入→视图相等→回放哈希一致→继续回合；重启后仍可读；源存档不变；checksum/state_hash/rng_seed/未知字段/版本/事件数/包体积/缺幂等键/非法 JSON 拒绝且零写入；同键同包回放、同键异包 409）。
- 回放/集成：`tests/replay` **69 passed**（零回归）；console 五件套 **59 passed**；API 周边（healthz/frontend_contract/app_control/error_envelope）**64 passed**；create_game_with_card/world_book_flow/static_spa **45 passed**。
- demo/console 自检：`console --check` 4/4 回合 exit 0（SHA256 前后一致）；`demo --check` 7/7 屏 exit 0（SHA256 前后一致）。
- 手工验收（临时库）：m2 局 4 事件导出 → 导入新 `game_id` → `/view` 与源局一致（仅 game_id 不同）→ 再导出 `snapshot.state_hash` 相同、`seq/rng_seed` 逐值相同 → 导入局 `POST /turns` 正常推进；篡改事件（重签）→ 422；同键重放 → 同一 201 响应且只多一局。
- 失败与回滚：无。

## 4. 下一步与风险

- 下一步：网页导出/导入 UI（后续前端批次）：`GET .../export` 下载文件、`POST /saves/import` 上传后跳转 `/games/{new_id}`；契约见第 6 章 §6.8 + `docs/design/save-export-import.md`。
- 风险/开放问题：① checksum 非数字签名，重签后仅改叙事展示文本的篡改检测不到（本地文件交换场景接受，云分享需再加签名）；② 绑定元数据（`card_id`/`scenario_id`/`world_book_id`）不随包迁移，世界书条目注入不保留（状态完整；如需保留后续加可选 `bindings` 字段并做存在性校验）；③ 写库阶段基础设施异常（磁盘满/崩溃）无法回滚半成品（校验失败严格零写入）；④ 导入路径请求体上限 8MB+64KB 是 R-18 的**单路径例外**，其它端点仍 1MB。
- 需 Lead 统一处理：`scripts/check_docs.py` 当前 FAIL 于「第 06 章拼接不一致」（需重建 `docs/开发文档.md`）与「048 未在 PROGRESS §3 索引」；`docs/开发文档.md`/`docs/PROGRESS.md` 均为我禁改文件。
- 需要人工决策：无（本轮）。
