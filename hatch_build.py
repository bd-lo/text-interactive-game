"""Hatch 构建钩子：把已入库的网页构建产物 ``web/dist`` 内嵌进分发包。

为什么不用静态 ``[tool.hatch.build.targets.wheel.force-include]``：
``force-include`` 在源路径缺失时会让**整个构建失败**，而 editable 安装（``uv sync`` /
``uv run``）也会走 wheel 构建路径——这样「源码里暂时没有 ``web/dist``」的 checkout 会连
终端版都跑不起来（R2 验收要求：无 dist 时终端仍可玩）。改为自定义 build hook：有产物就内嵌，
没有就跳过（运行时 ``create_app`` 会回退到仓库根/包内候选并打 WARNING，终端版不受影响）。

目标路径按 target 区分：
- ``wheel``：``web/dist`` → ``textgame/web_dist``（直接构建 wheel / pip install 后用）；
- ``sdist``：``web/dist`` → ``src/textgame/web_dist``（uv build 会「先 sdist 再 wheel」，
  放到包源码树下后，第二轮 wheel 构建会把 ``src/textgame/web_dist`` 作为包数据自动带入）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class WebDistBuildHook(BuildHookInterface):
    """构建前把 ``web/dist`` 映射进分发包（缺失则跳过，不阻断构建/editable 安装）。"""

    PLUGIN_NAME = "web-dist"

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        dist = Path(self.root) / "web" / "dist"
        if not (dist / "index.html").is_file():
            return
        target = "textgame/web_dist" if self.target_name == "wheel" else "src/textgame/web_dist"
        force_include = build_data.setdefault("force_include", {})
        force_include[str(dist)] = target
