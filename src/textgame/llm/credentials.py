"""本机 LLM 凭据存储（玩家在网页/终端输入的 DeepSeek API Key）。

设计契约：``docs/design/user-scenario-modules.md`` §4 P3 / 本模块 docstring 即口径。

- **只存本机**：默认 ``data/llm.credentials.json``（``data/`` 已 gitignore，与 SQLite 存档同目录）；
  可用 ``TEXTGAME_LLM_CREDENTIALS_FILE`` 覆盖路径。绝不写入数据库、不写日志、不进 git。
- **优先级由 ``llm/runtime.py`` 决定**：环境变量/``.env`` 的开发 key 永远优先（本地开发免输入），
  没有开发 key 时才用本文件里玩家自己填的 key（干净克隆场景）。
- **原子写**：写临时文件 → ``os.replace``；POSIX 下尽力设 ``0o600``（Windows 无等效权限位）。
- **损坏降级**：文件缺失/JSON 损坏 → 返回 ``None``（视为未配置），不阻塞启动；只在读取失败时
  记一条 WARNING（不含任何密钥内容）。
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

__all__ = [
    "CREDENTIALS_SCHEMA_VERSION",
    "DEFAULT_CREDENTIALS_PATH",
    "CredentialStoreError",
    "LocalLLMCredentialStore",
    "StoredLLMCredentials",
    "mask_api_key",
]

_LOGGER = logging.getLogger(__name__)

#: 凭据文件 schema 版本（未来格式变化时用于迁移；当前只接受 1）。
CREDENTIALS_SCHEMA_VERSION = 1

#: 默认凭据文件路径（与默认 DB ``data/textgame.db`` 同目录；```data/`` 已 gitignore）。
DEFAULT_CREDENTIALS_PATH = Path("data") / "llm.credentials.json"

#: 环境变量：覆盖凭据文件路径（测试/多实例用）。
CREDENTIALS_PATH_ENV = "TEXTGAME_LLM_CREDENTIALS_FILE"

#: 支持的供应商 id（v1 只做 DeepSeek；未知值在保存时拒绝，避免误配）。
SUPPORTED_PROVIDERS: frozenset[str] = frozenset({"deepseek"})

_MAX_API_BASE = 512
_MAX_MODEL = 128
_MAX_KEY = 512


class CredentialStoreError(ValueError):
    """凭据字段非法/写入失败；接入层映射 422/500（消息不含密钥内容）。"""


@dataclass(frozen=True)
class StoredLLMCredentials:
    """本机保存的一份 LLM 配置（明文 key 只在内存与本地文件中流转）。"""

    provider: str
    model: str
    api_base: str
    api_key: str
    updated_at: str = field(default="")

    def masked(self) -> str:
        """打码后的 key（用于状态展示；不含完整密钥）。"""
        return mask_api_key(self.api_key) or ""


def mask_api_key(api_key: str | None) -> str | None:
    """把密钥打码成可安全展示的形态（``sk-****abcd``）；空值返回 ``None``。"""
    if not api_key:
        return None
    key = api_key.strip()
    if not key:
        return None
    if len(key) <= 8:
        return f"{key[:2]}****"
    return f"{key[:4]}****{key[-4:]}"


def _now_iso() -> str:
    """当前 UTC 时间（秒精度 ISO8601，便于展示/排错；不参与业务逻辑）。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _validate_fields(*, provider: str, model: str, api_base: str, api_key: str) -> None:
    """保存前校验字段（纯格式校验；不联网验证 key 是否有效）。"""
    if provider not in SUPPORTED_PROVIDERS:
        raise CredentialStoreError(
            f"暂不支持该供应商：{provider!r}（当前支持：{', '.join(sorted(SUPPORTED_PROVIDERS))}）"
        )
    if not model or len(model) > _MAX_MODEL or any(ch.isspace() for ch in model):
        raise CredentialStoreError("模型名非法：必须是非空且不含空白的短字符串")
    if not api_base.startswith(("http://", "https://")) or len(api_base) > _MAX_API_BASE:
        raise CredentialStoreError("API 地址非法：必须以 http:// 或 https:// 开头")
    if not api_key or len(api_key) > _MAX_KEY or any(ch.isspace() for ch in api_key):
        raise CredentialStoreError("API Key 非法：不能为空且不能包含空白字符")


