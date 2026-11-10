## 第 6 章 服务层 API：查询、背包、角色、判定、动作、制造、事件日志

### 本章检索关键词
- `python service layer Result pattern business logic`、`python-statemachine state machine quest`、`pytransitions transitions library maintenance`、`dry-python returns Result container`、`rustedpy result archived`、`dependency-injector cython extension wheels`、`fastapi dependency injection vs DI container`、`pypi returns requires-python 3.10`

### 实际打开确认的仓库列表
- `sqlalchemy/sqlalchemy`（https://github.com/sqlalchemy/sqlalchemy）— 打开确认：许可证 MIT、最近提交 2026-11、star 约 12.3k、Python 3.10 兼容需锁 2.0.x（PyPI 2.0.57 要求 ≥3.7，2.1.8 要求 ≥3.11）、确认要点（ORM/Unit of Work 适合做仓储接口实现；CAS 版本计数器能力已在第 11 章核对）。
- `fgmacedo/python-statemachine`（https://github.com/fgmacedo/python-statemachine）— 打开确认：许可证 MIT、最近提交 2026-10、star 约 1.3k、Python 3.10 兼容（PyPI 3.2.2 `requires-python=>=3.10`）、确认要点（README：`StateChart`/`State` 声明式状态机，`green.to(yellow) | ...` 组合转移，支持 sync/async）。
- `dry-python/returns`（https://github.com/dry-python/returns）— 打开确认：许可证 BSD-3-Clause、最近提交 2026-11、star 约 4.4k、Python 3.10 兼容需锁系列（PyPI 0.30.0 要求 `>=3.11`；0.26.0 要求 `>=3.10`）、确认要点（README：`Result`/`IO` 容器与 HKT 模拟，`mypy` 插件）。
- `rustedpy/result`（https://github.com/rustedpy/result）— 打开确认：许可证 MIT、最近提交 2026-06、star 约 1.7k、**仓库已归档（archived=True）**、Python 3.10 兼容（PyPI 0.17.0 要求 ≥3.8）、确认要点（Result/Option 风格容器；归档意味着不再修 bug）。
- `ets-labs/python-dependency-injector`（https://github.com/ets-labs/python-dependency-injector）— 打开确认：许可证 BSD（PyPI classifier 为 `License :: OSI Approved :: BSD License`）、最近提交 2026-11、star 约 4.9k、Python 3.10 兼容（PyPI 4.49.1 要求 ≥3.8）、确认要点（PyPI 文件列表为 `cp310-abi3-manylinux*`/`cp310-abi3-macosx*` 等平台相关编译 wheel，说明是编译扩展而非纯 Python）。
- `pytransitions/transitions`（https://github.com/pytransitions/transitions）— 打开确认：许可证 MIT、最近提交 2025-09（**近 6 个月无提交**）、star 约 6.6k、Python 3.10 兼容（setup 中带 `Programming Language :: Python :: 3.10` classifier；PyPI 0.9.3 未声明 `requires-python`）、确认要点（README/API 的 `Machine` 事件转移模型）。

### 6.1 服务层定位与三条硬边界

服务层是“可信 API 层”：智能体只能经第 7 章的工具白名单间接调用它，它负责状态读取、前置校验、确定性计算与事件生成。三条边界必须在代码评审与架构测试里守住：

1. **不提交状态**：服务层不 `session.commit()`、不调 `EventService.append()`/`EventLog.append()`、不修改 `GameState`；写方法只返回 `Result(ok=True, events=[草稿 Event])`。提交只发生在唯一写入者（Single Writer）`GameEngine.submit(expected_version, events, idempotency_key)`（第 11、14 章）。
2. **不返回领域对象引用**：对外返回 `dict`（塞进 `Result.data`）或 `model_copy(deep=True)` 的只读副本；绝不把 `Character` 实例直接交给 Agent，避免“Agent 拿着对象改字段”绕过校验。
3. **不做叙事**：服务层只产数值与事实（`hit=True, damage=5`），80～150 字屏文本由叙事 Agent/模板渲染（第 8 章）；服务层写方法也不调 LLM。

写方法的调用方是 `Resolver`（裁决通过后取草稿事件）与 `GameEngine`（系统事件，如世界时间推进）；工具层只直接执行 `READ_ONLY` 工具，`DESTRUCTIVE` 工具在工具层被转成提案（Proposal）后进 `ProposalQueue`（第 7 章）。

### 6.2 统一约定：ToolContext、幂等键与错误码映射

```python
from typing import Literal

# 复用第 5 章的 pydantic import（BaseModel / ConfigDict / Field）；各服务模块共享该上下文
class ToolContext(BaseModel):
    """一次调用的上下文：由编排层构造、工具层透传、服务层消费。不含 GameState 引用。"""
    model_config = ConfigDict(frozen=True, extra="forbid")

    actor_id: str = Field(min_length=1, max_length=64)
    actor_tags: frozenset[str] = Field(default_factory=frozenset)  # {"player"} / {"npc", "npc:blacksmith"}
    turn_id: int = Field(ge=1)
    save_seed: str = Field(min_length=1, max_length=64)           # 第 10 章派生随机用
    idempotency_key: str = Field(min_length=8, max_length=128)    # 接入层透传或工具层派生
    caller: Literal["player", "npc_agent", "parser", "system", "resolver"]
```

- **幂等键（Idempotency Key）**：接入层收到 `Idempotency-Key` 后原样放进 `ToolContext`；同一键重复提交时 `GameEngine` 返回首次结果（命中幂等表），不重复写事件。工具层在缺省时派生子键：`sha256(f"{turn_id}:{actor_id}:{tool}:{canonical_json(args)}")`，同一参数可安全重试。
- **`actor_id` vs `actor_scope`**：`actor_scope` 是工具可见性（第 7 章，回答“这个 Agent 能不能看到并调用这个工具”）；服务层只做**数据归属校验**（回答“这个 actor 能不能动这条数据”）。两者分开后，新增一个 NPC 不需要改服务代码。
- **`ToolContext` 六字段固定（M4 复核，不扩字段）**：制造不新增 `seq`/`next_seq` 等上下文字段；`calculate_craft_result` 所需的序号由服务层取当前存档 `last_seq + 1` 注入 `CraftStateSlice.next_seq` 后派生种子（D-M4-4，第 13 章），并要求玩家 `craft_item` 事件是本次提交批次的首条事件（编排层 player→npc 顺序保证，M4 补断言测试）。
- **错误码口径（全队统一）**：工具/LLM 参数不符合 JSON Schema → `INVALID_INPUT`；LLM 原始文本无法解析或未过输出 Schema → `SCHEMA_VIOLATION`；`actor_scope` 越权 → `NOT_ALLOWED`；业务前置条件 → `NOT_FOUND`/`NOT_ALLOWED`/`OUT_OF_RANGE`/`CONFLICT`；模型调用失败 → `LLM_UNAVAILABLE`；其余 → `INTERNAL`。所有失败 `Result.events` 必须为空（第 5 章模型已强制），保证“失败零状态变更”。

