"""第 13 章 §13.2/§13.6 制造域契约类型（M4-015 切片）。

本模块是制造域的纯数据契约与纯函数（M4-015 契约 + M4-016 确定性计算）：只依赖标准库、
pydantic 与 domain 内的 ID/随机类型，不含 IO、不调 LLM。
材料与产物按「模板 / 实例分离」建模（D-M4-1）：

- 配方、草案与制造事件中的“材料引用”一律用物品模板键 ItemTemplateId
  （复用第 5 章 §5.7 的正则，规范写法 item.<name>，如 item.iron_ingot）；
  物品实例 ID item_<26位ULID> 因大写字符不匹配该正则，不得用作模板键；
- 背包与产出仍是物品实例 ItemId（第 5 章 ID 规则），实例化由服务层在
  事件 result.produced_items 中记录（第 13 章 §13.2、第 14 章 §14.7）。

Recipe 的交叉校验（第 13 章 §13.2）：output_template_id 与每个
byproduct.template_id 必须出现在 item_templates 中、item_templates 的键等于元素
template_id、quality_bands 按 min_roll 严格升序且首档 min_roll == 0.0、
inputs 为 1–8 项。
"""

from __future__ import annotations

import hashlib
import itertools
from enum import Enum
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import GameState, Item
from .rules.dice import DerivedRandom
from .types import CharacterId, ItemId

__all__ = [
    "Byproduct",
    "CraftDraft",
    "CraftInput",
    "CraftItemView",
    "CraftMode",
    "CraftRequest",
    "CraftResult",
    "CraftStateSlice",
    "CraftView",
    "ItemTemplate",
    "ItemTemplateId",
    "QualityBand",
    "Recipe",
    "build_craft_state_slice",
    "calculate_craft_result",
    "derive_item_id",
    "instantiate_craft_result",
    "recipe_from_draft",
]

#: 物品模板键：复用第 5 章 §5.7 的定义（规范写法 item.<name>；item_<name> 亦接受）。
#: item_<26位ULID> 是实例 ID（大写字符不匹配 [a-z]），不得作模板键（D-M4-1）。
ItemTemplateId = Annotated[str, Field(pattern=r"^item[._][a-z][a-z0-9_.]{1,63}$")]


class CraftMode(str, Enum):
    """制造路径（第 13 章 §13.2）：有配方走确定性，无配方走生成路径。"""

    RECIPE = "recipe"
    FREE = "free"


class ItemTemplate(BaseModel):
    """物品模板：配方内嵌的产出/副产物定义（回放不依赖配方目录，D-M4-1）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    template_id: ItemTemplateId
    name: str = Field(min_length=1, max_length=32)
    description: str = Field(max_length=500)
    tags: list[str] = Field(default_factory=list, max_length=7)  # 实例化前置模板键，实例 tags ≤8
    stats: dict[str, int | float] = Field(default_factory=dict)
    rarity: Literal["common", "uncommon", "rare", "epic", "legendary"] = "common"
    stackable: bool = False


class CraftInput(BaseModel):
    """一项配方输入：物品模板键 + 数量 + 角色（材料/工具/催化剂）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    template_id: ItemTemplateId
    qty: int = Field(ge=1)
    role: Literal["material", "tool", "catalyst"]


class QualityBand(BaseModel):
    """质量档：min_roll 为命中该档的最低掷骰值（0.0–1.0，升序分档）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    min_roll: float = Field(ge=0.0, le=1.0)
    quality: Literal["poor", "common", "fine", "masterwork"]


class Byproduct(BaseModel):
    """副产物：模板键 + 数量 + 触发概率（仅实际触发项会进入结果/事件）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    template_id: ItemTemplateId
    qty: int = Field(ge=1)
    chance: float = Field(ge=0.0, le=1.0)


