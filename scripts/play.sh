#!/usr/bin/env bash
# 一键启动（网页优先）· 文字交互游戏
# 用法：
#   ./scripts/play.sh                    # 有 web/dist → 启动网页版并打印 URL；否则回终端版
#   ./scripts/play.sh --terminal         # 直接玩终端版（等价于旧的 quickstart.sh）
#   ./scripts/play.sh --check            # 自检：启动网页服务 → GET /healthz 与 / 均 200 → 退出
#   ./scripts/play.sh --launcher         # 启动器模式：打开浏览器，等待服务退出后清理
#   ./scripts/play.sh --launcher-check   # headless 自检：验证 launcher 控制面后自动退出
#   ./scripts/play.sh --port 8001        # 指定端口（默认 8000）
#   ./scripts/play.sh --llm              # 用真实 LLM 叙事（需 .env 填 TEXTGAME_LLM_API_KEY）
# 环境变量（自动化/调试）：TEXTGAME_LAUNCHER_NO_BROWSER=1 → --launcher 不自动打开浏览器
set -euo pipefail

TERMINAL=0
CHECK=0
LAUNCHER=0
LAUNCHER_CHECK=0
PORT=8000
LLM=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --terminal|-t) TERMINAL=1 ;;
    --check) CHECK=1 ;;
    --launcher) LAUNCHER=1 ;;
    --launcher-check) LAUNCHER_CHECK=1 ;;
    --port) shift; PORT="${1:?--port 需要端口号}" ;;
    --port=*) PORT="${1#*=}" ;;
    --llm) LLM=1 ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "未知参数：$1（可用 --terminal / --check / --launcher / --launcher-check / --port / --llm）" >&2; exit 2 ;;
  esac
  shift
done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# 本机回环探测：强制忽略 http_proxy/https_proxy（只访问 127.0.0.1，绝不出网）。
health_ok() {
  uv run --no-dev python - "$1" <<'PY'
import sys
import urllib.request

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    with opener.open(sys.argv[1] + "/healthz", timeout=2) as resp:
        sys.exit(0 if resp.status == 200 else 1)
except Exception:
    sys.exit(1)
PY
}

wait_health() {
  uv run --no-dev python - "$1" "${2:-90}" <<'PY'
import sys
import time
import urllib.request

base = sys.argv[1]
timeout = float(sys.argv[2])
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
deadline = time.time() + timeout
while time.time() < deadline:
    try:
        with opener.open(base + "/healthz", timeout=2) as resp:
            if resp.status == 200:
                sys.exit(0)
    except Exception:
        time.sleep(0.4)
sys.exit(1)
PY
}

open_browser() {
  local url="$1"
  if [ "${TEXTGAME_LAUNCHER_NO_BROWSER:-0}" = "1" ]; then
    return 0
  fi
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$url" >/dev/null 2>&1 &
  elif command -v open >/dev/null 2>&1; then
    open "$url" >/dev/null 2>&1 &
  elif command -v cmd.exe >/dev/null 2>&1; then
    cmd.exe /c start "" "$url" >/dev/null 2>&1 &
  else
    echo "请手动在浏览器打开：${url}"
  fi
  return 0
}

# 1) 前置：uv（唯一必需前置；Python 由 uv 自动下载管理）
if ! command -v uv >/dev/null 2>&1; then
  echo "未找到 uv：请先安装 https://github.com/astral-sh/uv 后重试。" >&2
  exit 1
fi

# 2) 可选：真实 LLM 叙事（默认离线确定性，不需要 Key）
if [ "$LLM" -eq 1 ]; then
  export TEXTGAME_LLM_ENABLED=true
  if [ -z "${TEXTGAME_LLM_API_KEY:-}" ] && [ ! -f "$ROOT/.env" ]; then
    echo "已请求 --llm，但没有找到 API Key：" >&2
    echo "  请复制 .env.example 为 .env，填写 TEXTGAME_LLM_API_KEY=<你的 DeepSeek Key>；" >&2
    echo "  不加 --llm 时默认离线模式，不填 Key 也能正常游玩。" >&2
    exit 1
  fi