### 6.3 七个模块的方法签名

以下签名只列关键方法；`ctx` 统一为 `ToolContext`，写方法统一带关键字参数 `idempotency_key: str`（可从 `ctx` 取，显式传入利于单测）。`StateReader` 是只读快照接口、`ActorDirectory` 是 actor→标签解析器，二者由第 14/8 章提供实现；服务层只依赖这两个接口，不直接 import 仓储。

模块与服务文件一一对应（第 16 章目录权威命名）：查询 `services/query_service.py`、背包 `inventory_service.py`、角色 `character_service.py`、判定 `rule_service.py`、动作 `action_service.py`、制造 `craft_service.py`、事件日志 `event_service.py`（包装 `domain/events.py` 的 `EventLog`）。

```python
# services/query_service.py —— 查询：只读，零事件
class QueryService:
    def __init__(self, state_reader: StateReader, actors: ActorDirectory) -> None: ...

    def get_room_view(self, ctx: ToolContext, room_id: RoomId | None = None) -> Result:
        """返回当前房间的描述、出口、可见实体；data={"room":..., "exits":..., "entities":[...]}"""

    def get_character_view(self, ctx: ToolContext, character_id: CharacterId) -> Result:
        """只能看自己或同房间实体；不可见一律 NOT_FOUND（不泄露存在性）"""

    def get_inventory(self, ctx: ToolContext, owner: CharacterId | None = None) -> Result: ...

    def search_entities(self, ctx: ToolContext, keyword: str,
                        kinds: frozenset[str] = frozenset({"item", "npc"})) -> Result: ...

    def build_perception_slice(self, state: GameState, actor_id: str, *,
                               turn_id: int, save_seed: str,
                               plan_hint: str | None = None,
                               events: Sequence[Event] = ()) -> PerceptionSlice:
        """编排层专用（M3 实际实现口径，第 8/9/21 章按此引用）：组装私有感知切片（Perception Slice）；
        `state` 为已载入的只读快照；`events` 只用于筛选 actor 自己的最近事件（`local_events` 按 seq 升序）；
        `plan_hint` 只由调度器显式注入；`save_seed` 与 `ToolContext.save_seed` / 第 10 章 `derive_seed(save_seed: str, ...)` 一致；
        只可能是 INTERNAL 失败（快照损坏/actor 不存在）"""
```

```python
# services/inventory_service.py —— 背包：所有写方法返回草稿事件
class InventoryService:
    def add_item(self, ctx: ToolContext, owner: CharacterId, item_id: ItemId,
                 qty: int = 1, *, idempotency_key: str) -> Result:
        """事件：item_added；错误码：NOT_FOUND(item)、NOT_ALLOWED(非本人)、OUT_OF_RANGE(超容量/数量)"""

    def remove_item(self, ctx: ToolContext, owner: CharacterId, item_id: ItemId,
                    qty: int = 1, *, idempotency_key: str) -> Result:
        """事件：item_removed；错误码：NOT_FOUND、INVALID_INPUT(qty<1)、OUT_OF_RANGE(数量不足)"""

    def transfer_item(self, ctx: ToolContext, src: CharacterId, dst: CharacterId,
                      item_id: ItemId, qty: int = 1, *, idempotency_key: str) -> Result:
        """事件：item_transferred；错误码：NOT_FOUND、NOT_ALLOWED(非 src 本人且非 system)"""

    def equip_item(self, ctx: ToolContext, owner: CharacterId, item_id: ItemId,
                   slot: str, *, idempotency_key: str) -> Result:
        """事件：item_equipped；错误码：NOT_FOUND、INVALID_INPUT(槽位非法)、NOT_ALLOWED(不满足条件)"""
```

```python
# services/character_service.py —— 角色：数值变更的唯一入口
class CharacterService:
    def create_character(self, ctx: ToolContext, name: str, attributes: dict[str, int],
                         location: RoomId, *, idempotency_key: str) -> Result:
        """事件：character_created；错误码：INVALID_INPUT、NOT_FOUND(房间不存在)"""

    def apply_damage(self, ctx: ToolContext, target: CharacterId, amount: int,
                     *, source: str, idempotency_key: str) -> Result:
        """事件：character_damaged（result 含 hp_after）；错误码：NOT_FOUND、INVALID_INPUT(amount<=0)、
        OUT_OF_RANGE(超过 max_hp 上限)；死亡只落 hp=0 事件，死亡处理由 rules 产生后续事件"""

    def heal(self, ctx: ToolContext, target: CharacterId, amount: int,
             *, idempotency_key: str) -> Result:
        """事件：character_healed；错误码同上"""

    def apply_status(self, ctx: ToolContext, target: CharacterId, status: Status,
                     *, idempotency_key: str) -> Result:
        """事件：status_applied；错误码：NOT_FOUND、NOT_ALLOWED(系统状态只能由 system 施加)"""

    def update_attributes(self, ctx: ToolContext, target: CharacterId,
                          delta: dict[str, int], *, idempotency_key: str) -> Result:
        """事件：attributes_changed；错误码：INVALID_INPUT(未知属性键)、OUT_OF_RANGE(越界)"""
```

```python
# services/rule_service.py —— 判定：服务层门面，纯确定性、零事件；计算在 domain/rules/ 的 RuleEngine
class RuleService:
    def __init__(self, engine: RuleEngine, random: RandomSource) -> None: ...

    def check(self, ctx: ToolContext, spec: CheckSpec) -> Result:
        """纯计算（委托 RuleEngine）：data={"success": bool, "dice": [...], "total": int, "dc": int, "rng_seed": int}
        零事件；同一 rng_seed 必得同一结果；错误码：NOT_FOUND(规则不存在)、OUT_OF_RANGE(dc 越界)"""

    def calculate_damage(self, ctx: ToolContext, attacker: CharacterId, defender: CharacterId,
                         weapon_id: ItemId | None, *, dice: list[DiceRoll]) -> Result:
        """纯计算：data={"damage": int, "dice": [...], "rng_seed": int}；零事件"""

    def evaluate_preconditions(self, ctx: ToolContext, tool: str,
                               args: dict, snapshot: GameState) -> Result:
        """裁决前置条件：data={"allowed": bool, "needs_judge": bool, "reason": str}
        错误码：NOT_ALLOWED(明确不允许)、NOT_FOUND、OUT_OF_RANGE；纯函数，不写库"""
```

