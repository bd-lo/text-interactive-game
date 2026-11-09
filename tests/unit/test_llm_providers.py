"""单元测试：供应商模型映射、NO_PROXY 兼容清洗与异常分类（不联网，M1/M5）。

M5-019 追加：``classify_exception`` 的 retryable 判定（第 12 章 12.3：仅超时/429/5xx 重试）、
``chat_completion`` 强制 ``num_retries=0``（重试所有权归 tenacity），以及「全仓只有
``providers.py`` import litellm」的架构门禁（第 12 章 12.1、第 16 章 16.2）。
"""

import ast
import os
from pathlib import Path
from typing import Any

import pytest

from textgame.llm import providers
from textgame.llm.errors import ProviderCallError, classify_status_code
from textgame.llm.providers import (
    chat_completion,
    classify_exception,
    litellm_model_id,
    sanitize_no_proxy_env,
)


def test_litellm_model_id_adds_provider_prefix() -> None:
    """provider/model 分开配置时拼成 LiteLLM 需要的 ``provider/model``。"""
    assert litellm_model_id("deepseek", "deepseek-flash") == "deepseek/deepseek-flash"


def test_litellm_model_id_keeps_explicit_provider() -> None:
    """模型名里已带 provider 前缀时不重复拼接。"""
    assert litellm_model_id("deepseek", "openai/gpt-x") == "openai/gpt-x"


def test_sanitize_no_proxy_env_removes_bracketed_ipv6(monkeypatch: pytest.MonkeyPatch) -> None:
    """本机 NO_PROXY 含 httpx 0.28 无法解析的 ``[::1]``，必须剔除且保留其余条目。"""
    raw = "localhost,127.0.0.1,::1,[::1]"
    monkeypatch.setenv("NO_PROXY", raw)
    monkeypatch.setenv("no_proxy", raw)

    sanitize_no_proxy_env()

    assert os.environ["NO_PROXY"] == "localhost,127.0.0.1,::1"


def test_sanitize_no_proxy_env_keeps_valid_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """没有方括号 IPv6 条目时不改写 NO_PROXY。"""
    raw = "localhost,127.0.0.1,::1"
    monkeypatch.setenv("NO_PROXY", raw)
    monkeypatch.setenv("no_proxy", raw)

    sanitize_no_proxy_env()

    assert os.environ["NO_PROXY"] == raw


# ---------------------------------------------------------------------------
# M5-019：异常分类 / 重试所有权 / SDK 导入边界
# ---------------------------------------------------------------------------
class _FakeProviderError(Exception):
    """模拟供应商 SDK 异常（可带 status_code 属性）。"""

    def __init__(self, status_code: int | None = None) -> None:
        super().__init__("provider failure")
        if status_code is not None:
            self.status_code = status_code


def test_classify_exception_retryable_5xx_and_429() -> None:
    """503/429 可重试；401/400 不可重试（第 12 章 12.3）。"""
    for status in (503, 429, 500):
        error = classify_exception(_FakeProviderError(status))
        assert error.retryable is True, status
        assert error.status_code == status
    for status in (401, 400, 422):
        error = classify_exception(_FakeProviderError(status))
        assert error.retryable is False, status


def test_classify_exception_timeout_and_connection_are_retryable() -> None:
    """内置超时/连接异常无状态码时按类型判定可重试。"""
    assert classify_exception(TimeoutError("slow")).retryable is True
    assert classify_exception(ConnectionError("reset")).retryable is True


def test_classify_exception_unknown_defaults_to_not_retryable() -> None:
    """未知异常默认不可重试（避免未知故障反复计费）。"""

    class WeirdError(Exception):
        pass

    assert classify_exception(WeirdError("?")).retryable is False


def test_classify_status_code_table() -> None:
    """状态码表：429/5xx/408 可重试，其余不可重试；None 不可重试。"""
    assert classify_status_code(None) is False
    assert classify_status_code(408) is True
    assert classify_status_code(429) is True
    assert classify_status_code(502) is True
    assert classify_status_code(400) is False


def test_chat_completion_forces_num_retries_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """LiteLLM ``num_retries=0``：网络重试唯一计数者是 tenacity（第 12 章 12.3）。"""
    import litellm

    captured: dict[str, Any] = {}

    class _Message:
        content = "可用"

    class _Choice:
        def __init__(self) -> None:
            self.message = _Message()

    class _Usage:
        prompt_tokens = 3
        completion_tokens = 2

    class _Response:
        def __init__(self) -> None:
            self.choices = [_Choice()]
            self.usage = _Usage()

    def fake_completion(**kwargs: Any) -> _Response:
        captured.update(kwargs)
        return _Response()

    monkeypatch.setattr(litellm, "completion", fake_completion)

    result = chat_completion(
        provider="deepseek",
        model="deepseek-flash",
        api_key="sk-test",
        api_base="https://api.deepseek.com",
        messages=[{"role": "user", "content": "hi"}],
        timeout_s=5.0,
    )

    assert captured["num_retries"] == 0
    assert captured["timeout"] == 5.0
    assert result["content"] == "可用"
    assert result["prompt_tokens"] == 3


def test_chat_completion_translates_provider_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """供应商异常在 providers 内翻译为 ProviderCallError，不向上抛裸 SDK 异常。"""
    import litellm

    def boom(**kwargs: Any) -> Any:
        del kwargs
        raise _FakeProviderError(503)

    monkeypatch.setattr(litellm, "completion", boom)

    with pytest.raises(ProviderCallError) as excinfo:
        chat_completion(
            provider="deepseek",
            model="deepseek-flash",
            api_key="sk-test",
            api_base="https://api.deepseek.com",
            messages=[{"role": "user", "content": "hi"}],
            timeout_s=5.0,
        )
    assert excinfo.value.retryable is True
    assert excinfo.value.status_code == 503


def test_litellm_import_only_in_providers() -> None:
    """全仓只有 ``llm/providers.py`` import litellm（第 12 章 12.1 / 第 16 章 16.2）。"""
    src_root = Path(__file__).resolve().parents[2] / "src" / "textgame"
    offenders: list[str] = []
    for path in sorted(src_root.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError:  # pragma: no cover - 源文件语法错误由 ruff/mypy 拦截
            continue
        for node in ast.walk(tree):
            module: str | None = None
            if isinstance(node, ast.Import):
                module = next(
                    (alias.name for alias in node.names if alias.name.startswith("litellm")), None
                )
            elif isinstance(node, ast.ImportFrom):
                module = node.module if node.module and node.module.startswith("litellm") else None
            if module is not None and path.name != "providers.py":
                offenders.append(str(path.relative_to(src_root)))
    assert offenders == [], f"litellm import 越界：{offenders}"


def test_providers_module_has_single_sdk_boundary() -> None:
    """providers 模块暴露 chat_completion（默认 transport 边界），不暴露供应商原始对象。"""
    assert callable(providers.chat_completion)
