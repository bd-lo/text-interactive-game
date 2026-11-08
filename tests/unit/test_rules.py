"""单元测试: RuleEngine 的 L0 前置检查与 d20 判定 (M2 切片 007 验收文件)。

对应第 10 章 10.1 (L0 硬约束)、10.2 (d20/固定 DC 档/自然 20 与自然 1)、10.3 (派生式随机
与 rng_seed 掩码)、第 5 章 5.6 (CheckResult 值对象)、第 18 章 18.2 (确定性单测口径)。
固定 save_seed 与 stub ``RandomSource``, 不读时钟、不依赖全局随机、不做任何 IO。
"""

from collections.abc import Sequence
from typing import Any, TypeVar

import pytest
from pydantic import ValidationError

from textgame.domain.models import NPC, Character, GameState, Item, RngState, Room
from textgame.domain.results import ErrorCode
from textgame.domain.rules.checks import (
    D20_SIDES,
    DC_TIERS,
    difficulty_for,
    outcome_for,
    sum_modifiers,
    validate_dc,
)
from textgame.domain.rules.dice import DerivedRandom
from textgame.domain.rules.engine import BACKPACK_CAPACITY, RuleEngine
from textgame.domain.rules.ruleset import (
    M2_RULESET,
    RULESET_VERSION,
    RuleDefinition,
    Ruleset,
)
from textgame.domain.rules.types import Modifier

_SAVE_SEED = "save-42"
_RNG_SEED_MAX = 0x7FFF_FFFF_FFFF_FFFF

_ULID_ENTRY = "01J9Z3K4Q7X8Y9Z0ABCDEFGHJK"
_ROOM_ENTRY = f"room_{_ULID_ENTRY}"
_ROOM_CELLAR = "room_01J9Z3K4Q7X8Y9Z0ABCDEFGHJM"
_PLAYER = "char_01J9Z3K4Q7X8Y9Z0ABCDEFGHJN"
_OTHER_ACTOR = "char_01J9Z3K4Q7X8Y9Z0ABCDEFGHJV"
_NPC = "npc_01J9Z3K4Q7X8Y9Z0ABCDEFGHJP"
_ITEM_TORCH = "item_01J9Z3K4Q7X8Y9Z0ABCDEFGHJQ"
_ITEM_POTION = "item_01J9Z3K4Q7X8Y9Z0ABCDEFGHJR"
_ITEM_SWORD = "item_01J9Z3K4Q7X8Y9Z0ABCDEFGHJS"
_ITEM_RELIC = "item_01J9Z3K4Q7X8Y9Z0ABCDEFGHJT"

_T = TypeVar("_T")


class _FixedRandom:
    """实现 ``RandomSource`` 的测试 stub: ``randint`` 恒返回固定骰面。"""

    def __init__(self, face: int) -> None:
        self._face = face
        self.bounds: list[tuple[int, int]] = []

    def randint(self, a: int, b: int) -> int:
        self.bounds.append((a, b))
        return self._face

    def choice(self, seq: Sequence[_T]) -> _T:
        raise NotImplementedError("check() 只用 randint")


# ---------------------------------------------------------------------------
# 测试夹具: 内置单场景的 GameState (第 14 章 14.7 / M2 契约 §3.4 的最小复刻)
# ---------------------------------------------------------------------------


def _base_items() -> dict[str, Item]:
    return {
        _ITEM_TORCH: Item(
            id=_ITEM_TORCH, name="火把", description="还在燃烧的火把", tags=["light"]
        ),
        _ITEM_POTION: Item(id=_ITEM_POTION, name="治疗药水", description="散发草药味"),
        _ITEM_SWORD: Item(id=_ITEM_SWORD, name="锈剑", description="锈迹斑斑的剑"),
        _ITEM_RELIC: Item(id=_ITEM_RELIC, name="古物", description="不知年代的物件"),
    }


