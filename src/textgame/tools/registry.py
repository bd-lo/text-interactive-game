"""ToolRegistry：工具白名单、参数 JSON Schema 校验与提案构造 (第 7 章 7.3/7.4)。

错误码口径 (任务 006 / 契约 §4.7)：
- 工具名不存在 → ``NOT_FOUND`` (第 7 章 §7.5 失败分支清单)；
- ``actor_scope`` 越权 → ``NOT_ALLOWED``；
- 参数不符合工具 JSON Schema → ``INVALID_INPUT``，message 携带精简后的 violations；
- 直接 ``invoke`` 写工具 → ``NOT_ALLOWED`` (写请求必须经 ``ToolGate`` / 提案，M2 由 gate 路由)；
- 实现函数抛异常 → ``INTERNAL``，不把堆栈/内部信息返回给调用方。

``visible_tools`` 实现第 7 章 §7.3 可见性矩阵：``actor_id`` 命中 ``actor_scope``，或
``actor_tags`` 与 ``actor_scope`` 有交集即可见 (标签是授权标签，不是角色 ID)。

``ctx`` 使用 005 交付的 ``services/tool_context.py::ToolContext`` (第 6 章 6.2 六字段)，
工具层只读消费，不持有领域对象引用 (第 7 章 7.1)。
"""

from __future__ import annotations

from typing import Any, cast

# jsonschema 未随包发布 py.typed，且任务约束禁止新增 dev 依赖 (types-jsonschema)，
# 故此处保留单行 import-untyped 抑制；运行期校验行为由单元测试固定 (第 18 章 18.2)。
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from pydantic import BaseModel

from textgame.domain.models import (
    Proposal,
    ResolutionPolicy,
    SideEffect,
    ToolSpec,
)
from textgame.domain.results import ErrorCode, Result
from textgame.services.tool_context import ToolContext
from textgame.tools.decorators import (
    ToolFunction,
    build_params_model,
    first_sentence,
    generate_json_schema,
)

_EMPTY_TAGS: frozenset[str] = frozenset()
_MAX_VIOLATIONS_SHOWN = 5


