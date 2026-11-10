"""第 5 章 §5.7 ``CharacterCard`` DTO 与物品模板目录（M6 人物卡库后端契约）。

``CharacterCard`` 是 v0.2 前端（Vue 3 SPA，第 23 章 §23.5/§23.6）与后端共享的**人物卡
JSON 契约**，不是领域实体：只在 API 边界存在，由服务端 Pydantic 权威校验，开局时再映射
为 ``Character`` 并写入新存档（任务 task-24）。字段与第 5 章 §5.7、第 23 章 §23.4 表逐字
一致，**不得增删**；``build_character_card_schema()`` 是唯一 Schema 生成入口，
``GET /api/v1/character-cards/schema`` 直接返回 ``CHARACTER_CARD_SCHEMA`` 对象本身。

本模块同时提供内置**物品模板目录**（``ITEM_TEMPLATES``）：``starting_items`` 里是物品模板
ID（``item.<name>`` 或 ``item_<name>`` 两种写法，如 ``item.torch``/``item_torch``），开局时
经 :func:`instantiate_item` 实例化为 ``Item``（实例 ID 由调用方生成，禁止模板 ID 直接当
实例 ID）。目录是冻结的常量数据，不读库、不读配置、不碰 IO（第 5 章 §5.4/§5.5 分层）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Annotated, Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from .models import Item

#: API 层 ID 只由服务端生成（第 5 章 §5.7）；``card_<26 位 ULID>``。
_ULID = r"[0-9A-HJKMNP-TV-Z]{26}"
CardId = Annotated[str, Field(pattern=rf"^card_{_ULID}$")]
#: 物品模板 ID：``item.<name>`` 或 ``item_<name>``；``item_<26 位 ULID>`` 是实例 ID，不接受。
ItemTemplateId = Annotated[str, Field(pattern=r"^item[._][a-z][a-z0-9_.]{1,63}$")]

AttributeKey = Literal["str", "dex", "con", "int", "wis", "cha", "max_hp"]
CardSource = Literal["upload", "llm", "manual"]

#: ``max_hp`` 是生命上限，必须 ≥1（否则 ``hp >= 0`` 与 ``hp <= max_hp`` 无解、卡片永不可能
#: 合法）；其余六键 -10..100（第 5 章 §5.7）。
_ATTRIBUTE_BOUNDS: Final[dict[str, tuple[int, int]]] = {
    "str": (-10, 100),
    "dex": (-10, 100),
    "con": (-10, 100),
    "int": (-10, 100),
    "wis": (-10, 100),
    "cha": (-10, 100),
    "max_hp": (1, 100),
}
_ATTRIBUTE_KEYS: Final[frozenset[str]] = frozenset(_ATTRIBUTE_BOUNDS)


class CharacterCard(BaseModel):
    """人物卡 DTO（v0.2）：上传 / LLM 生成 / 手填共用同一份 JSON 契约；不进入 GameState。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"]
    name: str = Field(min_length=1, max_length=24)
    attributes: dict[AttributeKey, int]
    hp: int = Field(ge=0)
    starting_items: list[ItemTemplateId] = Field(max_length=8)
    background: str = Field(max_length=2000)
    appearance: str | None = Field(default=None, max_length=500)
    source: CardSource
    generated_by: str | None = Field(default=None, max_length=120)

    @field_validator("name", "background", mode="before")
    @classmethod
    def _trim(cls, value: object) -> object:
        """``name``/``background`` 先 trim 再计长度（服务端规范化口径，第 5 章 §5.7）。

        ``mode="before"`` 保证约束在 trim **之后**生效：全空白 ``name`` 会因
        ``min_length=1`` 被拒绝，而不是先通过再变成空串。
        """
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("attributes")
    @classmethod
    def _exact_keys_and_range(cls, value: dict[AttributeKey, int]) -> dict[AttributeKey, int]:
        """``attributes`` 必须恰好 7 个已知键且取值在各自值域内。"""
        if set(value) != _ATTRIBUTE_KEYS:
            missing = sorted(_ATTRIBUTE_KEYS - set(value))
            extra = sorted(set(value) - _ATTRIBUTE_KEYS)
            raise ValueError(
                f"attributes keys must be exactly 7 known keys; missing={missing} extra={extra}"
            )
        for key, number in value.items():
            low, high = _ATTRIBUTE_BOUNDS[key]
            if not (low <= number <= high):
                raise ValueError(f"attribute {key} must be within [{low}, {high}]")
        return value

    @field_validator("hp")
    @classmethod
    def _hp_within_max(cls, value: int, info: ValidationInfo) -> int:
        """``0 <= hp <= attributes.max_hp``（``max_hp >= 1`` 已在键值域校验中保证）。

        用字段级校验器（而非 model 级）是为了让 Pydantic 的 ``loc`` 落在 ``["hp"]`` 上，
        与第 23 章 §23.5 的失败响应示例（``violations[].loc == ["hp"]``）一致；
        ``attributes`` 定义在 ``hp`` 之前，校验时已可用；attributes 本身非法时跳过本检查
        （由 attributes 的错误统一报告）。
        """
        attributes = info.data.get("attributes")
        if isinstance(attributes, dict):
            max_hp = attributes.get("max_hp")
            if isinstance(max_hp, int) and value > max_hp:
                raise ValueError("hp must be <= attributes.max_hp")
        return value