判定模块的关键约束：它是**确定性计算**（骰子（Dice Roll）由 `RandomSource` 在 `domain/rules/` 的 `RuleEngine` 内掷出，`rng_seed` 随结果返回）；`RuleService` 只做参数解析与 `Result` 包装，不重算数值。事件里不出现判定事件，判定结果与 `rng_seed` 被写进后续写事件的 `result` 字段（例如 `attack_target` 的 `result.dice`），这样回放时只需事件就能复算，不需要额外的判定日志。

```python
# services/action_service.py —— 动作：所有 DESTRUCTIVE 工具最终落到这里
class ActionService:
    def move_to(self, ctx: ToolContext, direction: Direction, *,
                force: bool = False, idempotency_key: str) -> Result:
        """事件：character_moved；错误码：NOT_FOUND(无此出口)、NOT_ALLOWED(被阻挡且未 force)、
        OUT_OF_RANGE(目标房间已满)；result={"from":..., "to":...}"""

    def take_item(self, ctx: ToolContext, item_id: ItemId, *,
                  idempotency_key: str) -> Result:
        """事件：item_taken；错误码：NOT_FOUND(房间无此物)、OUT_OF_RANGE(背包满)"""

    def drop_item(self, ctx: ToolContext, item_id: ItemId, qty: int = 1, *,
                  idempotency_key: str) -> Result:
        """事件：item_dropped；错误码：NOT_FOUND、INVALID_INPUT"""

    def attack_target(self, ctx: ToolContext, target_id: CharacterId,
                      weapon_id: ItemId | None = None, *, idempotency_key: str) -> Result:
        """先调 RuleService.check/calculate_damage，再产一个或多个草稿事件：
        attack_resolved（含 dice/rng_seed）、character_damaged；错误码：NOT_FOUND(目标不在此房间)、
        CONFLICT(目标已死亡)、NOT_ALLOWED(武器不匹配/回合已结束)"""

    def use_item(self, ctx: ToolContext, item_id: ItemId,
                 target_id: CharacterId | None = None, *, idempotency_key: str) -> Result:
        """事件：item_used（+ 效果事件）；错误码：NOT_FOUND、NOT_ALLOWED(不可使用)、OUT_OF_RANGE"""

    def talk_to(self, ctx: ToolContext, npc_id: NpcId, *,
                idempotency_key: str) -> Result:
        """事件：dialogue_exchanged；错误码：NOT_FOUND(不在同房间)；叙事由第 8 章生成后随事件提交"""
```

```python
# services/craft_service.py —— 制造：服务层只做“解析 ctx → 调领域纯函数 → 组草稿事件”，不提交状态
class CraftService:
    def list_recipes(self, ctx: ToolContext, *,
                     tags: frozenset[str] = frozenset()) -> Result:
        """只读：data={"recipes":[...]}；错误码：无（空列表合法）"""

    def craft(self, ctx: ToolContext, req: CraftRequest, *, idempotency_key: str) -> Result:
        """与第 13 章的职责边界一致（13.2/13.5）：
        1) 解析 ctx（actor_id / save_seed / idempotency_key）与背包上下文，构造 `CraftStateSlice`
           （`next_seq` = 当前存档 `last_seq + 1`，由服务层注入，D-M4-4）；
        2) 常规路径直接调用 domain/crafting.py 的纯函数（签名固定，第 13 章）：
           `calculate_craft_result(state: CraftStateSlice, recipe: Recipe,
                                   actor_id: CharacterId, rng_seed: int) -> CraftResult`；
           `rng_seed = RandomSource.derive_seed(save_seed, turn_id, actor_id, "craft_item", next_seq)`，
           成功率、消耗、质量、产出全部由该纯函数计算，服务层不重算、不改写数值；
        3) 生成路径先把第 8 章 `CraftOutput` 规范化为 `CraftDraft`，再过 JSON Schema
           + 状态一致性检查（第 13 章 §13.4），失败即拒绝；M4 制造（含自由路径）作为普通 DESTRUCTIVE
           工具复用 M3 编排链路：玩家 Proposal 经 ProposalQueue → 确定性 Resolver（其内调 ToolGate → CraftService
           取草稿事件）后交 `GameEngine.submit`（D-M4-13；m4 无 NPC Agent → 无跨角色冲突、无 Judge 覆盖）；D-M4-3
           仅指 `CraftProposal` 不入队（仅 M5 契约保留），M5 接线前须迁移到 pre-queue（第 11 章 Resolver
           纯函数约束）；
        4) 无论哪条路径，都只返回 Result(ok=True, data={"craft": CraftResult}, events=[草稿 Event])（`CraftResult` 为实例化前模板级结果；实例 ID/完整定义见事件 `result.produced_items`，N-01）：
           **服务层不提交状态、不调 GameEngine**（提交只由 GameEngine 做）。
        事件（event_type 以第 13、14 章为准）：配方路径 `CRAFT_DETERMINISTIC`、自由路径 `CRAFT_GENERATED`
        （`params` 分别记 `mode/recipe_id/recipe_version` 与 `free_description_sha256/proposal_hash/prompt_version`；
        `result.craft` 为 CraftResult、`result.produced_items` 为 Item 定义列表，D-M4-5）；
        `CRAFT_REJECTED` M4 不产生（仅预留 identity reducer）：前置校验失败 → `Result(ok=False)` 且零事件，
        掷骰失败 → 一条 `CRAFT_DETERMINISTIC`（success=false、consumed/produced/byproducts 为空）。
        错误码：NOT_FOUND(无配方)、INVALID_INPUT(数量/参数非法)、OUT_OF_RANGE(数值越界)、
        NOT_ALLOWED(工具/技能/越权/草案违反世界状态)、CONFLICT(并发 CAS 失败)、
        SCHEMA_VIOLATION(LLM 原始草案未过 Schema，经第 12 章修复重试后仍失败)"""
```

制造模块的边界重申：`calculate_craft_result` 是 `domain/crafting.py` 的纯函数、唯一数值权威（不依赖数据库、不调 LLM），`CraftService.craft()` 只解析 `ctx`、组装 `CraftStateSlice` 与 `recipe`、把 `CraftResult` 转成草稿事件；服务层既不重新实现数值，也不提交状态——与第 13 章“`CraftService` 只是服务入口包装、提交只能由 `GameEngine` 做”是同一口径；M4 制造复用 M3 编排链路（Proposal → `ProposalQueue` → 确定性 `Resolver` → `ToolGate` → 服务层 → `GameEngine`，D-M4-13），D-M4-3 的「不接 Resolver」仅指自由制造草案本身不入队/不经裁决（`CraftProposal` 仅 M5 契约保留）。

