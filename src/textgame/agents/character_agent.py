"""``DeterministicCharacterAgent``：M3 确定性角色 Agent（第 8 章 §8.1/§8.2、契约 §3.4）。

架构约束（第 8 章 §8.1/§8.2、第 15 章 §15.4）：

- **Agent 只提案，不落库**：本模块只产出 ``Proposal``，不调工具、不写库、不碰 ``GameEngine``；
  提案进 ``ProposalQueue`` → ``Resolver`` 裁决 → 唯一写入者提交；
- **结构性防泄露**：``propose`` 只接收该角色自己的 ``PerceptionSlice``，不接收 ``GameState``、
  不接收其他角色的记忆/切片；计划表是服务端静态配置（``domain.scenario.M3_NPC_PLANS``），
  不来自模型输出，因此 M3 零 LLM、可回放（决策 D-M3-2）；
- **确定性**：同一 ``(perception.actor_id, turn_id)`` 必得同一提案列表；无随机、无时钟、无 IO。

M3 裁剪：真实 ``CharacterAgent``（LLM + ``CharacterOutput`` Schema 校验 + ≤2 提案）留 M5；
本类按场景计划表确定性产出提案：每回合最多 ``_MAX_PROPOSALS_PER_TURN`` 条、``priority=50``、
``turn_id`` 取当前回合。计划步骤不满足（该 actor 无计划/该回合无步骤）时返回空列表
（第 9 章 §9.2 步骤 8“单个 NPC 失败按不行动处理”的确定性等价）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final, Protocol

from textgame.domain.models import PerceptionSlice, Proposal
from textgame.domain.scenario import PlanStep

#: 每个角色每回合最多产出的提案数（第 8 章 §8.1 ``CharacterOutput.proposals`` ≤ 2）。
_MAX_PROPOSALS_PER_TURN: Final[int] = 2

#: NPC 提案默认优先级（第 11 章 §11.3 规则 1：玩家 100、NPC 50）。
NPC_PROPOSAL_PRIORITY: Final[int] = 50


class CharacterAgentLike(Protocol):
    """角色 Agent 端口（契约 §3.4）：只消费 ``PerceptionSlice``，输出 ``Proposal`` 列表。"""

    def propose(
        self,
        perception: PerceptionSlice,
        *,
        turn_id: int,
        save_seed: str,
    ) -> list[Proposal]: ...


class DeterministicCharacterAgent:
    """按场景计划表产提案的确定性角色 Agent（M3 无 LLM、可回放，决策 D-M3-2）。

    ``plans`` 是服务端静态配置：``actor_id -> 该角色的计划步骤序列``
    （``domain.scenario.M3_NPC_PLANS`` 即该口径）。构造时做防御性拷贝，运行期不再读取外部状态。
    """

    def __init__(self, plans: Mapping[str, Sequence[PlanStep]]) -> None:
        """保存计划表的只读快照（``dict``/``tuple`` 拷贝，避免调用方后续修改影响回放）。"""
        self._plans: dict[str, tuple[PlanStep, ...]] = {
            actor_id: tuple(steps) for actor_id, steps in plans.items()
        }

    def propose(
        self,
        perception: PerceptionSlice,
        *,
        turn_id: int,
        save_seed: str,
    ) -> list[Proposal]:
        """返回该 actor 在 ``turn_id`` 的计划提案；无计划/不在场 → ``[]``。

        - actor 身份只取 ``perception.actor_id``（切片是该角色私有视角，第 8 章 §8.2）；
        - 计划按 ``PlanStep.turn_id`` 过滤，保持计划表原始顺序，最多截取 2 条；
        - ``save_seed`` 为 M5 真实 LLM 角色 Agent 预留（M3 确定性路径不使用）：不读随机；
        - 非法 ``turn_id``（< 1）属编程错误 → ``ValueError``，避免产出非法 ``Proposal``。
        """
        _ = save_seed  # M3 确定性路径无需种子；保留签名与第 8 章 CharacterAgent 端口一致
        if isinstance(turn_id, bool) or turn_id < 1:
            raise ValueError(f"turn_id must be a positive integer, got {turn_id!r}")
        steps = self._plans.get(perception.actor_id, ())
        proposals: list[Proposal] = []
        for step in steps:
            if step.turn_id != turn_id:
                continue
            proposals.append(
                Proposal(
                    turn_id=turn_id,
                    actor_id=perception.actor_id,
                    tool=step.tool,
                    args=dict(step.args),
                    reason=step.reason,
                    priority=NPC_PROPOSAL_PRIORITY,
                )
            )
            if len(proposals) >= _MAX_PROPOSALS_PER_TURN:
                break
        return proposals


__all__ = ["NPC_PROPOSAL_PRIORITY", "CharacterAgentLike", "DeterministicCharacterAgent"]
