"""单元测试: 派生式随机基座 (第 10 章 10.3、第 18 章 18.2、第 5 章 5.6)。

覆盖种子派生确定性、掩码边界、DerivedRandom 序列可复现与 ``config.rng`` 委托
一致性, 并扫描源码保证 ``domain/rules/dice.py`` 是全仓唯一 import ``random`` 的模块。
"""

import ast
from pathlib import Path

import pytest

from textgame.config.rng import DEFAULT_SEED_MASK, derive_event_seed, save_seed_from_settings
from textgame.domain.rules.dice import DerivedRandom
from textgame.domain.rules.types import RandomSource

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "textgame"
_DOC_EXAMPLE_SEED = 853400723015425181  # 第 10 章 10.2 示例 JSON 的 rng_seed


# ---------------------------------------------------------------------------
# 种子派生: 确定性、输入敏感、63 位掩码
# ---------------------------------------------------------------------------


def test_derive_seed_matches_doc_example() -> None:
    """第 10 章 10.2 示例输入必须派生出文档给出的 rng_seed。"""
    seed = DerivedRandom.derive_seed(
        save_seed="demo-save-2026",
        turn_id=43,
        actor_id="player_1",
        method="sneak_move",
        seq=50,
    )

    assert seed == _DOC_EXAMPLE_SEED


def test_derive_seed_is_deterministic_across_entry_points() -> None:
    """同一输入在 DerivedRandom/RandomSource/config.rng 三个入口派生同一种子。"""
    expected = DerivedRandom.derive_seed(
        save_seed="save-2026-11", turn_id=1, actor_id="player_1", method="move_to", seq=1
    )
    via_protocol = RandomSource.derive_seed(
        save_seed="save-2026-11", turn_id=1, actor_id="player_1", method="move_to", seq=1
    )
    via_config = derive_event_seed(
        save_seed="save-2026-11", turn_id=1, actor_id="player_1", method="move_to", seq=1
    )

    assert via_protocol == expected
    assert via_config == expected


_BASE_INPUTS: tuple[str, int, str, str, int] = ("save-42", 1, "player_1", "move_to", 1)


@pytest.mark.parametrize(
    ("index", "replacement"),
    [(0, "save-43"), (1, 2), (2, "player_2"), (3, "sneak_move"), (4, 2)],
    ids=["save_seed", "turn_id", "actor_id", "method", "seq"],
)
def test_derive_seed_changes_when_any_input_changes(index: int, replacement: object) -> None:
    """save_seed/turn_id/actor_id/method/seq 任一变化都会得到不同子种子。"""
    base_seed = derive_event_seed(*_BASE_INPUTS)
    changed = list(_BASE_INPUTS)
    changed[index] = replacement

    assert derive_event_seed(*changed) != base_seed


@pytest.mark.parametrize(
    ("save_seed", "turn_id", "actor_id", "method", "seq"),
    [
        ("save-42", 1, "player_1", "move_to", 1),
        ("demo-save-2026", 43, "player_1", "sneak_move", 50),
        ("存档-Seed-①", 10**6, "npc_铁匠", "craft.sword", 10**9),
        ("", -1, "", "", 0),
    ],
)
def test_derive_seed_is_masked_to_63_bits(
    save_seed: str, turn_id: int, actor_id: str, method: str, seq: int
) -> None:
    """派生结果恒落在 ``0..2**63-1``, 可直接写入 BIGINT 列。"""
    seed = derive_event_seed(save_seed, turn_id, actor_id, method, seq)

    assert isinstance(seed, int)
    assert 0 <= seed <= DEFAULT_SEED_MASK
    assert seed & DEFAULT_SEED_MASK == seed


def test_config_seed_defaults() -> None:
    """掩码常量与存档种子字符串按第 10 章 10.3 的约定生成。"""
    assert DEFAULT_SEED_MASK == 0x7FFF_FFFF_FFFF_FFFF == 2**63 - 1
    assert save_seed_from_settings(42) == "save-42"
    assert save_seed_from_settings(0) == "save-0"
    assert save_seed_from_settings(7) == save_seed_from_settings(7)


def test_config_derive_event_seed_returns_masked_value() -> None:
    """config.rng 委托结果已掩码, 与 DerivedRandom 同级调用一致, 不得重复掩码。"""
    seed = derive_event_seed("save-42", 1, "player_1", "move_to", 1)
    same_seed = DerivedRandom.derive_seed("save-42", 1, "player_1", "move_to", 1)

    assert seed == same_seed
    assert 0 <= seed <= DEFAULT_SEED_MASK
    assert seed & DEFAULT_SEED_MASK == seed


# ---------------------------------------------------------------------------
# mask_seed 边界
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (-1, 0x7FFF_FFFF_FFFF_FFFF),
        (0, 0),
        (2**64 - 1, 0x7FFF_FFFF_FFFF_FFFF),
        (2**63 - 1, 0x7FFF_FFFF_FFFF_FFFF),
        (2**63, 0),
        (42, 42),
    ],
)
def test_mask_seed_boundaries(raw: int, expected: int) -> None:
    """``raw & 0x7FFF_FFFF_FFFF_FFFF`` 的确定性边界值, 协议级与实现类级一致。"""
    assert RandomSource.mask_seed(raw) == expected
    assert DerivedRandom.mask_seed(raw) == expected