```python
# services/event_service.py —— 事件日志服务：包装 domain/events.py 的 EventLog（append 只允许 GameEngine 调用）
class EventService:
    def __init__(self, log: EventLog) -> None: ...

    def stamp(self, events: list[Event], *, turn_id: int, save_seed: str,
              start_seq: int) -> list[Event]:
        """委托 EventLog.stamp：start_seq = 当前存档最后 seq + 1；id=f"evt_{seq:06d}"；
        seq 全局单调递增、跨回合连续（同回合按 seq 升序）；
        rng_seed=RandomSource.derive_seed(save_seed, turn_id, actor_id, method, seq)（已含 63 位掩码）、ts=UTC now"""

    def append(self, ctx: ToolContext, events: list[Event],
               *, expected_version: int) -> Result:
        """仅 GameEngine 调用；校验 seq 全局单调递增与 expected_version；
        错误码：NOT_ALLOWED(非 GameEngine 调用)、CONFLICT(seq/版本冲突)、SCHEMA_VIOLATION(未盖章)"""

    def query(self, ctx: ToolContext, *, since_seq: int = 0,
              turn_id: int | None = None, actor_id: str | None = None,
              limit: int = 100) -> Result:
        """只读：审计与调试；错误码：INVALID_INPUT(limit 越界)"""

    def replay(self, upto_seq: int | None = None) -> Result:
        """只读重放：data={"state": <GameState JSON>, "last_seq": n}；
        错误码：SCHEMA_VIOLATION(upcaster 失败)、INTERNAL"""

    def snapshot(self, ctx: ToolContext) -> Result:
        """落快照（由第 14 章实现持久化）；错误码：CONFLICT、INTERNAL"""
```

### 6.4 错误码与事件对照表

| 模块 | 方法 | 产生事件（`method`） | 事件 `rng_seed` | 主要错误码 | 幂等键来源 | 归属校验 |
| --- | --- | --- | --- | --- | --- | --- |
| 查询 | `get_room_view`/`get_character_view`/`get_inventory`/`search_entities` | 无（只读） | — | `NOT_FOUND`/`INVALID_INPUT` | 无需（天然幂等） | 可见范围内才返回，越权= `NOT_FOUND` |
| 背包 | `add_item`/`remove_item`/`transfer_item`/`equip_item` | `item_added`/`item_removed`/`item_transferred`/`item_equipped` | 必需 | `NOT_FOUND`/`NOT_ALLOWED`/`OUT_OF_RANGE`/`CONFLICT` | 接入层 `Idempotency-Key`，缺省由工具层派生 | `ctx.actor_id == owner`，否则 `NOT_ALLOWED` |
| 角色 | `apply_damage`/`heal`/`apply_status`/`update_attributes` | `character_damaged`/`character_healed`/`status_applied`/`attributes_changed` | 必需 | `NOT_FOUND`/`INVALID_INPUT`/`OUT_OF_RANGE`/`NOT_ALLOWED` | 同上 | 只能作用于自己或同房间目标；系统状态限 `caller=system` |
| 判定 | `RuleService.check`/`calculate_damage`/`evaluate_preconditions` | 无（结果嵌入后续动作事件） | 结果内含 `rng_seed` | `NOT_FOUND`/`OUT_OF_RANGE`/`NOT_ALLOWED` | 无需（纯函数、同种子同结果） | 仅服务层/Resolver 可调，不对 Agent 暴露工具 |
| 动作 | `move_to`/`take_item`/`drop_item`/`attack_target`/`use_item`/`talk_to` | `character_moved`/`item_taken`/`item_dropped`/`attack_resolved`+`character_damaged`/`item_used`/`dialogue_exchanged` | 必需 | `NOT_FOUND`/`NOT_ALLOWED`/`OUT_OF_RANGE`/`CONFLICT` | 同上 | `ctx.actor_id` 必须是行动角色本人（NPC 走提案通道） |
| 制造 | `craft`（内部调 `calculate_craft_result` 纯函数；`data={"craft": CraftResult}`） | `craft_item`（event_type=`CRAFT_DETERMINISTIC`/`CRAFT_GENERATED`；`CRAFT_REJECTED` 仅预留、M4 不产生，第 14 章） | 必需 | `NOT_FOUND`/`INVALID_INPUT`/`OUT_OF_RANGE`/`NOT_ALLOWED`/`CONFLICT`/`SCHEMA_VIOLATION` | 同上 | 只能为自己制造；M4 制造走既有提案/裁决链（D-M4-13），自由制造草案不入队（D-M4-3） |
| 事件日志 | `EventService.stamp`/`append`/`query`/`replay`/`snapshot`（包装 `EventLog`） | 不产生事件（它就是事件存储） | — | `NOT_ALLOWED`/`CONFLICT`/`SCHEMA_VIOLATION`/`INTERNAL` | `append` 用幂等键 + `expected_version` | `caller` 必须是 `system`（GameEngine） |

写事件统一要求：`method` = 工具名（如 `attack_target`），`params` = 工具参数 + `proposal_id`，`result` = 判定摘要与效果数值，`rng_seed` 必填，`actor_id` = 实际行为者。一次动作可以产多个事件（攻击=判定 + 伤害两个事件），它们必须在同一个 `Resolution.applied_events` 里被原子提交。并发提交冲突（`CONFLICT`）的重试由调用方负责，全队统一措辞为「最多 3 次尝试（首次 + 2 次重试）」（第 11 章）。

### 6.5 服务层与 GameEngine 的边界（伪代码）

```python
INVENTORY_CAPACITY = 64          # 背包容量上限，数值配置放 config/，不分散在服务里

# 错误示范：服务层自己提交
class BadInventoryService:
    def add_item(self, ctx, owner, item_id, qty=1, *, idempotency_key):
        self.repo.add_item(owner, item_id, qty)
        self.session.commit()                       # 禁止：服务层写库
        return Result.success({"qty": qty})

# 正确：服务层只产草稿事件，由 GameEngine 统一提交
class GoodInventoryService:
    def add_item(self, ctx, owner, item_id, qty=1, *, idempotency_key):
        if ctx.actor_id != owner and ctx.caller != "system":
            return Result.fail(ErrorCode.NOT_ALLOWED, "inventory owner mismatch")
        owner_char = self.state_reader.character(owner)
        if item_id not in self.state_reader.items():
            return Result.fail(ErrorCode.NOT_FOUND, f"unknown item {item_id}")
        if len(owner_char.inventory) >= INVENTORY_CAPACITY:
            return Result.fail(ErrorCode.OUT_OF_RANGE, "inventory full")
        draft = Event(turn_id=ctx.turn_id, actor_id=ctx.actor_id, method="add_item",
                      params={"owner": owner, "item_id": item_id, "qty": qty},
                      result={"qty_after": len(owner_char.inventory) + qty})
        return Result.success(data={"item_id": item_id, "qty": qty}, events=[draft])
```