class ToolRegistry:
    """进程内工具注册表：spec + 实现函数 + 参数模型；注册顺序即可见顺序。"""

    def __init__(self) -> None:
        """创建空注册表；工具由 ``register`` 显式注册 (便于测试与多存档隔离)。"""
        self._specs: dict[str, ToolSpec] = {}
        self._impls: dict[str, ToolFunction] = {}
        self._models: dict[str, type[BaseModel]] = {}

    def register(
        self,
        fn: ToolFunction,
        *,
        actor_scope: set[str] | frozenset[str] | None = None,
        side_effect: SideEffect | None = None,
        resolution_policy: ResolutionPolicy | None = None,
    ) -> ToolSpec:
        """注册工具：优先用 ``@tool`` 附加的 ``__tool_spec__``，也允许显式传参现建。"""
        existing = getattr(fn, "__tool_spec__", None)
        if existing is not None:
            spec = cast(ToolSpec, existing)
        else:
            if actor_scope is None:
                raise ValueError(
                    f"{fn.__name__}: register() needs @tool metadata or explicit actor_scope"
                )
            spec = ToolSpec(
                name=fn.__name__,
                description=first_sentence(fn.__doc__),
                json_schema=generate_json_schema(fn),
                actor_scope=frozenset(actor_scope),
                side_effect=side_effect or SideEffect.READ_ONLY,
                resolution_policy=resolution_policy or ResolutionPolicy.DETERMINISTIC,
            )
        if spec.name in self._specs:
            raise ValueError(f"duplicate tool: {spec.name}")
        self._specs[spec.name] = spec
        self._impls[spec.name] = fn
        self._models[spec.name] = build_params_model(fn)
        return spec

    def get(self, name: str) -> ToolSpec:
        """返回工具规格；不存在时抛 ``KeyError`` (gate 转 ``NOT_FOUND``)。"""
        try:
            return self._specs[name]
        except KeyError as exc:
            raise KeyError(f"unknown tool: {name}") from exc

    def visible_tools(
        self, actor_id: str, actor_tags: frozenset[str] = _EMPTY_TAGS
    ) -> list[ToolSpec]:
        """白名单：Actor 标签命中 ``actor_scope`` 的工具才可见 (第 7 章 §7.3 矩阵)。"""
        return [spec for spec in self._specs.values() if _is_visible(spec, actor_id, actor_tags)]

    def is_visible(self, name: str, ctx: ToolContext) -> bool:
        """工具存在且对 ``ctx`` 可见时返回 ``True`` (gate 的 scope 判定入口)。"""
        spec = self._specs.get(name)
        return spec is not None and _is_visible(spec, ctx.actor_id, ctx.actor_tags)

    def validate_args(self, name: str, args: dict[str, Any]) -> Result:
        """用 Draft 2020-12 Schema 校验参数；失败 ``INVALID_INPUT`` 并携带 violations。"""
        spec = self._specs.get(name)
        if spec is None:
            return Result.fail(ErrorCode.NOT_FOUND, f"unknown tool: {name}")
        violations = _schema_violations(spec, args)
        if violations:
            shown = "; ".join(violations[:_MAX_VIOLATIONS_SHOWN])
            suffix = (
                f"（共 {len(violations)} 处）" if len(violations) > _MAX_VIOLATIONS_SHOWN else ""
            )
            return Result.fail(
                ErrorCode.INVALID_INPUT, f"工具参数不符合 JSON Schema: {shown}{suffix}"
            )
        return Result.success(data={"args": dict(args)})

    def invoke(self, name: str, ctx: ToolContext, args: dict[str, Any]) -> Result:
        """即时执行 READ_ONLY 工具；写工具必须经 gate 路由，不在本方法直接执行。

        参数 Schema 校验先于副作用检查：不合法参数一律 ``INVALID_INPUT`` (任务 006 口径)，
        合法参数的写工具请求再返回 ``NOT_ALLOWED`` (第 7 章 7.4：写请求必须走提案/gate)。
        """
        spec = self._specs.get(name)
        if spec is None:
            return Result.fail(ErrorCode.NOT_FOUND, f"unknown tool: {name}")
        if not _is_visible(spec, ctx.actor_id, ctx.actor_tags):
            return Result.fail(ErrorCode.NOT_ALLOWED, "actor_scope violation")
        validated = self.validate_args(name, args)
        if not validated.ok or not validated.data:
            return validated
        if spec.side_effect is not SideEffect.READ_ONLY:
            return Result.fail(
                ErrorCode.NOT_ALLOWED, "write tools must be dispatched through ToolGate"
            )
        params = validated.data["args"]
        try:
            return self._impls[name](ctx=ctx, **params)
        except Exception:  # 不把堆栈/内部信息返回给 Agent (第 7 章 7.5 执行异常分支)
            return Result.fail(ErrorCode.INTERNAL, "tool execution failed")

    def build_proposal(
        self,
        tool: str,
        args: dict[str, Any],
        *,
        actor_id: str,
        actor_tags: frozenset[str],
        turn_id: int,
        reason: str,
        priority: int,
    ) -> Proposal | None:
        """白名单 + Schema 通过且为写工具时构造 ``Proposal``；否则返回 ``None`` (转澄清)。

        ``tool``/``args`` 由编排层从 ``ParseOutput`` 提取并映射 (第 7 章 7.4)；M2 单角色
        不经 ProposalQueue，gate 直接路由到 ``ActionService``，本方法保留契约供 M3 使用。
        """
        spec = self._specs.get(tool)
        if spec is None:
            return None
        if actor_id not in spec.actor_scope and spec.actor_scope.isdisjoint(actor_tags):
            return None
        if spec.side_effect is SideEffect.READ_ONLY:
            return None
        if spec.resolution_policy is ResolutionPolicy.MANUAL:
            return None
        validated = self.validate_args(tool, args)
        if not validated.ok or not validated.data:
            return None
        validated_args = cast(dict[str, Any], validated.data["args"])
        return Proposal(
            turn_id=turn_id,
            actor_id=actor_id,
            tool=spec.name,
            args=validated_args,
            reason=reason,
            priority=priority,
        )


def _is_visible(spec: ToolSpec, actor_id: str, actor_tags: frozenset[str]) -> bool:
    """可见性判定：actor_id 命中 scope，或标签与 scope 有交集 (第 7 章 §7.3)。"""
    return actor_id in spec.actor_scope or not spec.actor_scope.isdisjoint(actor_tags)


def _schema_violations(spec: ToolSpec, args: dict[str, Any]) -> list[str]:
    """返回全部 Schema 违规的 ``路径: validator`` 摘要；排序保证输出稳定。"""
    validator = Draft202012Validator(spec.json_schema)
    violations = [
        f"{'.'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.validator}"
        for error in validator.iter_errors(dict(args))
    ]
    return sorted(violations)
