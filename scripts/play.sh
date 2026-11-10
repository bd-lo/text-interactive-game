#!/usr/bin/env bash
# 一键启动（网页优先）· 文字交互游戏
# 用法：
#   ./scripts/play.sh              # 有 web/dist → 启动网页版并打印 URL；否则回终端版
#   ./scripts/play.sh --terminal   # 直接玩终端版（等价于旧的 quickstart.sh）
#   ./scripts/play.sh --check      # 自检：启动网页服务 → GET /healthz 与 / 均 200 → 退出
#   ./scripts/play.sh --port 8001  # 指定端口（默认 8000）
#   ./scripts/play.sh --llm        # 用真实 LLM 叙事（需 .env 填 TEXTGAME_LLM_API_KEY）
set -euo pipefail

TERMINAL=0
CHECK=0
PORT=8000
LLM=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --terminal|-t) TERMINAL=1 ;;
    --check) CHECK=1 ;;
    --port) shift; PORT="${1:?--port 需要端口号}" ;;
    --port=*) PORT="${1#*=}" ;;
    --llm) LLM=1 ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "未知参数：$1（可用 --terminal / --check / --port / --llm）" >&2; exit 2 ;;
  esac
  shift
done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

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

DIST_INDEX="$ROOT/web/dist/index.html"

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
deadline = time.time() + 25
ready = False
while time.time() < deadline:
    try:
        with urllib.request.urlopen(url + "/healthz", timeout=2) as resp:
            if resp.status == 200:
                ready = True
                break
    except Exception:
        time.sleep(0.5)
if not ready:
    print("自检失败：服务未在 25 秒内就绪。")
    sys.exit(1)
try:
    with urllib.request.urlopen(url + "/", timeout=5) as resp:
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