架构测试（第 18 章）必须能自动抓住 `BadInventoryService`：`services/` 与 `domain/` 禁止 import `sqlalchemy`、禁止出现 `.commit()`、禁止给 `GameState` 字段赋值；`GameState` 是 `frozen` 的，真赋值会直接抛异常，这是第二道保险。

### 6.6 候选轮子与拼接判断

先按模块给拼接方式（第 7 章的工具层只是这些服务的白名单外壳）：

| 模块 | 候选仓库 | 拼接方式 | 判断依据 |
| --- | --- | --- | --- |
| 查询 | sqlalchemy/sqlalchemy（仓储）+ pydantic（DTO/切片） | pip 依赖 | 只读查询依赖 `StateReader` 接口；感知切片裁剪逻辑自建（约 80 行），无轮子代劳 |
| 背包 | sqlalchemy/sqlalchemy（事务）+ pydantic（容量/堆叠校验） | pip 依赖 | 容量、堆叠、装备槽规则属玩法，自建在 `domain/rules` |
| 角色 | pydantic（数值/状态模型）+ fgmacedo/python-statemachine（任务状态转移） | pip 依赖 | 状态效果用 Pydantic 校验；`QuestState` 用状态机表达合法转移 |
| 判定 | 检索后未找到（关键词：`python dice roller replay seed`、`python rpg rules engine deterministic`；看过 dice_roller 需 3.11+ 与 numpy、YARE 非图灵完备），方向为自建 | 自建 | 必须“纯函数 + 可注入种子 + 零 IO”，没有轮子能同时满足（第 10 章展开） |
| 动作 | 检索后未找到（关键词：`python mud action service inventory`；Evennia 要求 3.12 且自带状态权威），方向为自建 | 自建 | 业务语义强、要产草稿事件，拼接收益为负 |
| 制造 | 检索后未找到（关键词：`python crafting recipe engine library`），方向为自建 | 自建 | 常规路径是纯计算函数 `calculate_craft_result`，自由制造走 LLM 校验链（第 13 章） |
| 事件日志 | sqlalchemy/sqlalchemy（事件表/快照表）+ pyeventsourcing/eventsourcing（参考） | pip 依赖 / 参考设计 | 事务与 CAS 用 SQLAlchemy；upcaster/回放分层参考 eventsourcing，不引依赖 |

口径说明：上表“自建”表示不引入外部仓库——对应四种拼接方式中的“放弃”（放弃现成轮子），由本仓库实现；第 16 章目录与第 19 章依赖清单按“依赖 / 拷贝 / 适配 / 自建”标注落地方式，两套词表不混用。

跨模块候选轮子总表：

| 仓库 | 定位 | 可复用部分 | 许可证 | 活跃度 | 建议 |
| --- | --- | --- | --- | --- | --- |
| sqlalchemy/sqlalchemy | 仓储与事务实现 | 仓储接口实现、`session`/事务边界、版本计数器 | MIT | 2026-11 有提交，star 约 12.3k；2.0.57 要求 ≥3.7 | pip 依赖 |
| fgmacedo/python-statemachine | 任务/回合状态机 | `QuestState` 转移合法性、回合阶段机 | MIT | 2026-10 有提交，star 约 1.3k；3.2.2 要求 ≥3.10 | pip 依赖 |
| dry-python/returns | 函数式 Result/IO 容器 | 仅借鉴“成功/失败同构返回”的组合写法 | BSD-3-Clause | 2026-11 有提交，star 约 4.4k；0.26.0 要求 ≥3.10、0.30.0 要求 ≥3.11 | 参考设计 |
| rustedpy/result | Result/Option 容器 | 无（仓库已归档，不再维护） | MIT | 2026-06 最后提交，star 约 1.7k，archived | 放弃 |
| ets-labs/python-dependency-injector | DI 容器 | 无（能力与 FastAPI `Depends` 重叠，且引入编译扩展） | BSD（PyPI classifier） | 2026-11 有提交，star 约 4.9k；4.49.1 要求 ≥3.8，仅提供平台编译 wheel | 放弃 |
| pytransitions/transitions | 状态机 | 事件转移模型可借鉴 | MIT | 最后提交 2025-09（近 6 个月无提交），star 约 6.6k | 放弃 |

**复用判断与拼接边界（一）：结果类型用项目自己的 `Result`，不用 `returns`/`result`**。`returns` 的 `Result` 是带 HKT 的函数式容器，配 `mypy` 插件才舒服，而本项目 `Result` 已定稿为 Pydantic 模型（要进事件日志、要 `model_dump(mode="json")`），二者混用只会制造两套错误处理风格；`returns` 更关键的问题是 0.27 之后要求 Python ≥3.11（0.26.0 才 ≥3.10），锁旧版本等于给自己埋升级债。`rustedpy/result` 直接排除：仓库已归档，MIT 也无法弥补“不再修 bug”。

**复用判断与拼接边界（二）：状态机只用于“有限、可枚举”的状态**。`QuestState`（未开始/进行中/已完成/失败）与回合阶段（解析→判定→叙事→提交）适合 `python-statemachine` 的声明式转移：非法转移在单测里就能抓到，且它只做状态合法性，不碰金额/伤害这类数值计算。边界：状态机管“能不能转移”，不理解业务后果；伤害、掉落、成功率的数值仍在 `RuleEngine`/`domain/crafting.py`。`transitions` 库 API 更老、近 6 个月无提交，故不用。

**复用判断与拼接边界（三）：依赖注入用 FastAPI 原生能力，不引入 DI 容器**。`api/` 用 `Depends` 注入 `TurnOrchestrator`，`main.py` 里手工组装服务对象（显式构造函数注入，可读、可单测）；`dependency-injector` 会带来平台相关编译 wheel（PyPI 文件列表为 `cp310-abi3-*` 的多平台二进制），CI 与部署环境多一层构建风险，而它解决的问题（延迟绑定）本项目用不着。若未来服务数量膨胀到 30+，再评估引入。

### 6.7 前端调用的新增 API（v0.2：人物卡库、开局与回放）

v0.2 前端（Vue 3 SPA，第 23 章）只经 `/api/v1` 与后端交互：**前端只做展示与输入，不持有权威状态、不直接改 `GameState`、不直接调 `GameEngine`**；服务端为准。人物卡库是独立于游戏世界状态的资料库（**不进事件溯源**），其写入由 `CharacterCardService` 校验后落 `character_cards` 表；只有创建存档（`POST /games`）才由唯一写入者（Single Writer）`GameEngine` 提交 `game_created` 系统事件。

