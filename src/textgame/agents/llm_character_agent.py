"""LLM 角色 Agent（M5 出口附加项；第 8 章 §8.1/§8.2、第 12 章 §12.6、M5-023 规则）。

输入 = **仅该角色自己的私有感知切片**（``PerceptionSlice``：可见实体 + 自己的 local_events
+ 自己的 private_memory）；输出 = ``CharacterOutput``（≤2 个 ``Proposal``，Pydantic Schema）。
失败/离线/两次输出都不合格 → 注入的确定性 ``CharacterAgent`` 兜底（M3 计划表，零 LLM）。

与 023 批量/休眠规则的边界：批量只允许「公共感知」的公共群演，本 Agent 消费私有切片，
因此**只能走串行路径**（``batchable_actors`` 不应包含它）；休眠由编排层调度器决定是否调用
本 Agent（未唤醒零调用），本类不持有状态、不写库、不直接执行工具。
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from textgame.agents.character_agent import NPC_PROPOSAL_PRIORITY, CharacterAgentLike
from textgame.domain.models import PerceptionSlice, Proposal
from textgame.llm.client import ChatMessage, LLMClient, LLMRequest, ModelTier
from textgame.llm.validation import complete_json_with_repair

__all__ = ["PROMPT_VERSION", "CharacterOutput", "CharacterProposalOutput", "LLMCharacterAgent"]

#: 角色 Agent 的 Prompt 版本（第 12 章 §12.5 可追溯；改动 Prompt 必须递增）。
PROMPT_VERSION = "character@v1"

_MAX_PROPOSALS = 2
_MAX_ARGS_JSON_CHARS = 1000
_TOOL_NAME_PATTERN = r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$"
_TASK = "character_act"
_AGENT_ID = "character"
_MAX_PROMPT_CHARS = 7500


class CharacterProposalOutput(BaseModel):
    """单个 NPC 提案的 LLM 输出契约（只给工具名 + 参数 + 理由，任何数值由框架重算）。"""

    model_config = ConfigDict(extra="forbid")

    tool: Annotated[str, Field(min_length=1, max_length=48, pattern=_TOOL_NAME_PATTERN)]
    args: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="npc_llm", min_length=1, max_length=500)


class CharacterOutput(BaseModel):
    """LLM 角色 Agent 的结构化输出契约：每回合 ≤2 条提案（第 8 章 §8.1）。"""

    model_config = ConfigDict(extra="forbid")

    proposals: list[CharacterProposalOutput] = Field(
        default_factory=list, max_length=_MAX_PROPOSALS
    )


def _args_are_sane(args: dict[str, Any]) -> bool:
    """拒绝嵌套/超长参数（注入防御，与解析 Agent 同口径）。"""
    scalar_types = (str, int, float, bool)
    if any(value is not None and not isinstance(value, scalar_types) for value in args.values()):
        return False
    try:
        encoded = json.dumps(args, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        return False
    return len(encoded) <= _MAX_ARGS_JSON_CHARS


class LLMCharacterAgent:
    """LLM 角色 Agent：私有感知 → ≤2 提案；失败回退确定性 Agent（同一端口签名）。"""

    prompt_version = PROMPT_VERSION

    def __init__(self, client: LLMClient, *, fallback: CharacterAgentLike) -> None:
        """注入 LLM 客户端与确定性兜底 Agent（离线可回放、零网络测试）。"""
        self._client = client
        self._fallback = fallback

    def propose(
        self, perception: PerceptionSlice, *, turn_id: int, save_seed: str
    ) -> list[Proposal]:
        """返回该角色本回合提案（≤2）；任何失败都回退 ``fallback.propose``。"""
        try:
            request = self._build_request(perception, turn_id=turn_id, save_seed=save_seed)
            result = complete_json_with_repair(self._client, request, schema_model=CharacterOutput)
            if not result.ok or not result.data:
                return self._fallback.propose(perception, turn_id=turn_id, save_seed=save_seed)
            parsed = result.data.get("parsed")
            if not isinstance(parsed, dict):
                return self._fallback.propose(perception, turn_id=turn_id, save_seed=save_seed)
            output = CharacterOutput.model_validate(parsed)
        except Exception:
            return self._fallback.propose(perception, turn_id=turn_id, save_seed=save_seed)

        proposals: list[Proposal] = []
        for item in output.proposals[:_MAX_PROPOSALS]:
            if not _args_are_sane(item.args):
                continue
            proposals.append(
                Proposal(
                    turn_id=turn_id,
                    actor_id=perception.actor_id,
                    tool=item.tool,
                    args=dict(item.args),
                    reason=item.reason,
                    priority=NPC_PROPOSAL_PRIORITY,
                )
            )
        return proposals

    def _build_request(
        self, perception: PerceptionSlice, *, turn_id: int, save_seed: str
    ) -> LLMRequest:
        """构造角色请求：输入只含该角色私有感知切片（结构性防泄露，第 15 章 §15.4）。"""
        _ = save_seed  # M5 缓存/成本归集预留；本层不用种子
        system = (
            "你是文字冒险游戏中的角色行为规划器：根据该角色自己的私有感知决定本回合动作。"
            "硬性规则：1) 只能依据给定私有感知，不得假设视野外或他人私有信息；"
            "2) 最多 2 个提案，只给工具名/参数/理由，禁止输出数值、成功率或属性改动；"
            "3) 无法确定时输出空 proposals；"
            "输出 JSON Schema："
            + json.dumps(CharacterOutput.model_json_schema(), ensure_ascii=False)
            + "；私有感知："
            + json.dumps(perception.model_dump(mode="json"), ensure_ascii=False)
        )
        return LLMRequest(
            task=_TASK,
            output_schema=CharacterOutput,
            agent_id=_AGENT_ID,
            turn_id=turn_id,
            idempotency_key=f"character:{perception.actor_id}:{turn_id}",
            model_tier=ModelTier.ECONOMY,
            messages=[
                ChatMessage(role="system", content=system[:_MAX_PROMPT_CHARS]),
                ChatMessage(role="user", content="请输出本回合的提案 JSON。"),
            ],
            timeout_s=20.0,
            max_repair=1,
            prompt_version=PROMPT_VERSION,
        )
