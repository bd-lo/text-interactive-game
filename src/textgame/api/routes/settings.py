"""LLM 运行期设置端点（玩家在网页里输入/测试/清除自己的 DeepSeek API Key）。

设计契约：``llm/runtime.py`` 顶部优先级说明 + ``llm/credentials.py``。

- 端点只操作**本机**凭据文件与进程内运行期客户端：保存后当前 Agent 立即切换（热更新），
  下次启动从文件恢复；不写数据库、不进 git、日志不含明文；
- 优先级由运行期客户端决定：``.env`` 开发 key > 本机保存 key > 离线；显式
  ``TEXTGAME_LLM_ENABLED=false`` 时永远离线（保存/测试端点仍可用，只报告状态）；
- ``GET /settings/llm`` 只返回打码 key 与来源，绝不回传明文；
- ``PUT`` 需要完整 ``api_key``（前端不持久化、不回显）；``run_test=true`` 时保存后立即
  发一次最小请求（可选，失败不影响已保存的配置）；
- 写端点不用 ``Idempotency-Key``：按值幂等（同值重复保存结果一致），与游戏回合/导入的
  幂等语义无关（避免把「保存设置」误当成会产生领域副作用的操作）。
"""

from __future__ import annotations

import threading
from typing import Any, Literal, cast

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from textgame.api.errors import ApiError
from textgame.domain.results import ErrorCode
from textgame.llm.credentials import CredentialStoreError
from textgame.llm.runtime import RuntimeLLMClient
from textgame.persistence.free_craft_settings import FreeCraftSettingsStore
from textgame.services.free_craft import FreeCraftSwitch

router = APIRouter(prefix="/api/v1", tags=["settings"])

# 单进程内串行化“落盘 + 切换共享开关”，避免两个并发 PUT 让内存与文件顺序相反。
_FREE_CRAFT_WRITE_LOCK = threading.Lock()

__all__ = ["router"]


class LlmStatusResponse(BaseModel):
    """当前 LLM 配置状态（不含明文密钥；键与前端 TS 接口一一对应）。"""

    model_config = ConfigDict(extra="forbid")

    configured: bool = Field(description="是否有可用配置（env key 或本机保存 key）")
    enabled: bool = Field(description="当前是否真的启用 LLM（显式关闭时为 false）")
    source: str = Field(description="配置来源：env/user/none")
    provider: str = Field(description="供应商 id（当前支持 deepseek）")
    model: str = Field(description="模型 id")
    api_base: str = Field(description="API 地址")
    masked_key: str | None = Field(default=None, description="打码后的 key（如 sk-****abcd）")
    has_saved_user_key: bool = Field(description="本机是否保存过玩家自己的 key")
    detail: str = Field(description="给玩家看的状态说明（中文）")


class LlmTestResponse(BaseModel):
    """连接测试结果（只验证网络/鉴权/协议，不写游戏状态）。"""

    model_config = ConfigDict(extra="forbid")

    ok: bool = Field(description="是否连通")
    latency_ms: int | None = Field(default=None, description="往返耗时（毫秒；未启用时为 null）")
    provider: str = Field(description="测试所用供应商")
    model: str = Field(description="测试所用模型")
    error: str | None = Field(default=None, description="失败原因（人话，不含密钥）")


class LlmSaveRequestBody(BaseModel):
    """保存本机 key 的请求；前端密码框提交，不回显。"""

    model_config = ConfigDict(extra="forbid")

    api_key: str = Field(min_length=1, max_length=512, description="DeepSeek API Key")
    provider: str = Field(default="deepseek", min_length=1, max_length=32)
    model: str = Field(default="deepseek-flash", min_length=1, max_length=128)
    api_base: str = Field(default="https://api.deepseek.com", min_length=1, max_length=512)
    run_test: bool = Field(default=False, description="保存后是否立即测试连接")


