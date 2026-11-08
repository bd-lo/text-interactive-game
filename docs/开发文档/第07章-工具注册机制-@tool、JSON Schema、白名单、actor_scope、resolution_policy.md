## 第 7 章 工具注册机制：@tool、JSON Schema、白名单、actor_scope、resolution_policy

### 本章检索关键词
- `toolregistry python json schema permission tags`、`agent-fn-registry side effects schema`、`modelcontextprotocol python-sdk tools`、`fastmcp MCP python server`、`pydantic create_model json schema function signature`、`pydantic-ai toolset requires-python`、`pypi mcp fastmcp requires-python`、`agent-fn-registry pypi 404`

### 实际打开确认的仓库列表
- `Oaklight/ToolRegistry`（https://github.com/Oaklight/ToolRegistry）— 打开确认：许可证 MIT、最近提交 2026-11-08、star 约 67、Python 3.10 兼容（PyPI 0.20.1 `requires-python>=3.10`）、确认要点（`README_en.md`：protocol-agnostic、多供应商 Schema 生成、tag 权限 `READ_ONLY`/`DESTRUCTIVE`/`NETWORK`、MCP/OpenAPI 适配、核心依赖 pydantic + llm-rosetta；源码 `src/toolregistry/permissions/policy.py` 与 `builtin_rules.py`：`PermissionRule(match(tool, params), result)`、first-match-wins、默认 `fallback=DENY`，内置 `allow_readonly`/`ask_destructive`/`ask_network`）。
- `MukundaKatta/agent-fn-registry`（https://github.com/MukundaKatta/agent-fn-registry）— 打开确认：许可证 MIT、最近提交 2026-06-13、star 0、Python 3.10 兼容（仓库 `pyproject.toml` `requires-python>=3.10`）、**PyPI 上无此包（`pypi.org/pypi/agent-fn-registry` 返回 404，README 里的 `pip install agent-fn-registry` 当前不可用）**、确认要点（README：`Registry` + `@reg.tool(schema=..., side_effects=[...], defaults=...)`、`dispatch`/`anthropic_tools`/`openai_functions`，并明确声明“不做参数校验”）。
- `modelcontextprotocol/python-sdk`（https://github.com/modelcontextprotocol/python-sdk）— 打开确认：许可证 MIT、最近提交 2026-11（2026-11-05）、star 约 24.8k、Python 3.10 兼容以发布包为准（仓库 main 分支 `pyproject.toml` 已要求 ≥3.11，PyPI `mcp` 2.3.0 `requires-python>=3.10`）、确认要点（README：`MCPServer` + `@mcp.tool()`、stdio/Streamable HTTP/SSE 三种传输）。
- `jlowin/fastmcp`（https://github.com/jlowin/fastmcp）— 打开确认：许可证 Apache-2.0、最近提交 2026-11-08、star 约 28.3k、Python 3.10 兼容（PyPI 4.1.6 `requires-python>=3.10`）、确认要点（README：`FastMCP` 服务器/客户端/Apps 三层，`@mcp.tool` 自动生成 schema 与校验；README 自述 FastMCP 1.0 已并入官方 SDK）。
- `pydantic/pydantic`（https://github.com/pydantic/pydantic）— 打开确认：许可证 MIT、最近提交 2026-11、star 约 29.4k、Python 3.10 兼容（PyPI 2.14.3 要求 ≥3.10；仓库 main 分支开发版已要求 ≥3.11）、确认要点（`model_json_schema()` 与 `create_model` 可用于从函数签名生成工具参数 Schema，第 5 章已确认）。
- `pydantic/pydantic-ai`（https://github.com/pydantic/pydantic-ai）— 打开确认：许可证 MIT、最近提交 2026-11-08、star 约 21.1k、Python 3.10 兼容性：不兼容（PyPI 2.66.0 `requires-python>=3.11`；仓库 main 分支同样 ≥3.11）、确认要点（README：类型化 Agent 运行时 + capabilities/toolsets）。

### 7.1 设计目标与五条不变量

工具层是智能体与权威状态之间唯一的合法通道。落地时守住五条不变量，任何一条被破坏都按架构违规处理：

