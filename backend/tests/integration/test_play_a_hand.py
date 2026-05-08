"""End-to-end Path A integration test.

Two players connect WebSockets to a fresh table, sit down via HTTP,
play a single hand to completion. Asserts that:

- Public state never leaks hole cards
- Each player receives only their own hole cards
- Single-fold-to-BB resolves with correct chip movements
"""
from __future__ import annotations

from collections.abc import Iterable

import pytest
from fastapi.testclient import TestClient

from app.api.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _drain_until(ws, types: Iterable[str], cap: int = 30) -> dict:
    """Read messages until we get one whose type is in `types`. Returns it."""
    targets = set(types)
    for _ in range(cap):
        msg = ws.receive_json()
        if msg.get("type") in targets:
            return msg
    raise AssertionError(f"none of {targets} arrived within {cap} messages")


def _stack_of(public_state: dict, player_id: str) -> int:
    for p in public_state["players"]:
        if p is not None and p["id"] == player_id:
            return p["stack"]
    raise AssertionError(f"player {player_id} not in public state")


def test_two_players_play_a_hand(client: TestClient) -> None:
    # Alice creates the table.
    res = client.post(
        "/tables",
        params={"as": "alice"},
        json={"small_blind": 5, "big_blind": 10},
    )
    assert res.status_code == 200, res.text
    code = res.json()["code"]

    # Both players open WebSockets first (as spectators), then sit down.
    with client.websocket_connect(f"/ws/tables/{code}?as=alice") as ws_alice, \
         client.websocket_connect(f"/ws/tables/{code}?as=bob") as ws_bob:

        # Initial seats message (empty table for both).
        seats_a = _drain_until(ws_alice, ["seats"])
        seats_b = _drain_until(ws_bob, ["seats"])
        assert all(s is None for s in seats_a["seats"])
        assert all(s is None for s in seats_b["seats"])

        # Both players join.
        for who, seat in [("alice", 0), ("bob", 1)]:
            res = client.post(
                "/tables/join",
                params={"as": who},
                json={"code": code, "seat": seat, "buy_in": 1000},
            )
            assert res.status_code == 200, res.text

        # The hand will start as soon as the table loop notices 2 seats are filled.
        alice_start = _drain_until(ws_alice, ["hand_started"])
        bob_start = _drain_until(ws_bob, ["hand_started"])

        # ---- Hidden information audit ----
        # Public state must not contain hole cards for any player.
        for snapshot in (alice_start, bob_start):
            for p in snapshot["state"]["players"]:
                if p is not None:
                    assert p.get("hole") in (None, []), \
                        f"public state leaked hole cards: {p}"

        # Both players should receive a private message with their own hole cards.
        alice_priv = _drain_until(ws_alice, ["private"])
        bob_priv = _drain_until(ws_bob, ["private"])
        alice_hole = alice_priv["state"]["hole"]
        bob_hole = bob_priv["state"]["hole"]
        assert alice_hole is not None and len(alice_hole) == 2
        assert bob_hole is not None and len(bob_hole) == 2
        assert alice_hole != bob_hole, "two players got the same hole cards"

        # Heads-up: alice (seat 0) is button = SB and acts first pre-flop.
        assert alice_priv["state"]["your_turn"] is True
        assert bob_priv["state"]["your_turn"] is False

        # Alice folds → bob takes the SB.
        ws_alice.send_json({"type": "action", "action": "fold"})

        alice_complete = _drain_until(ws_alice, ["hand_complete"])
        bob_complete = _drain_until(ws_bob, ["hand_complete"])

        assert _stack_of(alice_complete["state"], "alice") == 995
        assert _stack_of(bob_complete["state"], "bob") == 1005

        # The non-folded winner's hole cards may be revealed; folded player's
        # hole cards must NOT be revealed.
        for p in alice_complete["state"]["players"]:
            if p is None:
                continue
            if p["id"] == "alice":
                assert p.get("hole") is None
            elif p["id"] == "bob":
                assert p.get("hole") is not None  # winner reveal is fine