class LlmSaveResponse(BaseModel):
    """保存结果：状态快照 + 可选连接测试结果。"""

    model_config = ConfigDict(extra="forbid")

    status: LlmStatusResponse
    test: LlmTestResponse | None = None


class LlmDeleteResponse(BaseModel):
    """清除本机 key 后的状态快照。"""

    model_config = ConfigDict(extra="forbid")

    status: LlmStatusResponse


class FreeCraftStatusResponse(BaseModel):
    """自由制造开关状态（与前端 TS 接口一一对应）。"""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(description="当前是否允许自由制造（解析与执行实时生效）")
    available: bool = Field(description="当前是否具备开启条件（大模型已启用）")
    llm_configured: bool = Field(description="是否配置了大模型凭据")
    llm_enabled: bool = Field(description="大模型当前是否真正启用")
    risk_accepted: bool = Field(description="是否已确认 Resolver 内调 LLM 的风险")
    source: Literal["user", "env", "default"] = Field(
        description="当前状态来源：网页设置 / env 默认 / 内置默认"
    )
    detail: str = Field(description="给玩家看的状态说明（中文）")


class FreeCraftSaveRequestBody(BaseModel):
    """切换自由制造开关；开启时必须显式确认 Resolver LLM 风险。"""

    model_config = ConfigDict(extra="forbid")

    enabled: bool
    accept_resolver_llm_risk: bool = Field(
        default=False, description="开启时是否确认「裁决链路内调 LLM、可能重复计费」"
    )


def _runtime(request: Request) -> RuntimeLLMClient:
    """取组合根注入的运行期客户端；未装配视为装配错误（500 信封）。"""
    runtime = getattr(request.app.state, "llm_runtime", None)
    if runtime is None:
        raise ApiError(ErrorCode.INTERNAL, "服务未完成 LLM 设置装配")
    return cast(RuntimeLLMClient, runtime)


def _status_payload(runtime: RuntimeLLMClient) -> dict[str, Any]:
    """状态模型所需的 dict（保持键集合稳定，便于前端严格校验）。"""
    return runtime.status().as_dict()


def _free_craft_state(
    request: Request,
) -> tuple[FreeCraftSwitch, FreeCraftSettingsStore, Literal["user", "env", "default"]]:
    """取组合根注入的开关/持久化 store/来源；未装配视为装配错误（500 信封）。"""
    switch = getattr(request.app.state, "free_craft_switch", None)
    store = getattr(request.app.state, "free_craft_store", None)
    source = getattr(request.app.state, "free_craft_source", "default")
    if switch is None or store is None:
        raise ApiError(ErrorCode.INTERNAL, "服务未完成自由制造设置装配")
    return (
        cast(FreeCraftSwitch, switch),
        cast(FreeCraftSettingsStore, store),
        cast(Literal["user", "env", "default"], source),
    )


def _free_craft_status(
    runtime: RuntimeLLMClient,
    switch: FreeCraftSwitch,
    source: Literal["user", "env", "default"],
) -> FreeCraftStatusResponse:
    """由 LLM 运行期状态 + 开关生成稳定响应（不读写文件）。"""
    llm = runtime.status()
    enabled = switch.enabled
    if enabled and llm.enabled:
        detail = "自由制造已开启：未命中配方时会调用大模型生成草稿，可能产生额外费用。"
    elif enabled:
        detail = "自由制造已开启，但大模型当前不可用：自由制造会返回 LLM_UNAVAILABLE。"
    elif not llm.enabled:
        detail = (
            "自由制造未开启；大模型已配置但被显式关闭，无法调用。"
            if llm.configured
            else "自由制造未开启；请先在下方配置并启用大模型 API Key。"
        )
    else:
        detail = "自由制造未开启：未命中配方时会返回不支持。"
    return FreeCraftStatusResponse(
        enabled=enabled,
        available=llm.enabled,
        llm_configured=llm.configured,
        llm_enabled=llm.enabled,
        risk_accepted=switch.risk_accepted,
        source=source,
        detail=detail,
    )


