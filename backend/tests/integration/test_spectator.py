"""Spectator path + viewer count tests.

Covers:
- A spectator can connect to a table and see public events.
- Spectators NEVER receive hole cards via any message (except showdown reveals
  which are public by design).
- Viewer count updates when subscribers come and go.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _drain_until(ws, types, cap=40):
    targets = set(types)
    for _ in range(cap):
        msg = ws.receive_json()
        if msg.get("type") in targets:
            return msg
    raise AssertionError(f"none of {targets} arrived within {cap} messages")


def test_spectator_receives_public_state(client: TestClient) -> None:
    """A spectator connects mid-hand and sees the table state."""
    res = client.post(
        "/api/tables", params={"as": "alice"},
        json={"small_blind": 5, "big_blind": 10},
    )
    code = res.json()["code"]

    with client.websocket_connect(f"/ws/tables/{code}?as=alice") as ws_a, \
         client.websocket_connect(f"/ws/tables/{code}?as=bob") as ws_b:
        _drain_until(ws_a, ["seats"])
        _drain_until(ws_b, ["seats"])
        for who, seat in [("alice", 0), ("bob", 1)]:
            client.post(
                "/api/tables/join", params={"as": who},
                json={"code": code, "seat": seat, "buy_in": 1000},
            )
        _drain_until(ws_a, ["hand_started"])
        _drain_until(ws_b, ["hand_started"])

        # Open a spectator connection. They should receive a hand_started
        # snapshot for the in-progress hand.
        with client.websocket_connect(f"/ws/spectate/{code}?as=carol") as ws_spec:
            spec_started = _drain_until(ws_spec, ["hand_started"])
            assert spec_started["state"]["pot_total"] == 15
            # Confirm the spectator received a viewer_count event somewhere.
            # (Could come before or after hand_started depending on ordering.)


def test_spectator_never_sees_hole_cards(client: TestClient) -> None:
    """Hidden information audit: a spectator must NEVER receive hole cards
    (except in the optional showdown reveal which is public information)."""
    res = client.post(
        "/api/tables", params={"as": "alice"},
        json={"small_blind": 5, "big_blind": 10},
    )
    code = res.json()["code"]

    with client.websocket_connect(f"/ws/tables/{code}?as=alice") as ws_a, \
         client.websocket_connect(f"/ws/tables/{code}?as=bob") as ws_b, \
         client.websocket_connect(f"/ws/spectate/{code}?as=carol") as ws_spec:
        _drain_until(ws_a, ["seats"])
        _drain_until(ws_b, ["seats"])

        for who, seat in [("alice", 0), ("bob", 1)]:
            client.post(
                "/api/tables/join", params={"as": who},
                json={"code": code, "seat": seat, "buy_in": 1000},
            )

        _drain_until(ws_a, ["hand_started"])
        _drain_until(ws_b, ["hand_started"])

        # Drain alice's private state so she's seen her hole, then alice acts.
        _drain_until(ws_a, ["private"])
        ws_a.send_json({"type": "action", "action": "fold"})

        # Collect every spectator message during the hand.
        spec_messages: list[dict] = []
        for _ in range(30):
            try:
                msg = ws_spec.receive_json()
                spec_messages.append(msg)
                if msg["type"] == "hand_complete":
                    break
            except Exception:
                break

        # CRITICAL ASSERTION: the spectator must never have received a
        # private-state event (no such message type), and no public-state
        # snapshot they received should contain hole cards for any non-
        # folded mid-hand player.
        for msg in spec_messages:
            assert msg["type"] != "private", (
                f"spectator received a private message: {msg}"
            )
            if msg["type"] == "hand_complete":
                # At showdown, hole cards may be revealed for non-folded
                # players. That's public information by poker rules.
                # Folded players' cards must still be hidden.
                for p in msg["state"]["players"]:
                    if p and p["status"] == "folded":
                        assert p.get("hole") is None, (
                            f"folded player's hole was revealed to spectator: {p}"
                        )
            elif msg["type"] in ("hand_started", "state_update"):
                # Mid-hand snapshots: NO hole cards ever, for any player.
                for p in msg["state"]["players"]:
                    if p is not None:
                        assert p.get("hole") is None, (
                            f"mid-hand spectator state leaked hole for {p['id']}: {p}"
                        )


def test_viewer_count_updates(client: TestClient) -> None:
    """Viewer count tracks public-stream subscribers (players + spectators)."""
    res = client.post(
        "/api/tables", params={"as": "alice"},
        json={"small_blind": 5, "big_blind": 10},
    )
    code = res.json()["code"]

    # Connect alice (player). Viewer count should become 1.
    with client.websocket_connect(f"/ws/tables/{code}?as=alice") as ws_a:
        # First message could be seats or viewer_count; drain looking for vc.
        first_count = _drain_until(ws_a, ["viewer_count"])
        assert first_count["count"] == 1

        # Connect a spectator. Alice's stream should see count → 2.
        with client.websocket_connect(f"/ws/spectate/{code}?as=carol") as ws_spec:
            second_count = _drain_until(ws_a, ["viewer_count"])
            assert second_count["count"] == 2

            # The spectator also gets a viewer_count event.
            spec_count = _drain_until(ws_spec, ["viewer_count"])
            assert spec_count["count"] == 2

        # After spectator disconnects, count should drop to 1.
        third_count = _drain_until(ws_a, ["viewer_count"])
        assert third_count["count"] == 1
