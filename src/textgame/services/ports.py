"""仓储端口 (第 6 章 6.2、第 16 章 16.2)：``services`` 声明，``persistence`` 实现。

``GameEngine`` 只依赖本文件的 ``Protocol`` 与记录类型，不 import 任何具体仓储实现
(第 21 章 21.2.1)；内存实现见 ``textgame.persistence.memory``，SQLite 实现见
``textgame.persistence.sqlite_repository``，装配只发生在 ``main.py`` 组合根。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from textgame.domain.events import Event
from textgame.domain.models import GameState


@dataclass(frozen=True)
class GameRecord:
    """存档当前状态投影；``version`` 是下一次提交的 expected_version。"""

    game_id: str
    state: GameState
    version: int
    last_seq: int


@dataclass(frozen=True)
class SnapshotRecord:
    """派生快照；``last_seq`` 为快照覆盖到的全局事件序号 (第 14 章 14.4)。"""

    game_id: str
    snapshot_version: int
    turn_id: int
    last_seq: int
    state: GameState
    state_hash: str
    schema_version: int = 1


@dataclass(frozen=True)
class IdempotencyRecord:
    """幂等记录；同一 ``key`` + 同一 ``params_hash`` 时回放首次响应 (第 6 章 6.7)。

    ``payload`` 是首次响应的 JSON 载荷（回合为 ``TurnResult.model_dump(mode="json")``，
    开局为 ``GameView.model_dump(mode="json")``），由调用方按场景反序列化；``events``
    为该次提交的盖章态事件，重复请求不重复写事件。
    """

    key: str
    params_hash: str
    payload: dict[str, Any]
    events: tuple[Event, ...] = ()


@dataclass(frozen=True)
class CommitPayload:
    """原子提交载荷：CAS 版本 + 事件全局序号连续 + 更新当前状态。"""

    game_id: str
    expected_version: int
    new_version: int
    last_seq: int
    new_state: GameState
    stamped_events: tuple[Event, ...]


class GameRepository(Protocol):
    """唯一写入者使用的仓储端口；实现必须保证 ``commit`` 的原子性。"""

    def load(self, game_id: str) -> GameRecord | None:
        """读当前状态；current 行缺失时用「最新快照 + 之后事件」重建 (第 14 章 14.4)。"""
        ...

    def read_events(self, game_id: str, *, after_seq: int = 0) -> list[Event]:
        """按 ``seq`` 升序读取 ``seq > after_seq`` 的事件。"""
        ...

    def latest_snapshot(self, game_id: str) -> SnapshotRecord | None:
        """返回最新快照；无快照时返回 ``None``。"""
        ...

    def save_snapshot(self, snapshot: SnapshotRecord) -> None:
        """追加派生快照；快照可随时删除重建，不参与事务正确性。"""
        ...

    def get_idempotent(self, key: str) -> IdempotencyRecord | None:
        """按存储键取幂等记录 (键由编排层拼装，见第 6 章 6.7)。"""
        ...

    def save_idempotent(self, record: IdempotencyRecord) -> None:
        """保存幂等记录；同键已存在时覆盖为最新 (同键异参判定由编排层完成)。"""
        ...

    def commit(self, payload: CommitPayload) -> bool:
        """原子提交；版本冲突/序号不连续时返回 ``False`` 且不部分写入。"""
        ...
