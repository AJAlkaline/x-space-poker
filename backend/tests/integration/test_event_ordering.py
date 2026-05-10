"""Regression test for the public/private event ordering bug.

User-reported repro:
  "I believe it was a situation in which player AJ had just called for 10
   and then their turn happened again (turn order swapping from heads-up?),
   but instead of the UI updating for the new street it showed the same as
   previously (so, still call 10 instead of check)."

Server log: `illegal action ActionType.CALL for player AJ
            (legal: {FOLD, BET, CHECK})`.

That tells us the *server* was in the right state (post-flop, current_bet=0,
to_call=0). The client had stale legal_actions from a prior private state
event because public and private events were going through separate queues
on the WebSocket, with no ordering guarantee between them. After the bus
rework merging player events into a single ordered queue, the client's
last-received private state for the to-act player must reflect the current
street's legal actions.

This test asserts ordering: the client's last-received `private` event
before submitting the next action must include CHECK (not CALL) when the
state has advanced to a new street with current_bet=0.
"""
from __future__ import annotations

from collections.abc import Iterable

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.services import table_manager


@pytest.fixture
def fast_timers(monkeypatch: pytest.MonkeyPatch):
    """Shrink action timers so the post-test cleanup doesn't wait 85s for
    auto-fold cycles to fire on disconnected players."""
    monkeypatch.setattr(table_manager, "ACTION_TIMER_SECONDS", 0.3)
    monkeypatch.setattr(table_manager, "TIMEBANK_MAX", 0.5)
    monkeypatch.setattr(table_manager, "TIMEBANK_REFILL_PER_HAND", 0.1)
    monkeypatch.setattr(table_manager, "DISCONNECT_GRACE_SECONDS", 0.4)
    yield


@pytest.fixture
def client(fast_timers):
    with TestClient(app) as c:
        yield c


def _drain_until(ws, types: Iterable[str], cap: int = 30) -> dict:
    targets = set(types)
    for _ in range(cap):
        msg = ws.receive_json()
        if msg.get("type") in targets:
            return msg
    raise AssertionError(f"none of {targets} arrived within {cap} messages")


def _drain_collect(ws, until_type: str, cap: int = 30) -> list[dict]:
    """Drain messages, collecting all of them, until one of `until_type`
    is seen (inclusive). Returns the list."""
    out: list[dict] = []
    for _ in range(cap):
        msg = ws.receive_json()
        out.append(msg)
        if msg.get("type") == until_type:
            return out
    raise AssertionError(f"no {until_type} within {cap} messages")