def _player(*, inventory: list[str] | None = None) -> Character:
    return Character(
        id=_PLAYER,
        name="旅人",
        attributes={"str": 12, "dex": 14, "con": 12, "int": 10, "wis": 11, "cha": 9, "max_hp": 20},
        inventory=[] if inventory is None else inventory,
        location=_ROOM_ENTRY,
        hp=20,
        created_turn=1,
    )


def _entries() -> dict[str, Room]:
    return {
        _ROOM_ENTRY: Room(
            id=_ROOM_ENTRY,
            name="废弃哨站·入口",
            description="碎石与枯草间立着一座半塌的哨塔。",
            exits={"north": _ROOM_CELLAR},
            item_ids=[_ITEM_TORCH, _ITEM_POTION],
            npc_ids=[_NPC],
        ),
        _ROOM_CELLAR: Room(
            id=_ROOM_CELLAR,
            name="哨站地窖",
            description="潮湿的台阶通向地窖深处，角落里堆着旧木箱。",
            exits={"south": _ROOM_ENTRY},
            item_ids=[_ITEM_SWORD, _ITEM_RELIC],
        ),
    }


def _state(
    *,
    player: Character | None = None,
    rooms: dict[str, Room] | None = None,
    items: dict[str, Item] | None = None,
    npcs: dict[str, NPC] | None = None,
) -> GameState:
    default_npcs = {
        _NPC: NPC(id=_NPC, name="老哨兵", room_id=_ROOM_ENTRY, memory_ref="npc_sentinel")
    }
    return GameState(
        turn=1,
        world_time=0,
        characters={_PLAYER: _player() if player is None else player},
        rooms=_entries() if rooms is None else rooms,
        items=_base_items() if items is None else items,
        npcs=default_npcs if npcs is None else npcs,
        quests={},
        rng_state=RngState(save_seed=_SAVE_SEED),
    )


_FULL_INVENTORY_IDS = [f"item_01J9Z3K4Q7X8Y9Z0ABCDEF{index:04d}" for index in range(64)]


def _state_with_full_backpack() -> GameState:
    filler = {
        item_id: Item(id=item_id, name=f"杂物{index}", description="")
        for index, item_id in enumerate(_FULL_INVENTORY_IDS)
    }
    return _state(
        player=_player(inventory=list(_FULL_INVENTORY_IDS)), items={**_base_items(), **filler}
    )


def _preconditions(
    state: GameState | None = None,
    *,
    tool: str = "look",
    args: dict[str, Any] | None = None,
    actor_id: str = _PLAYER,
) -> Any:
    return RuleEngine().evaluate_preconditions(
        tool=tool,
        args={} if args is None else args,
        actor_id=actor_id,
        turn_id=1,
        save_seed=_SAVE_SEED,
        state=_state() if state is None else state,
    )


def _check_kwargs(**overrides: object) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "rule_id": "stealth.sneak_v1",
        "dc": 15,
        "modifiers": [Modifier(source="dexterity", value=3)],
        "save_seed": _SAVE_SEED,
        "turn_id": 7,
        "actor_id": _PLAYER,
        "seq": 3,
    }
    kwargs.update(overrides)
    return kwargs


# ---------------------------------------------------------------------------
# checks.py 纯函数
# ---------------------------------------------------------------------------


def test_dc_tiers_are_fixed_by_chapter_10() -> None:
    """DC 固定档为 5/10/15/20/25, 每档均通过校验。"""
    assert frozenset({5, 10, 15, 20, 25}) == DC_TIERS
    for dc in DC_TIERS:
        assert validate_dc(dc) == dc


@pytest.mark.parametrize("dc", [0, 1, 4, 12, 26, 100])
def test_validate_dc_rejects_non_tier_values(dc: int) -> None:
    """非固定档位 (含第 9 章示例的 12) 一律 ValueError。"""
    with pytest.raises(ValueError, match="dc must be one of"):
        validate_dc(dc)


@pytest.mark.parametrize(
    ("dc", "label"), [(5, "琐碎"), (10, "简单"), (15, "普通"), (20, "困难"), (25, "极难")]
)
def test_difficulty_for_maps_tiers(dc: int, label: str) -> None:
    """DC 档位 → 第 10 章 10.2 难度表中文名。"""
    assert difficulty_for(dc) == label


