"""Persistence service: the only module that writes to the DB on behalf of
the table loop and the API.

Three categories of operations:

1. **Account/wallet operations** (ledger writes).
   - `ensure_account_for_handle`: idempotent get-or-create for a player.
   - `buy_in`: debit account, attribute to a seat. Idempotent on seat_id.
   - `cash_out`: credit account from a seat. Idempotent on seat_id.
   - `award_pot`: credit account from a hand's pot. Idempotent on hand_id+pot_index+player.
   - `refund_committed`: credit account from an aborted hand. Idempotent on hand_id+player.

2. **Hand history** (event log writes).
   - `record_hand_started`: insert a `Hand` row at hand start.
   - `record_action`: append a `HandAction` row.
   - `record_hand_completed`: update `Hand` with final state and deck reveal.
   - `record_hand_aborted`: update `Hand` with aborted status.

3. **Table/seat persistence** for recovery.
   - `persist_table`, `persist_seat`, `remove_seat`: track what's at each table.

All functions take an `AsyncSession` and don't commit — the caller controls
transaction boundaries. Idempotency is enforced via unique `idempotency_key`
on `LedgerEntry` and via primary-key conflict handling on `HandAction`.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Account,
    Hand,
    HandAction,
    LedgerEntry,
    Table,
    TableSeat,
    User,
)

# ---------------------------------------------------------------------------
# Account operations
# ---------------------------------------------------------------------------

DEFAULT_BALANCE_MINOR = 10_000  # 10k chips on first signup


async def ensure_account_for_handle(
    session: AsyncSession, handle: str,
) -> Account:
    """Return the play-money account for a handle, creating User+Account if needed.

    Idempotent: safe to call on every request that needs to know a player's
    balance. Returns the loaded Account (with `id` and `balance_minor`).
    """
    result = await session.execute(
        select(User).where(User.x_user_id == handle).limit(1),
    )
    user = result.scalar_one_or_none()
    if user is None:
        user = User(x_user_id=handle, handle=handle)
        session.add(user)
        await session.flush()  # populate user.id

    result = await session.execute(
        select(Account).where(
            Account.user_id == user.id,
            Account.currency_type == "PLAY",
        ).limit(1),
    )
    account = result.scalar_one_or_none()
    if account is None:
        account = Account(
            user_id=user.id,
            currency_type="PLAY",
            balance_minor=DEFAULT_BALANCE_MINOR,
        )
        session.add(account)
        await session.flush()  # populate account.id BEFORE we reference it
        # Seed initial balance with a ledger entry so reconciliation works.
        session.add(LedgerEntry(
            account_id=account.id,
            delta_minor=DEFAULT_BALANCE_MINOR,
            reason="signup_grant",
            idempotency_key=f"signup:{user.id}",
        ))
        await session.flush()
    return account


class InsufficientFundsError(Exception):
    pass


async def _record_ledger(
    session: AsyncSession,
    account_id: uuid.UUID,
    delta: int,
    reason: str,
    idempotency_key: str,
    table_id: uuid.UUID | None = None,
    hand_id: uuid.UUID | None = None,
) -> bool:
    """Insert a ledger entry and update account balance. Returns True if written,
    False if a duplicate (idempotency hit)."""
    existing = await session.scalar(
        select(LedgerEntry).where(LedgerEntry.idempotency_key == idempotency_key),
    )
    if existing is not None:
        return False
    account = await session.get(Account, account_id, with_for_update=True)
    if account is None:
        raise ValueError(f"account {account_id} not found")
    new_balance = account.balance_minor + delta
    if new_balance < 0:
        raise InsufficientFundsError(
            f"account {account_id}: balance {account.balance_minor} + delta {delta} < 0",
        )
    account.balance_minor = new_balance
    session.add(LedgerEntry(
        account_id=account_id,
        delta_minor=delta,
        reason=reason,
        idempotency_key=idempotency_key,
        table_id=table_id,
        hand_id=hand_id,
    ))
    await session.flush()
    return True


async def buy_in(
    session: AsyncSession, account_id: uuid.UUID, amount: int,
    seat_id: uuid.UUID, table_id: uuid.UUID,
) -> None:
    """Debit account by `amount` for a buy-in to a specific seat."""
    await _record_ledger(
        session, account_id, -amount, "buy_in",
        idempotency_key=f"buyin:{seat_id}", table_id=table_id,
    )


async def cash_out(
    session: AsyncSession, account_id: uuid.UUID, amount: int,
    seat_id: uuid.UUID, table_id: uuid.UUID,
) -> None:
    """Credit account by `amount` from a seat that's leaving the table."""
    await _record_ledger(
        session, account_id, amount, "cash_out",
        idempotency_key=f"cashout:{seat_id}", table_id=table_id,
    )


