#!/usr/bin/env bash
# 内容包一键体验（N1-035-036）· examples/content-pack/（校验+导入+建局+打开对局页）
#
# 用法：
#   ./scripts/quickstart-content-pack.sh                  # 临时数据库 + 离线 + 自动导入 + 打开对局页
#   ./scripts/quickstart-content-pack.sh --check          # headless 自检：校验/导入/建局全过则退出码 0
#   ./scripts/quickstart-content-pack.sh --port 8001      # 换端口（--check 时端口被占用会自动向后找空端口）
#   ./scripts/quickstart-content-pack.sh --no-browser     # 不自动打开浏览器
#   ./scripts/quickstart-content-pack.sh --keep-db        # 退出后保留临时数据库（默认清理）
#
# 说明（护栏）：显式离线（TEXTGAME_LLM_ENABLED=false）+ 独立临时数据库（不读写 data/textgame.db）；
# 不复制/不打印任何密钥；服务只监听 127.0.0.1（复用 scripts/play.sh 的 uv/uvicorn 启动口径）；
# 内容包是纯数据：脚本只把它 POST 到既有 validate/import 端点，不执行其中任何内容。
set -euo pipefail

CHECK=0
NO_BROWSER=0
KEEP_DB=0
PORT=8000

usage() {
  sed -n '2,12p' "$0"
  exit 0
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --check|-c) CHECK=1 ;;
    --no-browser) NO_BROWSER=1 ;;
    --keep-db) KEEP_DB=1 ;;
    --port) shift; PORT="${1:?--port 需要端口号}" ;;
    --port=*) PORT="${1#*=}" ;;
    -h|--help) usage ;;
    *) echo "未知参数：$1（可用 --check / --port / --no-browser / --keep-db）" >&2; exit 2 ;;
  esac
  shift
done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PACK_DIR="$ROOT/examples/content-pack"
BASE_URL="http://127.0.0.1:$PORT"
cd "$ROOT"

# 1) 前置：uv（与 scripts/play.sh 相同的唯一必需前置）
if ! command -v uv >/dev/null 2>&1; then
  echo "未找到 uv：请先安装 https://github.com/astral-sh/uv 后重试。" >&2
  exit 1
fi
if [ ! -d "$PACK_DIR" ]; then
  echo "缺少内容包目录：$PACK_DIR" >&2
  exit 1
fi

# 2) 依赖：首次运行安装运行时依赖（与 play.sh 同口径；已安装则秒级）
if [ ! -d "$ROOT/.venv" ]; then
  echo "== 首次运行：安装 Python 运行时依赖（需要联网）…"
  (cd "$ROOT" && uv sync --frozen --no-dev) || {
    echo "依赖安装失败：首次运行需要联网下载 CPython 与依赖包。" >&2
    exit 1
  }
fi

port_busy() {
  uv run --no-dev python - "$1" <<'PY'
import socket
import sys

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(0.5)
try:
    busy = sock.connect_ex(("127.0.0.1", int(sys.argv[1]))) == 0
finally:
    sock.close()
sys.exit(0 if busy else 1)
PY
}

# 3) 端口：--check 自动找空端口；交互模式端口被占用时给可操作提示（与 play.sh 一致）。
if port_busy "$PORT"; then
  if [ "$CHECK" -eq 1 ]; then
    PICKED=0
    candidate="$PORT"
    while [ "$candidate" -lt $((PORT + 20)) ]; do
      if ! port_busy "$candidate"; then
        PICKED="$candidate"
        break
      fi
      candidate=$((candidate + 1))
    done
    if [ "$PICKED" -eq 0 ]; then
      echo "端口 $PORT 起连续 20 个端口都被占用，请用 --port 指定。" >&2
      exit 1
    fi
    PORT="$PICKED"
    BASE_URL="http://127.0.0.1:$PORT"
  else
    echo "端口 $PORT 已被占用：请先关闭占用进程，或用 --port 换一个端口（如 --port 8001）。" >&2
    exit 1
  fi
fi

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
  if [ "$NO_BROWSER" -eq 1 ] || [ "${TEXTGAME_LAUNCHER_NO_BROWSER:-0}" = "1" ]; then
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

# 4) 临时运行目录：临时 SQLite + 临时凭据路径（不读写仓库 data/，不复制任何真实密钥）
TEMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/textgame-content-pack-XXXXXX")"
DB_FILE="$TEMP_ROOT/content-pack.db"
LOG_FILE="$TEMP_ROOT/server.log"
ERR_LOG_FILE="$TEMP_ROOT/server.err.log"
RUN_TOKEN="$(date +%s)-$$"