fi

# 3) 依赖：首次运行安装运行时依赖（需联网，约数百 MB；已安装过则秒级）
if [ ! -d "$ROOT/.venv" ]; then
  echo "== 首次运行：安装 Python 运行时依赖（需要联网）…"
  if ! uv sync --frozen --no-dev; then
    echo "依赖安装失败：首次运行需要联网下载 CPython 与依赖包。" >&2
    echo "  如使用代理/内网，请先配置代理后重试；详见 README「常见问题」。" >&2
    exit 1
  fi
fi

# 3.1) 自动化验收：launcher 控制面 headless 自检（不弹窗、不开浏览器、临时数据库）
if [ "$LAUNCHER_CHECK" -eq 1 ]; then
  echo "== Launcher 自检：启动 launcher 控制面（无窗口、无浏览器、临时数据库）…"
  REQUESTED_PORT="$PORT"
  PORT="$(
    uv run --no-dev python - "$REQUESTED_PORT" <<'PY'
import socket
import sys

start = int(sys.argv[1])
for port in range(start, start + 50):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", port))
    except OSError:
        sock.close()
        continue
    sock.close()
    print(port)
    sys.exit(0)
print("找不到空闲端口：%d..%d" % (start, start + 49), file=sys.stderr)
sys.exit(1)
PY
  )"
  if [ "$PORT" != "$REQUESTED_PORT" ]; then
    echo "提示：端口 $REQUESTED_PORT 被占用，自检改用空闲端口 $PORT。"
  fi
  URL="http://127.0.0.1:${PORT}"
  TMP_DIR="$(mktemp -d)"
  LOG="$TMP_DIR/launcher-server.log"
  export TEXTGAME_DB_URL="sqlite:///$TMP_DIR/textgame.db"
  export TEXTGAME_LLM_ENABLED=false
  echo "   端口：$PORT ｜ 临时库：$TMP_DIR/textgame.db"
  uv run --no-dev python -m textgame.launcher --host 127.0.0.1 --port "$PORT" --idle-exit-seconds 0 >>"$LOG" 2>&1 &
  SERVER_PID=$!
  cleanup_launcher_check() {
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
    rm -rf "$TMP_DIR"
  }
  trap cleanup_launcher_check EXIT
  cat >"$TMP_DIR/launcher-check.py" <<'PY'
import json
import sys
import time
import urllib.request

base = sys.argv[1]
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def request(path, method="GET", body=None):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    with opener.open(req, timeout=5) as resp:
        raw = resp.read()
        return resp.status, json.loads(raw.decode("utf-8")) if raw else None


# 1) 等 /healthz 就绪（≤90s）
deadline = time.time() + 90
healthy = False
while time.time() < deadline:
    try:
        with opener.open(base + "/healthz", timeout=2) as resp:
            if resp.status == 200:
                healthy = True
                break
    except Exception:
        time.sleep(0.4)
if not healthy:
    print("Launcher 自检失败：服务未在 90 秒内就绪。", file=sys.stderr)
    sys.exit(1)
print(f"  [1/5] /healthz 就绪：{base}")

# 2) GET /api/v1/app/status
try:
    status_code, status_body = request("/api/v1/app/status")
except Exception as exc:
    print(f"Launcher 自检失败：GET /api/v1/app/status 出错（{exc}）。", file=sys.stderr)
    sys.exit(1)
if status_code != 200:
    print(f"Launcher 自检失败：GET /api/v1/app/status 返回 {status_code}，期望 200。", file=sys.stderr)
    sys.exit(1)
if not status_body or status_body.get("launcher_mode") is not True:
    print("Launcher 自检失败：status.launcher_mode 不为 true（服务未以 launcher 模式启动）。", file=sys.stderr)
    sys.exit(1)
if status_body.get("shutdown_supported") is not True:
    print("Launcher 自检失败：status.shutdown_supported 不为 true（控制面不可用）。", file=sys.stderr)
    sys.exit(1)