async def award_pot(
    session: AsyncSession, account_id: uuid.UUID, amount: int,
    hand_id: uuid.UUID, pot_index: int, player_handle: str,
) -> None:
    """Credit pot winnings. Idempotent on hand+pot+player."""
    await _record_ledger(
        session, account_id, amount, "pot_win",
        idempotency_key=f"pot:{hand_id}:{pot_index}:{player_handle}",
        hand_id=hand_id,
    )


async def refund_committed(
    session: AsyncSession, account_id: uuid.UUID, amount: int,
    hand_id: uuid.UUID, player_handle: str,
) -> None:
    """Credit refund for an aborted hand. Idempotent on hand+player."""
    await _record_ledger(
        session, account_id, amount, "abort_refund",
        idempotency_key=f"refund:{hand_id}:{player_handle}",
        hand_id=hand_id,
    )


# ---------------------------------------------------------------------------
# Hand history
# ---------------------------------------------------------------------------

async def record_hand_started(
    session: AsyncSession,
    hand_id: uuid.UUID,
    table_id: uuid.UUID,
    hand_number: int,
    deck_commit: str,
) -> None:
    """Insert a Hand row at the start of a hand."""
    session.add(Hand(
        id=hand_id,
        table_id=table_id,
        hand_number=hand_number,
        deck_seed_commit=deck_commit,
        deck_seed_reveal=None,
        final_state=None,
    ))
    await session.flush()


async def record_actions(
    session: AsyncSession,
    hand_id: uuid.UUID,
    actions: list[dict],
) -> None:
    """Bulk-insert action rows. `actions` is a list of dicts with sequence,
    user_id (the User.id), action_type, amount."""
    for a in actions:
        session.add(HandAction(
            hand_id=hand_id,
            sequence=a["sequence"],
            user_id=a["user_id"],
            action_type=a["action_type"],
            amount=a["amount"],
        ))
    await session.flush()


async def record_hand_completed(
    session: AsyncSession,
    hand_id: uuid.UUID,
    deck_reveal: str,
    final_state: dict,
) -> None:
    """Update a Hand with the revealed seed and final state."""
    await session.execute(
        update(Hand).where(Hand.id == hand_id).values(
            deck_seed_reveal=deck_reveal,
            final_state=final_state,
        ),
    )


async def record_hand_aborted(
    session: AsyncSession, hand_id: uuid.UUID, refunds: dict,
) -> None:
    """Mark a Hand as aborted. We use final_state's `aborted` key as a marker
    rather than adding a new column."""
    await session.execute(
        update(Hand).where(Hand.id == hand_id).values(
            final_state={"aborted": True, "refunds": refunds},
        ),
    )


# ---------------------------------------------------------------------------
# Table / seat persistence
# ---------------------------------------------------------------------------