class CraftStateSlice(BaseModel):
    """calculate_craft_result 的确定性输入：只含本次制造所需视图（第 13 章 §13.2）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor_id: CharacterId
    inventory: dict[ItemTemplateId, int]  # 服务层按实例模板标签聚合（数量=同模板实例重复项）
    skill_levels: dict[str, int]  # M4 恒为 {}（D-M4-2）
    station_id: str | None = None  # 房间 ambient_tags 的 station.<name>（D-M4-9）
    next_seq: int = Field(ge=1)  # = 存档 last_seq + 1，服务层注入（D-M4-4）


class CraftResult(BaseModel):
    """calculate_craft_result 的实例化前模板级结果（第 13 章 §13.2、N-01）。

    consumed/produced/byproducts 只含物品模板键与数量；产出与副产物的实例 ID、
    完整 Item 定义只出现在事件 result.produced_items 中。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ok: bool
    success: bool
    consumed: list[CraftInput]  # 实际扣除项，tool 不消耗（D-M4-8）
    produced: list[CraftInput]  # role 固定 "material"（无语义，D-M4-8）
    byproducts: list[Byproduct] = Field(default_factory=list)  # 仅实际触发项
    quality: str | None = None
    rng_seed: int = Field(ge=0, le=0x7FFF_FFFF_FFFF_FFFF)
    reason: str | None = None


class CraftRequest(BaseModel):
    """服务层制造入参（第 13 章 §13.2 / 第 6 章）：模式必填，按模式要求对应字段。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: CraftMode
    recipe_id: str | None = Field(default=None, min_length=1, max_length=80)
    free_description: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def _mode_specific_fields(self) -> CraftRequest:
        if self.mode is CraftMode.RECIPE and self.recipe_id is None:
            raise ValueError("recipe_id is required when mode=recipe")
        if self.mode is CraftMode.FREE and self.free_description is None:
            raise ValueError("free_description is required when mode=free")
        return self


class Recipe(BaseModel):
    """配方数据模型（第 13 章 §13.2）：JSON 反序列化后即完成全部结构与交叉校验。

    item_templates 承载产出/副产物物品定义（M4 扩展，D-M4-1）；version 变更不允许
    原地修改，必须新增版本（事件回放要能还原旧配方语义，第 14 章）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    recipe_id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=30)
    version: int = Field(default=1, ge=1)
    inputs: list[CraftInput] = Field(min_length=1, max_length=8)
    output_template_id: ItemTemplateId
    output_qty: int = Field(default=1, ge=1)
    station: str | None = None  # 工位名，与房间 ambient_tags 的 station.<name> 同串（D-M4-9）
    skill_req: dict[str, int] = Field(default_factory=dict)  # M4 不校验（D-M4-2）
    base_success_rate: float = Field(ge=0.0, le=1.0)
    quality_bands: list[QualityBand] = Field(min_length=1)
    byproducts: list[Byproduct] = Field(default_factory=list)
    item_templates: dict[ItemTemplateId, ItemTemplate]  # 产出/副产物 Item 定义（D-M4-1）

    @model_validator(mode="after")
    def _cross_validate(self) -> Recipe:
        """第 13 章 §13.2 的 Recipe 交叉校验（错误信息带规则名，便于启动期定位）。"""
        for key, template in self.item_templates.items():
            if key != template.template_id:
                raise ValueError(
                    f"item_templates key {key!r} must equal template.template_id "
                    f"{template.template_id!r}"
                )
        if self.output_template_id not in self.item_templates:
            raise ValueError(
                f"output_template_id {self.output_template_id!r} is not in item_templates"
            )
        missing = [
            byproduct.template_id
            for byproduct in self.byproducts
            if byproduct.template_id not in self.item_templates
        ]
        if missing:
            raise ValueError(f"byproduct templates {missing!r} are not in item_templates")

        rolls = [band.min_roll for band in self.quality_bands]
        if rolls[0] != 0.0:
            raise ValueError("quality_bands must start at min_roll=0.0")
        for earlier, later in itertools.pairwise(rolls):
            if later <= earlier:
                raise ValueError("quality_bands must be strictly ascending by min_roll")
        return self


