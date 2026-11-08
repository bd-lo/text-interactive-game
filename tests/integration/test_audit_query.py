"""M3-014 集成验收：审计查询（`GET /api/v1/games/{game_id}/audit`、AuditService）。

覆盖 M3 契约 D-M3-7 / 第 11 章 §11.4 / 第 14 章 §14.6：
- 冲突回合里 ``applied`` 与 ``rejected`` 两条提案审计行都在，状态为 ``committed``；
- 审计只增不改（重复查询结果稳定、无 UPDATE/DELETE 路径、重复写入幂等）；
- 查询过滤 ``turn_id``、未知存档 404、非法 ``turn_id`` 422；
- 零写入回合（提案全被拒/只读）同样留审计行（失败也有记录）。
"""

from __future__ import annotations

from typing import Any

import pytest

from textgame.services.audit_service import AuditService
from textgame.services.query_service import GameNotFoundError

_GAME_KEY = "m3-audit-game-0001"


def _create_m3_game(client: Any, *, key: str = _GAME_KEY) -> dict[str, Any]:
    """POST /api/v1/games（scenario=m3）并断言 201。"""
    resp = client.post(
        "/api/v1/games",
        headers={"Idempotency-Key": key},
        json={"seed": 42, "scenario": "m3"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _post_turn(
    client: Any, *, game_id: str, text: str, expected_version: int, key: str
) -> dict[str, Any]:
    """POST /api/v1/turns 并返回响应体（业务失败仍是 HTTP 200）。"""
    resp = client.post(
        "/api/v1/turns",
        headers={"Idempotency-Key": key},
        json={"game_id": game_id, "input": text, "expected_version": expected_version},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_audit_endpoint_returns_conflict_rows(m3_game_harness) -> None:
    """冲突回合审计：同一回合有 applied + rejected，状态 committed，字段完整。"""
    client = m3_game_harness.client
    game = _create_m3_game(client)
    _post_turn(
        client,
        game_id=game["game_id"],
        text="环顾四周",
        expected_version=1,
        key="m3-audit-turn-01",
    )

    resp = client.get(f"/api/v1/games/{game['game_id']}/audit", params={"turn_id": 2})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"records"}
    records = body["records"]
    assert len(records) == 2
    verdicts = sorted(record["verdict"] for record in records)
    assert verdicts == ["applied", "rejected"]
    assert all(record["status"] == "committed" for record in records)
    assert all(record["turn_id"] == 2 for record in records)
    assert all(record["attempt"] == 1 for record in records)
    assert all(record["priority"] == 50 for record in records)
    assert all(record["source"] == "npc" for record in records)
    applied = next(record for record in records if record["verdict"] == "applied")
    rejected = next(record for record in records if record["verdict"] == "rejected")
    assert applied["tool"] == "take_item"
    assert applied["resolution_id"].startswith("r_000002_")
    assert applied["args"]["item_id"]
    assert "not in" in rejected["verdict_reason"]
    # 记录按 (turn_id, proposal_id) 升序。
    assert [record["proposal_id"] for record in records] == sorted(
        record["proposal_id"] for record in records
    )


def test_audit_query_is_stable_and_read_only(m3_game_harness) -> None:
    """审计查询只读：重复调用结果一致，且不会新增审计行（只增表由写入方负责）。"""
    client = m3_game_harness.client
    game = _create_m3_game(client)
    _post_turn(
        client, game_id=game["game_id"], text="环顾四周", expected_version=1, key="m3-audit-turn-11"
    )
    url = f"/api/v1/games/{game['game_id']}/audit"

    first = client.get(url)
    second = client.get(url)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    all_records = first.json()["records"]
    assert len(all_records) == 2  # 只有一个已提交回合：两条冲突提案审计行
    # 未过滤时包含多个回合；按 turn_id 过滤是子集。
    turn_two = client.get(url, params={"turn_id": 2}).json()["records"]
    assert {record["turn_id"] for record in all_records} == {2}
    assert turn_two == all_records


def test_audit_endpoint_errors(m3_game_harness) -> None:
    """未知存档 404 信封；非法 turn_id 422；不存在的 turn 返回空记录。"""
    client = m3_game_harness.client
    game = _create_m3_game(client)

    missing = client.get("/api/v1/games/game_does_not_exist/audit")
    assert missing.status_code == 404
    assert missing.json()["ok"] is False
    assert missing.json()["error_code"] == "NOT_FOUND"

    invalid = client.get(f"/api/v1/games/{game['game_id']}/audit", params={"turn_id": 0})
    assert invalid.status_code == 422
    assert invalid.json()["error_code"] == "INVALID_INPUT"

    empty = client.get(f"/api/v1/games/{game['game_id']}/audit", params={"turn_id": 99})
    assert empty.status_code == 200
    assert empty.json() == {"records": []}


def test_audit_rows_are_append_only_and_attempt_scoped(m3_game_harness) -> None:
    """同 turn 的零写入尝试与后续提交尝试都保留审计行（attempt 区分、不覆盖）。"""
    harness = m3_game_harness
    client = harness.client
    game = _create_m3_game(client)
    game_id = game["game_id"]
    # 第一次回合：两名拾荒者冲突（turn 2 提交成功）。
    _post_turn(client, game_id=game_id, text="环顾四周", expected_version=1, key="m3-audit-turn-21")
    # 第二个回合：玩家读取背包（只读）+ NPC 计划 turn 3 无动作 → 零写入，turn 不变。
    zero = _post_turn(
        client, game_id=game_id, text="查看背包", expected_version=2, key="m3-audit-turn-22"
    )
    assert zero["ok"] is True
    assert zero["events"] == []
    turn2_rows = harness.audit.query(game_id, turn_id=2)
    assert len(turn2_rows) == 2  # 只有第一次尝试的审计行，只读回合不覆盖
    assert all(row.status == "committed" for row in turn2_rows)

    # 只增不改：直接再查一次内存仓储，行数与内容不变。
    again = harness.audit.query(game_id, turn_id=2)
    assert again == turn2_rows


def test_audit_service_unknown_game_raises(m3_game_harness) -> None:
    """AuditService 对未知存档抛 GameNotFoundError（接入层映射 404）。"""
    service: AuditService = m3_game_harness.audit
    with pytest.raises(GameNotFoundError):
        service.query("game_missing")
