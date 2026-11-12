# 存档导出/导入设计（N3-048，2026-11-12）

> 切片：N3 功能轨 048 · 目标：玩家把一局游戏导出为**单个 JSON 文件**，导入本机库后从同一进度继续玩；只读安全、非法/篡改拒绝、回放哈希一致。网页 UI 由后续前端批次接入，本设计只覆盖后端契约与实现。
> 关联：第 17 章 §17.9.2（N3-048 行）、第 14 章 §14.4/§14.6（快照/事件/回放）、第 6 章 §6.7/§6.8（API 口径）、第 22 章 §22.4（进度文档）。
> 代码：`src/textgame/services/save_service.py`（校验/导出/导入）、`src/textgame/api/routes/saves.py`（HTTP）、`src/textgame/api/save_schemas.py`（响应 DTO）、`tests/integration/test_saves_export_import.py`（15 例）。

## 1. 端点

| 方法 | 路径 | 说明 | 成功 | 主要错误 |
| --- | --- | --- | --- | --- |
| GET | `/api/v1/games/{game_id}/export` | 只读导出存档包（不改库、不写事件/快照） | 200 包 JSON | 404 `NOT_FOUND`；422 `OUT_OF_RANGE`（超 8MB/2 万事件） |
| POST | `/api/v1/saves/import` | 校验后导入为**新的一局**（必带 `Idempotency-Key`） | 201 `{game_id, turn, version, snapshot_version}` | 422 `SCHEMA_VIOLATION`（结构/校验和/回放不一致）、422 `INVALID_INPUT`（版本不支持/事件数）、413 `INVALID_INPUT`（包体超限）、409 `CONFLICT`（同键异包）、422 `INVALID_INPUT`（缺幂等键） |

导出包是**数据文件**而非领域实体：不新增数据库表/迁移，不新增 `EventType`，不参与 `GameState` 字段变更。

## 2. 导出包 schema（`export_version = "1"`）

```json
{
  "export_version": "1",
  "exported_at": "2026-11-12T11:56:03.529556+00:00",
  "game": {
    "game_id": "game_012c49150ad84b0790fa2fd734694124",
    "turn": 4, "version": 4, "snapshot_version": 1,
    "save_seed": "save-42", "scenario": "m2",
    "created_at": "2026-11-12T11:56:03.529556+00:00", "event_count": 4
  },
  "events": [
    {"id": "evt_000001", "seq": 1, "turn_id": 1, "actor_id": "system",
     "method": "game_created", "event_type": "game_created",
     "params": {}, "result": {"initial_state": {}}, "rng_seed": 0,
     "ts": "2026-11-12T11:56:03.529556+00:00"}
  ],
  "snapshot": {
    "state_hash": "<sha256(canonical_json(GameState))>",
    "version": 4, "last_event_seq": 4, "turn": 4, "snapshot_version": 1
  },
  "checksum": "<sha256(canonical_json(包去掉 checksum))>"
}
```

- `game`/`snapshot` 是**当前提交头**（`state_hash` 取当前状态，而非最近快照那一刻）：导入端用全量事件回放重算同一状态，才允许写入。
- `checksum` 是**完整性校验和**（规范化 JSON 哈希），防意外损坏/简单篡改；不是数字签名（见 §6 限制）。
- 字段白名单：顶层 6 键、`game`/`snapshot` 各定长字段集、事件 10 键，`extra="forbid"`；未知字段直接 422。
- 不含 `Settings`/API Key/凭据/日志/`last_text` 之外的自由文本：事件只存领域数据与叙事 `result.narrative`。

## 3. 导入校验流水线（全部在写库之前，失败零写入）