def test_sum_modifiers() -> None:
    """修正求和按来源逐项相加。"""
    modifiers = [Modifier(source="dexterity", value=3), Modifier(source="darkness", value=-2)]

    assert sum_modifiers(modifiers) == 1
    assert sum_modifiers([]) == 0


@pytest.mark.parametrize(
    ("natural", "total", "dc", "expected"),
    [
        (20, 5, 25, "critical_success"),
        (1, 400, 5, "critical_failure"),
        (10, 15, 15, "success"),
        (9, 14, 15, "failure"),
        (2, 100, 5, "success"),
    ],
)
def test_outcome_for_grades(natural: int, total: int, dc: int, expected: str) -> None:
    """自然 20/自然 1 优先于总值比较, 其余按 total >= dc 判成功。"""
    assert outcome_for(natural=natural, total=total, dc=dc) == expected


@pytest.mark.parametrize("natural", [0, 21, -1])
def test_outcome_for_rejects_impossible_roll(natural: int) -> None:
    """自然骰越出 1..20 属实现 bug, 直接 ValueError。"""
    with pytest.raises(ValueError, match="natural roll"):
        outcome_for(natural=natural, total=10, dc=10)


# ---------------------------------------------------------------------------
# Ruleset 数据
# ---------------------------------------------------------------------------


def test_ruleset_version_and_entries() -> None:
    """内置规则集版本为 m2-1, 含任务要求的两条规则且 DC 均为固定档。"""
    assert RULESET_VERSION == "m2-1"
    assert M2_RULESET.ruleset_version == "m2-1"
    assert M2_RULESET.require("stealth.sneak_v1").dc == 15
    assert M2_RULESET.require("perception.track_v1").dc == 10
    for rule in M2_RULESET.rules.values():
        assert rule.dc in DC_TIERS


def test_ruleset_get_and_require_unknown() -> None:
    """get 对未知规则返回 None; require 对未知规则 ValueError。"""
    assert M2_RULESET.get("no.such_rule") is None
    with pytest.raises(ValueError, match="unknown rule_id"):
        M2_RULESET.require("no.such_rule")


@pytest.mark.parametrize("dc", [4, 12, 26])
def test_rule_definition_rejects_non_tier_dc(dc: int) -> None:
    """RuleDefinition 在构造期就拒绝非固定档 DC。"""
    with pytest.raises(ValidationError):
        RuleDefinition(rule_id="test.rule_v1", dc=dc, description="test")


def test_ruleset_rejects_key_rule_id_mismatch() -> None:
    """rules 字典键必须等于条目自身的 rule_id。"""
    rule = RuleDefinition(rule_id="stealth.sneak_v1", dc=15, description="潜行")

    with pytest.raises(ValidationError, match="rule_id"):
        Ruleset(rules={"other.rule_v1": rule})


def test_ruleset_rejects_unknown_version_format() -> None:
    """ruleset_version 必须是 ``m2-<数字>``。"""
    with pytest.raises(ValidationError):
        Ruleset(ruleset_version="v1", rules={})


# ---------------------------------------------------------------------------
# RuleEngine.check: 确定性、派生种子、d20 分级
# ---------------------------------------------------------------------------


def test_check_is_deterministic_for_same_inputs() -> None:
    """同一 (种子, 回合, actor, rule, seq, dc, modifiers) 两次判定完全一致。"""
    engine = RuleEngine()

    first = engine.check(**_check_kwargs())
    second = engine.check(**_check_kwargs())

    assert first == second
    assert first.rng_seed == second.rng_seed
    assert first.dice == second.dice


