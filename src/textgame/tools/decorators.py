"""``@tool`` 装饰器：从函数签名自动生成参数 JSON Schema (第 7 章 7.2)。

不变量 (第 7 章 7.1)：
- Schema 自动生成、禁止手写：Pydantic ``create_model`` + ``model_json_schema(mode="validation")``；
- 注入参数 ``ctx``/``self`` 不进入 JSON Schema；``*args``/``**kwargs`` 与无类型注解参数注册即失败；
- 根 Schema 固定 ``{"type": "object", ..., "additionalProperties": false}``，多一个字段即拒绝；
- 工具元数据复用 ``domain/models.py::ToolSpec`` (六字段)，``side_effect=NETWORK`` 注册即拒绝。

工具函数只接收 ``ctx`` 与 JSON 可表达参数，不持有 ``GameState`` 引用 (第 7 章 7.1)。
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from typing import Any, TypeVar, get_type_hints

from pydantic import BaseModel, ConfigDict, create_model

from textgame.domain.models import ResolutionPolicy, SideEffect, ToolSpec
from textgame.domain.results import Result

ToolFunction = Callable[..., Result]
_F = TypeVar("_F", bound=ToolFunction)

_INJECTED_PARAMS = frozenset({"ctx", "self"})
_DESCRIPTION_MAX = 200


class ToolSchemaError(TypeError):
    """注册期错误：函数签名不可用于生成工具 Schema (不是运行时错误)。"""


def build_params_model(fn: ToolFunction) -> type[BaseModel]:
    """从函数签名构建参数 Pydantic 模型；``ctx``/``self`` 注入参数被排除。"""
    signature = inspect.signature(fn)
    hints = get_type_hints(fn, include_extras=True)
    fields: dict[str, Any] = {}
    for param_name, param in signature.parameters.items():
        if param_name in _INJECTED_PARAMS:
            continue
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            raise ToolSchemaError(
                f"{fn.__name__}: *args/**kwargs are not allowed in tool signatures"
            )
        if param.kind is param.POSITIONAL_ONLY:
            raise ToolSchemaError(
                f"{fn.__name__}: positional-only parameter '{param_name}' is not allowed"
            )
        annotation = hints.get(param_name, param.annotation)
        if annotation is inspect.Parameter.empty:
            raise ToolSchemaError(
                f"{fn.__name__}: parameter '{param_name}' must have a type annotation"
            )
        default = ... if param.default is param.empty else param.default
        fields[param_name] = (annotation, default)
    model_name = "".join(part.title() for part in fn.__name__.split("_")) + "Params"
    return create_model(model_name, __config__=ConfigDict(extra="forbid"), **fields)


def generate_json_schema(fn: ToolFunction) -> dict[str, Any]:
    """生成工具参数 JSON Schema；根必须是 object 且禁止额外字段。"""
    schema: dict[str, Any] = build_params_model(fn).model_json_schema(mode="validation")
    if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
        raise ToolSchemaError(
            f"{fn.__name__}: generated schema must be an object with additionalProperties=false"
        )
    return schema


def first_sentence(docstring: str | None) -> str:
    """取 docstring 首句作为工具描述；缺失即注册失败 (第 7 章 7.2)。"""
    text = (docstring or "").strip()
    if not text:
        raise ToolSchemaError(
            "tool function must have a docstring (first line is the tool description)"
        )
    return re.split(r"[。\n]", text, maxsplit=1)[0].strip()[: _DESCRIPTION_MAX - 1]


def tool(
    *,
    actor_scope: set[str] | frozenset[str],
    side_effect: SideEffect = SideEffect.READ_ONLY,
    resolution_policy: ResolutionPolicy = ResolutionPolicy.DETERMINISTIC,
    description: str | None = None,
) -> Callable[[_F], _F]:
    """注册工具元数据的装饰器；元数据以 ``__tool_spec__`` 挂在函数对象上。"""
    scope = frozenset(actor_scope)

    def decorator(fn: _F) -> _F:
        tool_spec = ToolSpec(
            name=fn.__name__,
            description=description or first_sentence(fn.__doc__),
            json_schema=generate_json_schema(fn),
            actor_scope=scope,
            side_effect=side_effect,
            resolution_policy=resolution_policy,
        )
        setattr(fn, "__tool_spec__", tool_spec)  # noqa: B010 - 元数据挂载点，不是变量
        return fn

    return decorator