@router.get("/settings/llm", response_model=LlmStatusResponse)
def get_llm_settings(request: Request) -> LlmStatusResponse:
    """返回当前 LLM 配置状态（只读、零网络、不含明文密钥）。"""
    runtime = _runtime(request)
    return LlmStatusResponse.model_validate(_status_payload(runtime))


@router.put("/settings/llm", response_model=LlmSaveResponse)
def save_llm_settings(request: Request, body: LlmSaveRequestBody) -> LlmSaveResponse:
    """保存玩家输入的 key 并立即热切换；可选保存后测试连接。"""
    runtime = _runtime(request)
    api_key = body.api_key.strip()
    if not api_key:
        raise ApiError(ErrorCode.INVALID_INPUT, "API Key 不能为空")
    try:
        status = runtime.save_user_credentials(
            provider=body.provider,
            model=body.model,
            api_base=body.api_base,
            api_key=api_key,
        )
    except CredentialStoreError as exc:
        raise ApiError(ErrorCode.INVALID_INPUT, str(exc)) from exc
    test_payload: LlmTestResponse | None = None
    if body.run_test:
        test_payload = LlmTestResponse.model_validate(runtime.test_connection())
    return LlmSaveResponse(
        status=LlmStatusResponse.model_validate(status.as_dict()),
        test=test_payload,
    )


@router.post("/settings/llm/test", response_model=LlmTestResponse)
def test_llm_connection(request: Request) -> LlmTestResponse:
    """用当前配置发一次最小请求（默认离线时零网络，直接返回失败原因）。"""
    runtime = _runtime(request)
    return LlmTestResponse.model_validate(runtime.test_connection())


@router.delete("/settings/llm", response_model=LlmDeleteResponse)
def clear_llm_settings(request: Request) -> LlmDeleteResponse:
    """清除本机保存的 key 并热切换（存在开发 key 时仍会继续使用开发 key）。"""
    runtime = _runtime(request)
    status = runtime.clear_user_credentials()
    return LlmDeleteResponse(status=LlmStatusResponse.model_validate(status.as_dict()))


@router.get("/settings/free-craft", response_model=FreeCraftStatusResponse)
def get_free_craft_settings(request: Request) -> FreeCraftStatusResponse:
    """返回自由制造开关状态（只读、不联网；大模型不可用时提示先配置 key）。"""
    runtime = _runtime(request)
    switch, _store, source = _free_craft_state(request)
    return _free_craft_status(runtime, switch, source)


@router.put("/settings/free-craft", response_model=FreeCraftStatusResponse)
def save_free_craft_settings(
    request: Request, body: FreeCraftSaveRequestBody
) -> FreeCraftStatusResponse:
    """切换自由制造：开启需 LLM 已启用 + 显式确认风险；先落盘再改内存开关。"""
    runtime = _runtime(request)
    switch, store, _source = _free_craft_state(request)
    llm = runtime.status()
    if body.enabled:
        if not llm.enabled:
            raise ApiError(
                ErrorCode.CONFLICT,
                "请先配置并启用大模型 API Key，再开启自由制造",
            )
        if not body.accept_resolver_llm_risk:
            raise ApiError(
                ErrorCode.CONFLICT,
                "开启自由制造需要确认：会在裁决/重试链路内调用大模型，可能重复计费",
            )
    risk_accepted = switch.risk_accepted or body.accept_resolver_llm_risk
    with _FREE_CRAFT_WRITE_LOCK:
        try:
            store.save(enabled=body.enabled, risk_accepted=risk_accepted)
        except OSError as exc:
            raise ApiError(
                ErrorCode.INTERNAL, "保存自由制造设置失败，请检查本机 data 目录写权限"
            ) from exc
        # 落盘成功后才切换运行期状态；失败时保持原状态，避免“界面显示已开但重启丢失”。
        switch.set_enabled(body.enabled, risk_accepted=risk_accepted)
        request.app.state.free_craft_source = "user"
    return _free_craft_status(runtime, switch, "user")
