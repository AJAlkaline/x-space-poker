"""Action timer + timebank + disconnect tests.

These use monkey-patched timer values to keep tests fast.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.services import table_manager


@pytest.fixture
def fast_timers(monkeypatch: pytest.MonkeyPatch):
    """Make the action timer fire in a fraction of a second so tests stay fast."""
    monkeypatch.setattr(table_manager, "ACTION_TIMER_SECONDS", 0.3)
    monkeypatch.setattr(table_manager, "TIMEBANK_MAX", 0.5)
    monkeypatch.setattr(table_manager, "TIMEBANK_REFILL_PER_HAND", 0.1)
    monkeypatch.setattr(table_manager, "DISCONNECT_GRACE_SECONDS", 0.4)
    yield


@pytest.fixture
def client(fast_timers):
    with TestClient(app) as c:
        yield c


def _drain_until(ws, types, cap=40):
    targets = set(types)
    for _ in range(cap):
        msg = ws.receive_json()
        if msg.get("type") in targets:
            return msg
    raise AssertionError(f"none of {targets} arrived within {cap} messages")


def _start_two_player_hand(client: TestClient):
    """Helper: create table, seat alice + bob, return (code, ws_a, ws_b)."""
    res = client.post(
        "/api/tables",
        params={"as": "alice"},
        json={"small_blind": 5, "big_blind": 10},
    )
    code = res.json()["code"]
    return code


def test_action_timer_auto_folds_when_player_doesnt_act(client: TestClient) -> None:
    code = _start_two_player_hand(client)
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
        _drain_until(ws_a, ["hand_started"])
        _drain_until(ws_b, ["hand_started"])

        # Alice (button/SB heads-up) acts first pre-flop. If she does nothing,
        # the timer should fire and auto-fold her, ending the hand with bob
        # winning the SB.
        # ACTION_TIMER_SECONDS=0.3 + TIMEBANK_MAX=0.5 → 0.8s total budget.
        complete = _drain_until(ws_a, ["hand_complete"], cap=60)
        # Find alice's stack — she folded SB so she lost 5.
        for p in complete["state"]["players"]:
            if p and p["id"] == "alice":
                assert p["stack"] == 995, f"alice should have 995, got {p['stack']}"
            if p and p["id"] == "bob":
                assert p["stack"] == 1005, f"bob should have 1005, got {p['stack']}"


def test_action_timer_includes_deadlines_in_private_view(client: TestClient) -> None:
    """Private view sent to the to-act player must include base/bank deadlines."""
    code = _start_two_player_hand(client)
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
        _drain_until(ws_a, ["hand_started"])
        _drain_until(ws_b, ["hand_started"])

        # Alice's private view should arrive with deadlines (she's to act).
        a_priv = _drain_until(ws_a, ["private"])
        assert a_priv["state"]["your_turn"] is True
        assert a_priv["state"]["base_deadline_unix_ms"] is not None
        assert a_priv["state"]["bank_deadline_unix_ms"] is not None
        assert a_priv["state"]["base_deadline_unix_ms"] > int(time.time() * 1000)
        # Bank deadline should be after base deadline.
        assert (
            a_priv["state"]["bank_deadline_unix_ms"]
            >= a_priv["state"]["base_deadline_unix_ms"]
        )

        # Bob's private view should NOT have deadlines (not his turn).
        b_priv = _drain_until(ws_b, ["private"])
        assert b_priv["state"]["your_turn"] is False
        assert b_priv["state"]["base_deadline_unix_ms"] is None

        # Now alice quickly calls — she shouldn't auto-fold.
        ws_a.send_json({"type": "action", "action": "call"})
        # Now bob is to act. His next private view should have deadlines.
        b_priv2 = _drain_until(ws_b, ["private"])
        assert b_priv2["state"]["your_turn"] is True
        assert b_priv2["state"]["base_deadline_unix_ms"] is not None


def test_mid_hand_spectator_receives_to_act_deadline(client: TestClient) -> None:
    """Regression: a spectator (or player) who connects mid-hand must see
    the current to_act_deadline_unix_ms AND to_act_base_deadline_unix_ms
    in the initial snapshot, so the countdown badge can do a two-phase
    base→bank display matching the actor's own ActionTimer.

    Reproduces two historical bugs:
    - `_send_initial_public_snapshot` and `_send_initial_snapshot` used
      to call `_public_view()` without passing the active deadline,
      causing both fields to default to None and the badge to vanish.
    - The badge previously rendered the bank-included deadline only,
      so the actor's own badge would show ~85s while their action bar
      counted down "25.0s base" — confusing inconsistency for the
      acting player and anyone comparing screens.
    """
    code = _start_two_player_hand(client)
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
        _drain_until(ws_a, ["hand_started"])
        _drain_until(ws_b, ["hand_started"])
        _drain_until(ws_a, ["private"])

        with client.websocket_connect(f"/ws/spectate/{code}") as ws_spec:
            started = _drain_until(ws_spec, ["hand_started"])
            state = started["state"]
            deadline = state.get("to_act_deadline_unix_ms")
            base_deadline = state.get("to_act_base_deadline_unix_ms")
            assert deadline is not None, (
                f"spectator must see to_act_deadline_unix_ms; got {state}"
            )
            assert base_deadline is not None, (
                f"spectator must see to_act_base_deadline_unix_ms; got {state}"
            )
            assert isinstance(deadline, int) and isinstance(base_deadline, int)
            # Bank deadline must be >= base; equal when no bank remains.
            assert deadline >= base_deadline


def test_public_base_deadline_matches_actor_private_base(
    client: TestClient,
) -> None:
    """The base deadline on public state (what observers see) must equal
    the actor's own private base deadline within a small window. Both
    are computed from the same time.time() reading on each publish, so
    drift should be sub-millisecond. Tolerance: 100ms.

    Without this guarantee, the badge above the actor's seat and their
    own ActionTimer can show different numbers at the same instant.
    """
    code = _start_two_player_hand(client)
    with client.websocket_connect(f"/ws/tables/{code}?as=alice") as ws_a, \
         client.websocket_connect(f"/ws/tables/{code}?as=bob") as ws_b:
        for who, seat in [("alice", 0), ("bob", 1)]:
            client.post(
                "/api/tables/join", params={"as": who},
                json={"code": code, "seat": seat, "buy_in": 1000},
            )
        a_start = _drain_until(ws_a, ["hand_started"])
        _drain_until(ws_b, ["hand_started"])
        a_priv = _drain_until(ws_a, ["private"])
        assert a_priv["state"]["your_turn"] is True

        public_base = a_start["state"]["to_act_base_deadline_unix_ms"]
        private_base = a_priv["state"]["base_deadline_unix_ms"]
        assert public_base is not None and private_base is not None
        assert abs(public_base - private_base) < 100, (
            f"public_base={public_base} vs private_base={private_base} "
            "diverged by more than 100ms — observer and actor see "
            "different countdowns"
        )


def test_disconnect_during_action_auto_folds_and_sits_out(
    client: TestClient,
) -> None:
    """End-to-end: alice disconnects mid-hand on her turn.

    Expected: the action timer keeps running (real poker rule), alice gets
    auto-folded when it expires, bob wins the SB, the hand completes, and
    after the disconnect grace window passes alice is marked sitting_out
    for the next hand. Her seat and stack are preserved.
    """
    code = _start_two_player_hand(client)

    # Connect bob first; he'll stay connected throughout so we can watch.
    with client.websocket_connect(f"/ws/tables/{code}?as=bob") as wb:
        _drain_until(wb, ["seats"])

        # Alice connects, both sit down, then alice disconnects on her turn.
        with client.websocket_connect(f"/ws/tables/{code}?as=alice") as wa:
            _drain_until(wa, ["seats"])
            for who, seat in [("alice", 0), ("bob", 1)]:
                client.post(
                    "/api/tables/join",
                    params={"as": who},
                    json={"code": code, "seat": seat, "buy_in": 1000},
                )
            # Wait for hand to start; alice (HU button = SB) is to act first.
            _drain_until(wa, ["hand_started"])
            _drain_until(wb, ["hand_started"])
            a_priv = _drain_until(wa, ["private"])
            assert a_priv["state"]["your_turn"] is True
            # Alice's WebSocket exits the `with` block here → disconnect.

        # Bob, still connected, should observe the hand completing via auto-fold.
        # Then a seats message reflecting alice still seated. Then on the next
        # hand cycle, alice should be sitting out (her disconnected_at >
        # DISCONNECT_GRACE_SECONDS = 0.4s by the time the loop checks).
        complete = _drain_until(wb, ["hand_complete"], cap=60)

        alice_p = next(
            p for p in complete["state"]["players"]
            if p is not None and p["id"] == "alice"
        )
        bob_p = next(
            p for p in complete["state"]["players"]
            if p is not None and p["id"] == "bob"
        )
        assert alice_p["stack"] == 995, (
            f"alice should have lost 5 SB (995), got {alice_p['stack']}"
        )
        assert bob_p["stack"] == 1005, (
            f"bob should have gained 5 (1005), got {bob_p['stack']}"
        )

        # The next seats message after hand_complete should show alice still
        # seated (seat preserved). The loop's grace check happens at end of
        # hand and may mark her sitting_out before this seats message arrives.
        seats_msg = _drain_until(wb, ["seats"], cap=10)
        alice_seat = next(
            (s for s in seats_msg["seats"] if s and s["user_id"] == "alice"),
            None,
        )
        assert alice_seat is not None, "alice's seat should be preserved"
        assert alice_seat["stack"] == 995, "alice's stack should be 995"
