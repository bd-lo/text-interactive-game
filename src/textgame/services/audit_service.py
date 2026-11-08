"""``AuditService``: 审计行的保存与只读查询 (M3 契约 D-M3-7、第 11 章 §11.4、第 14 章 §14.6)。

审计表只 INSERT、不 UPDATE/DELETE：每次回合尝试由编排层组装 ``AuditRecord`` 后调用
:meth:`save`；查询按 ``(turn_id, proposal_id)`` 升序返回。审计状态词固定
``{committed, conflicted, aborted}``，与 ``Resolution.verdict`` 词表分离、不得混写。
"""

from __future__ import annotations

from collections.abc import Sequence

from textgame.services.ports import AuditRecord, GameRepository
from textgame.services.query_service import GameNotFoundError


class AuditService:
    """审计记录写入与查询；只依赖仓储端口，不写游戏状态、不产生事件。"""

    def __init__(self, repo: GameRepository) -> None:
        """注入仓储（组合根装配），审计表只增不改。"""
        self._repo = repo

    def save(self, records: Sequence[AuditRecord]) -> None:
        """批量只增写入审计行；空列表直接返回（不产生 IO）。"""
        if not records:
            return
        self._repo.save_audit(records)

    def query(self, game_id: str, *, turn_id: int | None = None) -> list[AuditRecord]:
        """按存档（可选回合）查询审计行；存档不存在抛 ``GameNotFoundError``（接入层 404）。"""
        if self._repo.load(game_id) is None:
            raise GameNotFoundError(game_id)
        return self._repo.read_audit(game_id, turn_id=turn_id)


__all__ = ["AuditService"]
