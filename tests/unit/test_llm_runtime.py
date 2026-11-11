"""``llm/runtime.py`` 单测：环境变量优先/干净克隆离线/玩家 key 热切换/连接测试。

关键口径（用户需求）：本地开发机有 ``.env`` 开发 key → 默认直接启用，无需输入；
``git clone`` 无 ``.env`` → 未配置、离线可玩，玩家网页填入 key 后立即生效。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import SecretStr

from fakes import FakeTransport
from textgame.config.settings import Settings
from textgame.llm.client import ChatMessage, LLMRequest, ModelTier
from textgame.llm.credentials import CredentialStoreError, LocalLLMCredentialStore
from textgame.llm.runtime import RuntimeLLMClient


@pytest.fixture(autouse=True)
def _clear_llm_enabled_flag(
    monkeypatch: pytest.MonkeyPatch,
    _isolate_llm_environment: None,
) -> None:
    """本模块测「运行期自动判定」：清掉 conftest 为安全加上的 ``TEXTGAME_LLM_ENABLED=false``。

    依赖 conftest 的 ``_isolate_llm_environment`` 以保证执行顺序（先污染再清除）；
    需要测显式关闭的用例在 Settings 构造参数里显式传 ``llm_enabled=False``。
    """
    monkeypatch.delenv("TEXTGAME_LLM_ENABLED", raising=False)


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    """不读仓库根 ``.env``、也不受 conftest 隔离变量影响的 Settings（测试辅助）。

    本模块专测「运行期自动判定」：构造期间临时移除 ``TEXTGAME_LLM_ENABLED``，避免任何
    fixture 顺序（pytest-randomly 会打乱顺序）把显式关闭标记带进配置。
    """
    previous = os.environ.pop("TEXTGAME_LLM_ENABLED", None)
    try:
        values: dict[str, object] = {
            "_env_file": None,
            "llm_credentials_file": tmp_path / "llm.credentials.json",
        }
        values.update(overrides)
        return Settings(**values)  # type: ignore[arg-type]
    finally:
        if previous is not None:
            os.environ["TEXTGAME_LLM_ENABLED"] = previous


def _request(turn_id: int = 1) -> LLMRequest:
    """最小纯文本请求（连接测试之外的通用调用）。"""
    return LLMRequest(
        task="narrate",
        agent_id="narrator",
        turn_id=turn_id,
        idempotency_key=f"test-{turn_id}",
        model_tier=ModelTier.ECONOMY,
        messages=[ChatMessage(role="user", content="描述一下房间")],
        timeout_s=5.0,
        max_repair=0,
        prompt_version="test@v1",
    )


def _store(tmp_path: Path) -> LocalLLMCredentialStore:
    """临时凭据存储。"""
    return LocalLLMCredentialStore(tmp_path / "llm.credentials.json")


def test_clean_clone_is_offline_and_playable(tmp_path: Path) -> None:
    """无 env key、无本机凭据 → 未配置/离线（可玩，不抛异常）。"""
    client = RuntimeLLMClient(_settings(tmp_path))
    status = client.status()
    assert status.configured is False
    assert status.enabled is False
    assert status.source == "none"
    assert "未配置大模型" in status.detail
    result = client.complete_text(_request())
    assert result.ok is False


def test_dev_env_key_enables_without_input(tmp_path: Path) -> None:
    """本地开发机（.env 有开发 key）→ 默认直接启用，无需玩家输入。"""
    transport = FakeTransport(["OK"])
    client = RuntimeLLMClient(
        _settings(tmp_path, llm_api_key=SecretStr("sk-dev-key-123456")),
        transport=transport,
    )
    status = client.status()
    assert status.configured is True
    assert status.enabled is True
    assert status.source == "env"
    assert status.masked_key is not None
    assert "sk-dev-key-123456" not in status.masked_key
    result = client.complete_text(_request())
    assert result.ok is True
    assert transport.calls[0]["api_key"] == "sk-dev-key-123456"
    assert transport.calls[0]["provider"] == "deepseek"


def test_explicit_disable_wins_over_env_key(tmp_path: Path) -> None:
    """显式 ``TEXTGAME_LLM_ENABLED=false`` 优先：即使有开发 key 也离线。"""
    transport = FakeTransport(["OK"])
    client = RuntimeLLMClient(
        _settings(tmp_path, llm_api_key=SecretStr("sk-dev-key-123456"), llm_enabled=False),
        transport=transport,
    )
    status = client.status()
    assert status.enabled is False
    assert "显式关闭" in status.detail
    assert client.complete_text(_request()).ok is False
    assert transport.calls == []


def test_user_saved_key_enables_runtime_without_restart(tmp_path: Path) -> None:
    """干净克隆：玩家保存本机 key 后立即启用（无需重启）。"""
    transport = FakeTransport(["OK"])
    client = RuntimeLLMClient(_settings(tmp_path), transport=transport)
    assert client.status().enabled is False

    status = client.save_user_credentials(
        provider="deepseek",
        model="deepseek-chat",
        api_base="https://api.deepseek.com",
        api_key="sk-user-key-abcdef",
    )
    assert status.enabled is True
    assert status.source == "user"
    assert status.has_saved_user_key is True
    result = client.complete_text(_request())
    assert result.ok is True
    assert transport.calls[0]["api_key"] == "sk-user-key-abcdef"
    assert transport.calls[0]["model"] == "deepseek-chat"


def test_env_key_precedes_user_saved_key(tmp_path: Path) -> None:
    """环境变量开发 key 永远优先于本机保存的 key。"""
    store = _store(tmp_path)
    store.save(
        provider="deepseek",
        model="deepseek-chat",
        api_base="https://api.deepseek.com",
        api_key="sk-user-key-abcdef",
    )
    transport = FakeTransport(["OK"])
    client = RuntimeLLMClient(
        _settings(tmp_path, llm_api_key=SecretStr("sk-env-key-999999")),
        transport=transport,
    )
    status = client.status()
    assert status.source == "env"
    assert status.has_saved_user_key is True
    client.complete_text(_request())
    assert transport.calls[0]["api_key"] == "sk-env-key-999999"


def test_clear_user_key_returns_to_offline(tmp_path: Path) -> None:
    """清除本机 key 后回到离线（无 env key 场景）。"""
    client = RuntimeLLMClient(_settings(tmp_path))
    client.save_user_credentials(
        provider="deepseek",
        model="deepseek-chat",
        api_base="https://api.deepseek.com",
        api_key="sk-user-key-abcdef",
    )
    status = client.clear_user_credentials()
    assert status.enabled is False
    assert status.source == "none"
    assert status.has_saved_user_key is False


def test_invalid_save_rejected(tmp_path: Path) -> None:
    """非法凭据字段在保存时拒绝（422 语义由接入层映射）。"""
    client = RuntimeLLMClient(_settings(tmp_path))
    with pytest.raises(CredentialStoreError):
        client.save_user_credentials(
            provider="openai",
            model="gpt-4o",
            api_base="https://api.openai.com",
            api_key="sk-x",
        )


def test_corrupt_saved_file_treated_as_unconfigured(tmp_path: Path) -> None:
    """本机凭据文件损坏 → 视为未配置（不阻塞启动）。"""
    store = _store(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{broken", encoding="utf-8")
    client = RuntimeLLMClient(_settings(tmp_path))
    assert client.status().enabled is False


def test_test_connection_success_and_offline(tmp_path: Path) -> None:
    """连接测试：启用时发最小请求返回 ok/延迟；离线时不发请求直接给原因。"""
    offline = RuntimeLLMClient(_settings(tmp_path))
    offline_result = offline.test_connection()
    assert offline_result["ok"] is False
    assert offline_result["latency_ms"] is None
    assert "未配置大模型" in str(offline_result["error"])

    transport = FakeTransport(["OK"])
    client = RuntimeLLMClient(
        _settings(tmp_path, llm_api_key=SecretStr("sk-dev-key-123456")),
        transport=transport,
    )
    result = client.test_connection()
    assert result["ok"] is True
    assert isinstance(result["latency_ms"], int)
    assert result["provider"] == "deepseek"
    assert len(transport.calls) == 1


def test_model_override_honored(tmp_path: Path) -> None:
    """运行期模型覆盖会传到供应商（不被 JSON 分级默认值吞掉）。"""
    transport = FakeTransport(["OK"])
    client = RuntimeLLMClient(
        _settings(
            tmp_path,
            llm_api_key=SecretStr("sk-dev-key-123456"),
            llm_model="deepseek-chat",
            llm_provider="deepseek",
            llm_base_url="https://api.deepseek.com",
        ),
        transport=transport,
    )
    client.complete_text(_request())
    assert transport.calls[0]["model"] == "deepseek-chat"
    assert transport.calls[0]["api_base"] == "https://api.deepseek.com"
