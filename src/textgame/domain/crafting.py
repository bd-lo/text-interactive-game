"""第 13 章 §13.2/§13.6 制造域契约类型（M4-015 切片）。

本模块是制造域的纯数据契约：只依赖标准库、pydantic 与 domain 内的 ID 类型，
不含 IO、不调 LLM、不做数值计算（calculate_craft_result 由 016 切片加入本模块）。
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

import itertools
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .types import CharacterId

__all__ = [
    "Byproduct",
    "CraftInput",
    "CraftMode",
    "CraftRequest",
    "CraftResult",
    "CraftStateSlice",
    "ItemTemplate",
    "ItemTemplateId",
    "QualityBand",
    "Recipe",
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