async def persist_table(
    session: AsyncSession,
    table_id: uuid.UUID, code: str,
    small_blind: int, big_blind: int, max_seats: int,
    host_user_id: uuid.UUID,
) -> None:
    session.add(Table(
        id=table_id, code=code,
        small_blind=small_blind, big_blind=big_blind,
        min_buyin=20 * big_blind, max_buyin=200 * big_blind,
        max_seats=max_seats, rake_bps=0, status="active",
        host_user_id=host_user_id,
    ))
    await session.flush()


async def mark_table_closed(
    session: AsyncSession, table_id: uuid.UUID,
) -> None:
    await session.execute(
        update(Table).where(Table.id == table_id).values(status="closed"),
    )


async def persist_seat(
    session: AsyncSession,
    seat_id: uuid.UUID, table_id: uuid.UUID, user_id: uuid.UUID,
    seat_number: int, stack: int,
) -> None:
    session.add(TableSeat(
        id=seat_id, table_id=table_id, user_id=user_id,
        seat_number=seat_number, stack=stack, status="active",
    ))
    await session.flush()


async def remove_seat(
    session: AsyncSession, seat_id: uuid.UUID,
) -> None:
    await session.execute(
        update(TableSeat).where(TableSeat.id == seat_id).values(status="left"),
    )


async def update_seat_stack(
    session: AsyncSession, seat_id: uuid.UUID, stack: int,
) -> None:
    """Update a seat's stack — used at hand boundaries to record current chip count."""
    await session.execute(
        update(TableSeat).where(TableSeat.id == seat_id).values(stack=stack),
    )


# ---------------------------------------------------------------------------
# Recovery: read state on startup
# ---------------------------------------------------------------------------

async def list_active_tables(session: AsyncSession) -> list[Table]:
    result = await session.execute(
        select(Table).where(Table.status == "active"),
    )
    return list(result.scalars().all())


async def list_seats_for_table(
    session: AsyncSession, table_id: uuid.UUID,
) -> list[TableSeat]:
    result = await session.execute(
        select(TableSeat).where(
            TableSeat.table_id == table_id,
            TableSeat.status == "active",
        ).order_by(TableSeat.seat_number),
    )
    return list(result.scalars().all())


async def find_interrupted_hand(
    session: AsyncSession, table_id: uuid.UUID,
) -> Hand | None:
    """Return the most recent hand at a table that has no deck_seed_reveal —
    indicating it was interrupted. None if no such hand exists."""
    result = await session.execute(
        select(Hand).where(
            Hand.table_id == table_id,
            Hand.deck_seed_reveal.is_(None),
        ).order_by(Hand.started_at.desc()).limit(1),
    )
    return result.scalar_one_or_none()


async def get_account_for_handle(
    session: AsyncSession, handle: str,
) -> Account | None:
    result = await session.execute(
        select(Account).join(User).where(
            User.x_user_id == handle,
            Account.currency_type == "PLAY",
        ).limit(1),
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Hand replay query
# ---------------------------------------------------------------------------

async def get_hand_for_replay(
    session: AsyncSession, hand_id: uuid.UUID,
) -> dict | None:
    """Return everything needed to replay a hand: the hand row + all actions.
    Returns None if hand not found or not yet complete."""
    hand = await session.get(Hand, hand_id)
    if hand is None or hand.deck_seed_reveal is None:
        return None
    result = await session.execute(
        select(HandAction).where(HandAction.hand_id == hand_id)
        .order_by(HandAction.sequence),
    )
    actions = result.scalars().all()
    return {
        "hand_id": str(hand.id),
        "table_id": str(hand.table_id),
        "hand_number": hand.hand_number,
        "deck_seed_commit": hand.deck_seed_commit,
        "deck_seed_reveal": hand.deck_seed_reveal,
        "final_state": hand.final_state,
        "started_at": hand.started_at.isoformat() if hand.started_at else None,
        "actions": [
            {
                "sequence": a.sequence,
                "user_id": str(a.user_id),
                "action_type": a.action_type,
                "amount": a.amount,
                "at": a.at.isoformat() if a.at else None,
            }
            for a in actions
        ],
    }