> **M6 后端前置（2026-11-10 定案并部分交付，D-M6-1..6，`docs/decisions/2026-11-10-m6-backend-contract.md`）**：本节 11 个 v0.2 新端点中，`GET /games/{id}/view`（D-M6-4，前批）、`GET /games/{id}/replay`（D-M6-3，M6B-1）与静态托管/SPA fallback（D-M6-5①，M6B-2）**已交付**；`character-cards` 8 端点与 `POST /games` 的 `card_id` 语义**尚未实现**（现状 7 条 API 路径 + 静态托管）。它们登记为 **M6 出口附加项（不新增 025–028 编号）**，M6 出口验收必须包含。`POST /games` 取加法兼容（body `{card_id?, seed?, scenario?}`，D-M6-2）；`view`/`replay` 只读，`view` 供 StatusPanel/终端入口，`replay` 供 027 回放 UI（D-M6-3/4）。

**响应约定（v0.2 定稿；第 21、23 章按本节对齐）**：服务层方法仍返回 `Result`（6.1–6.5 的内部约定不变），但接入层对外**不使用 `ok`/`data` 信封**。

- **成功响应**：HTTP 200/201（`DELETE` 用 204 无 body）+ **直接返回业务 payload**——`GET /character-cards/schema` → JSON Schema 对象本身；`GET /character-cards` → `{cards, total}`；`GET /character-cards/{card_id}` → `{card}`；`POST /character-cards/import` → 201 `{card_id, card, warnings}`；`POST /character-cards/validate` → 200 `{valid, errors}`；`POST /character-cards/generate` → 200 `{draft, validation, generated_by}`；`POST /character-cards`（保存）→ 201 `{card_id, card}`；`DELETE /character-cards/{card_id}` → 204 无 body；`POST /games` → 201 `{game_id, turn, version, snapshot_version, save_seed, scenario?}`（body 可选 `scenario`：`m2`/`m3`/`m4`，缺省 `m2`，D-M4-11；D-M6-2 增 `card_id` 加法语义，实现现状另含 `save_seed`）；`GET /games/{game_id}` → 200 `{game_id, turn, version, snapshot_version, status, last_text}`；`GET /games/{game_id}/view` → 200 `{game_id, turn, version, snapshot_version, status, hp, max_hp, inventory, room}`（只读状态，D-M6-4；`inventory[]` 项为 `{item_id, name, qty}`，`qty` 为同模板实例聚合计数的加法字段，D-M6-7）；`GET /games/{game_id}/replay` → 200 `{events, from_seq, to_seq}`（`events[]` 按全局 `seq` 升序，项含 `{id, seq, turn_id, actor_id, method, event_type, params, result, rng_seed, ts}`，`result.narrative` 原样保留；`from_seq` 缺省 1、`limit` 缺省 200/上限 500，D-M6-3）。
- **失败响应（所有端点统一）**：对应 4xx/5xx + `{"ok": false, "error_code": <ErrorCode>, "message": "...", "violations": [...]?}`；HTTP 映射：`INVALID_INPUT`→422（文件过大→413）、`SCHEMA_VIOLATION`→422（带 `violations`）、`NOT_FOUND`→404、`NOT_ALLOWED`→403、`CONFLICT`→409、`LLM_UNAVAILABLE`→503、`INTERNAL`→500；`OUT_OF_RANGE`：数值超范围→422，配额/限流→429（带 `Retry-After`）。
- **既有 `POST /api/v1/turns` 例外**：保持第 9 章 `TurnResult` 不变，body 增补 `game_id`/`input`/`expected_version`；**`TurnResult` 增 `version`**（本次提交后的新 `version`，即下一次提交的 `expected_version`，第 9 章同步）。失败回合另有 `effects`/`suggested_actions` 加法字段：`effects=[{hint}]`（固定中文提示）+ 建议动作列表，只读 `error_code` 映射、不改 `text`/事件/`version`（D-M6-7、OSS-2）。

**对局版本链（CAS 乐观锁的唯一版本来源）**：`version` = 持久化状态行的版本列 `version`（文档概念称 `row_version`），所有响应里返回的 `version` 统一表示**下一次提交的 `expected_version`**。`POST /games` 返回初始 `version`；每次 `POST /turns` 成功后以 `TurnResult.version` 刷新；前端提交 `POST /turns` 时必须带 `expected_version`（来自 `POST /games` 或最近一次 `TurnResult.version`）。`GET /games/{game_id}` 用于刷新/重连恢复（`{game_id, turn, version, snapshot_version, status, last_text}`，`status ∈ {"active","ended"}`，`last_text` 为最近一屏 80～150 字文本、尚未提交时为 `null`），不产生状态变更；版本冲突（`CONFLICT`）按新提交处理：前端重新 `GET /games/{game_id}` 取最新 `version` 后重提，可生成新 `Idempotency-Key`。

端点总表（前缀 `/api/v1`；本组写操作 `POST`/`DELETE` 必带 `Idempotency-Key` 请求头，GET 不需要）：

| 方法 | 路径 | 服务方法 | 成功状态 | 主要错误码 |
| --- | --- | --- | --- | --- |
| GET | `/character-cards/schema` | `CharacterCardService.get_schema` | 200 | `INTERNAL` |
| GET | `/character-cards` | `list_cards`（`?query=&limit=&offset=`） | 200 | `INVALID_INPUT` |
| GET | `/character-cards/{card_id}` | `get_card` | 200 | `NOT_FOUND`/`INVALID_INPUT` |
| POST | `/character-cards/import` | `import_card`（multipart，≤64KB） | 201 | `INVALID_INPUT`/`SCHEMA_VIOLATION`/`NOT_ALLOWED`/`NOT_FOUND`/`CONFLICT` |
| POST | `/character-cards/validate` | `validate_card`（无副作用） | 200 | `INVALID_INPUT` |
| POST | `/character-cards/generate` | `generate_card`（返回未保存草稿） | 200 | `LLM_UNAVAILABLE`/`SCHEMA_VIOLATION`/`NOT_ALLOWED`/`INVALID_INPUT` |
| POST | `/character-cards` | `create_card`（保存，**方案 A**） | 201 | `SCHEMA_VIOLATION`/`NOT_ALLOWED`/`NOT_FOUND`/`CONFLICT`/`INVALID_INPUT` |
| DELETE | `/character-cards/{card_id}` | `delete_card`（软删除） | 204 | `NOT_FOUND`/`CONFLICT` |
| POST | `/games` | `GameEngine.create_game`（返回初始 `version`；body 可带 `scenario`） | 201 | `NOT_FOUND`（card）/`SCHEMA_VIOLATION`/`NOT_ALLOWED`/`CONFLICT` |
| GET | `/games/{game_id}` | `QueryService.get_game_view`（v0.2 补充，刷新/重连恢复） | 200 | `NOT_FOUND`/`INVALID_INPUT` |
| GET | `/games/{game_id}/view` | `QueryService.get_player_view`（**2026-11-10 新增**：只读 HP/背包/房间状态；`StatusPanel` 与终端入口唯一数据源，D-M6-4；inventory 项含 `qty`，D-M6-7） | 200 | `NOT_FOUND`/`INVALID_INPUT` |
| GET | `/games/{game_id}/replay` | `QueryService.read_events` + 路由切片（**已交付 M6B-1**：`?from_seq=&limit=`；limit 默认 200、上限 500；`events[]` 按 seq 升序、`result.narrative` 原样，D-M6-3） | 200 | `NOT_FOUND`/`INVALID_INPUT` |
| POST | `/turns` | 既有回合接口（第 9 章，返回 `TurnResult`） | 200 | 见第 9 章 |

