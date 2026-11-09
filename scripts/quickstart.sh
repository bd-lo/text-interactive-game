#!/usr/bin/env bash
# OSS-1 一键试玩（Linux / macOS / WSL / Git Bash）
# 唯一前置：uv（https://github.com/astral-sh/uv）；Python 由 uv 自动下载管理。
# 默认离线确定性路径：不需要 API Key、Node/pnpm 或 curl。
set -euo pipefail

if ! command -v uv >/dev/null 2>&1; then
    echo "未找到 uv：请先安装 https://github.com/astral-sh/uv 后重试。"
    exit 1
fi

cd "$(dirname "$0")/.."

echo "== 文字交互游戏 · 5 分钟上手 =="
uv sync --frozen
exec uv run python -m textgame.console --seed 42
