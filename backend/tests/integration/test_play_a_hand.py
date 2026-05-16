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
        "/api/tables",
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
                "/api/tables/join",
                params={"as": who},
                json={"code": code, "seat": seat, "buy_in": 1000},
            )
            assert res.status_code == 200, res.text

        # The hand will start as soon as the table loop notices 2 seats are filled.
        alice_start = _drain_until(ws_alice, ["hand_started"])
        bob_start = _drain_until(ws_bob, ["hand_started"])

        # Pot starts at 15 (SB 5 + BB 10), even though no Pot object exists yet.
        assert alice_start["state"]["pot_total"] == 15
        assert bob_start["state"]["pot_total"] == 15

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


def test_pot_updates_through_betting_round(client: TestClient) -> None:
    """Regression: pot_total should update on every action, not just at hand end."""
    res = client.post(
        "/api/tables",
        params={"as": "alice"},
        json={"small_blind": 5, "big_blind": 10},
    )
    code = res.json()["code"]

    with client.websocket_connect(f"/ws/tables/{code}?as=alice") as ws_a, \
         client.websocket_connect(f"/ws/tables/{code}?as=bob") as ws_b:

        _drain_until(ws_a, ["seats"])
        _drain_until(ws_b, ["seats"])

        for who, seat in [("alice", 0), ("bob", 1)]:
            client.post(
                "/api/tables/join",
                params={"as": who},
                json={"code": code, "seat": seat, "buy_in": 1000},
            )

        a_start = _drain_until(ws_a, ["hand_started"])
        _drain_until(ws_b, ["hand_started"])
        # Initial pot = 15 (SB + BB)
        assert a_start["state"]["pot_total"] == 15

        # Drain everyone's private state so the queues are at the action.
        _drain_until(ws_a, ["private"])
        _drain_until(ws_b, ["private"])

        # Alice raises to 30. New pot = 30 (alice) + 10 (bob's BB) = 40.
        ws_a.send_json({"type": "action", "action": "raise", "amount": 30})
        a_update = _drain_until(ws_a, ["state_update"])
        assert a_update["state"]["pot_total"] == 40, (
            f"after alice raise to 30, pot_total should be 40, got "
            f"{a_update['state']['pot_total']}"
        )

        # Bob calls. New pot = 60 (both at 30). After call, pre-flop closes
        # and we move to flop: street_committed resets but total_committed
        # stays at 30/30, so pot_total still 60.
        # Drain alice's private update too so we can read bob's update next.
        _drain_until(ws_a, ["private"])
        _drain_until(ws_b, ["state_update"])
        _drain_until(ws_b, ["private"])

        ws_b.send_json({"type": "action", "action": "call"})
        # After the call, the loop broadcasts state_update (still pre-flop or
        # already moved to flop — either way pot_total should be 60).
        b_update = _drain_until(ws_b, ["state_update"])
        assert b_update["state"]["pot_total"] == 60, (
            f"after bob calls 30, pot_total should be 60, got "
            f"{b_update['state']['pot_total']}"
        )


def test_seats_broadcast_after_each_join(client: TestClient) -> None:
    """Regression: every player must receive a `seats` snapshot reflecting
    their seating before any `hand_started` event arrives. Without this, the
    UI keeps showing the seat picker after a player has actually sat down."""
    res = client.post(
        "/api/tables",
        params={"as": "alice"},
        json={"small_blind": 5, "big_blind": 10},
    )
    code = res.json()["code"]

    with client.websocket_connect(f"/ws/tables/{code}?as=alice") as ws_a, \
         client.websocket_connect(f"/ws/tables/{code}?as=bob") as ws_b:

        # Initial snapshots (empty).
        _drain_until(ws_a, ["seats"])
        _drain_until(ws_b, ["seats"])

        # Alice joins; her socket must receive a seats update with her in it
        # BEFORE any hand_started arrives.
        client.post(
            "/api/tables/join",
            params={"as": "alice"},
            json={"code": code, "seat": 0, "buy_in": 1000},
        )
        msg = _drain_until(ws_a, ["seats"])
        assert msg["seats"][0] is not None and msg["seats"][0]["user_id"] == "alice"

        # Bob joins; both sockets should receive a seats update reflecting
        # both players seated, BEFORE hand_started.
        client.post(
            "/api/tables/join",
            params={"as": "bob"},
            json={"code": code, "seat": 1, "buy_in": 1000},
        )

        # On bob's socket, drain seats messages until both players are present.
        # Skip viewer_count events that arrive in between.
        for _ in range(20):
            bob_msg = ws_b.receive_json()
            if bob_msg["type"] != "seats":
                # viewer_count, etc. — not what we're looking for
                continue
            ids = {s["user_id"] for s in bob_msg["seats"] if s}
            if ids == {"alice", "bob"}:
                break
        else:
            raise AssertionError("never received seats with both players")

        # Now hand_started should be next.
        bob_started = _drain_until(ws_b, ["hand_started"])
        assert bob_started["state"]["pot_total"] == 15
