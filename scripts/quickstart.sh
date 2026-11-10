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
# 只安装运行时依赖（不装 dev 组，约 98 个包）；首次运行需联网下载 CPython 3.12 与锁定依赖
uv sync --frozen --no-dev
exec uv run --no-dev python -m textgame.console --seed 42
