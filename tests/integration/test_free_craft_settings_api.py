"""``/api/v1/settings/free-craft`` 集成测试（前端开关 + 持久化 + R-19 确认）。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from textgame.api.errors import install_error_handlers
from textgame.api.routes.settings import router as settings_router
from textgame.config.settings import Settings
from textgame.llm.runtime import RuntimeLLMClient
from textgame.persistence.free_craft_settings import FreeCraftSettingsStore
from textgame.services.free_craft import FreeCraftSwitch

_STATUS_KEYS = {
    "enabled",
    "available",
    "llm_configured",
    "llm_enabled",
    "risk_accepted",
    "source",
    "detail",
}


@pytest.fixture(autouse=True)
def _auto_mode_env(monkeypatch: pytest.MonkeyPatch, _isolate_llm_environment: None) -> None:
    """本模块测「运行期自动判定」：清掉 conftest 为安全加上的显式关闭标记。"""
    monkeypatch.delenv("TEXTGAME_LLM_ENABLED", raising=False)


def _settings(tmp_path: Path, *, with_key: bool, **overrides: object) -> Settings:
    """不读仓库根 ``.env`` 的 Settings；``with_key`` 控制 LLM 是否已启用。"""
    previous = os.environ.pop("TEXTGAME_LLM_ENABLED", None)
    try:
        values: dict[str, object] = {
            "_env_file": None,
            "llm_credentials_file": tmp_path / "llm.credentials.json",
        }
        if with_key:
            values["llm_api_key"] = SecretStr("sk-test-free-craft")
        values.update(overrides)
        return Settings(**values)  # type: ignore[arg-type]
    finally:
        if previous is not None:
            os.environ["TEXTGAME_LLM_ENABLED"] = previous


def _app(
    *, with_key: bool, tmp_path: Path
) -> tuple[TestClient, FreeCraftSwitch, FreeCraftSettingsStore]:
    """最小应用：设置路由 + 共享 switch/store（组合根装配由 main.py 负责）。"""
    runtime = RuntimeLLMClient(_settings(tmp_path, with_key=with_key))
    switch = FreeCraftSwitch()
    store = FreeCraftSettingsStore(tmp_path / "free_craft.json")
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(settings_router)
    app.state.llm_runtime = runtime
    app.state.free_craft_switch = switch
    app.state.free_craft_store = store
    app.state.free_craft_source = "default"
    return TestClient(app), switch, store


def test_status_defaults_off_and_requires_llm(tmp_path: Path) -> None:
    """无 key：开关关闭、available=false，提示先配置 API Key。"""
    client, _switch, _store = _app(with_key=False, tmp_path=tmp_path)
    with client:
        response = client.get("/api/v1/settings/free-craft")

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == _STATUS_KEYS
    assert body["enabled"] is False
    assert body["available"] is False
    assert body["llm_configured"] is False
    assert body["llm_enabled"] is False
    assert body["source"] == "default"
    assert "先" in body["detail"] and "API Key" in body["detail"]


def test_enable_without_llm_is_conflict_and_keeps_switch_off(tmp_path: Path) -> None:
    """未配置 LLM：409 CONFLICT，不写文件、不切开关。"""
    client, switch, store = _app(with_key=False, tmp_path=tmp_path)
    with client:
        response = client.put(
            "/api/v1/settings/free-craft",
            json={"enabled": True, "accept_resolver_llm_risk": True},
        )

    assert response.status_code == 409
    assert response.json()["error_code"] == "CONFLICT"
    assert switch.enabled is False
    assert store.load() is None


def test_enable_requires_explicit_risk_confirmation(tmp_path: Path) -> None:
    """LLM 已启用但未确认风险：409，不开启；文案说明草稿生成一次且失败零写入。"""
    client, switch, _store = _app(with_key=True, tmp_path=tmp_path)
    with client:
        response = client.put(
            "/api/v1/settings/free-craft",
            json={"enabled": True, "accept_resolver_llm_risk": False},
        )

    assert response.status_code == 409
    body = response.json()
    assert body["error_code"] == "CONFLICT"
    assert "生成一次草稿" in body["message"]
    assert "失败不会写入对局" in body["message"]
    assert switch.enabled is False


def test_enable_persists_and_updates_shared_switch(tmp_path: Path) -> None:
    """LLM 已启用 + 确认风险：立即开启、source=user、写盘 enabled/risk_accepted。"""
    client, switch, store = _app(with_key=True, tmp_path=tmp_path)
    with client:
        enabled = client.put(
            "/api/v1/settings/free-craft",
            json={"enabled": True, "accept_resolver_llm_risk": True},
        )
        status = client.get("/api/v1/settings/free-craft")

    assert enabled.status_code == 200, enabled.text
    body = enabled.json()
    assert set(body) == _STATUS_KEYS
    assert body["enabled"] is True
    assert body["available"] is True
    assert body["risk_accepted"] is True
    assert body["source"] == "user"
    assert "生成一次草稿" in body["detail"]
    assert switch.enabled is True and switch.risk_accepted is True
    saved = FreeCraftSettingsStore(tmp_path / "free_craft.json").load()
    assert saved is not None and saved.enabled is True and saved.risk_accepted is True
    assert status.json()["source"] == "user"

    with client:
        disabled = client.put(
            "/api/v1/settings/free-craft",
            json={"enabled": False, "accept_resolver_llm_risk": False},
        )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["enabled"] is False
    assert switch.enabled is False
    saved_after = store.load()
    assert saved_after is not None and saved_after.enabled is False


def test_enable_rejects_extra_fields(tmp_path: Path) -> None:
    """schema 收紧：未知字段 → 422 统一信封。"""
    client, _switch, _store = _app(with_key=True, tmp_path=tmp_path)
    with client:
        response = client.put(
            "/api/v1/settings/free-craft",
            json={"enabled": True, "accept_resolver_llm_risk": True, "extra": 1},
        )

    assert response.status_code == 422
    assert response.json()["error_code"] == "INVALID_INPUT"


def test_save_failure_keeps_switch_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """落盘失败：500，内存开关保持关闭（避免界面显示已开但重启丢失）。"""
    client, switch, store = _app(with_key=True, tmp_path=tmp_path)

    def _fail(*, enabled: bool, risk_accepted: bool) -> None:
        del enabled, risk_accepted
        raise OSError("disk full")

    monkeypatch.setattr(store, "save", _fail)
    with client:
        response = client.put(
            "/api/v1/settings/free-craft",
            json={"enabled": True, "accept_resolver_llm_risk": True},
        )

    assert response.status_code == 500
    assert response.json()["error_code"] == "INTERNAL"
    assert switch.enabled is False
