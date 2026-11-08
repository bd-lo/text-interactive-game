"""M1-003 阶段 B 回放测试: EventLog 盖章/追加/读取与 derive_seed 派生重放。

对应第 5 章 5.3 (Event 草稿/盖章)、第 14 章 14.2 (全局 seq、EventLog.stamp 分配 seq/id)、
第 18 章 18.3 (固定种子回放断言口径)、第 10 章 10.3 (派生种子已掩码、禁止重复掩码)。
全部使用固定 save_seed，不依赖当前时间做断言；覆盖 stamp 确定性、append 连续性、
read 升序/回合过滤、replay 一致与草稿/跳号/篡改/NaN 等失败分支。
"""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from textgame.config.rng import save_seed_from_settings
from textgame.domain.events import Event, EventLog, ReplayMismatchError
from textgame.domain.rules.dice import DerivedRandom

_SAVE_SEED = save_seed_from_settings(42)
_ACTOR = "char_01ARZ3NDEKTSV4RRFFQ69G5FAV"
_ACTOR_2 = "char_01BX5ZZKBKACTAV9WEVGEMMVRZ"
_RNG_SEED_MAX = 0x7FFF_FFFF_FFFF_FFFF


def _draft(
    *,
    turn_id: int = 1,
    actor_id: str = _ACTOR,
    method: str = "move_to",
    params: dict[str, object] | None = None,
) -> Event:
    """构造一条合法草稿事件 (id/seq/rng_seed/ts 均为 None)。"""
    return Event(
        turn_id=turn_id,
        actor_id=actor_id,
        method=method,
        params={} if params is None else params,
    )


def _stamped_events() -> list[Event]:
    """用固定存档种子盖章一批事件：回合 1 两条 + 回合 2 一条。"""
    log = EventLog(_SAVE_SEED)
    return log.stamp(
        [
            _draft(params={"direction": "north"}),
            _draft(method="attack_target", params={"target_id": "npc_goblin_01"}),
            _draft(turn_id=2, actor_id=_ACTOR_2, method="take_item"),
        ]
    )


def _seed_projection(events: list[Event]) -> list[tuple[int | None, str | None, int | None]]:
    """只取契约字段 (seq, id, rng_seed) 做跨实例比较，忽略盖章时刻的 ts。"""
    return [(event.seq, event.id, event.rng_seed) for event in events]


# ---------------------------------------------------------------------------
# stamp: 分配 seq/id/rng_seed/ts，不写状态
# ---------------------------------------------------------------------------


def test_stamp_allocates_seq_id_and_utc_ts() -> None:
    """单条草稿盖章后 seq=1、id=evt_000001、ts 为 UTC。"""
    log = EventLog(_SAVE_SEED)
    [stamped] = log.stamp([_draft(params={"direction": "north"})])

    assert stamped.seq == 1
    assert stamped.id == "evt_000001"
    assert stamped.is_stamped
    assert stamped.ts is not None
    assert stamped.ts.tzinfo is not None
    assert stamped.ts.utcoffset() == timedelta(0)
    assert stamped.actor_id == _ACTOR
    assert stamped.method == "move_to"
    assert stamped.params == {"direction": "north"}


def test_stamp_rng_seed_matches_derive_seed() -> None:
    """每条事件的 rng_seed 等于 derive_seed(save_seed, turn_id, actor_id, method, seq)。"""
    log = EventLog(_SAVE_SEED)
    stamped = log.stamp(
        [
            _draft(),
            _draft(method="attack_target"),
            _draft(turn_id=2, actor_id=_ACTOR_2, method="take_item"),
        ]
    )

    for event in stamped:
        assert event.seq is not None
        assert event.rng_seed == DerivedRandom.derive_seed(
            _SAVE_SEED, event.turn_id, event.actor_id, event.method, event.seq
        )
        assert event.rng_seed is not None
        assert 0 <= event.rng_seed <= _RNG_SEED_MAX
    assert [event.seq for event in stamped] == [1, 2, 3]


def test_stamp_does_not_mutate_log_state() -> None:
    """stamp 只返回新列表：不改 last_seq、不写入日志、草稿保持草稿态。"""
    log = EventLog(_SAVE_SEED)
    drafts = [_draft(), _draft(method="attack_target")]

    log.stamp(drafts)

    assert log.last_seq == 0
    assert log.read() == []
    assert all(not draft.is_stamped for draft in drafts)


def test_stamp_is_deterministic_across_instances() -> None:
    """同一 save_seed + 同一草稿序列在两个实例上得到相同 seq/id/rng_seed。"""
    drafts = [
        _draft(),
        _draft(method="attack_target"),
        _draft(turn_id=2, actor_id=_ACTOR_2, method="take_item"),
    ]
    first = EventLog(_SAVE_SEED).stamp(drafts)
    second = EventLog(_SAVE_SEED).stamp(drafts)

    assert _seed_projection(first) == _seed_projection(second)
    assert [event.id for event in first] == [
        "evt_000001",
        "evt_000002",
        "evt_000003",
    ]


