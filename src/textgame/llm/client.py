"""LLM 客户端最小子集 (第 12 章 12.2；M2 只对接 FakeLLM)。

M2 阶段只定义请求/响应契约与 ``LLMClient`` Protocol，供解析 Agent 与
``complete_json_with_repair`` 校验层使用；真实供应商封装只在 ``llm/providers.py``，
本模块不得 import litellm/openai (第 12 章 12.1、M2-006 任务约束)。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from textgame.domain.results import ErrorCode, Result


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
    """一次结构化 LLM 调用请求 (第 12 章 12.2 的最小子集，M5 再补缓存/限流字段)。"""

    model_config = ConfigDict(extra="forbid")

    task: str = Field(min_length=1, max_length=64)
    output_schema: dict[str, Any] | None = Field(
        default=None, description="结构化输出的 JSON Schema；纯文本任务为 None"
    )
    agent_id: str = Field(min_length=1, max_length=32)
    turn_id: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=128)
    model_tier: ModelTier = ModelTier.ECONOMY
    messages: list[ChatMessage] = Field(min_length=1, max_length=16)
    timeout_s: float = Field(
        default=20.0, gt=0, description="整链 deadline，单次尝试不超过剩余时间"
    )
    max_repair: int = Field(
        default=1, ge=0, le=1, description="Schema 修复重试次数；全队固定最多 1 次 (第 12 章 12.6)"
    )


class LLMResponse(BaseModel):
    """``complete_json`` 成功时的业务数据 (供应商原始对象不向外暴露)。"""

    model_config = ConfigDict(extra="forbid")

    content: str
    repaired: bool = False
    usage: dict[str, Any] = Field(default_factory=dict)


class LLMClient(Protocol):
    """结构化补全客户端接口；M2 由 ``tests/fakes.py::FakeLLM`` 实现，M5 接真实供应商。

    成功时 ``data={"content": str, "repaired": bool, "usage": dict}``；
    失败时返回 ``Result.fail(LLM_UNAVAILABLE | SCHEMA_VIOLATION | INVALID_INPUT | INTERNAL)``
    且 ``events=[]`` (LLM 调用不产生领域事件，第 12 章 12.2)。
    """

    def complete_json(self, req: LLMRequest) -> Result:
        """执行一次结构化补全并返回 ``Result``；网络/限流等失败由实现映射错误码。"""
        ...


class DisabledLLMClient:
    """M2 生产默认离线客户端：M2 不接真实 LLM (第 3 章 §3.6 方案 A)，一律失败。

    解析 Agent 收到 ``LLM_UNAVAILABLE`` 后会退回 005 的确定性关键词解析，保证回合可结束、
    零联网、可复现；真实供应商接入在 M5 的 ``llm/providers.py`` 封装后替换本类。
    """

    def complete_json(self, req: LLMRequest) -> Result:
        """始终返回 ``LLM_UNAVAILABLE``，不发起任何网络请求。"""
        return Result.fail(
            ErrorCode.LLM_UNAVAILABLE,
            "M2 默认离线：真实 LLM 在 M5 接入，当前回退确定性解析",
        )