```python
# services/card_service.py（v0.2 新增，第 16 章目录补登；只操作人物卡库，不接触 GameState）
class CharacterCardService:
    def get_schema(self, ctx: ToolContext) -> Result: ...
    def list_cards(self, ctx: ToolContext, *, query: str | None = None,
                   limit: int = 20, offset: int = 0) -> Result: ...
    def get_card(self, ctx: ToolContext, card_id: CardId) -> Result: ...
    def import_card(self, ctx: ToolContext, *, filename: str, content: bytes,
                    idempotency_key: str) -> Result: ...
    def validate_card(self, ctx: ToolContext, payload: dict[str, Any]) -> Result: ...
    def generate_card(self, ctx: ToolContext, *, brief: str, schema_version: str = "1.0",
                      options: dict[str, Any] | None = None,
                      idempotency_key: str) -> Result: ...
    def create_card(self, ctx: ToolContext, card: CharacterCard, *,
                    idempotency_key: str) -> Result:
        """方案 A 保存接口：服务端重新校验并强制 card_id=card_<ULID>、source 按入口判定。"""
    def delete_card(self, ctx: ToolContext, card_id: CardId, *, idempotency_key: str) -> Result: ...


# 开局：唯一写入者的 v0.2 补充签名（提交/CAS 细节见第 11、14 章）
class GameEngine:
    def create_game(self, ctx: ToolContext, *, card_id: CardId, seed: int | None = None,
                    scenario: Literal["m2", "m3", "m4"] = "m2",
                    idempotency_key: str) -> Result:
        """`scenario` 可选：`m2`（单角色）/`m3`（多角色）/`m4`（M4 制造），缺省 `m2`；
        `m2`/`m3` 初始状态与既有 golden 字节不变（D-M4-11）。"""


# 回放：6.3 EventService 的 v0.2 补充方法（只读，按全局 seq 分页）
class EventService:
    def replay_range(self, ctx: ToolContext, *, game_id: GameId, from_seq: int = 0,
                     limit: int = 200) -> Result: ...


# 对局视图：6.3 QueryService 的 v0.2 补充方法（只读；刷新/重连恢复用）
class QueryService:
    def get_game_view(self, ctx: ToolContext, game_id: GameId) -> Result: ...
```

约束与幂等：
- **幂等**：本组 `POST`/`DELETE` 写操作必须带 `Idempotency-Key`（重试复用同一键，第 23 章）；**同键同参 → 返回首次的 HTTP 响应与 payload（含失败响应）**；**同键异参 → `CONFLICT`**；幂等表分开存储（人物卡库 / 第 11 章游戏）。
- **warnings 结构**：统一为 `[{code, message, field?}]`（`code` 为稳定短码，如 `DUPLICATE_ITEM_MERGED`；`field` 为 JSON 路径，可选）；`POST /character-cards/import` 响应与 `POST /character-cards/generate` 的 `validation.warnings` 同构，第 21/23 章引用。
- **文件与解析**：multipart 文件 ≤64KB；`Content-Length` 预检 + 流式读取上限双重校验；仅接受 `application/json`、UTF-8 严格解码、JSON 深度 ≤8、不收压缩包；超限/类型错 → `INVALID_INPUT`；重复键的 JSON 直接拒绝（第 15 章）。
- **`source` 由服务端判定**：

  | 入口 | 强制 `source` | 说明 |
  | --- | --- | --- |
  | `POST /character-cards/import` | `upload` | 客户端值被覆盖并记 warning |
  | `POST /character-cards`（body.source=`llm`） | `llm` | LLM 真生成草稿确认保存 |
  | `POST /character-cards`（body.source=`manual`） | `manual` | 手工创建 / 模板兜底草稿保存 |
  | `POST /character-cards/generate` | 不入库：真生成 `llm`、模板兜底 `manual`（`generated_by=null`） | 只返回草稿 |
  其他 source 值 → `INVALID_INPUT`；`POST /character-cards` 服务端重新执行 5.7 全部校验并强制 `card_id = card_<ULID>`（DTO 本身不含 id，响应以 `card_id` 返回），重复提交由幂等键兜底。
- **生成限制**：`brief` ≤1000 字符、单用户 5 次/分钟（超限 → 429 + `OUT_OF_RANGE`）；LLM 调用按第 12 章唯一签名构造：`LLMClient.complete_json(LLMRequest(task="character_card_generate", output_schema=CharacterCard, agent_id="character_card", model_tier=ModelTier.STRONG, max_repair=1, timeout_s=20, turn_id=ctx.turn_id, idempotency_key=ctx.idempotency_key, messages=[...]))`（模型分级/重试/限流以第 12 章为准）；失败 → 模板兜底（`generated_by=null`）；内容安全 → `NOT_ALLOWED`（第 15 章）；**限流主体**：本地单机无账号按 `IP+session` 计，部署多用户时再加鉴权（第 23 章）。
- **错误码**：只用既有 8 码；HTTP 映射与失败信封见上方响应约定。

请求/响应示例（按本节响应约定：成功直接返回业务 payload；失败返回 `{ok:false, error_code, message, violations?}`）：

（1）`GET /character-cards/schema` → 200：**直接返回 JSON Schema 对象本身**（顶层含 `$schema`/`$id`/`type`/`properties`/`required`/`additionalProperties`，即第 5 章 5.7 的 `CHARACTER_CARD_SCHEMA`，不再包一层；前端 Ajv2020 直接复用）。

（2）`GET /character-cards?query=灰烬&limit=20&offset=0` → 200：`{"cards": [{"card_id": "card_01J9Z3K4Q7...", "name": "灰烬旅人", "source": "upload", "generated_by": null, "updated_at": "2026-11-08T12:00:00Z"}], "total": 1}`

