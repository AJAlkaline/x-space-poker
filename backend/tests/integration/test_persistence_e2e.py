"""End-to-end persistence test: persistence_enabled=True, play a hand, query replay.

Wires up the in-memory SQLite override before the FastAPI app handles any
requests. Uses a fresh module-level singleton (table manager) per test by
clearing it via fixture.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from app.api.main import app
from app.core.config import get_settings
from app.db import session as db_session
from app.db.models import Base
from app.services import table_manager


@pytest.fixture
async def persistent_app(monkeypatch):
    """Set up an in-memory SQLite engine, enable persistence, reset the
    table manager singleton."""
    # Spin up SQLite, create schema.
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    db_session.set_engine(engine)

    # Enable persistence in settings (modify the cached singleton).
    get_settings.cache_clear()
    monkeypatch.setenv("PERSISTENCE_ENABLED", "true")
    settings = get_settings()
    assert settings.persistence_enabled

    # Reset table manager singleton.
    monkeypatch.setattr(table_manager, "_manager", None)

    yield

    # Teardown: reset everything.
    get_settings.cache_clear()
    db_session.set_engine(None)  # type: ignore[arg-type]
    await engine.dispose()


@pytest.fixture
def client(persistent_app):
    with TestClient(app) as c:
        yield c


def _drain_until(ws, types, cap=40):
    targets = set(types)
    for _ in range(cap):
        msg = ws.receive_json()
        if msg.get("type") in targets:
            return msg
    raise AssertionError(f"none of {targets} arrived within {cap} messages")


def test_buy_in_persists_to_db(client: TestClient) -> None:
    """A buy-in should debit the persisted account, and /auth/me must
    reflect the DB balance (not the in-memory wallet)."""
    res = client.post(
        "/api/tables", params={"as": "alice"},
        json={"small_blind": 5, "big_blind": 10},
    )
    assert res.status_code == 200, res.text
    code = res.json()["code"]

    # Initial balance: 10_000 (from ensure_account_for_handle's signup grant).
    res = client.get("/auth/me", params={"as": "alice"})
    assert res.status_code == 200
    assert res.json()["balance"] == 10_000

    # Join debits 1000.
    res = client.post(
        "/api/tables/join", params={"as": "alice"},
        json={"code": code, "seat": 0, "buy_in": 1000},
    )
    assert res.status_code == 200, res.text

    # /auth/me should now reflect the debit. Before the wart fix this
    # returned 10_000 (in-memory wallet untouched); now it returns 9_000.
    res = client.get("/auth/me", params={"as": "alice"})
    assert res.status_code == 200
    assert res.json()["balance"] == 9_000


def test_completed_hand_is_replayable(client: TestClient) -> None:
    """Play a hand to completion, then GET /tables/hands/<id>/replay.

    Asserts that the returned snapshots are sequential, plausible, and
    that the final snapshot reveals showdown information.
    """
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

        # Wait for hand to start, capture hand_id from the started event.
        started = _drain_until(ws_a, ["hand_started"])
        hand_id = started["state"]["hand_id"]
        _drain_until(ws_b, ["hand_started"])
        _drain_until(ws_a, ["private"])
        _drain_until(ws_b, ["private"])

        # Alice folds; bob wins SB.
        ws_a.send_json({"type": "action", "action": "fold"})
        _drain_until(ws_a, ["hand_complete"])
        _drain_until(ws_b, ["hand_complete"])

    # Give the persistence consumer a tick to flush.
    import time as _t
    _t.sleep(0.2)

    # Replay endpoint should return the hand with action log + snapshots.
    res = client.get(f"/api/tables/hands/{hand_id}/replay")
    assert res.status_code == 200, res.text
    replay = res.json()
    assert replay["hand_id"] == hand_id
    assert replay["deck_seed_reveal"]
    assert replay["deck_seed_commit"]
    assert replay["start_state"] is not None, "start_state should be captured"

    # We expect at least 1 action — alice's fold.
    assert len(replay["actions"]) >= 1
    fold_actions = [a for a in replay["actions"] if a["action_type"] == "fold"]
    assert len(fold_actions) == 1

    # Action handles should be resolved (not bare UUIDs).
    for action in replay["actions"]:
        assert action["handle"] in ("alice", "bob"), (
            f"action handle should resolve to alice/bob, got {action['handle']!r}"
        )

    # Snapshots: at minimum the initial deal + one per action.
    snapshots = replay["snapshots"]
    assert snapshots is not None, "snapshots should be reconstructed"
    assert len(snapshots) >= 2, f"expected ≥2 snapshots, got {len(snapshots)}"

    # First snapshot has no action.
    assert snapshots[0]["action"] is None
    assert snapshots[0]["public_state"]["phase"] == "pre_flop"

    # Last snapshot is the showdown / hand-end state with revealed holes
    # for non-folded players.
    final = snapshots[-1]
    assert final["public_state"]["phase"] in ("complete", "showdown")
    # Bob (didn't fold) should have his hole cards revealed.
    bob_player = next(
        p for p in final["public_state"]["players"]
        if p is not None and p["id"] == "bob"
    )
    assert bob_player["hole"] is not None and len(bob_player["hole"]) == 2, (
        "bob's hole cards should be revealed at hand end"
    )


def test_table_hands_list_returns_completed_hands(client: TestClient) -> None:
    """GET /tables/<code>/hands lists recent completed hands for the table."""
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
        _drain_until(ws_a, ["private"])
        _drain_until(ws_b, ["private"])

        ws_a.send_json({"type": "action", "action": "fold"})
        _drain_until(ws_a, ["hand_complete"])
        _drain_until(ws_b, ["hand_complete"])

    import time as _t
    _t.sleep(0.2)

    res = client.get(f"/api/tables/{code}/hands")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["code"] == code
    assert len(body["hands"]) >= 1
    h = body["hands"][0]
    assert h["hand_id"]
    assert h["hand_number"] >= 1
    assert h["started_at"]


def test_hands_list_404_for_unknown_table(client: TestClient) -> None:
    res = client.get("/api/tables/NOPE99/hands")
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_recovery_rehydrates_table(persistent_app, monkeypatch) -> None:
    """A table created and seated, then 'restarted', should come back from DB."""
    from app.services.recovery import recover_tables
    from app.services.table_manager import get_manager

    # First "session": create a table and seat alice.
    with TestClient(app) as c:
        res = c.post(
            "/api/tables", params={"as": "alice"},
            json={"small_blind": 5, "big_blind": 10},
        )
        assert res.status_code == 200
        code = res.json()["code"]
        table_id = res.json()["table_id"]
        res = c.post(
            "/api/tables/join", params={"as": "alice"},
            json={"code": code, "seat": 0, "buy_in": 1000},
        )
        assert res.status_code == 200

    # Simulate restart: clear the table manager singleton so it starts empty.
    monkeypatch.setattr(table_manager, "_manager", None)
    mgr = get_manager()
    assert mgr.get_by_code(code) is None  # Empty after "restart"

    # Run recovery.
    await recover_tables()

    # Table should be back with its original code.
    rt = mgr.get_by_code(code)
    assert rt is not None
    assert rt.table_id == table_id
    # Alice should be seated.
    assert any(s.user_id == "alice" for s in rt.seats.values())