def test_stamp_accepts_empty_batch() -> None:
    """空批次返回空列表，不推进 seq。"""
    log = EventLog(_SAVE_SEED)

    assert log.stamp([]) == []
    assert log.last_seq == 0


def test_stamp_rejects_already_stamped_event() -> None:
    """已盖章事件不得再次盖章 (草稿态校验)。"""
    log = EventLog(_SAVE_SEED)
    [stamped] = log.stamp([_draft()])

    with pytest.raises(ValueError, match="draft"):
        log.stamp([stamped])


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf")])
def test_stamp_rejects_non_finite_params(bad_value: float) -> None:
    """params 含 NaN/Infinity 时 json.dumps(allow_nan=False) 失败 → ValueError (SCHEMA_VIOLATION 口径)。"""
    log = EventLog(_SAVE_SEED)

    with pytest.raises(ValueError) as exc_info:
        log.stamp([_draft(params={"damage": bad_value})])

    message = str(exc_info.value)
    assert "seq=1" in message
    assert "move_to" in message
    assert log.last_seq == 0


def test_stamp_rejects_non_json_serializable_params() -> None:
    """params 含任意 Python 对象时序列化校验失败 → ValueError。"""
    log = EventLog(_SAVE_SEED)

    with pytest.raises(ValueError, match="JSON serializable"):
        log.stamp([_draft(params={"payload": object()})])


# ---------------------------------------------------------------------------
# append / read: 连续性、原子性、回合过滤
# ---------------------------------------------------------------------------


def test_append_advances_last_seq_and_read_is_ordered() -> None:
    """多事件 append 后 seq 连续、read 按 seq 升序返回。"""
    log = EventLog(_SAVE_SEED)
    batch = _stamped_events()

    log.append(batch)

    assert log.last_seq == 3
    assert log.read() == batch
    assert [event.seq for event in log.read()] == [1, 2, 3]


def test_append_continues_seq_across_batches() -> None:
    """第二批 stamp 从 _last_seq + 1 继续分配 seq。"""
    log = EventLog(_SAVE_SEED)
    log.append(_stamped_events()[:2])

    [stamped] = log.stamp([_draft(turn_id=2)])
    assert stamped.seq == 3

    log.append([stamped])
    assert log.last_seq == 3


def test_read_filters_by_turn() -> None:
    """read(turn_id=...) 只返回该回合事件。"""
    log = EventLog(_SAVE_SEED)
    log.append(_stamped_events())

    assert [event.seq for event in log.read(turn_id=1)] == [1, 2]
    assert [event.seq for event in log.read(turn_id=2)] == [3]
    assert log.read(turn_id=99) == []


def test_read_returns_a_copy() -> None:
    """read 返回副本：调用方修改列表不污染内部日志。"""
    log = EventLog(_SAVE_SEED)
    log.append(_stamped_events())

    events = log.read()
    events.clear()

    assert len(log.read()) == 3


def test_append_rejects_draft_events() -> None:
    """append 只接受盖章态；草稿事件 → ValueError 且不写入。"""
    log = EventLog(_SAVE_SEED)

    with pytest.raises(ValueError, match="draft"):
        log.append([_draft()])

    assert log.last_seq == 0
    assert log.read() == []


def test_append_rejects_seq_gap() -> None:
    """seq 不紧接 _last_seq (跳号) → ValueError 且不写入。"""
    batch = _stamped_events()
    log = EventLog(_SAVE_SEED)

    with pytest.raises(ValueError, match="seq"):
        log.append(batch[1:])

    assert log.last_seq == 0
    assert log.read() == []


def test_append_is_atomic_on_invalid_batch() -> None:
    """批次中后续事件非法时，前面已校验通过的事件也不写入。"""
    batch = _stamped_events()
    log = EventLog(_SAVE_SEED)

    with pytest.raises(ValueError):
        log.append([batch[0], _draft(), batch[2]])

    assert log.last_seq == 0
    assert log.read() == []


def test_append_rejects_id_mismatch() -> None:
    """id 必须等于 f"evt_{seq:06d}"。"""
    batch = _stamped_events()
    tampered = batch[0].model_copy(update={"id": "evt_000009"})
    log = EventLog(_SAVE_SEED)

    with pytest.raises(ValueError, match="id"):
        log.append([tampered])

    assert log.last_seq == 0


