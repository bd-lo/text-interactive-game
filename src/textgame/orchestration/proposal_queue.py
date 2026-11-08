"""``ProposalQueue``：多角色提案的回合内排队、去重、排序与过期（第 11 章 §11.2/§11.3）。

架构红线（第 11 章 §11.1、§11.3）：多个角色 Agent 只生产提案，``ProposalQueue`` 收集后按
``(priority desc, enqueue_seq asc, actor_id asc)`` 排序交给 ``Resolver`` 裁决，最后由唯一写入者
``GameEngine`` 原子提交。本模块是**纯内存组件**：零 IO、无随机、不读时钟、不持有仓储句柄，
同一入队序列必得同一 ``proposal_id``/同一出队顺序（可回放，第 11 章 §11.3 规则 1）。

口径（契约 ``.work/m3-contract.md`` §3.2，字段名禁止改名）：

- ``proposal_id = f"p_{turn_id:06d}_{enqueue_seq:03d}"``（第 21 章 §21.2.2 口径；``turn_id`` 为
  6 位补零、``enqueue_seq`` 为 3 位补零的队列内部计数器，不是 ``Event.seq``，第 9 章 §9.2 步骤 9）。
- 同 actor + 同 tool + 同 args（``domain.hashing.canonical_json`` 规范化后比较）的提案在回合内
  去重：重复入队**返回已存在的 entry**、不新增、不消耗新的 ``enqueue_seq``（第 11 章 §11.3 规则 4
  “同一 actor 的多提案合并为一次裁决，避免自我竞争”）。
- ``drain`` 只处理 pending 条目，按排序键取前 ``max_items`` 条并置 ``resolved``；超出部分保持
  pending，交给 ``expire_pending``（``expired`` 仅表示回合终止时未裁决，第 11 章 §11.4）。
- 提案生命周期固定 ``pending -> resolved / expired``；``resolved`` 只表示“已交给 Resolver”，
  其最终含义由对应的 ``Resolution.verdict`` 表达，两套状态词不得混写（第 11 章 §11.4）。

``entries``/``pending`` 属性返回列表副本：``QueueEntry`` 本身 frozen，外部无法借返回值改内部状态。
"""

from __future__ import annotations

from enum import Enum
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from textgame.domain.hashing import canonical_json
from textgame.domain.models import Proposal

#: 队列默认``drain``批量（契约§3.2：``ProposalQueue(turn_id, max_items=8)``）。
DEFAULT_MAX_ITEMS: Final[int] = 8


class ProposalSource(str, Enum):
    """提案来源（契约 §3.2；第 11 章 §11.1 authority 分层的服务端推导维度）。

    authority 由服务端按“谁产出提案 + 工具策略”推导，绝不读取模型输出或客户端字段，
    因此本枚举只由编排层在构造 ``QueueEntry`` 时显式传入。
    """

    PLAYER = "player"
    NPC = "npc"
    RULE = "rule"
    SYSTEM = "system"


#: 提案生命周期状态（第 11 章 §11.4：pending → resolved / expired）。
EntryStatus = Literal["pending", "resolved", "expired"]

#: 去重键口径：同 actor + 同 tool + 规范化 args（第 11 章 §11.3 规则 4）。
DedupeKey = tuple[str, str, str]


