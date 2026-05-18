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


def test_hand_complete_carries_next_hand_deadline(client: TestClient) -> None:
    """hand_complete advertises the absolute unix-ms deadline for the
    auto-start of the next hand. Frontend renders a countdown from this
    plus a "waiting for players" banner if the deadline expires without
    a hand_started arriving (loop is gated on >=2 eligible seats).
    Field name: next_hand_starts_at_unix_ms."""
    import time

    res = client.post(
        "/api/tables", params={"as": "alice"},
        json={"small_blind": 5, "big_blind": 10},
    )
    code = res.json()["code"]

    with client.websocket_connect(f"/ws/tables/{code}?as=alice") as ws_a, \
         client.websocket_connect(f"/ws/tables/{code}?as=bob") as ws_b:
        for who, seat in [("alice", 0), ("bob", 1)]:
            client.post(
                "/api/tables/join", params={"as": who},
                json={"code": code, "seat": seat, "buy_in": 1000},
            )
        _drain_until(ws_a, ["hand_started"])
        _drain_until(ws_b, ["hand_started"])

        # Alice (button heads-up pre-flop) folds → fold-win, ~3s pause.
        priv = _drain_until(ws_a, ["private"])
        assert priv["state"]["your_turn"] is True
        sent_at_ms = int(time.time() * 1000)
        ws_a.send_json({"type": "action", "action": "fold"})

        complete = _drain_until(ws_a, ["hand_complete"])
        assert "next_hand_starts_at_unix_ms" in complete, complete
        deadline = complete["next_hand_starts_at_unix_ms"]
        assert isinstance(deadline, int) and deadline > 0
        # Fold-win pause is 3s. Allow generous slack for test scheduling.
        delta_ms = deadline - sent_at_ms
        assert 2_000 <= delta_ms <= 6_000, (
            f"fold-win deadline should be ~3s out, got delta={delta_ms}ms"
        )


def test_hand_complete_includes_pot_distributions(client: TestClient) -> None:
    """hand_complete carries pot_distributions describing who won each pot,
    with hand description and best-five for showdown winners."""
    res = client.post(
        "/api/tables", params={"as": "alice"},
        json={"small_blind": 5, "big_blind": 10},
    )
    code = res.json()["code"]

    with client.websocket_connect(f"/ws/tables/{code}?as=alice") as ws_a, \
         client.websocket_connect(f"/ws/tables/{code}?as=bob") as ws_b:
        for who, seat in [("alice", 0), ("bob", 1)]:
            client.post(
                "/api/tables/join", params={"as": who},
                json={"code": code, "seat": seat, "buy_in": 1000},
            )
        _drain_until(ws_a, ["hand_started"])
        _drain_until(ws_b, ["hand_started"])

        # Alice (button heads-up) folds. Bob wins by fold, not showdown.
        priv = _drain_until(ws_a, ["private"])
        assert priv["state"]["your_turn"] is True
        ws_a.send_json({"type": "action", "action": "fold"})

        complete = _drain_until(ws_a, ["hand_complete"])
        assert "pot_distributions" in complete
        dists = complete["pot_distributions"]
        assert len(dists) == 1
        # The pot.amount field is the matched committed chips (alice's 5 SB
        # matched against 5 of bob's 10 BB = 10 matched; bob's other 5 is
        # refunded as uncalled). So pot is 10, not 15.
        assert dists[0]["amount"] == 10
        winners = dists[0]["winners"]
        assert len(winners) == 1
        assert winners[0]["player_id"] == "bob"
        # Fold-win has no showdown — empty hand description and best_five.
        assert winners[0]["hand_description"] == ""
        assert winners[0]["best_five"] == []


