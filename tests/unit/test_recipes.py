"""单元测试：M4-015 配方数据模型与加载（第 13 章 §13.2/§13.6、D-M4-1/D-M4-6）。

覆盖：内置配方加载/排序/版本、Recipe 结构与交叉校验、加载器失败分支（重复
recipe_id / 非法 JSON / 未知字段 / version<1 / 空目录 / 目录不存在）、目录查询与
name_index、importlib.resources 打包可读、CraftRequest 模式必填、ItemTemplateId
正则（模板键可用、实例 ID 不可用）。配方 JSON 内容与 textgame/data/recipes/ 保持
同步；测试不依赖工作目录，也不修改内置数据。
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from textgame.domain.crafting import CraftMode, CraftRequest, ItemTemplateId, Recipe
from textgame.services.recipe_catalog import RecipeCatalog

_BUILTIN_IDS = ("recipe.forge_sword", "recipe.signal_torch")
_BUILTIN_FILES = ("forge_sword_v1.json", "signal_torch_v1.json")
_TEMPLATE_ID = TypeAdapter(ItemTemplateId)


def _template(template_id: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "template_id": template_id,
        "name": "测试物品",
        "description": "",
        "tags": [],
        "stats": {},
        "rarity": "common",
        "stackable": False,
    }
    payload.update(overrides)
    return payload


def _recipe_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "recipe_id": "recipe.test_item",
        "name": "测试配方",
        "version": 1,
        "inputs": [{"template_id": "item.test_ingot", "qty": 1, "role": "material"}],
        "output_template_id": "item.test_item",
        "output_qty": 1,
        "station": None,
        "skill_req": {},
        "base_success_rate": 0.5,
        "quality_bands": [{"min_roll": 0.0, "quality": "common"}],
        "byproducts": [],
        "item_templates": {
            "item.test_item": _template("item.test_item", name="测试产物"),
            "item.test_ingot": _template("item.test_ingot", name="测试材料"),
        },
    }
    payload.update(overrides)
    return payload


def _write_recipes(directory: Path, *payloads: dict[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for index, payload in enumerate(payloads):
        path = directory / f"{index:02d}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# 内置配方：加载 / 排序 / 版本 / 打包
# ---------------------------------------------------------------------------


def test_builtin_recipes_load_and_are_sorted() -> None:
    catalog = RecipeCatalog.load_builtin()

    assert [recipe.recipe_id for recipe in catalog.all()] == list(_BUILTIN_IDS)
    assert len(catalog) == 2


def test_builtin_recipe_ids_unique() -> None:
    recipes = RecipeCatalog.load_builtin().all()

    assert len({recipe.recipe_id for recipe in recipes}) == len(recipes)


def test_builtin_recipe_versions_positive() -> None:
    assert all(recipe.version >= 1 for recipe in RecipeCatalog.load_builtin().all())


def test_builtin_recipes_ship_in_package() -> None:
    package = resources.files("textgame.data.recipes")
    names = tuple(sorted(entry.name for entry in package.iterdir() if entry.name.endswith(".json")))

    assert names == _BUILTIN_FILES
    catalog = RecipeCatalog.load_builtin()
    assert len(catalog.all()) == len(_BUILTIN_FILES)


# ---------------------------------------------------------------------------
# Recipe 结构与交叉校验
# ---------------------------------------------------------------------------


def test_recipe_requires_inputs_and_quality_bands() -> None:
    payload = _recipe_payload()
    payload.pop("inputs")
    with pytest.raises(ValidationError):
        Recipe.model_validate(payload)

    payload = _recipe_payload()
    payload.pop("quality_bands")
    with pytest.raises(ValidationError):
        Recipe.model_validate(payload)

    too_many_inputs = [{"template_id": "item.test_ingot", "qty": 1, "role": "material"}] * 9
    with pytest.raises(ValidationError):
        Recipe.model_validate(_recipe_payload(inputs=too_many_inputs))


def test_quality_bands_must_start_at_zero() -> None:
    payload = _recipe_payload(quality_bands=[{"min_roll": 0.2, "quality": "common"}])

    with pytest.raises(ValidationError, match=r"start at min_roll=0\.0"):
        Recipe.model_validate(payload)


def test_quality_bands_must_be_strictly_ascending() -> None:
    payload = _recipe_payload(
        quality_bands=[
            {"min_roll": 0.0, "quality": "common"},
            {"min_roll": 0.0, "quality": "fine"},
        ]
    )

    with pytest.raises(ValidationError, match="strictly ascending"):
        Recipe.model_validate(payload)


def test_recipe_item_templates_cover_output_and_byproducts() -> None:
    with pytest.raises(ValidationError, match="output_template_id"):
        Recipe.model_validate(_recipe_payload(output_template_id="item.missing"))

    byproducts = [{"template_id": "item.missing", "qty": 1, "chance": 0.5}]
    with pytest.raises(ValidationError, match="byproduct templates"):
        Recipe.model_validate(_recipe_payload(byproducts=byproducts))


def test_recipe_item_template_ids_match_keys() -> None:
    payload = _recipe_payload(output_template_id="item.other")
    payload["item_templates"]["item.test_item"] = _template("item.other")

    with pytest.raises(ValidationError, match=r"must equal template\.template_id"):
        Recipe.model_validate(payload)


def test_recipe_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        Recipe.model_validate(_recipe_payload(unknown_field=1))


# ---------------------------------------------------------------------------
# 加载器失败分支
# ---------------------------------------------------------------------------


def test_recipe_loader_rejects_duplicate_recipe_id(tmp_path: Path) -> None:
    _write_recipes(tmp_path, _recipe_payload(), _recipe_payload())

    with pytest.raises(ValueError, match="duplicate recipe_id"):
        RecipeCatalog.load_from_dir(tmp_path)


def test_recipe_loader_rejects_invalid_json(tmp_path: Path) -> None:
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(ValueError, match="invalid JSON"):
        RecipeCatalog.load_from_dir(tmp_path)


def test_recipe_loader_rejects_unknown_field(tmp_path: Path) -> None:
    _write_recipes(tmp_path, _recipe_payload(unexpected="x"))

    with pytest.raises(ValueError, match="invalid recipe"):
        RecipeCatalog.load_from_dir(tmp_path)


def test_recipe_loader_rejects_non_positive_version(tmp_path: Path) -> None:
    _write_recipes(tmp_path, _recipe_payload(version=0))

    with pytest.raises(ValueError, match="invalid recipe"):
        RecipeCatalog.load_from_dir(tmp_path)


def test_recipe_loader_rejects_missing_or_empty_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="recipe directory not found"):
        RecipeCatalog.load_from_dir(tmp_path / "missing")

    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="no recipe JSON files"):
        RecipeCatalog.load_from_dir(empty)


# ---------------------------------------------------------------------------
# 目录查询与索引
# ---------------------------------------------------------------------------


def test_catalog_get_returns_none_for_unknown() -> None:
    catalog = RecipeCatalog.load_builtin()

    assert catalog.get("recipe.no_such_recipe") is None
    assert catalog.get("recipe.forge_sword") is not None
    assert "recipe.signal_torch" in catalog
    assert "recipe.no_such_recipe" not in catalog


def test_catalog_require_raises_for_unknown() -> None:
    catalog = RecipeCatalog.load_builtin()

    assert catalog.require("recipe.forge_sword").recipe_id == "recipe.forge_sword"
    with pytest.raises(KeyError, match="unknown recipe_id"):
        catalog.require("recipe.no_such_recipe")


def test_name_index_is_deterministic_and_has_both_recipes() -> None:
    catalog = RecipeCatalog.load_builtin()
    expected = {"铁剑": "recipe.forge_sword", "信号火把": "recipe.signal_torch"}

    assert catalog.name_index() == expected
    assert catalog.name_index() == expected
    assert catalog.version_index == {"recipe.forge_sword": 1, "recipe.signal_torch": 1}


# ---------------------------------------------------------------------------
# CraftRequest 与 ItemTemplateId
# ---------------------------------------------------------------------------


def test_craft_request_requires_mode_specific_fields() -> None:
    with pytest.raises(ValidationError):
        CraftRequest.model_validate({})

    with pytest.raises(ValidationError, match="recipe_id is required"):
        CraftRequest(mode=CraftMode.RECIPE)

    with pytest.raises(ValidationError, match="free_description is required"):
        CraftRequest(mode=CraftMode.FREE)

    assert CraftRequest(mode=CraftMode.RECIPE, recipe_id="recipe.forge_sword").recipe_id
    assert CraftRequest(mode=CraftMode.FREE, free_description="把火把和铜线绑在一起").mode is (
        CraftMode.FREE
    )

    with pytest.raises(ValidationError):
        CraftRequest(mode=CraftMode.FREE, free_description="x" * 201)


def test_item_template_id_accepts_templates_and_rejects_instance_ids() -> None:
    assert _TEMPLATE_ID.validate_python("item.iron_ingot") == "item.iron_ingot"
    assert _TEMPLATE_ID.validate_python("item_torch") == "item_torch"
    with pytest.raises(ValidationError):
        _TEMPLATE_ID.validate_python("item_01J9Z3K4Q7X8Y9Z0ABCDEFGHJN")

    # 内置配方的输入/产出/副产物模板键都必须是 ItemTemplateId（而非实例 ID）。
    for recipe in RecipeCatalog.load_builtin().all():
        template_ids = {recipe.output_template_id, *recipe.item_templates}
        template_ids.update(input_.template_id for input_ in recipe.inputs)
        template_ids.update(byproduct.template_id for byproduct in recipe.byproducts)
        for template_id in template_ids:
            assert _TEMPLATE_ID.validate_python(template_id) == template_id
        assert recipe.output_template_id in recipe.item_templates
        assert all(input_.qty >= 1 for input_ in recipe.inputs)
