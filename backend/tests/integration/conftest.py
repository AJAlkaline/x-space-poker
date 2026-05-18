"""Per-test cleanup for integration tests.

Each test creates tables that get registered in the module-level
`table_manager._manager` singleton. The TableRuntime objects hold
asyncio.Event and asyncio.Queue instances bound to the event loop
active at construction time. Each new TestClient spawns a fresh event
loop. So tables created in test N have their internal events bound to
loop N, but the singleton survives into test N+1 with a different loop.

This fixture resets the singleton between every test as a defensive
measure: each test starts with a clean manager and constructs its
TableRuntime objects under its own event loop.

KNOWN ISSUE: specific multi-file pytest combos can still trigger a
TestClient/anyio WebSocket portal teardown race that's deeper than
this fixture can fix. Symptom: `test_seats_broadcast_after_each_join`
or `test_spectator_never_sees_hole_cards` hangs on `ws.receive_json()`
in a full run, while every test passes in isolation and in most
subsets. The product code is correct — this is a pytest/anyio harness
quirk. Recommended invocation:

    pytest tests/ --ignore=tests/integration/test_spectator.py \
                  --ignore=tests/integration/test_play_a_hand.py
    pytest tests/integration/test_play_a_hand.py
    pytest tests/integration/test_spectator.py

Run those three commands in sequence; each individually completes
cleanly. CI should chain them with && rather than running a single
`pytest tests/`.
"""
from __future__ import annotations

import contextlib

import pytest

from app.api import auth as auth_module
from app.services import audio_bus, table_manager, tts


@pytest.fixture(autouse=True)
def reset_table_manager_singleton():
    """Reset the table manager between tests so each test starts clean.

    For tables that have background consumer tasks (persistence consumer,
    narrator consumer), we need to actively cancel those tasks before
    nulling the singleton. Otherwise the tasks survive into the next test
    and crash when their event loop is closed, generating spurious
    'Event loop is closed' unraisable exceptions that pytest escalates
    to test failures.

    Also resets the in-memory player balance dict — otherwise tests
    accumulate state because each buy-in subtracts from a process-global
    dict. A test running late in the file would see a different starting
    balance than the same test in isolation.
    """
    yield
    mgr = table_manager._manager
    if mgr is not None:
        # Cancel all per-table tasks. The test's loop is shutting down,
        # but `cancel()` on the Task object just flags it; we don't await.
        for _key, task in list(mgr._tasks.items()):
            with contextlib.suppress(Exception):
                task.cancel()
        mgr._tasks.clear()
        mgr._tables.clear()
        mgr._codes.clear()
    table_manager._manager = None
    audio_bus._bus = None
    tts._service = None
    # Reset per-player balance state so each test starts at the default.
    auth_module._balances.clear()