def test_players_have_position_labels(client: TestClient) -> None:
    """At hand_started, each player has a `position` label like BTN/BB/UTG."""
    res = client.post(
        "/api/tables", params={"as": "alice"},
        json={"small_blind": 5, "big_blind": 10},
    )
    code = res.json()["code"]

    with client.websocket_connect(f"/ws/tables/{code}?as=alice") as ws_a, \
         client.websocket_connect(f"/ws/tables/{code}?as=bob") as ws_b:
        for who, seat in [("alice", 0), ("bob", 1)]:
            client.post(
                "/api/tables/join", params={"as": who},
                json={"code": code, "seat": seat, "buy_in": 1000},
            )
        started = _drain_until(ws_a, ["hand_started"])
        _drain_until(ws_b, ["hand_started"])

        positions = {
            p["id"]: p.get("position")
            for p in started["state"]["players"]
            if p is not None
        }
        # Heads-up: button player is BTN (also posts SB), the other is BB.
        # Alice has seat 0 — depending on button placement she's BTN or BB.
        # We just verify both are populated and one of each kind.
        labels = set(positions.values())
        assert "BTN" in labels
        assert "BB" in labels


def test_hand_number_is_on_public_state(client: TestClient) -> None:
    """The wire's `hand_started.state.hand_number` carries the session
    hand counter — needed by the narrator to announce milestone hands
    correctly. Regression: previously the field wasn't included on the
    public_view dict, so the narrator always saw 0 and announced 'Hand
    number 0' on every hand from #2 onwards."""
    res = client.post(
        "/api/tables", params={"as": "alice"},
        json={"small_blind": 5, "big_blind": 10},
    )
    code = res.json()["code"]
    with client.websocket_connect(f"/ws/tables/{code}?as=alice") as ws_a, \
         client.websocket_connect(f"/ws/tables/{code}?as=bob") as ws_b:
        for who, seat in [("alice", 0), ("bob", 1)]:
            client.post(
                "/api/tables/join", params={"as": who},
                json={"code": code, "seat": seat, "buy_in": 1000},
            )
        started = _drain_until(ws_a, ["hand_started"])
        _drain_until(ws_b, ["hand_started"])
        assert started["state"].get("hand_number") == 1


def test_current_hand_is_on_private_state_post_flop(client: TestClient) -> None:
    """The player's private state carries `current_hand`: a description of
    their best 5-card hand right now, populated on the flop and later.
    Pre-flop it's null (only 2 cards visible)."""
    res = client.post(
        "/api/tables", params={"as": "alice"},
        json={"small_blind": 5, "big_blind": 10},
    )
    code = res.json()["code"]
    with client.websocket_connect(f"/ws/tables/{code}?as=alice") as ws_a, \
         client.websocket_connect(f"/ws/tables/{code}?as=bob") as ws_b:
        for who, seat in [("alice", 0), ("bob", 1)]:
            client.post(
                "/api/tables/join", params={"as": who},
                json={"code": code, "seat": seat, "buy_in": 1000},
            )
        _drain_until(ws_a, ["hand_started"])
        _drain_until(ws_b, ["hand_started"])

        # Drain through to the flop. We expect `current_hand` to be null
        # for any private messages we see pre-flop, and populated for any
        # message we see at flop or later.
        sockets = {"alice": ws_a, "bob": ws_b}
        seen_preflop_null = False
        seen_postflop_populated = False
        for _ in range(40):
            for _who, ws in sockets.items():
                msg = ws.receive_json()
                t = msg.get("type")
                if t == "private":
                    ch = msg["state"].get("current_hand")
                    # The pre-flop case: hole cards only, no flop yet.
                    if ch is None and not seen_postflop_populated:
                        seen_preflop_null = True
                    elif ch is not None:
                        # Got a populated current_hand. Verify shape.
                        assert "description" in ch
                        assert ch["description"]  # non-empty
                        assert len(ch["best_five"]) == 5
                        seen_postflop_populated = True
                if (
                    t == "private"
                    and msg["state"]["your_turn"]
                    and not seen_postflop_populated
                ):
                    legals = [la["action_type"] for la in msg["state"]["legal_actions"]]
                    if "check" in legals:
                        ws.send_json({"type": "action", "action": "check"})
                    elif "call" in legals:
                        ws.send_json({"type": "action", "action": "call"})
            if seen_postflop_populated:
                break
        assert seen_preflop_null, "never saw pre-flop private with current_hand=null"
        assert seen_postflop_populated, "never saw post-flop private with populated current_hand"