1. **checksum**：`package_checksum(package)` 与包内声明比对；不一致 → 422 `SCHEMA_VIOLATION`（`loc=["checksum"]`）。
2. **上限**：规范化 JSON ≤ 8 MiB、事件 ≤ 20000 条 → 超限 422/413 `INVALID_INPUT`；HTTP 层对导入路径单独放行到 8MB+64KB（其余端点保持 R-18 的 1MB）。
3. **结构**：Pydantic `SavePackage` 白名单解析（未知字段/类型/范围 → 422 + `violations[].loc/type/msg`）。
4. **版本**：`export_version != "1"` → 422 `INVALID_INPUT`（不猜未来格式）。
5. **事件**：包内事件经领域 `Event` 模型重校验（序号/回合/角色/方法/事件类型/`rng_seed`/UTC `ts`），未知 `EventType` 拒绝。
6. **回放**：`EventLog(save_seed).append(events).replay()` 校验 `seq` 连续、`id == evt_000001…`、`rng_seed == derive_seed(save_seed, turn_id, actor_id, method, seq)`；再用唯一投影 `apply_events(None, events)` 回放得到 `GameState`。
7. **一致性**：重放 `state_hash` 必须等于 `snapshot.state_hash`；`turn`/`version`（= distinct `turn_id` 数）/`last_event_seq`/`event_count`/`save_seed`/快照版本逐一比对；回合号从 1 连续递增且同回合事件相邻。
8. **幂等**：`Idempotency-Key`（8..128）→ 存储键 `save:import:<key>`；同键同包回放首次 201 响应，同键异包 409 `CONFLICT`；非法包不写幂等表（重复提交得到同一结构化错误）。

## 4. 写入路径（唯一写入者，事件不旁路）

校验通过后：`GameEngine.create_game(save_seed=包内种子, scenario="custom", initial_state=首事件.result.initial_state)` 重建新 `game_created` → 按回合分组把后续事件转回**草稿**（去掉 `id/seq/rng_seed/ts`）→ 逐回合 `GameEngine.submit(expected_version)`。

- `seq`/`rng_seed` 由同一 `save_seed` 重新派生，与导出端逐值一致；只有 `ts` 变为导入时刻（`ts` 不参与状态哈希）。
- 版本链：`version = 1 + 成功回合数`；快照节奏由引擎阈值决定，与导出端一致（复制用同一提交序列）。
- 写完后 `repo.load` 的 `state_hash` 必等于包内声明（校验阶段已数学等价），`/view`/`/replay` 直接可用，可继续 `POST /turns`。
- 不修改源存档（只读导出），不覆盖任何现有 `game_id`（导入总是分配新 ID）。

## 5. 安全边界

- 导出端只读事件表/当前状态/最新快照；不读 `Settings`、`.env`、LLM 凭据、进程日志，因此包内无密钥（测试断言 `api_key/apikey/authorization/idempotency/sk-/secret` 均不出现）。
- 导入端：体积/条数上限、字段白名单、事件模型校验、回放一致性，全部通过才写库；失败零写入（不新增存档、不写事件、不写幂等表）。
- HTTP 请求体：导入路径 8MB+64KB，其余端点 1MB 不变；未知存档导出 404，错误信封沿用第 6 章 8 码映射。
- 不新增运行时依赖、不新增存储、不改 `/turns` 契约与旧 golden。

## 6. 已知限制与后续

1. **checksum 非签名**：攻击者若改事件后重算 checksum 且保持 `state_hash` 一致（仅改叙事展示文本），完整性校验无法发现——本地单机/文件交换场景接受；未来若做云分享需加签名/发布者身份。
2. **绑定元数据不迁移**：导入首事件由本机组合根重建，`game_created.params` 的 `card_id`/`scenario_id`/`world_book_id` 不保留；状态本身完整（自定义场景钩子从状态派生仍可用），但**世界书条目注入**不随包迁移（如需保留，后续在包内加可选 `bindings` 字段并做存在性校验）。
3. **写库阶段基础设施异常**（磁盘满/进程崩溃）无法回滚半成品，属运维范畴；校验失败路径严格零写入。
4. 网页 UI/CLI 入口未接（本批后端优先）：前端可直接 `GET .../export` 下载文件、`POST /saves/import` 上传后跳转 `/games/{new_id}`。
5. 动态场景（用户上传 `scenario=custom`）导入后按状态派生识别，无需场景库同 ID 存在；若后续需要「导入时绑定场景 ID」再扩包字段。

## 7. 复跑命令

```powershell
.venv\Scripts\python.exe -m pytest -q tests/integration/test_saves_export_import.py
.venv\Scripts\python.exe -m pytest -q tests/replay
.venv\Scripts\python.exe -m textgame.console --check   # 4 轮逐字不变
.venv\Scripts\python.exe -m textgame.demo --check      # 7 屏逐字不变
.venv\Scripts\python.exe -m ruff check src/textgame/services/save_service.py tests/integration/test_saves_export_import.py
.venv\Scripts\python.exe -m mypy src/textgame
```
