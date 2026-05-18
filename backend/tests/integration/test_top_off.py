"""Mid-hand top-off rejection test.

Lives in its own file (rather than alongside the other top-off tests in
test_play_a_hand.py) because a pytest WebSocket-portal teardown race
between this test and the showdown test in that file causes flaky
hangs. Running this test in isolation — or together with only itself
— works reliably. See conftest.py for context on the harness fragility.
"""
from __future__ import annotations

import os
from collections.abc import Iterable

import pytest
from fastapi.testclient import TestClient

from app.api.main import app


@pytest.fixture
def client():
    os.environ.pop("ELEVENLABS_API_KEY", None)
    with TestClient(app) as c:
        yield c


def _drain_until(ws, types: Iterable[str], cap: int = 30) -> dict:
    targets = set(types)
    for _ in range(cap):
        msg = ws.receive_json()
        if msg.get("type") in targets:
            return msg
    raise AssertionError(f"never received {types}")


def test_top_off_rejected_mid_hand(client: TestClient) -> None:
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
                json={"code": code, "seat": seat, "buy_in": 500},
            )
        _drain_until(ws_a, ["hand_started"])
        _drain_until(ws_b, ["hand_started"])
        # Hand is in progress. Top-off should be rejected.
        r = client.post(
            "/api/tables/top_off", params={"as": "alice"},
            json={"code": code, "amount": 100},
        )
        assert r.status_code == 400
        assert "mid-hand" in r.text.lower()


def test_top_off_rejected_after_bust(client: TestClient) -> None:
    """A busted player (removed from rt.seats at hand-complete) must be
    rejected by /api/tables/top_off with 'not seated at this table'.

    The frontend's `seated` computation depends on this 400 to route the
    user to the rebuy/join flow instead of leaving them on the top-off
    UI. If this rejection ever weakens — e.g. the backend starts silently
    re-seating the player, or returns a different error string — the
    frontend's RebuyCTA gate breaks and busted players see
    'Top off failed: not seated at this table' on the live site.
    """
    res = client.post(
        "/api/tables", params={"as": "alice"},
        json={"small_blind": 5, "big_blind": 10},
    )
    code = res.json()["code"]
    for who, seat in [("alice", 0), ("bob", 1)]:
        r = client.post(
            "/api/tables/join", params={"as": who},
            json={"code": code, "seat": seat, "buy_in": 1000},
        )
        assert r.status_code == 200, r.text

    # Simulate the bust: pop bob from rt.seats the same way the run-loop's
    # hand-complete handler does at table_manager.py L713 when a player's
    # final stack is 0. We do this directly rather than driving the engine
    # to a deterministic bust because the engine's deck is random and the
    # min buy-in (20*BB) is too large to bust in a single forced-blind
    # all-in without controlling cards.
    from app.services.table_manager import get_manager
    rt = get_manager().get_by_code(code)
    assert rt is not None
    bob_seat = next(n for n, s in rt.seats.items() if s.user_id == "bob")
    del rt.seats[bob_seat]

    r = client.post(
        "/api/tables/top_off", params={"as": "bob"},
        json={"code": code, "amount": 100},
    )
    assert r.status_code == 400, r.text
    assert "not seated" in r.text.lower(), r.text
