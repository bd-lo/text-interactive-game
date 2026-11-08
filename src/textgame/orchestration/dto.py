"""回合编排的输入 DTO (第 6 章 6.7、第 9 章 9.1)。

``TurnRequest`` 是接入层收到的 HTTP body + ``Idempotency-Key`` 头的编排层投影；
``TurnInput`` 是编排层载入存档后补全 ``turn_id``/``save_seed`` 的内部输入 (第 9 章 TurnInput)。
解析输出 ``ParseOutcome`` 属智能体产物，定义在 ``textgame.agents.dto`` (第 8 章 §8.1)。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TurnRequest(BaseModel):
    """接入层 → 编排层的回合请求 (body 字段 ``input`` 保留原始 JSON 契约名)。"""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    game_id: str = Field(min_length=1, max_length=64)
    text: str = Field(alias="input", min_length=1, max_length=500)
    expected_version: int = Field(ge=0)
    idempotency_key: str = Field(min_length=8, max_length=128)


class TurnInput(BaseModel):
    """编排层载入存档后补全的回合输入 (第 9 章 9.1 的 M2 版本)。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    game_id: str = Field(min_length=1, max_length=64)
    player_id: str = Field(default="player", min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=500)
    idempotency_key: str = Field(min_length=8, max_length=128)
    save_seed: str = Field(min_length=1, max_length=64)
    turn_id: int = Field(ge=1)
    expected_version: int = Field(ge=0)