def test_check_rng_seed_matches_derive_seed() -> None:
    """rng_seed 等于 DerivedRandom.derive_seed 的结果且落在 0..2**63-1。"""
    result = RuleEngine().check(**_check_kwargs())
    expected = DerivedRandom.derive_seed(_SAVE_SEED, 7, _PLAYER, "stealth.sneak_v1", 3)

    assert result.rng_seed == expected
    assert 0 <= result.rng_seed <= _RNG_SEED_MAX


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("seq", 4),
        ("turn_id", 8),
        ("actor_id", _OTHER_ACTOR),
        ("rule_id", "perception.track_v1"),
    ],
)
def test_check_seed_changes_with_derivation_context(field: str, value: object) -> None:
    """seq/turn_id/actor_id/rule_id 任一变化都会派生出不同子种子。"""
    engine = RuleEngine()

    base = engine.check(**_check_kwargs())
    changed = engine.check(**_check_kwargs(**{field: value}))

    assert changed.rng_seed != base.rng_seed


def test_check_same_seed_produces_same_dice() -> None:
    """同一派生种子的两个 DerivedRandom 实例掷出同一骰面。"""
    seed = DerivedRandom.derive_seed(_SAVE_SEED, 7, _PLAYER, "stealth.sneak_v1", 3)
    engine = RuleEngine()

    first = engine.check(**_check_kwargs(rng=DerivedRandom(seed)))
    second = engine.check(**_check_kwargs(rng=DerivedRandom(seed)))

    assert first.dice == second.dice
    assert first.total == second.total
    assert first.outcome == second.outcome


def test_check_natural_20_is_critical_success() -> None:
    """自然 20 必为大成功 (即使 total < dc)。"""
    result = RuleEngine().check(
        **_check_kwargs(dc=25, modifiers=[], rng=_FixedRandom(20)),
    )

    assert result.dice[0].rolls == [20]
    assert result.total == 20
    assert result.outcome == "critical_success"


def test_check_natural_1_is_critical_failure() -> None:
    """自然 1 必为大失败 (即使 total >= dc)。"""
    result = RuleEngine().check(
        **_check_kwargs(dc=5, modifiers=[Modifier(source="luck", value=100)], rng=_FixedRandom(1)),
    )

    assert result.dice[0].rolls == [1]
    assert result.total == 101
    assert result.outcome == "critical_failure"


@pytest.mark.parametrize(
    ("natural", "dc", "expected"),
    [(10, 10, "success"), (9, 10, "failure"), (15, 15, "success"), (14, 15, "failure")],
)
def test_check_total_against_dc(natural: int, dc: int, expected: str) -> None:
    """非自然 1/20 时按 total >= dc 分级。"""
    result = RuleEngine().check(**_check_kwargs(dc=dc, modifiers=[], rng=_FixedRandom(natural)))

    assert result.outcome == expected


def test_check_total_sums_modifiers() -> None:
    """total = d20 + 全部修正之和, 修正顺序照原样记录。"""
    modifiers = [Modifier(source="dexterity", value=3), Modifier(source="darkness", value=2)]
    result = RuleEngine().check(**_check_kwargs(dc=15, modifiers=modifiers, rng=_FixedRandom(12)))

    assert result.total == 12 + 3 + 2
    assert result.modifiers == modifiers


def test_check_records_value_object_fields() -> None:
    """CheckResult 字段与第 5 章 5.6 定义一致 (单颗 d20, actor/target 原样记录)。"""
    result = RuleEngine().check(**_check_kwargs(target="guard_a", rng=_FixedRandom(12)))

    assert result.rule_id == "stealth.sneak_v1"
    assert result.actor_id == _PLAYER
    assert result.target == "guard_a"
    assert result.dc == 15
    assert len(result.dice) == 1
    dice = result.dice[0]
    assert dice.sides == D20_SIDES
    assert dice.rolls == dice.kept == [12]


@pytest.mark.parametrize("dc", [0, 1, 4, 12, 26, 100])
def test_check_rejects_invalid_dc(dc: int) -> None:
    """check 的 dc 必须是 5/10/15/20/25 固定档, 否则 ValueError。"""
    stub = _FixedRandom(10)

    with pytest.raises(ValueError, match="dc must be one of"):
        RuleEngine().check(**_check_kwargs(dc=dc, rng=stub))

    assert stub.bounds == []  # 非法 dc 在掷骰前就被拒绝


