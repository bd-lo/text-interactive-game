"""真实子进程启动器端到端测试（``python -m textgame.launcher``）。

覆盖冻结契约 §4：

- 真实进程 + 临时 SQLite + 空闲端口启动；``/healthz`` 就绪后 ``GET /app/status`` 断言
  启动器模式字段，``POST /app/heartbeat`` 200，``POST /app/shutdown`` 200 →
  进程 ≤15s 内优雅退出且 ``returncode == 0``，日志含 ready / ``exit reason=shutdown``；
- 空闲自动退出：``--idle-exit-seconds 1`` + 一次心跳 → 进程在阈值后自行退出
  （``exit reason=idle``，returncode 0）。

只连 ``127.0.0.1``（符合 conftest 的 Q9 禁网守卫）；所有临时文件在 ``tmp_path``。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from textgame import __version__

pytestmark = [pytest.mark.integration]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_READY_TIMEOUT_S = 25.0
_EXIT_TIMEOUT_S = 15.0

#: 显式绕过系统代理（本机回环请求不应经过 HTTP_PROXY）。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _free_port() -> int:
    """向 OS 借一个空闲的 loopback 端口（关闭后立刻复用，竞态窗口可忽略）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _http(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 2.0,
) -> tuple[int, Any]:
    """发一个 JSON HTTP 请求，返回 ``(status, body)``；4xx/5xx 也返回而不是抛错。"""
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return int(response.status), (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        return int(exc.code), (json.loads(raw) if raw else None)


def _start_launcher(*, port: int, db_path: Path, idle_exit_seconds: int) -> subprocess.Popen[str]:
    """启动真实 ``python -m textgame.launcher`` 子进程（合并 stdout/stderr 便于断言日志）。"""
    env = dict(os.environ)
    env["TEXTGAME_DB_URL"] = f"sqlite:///{db_path.as_posix()}"
    env["TEXTGAME_LLM_ENABLED"] = "false"  # 子进程也不联网（继承测试隔离口径）
    env.pop("TEXTGAME_LLM_API_KEY", None)
    env.pop("TEXTGAME_IDLE_EXIT_SECONDS", None)  # 用 CLI 显式覆盖，避免本机 .env 干扰
    command = [
        sys.executable,
        "-m",
        "textgame.launcher",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--idle-exit-seconds",
        str(idle_exit_seconds),
        "--log-level",
        "INFO",
    ]
    return subprocess.Popen(
        command,
        cwd=str(_REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _finish_process(process: subprocess.Popen[str]) -> str:
    """确保子进程结束并返回合并输出（测试失败/成功收尾共用）。"""
    if process.poll() is None:
        process.kill()
    try:
        output, _stderr = process.communicate(timeout=10)
    except subprocess.TimeoutExpired:  # pragma: no cover - kill 后仍卡住的极端情况
        process.kill()
        output, _stderr = process.communicate()
    return output or ""


def _wait_ready(base_url: str, process: subprocess.Popen[str]) -> None:
    """轮询 ``/healthz`` 直到服务就绪；进程提前退出则带输出报错。"""
    deadline = time.monotonic() + _READY_TIMEOUT_S
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = _finish_process(process)
            raise AssertionError(
                f"launcher 提前退出（returncode={process.returncode}）：\n{output}"
            )
        try:
            status, _body = _http("GET", f"{base_url}/healthz", timeout=0.5)
            if status == 200:
                return
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        time.sleep(0.05)
    raise AssertionError(f"launcher 未在 {_READY_TIMEOUT_S}s 内就绪")


# ---------------------------------------------------------------------------
# 前端 POST /app/shutdown → 优雅退出
# ---------------------------------------------------------------------------
def test_launcher_process_shutdown_exits_zero(tmp_path: Path) -> None:
    """真实子进程：healthz → status → heartbeat → shutdown → ≤15s 退出且 returncode=0 + 日志。"""
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    process = _start_launcher(
        port=port,
        db_path=tmp_path / "launcher-shutdown.db",
        idle_exit_seconds=120,
    )
    try:
        _wait_ready(base_url, process)

        status_code, status_body = _http("GET", f"{base_url}/api/v1/app/status")
        assert status_code == 200, status_body
        assert set(status_body) == {
            "launcher_mode",
            "idle_exit_seconds",
            "shutdown_supported",
            "version",
        }
        assert status_body["launcher_mode"] is True
        assert status_body["shutdown_supported"] is True
        assert status_body["idle_exit_seconds"] == 120
        assert status_body["version"] == __version__

        heartbeat_code, heartbeat_body = _http("POST", f"{base_url}/api/v1/app/heartbeat")
        assert heartbeat_code == 200, heartbeat_body
        assert heartbeat_body == {"ok": True, "idle_exit_seconds": 120}

        shutdown_code, shutdown_body = _http(
            "POST", f"{base_url}/api/v1/app/shutdown", {"confirm": True}
        )
        assert shutdown_code == 200, shutdown_body
        assert shutdown_body == {"ok": True, "message": "游戏正在退出"}

        returncode = process.wait(timeout=_EXIT_TIMEOUT_S)
        output = _finish_process(process)
        assert returncode == 0, output
        assert f"LAUNCHER: ready on http://127.0.0.1:{port}" in output
        assert "LAUNCHER: exit reason=shutdown" in output
    finally:
        _finish_process(process)


# ---------------------------------------------------------------------------
# 空闲自动退出（至少 1 次心跳后超过阈值）
# ---------------------------------------------------------------------------
def test_launcher_process_idle_exits_after_heartbeat(tmp_path: Path) -> None:
    """真实子进程：``--idle-exit-seconds 1`` + 一次心跳 → 自行优雅退出（reason=idle, code 0）。"""
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    process = _start_launcher(
        port=port,
        db_path=tmp_path / "launcher-idle.db",
        idle_exit_seconds=1,
    )
    try:
        _wait_ready(base_url, process)
        heartbeat_code, heartbeat_body = _http("POST", f"{base_url}/api/v1/app/heartbeat")
        assert heartbeat_code == 200, heartbeat_body
        assert heartbeat_body == {"ok": True, "idle_exit_seconds": 1}

        returncode = process.wait(timeout=_EXIT_TIMEOUT_S)
        output = _finish_process(process)
        assert returncode == 0, output
        assert "LAUNCHER: exit reason=idle" in output
    finally:
        _finish_process(process)