print(
    "  [2/5] GET /api/v1/app/status → 200（launcher_mode=true，shutdown_supported=true，"
    f"idle_exit_seconds={status_body.get('idle_exit_seconds')}，version={status_body.get('version')}）"
)

# 3) POST /api/v1/app/heartbeat
try:
    hb_code, hb_body = request("/api/v1/app/heartbeat", method="POST", body={})
except Exception as exc:
    print(f"Launcher 自检失败：POST /api/v1/app/heartbeat 出错（{exc}）。", file=sys.stderr)
    sys.exit(1)
if hb_code != 200:
    print(f"Launcher 自检失败：POST /api/v1/app/heartbeat 返回 {hb_code}，期望 200。", file=sys.stderr)
    sys.exit(1)
print(f"  [3/5] POST /api/v1/app/heartbeat → 200（idle_exit_seconds={(hb_body or {}).get('idle_exit_seconds')}）")

# 4) POST /api/v1/app/shutdown
try:
    sd_code, sd_body = request("/api/v1/app/shutdown", method="POST", body={"confirm": True})
except Exception as exc:
    print(f"Launcher 自检失败：POST /api/v1/app/shutdown 出错（{exc}）。", file=sys.stderr)
    sys.exit(1)
if sd_code != 200:
    print(f"Launcher 自检失败：POST /api/v1/app/shutdown 返回 {sd_code}，期望 200。", file=sys.stderr)
    sys.exit(1)
if not sd_body or sd_body.get("ok") is not True:
    print("Launcher 自检失败：POST /api/v1/app/shutdown 响应 ok 不为 true。", file=sys.stderr)
    sys.exit(1)
print("  [4/5] POST /api/v1/app/shutdown → 200（ok=true）")

# 5) shutdown 后服务应在 15 秒内停止响应（进程退出 + 端口释放）
deadline = time.time() + 15
stopped = False
while time.time() < deadline:
    try:
        with opener.open(base + "/healthz", timeout=1) as resp:
            resp.read()
    except Exception:
        stopped = True
        break
    time.sleep(0.2)
if not stopped:
    print("Launcher 自检失败：shutdown 后服务仍可访问（未在 15 秒内退出）。", file=sys.stderr)
    sys.exit(1)
print("  [5/5] shutdown 后服务已停止响应")
sys.exit(0)
PY
  if ! uv run --no-dev python "$TMP_DIR/launcher-check.py" "$URL"; then
    echo "Launcher 自检失败：控制面断言未通过（日志末 5 行）：" >&2
    tail -n 5 "$LOG" >&2 || true
    exit 1
  fi
  EXITED=0
  for _ in $(seq 1 60); do
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
      EXITED=1
      break
    fi
    sleep 0.25
  done
  if [ "$EXITED" -ne 1 ]; then
    echo "Launcher 自检失败：shutdown 后进程未在 15 秒内退出。" >&2
    tail -n 5 "$LOG" >&2 || true
    exit 1
  fi
  if ! uv run --no-dev python - "$PORT" <<'PY'
import socket
import sys

sock = socket.socket()
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    sock.bind(("127.0.0.1", int(sys.argv[1])))
except OSError:
    print("Launcher 自检失败：shutdown 后端口仍被占用。", file=sys.stderr)
    sys.exit(1)
finally:
    sock.close()
sys.exit(0)
PY
  then
    exit 1
  fi
  echo "  （进程已退出且端口已释放）"
  echo ""
  echo "Launcher 自检通过"
  exit 0
fi

DIST_INDEX="$ROOT/web/dist/index.html"