# ---------------------------------------------------------------------------
# M4-016 确定性计算与实例化（第 13 章 §13.2/§13.6、D-M4-1/D-M4-4/D-M4-8）
# ---------------------------------------------------------------------------

#: Crockford Base32 字符表（ULID 字符集；排除 I/L/O/U）。
_CROCKFORD_BASE32: Final[str] = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ITEM_ID_RADIX_BITS: Final[int] = 130  # 26 字符 × 5 bit
_ITEM_ID_CHARS: Final[int] = 26
_SUCCESS_ROLL_SCALE: Final[int] = 1_000_000


def derive_item_id(
    *,
    save_seed: str,
    turn_id: int,
    actor_id: str,
    craft_kind: str,
    event_seq: int,
    ordinal: int,
) -> str:
    """确定性派生制造产物的物品实例 ID（第 13 章 §13.2、D-M4-1）。

    blake2b(上下文键) 取 130 bit → Crockford Base32 26 位，前缀 item_，必然匹配
    domain.types.ItemId 的 ^item_<26位ULID>$ 正则；禁止 uuid4/random。同一上下文
    （存档种子/回合/角色/事件类型/事件序号/产出序号）必得同一 ID。
    """
    if turn_id < 1:
        raise ValueError(f"turn_id must be >= 1, got {turn_id}")
    if event_seq < 1:
        raise ValueError(f"event_seq must be >= 1, got {event_seq}")
    if ordinal < 0:
        raise ValueError(f"ordinal must be >= 0, got {ordinal}")
    key = f"{save_seed}|{turn_id}|{actor_id}|{craft_kind}|{event_seq}|{ordinal}".encode()
    digest = hashlib.blake2b(key, digest_size=32).digest()
    value = int.from_bytes(digest, "big") >> (len(digest) * 8 - _ITEM_ID_RADIX_BITS)
    chars = [
        _CROCKFORD_BASE32[(value >> (5 * (_ITEM_ID_CHARS - 1 - index))) & 0x1F]
        for index in range(_ITEM_ID_CHARS)
    ]
    return "item_" + "".join(chars)


def _quality_for(quality_bands: list[QualityBand], quality_roll: float) -> str:
    """命中「min_roll ≤ roll 的最高档」；分档已保证首档 min_roll == 0.0。"""
    quality = quality_bands[0].quality
    for band in quality_bands:
        if band.min_roll <= quality_roll:
            quality = band.quality
    return quality


def calculate_craft_result(
    state: CraftStateSlice,
    recipe: Recipe,
    actor_id: CharacterId,
    rng_seed: int,
) -> CraftResult:
    """确定性制造结算（第 13 章 §13.2 固定签名，唯一数值权威）。

    掷骰顺序固定：成功判定 → 质量档（成功时）→ 按 recipe.byproducts 顺序逐项副产物。
    公式：success_roll = rng.randint(0, 999_999) / 1_000_000，
    success = success_roll < recipe.base_success_rate；失败时 consumed/produced/
    byproducts 全空、quality=None。consumed 只含 material/catalyst（tool 不消耗），
    produced 用输出模板键与数量；实例 ID 与完整 Item 由 instantiate_craft_result 生成
    （N-01）。同一 (state, recipe, actor_id, rng_seed) 必得同一结果。
    """
    if actor_id != state.actor_id:
        raise ValueError(f"actor_id {actor_id!r} does not match CraftStateSlice.actor_id")
    rng = DerivedRandom(rng_seed)
    success_roll = rng.randint(0, _SUCCESS_ROLL_SCALE - 1) / _SUCCESS_ROLL_SCALE
    success = success_roll < recipe.base_success_rate
    if not success:
        return CraftResult(
            ok=True,
            success=False,
            consumed=[],
            produced=[],
            byproducts=[],
            quality=None,
            rng_seed=rng_seed,
            reason="success rate not met",
        )
    quality_roll = rng.randint(0, _SUCCESS_ROLL_SCALE - 1) / _SUCCESS_ROLL_SCALE
    byproducts = [
        byproduct
        for byproduct in recipe.byproducts
        if rng.randint(0, _SUCCESS_ROLL_SCALE - 1) / _SUCCESS_ROLL_SCALE < byproduct.chance
    ]
    return CraftResult(
        ok=True,
        success=True,
        consumed=[item for item in recipe.inputs if item.role in {"material", "catalyst"}],
        produced=[
            CraftInput(
                template_id=recipe.output_template_id,
                qty=recipe.output_qty,
                role="material",
            )
        ],
        byproducts=byproducts,
        quality=_quality_for(recipe.quality_bands, quality_roll),
        rng_seed=rng_seed,
        reason=None,
    )