def build_character_card_schema() -> dict[str, Any]:
    """生成与前端共享的 JSON Schema（draft 2020-12；前端 Ajv2020 直接复用同一份）。"""
    schema = CharacterCard.model_json_schema(mode="validation", ref_template="#/$defs/{model}")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://textgame.local/schemas/character-card-1.0.json"
    # attributes 是 dict[Literal, int]，Pydantic 只给 propertyNames + additionalProperties；
    # 这里显式展开为逐键 properties，让前端 Ajv 也能校验每个键的取值范围（max_hp minimum:1）
    attributes = schema["properties"]["attributes"]
    attributes.pop("additionalProperties", None)
    attributes.pop("propertyNames", None)
    attributes["properties"] = {
        key: {"type": "integer", "minimum": low, "maximum": high}
        for key, (low, high) in _ATTRIBUTE_BOUNDS.items()
    }
    attributes["required"] = sorted(_ATTRIBUTE_KEYS)
    attributes["additionalProperties"] = False
    return schema


#: ``GET /api/v1/character-cards/schema`` 直接返回本对象（不套信封，第 6 章 §6.7）。
CHARACTER_CARD_SCHEMA: Final[dict[str, Any]] = build_character_card_schema()


# ---------------------------------------------------------------------------
# 内置物品模板目录（character card starting_items → Item 实例化）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ItemTemplate:
    """物品模板：``starting_items`` 元素 → 开局 ``Item`` 实例的定义（冻结常量数据）。"""

    template_id: str
    name: str
    description: str
    tags: tuple[str, ...] = ()
    stats: Mapping[str, int | float] = field(default_factory=dict)


def _t(
    template_id: str,
    name: str,
    description: str,
    tags: tuple[str, ...] = (),
    stats: Mapping[str, int | float] | None = None,
) -> ItemTemplate:
    """构造一条模板记录（目录字面量用；避免重复写字段名）。"""
    return ItemTemplate(
        template_id=template_id,
        name=name,
        description=description,
        tags=tags,
        stats={} if stats is None else dict(stats),
    )


