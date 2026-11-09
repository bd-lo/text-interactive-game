"""配方目录（M4-015）：内置配方 JSON 的加载、校验与查询入口（第 13 章 §13.2/§13.6）。

配方以 JSON-only 存放于 textgame/data/recipes/，随包用 importlib.resources 读取
（D-M4-6）。启动时完成结构校验（pydantic strict）与目录级校验：重复 recipe_id、
非法 JSON、未知字段、version < 1 一律抛 ValueError（进程启动失败），避免带病
配方进入后续切片。加载只读文件、无其它副作用；排序规则固定（文件名 / recipe_id
升序），保证同一份数据加载结果确定。M4 不校验技能（Recipe.skill_req 仅保留字段）。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from importlib import resources
from pathlib import Path

from pydantic import ValidationError

from textgame.domain.crafting import Recipe

__all__ = ["RecipeCatalog"]

_BUILTIN_RECIPES_PACKAGE = "textgame.data.recipes"


class RecipeCatalog:
    """不可变配方目录：recipe_id -> Recipe，按 recipe_id 升序保存。"""

    def __init__(self, recipes: Mapping[str, Recipe]) -> None:
        """建立目录并校验键与 recipe_id 一致；同名配方按 recipe_id 升序取第一条。"""
        ordered: dict[str, Recipe] = {
            recipe_id: recipes[recipe_id] for recipe_id in sorted(recipes)
        }
        for recipe_id, recipe in ordered.items():
            if recipe_id != recipe.recipe_id:
                raise ValueError(
                    f"recipe key {recipe_id!r} must equal recipe.recipe_id {recipe.recipe_id!r}"
                )
        self._recipes: dict[str, Recipe] = ordered
        names: dict[str, str] = {}
        for recipe in ordered.values():
            names.setdefault(recipe.name, recipe.recipe_id)
        self._name_index: dict[str, str] = names

    @classmethod
    def load_builtin(cls) -> RecipeCatalog:
        """加载随包内置配方（importlib.resources，D-M4-6）；目录为空即启动失败。"""
        package = resources.files(_BUILTIN_RECIPES_PACKAGE)
        entries = sorted(
            (entry for entry in package.iterdir() if entry.name.endswith(".json")),
            key=lambda entry: entry.name,
        )
        if not entries:
            raise ValueError(
                f"no builtin recipe JSON files found in package {_BUILTIN_RECIPES_PACKAGE!r}"
            )
        return cls(cls._parse((entry.name, entry.read_text(encoding="utf-8")) for entry in entries))

    @classmethod
    def load_from_dir(cls, directory: Path) -> RecipeCatalog:
        """加载指定目录下的 *.json 配方（测试/内容包）；空目录即 ValueError。"""
        if not directory.is_dir():
            raise ValueError(f"recipe directory not found: {directory}")
        files = sorted(directory.glob("*.json"), key=lambda path: path.name)
        if not files:
            raise ValueError(f"no recipe JSON files found in {directory}")
        return cls(cls._parse((path.name, path.read_text(encoding="utf-8")) for path in files))

    @staticmethod
    def _parse(items: Iterable[tuple[str, str]]) -> dict[str, Recipe]:
        """解析并校验一组（文件名，JSON 文本）；任何失败都转 ValueError 并带文件名。"""
        recipes: dict[str, Recipe] = {}
        for name, text in items:
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{name}: invalid JSON: {exc}") from exc
            if not isinstance(payload, dict):
                raise ValueError(f"{name}: recipe JSON root must be an object")
            try:
                recipe = Recipe.model_validate(payload)
            except ValidationError as exc:
                raise ValueError(f"{name}: invalid recipe: {exc}") from exc
            if recipe.recipe_id in recipes:
                raise ValueError(f"{name}: duplicate recipe_id {recipe.recipe_id!r}")
            recipes[recipe.recipe_id] = recipe
        return recipes

    def get(self, recipe_id: str) -> Recipe | None:
        """按 recipe_id 取配方；不存在返回 None（只读）。"""
        return self._recipes.get(recipe_id)

    def require(self, recipe_id: str) -> Recipe:
        """按 recipe_id 取配方；不存在抛 KeyError（服务层转 NOT_FOUND）。"""
        try:
            return self._recipes[recipe_id]
        except KeyError as exc:
            raise KeyError(f"unknown recipe_id: {recipe_id!r}") from exc

    def all(self) -> tuple[Recipe, ...]:
        """全部配方，按 recipe_id 升序（确定性顺序）。"""
        return tuple(self._recipes.values())

    def name_index(self) -> dict[str, str]:
        """配方中文名 -> recipe_id 的确定性索引（同名取 recipe_id 字典序最小者）。"""
        return dict(self._name_index)

    @property
    def version_index(self) -> dict[str, int]:
        """recipe_id -> version（事件回放记录配方版本用）。"""
        return {recipe_id: recipe.version for recipe_id, recipe in self._recipes.items()}

    def __len__(self) -> int:
        return len(self._recipes)

    def __contains__(self, recipe_id: object) -> bool:
        return isinstance(recipe_id, str) and recipe_id in self._recipes
