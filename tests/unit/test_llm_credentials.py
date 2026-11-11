"""本机 LLM 凭据存储单测（``llm/credentials.py``）：存取/打码/损坏降级/字段校验/清除。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from textgame.llm.credentials import (
    CREDENTIALS_SCHEMA_VERSION,
    DEFAULT_CREDENTIALS_PATH,
    CredentialStoreError,
    LocalLLMCredentialStore,
    mask_api_key,
)


def _store(tmp_path: Path) -> LocalLLMCredentialStore:
    """临时目录下的凭据存储（测试辅助）。"""
    return LocalLLMCredentialStore(tmp_path / "llm.credentials.json")


def test_mask_api_key_never_leaks_full_key() -> None:
    """打码结果不含完整密钥，且保留可辨识的首尾。"""
    masked = mask_api_key("sk-1234567890abcdef")
    assert masked is not None
    assert "sk-1" in masked
    assert "cdef" in masked
    assert "234567890abcd" not in masked
    assert mask_api_key("") is None
    assert mask_api_key(None) is None
    assert mask_api_key("short") == "sh****"


def test_save_load_roundtrip(tmp_path: Path) -> None:
    """保存后可读回同一配置，文件含 schema 版本与 updated_at。"""
    store = _store(tmp_path)
    saved = store.save(
        provider="deepseek",
        model="deepseek-chat",
        api_base="https://api.deepseek.com",
        api_key="sk-test-key-0001",
    )
    loaded = store.load()
    assert loaded is not None
    assert loaded.provider == "deepseek"
    assert loaded.model == "deepseek-chat"
    assert loaded.api_key == "sk-test-key-0001"
    assert loaded.updated_at == saved.updated_at
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == CREDENTIALS_SCHEMA_VERSION


def test_save_overwrites_previous(tmp_path: Path) -> None:
    """再次保存覆盖旧值（单文件单凭据口径）。"""
    store = _store(tmp_path)
    store.save(provider="deepseek", model="m1", api_base="https://a.example", api_key="sk-1")
    store.save(provider="deepseek", model="m2", api_base="https://b.example", api_key="sk-2")
    loaded = store.load()
    assert loaded is not None
    assert loaded.model == "m2"
    assert loaded.api_key == "sk-2"


def test_missing_file_is_unconfigured(tmp_path: Path) -> None:
    """文件不存在 → ``None``（未配置，不抛异常）。"""
    assert _store(tmp_path).load() is None


def test_corrupt_json_is_unconfigured(tmp_path: Path) -> None:
    """损坏 JSON / 错误版本 / 缺字段 → ``None``（降级未配置）。"""
    store = _store(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{not json", encoding="utf-8")
    assert store.load() is None
    store.path.write_text(json.dumps({"schema_version": 99}), encoding="utf-8")
    assert store.load() is None
    store.path.write_text(
        json.dumps({"schema_version": 1, "provider": "deepseek"}),
        encoding="utf-8",
    )
    assert store.load() is None


@pytest.mark.parametrize(
    ("provider", "model", "api_base", "api_key"),
    [
        ("openai", "gpt", "https://a.example", "sk-1"),
        ("deepseek", "", "https://a.example", "sk-1"),
        ("deepseek", "has space", "https://a.example", "sk-1"),
        ("deepseek", "m", "ftp://a.example", "sk-1"),
        ("deepseek", "m", "https://a.example", ""),
        ("deepseek", "m", "https://a.example", "sk 1"),
    ],
)
def test_invalid_fields_rejected(
    tmp_path: Path, provider: str, model: str, api_base: str, api_key: str
) -> None:
    """非法字段在保存前拒绝（不写文件、消息不含密钥内容）。"""
    store = _store(tmp_path)
    with pytest.raises(CredentialStoreError):
        store.save(provider=provider, model=model, api_base=api_base, api_key=api_key)
    assert not store.path.exists()


def test_clear_removes_file(tmp_path: Path) -> None:
    """清除删除本机凭据文件；重复清除返回 False。"""
    store = _store(tmp_path)
    store.save(provider="deepseek", model="m", api_base="https://a.example", api_key="sk-1")
    assert store.clear() is True
    assert not store.path.exists()
    assert store.clear() is False


def test_env_override_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``TEXTGAME_LLM_CREDENTIALS_FILE`` 可覆盖凭据文件位置。"""
    target = tmp_path / "custom" / "cred.json"
    monkeypatch.setenv("TEXTGAME_LLM_CREDENTIALS_FILE", str(target))
    store = LocalLLMCredentialStore()
    assert store.path == target


def test_default_path_is_under_data_dir() -> None:
    """默认路径为 ``data/llm.credentials.json``（``data/`` 已 gitignore）。"""
    assert Path("data") / "llm.credentials.json" == DEFAULT_CREDENTIALS_PATH
