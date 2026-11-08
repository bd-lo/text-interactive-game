"""进程入口: 组装 FastAPI 应用, uvicorn 启动目标为 ``textgame.main:app``。

启动命令: ``uv run uvicorn textgame.main:app --reload --port 8000``
"""

from textgame.api.app import create_app

app = create_app()