1. **唯一入口**：Agent 只能看到工具白名单（Tool Whitelist）内的工具 Schema、只能调用这些工具；工具函数内部只调服务层，不 import ORM、不碰 `GameState`。
2. **不可见即不存在**：不在 `actor_scope` 内的工具不会出现在该 Actor 的 Prompt 里；即使模型硬编造调用，也按 `actor_scope` 越权返回 `NOT_ALLOWED`（工具名真不存在才返回 `NOT_FOUND`，两者不混用）。
3. **Schema 自动生成、禁止手写**：从函数签名 + 类型注解生成，签名改了 Schema 自动跟着改；手写 Schema 与实现漂移是工具层第一号 bug 来源。
4. **副作用标签（Side-effect Tag）驱动策略**：`side_effect` 决定该调用是直放、进 Resolver 还是拒绝；`resolution_policy` 决定由确定性规则还是裁判 Agent 裁决。
5. **工具不持有领域对象引用**：工具签名里只有 `ctx` 与 JSON 可表达的参数，没有任何 `Character`/`GameState` 形参。

### 7.2 `@tool` 装饰器与 JSON Schema 生成

```python
from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from typing import Any, get_type_hints

from pydantic import BaseModel, ConfigDict, ValidationError, create_model

_HIDDEN = frozenset({"ctx", "self"})          # 注入参数，不进 JSON Schema


class ToolSchemaError(TypeError):
    """注册期错误：签名不可用于生成 Schema（不是运行时错误）。"""


def build_params_model(fn: Callable[..., Any]) -> type[BaseModel]:
    """从函数签名构建参数模型；ctx/self 注入参数被排除。"""
    sig = inspect.signature(fn)
    hints = get_type_hints(fn, include_extras=True)
    fields: dict[str, tuple[Any, Any]] = {}
    for pname, param in sig.parameters.items():
        if pname in _HIDDEN:
            continue
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            raise ToolSchemaError(f"{fn.__name__}: *args/**kwargs are not allowed in tool signatures")
        annotation = hints.get(pname, param.annotation)
        if annotation is inspect.Parameter.empty:
            raise ToolSchemaError(f"{fn.__name__}: parameter '{pname}' must have a type annotation")
        default = ... if param.default is param.empty else param.default
        fields[pname] = (annotation, default)
    return create_model(
        f"{''.join(part.title() for part in fn.__name__.split('_'))}Params",
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )


def generate_json_schema(fn: Callable[..., Any]) -> dict[str, Any]:
    return build_params_model(fn).model_json_schema(mode="validation")


def _first_sentence(text: str) -> str:
    text = (text or "").strip()
    if not text:
        raise ToolSchemaError("tool function must have a docstring (first line is the tool description)")
    return re.split(r"[。\n]", text, maxsplit=1)[0][:200]


def tool(*, actor_scope: set[str] | frozenset[str],
         side_effect: SideEffect = SideEffect.READ_ONLY,
         resolution_policy: ResolutionPolicy = ResolutionPolicy.DETERMINISTIC,
         description: str | None = None) -> Callable[[Callable[..., Result]], Callable[..., Result]]:
    scope = frozenset(actor_scope)

    def decorator(fn: Callable[..., Result]) -> Callable[..., Result]:
        spec = ToolSpec(
            name=fn.__name__,
            description=description or _first_sentence(fn.__doc__ or ""),
            json_schema=generate_json_schema(fn),
            actor_scope=scope,
            side_effect=side_effect,
            resolution_policy=resolution_policy,
        )
        fn.__tool_spec__ = spec          # type: ignore[attr-defined]
        return fn

    return decorator
```

示例工具（工厂函数内定义，闭包持有服务对象，避免工具层拿全局单例）：

```python
def build_game_tools(services: ServiceContainer) -> list[Callable[..., Result]]:
    @tool(actor_scope={"player"},
          side_effect=SideEffect.READ_ONLY)
    def get_room_view(ctx: ToolContext, room_id: RoomId | None = None) -> Result:
        """查看当前所在房间的可见信息。"""
        return services.query.get_room_view(ctx, room_id)

    @tool(actor_scope={"player"},
          side_effect=SideEffect.DESTRUCTIVE,
          resolution_policy=ResolutionPolicy.DETERMINISTIC)
    def move_to(ctx: ToolContext, direction: Direction, *, force: bool = False) -> Result:
        """向一个方向移动。"""
        return services.action.move_to(ctx, direction, force=force,
                                       idempotency_key=ctx.idempotency_key)

    return [get_room_view, move_to]
```

`move_to` 生成的 `json_schema`（即注入给解析 Agent 的 `visible_tools` 内容之一）：