def test_check_rule_uses_ruleset_defaults() -> None:
    """check_rule 从 Ruleset 取固定 DC 与默认修正, 与显式 check 结果一致。"""
    engine = RuleEngine()
    kwargs = {
        "save_seed": _SAVE_SEED,
        "turn_id": 7,
        "actor_id": _PLAYER,
        "seq": 3,
    }

    result = engine.check_rule(rule_id="stealth.sneak_v1", **kwargs)
    explicit = engine.check(
        rule_id="stealth.sneak_v1",
        dc=15,
        modifiers=[Modifier(source="dexterity", value=3)],
        **kwargs,
    )

    assert result == explicit
    assert result.dc == 15
    assert result.modifiers == [Modifier(source="dexterity", value=3)]


def test_check_rule_appends_additional_modifiers() -> None:
    """additional_modifiers 叠加在规则默认修正之后。"""
    result = RuleEngine().check_rule(
        rule_id="stealth.sneak_v1",
        save_seed=_SAVE_SEED,
        turn_id=7,
        actor_id=_PLAYER,
        seq=3,
        additional_modifiers=[Modifier(source="darkness", value=2)],
        rng=_FixedRandom(12),
    )

    assert result.modifiers == [
        Modifier(source="dexterity", value=3),
        Modifier(source="darkness", value=2),
    ]


def test_check_rule_rejects_unknown_rule() -> None:
    """未登记的 rule_id → ValueError。"""
    with pytest.raises(ValueError, match="unknown rule_id"):
        RuleEngine().check_rule(
            rule_id="no.such_rule",
            save_seed=_SAVE_SEED,
            turn_id=1,
            actor_id=_PLAYER,
            seq=1,
        )


def test_engine_uses_injected_ruleset() -> None:
    """RuleEngine 可注入自定义 Ruleset (数据与逻辑解耦)。"""
    custom = Ruleset(
        rules={
            "perception.track_v1": RuleDefinition(
                rule_id="perception.track_v1",
                dc=25,
                modifiers=[Modifier(source="wisdom", value=1)],
                description="自定义规则",
            )
        }
    )
    engine = RuleEngine(ruleset=custom)

    result = engine.check_rule(
        rule_id="perception.track_v1",
        save_seed=_SAVE_SEED,
        turn_id=1,
        actor_id=_PLAYER,
        seq=1,
    )

    assert engine.ruleset is custom
    assert result.dc == 25
    assert result.modifiers == [Modifier(source="wisdom", value=1)]


# ---------------------------------------------------------------------------
# RuleEngine.evaluate_preconditions: L0 硬约束 (第 10 章 10.1)
# ---------------------------------------------------------------------------


def test_look_is_always_allowed() -> None:
    """look 恒允许: 空参数、任意 actor 都通过。"""
    result = _preconditions(tool="look")

    assert result.ok is True
    assert result.data == {"allowed": True, "reason": ""}
    assert result.events == []


def test_look_allowed_even_with_unknown_actor() -> None:
    """look 为只读工具, 不做 actor 存在性前置检查。"""
    result = _preconditions(tool="look", actor_id="char_missing")

    assert result.ok is True


def test_unknown_tool_is_not_allowed() -> None:
    """未知工具 → NOT_ALLOWED (白名单外的调用不进判定)。"""
    result = _preconditions(tool="admin_override", args={})

    assert result.ok is False
    assert result.error_code is ErrorCode.NOT_ALLOWED
    assert result.events == []


def test_move_to_allowed_when_exit_exists() -> None:
    """move_to 方向存在出口 → 允许。"""
    result = _preconditions(tool="move_to", args={"direction": "north"})

    assert result.ok is True


@pytest.mark.parametrize("direction", ["south", "east", "up", "northwest", 123, None])
def test_move_to_rejects_missing_or_invalid_direction(direction: object) -> None:
    """无该方向出口或方向值非法 → NOT_FOUND。"""
    result = _preconditions(tool="move_to", args={"direction": direction})

    assert result.ok is False
    assert result.error_code is ErrorCode.NOT_FOUND