def instantiate_craft_result(
    result: CraftResult,
    recipe: Recipe,
    *,
    save_seed: str,
    turn_id: int,
    actor_id: str,
    craft_kind: str = "CRAFT_DETERMINISTIC",
    event_seq: int = 1,
) -> tuple[CraftResult, list[Item]]:
    """把模板级 CraftResult 实例化为具体 Item（第 13 章 §13.2、N-01）。

    输出顺序固定：result.produced（按条目与 qty）→ result.byproducts（按顺序与 qty）；
    每个实例 id = derive_item_id(...)、tags = [模板键, *模板 tags]（≤8），
    name/description/stats/rarity/stackable 取模板。返回入参 result 原样（实例化不改变
    模板级数值结果）与实例定义列表；事件 result.produced_items 保存后者。
    """
    items: list[Item] = []
    ordinal = 0

    def _instantiate(template_id: str, qty: int) -> None:
        nonlocal ordinal
        template = recipe.item_templates.get(template_id)
        if template is None:
            raise ValueError(f"recipe item template {template_id!r} does not exist")
        tags = [template.template_id, *template.tags][:8]
        for _ in range(qty):
            item_id = derive_item_id(
                save_seed=save_seed,
                turn_id=turn_id,
                actor_id=actor_id,
                craft_kind=craft_kind,
                event_seq=event_seq,
                ordinal=ordinal,
            )
            ordinal += 1
            items.append(
                Item(
                    id=item_id,
                    name=template.name,
                    description=template.description,
                    tags=tags,
                    stats=dict(template.stats),
                    rarity=template.rarity,
                    stackable=template.stackable,
                )
            )

    for produced in result.produced:
        _instantiate(produced.template_id, produced.qty)
    for byproduct in result.byproducts:
        _instantiate(byproduct.template_id, byproduct.qty)
    return result, items


def build_craft_state_slice(state: GameState, actor_id: str) -> CraftStateSlice:
    """从 GameState 构造只读制造视图（第 13 章 §13.2、D-M4-1/D-M4-4/D-M4-9）。

    背包按 Item.tags[0]（模板键）聚合：数量 = 同模板实例在 Character.inventory 中的
    重复项数；工位取当前房间 ambient_tags 中 station. 前缀标签（如 station.forge）；
    skill_levels M4 恒 {}（D-M4-2）；next_seq = 存档 last_seq + 1（服务层注入，D-M4-4）。
    actor 不存在/物品缺模板键 → ValueError。
    """
    character = state.characters.get(actor_id)
    if character is None:
        raise ValueError(f"actor {actor_id!r} is not present in state")
    inventory: dict[str, int] = {}
    for item_id in character.inventory:
        item = state.items.get(item_id)
        if item is None:
            raise ValueError(f"inventory item {item_id!r} has no definition in state.items")
        if not item.tags:
            raise ValueError(f"inventory item {item_id!r} has no template tag")
        template_id = item.tags[0]
        inventory[template_id] = inventory.get(template_id, 0) + 1
    room = state.rooms.get(character.location)
    station_id = None
    if room is not None:
        station_id = next(
            (tag for tag in room.ambient_tags if tag.startswith("station.")),
            None,
        )
    return CraftStateSlice(
        actor_id=character.id,
        inventory=inventory,
        skill_levels={},
        station_id=station_id,
        next_seq=state.rng_state.last_seq + 1,
    )


