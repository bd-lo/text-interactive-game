"""接入层请求/响应 DTO (第 6 章 6.7、契约 §5)。

成功响应直接返回业务 payload（不套 ``ok/data`` 信封）；请求体额外字段一律拒绝
（``extra="forbid"`` → 422 ``INVALID_INPUT``）。回合的 ``input`` 字段用别名实现，
避免在 Python 侧出现名为 ``input`` 的属性（ruff A003）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from textgame.domain.character_card import CardSource, CharacterCard


class TurnRequestBody(BaseModel):
    """``POST /api/v1/turns`` 请求体；JSON key 保持 ``input``。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    game_id: str = Field(min_length=1, max_length=64)
    text: str = Field(alias="input", min_length=1, max_length=500)
    expected_version: int = Field(ge=0)


class CreateGameBody(BaseModel):
    """``POST /api/v1/games`` 请求体；可省略，``seed`` 缺省用服务端默认种子。

    可选 ``scenario``（``m2`` 单场景 / ``m3`` 多角色场景 / ``m4`` 制造场景 /
    ``story`` 剧情场景「余烬哨站」，推荐首玩），缺省 ``m2`` 保持 M2 契约与回放不变
    （D-M3-3/D-M4-11/D-TEAM-D1）。D-M6-2 加法：``card_id`` 指定人物卡建局（与
    ``seed``/``scenario`` 互斥，同给 → 422 ``INVALID_INPUT``）；不提供时行为与
    M2–M4 完全一致。

    H4 加法（D-H4）：可选 ``world_book_id`` 绑定世界书（未知/已删除 404；可与
    ``seed``/``scenario``/``card_id`` 共存）；不提供时旧请求行为与事件参数逐字不变。

    场景库加法（设计契约 §3.4）：可选 ``scenario_id`` 按玩家自上传场景建局（与
    ``card_id``/``scenario`` 互斥，同给 422；未知/已软删 404）；不提供时行为与旧请求
    逐字不变。
    """

    model_config = ConfigDict(extra="forbid")

    card_id: str | None = Field(default=None, min_length=1, max_length=64)
    seed: int | None = Field(default=None, ge=0)
    scenario: Literal["m2", "m3", "m4", "story"] = "m2"
    world_book_id: str | None = Field(default=None, min_length=1, max_length=64)
    # 玩家自上传场景加法（设计契约 §3.4）：与 card_id/scenario 互斥，未知/已删 404；
    # 互斥/存在性校验由接入层（routes/games.py）与编排层完成，本模型只承载字段。
    scenario_id: str | None = Field(default=None, min_length=1, max_length=64)


class CreatedGameResponse(BaseModel):
    """``POST /api/v1/games`` 201 响应。"""

    model_config = ConfigDict(extra="forbid")

    game_id: str
    turn: int
    version: int
    snapshot_version: int
    save_seed: str


class GameViewResponse(BaseModel):
    """``GET /api/v1/games/{game_id}`` 200 响应（刷新/重连恢复用）。

    加法字段（D9）：``scenario`` 为场景名；``ending_text`` 为故事结局正文（旧场景/未结束
    为 ``None``）；``status=ended`` 由结局读时求值得出（不写状态、不加事件）。
    H4 加法：``world_book``/``world_book_id``/``lore`` 与 ``/view`` 同源（旧场景全为
    ``None``；刷新/续玩路径不再因未知响应键 500）。
    """

    model_config = ConfigDict(extra="forbid")

    game_id: str
    turn: int
    version: int
    snapshot_version: int
    status: Literal["active", "ended"]
    last_text: str | None = None
    scenario: str = "m2"
    ending_text: str | None = None
    # H4 加法（与 QueryService.get_game_view 返回一致；旧场景全为 None）：
    # 本局绑定的世界书摘要/ID 与本回合激活的 lore 条目，供刷新续玩时直接渲染。
    world_book: WorldBookRefResponse | None = None
    world_book_id: str | None = None
    lore: LoreResponse | None = None


class ItemRefResponse(BaseModel):
    """实体引用（房间可见物品 / 玩家背包物品共用）：``{item_id, name}``。"""

    model_config = ConfigDict(extra="forbid")

    item_id: str
    name: str


class NpcRefResponse(BaseModel):
    """房间可见 NPC 引用：``{npc_id, name}``。"""

    model_config = ConfigDict(extra="forbid")

    npc_id: str
    name: str


