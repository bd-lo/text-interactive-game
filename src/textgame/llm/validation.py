"""结构化输出：JSON 解析 + Pydantic Schema 校验 + 一次修复重试 (第 12 章 12.6)。

``complete_json_with_repair`` 是解析 Agent 与 ``LLMClient`` 之间唯一的结构化输出入口：
① 原始输出 → ``json.loads`` + ``schema_model.model_validate`` (禁额外字段)；
② 失败 → 把精简后的 pydantic 错误回传，追加一次修正指令 (``max_repair`` 固定 1，共 2 次请求)；
③ 仍失败 → 返回 ``SCHEMA_VIOLATION``，**绝不返回未校验数据** (第 12 章 12.6 红线)。

M2 阶段不联网：客户端由 ``tests/fakes.py::FakeLLM`` 注入。
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ValidationError

from textgame.domain.results import ErrorCode, Result
from textgame.llm.client import ChatMessage, LLMClient, LLMRequest

_MAX_ERROR_DETAIL_CHARS = 600
_MAX_REPAIR_HINT_CHARS = 4000


def _failure_from_client(result: Result) -> Result:
    """客户端失败原样透传错误码 (如 ``LLM_UNAVAILABLE``)，不把失败包装成成功。"""
    error_code = result.error_code or ErrorCode.LLM_UNAVAILABLE
    return Result.fail(error_code, result.message)


def _content_of(result: Result) -> str | None:
    """取客户端返回的原始文本；缺失或类型不符返回 ``None``。"""
    if not result.data:
        return None
    content = result.data.get("content")
    return content if isinstance(content, str) else None


def _usage_of(result: Result) -> dict[str, Any]:
    """取 token 用量；缺失时返回空字典。"""
    if not result.data:
        return {}
    usage = result.data.get("usage")
    return dict(usage) if isinstance(usage, dict) else {}


def _brief_validation_error(exc: ValidationError) -> str:
    """只回传字段路径与错误类型，不回传完整输入 (第 7 章 7.4/7.5 防泄露口径)。"""
    parts: list[str] = []
    for error in exc.errors()[:5]:
        loc = ".".join(str(part) for part in error["loc"]) or "<root>"
        parts.append(f"{loc}: {error['type']}")
    if len(exc.errors()) > 5:
        parts.append(f"... 共 {len(exc.errors())} 处")
    return "; ".join(parts)


def parse_and_validate(
    content: str, schema_model: type[BaseModel]
) -> tuple[dict[str, Any] | None, str]:
    """解析 JSON 文本并按 ``schema_model`` 校验，返回规范化 dict 或错误说明。"""
    try:
        payload: Any = json.loads(content)
    except json.JSONDecodeError as exc:
        return None, f"JSON 解析失败：{exc.msg} (第 {exc.lineno} 行第 {exc.colno} 列)"
    if not isinstance(payload, dict):
        return None, "JSON 根节点必须是对象"
    try:
        model = schema_model.model_validate(payload)
    except ValidationError as exc:
        return None, f"字段校验失败: {_brief_validation_error(exc)}"
    dumped: dict[str, Any] = model.model_dump(mode="json")
    return dumped, ""


def _build_repair_request(req: LLMRequest, error_detail: str) -> LLMRequest:
    """构造修复请求：``max_repair=0`` + 追加一条携带错误说明的 user 消息。"""
    detail = error_detail[:_MAX_ERROR_DETAIL_CHARS]
    hint = (
        f"上一次输出未通过 JSON Schema 校验：{detail}。"
        "请只输出一个符合 Schema 的 JSON 对象，不要输出解释、注释或 Markdown 代码块。"
    )
    repair_message = ChatMessage(role="user", content=hint[:_MAX_REPAIR_HINT_CHARS])
    messages: list[ChatMessage] = [*req.messages, repair_message]
    return req.model_copy(update={"max_repair": 0, "messages": messages})


def complete_json_with_repair(
    client: LLMClient,
    req: LLMRequest,
    *,
    schema_model: type[BaseModel],
) -> Result:
    """调用客户端获取结构化输出；Schema 不合格时携带错误修复一次。

    成功 ``data={"parsed": dict, "content": str, "repaired": bool, "usage": dict}``；
    ``parsed`` 为**已通过 ``schema_model`` 校验并规范化**的字典。失败错误码：
    客户端错误原样透传 (如 ``LLM_UNAVAILABLE``)，两次输出都不合格 → ``SCHEMA_VIOLATION``。
    """
    first = client.complete_json(req)
    if not first.ok:
        return _failure_from_client(first)
    first_content = _content_of(first)
    if first_content is None:
        return Result.fail(ErrorCode.SCHEMA_VIOLATION, "LLM 响应缺少 content 文本")

    parsed, error_detail = parse_and_validate(first_content, schema_model)
    if parsed is not None:
        return Result.success(
            data={
                "parsed": parsed,
                "content": first_content,
                "repaired": False,
                "usage": _usage_of(first),
            }
        )
    if req.max_repair < 1:
        return Result.fail(ErrorCode.SCHEMA_VIOLATION, error_detail)

    second = client.complete_json(_build_repair_request(req, error_detail))
    if not second.ok:
        return _failure_from_client(second)
    second_content = _content_of(second)
    if second_content is None:
        return Result.fail(ErrorCode.SCHEMA_VIOLATION, "修复请求的 LLM 响应缺少 content 文本")

    repaired_parsed, repaired_error = parse_and_validate(second_content, schema_model)
    if repaired_parsed is None:
        return Result.fail(
            ErrorCode.SCHEMA_VIOLATION, f"修复后仍未通过 Schema 校验: {repaired_error}"
        )
    return Result.success(
        data={
            "parsed": repaired_parsed,
            "content": second_content,
            "repaired": True,
            "usage": _usage_of(second),
        }
    )
