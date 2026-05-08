"""Ledger service.

The ledger is append-only and idempotent. Every balance change goes through
`record()`, which:

1. Inserts a LedgerEntry row (unique on `idempotency_key`).
2. Updates the cached `accounts.balance_minor`.

Both operations occur in one transaction. If the idempotency key already exists,
the operation is a no-op and we return the existing entry.

This is the ONLY module that touches `accounts.balance_minor`. Any code that
needs to move chips between accounts must call `transfer()` here.

Player-to-player transfers are NOT exposed. The only counterparty for a player
account is the system (buy-in debits go to a virtual table escrow; pot wins
credit from that escrow). This is a deliberate constraint.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Account, LedgerEntry


class LedgerError(Exception):
    pass


class InsufficientFundsError(LedgerError):
    pass


@dataclass(frozen=True)
class LedgerOp:
    account_id: uuid.UUID
    delta_minor: int
    reason: str
    idempotency_key: str
    table_id: uuid.UUID | None = None
    hand_id: uuid.UUID | None = None


async def record(session: AsyncSession, op: LedgerOp) -> LedgerEntry:
    """Apply a single ledger op. Idempotent on `idempotency_key`."""
    existing = await session.scalar(
        select(LedgerEntry).where(LedgerEntry.idempotency_key == op.idempotency_key)
    )
    if existing is not None:
        return existing

    account = await session.get(Account, op.account_id, with_for_update=True)
    if account is None:
        raise LedgerError(f"account {op.account_id} not found")

    new_balance = account.balance_minor + op.delta_minor
    if new_balance < 0:
        raise InsufficientFundsError(
            f"account {account.id} would go negative: {account.balance_minor} + {op.delta_minor}"
        )
    account.balance_minor = new_balance

    entry = LedgerEntry(
        account_id=op.account_id,
        delta_minor=op.delta_minor,
        reason=op.reason,
        idempotency_key=op.idempotency_key,
        table_id=op.table_id,
        hand_id=op.hand_id,
    )
    session.add(entry)
    await session.flush()
    return entry


async def buy_in(
    session: AsyncSession,
    account_id: uuid.UUID,
    amount: int,
    table_id: uuid.UUID,
    seat_id: uuid.UUID,
) -> LedgerEntry:
    """Debit account for buy-in. Caller is responsible for crediting the seat stack."""
    return await record(
        session,
        LedgerOp(
            account_id=account_id,
            delta_minor=-amount,
            reason="buy_in",
            idempotency_key=f"buyin:{seat_id}",
            table_id=table_id,
        ),
    )


async def cash_out(
    session: AsyncSession,
    account_id: uuid.UUID,
    amount: int,
    table_id: uuid.UUID,
    seat_id: uuid.UUID,
) -> LedgerEntry:
    """Credit account on cash-out. Caller has already zeroed the seat stack."""
    return await record(
        session,
        LedgerOp(
            account_id=account_id,
            delta_minor=amount,
            reason="cash_out",
            idempotency_key=f"cashout:{seat_id}",
            table_id=table_id,
        ),
    )