```json
{
  "additionalProperties": false,
  "properties": {
    "direction": {"enum": ["north", "south", "east", "west", "up", "down", "in", "out"], "title": "Direction", "type": "string"},
    "force": {"default": false, "title": "Force", "type": "boolean"}
  },
  "required": ["direction"],
  "title": "MoveToParams",
  "type": "object"
}
```

### 7.3 白名单与 actor_scope

`actor_scope` 的元素是**授权标签**而不是单个角色 ID：`"player"`、`"npc"`、`"npc:blacksmith"`、`"system"`。这样 200 个 NPC 共享标签，只有需要差异化的 NPC（例如铁匠能交易）才加细粒度标签。`ToolContext.actor_tags` 由 `ActorDirectory` 在回合开始时解析（第 8 章的角色 Agent 只拿标签，不拿全局角色表）。`ToolContext`（第 6 章 6.2）字段固定为 `actor_id` / `actor_tags` / `turn_id` / `save_seed` / `idempotency_key` / `caller`，工具层只读不改写。

```python
class ToolRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._impls: dict[str, Callable[..., Result]] = {}
        self._models: dict[str, type[BaseModel]] = {}

    def register(self, fn: Callable[..., Result], *, actor_scope: set[str] | None = None,
                 side_effect: SideEffect | None = None,
                 resolution_policy: ResolutionPolicy | None = None) -> ToolSpec:
        """注册工具：优先用 @tool 附带的 __tool_spec__，也允许显式传参现建（便于单测）。"""
        spec: ToolSpec | None = getattr(fn, "__tool_spec__", None)
        if spec is None:
            if actor_scope is None:
                raise ValueError(f"{fn.__name__}: register() needs @tool metadata or explicit actor_scope")
            spec = ToolSpec(
                name=fn.__name__,
                description=_first_sentence(fn.__doc__ or ""),
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
        try:
            return self._specs[name]
        except KeyError as exc:
            raise KeyError(f"unknown tool: {name}") from exc

    def visible_tools(self, actor_id: str, actor_tags: frozenset[str] = frozenset()) -> list[ToolSpec]:
        """白名单：Actor 标签命中 actor_scope 才可见；空 scope 只允许编排层直调。"""
        return [spec for spec in self._specs.values()
                if actor_id in spec.actor_scope or not spec.actor_scope.isdisjoint(actor_tags)]
```

可见性矩阵（M2 单场景 MVP 的初始值，新增工具必须同步第 22 章进度文档）：

| 工具 | side_effect | resolution_policy | player | npc | npc:blacksmith | system |
| --- | --- | --- | --- | --- | --- | --- |
| `get_room_view` | READ_ONLY | deterministic | ✅ | ✅ | ✅ | ✅ |
| `get_inventory` | READ_ONLY | deterministic | ✅ | ✅ | ✅ | ✅ |
| `move_to` | DESTRUCTIVE | deterministic | ✅ | ✅ | ✅ | ✅ |
| `take_item` | DESTRUCTIVE | deterministic | ✅ | ❌ | ❌ | ✅ |
| `attack_target` | DESTRUCTIVE | deterministic | ✅ | ✅ | ❌ | ✅ |
| `transfer_item` | DESTRUCTIVE | agent_assisted | ❌ | ❌ | ✅ | ✅ |
| `craft_item` | DESTRUCTIVE | deterministic | ✅ | ❌ | ✅ | ✅ |
| `talk_to` | DESTRUCTIVE | deterministic | ✅ | ❌ | ❌ | ✅ |

### 7.4 side_effect 与 resolution_policy 的路由

| `side_effect` | `resolution_policy` | 行为 | 失败码 |
| --- | --- | --- | --- |
| READ_ONLY | deterministic | 工具层直接执行服务只读方法，返回 `Result`；零事件 | `NOT_FOUND`/`INVALID_INPUT` |
| DESTRUCTIVE | deterministic | 不直接执行；把已校验参数包成 `Proposal` 入 `ProposalQueue`，由 `Resolver` 按确定性规则裁决，`GameEngine` 提交 | `NOT_ALLOWED`/`CONFLICT`/`OUT_OF_RANGE` |
| DESTRUCTIVE | agent_assisted | 同上，但 `Resolver` 允许 `JudgeAgent` 覆盖（必须带 `reason` 且 `confidence>=0.7`，否则保守裁决） | 同上 + 裁判失败 → 确定性兜底（Deterministic Fallback） |
| DESTRUCTIVE | manual | 进入人工/作者审核队列（MVP 不启用）；`Resolver` 返回 `deferred` | `NOT_ALLOWED`（未授权）/ `deferred` |
| NETWORK | 任意 | 注册期直接拒绝（`ToolSpec` 不变量），核心工具层没有网络工具 | 注册失败 |

