"""单元测试: 判定值对象与随机源协议的字段约束 (第 5 章 5.6 定稿、第 10 章 10.2)。

覆盖 ``DiceRoll``/``Modifier``/``CheckResult`` 的 frozen、extra=forbid、
范围与正则失败分支; 纯 Pydantic 校验, 无 IO、无随机。
"""

import pytest
from pydantic import ValidationError

from textgame.domain.rules.types import CheckResult, DiceRoll, Modifier, RandomSource

_RNG_SEED_MAX = 0x7FFF_FFFF_FFFF_FFFF


def _make_dice_roll(**overrides: object) -> DiceRoll:
    """构造合法 ``DiceRoll``，可按字段覆盖以测试失败分支。"""
    data: dict[str, object] = {"sides": 20, "rolls": [12], "kept": [12]}
    data.update(overrides)
    return DiceRoll(**data)


def _make_modifier(**overrides: object) -> Modifier:
    """构造合法 ``Modifier``，可按字段覆盖以测试失败分支。"""
    data: dict[str, object] = {"source": "dexterity", "value": 3}
    data.update(overrides)
    return Modifier(**data)


def _check_result_data(**overrides: object) -> dict[str, object]:
    """返回一份合法的 ``CheckResult`` 构造参数 (结构取第 10 章 10.2 示例)。"""
    data: dict[str, object] = {
        "rule_id": "stealth.sneak.v1",
        "actor_id": "player_1",
        "target": "guard_a",
        "dc": 15,
        "dice": [DiceRoll(sides=20, rolls=[12], kept=[12])],
        "modifiers": [
            Modifier(source="dexterity", value=3),
            Modifier(source="darkness", value=2),
        ],
        "total": 17,
        "outcome": "success",
        "rng_seed": 853400723015425181,
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# DiceRoll
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sides", [2, 20, 100])
def test_dice_roll_accepts_sides_in_range(sides: int) -> None:
    """``sides`` 取值边界 2..100 均合法。"""
    assert _make_dice_roll(sides=sides).sides == sides


@pytest.mark.parametrize("sides", [1, 0, -1, 101])
def test_dice_roll_rejects_sides_out_of_range(sides: int) -> None:
    """``sides`` 越界触发 Pydantic ValidationError。"""
    with pytest.raises(ValidationError):
        _make_dice_roll(sides=sides)


@pytest.mark.parametrize("field", ["rolls", "kept"])
def test_dice_roll_requires_non_empty_list(field: str) -> None:
    """``rolls``/``kept`` 至少 1 项。"""
    with pytest.raises(ValidationError):
        _make_dice_roll(**{field: []})


@pytest.mark.parametrize("field", ["rolls", "kept"])
def test_dice_roll_caps_list_length(field: str) -> None:
    """``rolls``/``kept`` 最多 16 项。"""
    with pytest.raises(ValidationError):
        _make_dice_roll(**{field: [1] * 17})


def test_dice_roll_accepts_sixteen_entries() -> None:
    """``rolls`` 的合法上界为 16 项。"""
    assert len(_make_dice_roll(rolls=list(range(1, 17))).rolls) == 16


def test_dice_roll_is_frozen() -> None:
    """frozen=True: 字段不可重新赋值。"""
    roll = _make_dice_roll()
    with pytest.raises(ValidationError):
        roll.sides = 6


def test_dice_roll_forbids_extra_field() -> None:
    """extra="forbid": 多余字段被拒绝。"""
    with pytest.raises(ValidationError):
        DiceRoll(sides=20, rolls=[12], kept=[12], bonus=1)


# ---------------------------------------------------------------------------
# Modifier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [-100, -1, 0, 100])
def test_modifier_accepts_value_in_range(value: int) -> None:
    """修正值合法区间为 -100..100。"""
    assert _make_modifier(value=value).value == value


@pytest.mark.parametrize("value", [-101, 101])
def test_modifier_rejects_value_out_of_range(value: int) -> None:
    """修正值越界触发 ValidationError。"""
    with pytest.raises(ValidationError):
        _make_modifier(value=value)


@pytest.mark.parametrize("source", ["", "x" * 49])
def test_modifier_rejects_invalid_source_length(source: str) -> None:
    """``source`` 长度必须为 1..48。"""
    with pytest.raises(ValidationError):
        _make_modifier(source=source)


def test_modifier_accepts_boundary_source_length() -> None:
    """``source`` 的合法边界为 1 与 48 个字符。"""
    assert _make_modifier(source="x").source == "x"
    assert len(_make_modifier(source="x" * 48).source) == 48


def test_modifier_is_frozen() -> None:
    """frozen=True: 字段不可重新赋值。"""
    modifier = _make_modifier()
    with pytest.raises(ValidationError):
        modifier.value = 4


def test_modifier_forbids_extra_field() -> None:
    """extra="forbid": 多余字段被拒绝。"""
    with pytest.raises(ValidationError):
        Modifier(source="dexterity", value=3, note="extra")


# ---------------------------------------------------------------------------
# CheckResult
# ---------------------------------------------------------------------------


def test_check_result_accepts_doc_example() -> None:
    """第 10 章 10.2 示例结构可构造，且字段值原样保留。"""
    result = CheckResult(**_check_result_data())

    assert result.rule_id == "stealth.sneak.v1"
    assert result.actor_id == "player_1"
    assert result.target == "guard_a"
    assert result.dc == 15
    assert result.dice[0].sides == 20
    assert result.dice[0].kept == [12]
    assert result.total == 17
    assert result.outcome == "success"
    assert result.rng_seed == 853400723015425181


def test_check_result_target_is_optional() -> None:
    """``target`` 可省略，省略时为 None。"""
    result = CheckResult(**{k: v for k, v in _check_result_data().items() if k != "target"})

    assert result.target is None


@pytest.mark.parametrize("count", [1, 4])
def test_check_result_accepts_dice_count_in_range(count: int) -> None:
    """``dice`` 允许 1..4 组。"""
    dice = [DiceRoll(sides=20, rolls=[10], kept=[10]) for _ in range(count)]
    result = CheckResult(**{**_check_result_data(), "dice": dice})

    assert len(result.dice) == count


@pytest.mark.parametrize("count", [0, 5])
def test_check_result_rejects_dice_count_out_of_range(count: int) -> None:
    """``dice`` 为 0 组或超过 4 组时拒绝。"""
    dice = [DiceRoll(sides=20, rolls=[10], kept=[10]) for _ in range(count)]

    with pytest.raises(ValidationError):
        CheckResult(**{**_check_result_data(), "dice": dice})


def test_check_result_caps_modifiers_at_eight() -> None:
    """``modifiers`` 最多 8 项，恰好 8 项合法。"""
    modifiers = [Modifier(source=f"mod_{index}", value=1) for index in range(9)]

    with pytest.raises(ValidationError):
        CheckResult(**{**_check_result_data(), "modifiers": modifiers})

    result = CheckResult(**{**_check_result_data(), "modifiers": modifiers[:8]})
    assert len(result.modifiers) == 8


@pytest.mark.parametrize("dc", [1, 100])
def test_check_result_accepts_dc_boundaries(dc: int) -> None:
    """``dc`` 合法边界为 1 与 100。"""
    assert CheckResult(**{**_check_result_data(), "dc": dc}).dc == dc


@pytest.mark.parametrize("dc", [0, 101])
def test_check_result_rejects_dc_out_of_range(dc: int) -> None:
    """``dc`` 越界触发 ValidationError。"""
    with pytest.raises(ValidationError):
        CheckResult(**{**_check_result_data(), "dc": dc})


@pytest.mark.parametrize("total", [-100, 400])
def test_check_result_accepts_total_boundaries(total: int) -> None:
    """``total`` 合法边界为 -100 与 400。"""
    assert CheckResult(**{**_check_result_data(), "total": total}).total == total


@pytest.mark.parametrize("total", [-101, 401])
def test_check_result_rejects_total_out_of_range(total: int) -> None:
    """``total`` 越界触发 ValidationError。"""
    with pytest.raises(ValidationError):
        CheckResult(**{**_check_result_data(), "total": total})


@pytest.mark.parametrize(
    "rule_id", ["stealth.sneak.v1", "stealth.sneak_v1", "d20", "ab", "a" + "b" * 63]
)
def test_check_result_accepts_valid_rule_id(rule_id: str) -> None:
    """``rule_id`` 匹配 ``^[a-z][a-z0-9_.]{1,63}$`` 时合法 (总长 2..64, 允许 . 与 _)。"""
    assert CheckResult(**{**_check_result_data(), "rule_id": rule_id}).rule_id == rule_id


# 第 5 章 5.6 定稿正则 ``^[a-z][a-z0-9_.]{1,63}$`` 不允许大写、短横与空格。
@pytest.mark.parametrize(
    "rule_id",
    ["", "a", "Stealth", "1stealth", "stealth-sneak", "stealth sneak", "a" + "b" * 64],
)
def test_check_result_rejects_invalid_rule_id(rule_id: str) -> None:
    """``rule_id`` 为空、单字符、含大写/短横/空格或超长时拒绝。"""
    with pytest.raises(ValidationError):
        CheckResult(**{**_check_result_data(), "rule_id": rule_id})


@pytest.mark.parametrize("outcome", ["critical_success", "success", "failure", "critical_failure"])
def test_check_result_accepts_known_outcome(outcome: str) -> None:
    """四种判定结果均为合法 Literal 取值。"""
    assert CheckResult(**{**_check_result_data(), "outcome": outcome}).outcome == outcome


def test_check_result_rejects_unknown_outcome() -> None:
    """Literal 之外的判定结果被拒绝。"""
    with pytest.raises(ValidationError):
        CheckResult(**{**_check_result_data(), "outcome": "SUCCESS"})


@pytest.mark.parametrize("rng_seed", [0, _RNG_SEED_MAX])
def test_check_result_accepts_rng_seed_boundaries(rng_seed: int) -> None:
    """``rng_seed`` 合法边界为 0 与 2**63-1 (BIGINT 上界)。"""
    assert CheckResult(**{**_check_result_data(), "rng_seed": rng_seed}).rng_seed == rng_seed


@pytest.mark.parametrize("rng_seed", [-1, _RNG_SEED_MAX + 1])
def test_check_result_rejects_rng_seed_out_of_range(rng_seed: int) -> None:
    """``rng_seed`` 为负或超过 63 位掩码上界时拒绝。"""
    with pytest.raises(ValidationError):
        CheckResult(**{**_check_result_data(), "rng_seed": rng_seed})


def test_check_result_is_frozen() -> None:
    """frozen=True: 判定结果不可被篡改。"""
    result = CheckResult(**_check_result_data())

    with pytest.raises(ValidationError):
        result.total = 18


def test_check_result_forbids_extra_field() -> None:
    """extra="forbid": 多余字段 (如 rng_state) 被拒绝。"""
    with pytest.raises(ValidationError):
        CheckResult(**_check_result_data(), rng_state={"mode": "derived"})


def test_random_source_protocol_declares_seed_contract() -> None:
    """``RandomSource`` 以可调用的 static/classmethod 暴露 mask_seed/derive_seed。"""
    assert callable(RandomSource.mask_seed)
    assert callable(RandomSource.derive_seed)
