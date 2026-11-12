"""LLM 客户端契约（第 12 章 12.2）：请求/响应模型、客户端 Protocol 与离线实现。

M5-019 起 ``LLMClient`` 是业务代码唯一允许依赖的 LLM 入口（第 12 章 12.1、结论 1）：
- 同步签名（D-M5-1，2026-11-10 定案）：全链路（FastAPI 同步路由 → 同步编排 → 同步 Agent）
  都运行在 FastAPI 线程池中，M5 不引入 async 重构；异步化复评门 = M6 流式叙事/SSE 设计。
- 真实供应商实现见 ``litellm_client.py``（由 ``factory.build_llm_client`` 构造），
  SDK 只能出现在 ``providers.py``（第 16 章 16.2 分层契约）。
- ``DisabledLLMClient`` 是默认生产实现：零联网、恒 ``LLM_UNAVAILABLE``，各 Agent 退回
  确定性兜底，保证默认装配可复现（第 3 章 §3.6 方案 A、第 12 章 12.1）。
- N2-038（D-N2-4）追加**可选**流式扩展 Protocol :class:`StreamingLLMClient`：不并入
  ``LLMClient``（不破坏既有实现与测试 Fake），调用方用 :func:`supports_streaming` 探测。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from enum import Enum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from textgame.domain.results import ErrorCode, Result

#: 流式增量回调：参数是**供应商原始文本片段**（JSON 片段，未校验；不得直接写状态/展示）。
#: 回调异常由客户端吞掉并记日志（显示层故障不得影响回合提交，D-N2-4/D-N2-5）。
DeltaCallback = Callable[[str], None]


class ModelTier(str, Enum):
    """第 12 章 12.2 模型分级：按任务分级，不按角色分级。"""

    STRONG = "strong"  # 场景判定 / 冲突裁决 / 自由制造生成
    ECONOMY = "economy"  # 意图解析 / 角色对话 / 叙事包装


class ChatMessage(BaseModel):
    """一条 Prompt 消息 (第 12 章 12.2 的 ``ChatMessage`` 最小子集)。"""

    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


class LLMRequest(BaseModel):
    """一次 LLM 调用请求 (第 12 章 12.2)。

    ``output_schema`` 为结构化输出模型类（``None`` = 纯文本任务）。第 12 章附录写作
    ``schema``，但 ``schema`` 是 Pydantic v2 的保留属性名，故字段名固定为
    ``output_schema``（与第 12 章正文口径一致，ADR D-M5-3 记录该偏离）。
    ``cache_ttl_s`` 在 019 只保留字段（>0 才允许进程内缓存），生效归 020。
    ``prompt_version`` 由 Agent 传入（第 12 章 12.5 模板版本可追溯），默认占位。
    """

    model_config = ConfigDict(
        extra="forbid",
        arbitrary_types_allowed=True,
    )

    task: str = Field(min_length=1, max_length=64)
    output_schema: type[BaseModel] | None = None
    agent_id: str = Field(min_length=1, max_length=32)
    turn_id: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=160)
    model_tier: ModelTier = ModelTier.ECONOMY
    messages: list[ChatMessage] = Field(min_length=1, max_length=16)
    timeout_s: float = Field(
        default=20.0, gt=0, description="整链 deadline，单次尝试不超过剩余时间"
    )
    cache_ttl_s: int = Field(default=0, ge=0, description=">0 时允许进程内缓存（020 生效）")
    max_repair: int = Field(
        default=1, ge=0, le=1, description="Schema 修复重试次数；全队固定最多 1 次 (第 12 章 12.6)"
    )
    prompt_version: str = Field(default="unversioned", max_length=64)

    @property
    def output_schema_json(self) -> dict[str, Any] | None:
        """结构化输出模型的 JSON Schema（纯文本任务返回 ``None``）。"""
        if self.output_schema is None:
            return None
        return self.output_schema.model_json_schema()

    def safe_dump(self) -> dict[str, Any]:
        """可序列化的请求摘要（剔除不可 JSON 化的 ``output_schema`` 类对象）。

        日志/幂等哈希只允许使用本方法的结果，禁止直接 ``model_dump()``
        （``output_schema`` 是类对象，不能进日志或哈希）。
        """
        dumped: dict[str, Any] = self.model_dump(mode="json", exclude={"output_schema"})
        schema_json = self.output_schema_json
        if schema_json is not None:
            dumped["output_schema_sha256"] = hashlib.sha256(
                json.dumps(schema_json, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest()
        return dumped


class LLMResponse(BaseModel):
    """``complete_json``/``complete_text`` 成功时的业务数据（供应商原始对象不向外暴露）。"""

    model_config = ConfigDict(extra="forbid")

    content: str
    repaired: bool = False
    usage: dict[str, Any] = Field(default_factory=dict)


class LLMClient(Protocol):
    """LLM 客户端接口（M5 权威签名，同步；D-M5-1）。

    成功时 ``data`` 至少含 ``{"content": str, "usage": dict}``；019 增补 ``call``
    （``records.LLMCallRecord``）。失败时返回 ``Result.fail(LLM_UNAVAILABLE |
    SCHEMA_VIOLATION | INVALID_INPUT | INTERNAL)`` 且 ``events=[]``（LLM 调用不产生
    领域事件，第 12 章 12.2）。

    **可选流式扩展**见 :class:`StreamingLLMClient`（D-N2-4）：它是独立 Protocol，不并入
    本协议——``DisabledLLMClient``/测试 Fake 不实现它也不会破坏既有契约；调用方用
    :func:`supports_streaming` 做 ``getattr`` 探测（``@runtime_checkable`` 不适用）。
    """

    def complete_json(self, req: LLMRequest) -> Result:
        """执行一次结构化补全（结构化校验/修复链在 019 由 ``validation.py`` 承担）。"""
        ...

    def complete_text(self, req: LLMRequest) -> Result:
        """执行一次纯文本补全（``output_schema`` 必须为 None）。"""
        ...


class StreamingLLMClient(Protocol):
    """LLM 客户端**可选**流式扩展（N2-038 / ADR D-N2-4）。

    ``complete_json_stream(req, on_delta)`` 的返回值与 :meth:`LLMClient.complete_json`
    完全同构（成功含 ``content``/``usage``/``call``），区别只是生成过程中把**供应商原始
    文本片段**（JSON 片段，未校验）逐步交给 ``on_delta``；完整文本仍由返回值携带，调用方
    必须走原校验/一次修复链后才能进入状态（未校验文本不得落库）。

    治理口径（``LiteLLMClient`` 实现）：RPM 只按次前置、首 chunk 前可重试、首 chunk 后
    不重试且不换供应商、整链 deadline/空闲超时、末 chunk usage、缓存/幂等命中一次下发、
    ``LLMCallRecord.streamed=True``。
    """

    def complete_json_stream(self, req: LLMRequest, on_delta: DeltaCallback) -> Result:
        """流式结构化补全：每到达一段原始文本调用一次 ``on_delta``，结束时返回完整结果。"""
        ...


def supports_streaming(client: object) -> bool:
    """客户端是否实现可选流式扩展（``getattr`` 探测，D-N2-4；默认离线客户端不实现）。"""
    return callable(getattr(client, "complete_json_stream", None))


class DisabledLLMClient:
    """生产默认离线客户端：不联网、恒 ``LLM_UNAVAILABLE``（第 3 章 §3.6 方案 A）。

    解析/制造等 Agent 收到 ``LLM_UNAVAILABLE`` 后退回确定性路径，保证默认装配
    零联网、可复现；真实供应商由 ``factory.build_llm_client(settings)`` 在
    ``llm_enabled=True`` 时构造（D-M5-6，M5 出口附加项接线）。
    """

    def complete_json(self, req: LLMRequest) -> Result:
        """始终返回 ``LLM_UNAVAILABLE``，不发起任何网络请求。"""
        return Result.fail(
            ErrorCode.LLM_UNAVAILABLE,
            "LLM 默认离线（TEXTGAME_LLM_ENABLED=false）：已回退确定性路径",
        )

    def complete_text(self, req: LLMRequest) -> Result:
        """始终返回 ``LLM_UNAVAILABLE``，不发起任何网络请求。"""
        return self.complete_json(req)


def compose_idempotency_key(req: LLMRequest) -> str:
    """派生「进程内幂等存储键」（第 12 章 12.2 调用方组合规则）。

    调用方传入的 ``req.idempotency_key`` 通常是**回合级**幂等键（解析与制造同回合
    相同），直接用作 LLM 幂等键会让不同 Agent 的结果互相串用；因此客户端按
    ``(turn_id, agent_id, task, model_tier, prompt_version, messages)`` 组合后哈希，
    作为实际存储键（防御式组合，不改变对外 ``Result`` 语义）。
    """
    payload = {
        "turn_id": req.turn_id,
        "agent_id": req.agent_id,
        "task": req.task,
        "model_tier": req.model_tier.value,
        "prompt_version": req.prompt_version,
        "messages": [message.model_dump() for message in req.messages],
        "idempotency_key": req.idempotency_key,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    return f"llm:{digest[:40]}"