class RoomViewResponse(BaseModel):
    """玩家所在房间视图（只读；与 ``QueryService.get_room_view`` 同口径）。

    ``exit_names``（加法字段，D9）：方向 → 目标房间展示名，前端据此渲染「北 → 地窖」
    而不是内部房间 ID；``exits`` 保留方向 → 房间 ID 的既有契约不变。
    """

    model_config = ConfigDict(extra="forbid")

    room_id: str
    title: str
    description: str
    exits: dict[str, str]
    exit_names: dict[str, str] = Field(default_factory=dict)
    items: list[ItemRefResponse]
    npcs: list[NpcRefResponse]


class InventoryItemResponse(BaseModel):
    """``/view`` 玩家背包物品（M6 加法字段 ``qty``：同模板实例聚合计数）。

    与 ``ItemRefResponse``（房间可见物品）分开建模，避免改房间物品既有契约；``item_id``/
    ``name`` 取该模板首个实例，``qty`` 为 ``item.tags[0]``（无 tags 时用自身 id）去重后的
    数量，与 ``CraftService`` 的背包聚合口径一致（D-M4-1）。
    """

    model_config = ConfigDict(extra="forbid")

    item_id: str
    name: str
    qty: int = Field(default=1, ge=1)


class GoalObjectiveResponse(BaseModel):
    """剧情目标的一条子目标（``/view.goal.objectives[]``，D9 加法字段）。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    text: str
    done: bool


class GoalResponse(BaseModel):
    """剧情目标视图（仅 story 场景非空；旧场景 ``goal=None``）。"""

    model_config = ConfigDict(extra="forbid")

    title: str
    description: str
    objectives: list[GoalObjectiveResponse]


class GamePlayerViewResponse(BaseModel):
    """``GET /api/v1/games/{game_id}/view`` 200 响应（M6 状态面板/终端客户端契约）。

    只读：不产生事件、``version`` 不变；``version`` 语义与 ``GameViewResponse`` 一致。

    加法字段（D9）：``scenario`` 场景名、``goal`` 剧情目标（旧场景 ``None``）、
    ``ending_text`` 结局正文（未结束 ``None``）、``suggested_actions`` 当前可执行的下一步
    指令（成功回合也给，供前端「可点建议 / 新手引导」使用）。

    H4 加法（D-H4）：``world_book`` 本局绑定的世界书摘要（未绑定/旧存档 ``None``）、
    ``lore`` 本回合激活的世界书条目（无世界书 ``None``；确定性只读计算）。
    """

    model_config = ConfigDict(extra="forbid")

    game_id: str
    turn: int
    version: int
    snapshot_version: int
    status: Literal["active", "ended"]
    scenario: str = "m2"
    hp: int
    max_hp: int
    inventory: list[InventoryItemResponse]
    room: RoomViewResponse
    goal: GoalResponse | None = None
    ending_text: str | None = None
    suggested_actions: list[str] = Field(default_factory=list)
    world_book: WorldBookRefResponse | None = None
    lore: LoreResponse | None = None


class GameListItemResponse(BaseModel):
    """``GET /api/v1/games`` 列表项（M6 存档列表/续玩；只读，按最近更新时间倒序）。"""

    model_config = ConfigDict(extra="forbid")

    game_id: str
    turn: int
    version: int
    snapshot_version: int
    status: Literal["active", "ended"]
    scenario: str
    last_text: str | None = None
    updated_at: datetime | None = None


class GameListResponse(BaseModel):
    """``GET /api/v1/games`` 200 响应 ``{games, total}``（分页：``limit``≤50、``offset``）。"""

    model_config = ConfigDict(extra="forbid")

    games: list[GameListItemResponse]
    total: int


class ReplayEventResponse(BaseModel):
    """``GET /api/v1/games/{game_id}/replay`` 的事件项（D-M6-3，与事件表字段一一对应）。

    ``result.narrative`` 原样保留（文本回放的数据源）；``ts`` 为 UTC 时间戳。
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    seq: int
    turn_id: int
    actor_id: str
    method: str
    event_type: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    rng_seed: int
    ts: datetime


class ReplayResponse(BaseModel):
    """``GET /api/v1/games/{game_id}/replay`` 200 响应；``events`` 按全局 ``seq`` 升序。

    ``to_seq`` 为本次返回末条事件的 ``seq``；空窗口（``from_seq == last_seq + 1``）时
    为 ``from_seq - 1``（即 ``to_seq < from_seq`` 表示无事件）。
    """

    model_config = ConfigDict(extra="forbid")

    events: list[ReplayEventResponse]
    from_seq: int
    to_seq: int