`agent_assisted` 的复核与兜底接口由 `RuleEngine`（`domain/rules/engine.py`）定稿两个方法名，第 11 章 §11.2 与第 21 章 §21.2.2 直接引用，本节只引用、不新增模型：

- `RuleEngine.verify_agent_override(ctx, proposal, judge) -> Outcome`：复核 `JudgeAgent` 的覆盖建议——`confidence >= 0.7` 且 `reason` 非空才通过；不通过（低置信/无理由/证据无效）返回否定 `Outcome`，由 `Resolver` 落为 `rejected`。
- `RuleEngine.deterministic_fallback(ctx, proposal, snapshot) -> Outcome`：裁判不可用、裁判证据缺失或覆盖被拒时的确定性兜底——按 `Ruleset` 与快照给出保守 `Outcome`（不产生有利效果）；兜底判定不成立同样返回否定 `Outcome`（`Resolver` 落为 `rejected`）。

`Outcome` 与 `Result` 同构（`ok` / `data["allowed"]` / `message`），定义随第 11 章裁决链给出；两条接口均为纯函数，不调模型、不写库、不产生事件，失败时不改变状态。

签名定稿（第 21 章 §21.2.3 收录同一组接口）：`ToolRegistry.invoke(name: str, ctx: ToolContext, args: dict) -> Result`；`ToolRegistry.build_proposal(tool: str, args: dict, *, actor_id, actor_tags, turn_id, reason, priority) -> Proposal | None`；`ToolContext` 六字段见第 6 章 6.2。

```python
class ToolRegistry:
    # ... 续 7.3

    def invoke(self, name: str, ctx: ToolContext, args: dict[str, Any]) -> Result:
        """仅用于 READ_ONLY 工具的即时执行；写请求走 build_proposal（签名已定稿，见第 21 章 §21.2.3）。

        错误码口径：工具名不存在 → NOT_FOUND；actor_scope 越权 → NOT_ALLOWED；
        参数不符合 JSON Schema → INVALID_INPUT；执行异常 → INTERNAL。
        """
        spec = self._specs.get(name)
        if spec is None:
            return Result.fail(ErrorCode.NOT_FOUND, f"unknown tool: {name}")
        if not self._visible(spec, ctx):
            return Result.fail(ErrorCode.NOT_ALLOWED, "actor_scope violation")
        if spec.side_effect is not SideEffect.READ_ONLY:
            return Result.fail(ErrorCode.NOT_ALLOWED,
                               "write tools must go through Proposal → Resolver")
        try:
            params = self._validate_args(spec, args)
        except ValidationError as exc:
            return Result.fail(ErrorCode.INVALID_INPUT, self._brief(exc))
        try:
            return self._impls[name](ctx=ctx, **params)
        except Exception:                                    # 不把堆栈/内部信息返回给 Agent
            return Result.fail(ErrorCode.INTERNAL, "tool execution failed")

    def build_proposal(self, tool: str, args: dict[str, Any], *, actor_id: str,
                       actor_tags: frozenset[str], turn_id: int,
                       reason: str, priority: int) -> Proposal | None:
        """白名单 + 参数校验通过则产提案；工具不可见返回 None（编排层转澄清）。

        `priority` 由调用方按第 11 章传入：玩家动作 100、NPC 动作 50。
        `tool`/`args` 由编排层从第 8 章的 ParseOutput 中提取并映射（intent → 工具参数
        的映射在 tools/args_adapter.py，不让 LLM 直接决定工具参数结构）。
        """
        spec = self._specs.get(tool)
        if spec is None:
            return None
        if actor_id not in spec.actor_scope and spec.actor_scope.isdisjoint(actor_tags):
            return None
        if spec.side_effect is SideEffect.READ_ONLY:
            return None                                      # 读工具不值得走提案
        if spec.resolution_policy is ResolutionPolicy.MANUAL:
            return None                                      # MVP 不启用
        try:
            validated = self._validate_args(spec, args)
        except ValidationError:
            return None
        return Proposal(turn_id=turn_id, actor_id=actor_id, tool=spec.name, args=validated,
                        reason=reason, priority=priority)

    @staticmethod
    def _visible(spec: ToolSpec, ctx: ToolContext) -> bool:
        return ctx.actor_id in spec.actor_scope or not spec.actor_scope.isdisjoint(ctx.actor_tags)

    def _validate_args(self, spec: ToolSpec, args: dict[str, Any]) -> dict[str, Any]:
        model = self._models[spec.name]
        return model.model_validate(dict(args)).model_dump(exclude_none=True)

    @staticmethod
    def _brief(exc: ValidationError) -> str:
        """只回传字段路径与错误类型，不回传完整输入（防把内部结构写进 Prompt）。"""
        first = exc.errors()[0]
        return f"{'.'.join(str(p) for p in first['loc'])}: {first['type']}"
```