# Git Bash（Windows）下把 POSIX 路径转成 Python/SQLite 可用的 Windows 路径；Linux/macOS 原样使用。
if command -v cygpath >/dev/null 2>&1; then
  DB_FILE_FOR_URL="$(cygpath -m "$DB_FILE")"
else
  DB_FILE_FOR_URL="$DB_FILE"
fi

export TEXTGAME_DB_URL="sqlite:///$DB_FILE_FOR_URL"
export TEXTGAME_LLM_ENABLED=false
export TEXTGAME_FREE_CRAFT_ENABLED=false
export TEXTGAME_LLM_CREDENTIALS_FILE="$TEMP_ROOT/llm.credentials.json"

SERVER_PID=""
cleanup() {
  if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  if [ "$KEEP_DB" -eq 1 ]; then
    echo "临时数据库已保留：$DB_FILE"
    echo "服务日志已保留：$LOG_FILE"
  else
    rm -rf "$TEMP_ROOT"
  fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM

show_log_tail() {
  for path in "$ERR_LOG_FILE" "$LOG_FILE"; do
    if [ -f "$path" ]; then
      tail -n 8 "$path" | sed 's/^/  /' >&2 || true
    fi
  done
}

echo "== 启动网页服务（离线、临时数据库）：$BASE_URL"
uv run --no-dev uvicorn textgame.main:app --host 127.0.0.1 --port "$PORT" >"$LOG_FILE" 2>"$ERR_LOG_FILE" &
SERVER_PID=$!
if ! wait_health "$BASE_URL" 80; then
  echo "[content-pack] 失败：服务未在 80 秒内就绪（端口被占用/依赖缺失/启动异常？）。" >&2
  show_log_tail
  exit 1
fi

echo "== 校验并导入内容包（场景 / 世界书 / 人物卡 → 建局 → /view）"
PACK_OUTPUT="$(uv run --no-dev python - "$BASE_URL" "$PACK_DIR" "$RUN_TOKEN" <<'PY'
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = sys.argv[1].rstrip("/")
PACK = Path(sys.argv[2])
TOKEN = sys.argv[3]
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def request(method, path, body=None, headers=None):
    req = urllib.request.Request(BASE + path, data=body, headers=dict(headers or {}), method=method)
    try:
        with OPENER.open(req, timeout=30) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def fail(message):
    print("RESULT=FAIL")
    print(message)
    sys.exit(1)


def expect(status, raw, wanted, label):
    if status != wanted:
        fail("%s failed: HTTP %s %s" % (label, status, raw.decode("utf-8", "replace")[:600]))
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        fail("%s returned non-JSON: %s" % (label, raw.decode("utf-8", "replace")[:300]))


def post_json(path, payload, key=None):
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if key:
        headers["Idempotency-Key"] = key
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return request("POST", path, body, headers)


def post_raw(path, raw, key=None):
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if key:
        headers["Idempotency-Key"] = key
    return request("POST", path, raw, headers)


def post_multipart(path, filename, content, key):
    boundary = "----textgame-content-pack-boundary"
    head = (
        "--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"%s\"\r\n"
        "Content-Type: application/json\r\n\r\n" % (boundary, filename)
    ).encode("utf-8")
    tail = ("\r\n--%s--\r\n" % boundary).encode("utf-8")
    headers = {
        "Content-Type": "multipart/form-data; boundary=" + boundary,
        "Idempotency-Key": key,
    }
    return request("POST", path, head + content + tail, headers)


def read_json(path):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        fail("cannot read %s: %s" % (path, exc))
    if not isinstance(payload, dict):
        fail("%s root must be a JSON object" % path)
    return payload


def main():
    pack = PACK.resolve()
    scenarios = sorted((pack / "scenarios").glob("*.json"))
    books = sorted((pack / "world-books").glob("*.json"))
    cards = sorted((pack / "character-cards").glob("*.json"))
    if len(scenarios) < 2 or not books or not cards:
        fail(
            "content pack layout incomplete: scenarios=%d books=%d cards=%d"
            % (len(scenarios), len(books), len(cards))
        )

    scenario_ids = {}
    for index, path in enumerate(scenarios, start=1):
        raw = path.read_bytes()
        payload = read_json(path)
        slug = str(payload.get("slug") or path.stem)
        status, body = post_raw("/api/v1/scenarios/validate", raw)
        result = expect(status, body, 200, "validate scenario %s" % slug)
        if result.get("ok") is not True:
            fail("scenario %s not valid: %s" % (slug, json.dumps(result.get("errors"), ensure_ascii=False)[:600]))
        status, body = post_raw("/api/v1/scenarios", raw, key="cp-%s-scenario-%02d" % (TOKEN, index))
        imported = expect(status, body, 201, "import scenario %s" % slug)
        scenario_ids[slug] = str(imported["scenario_id"])
        print("SCENARIO slug=%s id=%s" % (slug, scenario_ids[slug]))

    book_ids = []
    for index, path in enumerate(books, start=1):
        payload = read_json(path)
        status, body = post_json("/api/v1/world-books/validate", {"payload": payload})
        result = expect(status, body, 200, "validate world book %s" % path.stem)
        if result.get("valid") is not True:
            fail("world book %s not valid: %s" % (path.stem, json.dumps(result.get("errors"), ensure_ascii=False)[:600]))
        status, body = post_multipart(
            "/api/v1/world-books/import",
            path.name,
            path.read_bytes(),
            "cp-%s-book-%02d" % (TOKEN, index),
        )
        imported = expect(status, body, 201, "import world book %s" % path.stem)
        book_ids.append(str(imported["world_book_id"]))
        print("WORLD_BOOK file=%s id=%s entries=%s" % (path.name, book_ids[-1], imported.get("entry_count")))

    card_ids = []
    for index, path in enumerate(cards, start=1):
        raw = path.read_bytes()
        status, body = post_raw("/api/v1/character-cards/validate", raw)
        result = expect(status, body, 200, "validate card %s" % path.stem)
        if result.get("valid") is not True:
            fail("card %s not valid: %s" % (path.stem, json.dumps(result.get("errors"), ensure_ascii=False)[:600]))
        status, body = post_raw("/api/v1/character-cards", raw, key="cp-%s-card-%02d" % (TOKEN, index))
        imported = expect(status, body, 201, "save card %s" % path.stem)
        card_ids.append(str(imported["card_id"]))
        print("CARD file=%s id=%s" % (path.name, card_ids[-1]))

    main_slug = "ashen-lighthouse" if "ashen-lighthouse" in scenario_ids else sorted(scenario_ids)[0]
    game_payload = {"scenario_id": scenario_ids[main_slug], "seed": 7}
    if book_ids:
        game_payload["world_book_id"] = book_ids[0]
    status, body = post_json("/api/v1/games", game_payload, key="cp-%s-game-main" % TOKEN)
    created = expect(status, body, 201, "create game")
    game_id = str(created["game_id"])
    print("GAME id=%s scenario=%s" % (game_id, main_slug))

    status, body = request("GET", "/api/v1/games/%s/view" % game_id)
    view = expect(status, body, 200, "game view")
    if view.get("version") != created.get("version"):
        fail("game view version mismatch: %s != %s" % (view.get("version"), created.get("version")))
    print("VIEW game=%s version=%s" % (game_id, view.get("version")))

    if card_ids:
        status, body = post_json("/api/v1/games", {"card_id": card_ids[0]}, key="cp-%s-game-card" % TOKEN)
        card_game = expect(status, body, 201, "create card game")
        print("CARD_GAME id=%s" % (card_game["game_id"]))

    print("RESULT=PASS")
    return 0


sys.exit(main())
PY
)" || {
  echo "$PACK_OUTPUT"
  echo "[content-pack] 失败：内容包校验/导入/建局失败（见上方输出与服务日志）。" >&2
  show_log_tail
  exit 1
}
echo "$PACK_OUTPUT"