class CharacterCardListItemResponse(BaseModel):
    """``GET /api/v1/character-cards`` 列表项（第 6 章 §6.7：卡片库列表）。"""

    model_config = ConfigDict(extra="forbid")

    card_id: str
    name: str
    source: CardSource
    generated_by: str | None = None
    updated_at: datetime


class CharacterCardListResponse(BaseModel):
    """``GET /api/v1/character-cards`` 200 响应 ``{cards, total}``。"""

    model_config = ConfigDict(extra="forbid")

    cards: list[CharacterCardListItemResponse]
    total: int


class CharacterCardDetailResponse(BaseModel):
    """``GET /api/v1/character-cards/{card_id}`` 200 响应 ``{card}``。"""

    model_config = ConfigDict(extra="forbid")

    card: CharacterCard


class CharacterCardWarningResponse(BaseModel):
    """人物卡告警项（与生成接口的 ``validation.warnings`` 同构：``[{code, message, field?}]``）。"""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    field: str | None = None


class CharacterCardImportResponse(BaseModel):
    """``POST /api/v1/character-cards/import`` 201 响应 ``{card_id, card, warnings}``。"""

    model_config = ConfigDict(extra="forbid")

    card_id: str
    card: CharacterCard
    warnings: list[CharacterCardWarningResponse]


class CharacterCardSaveResponse(BaseModel):
    """``POST /api/v1/character-cards``（保存）201 响应 ``{card_id, card}``。"""

    model_config = ConfigDict(extra="forbid")

    card_id: str
    card: CharacterCard


class CharacterCardValidationErrorResponse(BaseModel):
    """查询式校验错误项：``{loc, type, msg}``（与第 6 章 §6.7 示例一致）。"""

    model_config = ConfigDict(extra="forbid")

    loc: list[str]
    type: str
    msg: str


class CharacterCardValidateResponse(BaseModel):
    """``POST /api/v1/character-cards/validate`` 200 响应 ``{valid, errors}``（只读无副作用）。"""

    model_config = ConfigDict(extra="forbid")

    valid: bool
    errors: list[CharacterCardValidationErrorResponse]


class WorldBookRefResponse(BaseModel):
    """本局绑定的世界书摘要（``/view.world_book``，H4 加法字段；未绑定为 ``None``）。"""

    model_config = ConfigDict(extra="forbid")

    world_book_id: str
    name: str
    entry_count: int


class LoreEntryResponse(BaseModel):
    """本回合激活的一条世界书条目（``/view.lore.active_entries[]``，只读）。"""

    model_config = ConfigDict(extra="forbid")

    entry_uid: int
    content: str


class LoreResponse(BaseModel):
    """``/view.lore``：本回合激活的世界书条目（无世界书为 ``None``）。"""

    model_config = ConfigDict(extra="forbid")

    active_entries: list[LoreEntryResponse] = Field(default_factory=list)


class WorldBookWarningResponse(BaseModel):
    """世界书导入告警项：``[{code, message}]``（不阻塞导入）。"""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


class WorldBookSummaryResponse(BaseModel):
    """``GET /api/v1/world-books`` 列表项（按 ``updated_at`` 倒序）。"""

    model_config = ConfigDict(extra="forbid")

    world_book_id: str
    name: str
    description: str
    source: str
    entry_count: int
    updated_at: datetime


class WorldBookListResponse(BaseModel):
    """``GET /api/v1/world-books`` 200 响应 ``{world_books, total}``。"""

    model_config = ConfigDict(extra="forbid")

    world_books: list[WorldBookSummaryResponse]
    total: int


class WorldBookEntryResponse(BaseModel):
    """世界书条目（详情页/导入预览用；触发语义字段与 H4-CONTRACT §2 一致）。

    P1 加法（D-WB-3 可见性）：``probability``/``use_probability``/``group`` 组字段/
    ``scan_depth`` 与定时效果 ``sticky``/``cooldown``/``delay`` 由服务层从 ``raw_json``
    还原后返回，只读展示；旧数据缺字段时回退解析默认值（100/False/""/None/0）。
    """

    model_config = ConfigDict(extra="forbid")

    entry_uid: int
    keys: list[str]
    secondary_keys: list[str]
    content: str
    enabled: bool
    constant: bool
    order: int
    position: int
    depth: int
    selective: bool
    selective_logic: int
    case_sensitive: bool
    match_whole_words: bool
    exclude_recursion: bool
    prevent_recursion: bool
    probability: int
    use_probability: bool
    group: str
    group_weight: float
    group_override: bool
    group_scoring: bool
    scan_depth: int | None
    sticky: int
    cooldown: int
    delay: int


