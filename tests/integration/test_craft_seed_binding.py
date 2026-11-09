"""D-M4-14 回归：生产同构装配下，制造种子/实例 ID 必须绑定「本存档」真实 save_seed。

背景：`Resolver` 曾用组合根默认种子（save-42）构造 `ToolContext`，导致显式指定非 42 seed
的 m4 存档中 `CraftService` 派生种子/实例 ID 与 `EventLog.stamp` 的派生不一致（golden 固定
seed=42 且默认种子恰好等于存档种子而未暴露）。本文件用生产同构 harness（Resolver 链路）
钉死该回归。
"""

from __future__ import annotations

from typing import Any

from textgame.domain.events import EventType
from textgame.domain.hashing import state_hash
from textgame.domain.reducers import apply_events
from textgame.domain.rules.types import RandomSource

_M4_SEED = 123456


def _create(harness: Any, seed: int, key: str) -> dict[str, Any]:
    response = harness.client.post(
        "/api/v1/games",
        json={"seed": seed, "scenario": "m4"},
        headers={"Idempotency-Key": key},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _turn(harness: Any, game_id: str, text: str, version: int, key: str) -> dict[str, Any]:
    response = harness.client.post(
        "/api/v1/turns",
        json={"game_id": game_id, "input": text, "expected_version": version},
        headers={"Idempotency-Key": key},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _play_to_craft(harness: Any, seed: int, key_prefix: str) -> tuple[str, list[Any], Any]:
    created = _create(harness, seed, f"{key_prefix}-create")
    game_id = created["game_id"]
    version = created["version"]
    for index, text in enumerate(("向东走", "打造铁剑"), start=1):
        body = _turn(harness, game_id, text, version, f"{key_prefix}-turn-{index}")
        assert body["ok"] is True, body
        version = body["version"]
    record = harness.repo.load(game_id)
    assert record is not None
    return game_id, harness.repo.read_events(game_id), record.state


def _craft_events(events: list[Any]) -> list[Any]:
    return [
        event
        for event in events
        if event.event_type in (EventType.CRAFT_DETERMINISTIC, EventType.CRAFT_GENERATED)
    ]


def test_craft_event_rng_seed_binds_to_save_seed(m4_prod_harness: Any) -> None:
    """非默认 seed：盖章 rng_seed == result.craft.rng_seed == 由存档 save_seed 派生。"""
    _, events, state = _play_to_craft(m4_prod_harness, _M4_SEED, "seed-bind")
    craft_events = _craft_events(events)
    assert len(craft_events) == 1
    event = craft_events[0]
    craft = event.result["craft"]
    save_seed = state.rng_state.save_seed
    expected = RandomSource.derive_seed(
        save_seed, event.turn_id, event.actor_id, "craft_item", event.seq
    )
    assert event.rng_seed == craft["rng_seed"] == expected


_CRAFT_SCRIPT = ("拿起火把", "向东走进铁匠铺", "打造铁剑")  # 与 m4 golden 成功 case 同脚本


def _produced_ids(events: list[Any]) -> list[str]:
    return [
        item["id"] for event in _craft_events(events) for item in event.result["produced_items"]
    ]


def _play_craft_script(harness: Any, seed: int, key_prefix: str) -> list[str]:
    created = _create(harness, seed, f"{key_prefix}-create")
    game_id = created["game_id"]
    version = created["version"]
    for index, text in enumerate(_CRAFT_SCRIPT, start=1):
        body = _turn(harness, game_id, text, version, f"{key_prefix}-turn-{index}")
        assert body["ok"] is True, body
        version = body["version"]
    record = harness.repo.load(game_id)
    assert record is not None
    return _produced_ids(harness.repo.read_events(game_id))


def test_craft_instance_ids_depend_on_save_seed(m4_prod_harness: Any) -> None:
    """同输入下不同存档 seed 必须产出不同实例 ID（ID 由 save_seed 参与派生）。

    seed=42 用 golden 同款脚本（已钉死成功）；自定义 seed 扫描到一次成功产出后比较。
    """
    ids_default = _play_craft_script(m4_prod_harness, 42, "seed-42")
    assert ids_default, "default-seed craft should succeed with the golden script"
    ids_custom: list[str] = []
    for offset in range(20):
        candidate = 123_450 + offset
        produced = _play_craft_script(m4_prod_harness, candidate, f"seed-custom-{candidate}")
        if produced:
            ids_custom = produced
            break
    assert ids_custom, "no custom-seed craft succeeded within scan window"
    assert ids_default != ids_custom


def test_nondefault_seed_replay_matches_current_state(m4_prod_harness: Any) -> None:
    """非默认 seed 存档：事件全量重建的 state_hash 与当前状态一致（回放不依赖默认种子）。"""
    _, events, state = _play_to_craft(m4_prod_harness, _M4_SEED, "seed-replay")
    rebuilt = apply_events(None, events)
    assert state_hash(rebuilt) == state_hash(state)