GAME_ID="$(printf '%s\n' "$PACK_OUTPUT" | sed -n 's/^GAME id=\([^ ]*\).*/\1/p' | head -n 1)"
if [ -z "$GAME_ID" ]; then
  echo "[content-pack] 失败：未从输出中解析到对局 ID。" >&2
  exit 1
fi

if [ "$CHECK" -eq 1 ]; then
  if ! uv run --no-dev python - "$BASE_URL" <<'PY'
import sys
import urllib.request

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    with opener.open(sys.argv[1] + "/", timeout=5) as resp:
        sys.exit(0 if resp.status == 200 else 1)
except Exception:
    sys.exit(1)
PY
  then
    echo "[content-pack] 失败：GET / 未返回 200。" >&2
    show_log_tail
    exit 1
  fi
  echo "PASS: 内容包自检通过（2 场景校验+导入、1 世界书、1 人物卡、建局 /view、GET /=200；对局 $GAME_ID）"
  exit 0
fi

GAME_URL="$BASE_URL/game/$GAME_ID"
echo "== 对局页面：$GAME_URL"
echo "场景库：$BASE_URL/scenarios（可另开「镜井」；人物卡在「卡库」，世界书在「世界书」）"
echo "终端等价：uv run --no-dev python -m textgame.console --scenario-id <上方 SCENARIO 的 id>（或 --card-id <CARD 的 id>）"
echo "按 Ctrl+C 结束游戏；临时数据库会被清理（想保留请加 --keep-db）。"
open_browser "$GAME_URL"
wait "$SERVER_PID" 2>/dev/null || true