def test_append_rejects_non_utc_ts() -> None:
    """ts 非 UTC-aware → ValueError (append 独立校验，不信任绕过校验器的篡改)。"""
    batch = _stamped_events()
    tampered = batch[0].model_copy(update={"ts": datetime(2026, 1, 1, 12, 0, 0)})
    log = EventLog(_SAVE_SEED)

    with pytest.raises(ValueError, match="UTC"):
        log.append([tampered])

    assert log.last_seq == 0


def test_event_model_rejects_naive_ts() -> None:
    """Event 模型的 ts 校验器拒绝 naive datetime。"""
    with pytest.raises(ValidationError):
        Event(
            id="evt_000001",
            turn_id=1,
            seq=1,
            actor_id=_ACTOR,
            method="move_to",
            rng_seed=1,
            ts=datetime(2026, 1, 1, 12, 0, 0),
        )


def test_event_model_normalizes_aware_ts_to_utc() -> None:
    """带时区的 ts 会被 Event 校验器规范化到 UTC。"""
    event = Event(
        id="evt_000001",
        turn_id=1,
        seq=1,
        actor_id=_ACTOR,
        method="move_to",
        rng_seed=1,
        ts=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone(timedelta(hours=8))),
    )

    assert event.ts is not None
    assert event.ts.utcoffset() == timedelta(0)
    assert event.ts.hour == 4


# ---------------------------------------------------------------------------
# replay: 派生重放与篡改检出
# ---------------------------------------------------------------------------


def test_replay_returns_all_events_in_order() -> None:
    """replay 全部一致时按 seq 升序返回事件列表。"""
    log = EventLog(_SAVE_SEED)
    batch = _stamped_events()
    log.append(batch)

    assert log.replay() == batch
    assert [event.seq for event in log.replay()] == [1, 2, 3]


def test_replay_is_deterministic_across_instances() -> None:
    """同一 save_seed + 同一草稿序列，两个实例的 replay 结果逐字段一致 (ts 除外)。"""
    first = EventLog(_SAVE_SEED)
    first.append(first.stamp([_draft(), _draft(method="attack_target")]))
    second = EventLog(_SAVE_SEED)
    second.append(second.stamp([_draft(), _draft(method="attack_target")]))

    assert _seed_projection(first.replay()) == _seed_projection(second.replay())


def test_empty_log_replay_returns_empty_list() -> None:
    """空日志 replay 是合法结果 (空列表)。"""
    log = EventLog(_SAVE_SEED)

    assert log.last_seq == 0
    assert log.read() == []
    assert log.replay() == []


def test_replay_detects_tampered_rng_seed() -> None:
    """append 后篡改 rng_seed (append 不检查该字段) → replay 抛 ReplayMismatchError 并指出 seq。"""
    batch = _stamped_events()
    tampered = [batch[0].model_copy(update={"rng_seed": 0}), *batch[1:]]
    log = EventLog(_SAVE_SEED)
    log.append(tampered)

    with pytest.raises(ReplayMismatchError) as exc_info:
        log.replay()

    error = exc_info.value
    assert error.seq == 1
    assert error.field == "rng_seed"
    assert error.actual == 0
    assert error.expected == DerivedRandom.derive_seed(_SAVE_SEED, 1, _ACTOR, "move_to", 1)
    assert "seq=1" in str(error)


def test_replay_detects_seq_corruption() -> None:
    """模拟持久化加载后被篡改的 seq：append 会拦截，replay 仍需独立检出。"""
    log = EventLog(_SAVE_SEED)
    log.append(_stamped_events()[:1])
    # 白盒注入：append 校验无法覆盖“已入库事件被改”的场景，由 replay 兜底 (第 18.3)
    log._events[0] = log._events[0].model_copy(update={"seq": 7})

    with pytest.raises(ReplayMismatchError) as exc_info:
        log.replay()

    assert exc_info.value.seq == 1
    assert exc_info.value.field == "seq"
    assert exc_info.value.actual == 7


def test_replay_detects_id_corruption() -> None:
    """模拟持久化加载后被篡改的 id：replay 检出 id 与 seq 不匹配。"""
    log = EventLog(_SAVE_SEED)
    log.append(_stamped_events()[:1])
    log._events[0] = log._events[0].model_copy(update={"id": "evt_000009"})

    with pytest.raises(ReplayMismatchError) as exc_info:
        log.replay()

    assert exc_info.value.seq == 1
    assert exc_info.value.field == "id"
    assert exc_info.value.actual == "evt_000009"


# ---------------------------------------------------------------------------
# 构造校验
# ---------------------------------------------------------------------------


def test_event_log_rejects_empty_save_seed() -> None:
    """save_seed 非空是 EventLog 的构造前置条件。"""
    with pytest.raises(ValueError, match="save_seed"):
        EventLog("")