@pytest.mark.parametrize("raw", [-(2**100), -(2**63), 2**64, 2**100])
def test_mask_seed_always_fits_bigint(raw: int) -> None:
    """任意整数经掩码后都落在 ``0..2**63-1``。"""
    assert 0 <= RandomSource.mask_seed(raw) <= DEFAULT_SEED_MASK


# ---------------------------------------------------------------------------
# DerivedRandom 实例掷骰
# ---------------------------------------------------------------------------


def test_same_seed_produces_identical_randint_sequence() -> None:
    """同一 seed 的两个实例产生完全相同的 randint 序列。"""
    first = DerivedRandom(42)
    second = DerivedRandom(42)

    assert [first.randint(1, 20) for _ in range(30)] == [second.randint(1, 20) for _ in range(30)]


def test_same_seed_produces_identical_choice_sequence() -> None:
    """同一 seed 的两个实例产生完全相同的 choice 序列。"""
    candidates = ("north", "south", "east", "west")
    first = DerivedRandom(7)
    second = DerivedRandom(7)

    assert [first.choice(candidates) for _ in range(20)] == [
        second.choice(candidates) for _ in range(20)
    ]


@pytest.mark.parametrize(("seed_a", "seed_b"), [(0, 1), (1, 2), (42, 43), (123456789, 987654321)])
def test_different_seeds_produce_different_sequences(seed_a: int, seed_b: int) -> None:
    """不同 seed 的 randint 序列不同 (固定种子下确定性判定)。"""
    first = DerivedRandom(seed_a)
    second = DerivedRandom(seed_b)

    assert [first.randint(1, 100) for _ in range(20)] != [second.randint(1, 100) for _ in range(20)]


def test_randint_stays_within_inclusive_bounds() -> None:
    """``randint(1, 20)`` 的 200 次结果全部落在闭区间内。"""
    rng = DerivedRandom(7)

    assert all(1 <= rng.randint(1, 20) <= 20 for _ in range(200))


@pytest.mark.parametrize("value", [0, 5, -3, 0x7FFF_FFFF_FFFF_FFFF])
def test_randint_degenerate_bounds_return_the_only_value(value: int) -> None:
    """``randint(v, v)`` 必返回 v (含两端语义)。"""
    assert DerivedRandom(7).randint(value, value) == value


def test_randint_hits_both_endpoints() -> None:
    """``randint(1, 2)`` 在固定种子下既能取到 1 也能取到 2 (上界闭区间)。"""
    rng = DerivedRandom(7)

    assert {rng.randint(1, 2) for _ in range(100)} == {1, 2}


def test_randint_rejects_reversed_bounds() -> None:
    """下界大于上界时抛 ValueError (与 random.Random.randint 语义一致)。"""
    with pytest.raises(ValueError):
        DerivedRandom(7).randint(5, 1)


def test_choice_returns_member_of_candidates() -> None:
    """``choice`` 结果恒属于候选集。"""
    candidates = ("north", "south", "east", "west")
    rng = DerivedRandom(7)

    assert all(rng.choice(candidates) in candidates for _ in range(100))


def test_choice_hits_every_candidate() -> None:
    """固定种子下 100 次 ``choice`` 覆盖全部候选。"""
    candidates = [10, 20, 30]
    rng = DerivedRandom(7)

    assert {rng.choice(candidates) for _ in range(100)} == set(candidates)


def test_choice_rejects_empty_sequence() -> None:
    """空候选集抛 IndexError (与 random.Random.choice 语义一致)。"""
    with pytest.raises(IndexError):
        DerivedRandom(7).choice([])


def test_derived_random_satisfies_random_source_contract() -> None:
    """DerivedRandom 实例可直接按 RandomSource 协议消费 (结构化子类型)。"""
    source: RandomSource = DerivedRandom(7)

    assert 1 <= source.randint(1, 20) <= 20
    assert source.choice(["a", "b"]) in {"a", "b"}


# ---------------------------------------------------------------------------
# 随机入口隔离: 全仓只有 domain/rules/dice.py 允许 import random
# ---------------------------------------------------------------------------


def _imports_random(tree: ast.Module) -> bool:
    """判断语法树中是否出现 ``import random`` / ``from random import ...``。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(
                alias.name == "random" or alias.name.startswith("random.") for alias in node.names
            ):
                return True
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "random" or module.startswith("random."):
                return True
    return False


def test_random_import_is_isolated_to_dice_module() -> None:
    """第 10 章 10.3 约束 1: 游戏逻辑不得直接 import random, 唯一随机点为本模块。"""
    importers = [
        path.relative_to(_SRC_ROOT).as_posix()
        for path in sorted(_SRC_ROOT.rglob("*.py"))
        if _imports_random(ast.parse(path.read_text(encoding="utf-8")))
    ]

    assert importers == ["domain/rules/dice.py"]
