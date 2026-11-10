#!/usr/bin/env bash
# 终端版一键入口（兼容旧命令）：等价于 ./scripts/play.sh --terminal
# 网页版优先入口见 ./scripts/play.sh（有 web/dist 时自动启动 http://127.0.0.1:8000）
set -euo pipefail
exec "$(cd "$(dirname "$0")" && pwd)/play.sh" --terminal