### 7.5 失败分支清单

| 分支 | 触发条件 | 返回/处理 | 是否产生事件 |
| --- | --- | --- | --- |
| LLM 原始文本无法解析 / 未过输出 Schema | 第 8 章 `ParseOutput` 等契约校验失败 | `SCHEMA_VIOLATION`（第 12 章修复重试一次；仍失败转澄清，零状态变更） | 否 |
| 工具名不存在 | 模型编造工具名（注册表无此工具） | `NOT_FOUND` | 否 |
| `actor_scope` 越权 | 工具存在，但该 Actor 标签不在 `actor_scope` 内 | `NOT_ALLOWED` | 否 |
| 越权调用写工具 | READ_ONLY 之外的直接 `invoke`（写工具必须走提案） | `NOT_ALLOWED` | 否 |
| 参数不符合 JSON Schema | `create_model` 校验失败（缺必填、类型错、`extra="forbid"` 命中多余键） | `INVALID_INPUT` + 精简后的字段错误；编排层决定澄清还是让模型修复一次 | 否 |
| `resolution_policy=manual` | MVP 未启用 | `NOT_ALLOWED`（或 Resolver 返 `deferred`） | 否 |
| Resolver 拒绝 | 前置条件不成立、冲突、裁判低置信度 | `Resolution.verdict ∈ {rejected, conflict, deferred}` | 否 |
| 幂等键重复 | 同键已提交过 | 返回首次 `Result`（从幂等表），不重复执行 | 否（不重复写） |
| 服务层业务失败 | 出口不存在、背包满、目标已死 | 服务层错误码原样透传 | 否 |
| 执行异常 | 未捕获 bug、DB 断连 | `INTERNAL`，日志留 trace id，不向模型暴露细节 | 否 |

### 7.6 候选轮子与复用判断

| 仓库 | 定位 | 可复用部分 | 许可证 | 活跃度 | 建议 |
| --- | --- | --- | --- | --- | --- |
| Oaklight/ToolRegistry | 协议无关工具注册 + 权限标签 | `ToolTag`/`ToolMetadata`/`PermissionRule`（first-match-wins、默认 DENY、`ask_destructive`）、多供应商 Schema 生成 | MIT | 2026-11-08 有提交，star 约 67；0.20.1 要求 ≥3.10 | 参考设计 |
| MukundaKatta/agent-fn-registry | 零依赖轻量注册表 | `Registry.dispatch`（默认值合并）、`side_effects_of`/`with_side_effect` 的 API 形状 | MIT | 2026-06 有提交，star 0；PyPI 无包（404） | 参考设计 |
| modelcontextprotocol/python-sdk | MCP 官方 Python SDK | `MCPServer` + `@mcp.tool()`、stdio/Streamable HTTP/SSE 传输 | MIT | 2026-11 有提交，star 约 24.8k；PyPI `mcp` 2.3.0 要求 ≥3.10 | 协议对接 |
| jlowin/fastmcp | MCP 服务器/客户端框架 | `FastMCP` 快速起 server、客户端连接 | Apache-2.0 | 2026-11-08 有提交，star 约 28.3k；4.1.6 要求 ≥3.10 | 放弃 |
| pydantic/pydantic | Schema 生成内核 | `create_model` + `model_json_schema(mode="validation")` | MIT | 2026-11 有提交，star 约 29.4k；2.14.3 要求 ≥3.10 | pip 依赖 |
| pydantic/pydantic-ai | 类型化 Agent 框架（含 toolset） | 仅借鉴“工具注册 + 重试”的设计；不引入 | MIT | 2026-11-08 有提交，star 约 21.1k；2.66.0 要求 ≥3.11 | 放弃 |