class QueueEntry(BaseModel):
    """一条“已入队提案”的不可变记录；``status`` 变更通过 ``model_copy`` 生成新对象。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    proposal_id: str = Field(min_length=1, max_length=64)
    enqueue_seq: int = Field(ge=1, description="队列内部计数器（从 1 起），不是 Event.seq")
    source: ProposalSource
    proposal: Proposal
    status: EntryStatus = "pending"


def _proposal_id_for(*, turn_id: int, enqueue_seq: int) -> str:
    """按第 21 章 §21.2.2 口径派生 ``proposal_id``（审计与 ``Event.params`` 共用）。"""
    return f"p_{turn_id:06d}_{enqueue_seq:03d}"


def _dedupe_key(proposal: Proposal) -> DedupeKey:
    """同 actor + 同 tool + 规范化 args：键序/空白差异不影响去重结果（失败即抛 ``TypeError``）。"""
    return (proposal.actor_id, proposal.tool, canonical_json(proposal.args))


def _sort_key(entry: QueueEntry) -> tuple[int, int, str]:
    """裁决顺序键 ``(priority desc, enqueue_seq asc, actor_id asc)``（第 11 章 §11.3 规则 1）。

    ``enqueue_seq`` 在队列内唯一，``actor_id`` 兜底只在构造性测试（手工 ``QueueEntry``）里可达；
    保留它保证排序键与契约逐字一致，任何未来调整 ``enqueue_seq`` 语义时排序仍确定性。
    """
    return (-entry.proposal.priority, entry.enqueue_seq, entry.proposal.actor_id)


class ProposalQueue:
    """一回合内的提案队列；构造即绑定 ``turn_id``，跨回合须新建实例。

    - ``turn_id`` 必须为正整数（``TurnId`` 口径），否则 ``ValueError``；
    - ``max_items`` 是 ``drain()`` 的默认裁决批量（``>= 1``），非法值 ``ValueError``；
    - 不读时钟/随机/仓储，同一调用序列必得同一结果（第 11 章 §11.3“保证回放确定性”）。
    """

    def __init__(self, *, turn_id: int, max_items: int = DEFAULT_MAX_ITEMS) -> None:
        """校验 ``turn_id``/``max_items`` 后初始化空队列；纯内存，不读任何外部状态。"""
        if isinstance(turn_id, bool) or turn_id < 1:
            raise ValueError(f"turn_id must be a positive integer, got {turn_id!r}")
        if isinstance(max_items, bool) or max_items < 1:
            raise ValueError(f"max_items must be a positive integer, got {max_items!r}")
        self._turn_id: int = turn_id
        self._max_items: int = max_items
        self._entries: list[QueueEntry] = []
        self._position: dict[str, int] = {}
        self._dedupe: dict[DedupeKey, str] = {}
        self._next_enqueue_seq: int = 1

    def enqueue(self, proposal: Proposal, *, source: ProposalSource) -> QueueEntry:
        """入队一条提案并返回对应 ``QueueEntry``；重复提案返回已存在 entry（不新增）。

        - ``proposal.turn_id`` 必须与队列 ``turn_id`` 一致（提案属于本回合），否则 ``ValueError``；
        - 同 actor + 同 tool + 同 args 的去重命中时返回**当前**已存在 entry（可能已 ``resolved``）
          且不消耗 ``enqueue_seq``；
        - 入队不做容量拒绝：``max_items`` 是 ``drain()`` 的默认裁决批量（契约 §3.2“超出
          ``max_items`` 的保持 pending”），提案数量预算由编排层（每回合玩家 + 同场景 NPC）控制。
        """
        if proposal.turn_id != self._turn_id:
            raise ValueError(
                f"proposal turn_id {proposal.turn_id} does not match queue turn_id {self._turn_id}"
            )
        key = _dedupe_key(proposal)
        existing_id = self._dedupe.get(key)
        if existing_id is not None:
            return self._entries[self._position[existing_id]]
        enqueue_seq = self._next_enqueue_seq
        entry = QueueEntry(
            proposal_id=_proposal_id_for(turn_id=self._turn_id, enqueue_seq=enqueue_seq),
            enqueue_seq=enqueue_seq,
            source=source,
            proposal=proposal,
        )
        self._entries.append(entry)
        self._position[entry.proposal_id] = len(self._entries) - 1
        self._dedupe[key] = entry.proposal_id
        self._next_enqueue_seq += 1
        return entry

    def drain(self, max_items: int | None = None) -> list[QueueEntry]:
        """按排序键取出至多 ``max_items`` 条 pending 条目并置 ``resolved``。

        ``max_items=None`` 使用构造时的 ``max_items``；``0`` 是合法边界（返回空列表、不做任何
        状态迁移），负数/非整数 ``ValueError``。返回条目按裁决顺序排列；未取出的条目保持
        ``pending``（由 ``expire_pending`` 处理，第 11 章 §11.4）。已 ``resolved``/``expired``
        的条目不参与后续 ``drain``。
        """
        limit = self._max_items if max_items is None else max_items
        if isinstance(limit, bool) or limit < 0:
            raise ValueError(f"drain max_items must be a non-negative integer, got {limit!r}")
        if limit == 0:
            return []
        candidates = sorted(
            (entry for entry in self._entries if entry.status == "pending"),
            key=_sort_key,
        )[:limit]
        resolved: list[QueueEntry] = []
        for entry in candidates:
            updated = entry.model_copy(update={"status": "resolved"})
            self._entries[self._position[entry.proposal_id]] = updated
            resolved.append(updated)
        return resolved

    def expire_pending(self) -> list[QueueEntry]:
        """把全部 pending 条目置 ``expired`` 并返回（回合终止时未裁决，第 11 章 §11.4）。

        已 ``resolved`` 的条目不受影响、不会重复返回；返回顺序与 ``drain`` 的裁决顺序一致
        （``(priority desc, enqueue_seq asc, actor_id asc)``），便于审计按同一口径落库。
        """
        expired: list[QueueEntry] = []
        pending = sorted(
            (entry for entry in self._entries if entry.status == "pending"),
            key=_sort_key,
        )
        for entry in pending:
            updated = entry.model_copy(update={"status": "expired"})
            self._entries[self._position[entry.proposal_id]] = updated
            expired.append(updated)
        return expired

    @property
    def entries(self) -> list[QueueEntry]:
        """全部条目（入队顺序）的列表副本；外部修改返回值不污染队列内部。"""
        return list(self._entries)

    @property
    def pending(self) -> list[QueueEntry]:
        """仍为 ``pending`` 的条目（入队顺序）的列表副本；外部修改返回值不污染队列内部。"""
        return [entry for entry in self._entries if entry.status == "pending"]
