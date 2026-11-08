# 010 · 2026-11-09 · M3 多角色 · perception-slice

> 切片目标一句话：把 `QueryService.build_perception_slice` 扩展为「玩家 Character 与 NPC 都能取到自己的私有感知切片」，并落地结构性防泄露不变量 ｜ 验收命令：`uv run pytest tests/unit/test_perception.py` ｜ 里程碑 M3 ｜ 编号区间 010–014 ｜ 执行/验收：perception-dev（AI）/ lead（复核、集成、提交）

## 1. 目标与验收命令

- 目标（第 8 章 §8.2、第 5 章 §5.4/§5.5）：可见实体按 actor 当前房间裁剪；`local_events` 只含该 actor 自己的事件（最近 10 条）；`private_memory` 按 actor 隔离；`plan_hint` 只能由调用方注入；切片可 JSON 序列化且不含 `GameState`/仓储引用。
- 验收命令与结果：`uv run pytest tests/unit/test_perception.py -q` → **33 passed**；全量 `uv run pytest -q` → **960 passed**（M3 集成后基线）。

## 2. 改动与提交

- 新增 `src/textgame/domain/perception.py`：`LOCAL_EVENTS_LIMIT=10` + 纯函数 `filter_local_events`（domain 层零框架/IO）。
- 扩展 `src/textgame/services/query_service.py`：`build_perception_slice(state, actor_id, *, turn_id, save_seed, plan_hint=None, events=())`；支持 NPC actor（`room_id`/`memory_ref`）、排除 actor 自身引用；旧四参调用保持兼容。
- 新增 `tests/unit/test_perception.py`（33 用例：玩家/NPC 切片、跨房间/他人背包/quest/全局事件不泄露、私有记忆隔离、plan_hint、序列化白名单、events 过滤、失败分支）。
- 提交：`a5f2be3`（M3 多角色合并切片提交，下同）。

## 3. 验证结果

- 切片验收：33 passed；输出见 `.work` 之外的 CI/命令记录。
- 结构性防泄露：NPC 切片不含自身、不含他人背包；玩家切片不含其他房间实体与全局事件流水；感知切片 `model_dump(mode="json")` 可往返且字段集合固定。
- 质量门：ruff format/check、mypy strict、lint-imports（2 contracts）全绿；`domain` 仍零框架依赖。

## 4. 下一步与风险

- 下一步：011 actor-scope（工具可见性矩阵与越权拒绝）。
- 风险/口径：`local_events` 采用保守可见性（仅 actor 自己事件）——M3 调度器统一传 `repo.read_events(...)` 的盖章事件；若后续需要「同房间公共事件」可见性，再扩展过滤策略并同步文档。
- 接口偏离：`plan_hint` 为「只由调度器注入」，不来自其他 Agent 输出（第 8 章 §8.2 构造规则 4）。
