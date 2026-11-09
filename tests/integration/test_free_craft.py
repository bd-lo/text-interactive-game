"""集成测试：M4-017 自由制造校验与回滚（第 13 章 §13.4、第 8 章 §8.2、D-M4-3/5/7）。

装配为 M2 同构单角色 + scenario=m4 + CraftService(free_craft_enabled=True,
generator=CraftAgent(FakeLLM))；FreeCraftIntentParser 把输入映射为 craft_item(mode=free)。
覆盖：合法草案提交、事件无 LLM 数值、一次修复重试、两类 Schema/一致性失败零写入、
未知材料/qty 超限/他人所有物/工位不符拒绝、默认关闭 NOT_ALLOWED、LLM 不可用、
free_description 超长 INVALID_INPUT、事件 proposal_hash/prompt_version。固定 seed=1
（首回合自由制造确定性成功，base_success_rate 由框架固定为 0.5，D-M4-7）。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest
from pydantic import ValidationError

from textgame.domain.crafting import CraftMode, CraftRequest
from textgame.domain.hashing import params_hash, state_hash
from textgame.domain.results import ErrorCode
from textgame.domain.scenario import PLAYER_ID
from textgame.orchestration.dto import TurnRequest
from textgame.orchestration.turn_orchestrator import CreateGameRequest
from textgame.services.tool_context import ToolContext
from textgame.tools.builtin import build_game_tool_registry

_SEED = 1
_CREATE_KEY = "free-017-create-0001"
_TURN_KEY = "free-017-turn-0001"
_TEXT = "把铁锭和铜线做成一个简易电击棒"


def _draft(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "item_name": "简易电击棒",
        "output_template_id": "item.makeshift_taser",
        "inputs": [
            {"template_id": "item.iron_ingot", "qty": 1, "role": "material"},
            {"template_id": "item.copper_wire", "qty": 1, "role": "material"},
        ],
        "station": None,
        "steps": ["把铜线缠在铁锭上", "固定接头"],
    }
    payload.update(overrides)
    return payload


def _create(harness: Any) -> str:
    view = harness.orchestrator.create_game(
        CreateGameRequest(seed=_SEED, idempotency_key=_CREATE_KEY, scenario="m4")
    )
    return view.game_id


def _turn(harness: Any, game_id: str, *, text: str = _TEXT, expected_version: int = 1):
    return harness.orchestrator.run(
        TurnRequest(
            game_id=game_id,
            text=text,
            expected_version=expected_version,
            idempotency_key=_TURN_KEY,
        )
    )


def _record(harness: Any, game_id: str):
    record = harness.repo.load(game_id)
    assert record is not None
    return record


def _ctx(state: Any, *, key: str = "free-017-sec-0001") -> ToolContext:
    return ToolContext(
        actor_id=PLAYER_ID,
        actor_tags=frozenset({"player"}),
        turn_id=state.turn + 1,
        save_seed=state.rng_state.save_seed,
        idempotency_key=key,
        caller="player",
    )


def test_free_craft_valid_draft_commits_generated_event(m4_free_harness: Any) -> None:
    game_id = _create(m4_free_harness)
    before = _record(m4_free_harness, game_id)
    m4_free_harness.fake_llm.reset([_draft()])

    result = _turn(m4_free_harness, game_id)

    assert result.ok is True
    assert result.version == before.version + 1
    assert 80 <= len(result.text) <= 150
    assert len(result.events) == 1
    event = result.events[0]
    assert event.event_type is not None and event.event_type.value == "CRAFT_GENERATED"
    assert event.rng_seed == event.result["craft"]["rng_seed"]
    after = _record(m4_free_harness, game_id)
    inventory = after.state.characters[PLAYER_ID].inventory
    names = [after.state.items[item_id].name for item_id in inventory]
    assert "简易电击棒" in names
    assert len(inventory) == len(before.state.characters[PLAYER_ID].inventory) - 2 + 1


def test_free_craft_event_has_no_llm_numbers(m4_free_harness: Any) -> None:
    game_id = _create(m4_free_harness)
    m4_free_harness.fake_llm.reset(
        [_draft(inputs=[{"template_id": "item.iron_ingot", "qty": 1, "role": "material"}])]
    )

    result = _turn(m4_free_harness, game_id)

    event = result.events[0]
    craft = event.result["craft"]
    assert set(craft) == {
        "ok",
        "success",
        "consumed",
        "produced",
        "byproducts",
        "quality",
        "rng_seed",
        "reason",
    }
    assert craft["quality"] in {"common", "fine"}
    produced = event.result["produced_items"]
    assert produced
    assert produced[0]["stats"] == {}
    assert "base_success_rate" not in json.dumps(event.model_dump(mode="json"))


def test_free_craft_first_invalid_then_repaired_commits(m4_free_harness: Any) -> None:
    game_id = _create(m4_free_harness)
    m4_free_harness.fake_llm.reset(["not-json", _draft()])

    result = _turn(m4_free_harness, game_id)

    assert result.ok is True
    assert len(m4_free_harness.fake_llm.calls) == 2
    assert len(m4_free_harness.fake_llm.calls) == 2
    event = result.events[0]
    assert event.params["prompt_version"] == "craft@v1"


def test_free_craft_two_invalid_rolls_back_zero_writes(m4_free_harness: Any) -> None:
    game_id = _create(m4_free_harness)
    before = _record(m4_free_harness, game_id)
    before_hash = state_hash(before.state)
    before_events = len(m4_free_harness.repo.read_events(game_id))
    m4_free_harness.fake_llm.reset(["not-json", "still-not-json"])

    result = _turn(m4_free_harness, game_id)

    assert result.ok is False
    assert result.error_code is ErrorCode.SCHEMA_VIOLATION
    after = _record(m4_free_harness, game_id)
    assert after.version == before.version
    assert after.last_seq == before.last_seq
    assert state_hash(after.state) == before_hash
    assert len(m4_free_harness.repo.read_events(game_id)) == before_events


def test_free_craft_stats_field_rejected_by_schema(m4_free_harness: Any) -> None:
    game_id = _create(m4_free_harness)
    before = _record(m4_free_harness, game_id)
    attacked = _draft(stats={"attack": 99})
    m4_free_harness.fake_llm.reset([attacked, attacked])

    result = _turn(m4_free_harness, game_id)

    assert result.ok is False
    assert result.error_code is ErrorCode.SCHEMA_VIOLATION
    after = _record(m4_free_harness, game_id)
    assert after.version == before.version


def test_free_craft_unknown_material_rejected_zero_writes(m4_free_harness: Any) -> None:
    game_id = _create(m4_free_harness)
    before = _record(m4_free_harness, game_id)
    bad = _draft(inputs=[{"template_id": "item.unobtainium", "qty": 1, "role": "material"}])
    m4_free_harness.fake_llm.reset([bad])

    result = _turn(m4_free_harness, game_id)

    assert result.ok is False
    assert result.error_code is ErrorCode.NOT_ALLOWED
    after = _record(m4_free_harness, game_id)
    assert after.version == before.version
    assert after.last_seq == before.last_seq


def test_free_craft_qty_exceeds_inventory_rejected(m4_free_harness: Any) -> None:
    game_id = _create(m4_free_harness)
    before = _record(m4_free_harness, game_id)
    bad = _draft(inputs=[{"template_id": "item.iron_ingot", "qty": 99, "role": "material"}])
    m4_free_harness.fake_llm.reset([bad])

    result = _turn(m4_free_harness, game_id)

    assert result.ok is False
    assert result.error_code is ErrorCode.NOT_ALLOWED
    assert _record(m4_free_harness, game_id).version == before.version


def test_free_craft_other_actor_items_rejected(m4_free_harness: Any) -> None:
    game_id = _create(m4_free_harness)
    before = _record(m4_free_harness, game_id)
    bad = _draft(inputs=[{"template_id": "item.sword", "qty": 1, "role": "material"}])
    m4_free_harness.fake_llm.reset([bad])

    result = _turn(m4_free_harness, game_id)

    assert result.ok is False
    assert result.error_code is ErrorCode.NOT_ALLOWED
    assert _record(m4_free_harness, game_id).version == before.version


def test_free_craft_station_mismatch_rejected(m4_free_harness: Any) -> None:
    game_id = _create(m4_free_harness)
    bad = _draft(station="station.forge")
    m4_free_harness.fake_llm.reset([bad])

    result = _turn(m4_free_harness, game_id)

    assert result.ok is False
    assert result.error_code is ErrorCode.NOT_ALLOWED


def test_free_craft_disabled_by_default_rejected(m4_game_harness: Any) -> None:
    view = m4_game_harness.orchestrator.create_game(
        CreateGameRequest(seed=_SEED, idempotency_key="free-017-create-0002")
    )
    record = _record(m4_game_harness, view.game_id)

    result = m4_game_harness.craft.craft(
        _ctx(record.state),
        record.state,
        CraftRequest(mode=CraftMode.FREE, free_description=_TEXT),
        idempotency_key="free-017-sec-0002",
    )

    assert result.ok is False
    assert result.error_code is ErrorCode.NOT_ALLOWED
    assert result.events == []


def test_free_craft_llm_unavailable_zero_writes(m4_free_harness: Any) -> None:
    game_id = _create(m4_free_harness)
    before = _record(m4_free_harness, game_id)
    m4_free_harness.fake_llm.reset([RuntimeError("timeout")])

    result = _turn(m4_free_harness, game_id)

    assert result.ok is False
    assert result.error_code is ErrorCode.LLM_UNAVAILABLE
    after = _record(m4_free_harness, game_id)
    assert after.version == before.version
    assert after.last_seq == before.last_seq


def test_free_craft_oversized_description_rejected_invalid_input() -> None:
    registry = build_game_tool_registry()

    result = registry.validate_args(
        "craft_item",
        {"mode": "free", "free_description": "x" * 201},
    )

    assert result.ok is False
    assert result.error_code is ErrorCode.INVALID_INPUT
    assert result.events == []
    with pytest.raises(ValidationError):
        CraftRequest.model_validate({"mode": "free", "free_description": "x" * 201})


def test_free_craft_event_records_proposal_hash_and_prompt_version(m4_free_harness: Any) -> None:
    game_id = _create(m4_free_harness)
    m4_free_harness.fake_llm.reset([_draft()])
    description = _TEXT

    result = _turn(m4_free_harness, game_id, text=description)

    event = result.events[0]
    assert (
        event.params["free_description_sha256"]
        == hashlib.sha256(description.encode("utf-8")).hexdigest()
    )
    assert event.params["prompt_version"] == "craft@v1"
    assert len(event.params["proposal_hash"]) == 64
    expected_hash = params_hash({"draft": _draft(), "free_description": description})
    assert event.params["proposal_hash"] == expected_hash
    assert description not in json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