class LocalLLMCredentialStore:
    """本地 JSON 凭据文件读写（线程安全的最佳努力：单进程内保存/读取成对调用很少）。"""

    def __init__(self, path: str | Path | None = None) -> None:
        """``path=None`` 时按 env 覆盖 → 默认 ``data/llm.credentials.json`` 解析。"""
        if path is None:
            env_value = os.environ.get(CREDENTIALS_PATH_ENV)
            path = Path(env_value) if env_value else DEFAULT_CREDENTIALS_PATH
        self._path = Path(path)

    @property
    def path(self) -> Path:
        """凭据文件路径（用于错误消息/诊断；不含内容）。"""
        return self._path

    def load(self) -> StoredLLMCredentials | None:
        """读取并校验凭据；文件缺失/损坏/字段非法 → ``None``（视为未配置）。"""
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            _LOGGER.warning("读取本机 LLM 凭据失败（将视为未配置）：%s", exc.__class__.__name__)
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            _LOGGER.warning("本机 LLM 凭据文件不是合法 JSON（将视为未配置）")
            return None
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != CREDENTIALS_SCHEMA_VERSION
        ):
            _LOGGER.warning("本机 LLM 凭据文件版本不受支持（将视为未配置）")
            return None
        provider = payload.get("provider")
        model = payload.get("model")
        api_base = payload.get("api_base")
        api_key = payload.get("api_key")
        if not all(isinstance(value, str) for value in (provider, model, api_base, api_key)):
            _LOGGER.warning("本机 LLM 凭据字段不完整（将视为未配置）")
            return None
        try:
            _validate_fields(
                provider=str(provider),
                model=str(model),
                api_base=str(api_base),
                api_key=str(api_key),
            )
        except CredentialStoreError:
            _LOGGER.warning("本机 LLM 凭据字段非法（将视为未配置）")
            return None
        updated_at = payload.get("updated_at")
        return StoredLLMCredentials(
            provider=str(provider),
            model=str(model),
            api_base=str(api_base),
            api_key=str(api_key),
            updated_at=updated_at if isinstance(updated_at, str) else "",
        )

    def save(
        self,
        *,
        provider: str,
        model: str,
        api_base: str,
        api_key: str,
    ) -> StoredLLMCredentials:
        """校验并原子写入凭据文件（``0o600`` 尽力而为）；返回保存后的快照。"""
        _validate_fields(provider=provider, model=model, api_base=api_base, api_key=api_key)
        credentials = StoredLLMCredentials(
            provider=provider,
            model=model,
            api_base=api_base,
            api_key=api_key,
            updated_at=_now_iso(),
        )
        payload = {
            "schema_version": CREDENTIALS_SCHEMA_VERSION,
            "provider": credentials.provider,
            "model": credentials.model,
            "api_base": credentials.api_base,
            "api_key": credentials.api_key,
            "updated_at": credentials.updated_at,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self._path.parent,
                prefix=f".{self._path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_path = Path(handle.name)
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self._path)
            temp_path = None
            # Windows 等平台没有等效权限位：尝试设置，失败也不影响保存（单机本地场景可接受）。
            with contextlib.suppress(OSError):
                os.chmod(self._path, 0o600)
            return credentials
        except OSError as exc:
            raise CredentialStoreError(
                f"保存本机 LLM 凭据失败（{exc.__class__.__name__}）：请检查 data/ 目录权限"
            ) from exc
        finally:
            if temp_path is not None:
                with contextlib.suppress(OSError):
                    temp_path.unlink(missing_ok=True)

    def clear(self) -> bool:
        """删除本机凭据文件；返回是否真的删除了文件（不存在返回 ``False``）。"""
        try:
            self._path.unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise CredentialStoreError(
                f"清除本机 LLM 凭据失败（{exc.__class__.__name__}）：请检查 data/ 目录权限"
            ) from exc