class WorldBookDetailBodyResponse(BaseModel):
    """``GET /api/v1/world-books/{id}`` 的 ``world_book`` 摘要。"""

    model_config = ConfigDict(extra="forbid")

    world_book_id: str
    name: str
    description: str
    source: str
    entry_count: int
    updated_at: datetime


class WorldBookDetailResponse(BaseModel):
    """``GET /api/v1/world-books/{world_book_id}`` 200 响应 ``{world_book, entries}``。"""

    model_config = ConfigDict(extra="forbid")

    world_book: WorldBookDetailBodyResponse
    entries: list[WorldBookEntryResponse]


class WorldBookImportResponse(BaseModel):
    """``POST /api/v1/world-books/import`` 201 响应。"""

    model_config = ConfigDict(extra="forbid")

    world_book_id: str
    name: str
    entry_count: int
    source: str
    warnings: list[WorldBookWarningResponse] = Field(default_factory=list)


class WorldBookValidationErrorResponse(BaseModel):
    """世界书校验错误项：``{loc, type, msg}``（与人物卡校验同构）。"""

    model_config = ConfigDict(extra="forbid")

    loc: list[str]
    type: str
    msg: str


class WorldBookValidatePreviewResponse(BaseModel):
    """校验预览：``{name, entry_count, detected_format}``（格式不可识别时占位值）。"""

    model_config = ConfigDict(extra="forbid")

    name: str = ""
    entry_count: int = 0
    detected_format: str = "unknown"


class WorldBookValidateResponse(BaseModel):
    """``POST /api/v1/world-books/validate`` 200 响应（只读无副作用）。"""

    model_config = ConfigDict(extra="forbid")

    valid: bool
    errors: list[WorldBookValidationErrorResponse] = Field(default_factory=list)
    warnings: list[WorldBookWarningResponse] = Field(default_factory=list)
    preview: WorldBookValidatePreviewResponse = Field(
        default_factory=WorldBookValidatePreviewResponse
    )


class WorldBookDeleteResponse(BaseModel):
    """``DELETE /api/v1/world-books/{world_book_id}`` 200 响应（软删）。"""

    model_config = ConfigDict(extra="forbid")

    world_book_id: str
    deleted: bool


class ScenarioStatsResponse(BaseModel):
    """场景统计（``summary``）：6 个计数，键固定（前端契约逐键断言）。"""

    model_config = ConfigDict(extra="forbid")

    rooms: int
    items: int
    npcs: int
    objectives: int
    endings: int
    suggestions: int


class ScenarioWarningResponse(BaseModel):
    """场景告警项：``[{path, message}]``（不阻断导入；``path`` 为 JSON 路径）。"""

    model_config = ConfigDict(extra="forbid")

    path: str
    message: str


class ScenarioValidationErrorResponse(BaseModel):
    """场景校验错误项：``{path, message}``（与告警同构；``path`` 如 ``rooms[2].exits.east``）。"""

    model_config = ConfigDict(extra="forbid")

    path: str
    message: str


class ScenarioSummaryResponse(BaseModel):
    """``GET /api/v1/scenarios`` 列表项（按 ``created_at`` 倒序）。"""

    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    slug: str
    title: str
    author: str
    description: str
    schema_version: int
    created_at: datetime
    summary: ScenarioStatsResponse
    warnings: list[ScenarioWarningResponse]


class ScenarioListResponse(BaseModel):
    """``GET /api/v1/scenarios`` 200 响应 ``{scenarios, total}``（不含已软删）。"""

    model_config = ConfigDict(extra="forbid")

    scenarios: list[ScenarioSummaryResponse]
    total: int


class ScenarioDetailResponse(BaseModel):
    """``GET /api/v1/scenarios/{scenario_id}`` 200 响应（含原始定义对象、summary、warnings）。"""

    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    slug: str
    title: str
    author: str
    description: str
    schema_version: int
    created_at: datetime
    definition: dict[str, Any]
    summary: ScenarioStatsResponse
    warnings: list[ScenarioWarningResponse]


class ScenarioValidateResponse(BaseModel):
    """``POST /api/v1/scenarios/validate`` 200 响应（只读预检，无副作用）。"""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    errors: list[ScenarioValidationErrorResponse]
    warnings: list[ScenarioWarningResponse]
    summary: ScenarioStatsResponse


class ScenarioImportResponse(BaseModel):
    """``POST /api/v1/scenarios`` 201 响应（导入成功）。"""

    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    slug: str
    title: str
    summary: ScenarioStatsResponse
    warnings: list[ScenarioWarningResponse]


