"""Unit-ish tests for the persistence service against in-memory SQLite.

These don't go through the API or the table loop — they test persistence
operations directly. Validates schema works cross-dialect and that
idempotency keys actually prevent duplicate writes.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.models import Base
from app.services import persistence


@pytest.fixture
async def session_factory():
    """In-memory SQLite for fast schema-level tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _new_session(factory) -> AsyncSession:
    return factory()


@pytest.mark.asyncio
async def test_ensure_account_creates_user_and_account(session_factory):
    factory = session_factory
    async with factory() as s:
        account = await persistence.ensure_account_for_handle(s, "alice")
        assert account.balance_minor == persistence.DEFAULT_BALANCE_MINOR
        await s.commit()

    # Idempotent: second call returns same account, doesn't grant chips again.
    async with factory() as s:
        account2 = await persistence.ensure_account_for_handle(s, "alice")
        assert account2.id == account.id
        assert account2.balance_minor == persistence.DEFAULT_BALANCE_MINOR


@pytest.mark.asyncio
async def test_buy_in_debits_balance(session_factory):
    factory = session_factory
    async with factory() as s:
        account = await persistence.ensure_account_for_handle(s, "alice")
        await s.commit()

    seat_id = uuid.uuid4()
    table_id = uuid.uuid4()
    async with factory() as s:
        await persistence.buy_in(s, account.id, 1000, seat_id, table_id)
        await s.commit()

    async with factory() as s:
        a = await persistence.get_account_for_handle(s, "alice")
        assert a is not None
        assert a.balance_minor == persistence.DEFAULT_BALANCE_MINOR - 1000


@pytest.mark.asyncio
async def test_buy_in_idempotent_on_seat_id(session_factory):
    """Two buy_in calls with the same seat_id should net only one debit."""
    factory = session_factory
    async with factory() as s:
        account = await persistence.ensure_account_for_handle(s, "alice")
        await s.commit()

    seat_id = uuid.uuid4()
    table_id = uuid.uuid4()
    async with factory() as s:
        await persistence.buy_in(s, account.id, 1000, seat_id, table_id)
        await s.commit()
    async with factory() as s:
        # Same seat_id → should be no-op (idempotent).
        await persistence.buy_in(s, account.id, 1000, seat_id, table_id)
        await s.commit()

    async with factory() as s:
        a = await persistence.get_account_for_handle(s, "alice")
        assert a is not None
        assert a.balance_minor == persistence.DEFAULT_BALANCE_MINOR - 1000  # only debited once


@pytest.mark.asyncio
async def test_buy_in_rejects_overdraft(session_factory):
    factory = session_factory
    async with factory() as s:
        account = await persistence.ensure_account_for_handle(s, "alice")
        await s.commit()

    seat_id = uuid.uuid4()
    table_id = uuid.uuid4()
    async with factory() as s:
        with pytest.raises(persistence.InsufficientFundsError):
            await persistence.buy_in(s, account.id, 1_000_000, seat_id, table_id)


@pytest.mark.asyncio
async def test_refund_idempotent_on_hand_and_player(session_factory):
    """Two refund calls for the same hand+player only credit once."""
    factory = session_factory
    async with factory() as s:
        account = await persistence.ensure_account_for_handle(s, "alice")
        await s.commit()

    hand_id = uuid.uuid4()
    async with factory() as s:
        await persistence.refund_committed(s, account.id, 100, hand_id, "alice")
        await s.commit()
    async with factory() as s:
        await persistence.refund_committed(s, account.id, 100, hand_id, "alice")
        await s.commit()

    async with factory() as s:
        a = await persistence.get_account_for_handle(s, "alice")
        assert a is not None
        # Credited only once.
        assert a.balance_minor == persistence.DEFAULT_BALANCE_MINOR + 100


@pytest.mark.asyncio
async def test_hand_history_round_trip(session_factory):
    """Record a hand + actions + completion; read back via get_hand_for_replay."""
    factory = session_factory
    async with factory() as s:
        # Need a table and users to satisfy FKs.
        alice = await persistence.ensure_account_for_handle(s, "alice")
        bob = await persistence.ensure_account_for_handle(s, "bob")
        table_id = uuid.uuid4()
        await persistence.persist_table(
            s, table_id, "ABCD23",
            small_blind=5, big_blind=10, max_seats=9,
            host_user_id=alice.user_id,
        )
        await s.commit()

    hand_id = uuid.uuid4()
    async with factory() as s:
        await persistence.record_hand_started(
            s, hand_id, table_id, hand_number=1,
            deck_commit="abcd1234",
        )
        await persistence.record_actions(s, hand_id, [
            {"sequence": 1, "user_id": alice.user_id, "action_type": "raise", "amount": 30},
            {"sequence": 2, "user_id": bob.user_id, "action_type": "call", "amount": 0},
        ])
        await persistence.record_hand_completed(
            s, hand_id, deck_reveal="real-seed",
            final_state={"phase": "complete"},
        )
        await s.commit()

    async with factory() as s:
        replay = await persistence.get_hand_for_replay(s, hand_id)
        assert replay is not None
        assert replay["deck_seed_commit"] == "abcd1234"
        assert replay["deck_seed_reveal"] == "real-seed"
        assert len(replay["actions"]) == 2
        assert replay["actions"][0]["action_type"] == "raise"
        assert replay["actions"][1]["action_type"] == "call"


@pytest.mark.asyncio
async def test_interrupted_hand_lookup(session_factory):
    """A hand without deck_seed_reveal is found by find_interrupted_hand."""
    factory = session_factory
    async with factory() as s:
        alice = await persistence.ensure_account_for_handle(s, "alice")
        table_id = uuid.uuid4()
        await persistence.persist_table(
            s, table_id, "ABCD23",
            small_blind=5, big_blind=10, max_seats=9,
            host_user_id=alice.user_id,
        )
        await s.commit()

    hand_id = uuid.uuid4()
    async with factory() as s:
        await persistence.record_hand_started(
            s, hand_id, table_id, hand_number=1, deck_commit="abc",
        )
        await s.commit()

    async with factory() as s:
        h = await persistence.find_interrupted_hand(s, table_id)
        assert h is not None
        assert h.id == hand_id

    # After completion, no longer interrupted.
    async with factory() as s:
        await persistence.record_hand_completed(
            s, hand_id, deck_reveal="seed", final_state={},
        )
        await s.commit()

    async with factory() as s:
        h = await persistence.find_interrupted_hand(s, table_id)
        assert h is None
