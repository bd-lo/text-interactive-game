"""``/api/v1/settings/llm`` 端点集成测试（运行期 API Key 输入/测试/清除）。

覆盖：状态形状、保存热切换、非法字段 422、连接测试成功/离线、清除回退、env 优先、
响应不含明文密钥。使用内存 FastAPI + 真实 ``RuntimeLLMClient``（transport 注入，零网络）。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from fakes import FakeTransport
from textgame.api.errors import install_error_handlers
from textgame.api.routes.settings import router as settings_router
from textgame.config.settings import Settings
from textgame.llm.credentials import LocalLLMCredentialStore
from textgame.llm.runtime import RuntimeLLMClient

_STATUS_KEYS = {
    "configured",
    "enabled",
    "source",
    "provider",
    "model",
    "api_base",
    "masked_key",
    "has_saved_user_key",
    "detail",
}
_TEST_KEYS = {"ok", "latency_ms", "provider", "model", "error"}


@pytest.fixture(autouse=True)
def _auto_mode_env(
    monkeypatch: pytest.MonkeyPatch,
    _isolate_llm_environment: None,
) -> None:
    """本模块测「运行期自动判定」：清掉 conftest 为安全加上的显式关闭标记。"""
    monkeypatch.delenv("TEXTGAME_LLM_ENABLED", raising=False)


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    """不读仓库根 ``.env``、也不受 conftest 隔离变量影响的 Settings（测试辅助）。"""
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


def _app(runtime: RuntimeLLMClient) -> FastAPI:
    """只挂设置路由的最小应用（组合根装配由 main.py 负责，此处注入运行期客户端）。"""
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(settings_router)
    app.state.llm_runtime = runtime
    return app


def _client(
    tmp_path: Path, transport: FakeTransport | None = None, **overrides: object
) -> TestClient:
    """干净克隆场景的 TestClient（无 env key、无本机凭据）。"""
    runtime = RuntimeLLMClient(_settings(tmp_path, **overrides), transport=transport)
    return TestClient(_app(runtime))


def test_status_shape_and_offline_default(tmp_path: Path) -> None:
    """干净克隆：状态为未配置/离线，键集合稳定，detail 给玩家指引。"""
    with _client(tmp_path) as client:
        response = client.get("/api/v1/settings/llm")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == _STATUS_KEYS
    assert body["configured"] is False
    assert body["enabled"] is False
    assert body["source"] == "none"
    assert body["masked_key"] is None
    assert "未配置大模型" in body["detail"]


def test_save_activates_and_hides_plaintext(tmp_path: Path) -> None:
    """保存 key → 立即启用（source=user），响应与文件都不回显明文（响应只打码）。"""
    with _client(tmp_path) as client:
        response = client.put(
            "/api/v1/settings/llm",
            json={
                "api_key": "sk-user-key-abcdef",
                "provider": "deepseek",
                "model": "deepseek-chat",
                "api_base": "https://api.deepseek.com",
            },
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"status", "test"}
    assert body["test"] is None
    status = body["status"]
    assert set(status) == _STATUS_KEYS
    assert status["configured"] is True
    assert status["enabled"] is True
    assert status["source"] == "user"
    assert status["model"] == "deepseek-chat"
    assert status["has_saved_user_key"] is True
    assert status["masked_key"] is not None
    assert "sk-user-key-abcdef" not in response.text
    assert "sk-user-key-abcdef" not in str(status["masked_key"])
    assert LocalLLMCredentialStore(tmp_path / "llm.credentials.json").load() is not None


def test_save_with_run_test_returns_connection_result(tmp_path: Path) -> None:
    """run_test=true：保存后立即测试连接，返回 ok/延迟；不写游戏状态。"""
    transport = FakeTransport(["OK"])
    with _client(tmp_path, transport=transport) as client:
        response = client.put(
            "/api/v1/settings/llm",
            json={"api_key": "sk-user-key-abcdef", "run_test": True},
        )
    assert response.status_code == 200, response.text
    test_payload = response.json()["test"]
    assert set(test_payload) == _TEST_KEYS
    assert test_payload["ok"] is True
    assert isinstance(test_payload["latency_ms"], int)
    assert transport.calls and transport.calls[0]["api_key"] == "sk-user-key-abcdef"


def test_invalid_fields_rejected_422(tmp_path: Path) -> None:
    """非法供应商/地址/空 key → 422 错误信封（消息不含密钥）。"""
    with _client(tmp_path) as client:
        invalid_provider = client.put(
            "/api/v1/settings/llm",
            json={"api_key": "sk-x", "provider": "openai"},
        )
        empty_key = client.put("/api/v1/settings/llm", json={"api_key": "   "})
        bad_base = client.put(
            "/api/v1/settings/llm",
            json={"api_key": "sk-x", "api_base": "ftp://nope"},
        )
    assert invalid_provider.status_code == 422
    assert invalid_provider.json()["error_code"] == "INVALID_INPUT"
    assert empty_key.status_code == 422
    assert bad_base.status_code == 422
    assert "sk-x" not in invalid_provider.text


def test_test_connection_offline_returns_reason(tmp_path: Path) -> None:
    """离线时测试连接不发网络请求，直接返回失败原因。"""
    with _client(tmp_path) as client:
        response = client.post("/api/v1/settings/llm/test")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == _TEST_KEYS
    assert body["ok"] is False
    assert body["latency_ms"] is None
    assert "未配置大模型" in body["error"]


def test_delete_returns_offline(tmp_path: Path) -> None:
    """清除本机 key 后回到离线（无 env key）。"""
    with _client(tmp_path) as client:
        client.put("/api/v1/settings/llm", json={"api_key": "sk-user-key-abcdef"})
        response = client.delete("/api/v1/settings/llm")
    assert response.status_code == 200, response.text
    status = response.json()["status"]
    assert set(status) == _STATUS_KEYS
    assert status["enabled"] is False
    assert status["source"] == "none"
    assert not (tmp_path / "llm.credentials.json").exists()


def test_env_key_has_priority_over_saved(tmp_path: Path) -> None:
    """本地开发机（.env 开发 key）：状态显示 env 来源；清除本机 key 后仍是 env。"""
    with _client(tmp_path, llm_api_key=SecretStr("sk-env-key-999999")) as client:
        before = client.get("/api/v1/settings/llm").json()
        assert before["source"] == "env"
        assert before["enabled"] is True
        cleared = client.delete("/api/v1/settings/llm").json()["status"]
    assert cleared["source"] == "env"
    assert cleared["enabled"] is True
    assert "sk-env-key-999999" not in str(cleared)
    assert "sk-env-key-999999" not in str(before)
