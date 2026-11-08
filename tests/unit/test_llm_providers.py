"""单元测试：供应商模型映射与 NO_PROXY 兼容清洗（不联网，M1 环境追加项）。"""

import os

import pytest

from textgame.llm.providers import litellm_model_id, sanitize_no_proxy_env


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