**复用判断与拼接边界（一）：ToolRegistry 定为“参考设计”，不 pip 依赖**。它的能力最接近本项目需求（tag 权限、`READ_ONLY`/`DESTRUCTIVE`/`NETWORK`、first-match-wins 的 `PermissionRule`、pydantic 参数模型），但我们需要的三样东西它给不了：① **可见性语义**——它的权限是“调用时 allow/deny/ask”，`visible_tools` 必须按 Actor 标签过滤后注入 Prompt，这属于 `tools/` 内逻辑；② **注入参数**——工具函数要带 `ctx: ToolContext`，而它的参数模型是从完整签名生成的，`ctx` 会被误当模型参数暴露；③ **依赖面**——核心依赖 `pydantic + llm-rosetta`，第 4 章已锁定 pydantic 版本，再引入一个会漂移的 Schema 转换层不划算。故本章按“自建约 300 行 registry”执行，把它当作权限模型的参考实现；若 M3 后需要 MCP/OpenAPI 暴露，再评估 `pip install toolregistry` 只取 server 适配，不替换项目 `@tool` 注册表。

**复用判断与拼接边界（二）：agent-fn-registry 只借鉴 API 形状**。它的设计（schema + side_effects + defaults 三件套挂在同一个注册表上）与第 7.1 节的不变量高度一致，`tools/registry.py` 的 `ToolSpec`/`visible_tools` 命名可从它借；但它明确声明“不做参数校验”，且 PyPI 上不存在该包（README 的安装命令不可用）、star 为 0，作为依赖会把维护风险转嫁给自己。拷贝代码也不做：本项目的 `ToolSpec` 已由第 5 章定稿（六字段 Pydantic 模型），照抄反而要改。

**复用判断与拼接边界（三）：MCP 只做协议对接，不进核心回合**。智能体调用游戏工具走进程内白名单（可审计、可注入 ctx、零序列化开销），不经过 MCP；MCP 的价值在两侧：把 `READ_ONLY` 工具暴露给外部调试客户端（M3 后的开发工具），或接入外部只读数据源（例如把某张地图服务当作 `get_region_map` 的实现）。协议对接的硬边界：MCP 工具一律注册为 `READ_ONLY`，返回值必须过一次 Pydantic 校验并视为不可信输入；不允许 MCP 工具直接产生写事件（要写状态必须映射到本项目服务层并经 Resolver）。FastMCP 与官方 SDK 功能重叠（官方 SDK 已内置 `MCPServer`），二选一即可，这里选官方 SDK；`pydantic-ai` 要求 Python ≥3.11 且自带 Agent 运行时，与本项目自建编排冲突，排除。

### 本章结论

1. `@tool` 机制定稿：从类型注解自动生成参数 Schema（`create_model` + `model_json_schema(mode="validation")`），`ctx`/`self` 排除，禁止手写 Schema；描述取 docstring 首句，缺失即注册失败。
2. 白名单 = `actor_scope`（标签集合）过滤出的可见工具集；工具名不存在 → `NOT_FOUND`，`actor_scope` 越权 → `NOT_ALLOWED`，工具参数不符合 JSON Schema → `INVALID_INPUT`，LLM 原始文本未过 Schema → `SCHEMA_VIOLATION`（与第 6 章 6.2、第 9 章口径一致）；`ToolSpec` 严格六字段，`side_effect` 只允许 `READ_ONLY`/`DESTRUCTIVE`/`NETWORK`（NETWORK 本期注册即拒绝）。
3. 路由规则：READ_ONLY 工具直接 `invoke`；DESTRUCTIVE 工具一律转 `Proposal` 进 `ProposalQueue`，由 `Resolver` 按 `resolution_policy` 裁决，`GameEngine` 唯一提交；`manual` 策略 MVP 不启用。
4. 失败分支全部“零状态变更”：越权、Schema 不符、Resolver 拒绝、幂等命中、内部异常各有明确错误码，工具层不向模型泄露内部堆栈或工具存在性。
5. 依赖结论：只新增 pydantic（已依赖）；ToolRegistry 与 agent-fn-registry 均为参考设计（前者缺可见性与注入参数语义、后者无 PyPI 包且不校验参数）；MCP 用官方 SDK 做协议对接、FastMCP 与 pydantic-ai（≥3.11）放弃。
6. 签名定稿（第 21 章 §21.2.3 收录同一组接口）：`ToolRegistry.invoke(name: str, ctx: ToolContext, args: dict) -> Result`；`ToolRegistry.build_proposal(tool: str, args: dict, *, actor_id, actor_tags, turn_id, reason, priority) -> Proposal | None`；`ToolContext` 字段固定为 `actor_id`/`actor_tags`/`turn_id`/`save_seed`/`idempotency_key`/`caller`（第 6 章 6.2）。