def test_move_to_without_args_is_not_found() -> None:
    """args 缺少 direction → NOT_FOUND。"""
    result = _preconditions(tool="move_to", args={})

    assert result.ok is False
    assert result.error_code is ErrorCode.NOT_FOUND


def test_move_to_rejects_unknown_actor() -> None:
    """写工具要求 actor 存在, 未知 actor → NOT_FOUND。"""
    result = _preconditions(tool="move_to", args={"direction": "north"}, actor_id="char_missing")

    assert result.ok is False
    assert result.error_code is ErrorCode.NOT_FOUND


def test_take_item_allowed_from_current_room() -> None:
    """物品在当前房间且背包未满 → 允许。"""
    result = _preconditions(tool="take_item", args={"item_id": _ITEM_TORCH})

    assert result.ok is True


def test_take_item_rejects_item_not_in_room() -> None:
    """物品只在别的房间 (锈剑在地窖) → NOT_FOUND。"""
    result = _preconditions(tool="take_item", args={"item_id": _ITEM_SWORD})

    assert result.ok is False
    assert result.error_code is ErrorCode.NOT_FOUND


def test_take_item_rejects_unknown_item() -> None:
    """不存在的 item_id → NOT_FOUND。"""
    result = _preconditions(tool="take_item", args={"item_id": "item_missing"})

    assert result.ok is False
    assert result.error_code is ErrorCode.NOT_FOUND


def test_take_item_rejects_full_backpack() -> None:
    """背包已满 (>=64) → OUT_OF_RANGE。"""
    result = _preconditions(
        _state_with_full_backpack(), tool="take_item", args={"item_id": _ITEM_TORCH}
    )

    assert result.ok is False
    assert result.error_code is ErrorCode.OUT_OF_RANGE
    assert len(_FULL_INVENTORY_IDS) == BACKPACK_CAPACITY


def test_use_item_allowed_from_inventory() -> None:
    """物品在背包 → 允许。"""
    state = _state(player=_player(inventory=[_ITEM_POTION]))

    result = _preconditions(state, tool="use_item", args={"item_id": _ITEM_POTION})

    assert result.ok is True


def test_use_item_rejects_item_not_in_inventory() -> None:
    """物品不在背包 (即使房间里有) → NOT_FOUND。"""
    result = _preconditions(tool="use_item", args={"item_id": _ITEM_POTION})

    assert result.ok is False
    assert result.error_code is ErrorCode.NOT_FOUND


def test_talk_to_allowed_when_npc_in_same_room() -> None:
    """NPC 与 actor 同房间 → 允许。"""
    result = _preconditions(tool="talk_to", args={"npc_id": _NPC})

    assert result.ok is True


def test_talk_to_rejects_npc_in_other_room() -> None:
    """NPC 在别的房间 → NOT_FOUND。"""
    npcs = {_NPC: NPC(id=_NPC, name="老哨兵", room_id=_ROOM_CELLAR, memory_ref="npc_sentinel")}
    state = _state(npcs=npcs)

    result = _preconditions(state, tool="talk_to", args={"npc_id": _NPC})

    assert result.ok is False
    assert result.error_code is ErrorCode.NOT_FOUND


def test_talk_to_rejects_unknown_npc() -> None:
    """不存在的 npc_id → NOT_FOUND。"""
    result = _preconditions(tool="talk_to", args={"npc_id": "npc_missing"})

    assert result.ok is False
    assert result.error_code is ErrorCode.NOT_FOUND


def test_preconditions_are_deterministic_100_runs() -> None:
    """同一 state/args 调 100 次 evaluate_preconditions, 结果完全相同。"""
    engine = RuleEngine()
    state = _state()
    args = {"direction": "north"}

    results = [
        engine.evaluate_preconditions(
            tool="move_to",
            args=args,
            actor_id=_PLAYER,
            turn_id=1,
            save_seed=_SAVE_SEED,
            state=state,
        )
        for _ in range(100)
    ]

    assert all(result == results[0] for result in results)
    assert results[0].ok is True