（3）`POST /character-cards/import`（multipart）：
```http
POST /api/v1/character-cards/import HTTP/1.1
Idempotency-Key: 6f1c2f0a-9d33-4c2e-9a2f-3b7c6a1d0e55
Content-Type: multipart/form-data; boundary=----tg

------tg
Content-Disposition: form-data; name="file"; filename="card.json"
Content-Type: application/json

{"schema_version":"1.0","name":"灰烬游侠","attributes":{"str":12,"dex":16,"con":10,"int":8,"wis":11,"cha":9,"max_hp":18},"hp":18,"starting_items":["item.rusty_sword","item_torch"],"background":"边境村庄的斥候，追踪灰烬平原的异动。","appearance":"披着褪色斗篷，左脸有旧疤。","source":"upload","generated_by":null}
------tg--
```
→ `201`：`{"card_id": "card_01J9Z3K4Q7...", "card": {...CharacterCard...}, "warnings": [{"code": "DUPLICATE_ITEM_MERGED", "message": "duplicate starting item merged: item.rusty_sword", "field": "starting_items"}]}`

（4）`POST /character-cards/validate` → 200：请求体为 card JSON（≤64KB），响应 `{"valid": false, "errors": [{"loc": ["hp"], "type": "value_error", "msg": "hp must be <= attributes.max_hp"}]}`；校验不通过仍返回 200（查询式校验），只有请求体非 JSON/超大才返回 422 `{"ok": false, "error_code": "INVALID_INPUT", "message": "body is not valid JSON", "violations": []}`。

（5）`POST /character-cards/generate` → 200：请求 `{"brief":"一个在灰烬平原流浪的盲眼剑客，性格寡言","schema_version":"1.0","options":{"tone":"grim"}}`；响应 `{"draft": {"schema_version":"1.0","name":"盲眼剑客","attributes":{"str":13,"dex":14,"con":11,"int":9,"wis":15,"cha":8,"max_hp":17},"hp":17,"starting_items":["item.oak_cane"],"background":"……","appearance":null,"source":"llm","generated_by":"deepseek-chat"},"validation": {"valid": true, "errors": []}, "generated_by": "deepseek-chat"}`。**未保存、未开局**；保存走下一条。

（6）`POST /character-cards`（方案 A 定稿：body 为已校验的 `CharacterCard`，前端“确认保存”）→ 201：请求 `{"schema_version":"1.0","name":"盲眼剑客","attributes":{"str":13,"dex":14,"con":11,"int":9,"wis":15,"cha":8,"max_hp":17},"hp":17,"starting_items":["item.oak_cane"],"background":"……","appearance":null,"source":"llm","generated_by":"deepseek-chat"}`；响应 `{"card_id": "card_01J9Z3K4Q7...", "card": {...}}`。服务端强制 `card_id` 与 `source`（见上表）；`source` 与入口不符或 Schema 不符 → 422 `{"ok": false, "error_code": "SCHEMA_VIOLATION", "message": "...", "violations": [{"loc": ["source"], "type": "value_error"}]}`。

（7）`DELETE /character-cards/{card_id}` → **204 无 body**（软删除）。

（8）`POST /games` → 201：请求 `{"card_id": "card_01J9Z3K4Q7...", "scenario": "m4"}`（`scenario` 可选：`m2`/`m3`/`m4`，缺省 `m2`）；响应 `{"game_id": "game_01J9Z4A2B6...", "turn": 1, "version": 1, "snapshot_version": 1, "scenario": "m4", "save_seed": "save-42"}`（`save_seed` 供确定性回放/复现）。

（9）`GET /games/{game_id}` → 200：`{"game_id": "game_01J9Z4A2B6...", "turn": 7, "version": 12, "snapshot_version": 12, "status": "active", "last_text": "火把的光在湿漉漉的岩壁上摇晃。你蹲下身，泥地上有几枚模糊的脚印。"}`（`version` 即下一次 `POST /turns` 的 `expected_version`）。

（10）`GET /games/{game_id}/replay?from_seq=41&limit=2` → 200：`{"events": [{"seq": 42, "turn_id": 7, "event_type": "CHARACTER_MOVED", "method": "move_to", "params": {"direction": "north"}, "result": {"from": "room_a", "to": "room_b"}}, {"seq": 43, "turn_id": 7, "event_type": "ITEM_TAKEN", "method": "take_item", "params": {"item_id": "item_..."}, "result": {"inventory_size": 3}}], "from_seq": 41, "to_seq": 43}`

**前端只经 API 写入**：前端不得连接数据库、不得持有 `GameState`/`EventLog` 引用，也不得在本地“预提交”任何状态；游戏世界的写操作只能经 `POST /turns`/`POST /games` 进入 Resolver → `GameEngine`，人物卡库写操作只能经上述 API 由 `CharacterCardService` 校验后落库；读接口（列表/详情/回放/schema）只返回数据，供前端展示。

### 本章结论

1. 服务层七个模块定稿：`QueryService`、`InventoryService`、`CharacterService`、`RuleService`、`ActionService`、`CraftService`、`EventService`，分别落 `services/query_service.py`、`inventory_service.py`、`character_service.py`、`rule_service.py`、`action_service.py`、`craft_service.py`、`event_service.py`（第 16 章目录权威命名）；`RuleEngine`（`domain/rules/`）与 `EventLog`（`domain/events.py`）保留第 4 章核心组件名，服务层只做门面。
2. 硬边界：服务层不提交状态、不返回领域对象引用、不调 LLM；写方法返回 `Result(ok=True, events=[草稿 Event])`，失败 `events` 必空；提交只发生在 `GameEngine.submit(expected_version, events, idempotency_key)`。v0.2 前端只经 `/api/v1` 读写（见 6.7，成功直接返回业务 payload、失败返回 `{ok:false, error_code, message, violations?}`）：人物卡库写由 `CharacterCardService` 校验后落库、不进事件溯源（保存接口定稿方案 A：`POST /character-cards`，服务端强制 `card_id`/`source`）；开局由 `GameEngine.create_game` 提交 `game_created` 系统事件。
3. 每个写方法必须同时具备：`idempotency_key`、`ToolContext.actor_id` 归属校验、`ErrorCode` 集合、事件 `method/params/result/rng_seed`（对照表见 6.4，第 22 章进度文档模板直接引用该表）。
4. 确定性计算与智能体生成的边界：判定与常规制造是纯函数（同 `rng_seed` 同结果、零事件），自由制造草案必须过 JSON Schema + 状态一致性检查，失败返回 `SCHEMA_VIOLATION`/`NOT_ALLOWED` 且不写状态。
5. 依赖结论：SQLAlchemy 2.0.x + `python-statemachine` 2 个 pip 依赖；`returns` 仅参考设计（且必须锁 0.26.x 才满足 3.10）、`result` 因仓库归档放弃、`dependency-injector` 因编译扩展与能力重叠放弃、`transitions` 因 6 个月无提交放弃。
6. `ToolContext` 定稿（本章新增约定）：工具层构造、服务层消费，六字段 `actor_id`/`actor_tags`/`turn_id`/`save_seed`/`idempotency_key`/`caller`——已与第 21 章 §21.2.3 附录一致，第 8/9 章按同一字段集使用。
