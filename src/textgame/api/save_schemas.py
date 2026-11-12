"""N3-048 存档导出/导入的 HTTP DTO（独立模块，避免与并行改动的 ``api/schemas.py`` 冲突）。

导出成功响应直接返回存档包 JSON（见 ``docs/design/save-export-import.md`` 的包结构；
``export_version="1"``），因此不需要响应模型；导入成功 201 返回本模块的
:class:`SaveImportResponse`（新 ``game_id`` + 新存档头）。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class SaveImportResponse(BaseModel):
    """``POST /api/v1/saves/import`` 201 响应：导入后的新存档头。"""

    model_config = ConfigDict(extra="forbid")

    game_id: str
    turn: int
    version: int
    snapshot_version: int


__all__ = ["SaveImportResponse"]