# 3.2) 启动器模式：打开浏览器、等待服务退出后清理（trap INT/TERM 杀子进程）
if [ "$LAUNCHER" -eq 1 ]; then
  echo "== 启动器：正在准备…（首次运行需几分钟）"
  if [ ! -f "$DIST_INDEX" ]; then
    echo "未找到网页产物 web/dist/index.html，无法启动网页版。" >&2
    echo "  请重新下载完整的游戏仓库，或在开发环境执行 pnpm --dir web build。" >&2
    exit 1
  fi
  URL="http://127.0.0.1:${PORT}"
  if health_ok "$URL"; then
    echo "== 游戏已经在运行：${URL}"
    echo "   已为你打开浏览器（不会重复启动服务）。"
    open_browser "$URL"
    exit 0
  fi
  LOG_DIR="$ROOT/data/logs"
  mkdir -p "$LOG_DIR"
  LOG="$LOG_DIR/launcher-server.log"
  echo "   日志：$LOG"
  uv run --no-dev python -m textgame.launcher --host 127.0.0.1 --port "$PORT" --idle-exit-seconds 300 >>"$LOG" 2>&1 &
  SERVER_PID=$!
  cleanup_launcher() {
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  }
  trap cleanup_launcher EXIT
  trap 'exit 130' INT TERM
  if ! wait_health "$URL" 90; then
    echo "游戏启动失败：服务未在 90 秒内就绪。日志最后 5 行：" >&2
    tail -n 5 "$LOG" >&2 || true
    exit 1
  fi
  echo "== 已启动，正在打开浏览器：${URL}"
  open_browser "$URL"
  echo "== 游戏运行中：在游戏页面点「退出游戏」，或在本窗口按 Ctrl+C / 关闭本窗口退出。"
  wait "$SERVER_PID" 2>/dev/null || true
  echo "游戏已退出，感谢游玩。"
  exit 0
fi

# 4) 终端版：显式 --terminal，或网页产物缺失时自动回退（保证离线/无 Node 也能玩）
if [ "$TERMINAL" -eq 1 ] || [ ! -f "$DIST_INDEX" ]; then
  if [ ! -f "$DIST_INDEX" ]; then
    echo "== 未找到网页产物 web/dist：先进入终端版。"
    echo "   想玩网页版：pnpm --dir web install --frozen-lockfile && pnpm --dir web build"
  fi
  exec uv run --no-dev python -m textgame.console
fi

# 5) 网页版
URL="http://127.0.0.1:${PORT}"

if [ "$CHECK" -eq 1 ]; then
  echo "== 自检：启动网页服务并校验 ${URL} …"
  LOG="$ROOT/.play-check-${PORT}.log"
  uv run --no-dev uvicorn textgame.main:app --host 127.0.0.1 --port "$PORT" >"$LOG" 2>&1 &
  SERVER_PID=$!
  cleanup() {
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
    rm -f "$LOG"
  }
  trap cleanup EXIT
  if uv run --no-dev python - "$URL" <<'PY'
import sys
import time
import urllib.request

url = sys.argv[1]
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
deadline = time.time() + 25
ready = False
while time.time() < deadline:
    try:
        with opener.open(url + "/healthz", timeout=2) as resp:
            if resp.status == 200:
                ready = True
                break
    except Exception:
        time.sleep(0.5)
if not ready:
    print("自检失败：服务未在 25 秒内就绪。")
    sys.exit(1)
try:
    with opener.open(url + "/", timeout=5) as resp:
        ctype = resp.headers.get("Content-Type", "")
        ok = resp.status == 200 and "text/html" in ctype
        if ok:
            print(f"自检通过：/healthz=200，/=200（{ctype}）")
        else:
            print(f"自检失败：/ 返回 {resp.status}（{ctype}），期望 200 text/html。")
        sys.exit(0 if ok else 1)
except Exception as exc:
    print(f"自检失败：GET / 出错（{exc}）。")
    sys.exit(1)
PY
  then
    exit 0
  else
    if [ "$LLM" -eq 1 ]; then
      echo "  --llm 已开启：请确认 .env 中 TEXTGAME_LLM_API_KEY 已填写且可用。" >&2
    fi
    tail -n 5 "$LOG" >&2 || true
    exit 1
  fi
fi

echo "== 网页版已启动：${URL}"
echo "   按 Ctrl+C 停止服务；存档在 data/textgame.db，下次启动会自动继续。"
exec uv run --no-dev uvicorn textgame.main:app --host 127.0.0.1 --port "$PORT"
