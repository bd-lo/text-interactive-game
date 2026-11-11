"""自由制造运行期开关 / 本机持久化 / 共享接线单元测试。"""

from __future__ import annotations

import json
from pathlib import Path

from textgame.domain.crafting import CraftMode, CraftRequest
from textgame.domain.models import PerceptionSlice
from textgame.domain.results import ErrorCode, Result
from textgame.domain.scenario import PLAYER_ID
from textgame.orchestration.intent import DeterministicIntentParser
from textgame.orchestration.turn_orchestrator import CreateGameRequest
from textgame.persistence.free_craft_settings import FreeCraftSettingsStore
from textgame.services.craft_service import CraftService
from textgame.services.free_craft import FreeCraftSwitch
from textgame.services.tool_context import ToolContext


def test_switch_updates_shared_parser_without_rebuild() -> None:
    """R-19 前端开关：同一 switch 切换后，确定性解析器立即产出/停止 free 意图。"""
    switch = FreeCraftSwitch()
    parser = DeterministicIntentParser(recipe_names={}, free_craft_switch=switch)
    perception = PerceptionSlice(actor_id=PLAYER_ID)

    closed = parser.parse("打造一把抓钩", perception=perception)
    switch.set_enabled(True, risk_accepted=True)
    opened = parser.parse("打造一把抓钩", perception=perception)

    assert closed.tool is None
    assert opened.tool == "craft_item"
    assert opened.args["mode"] == "free"


def test_switch_updates_shared_craft_service_gate(m4_game_harness) -> None:
    """同一 switch 也被 CraftService 实时读取：关闭 → NOT_ALLOWED，开启 → 进入生成器。"""
    view = m4_game_harness.orchestrator.create_game(
        CreateGameRequest(seed=1, idempotency_key="free-switch-create-0001", scenario="m4")
    )
    record = m4_game_harness.repo.load(view.game_id)
    assert record is not None
    switch = FreeCraftSwitch()

    class _UnavailableGenerator:
        def generate(self, *args: object, **kwargs: object) -> Result:
            del args, kwargs
            return Result.fail(ErrorCode.LLM_UNAVAILABLE, "stub generator offline")

    craft = CraftService(
        m4_game_harness.catalog,
        generator=_UnavailableGenerator(),
        free_craft_switch=switch,
    )
    context = ToolContext(
        actor_id=PLAYER_ID,
        turn_id=record.state.turn + 1,
        save_seed=record.state.rng_state.save_seed,
        idempotency_key="free-switch-craft-0001",
        caller="player",
    )
    request = CraftRequest(mode=CraftMode.FREE, free_description="做一个抓钩")

    closed = craft.craft(context, record.state, request, idempotency_key="free-switch-craft-0001")
    switch.set_enabled(True, risk_accepted=True)
    opened = craft.craft(context, record.state, request, idempotency_key="free-switch-craft-0001")

    assert closed.ok is False and closed.error_code is ErrorCode.NOT_ALLOWED
    assert opened.ok is False and opened.error_code is ErrorCode.LLM_UNAVAILABLE


def test_settings_store_roundtrip_and_atomic_file(tmp_path: Path) -> None:
    """保存后读回一致；目录内不残留临时文件，JSON 键集合稳定。"""
    path = tmp_path / "nested" / "free_craft.json"
    store = FreeCraftSettingsStore(path)

    assert store.load() is None
    saved = store.save(enabled=True, risk_accepted=True)
    loaded = store.load()

    assert loaded is not None
    assert (loaded.enabled, loaded.risk_accepted) == (True, True)
    assert loaded.updated_at == saved.updated_at
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload) == {"enabled", "risk_accepted", "updated_at"}
    assert list(path.parent.glob("*.tmp")) == []


def test_settings_store_corrupt_or_invalid_falls_back_to_none(tmp_path: Path) -> None:
    """损坏 JSON / 字段类型错误 → None（组合根会回退到安全默认值，不意外开启）。"""
    path = tmp_path / "free_craft.json"
    store = FreeCraftSettingsStore(path)

    path.write_text("{not-json", encoding="utf-8")
    assert store.load() is None
    path.write_text('{"enabled": "yes", "risk_accepted": true}', encoding="utf-8")
    assert store.load() is None
    path.write_text('{"enabled": true}', encoding="utf-8")
    assert store.load() is None