#: canonical 模板键（``item.<name>``）→ 模板。``item_<name>`` 写法由
#: :func:`canonical_item_template_id` 归一到同一批键。
_CANONICAL_TEMPLATES: Final[dict[str, ItemTemplate]] = {
    "item.torch": _t("item.torch", "火把", "一支受潮的火把，布头上还留着松脂的气味。", ("light",)),
    "item.potion": _t(
        "item.potion",
        "治疗药水",
        "小瓶装的红色药水，木塞封口，液体在瓶里轻轻晃。",
        ("consumable",),
        {"heal": 8},
    ),
    "item.sword": _t(
        "item.sword",
        "锈剑",
        "一柄锈迹斑斑的短剑，刃口钝了，但分量还在。",
        ("weapon",),
        {"damage": 3},
    ),
    "item.relic": _t(
        "item.relic", "古物", "看不出用途的青铜小件，表面刻着早已磨平的纹路。", ("quest",)
    ),
    "item.rusty_sword": _t(
        "item.rusty_sword",
        "锈剑",
        "一柄锈迹斑斑的短剑，刃口钝了，但分量还在。",
        ("weapon",),
        {"damage": 3},
    ),
    "item.rusty_knife": _t(
        "item.rusty_knife",
        "锈刀",
        "缺了口的短刀，柄上缠着发黑的布条。",
        ("weapon",),
        {"damage": 2},
    ),
    "item.chalk": _t("item.chalk", "粉笔", "半截白色粉笔，能在石墙上留个记号。", ("misc",)),
    "item.oak_cane": _t(
        "item.oak_cane",
        "橡木手杖",
        "一根打磨光滑的橡木手杖，抵得住坑洼的路面。",
        ("weapon",),
        {"damage": 1},
    ),
    "item.iron_ingot": _t(
        "item.iron_ingot",
        "铁锭",
        "一块沉甸甸的粗铁锭，边角还带着没敲净的炉渣。",
        ("item.iron_ingot", "material"),
    ),
    "item.oak_handle": _t(
        "item.oak_handle",
        "橡木柄",
        "打磨过的橡木柄，握上去还算顺手。",
        ("item.oak_handle", "material"),
    ),
    "item.forge_hammer": _t(
        "item.forge_hammer",
        "锻锤",
        "一柄趁手的锻锤，锤面被多年的敲打磨得发亮。",
        ("item.forge_hammer", "tool"),
    ),
    "item.copper_wire": _t(
        "item.copper_wire",
        "铜线",
        "一截剥了皮的铜线，在光下泛着暗红。",
        ("item.copper_wire", "material"),
    ),
}

#: 只读模板目录（canonical 键 → 模板）；非法写入直接抛 ``TypeError``。
ITEM_TEMPLATES: Final[Mapping[str, ItemTemplate]] = MappingProxyType(_CANONICAL_TEMPLATES)


def canonical_item_template_id(template_id: str) -> str:
    """把 ``item_<name>`` 形式归一为 canonical ``item.<name>`` 键；未知模板抛 ``KeyError``。

    两种写法在服务端等价（第 5 章 §5.7 校验等价性），但是否存在以本目录为准；服务层把
    ``KeyError`` 映射为 ``NOT_FOUND``。
    """
    if template_id in _CANONICAL_TEMPLATES:
        return template_id
    if template_id.startswith("item_"):
        dotted = f"item.{template_id[len('item_') :]}"
        if dotted in _CANONICAL_TEMPLATES:
            return dotted
    raise KeyError(template_id)


def resolve_item_template(template_id: str) -> ItemTemplate:
    """按任意合法写法取模板定义；未知模板抛 ``KeyError``。"""
    return _CANONICAL_TEMPLATES[canonical_item_template_id(template_id)]


def instantiate_item(template_id: str, instance_id: str) -> Item:
    """把物品模板实例化为 ``Item``；``instance_id`` 必须由调用方生成（``item_<ULID>``）。

    模板不存在抛 ``KeyError``（服务层映射 ``NOT_FOUND``）；``instance_id`` 的格式由
    ``domain.models.Item`` 的 ``ItemId`` 约束校验。
    """
    template = resolve_item_template(template_id)
    return Item(
        id=instance_id,
        name=template.name,
        description=template.description,
        tags=list(template.tags),
        stats=dict(template.stats),
    )


__all__ = [
    "CHARACTER_CARD_SCHEMA",
    "ITEM_TEMPLATES",
    "AttributeKey",
    "CardId",
    "CardSource",
    "CharacterCard",
    "ItemTemplate",
    "ItemTemplateId",
    "build_character_card_schema",
    "canonical_item_template_id",
    "instantiate_item",
    "resolve_item_template",
]