def test_showdown_emits_hand_description_and_best_five(client: TestClient) -> None:
    """When a hand goes to showdown, pot_distributions winners carry both
    a hand description and the 5 cards that make up the winning hand."""
    res = client.post(
        "/api/tables", params={"as": "alice"},
        json={"small_blind": 5, "big_blind": 10},
    )
    code = res.json()["code"]

    with client.websocket_connect(f"/ws/tables/{code}?as=alice") as ws_a, \
         client.websocket_connect(f"/ws/tables/{code}?as=bob") as ws_b:
        for who, seat in [("alice", 0), ("bob", 1)]:
            client.post(
                "/api/tables/join", params={"as": who},
                json={"code": code, "seat": seat, "buy_in": 1000},
            )
        _drain_until(ws_a, ["hand_started"])
        _drain_until(ws_b, ["hand_started"])

        # Both players just check/call through to showdown. Pump messages
        # from both sockets, acting whenever it's my turn. Bail when either
        # socket reports hand_complete.
        sockets = {"alice": ws_a, "bob": ws_b}
        complete: dict | None = None
        # Limit total iterations to avoid hanging.
        for _ in range(200):
            for _handle, ws in sockets.items():
                # Non-blocking poll: try to receive one message.
                msg = ws.receive_json()
                t = msg.get("type")
                if t == "hand_complete":
                    complete = msg
                    break
                if t == "private" and msg["state"]["your_turn"]:
                    # Pick the lowest-cost legal action: check, call, or fold.
                    legals = [la["action_type"] for la in msg["state"]["legal_actions"]]
                    if "check" in legals:
                        ws.send_json({"type": "action", "action": "check"})
                    elif "call" in legals:
                        ws.send_json({"type": "action", "action": "call"})
                    else:
                        # Shouldn't happen if we're calling/checking
                        ws.send_json({"type": "action", "action": "fold"})
            if complete:
                break

        assert complete is not None, "never reached hand_complete"
        dists = complete.get("pot_distributions", [])
        assert len(dists) == 1
        assert dists[0]["amount"] > 0
        winners = dists[0]["winners"]
        assert len(winners) >= 1

        # At least one winner has a hand description.
        # The winner has exactly 5 cards in best_five.
        for w in winners:
            assert w["hand_description"], (
                f"showdown winner should have non-empty description: {w}"
            )
            assert len(w["best_five"]) == 5, (
                f"best_five should be exactly 5 cards: {w}"
            )


def test_top_off_increases_seated_stack(client: TestClient) -> None:
    """A seated player can top off their stack between hands."""
    res = client.post(
        "/api/tables", params={"as": "alice"},
        json={"small_blind": 5, "big_blind": 10},
    )
    code = res.json()["code"]
    client.post(
        "/api/tables/join", params={"as": "alice"},
        json={"code": code, "seat": 0, "buy_in": 500},
    )
    # Stack is 500, max is 200 * 10 = 2000. Top off by 300 → 800.
    r = client.post(
        "/api/tables/top_off", params={"as": "alice"},
        json={"code": code, "amount": 300},
    )
    assert r.status_code == 200, r.text
    assert r.json()["stack"] == 800


def test_top_off_rejected_when_at_cap(client: TestClient) -> None:
    res = client.post(
        "/api/tables", params={"as": "alice"},
        json={"small_blind": 5, "big_blind": 10},
    )
    code = res.json()["code"]
    client.post(
        "/api/tables/join", params={"as": "alice"},
        json={"code": code, "seat": 0, "buy_in": 2000},
    )
    # Already at max (200 * 10). Any top-off exceeds cap.
    r = client.post(
        "/api/tables/top_off", params={"as": "alice"},
        json={"code": code, "amount": 100},
    )
    assert r.status_code == 400
    assert "cap" in r.text.lower()


# NOTE: test_top_off_rejected_mid_hand lives in test_top_off.py to avoid a
# pytest WebSocket-portal teardown race when running after the showdown
# test in this file. Same harness fragility documented in conftest.py.
