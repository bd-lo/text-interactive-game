"""M6 加法字段：失败回合 ``effects``/``suggested_actions`` 与成功回合 ``suggested_actions``（A4/D7）。

验证点：失败 ``TurnResult`` 追加结构化 ``hint`` + 3–4 条可行动作建议；成功回合填 1–4 条当前
可提交的下一步指令（``effects`` 仍空）；**``text`` 原文、``version``、事件与零写入语义完全不变**；
失败结果不进入幂等缓存（重复请求提示稳定且仍零写入）。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from textgame.orchestration.turn_orchestrator import (
    _GAME_NOT_FOUND_TEXT,
    _UNKNOWN_ACTION_TEXT,
    _VERSION_CONFLICT_TEXT,
)

_GAME_KEY = "game-hints-key-0001"


def _create_game(client: TestClient) -> dict:
    """``POST /api/v1/games``（201 断言），返回响应体。"""
    resp = client.post("/api/v1/games", headers={"Idempotency-Key": _GAME_KEY}, json={"seed": 42})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _post_turn(
    client: TestClient, *, game_id: str, text: str, expected_version: int, key: str
) -> dict:
    """``POST /api/v1/turns``（200 断言），返回响应体。"""
    resp = client.post(
        "/api/v1/turns",
        headers={"Idempotency-Key": key},
        json={"game_id": game_id, "input": text, "expected_version": expected_version},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _assert_failure_hints(body: dict, *, hint_fragment: str = "") -> None:
    """失败体：ok=False、3–4 条建议、effects 含非空 hint，且顶层字段集合未变。"""
    assert body["ok"] is False
    assert 3 <= len(body["suggested_actions"]) <= 4
    assert all(isinstance(action, str) and action for action in body["suggested_actions"])
    assert isinstance(body["effects"], list) and body["effects"]
    hint = body["effects"][0]["hint"]
    assert isinstance(hint, str) and hint
    if hint_fragment:
        assert hint_fragment in hint


def test_unknown_input_hints_keep_text_unchanged(game_harness) -> None:
    """无法理解的输入：填 hints，但 ``text`` 与既有未知动作文案逐字相同、零写入。"""
    client = game_harness.client
    repo = game_harness.repo
    game_id = _create_game(client)["game_id"]

    body = _post_turn(
        client, game_id=game_id, text="跳舞", expected_version=1, key="turn-hints-key-0001"
    )

    _assert_failure_hints(body)
    assert body["error_code"] == "INVALID_INPUT"
    assert body["text"] == _UNKNOWN_ACTION_TEXT
    assert body["events"] == []
    assert body["version"] == 1
    assert len(repo.read_events(game_id)) == 1


def test_stale_version_conflict_hints_keep_text_unchanged(game_harness) -> None:
    """expected_version 过期：填 CONFLICT hints，``text`` 原文与零写入不变。"""
    game_id = _create_game(game_harness.client)["game_id"]

    body = _post_turn(
        game_harness.client,
        game_id=game_id,
        text="环顾四周",
        expected_version=0,
        key="turn-hints-key-0002",
    )

    _assert_failure_hints(body, hint_fragment="刷新对局视图")
    assert body["error_code"] == "CONFLICT"
    assert body["text"] == _VERSION_CONFLICT_TEXT
    assert body["events"] == []
    assert body["version"] == 1


def test_unknown_game_not_found_hints(game_harness) -> None:
    """未知存档：NOT_FOUND hints，``text`` 原文不变。"""
    body = _post_turn(
        game_harness.client,
        game_id="game_does_not_exist",
        text="环顾四周",
        expected_version=0,
        key="turn-hints-key-0003",
    )

    _assert_failure_hints(body)
    assert body["error_code"] == "NOT_FOUND"
    assert body["text"] == _GAME_NOT_FOUND_TEXT
    assert body["version"] == 0


def test_rejected_action_still_gets_hints_zero_writes(game_harness) -> None:
    """被门禁拒绝的动作同样有 hints，且零事件/版本不变（文案不做断言、不修改）。"""
    client = game_harness.client
    repo = game_harness.repo
    game_id = _create_game(client)["game_id"]

    body = _post_turn(
        client, game_id=game_id, text="使用治疗药水", expected_version=1, key="turn-hints-key-0004"
    )

    _assert_failure_hints(body)
    assert body["events"] == []
    assert body["version"] == 1
    assert len(repo.read_events(game_id)) == 1


def test_successful_turn_fills_suggested_actions_but_keeps_effects_empty(game_harness) -> None:
    """成功回合（D7）：``suggested_actions`` 填 1–4 条当前可提交的中文指令，``effects`` 仍空。

    ``text`` 原文不变（既有 80–150 字叙事口径）、无内部 ID/英文诊断；失败提示只在失败回合出现。
    """
    client = game_harness.client
    game_id = _create_game(client)["game_id"]

    body = _post_turn(
        client,
        game_id=game_id,
        text="拿起火把",
        expected_version=1,
        key="turn-hints-key-0005",
    )

    assert body["ok"] is True
    assert body["effects"] == []
    actions = body["suggested_actions"]
    assert 1 <= len(actions) <= 4
    assert all(isinstance(action, str) and action for action in actions)
    # 玩家可读：不出现内部 ID/协议术语
    joined = "".join(actions)
    assert "_" not in joined
    assert "item" not in joined.lower() and "npc" not in joined.lower()
    assert body["events"]
    assert body["version"] == 2


def test_failure_hints_are_deterministic_and_not_cached(game_harness) -> None:
    """同一失败请求重试两次：提示稳定、结果相等，且仍零写入（失败不入幂等缓存）。"""
    client = game_harness.client
    repo = game_harness.repo
    game_id = _create_game(client)["game_id"]
    request = {
        "game_id": game_id,
        "input": "跳舞",
        "expected_version": 1,
    }

    first = client.post(
        "/api/v1/turns", headers={"Idempotency-Key": "turn-hints-key-0006"}, json=request
    ).json()
    second = client.post(
        "/api/v1/turns", headers={"Idempotency-Key": "turn-hints-key-0006"}, json=request
    ).json()

    assert first == second
    assert first["effects"] == second["effects"]
    assert len(repo.read_events(game_id)) == 1