# ---------------------------------------------------------------------------
# M4-017 自由制造生成路径契约（第 13 章 §13.4、第 8 章 §8.2、D-M4-3/D-M4-7）
# ---------------------------------------------------------------------------


class CraftDraft(BaseModel):
    """CraftOutput 规范化后的草案（第 13 章 §13.2）：只含名称/材料/工位/步骤。

    extra=forbid：任何额外字段（stats/damage/quality/成功率等 LLM 数值）都会被
    pydantic 拒绝，经一次修复重试后仍失败 → SCHEMA_VIOLATION（第 12 章 §12.6）。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    item_name: str = Field(min_length=1, max_length=30)
    output_template_id: ItemTemplateId | None = None
    inputs: list[CraftInput] = Field(min_length=1, max_length=8)
    station: str | None = None
    steps: list[str] = Field(default_factory=list, max_length=8)


class CraftItemView(BaseModel):
    """CraftView 中的背包行（服务层投影，不暴露 GameState/其他角色数据）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    item_id: ItemId
    template_id: ItemTemplateId
    name: str = Field(min_length=1, max_length=32)
    qty: int = Field(ge=1)


class CraftView(BaseModel):
    """制造 Agent 的私有只读视图（第 13 章 §13.4、第 8 章 §8.2）：不含全局状态。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor_id: CharacterId
    inventory: list[CraftItemView] = Field(default_factory=list)
    station_id: str | None = None
    known_recipe_ids: list[str] = Field(default_factory=list)
    free_description: str = Field(min_length=1, max_length=200)


def _free_output_template_id(item_name: str) -> str:
    """为自由制造产出派生确定性模板键（item.crafted_<8hex>，匹配 ItemTemplateId）。"""
    digest = hashlib.blake2b(item_name.encode("utf-8"), digest_size=4).hexdigest()
    return f"item.crafted_{digest}"


def recipe_from_draft(
    draft: CraftDraft,
    view: CraftView,
    *,
    save_seed: str,
    turn_id: int,
    actor_id: str,
) -> Recipe:
    """把自由制造草案折算为临时 Recipe（第 13 章 §13.4 步骤 5、D-M4-7）。

    - inputs 取 draft.inputs；output_template_id 取 draft.output_template_id，缺省时按
      item_name 确定性派生 item.crafted_<8hex>（匹配 ItemTemplateId 正则）；
    - item_templates 仅内嵌该产出模板（name=draft.item_name，description 空，stats 空，
      rarity=common，stackable=False；不接收 LLM 数值）；
    - station=draft.station；output_qty=1；skill_req={}；byproducts=[]；
    - 数值口径由框架固定：base_success_rate=0.5、quality_bands=[0.0 common, 0.8 fine]；
    - save_seed/turn_id/actor_id 由服务层传入，保留给 M5 配方版本/缓存键使用（M4 不写事件）。
    """
    del save_seed, turn_id, actor_id  # M4 临时 Recipe 不进入目录/事件；保留签名供 M5 扩展
    output_template_id = draft.output_template_id or _free_output_template_id(draft.item_name)
    template = ItemTemplate(
        template_id=output_template_id,
        name=draft.item_name,
        description="",
        tags=[],
        stats={},
        rarity="common",
        stackable=False,
    )
    return Recipe(
        recipe_id=f"recipe.free_{hashlib.blake2b(draft.item_name.encode('utf-8'), digest_size=4).hexdigest()}",
        name=draft.item_name,
        version=1,
        inputs=list(draft.inputs),
        output_template_id=output_template_id,
        output_qty=1,
        station=draft.station,
        skill_req={},
        base_success_rate=0.5,
        quality_bands=[
            QualityBand(min_roll=0.0, quality="common"),
            QualityBand(min_roll=0.8, quality="fine"),
        ],
        byproducts=[],
        item_templates={output_template_id: template},
    )