def test_to_act_player_gets_fresh_legals_after_street_advance(
    client: TestClient,
) -> None:
    """Heads-up, alice (button/SB) calls pre-flop, bob (BB) checks. Phase
    advances to flop. Heads-up post-flop: SB (alice) acts first.

    Alice's last-received `private` event before her flop turn must show
    CHECK as legal, not the stale CALL from pre-flop. The bus rework that
    merges public+private into a single ordered queue should make this
    deterministic.
    """
    res = client.post(
        "/tables", params={"as": "alice"},
        json={"small_blind": 5, "big_blind": 10},
    )
    code = res.json()["code"]

    with client.websocket_connect(f"/ws/tables/{code}?as=alice") as ws_a, \
         client.websocket_connect(f"/ws/tables/{code}?as=bob") as ws_b:
        _drain_until(ws_a, ["seats"])
        _drain_until(ws_b, ["seats"])
        for who, seat in [("alice", 0), ("bob", 1)]:
            client.post(
                "/tables/join", params={"as": who},
                json={"code": code, "seat": seat, "buy_in": 1000},
            )

        # Wait for hand to start. Heads-up: button=alice (SB)=first to act pre-flop.
        _drain_until(ws_a, ["hand_started"])
        _drain_until(ws_b, ["hand_started"])

        # Drain alice's private to confirm she's to act with CALL.
        a_priv1 = _drain_until(ws_a, ["private"])
        assert a_priv1["state"]["your_turn"] is True
        legal_types_pre = {a["action_type"] for a in a_priv1["state"]["legal_actions"]}
        assert "call" in legal_types_pre, (
            f"pre-flop alice should have CALL, got {legal_types_pre}"
        )

        # Alice calls. Now bob is to act.
        ws_a.send_json({"type": "action", "action": "call"})

        # Drain alice's stream up through her next private event after she acted.
        # We want: state_update (from her call) → private (your_turn=false) → ...
        # → state_update (from bob's check or another action that ends preflop) →
        # eventually her flop private with CHECK as legal.

        # Drain bob's stream looking for his to-act private (skipping the
        # initial pre-hand snapshot if present).
        b_priv1 = None
        for _ in range(20):
            msg = ws_b.receive_json()
            if msg.get("type") == "private" and msg["state"]["your_turn"]:
                b_priv1 = msg
                break
        assert b_priv1 is not None, "bob never got a to-act private"

        # Bob checks (BB option, post-call).
        ws_b.send_json({"type": "action", "action": "check"})

        # Phase advances to flop. Heads-up post-flop: alice (SB) acts first.
        # Alice's stream should now deliver, in order:
        #   1. state_update (her own call) at some earlier point
        #   2. state_update (bob's check, advances phase)
        #   3. private (alice's flop turn — your_turn=true, legals=[fold, check, bet])
        #
        # The KEY ordering we're testing: by the time alice receives a `private`
        # with your_turn=true on the flop, the legals must be [fold, check, bet].
        # If the bus delivered private before public, alice's private from
        # pre-flop ([fold, call, raise]) would be the last one she has.

        # Drain alice's stream until we hit the flop's hand_started or a
        # state_update with phase=flop. Then her to-act private should arrive.
        flop_state_seen = False
        a_flop_priv = None
        for _ in range(40):
            msg = ws_a.receive_json()
            t = msg.get("type")
            if t == "state_update" and msg["state"]["phase"] == "flop":
                flop_state_seen = True
            elif t == "private" and flop_state_seen and msg["state"]["your_turn"]:
                a_flop_priv = msg
                break

        assert a_flop_priv is not None, "alice never received a flop to-act private"
        flop_legals = {a["action_type"] for a in a_flop_priv["state"]["legal_actions"]}
        assert "check" in flop_legals, (
            f"flop legals must include CHECK, got {flop_legals}"
        )
        assert "call" not in flop_legals, (
            f"flop legals must NOT include CALL (current_bet=0), got {flop_legals}"
        )

        # Fold to end the hand quickly — otherwise the test waits for the
        # action timer to fire on bob's flop turn (≈85s with default config).
        ws_a.send_json({"type": "action", "action": "fold"})


def test_player_queue_orders_public_and_private(client: TestClient) -> None:
    """At the bus level, public and private events go through a single
    queue in publish order. Verify by checking that alice's stream after
    her own action contains the public state-update for her action BEFORE
    any subsequent private event — the order they were published.
    """
    res = client.post(
        "/tables", params={"as": "alice"},
        json={"small_blind": 5, "big_blind": 10},
    )
    code = res.json()["code"]

    with client.websocket_connect(f"/ws/tables/{code}?as=alice") as ws_a, \
         client.websocket_connect(f"/ws/tables/{code}?as=bob") as ws_b:
        _drain_until(ws_a, ["seats"])
        _drain_until(ws_b, ["seats"])
        for who, seat in [("alice", 0), ("bob", 1)]:
            client.post(
                "/tables/join", params={"as": who},
                json={"code": code, "seat": seat, "buy_in": 1000},
            )
        _drain_until(ws_a, ["hand_started"])
        _drain_until(ws_b, ["hand_started"])
        _drain_until(ws_a, ["private"])  # alice's pre-flop private

        # Alice calls. Server publishes (in order):
        #   - public ActionApplied with public_state showing alice's commit
        #   - private to alice with your_turn=false (sent as part of the
        #     "publish private to all non-to-act players" loop)
        # The client should see them in that order.
        ws_a.send_json({"type": "action", "action": "call"})

        # Read alice's next two messages and check ordering.
        m1 = ws_a.receive_json()
        m2 = ws_a.receive_json()
        # The state_update must come before the private, because the server
        # publishes them in that order and the single queue preserves it.
        assert m1["type"] == "state_update", (
            f"expected state_update first, got {m1['type']} (m2={m2['type']})"
        )
        assert m2["type"] == "private", (
            f"expected private second, got {m2['type']}"
        )
        assert m2["state"]["your_turn"] is False, (
            "alice should no longer be to-act after her call"
        )

        # End the hand quickly so the test doesn't wait for the action timer
        # on bob's pre-flop turn.
        ws_b.send_json({"type": "action", "action": "fold"})