class AppStatusResponse(BaseModel):
    """``GET /api/v1/app/status`` 200 响应（启动器控制面状态，冻结契约）。

    ``launcher_mode``：是否由 ``python -m textgame.launcher`` 启动（注入控制器）；
    ``idle_exit_seconds``：控制器实际空闲阈值（非启动器模式固定 0）；
    ``shutdown_supported``：是否支持 ``POST /api/v1/app/shutdown``；
    ``version``：后端包版本（前端「关于」展示用）。
    """

    model_config = ConfigDict(extra="forbid")

    launcher_mode: bool
    idle_exit_seconds: int
    shutdown_supported: bool
    version: str


class AppHeartbeatResponse(BaseModel):
    """``POST /api/v1/app/heartbeat`` 200 响应（前端轮询上报存活）。"""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    idle_exit_seconds: int


class AppShutdownBody(BaseModel):
    """``POST /api/v1/app/shutdown`` 请求体：``{"confirm": true}``。

    防误触：``confirm`` 必须是 JSON 布尔字面量 ``true``（false/缺省/``1``/字符串等一律 422，
    不用 ``Literal[True]`` 因为 Pydantic 会把 ``1`` 宽松等值成 True）；未知字段一律拒绝。
    """

    model_config = ConfigDict(extra="forbid")

    confirm: bool

    @model_validator(mode="before")
    @classmethod
    def _confirm_must_be_literal_true(cls, data: Any) -> Any:
        """原始 JSON 层校验 ``confirm is true``（拒绝 1/"true"/缺省等非布尔 true）。"""
        if isinstance(data, dict) and data.get("confirm") is not True:
            raise ValueError("confirm 必须为布尔 true（用于确认退出游戏）")
        return data


class AppShutdownResponse(BaseModel):
    """``POST /api/v1/app/shutdown`` 200 响应（幂等：重复调用同响应）。"""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    message: str


class AuditRecordResponse(BaseModel):
    """``GET /api/v1/games/{game_id}/audit`` 单条审计记录（读模型）。"""

    model_config = ConfigDict(extra="forbid")

    turn_id: int
    attempt: int
    proposal_id: str
    resolution_id: str | None = None
    actor_id: str
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    reason: str
    priority: int
    source: str
    verdict: str | None = None
    verdict_reason: str
    status: Literal["committed", "conflicted", "aborted"]


class AuditListResponse(BaseModel):
    """``GET /api/v1/games/{game_id}/audit`` 200 响应（只读，零写入）。"""

    model_config = ConfigDict(extra="forbid")

    records: list[AuditRecordResponse]


__all__ = [
    "AppHeartbeatResponse",
    "AppShutdownBody",
    "AppShutdownResponse",
    "AppStatusResponse",
    "AuditListResponse",
    "AuditRecordResponse",
    "CharacterCardDetailResponse",
    "CharacterCardImportResponse",
    "CharacterCardListItemResponse",
    "CharacterCardListResponse",
    "CharacterCardSaveResponse",
    "CharacterCardValidateResponse",
    "CharacterCardValidationErrorResponse",
    "CharacterCardWarningResponse",
    "CreateGameBody",
    "CreatedGameResponse",
    "GameListItemResponse",
    "GameListResponse",
    "GamePlayerViewResponse",
    "GameViewResponse",
    "GoalObjectiveResponse",
    "GoalResponse",
    "InventoryItemResponse",
    "ItemRefResponse",
    "LoreEntryResponse",
    "LoreResponse",
    "NpcRefResponse",
    "ReplayEventResponse",
    "ReplayResponse",
    "RoomViewResponse",
    "ScenarioDetailResponse",
    "ScenarioImportResponse",
    "ScenarioListResponse",
    "ScenarioStatsResponse",
    "ScenarioSummaryResponse",
    "ScenarioValidateResponse",
    "ScenarioValidationErrorResponse",
    "ScenarioWarningResponse",
    "TurnRequestBody",
    "WorldBookDeleteResponse",
    "WorldBookDetailBodyResponse",
    "WorldBookDetailResponse",
    "WorldBookEntryResponse",
    "WorldBookImportResponse",
    "WorldBookListResponse",
    "WorldBookRefResponse",
    "WorldBookSummaryResponse",
    "WorldBookValidatePreviewResponse",
    "WorldBookValidateResponse",
    "WorldBookValidationErrorResponse",
    "WorldBookWarningResponse",
]
