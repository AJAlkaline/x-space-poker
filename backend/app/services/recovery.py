"""Startup recovery.

On process start, when persistence is enabled, this rehydrates the table
manager from the DB:

1. Load every Table with status='active'.
2. For each, check for an interrupted hand (no deck_seed_reveal). If found:
   - Mark the hand as aborted with an empty refunds map (we don't have the
     pre-restart in-flight commitments to refund — they're lost with the
     in-memory state).
   - The seat stacks in the DB reflect the last persisted state (end of
     last completed hand), so players come back with that stack.
3. Recreate the table in memory with its config.
4. Reseat all active TableSeats from the DB.

This is session-level recovery: balances and seating preserved, in-flight
hands lost. That's the documented behavior.
"""
from __future__ import annotations

import logging

from app.core.config import get_settings
from app.db.session import get_session
from app.services import persistence
from app.services.table_manager import get_manager

log = logging.getLogger(__name__)


async def recover_tables() -> None:
    """Reload active tables from DB into the table manager."""
    if not get_settings().persistence_enabled:
        return

    mgr = get_manager()
    async with get_session() as s:
        active_tables = await persistence.list_active_tables(s)

    for table_row in active_tables:
        # Mark any interrupted hand as aborted before rehydrating.
        async with get_session() as s:
            interrupted = await persistence.find_interrupted_hand(s, table_row.id)
            if interrupted is not None:
                log.warning(
                    "marking interrupted hand %s as aborted on recovery",
                    interrupted.id,
                )
                # Empty refunds: we don't know the in-flight commitments
                # because the in-memory state is gone. Players' persisted seat
                # stacks reflect end of last completed hand, which is correct.
                await persistence.record_hand_aborted(
                    s, interrupted.id, refunds={},
                )

        # Recreate the table in memory.
        rt = await mgr.create_table(
            table_id=str(table_row.id),
            small_blind=table_row.small_blind,
            big_blind=table_row.big_blind,
            max_seats=table_row.max_seats,
            code=table_row.code,
        )
        # Re-seat players from the DB.
        async with get_session() as s:
            seats = await persistence.list_seats_for_table(s, table_row.id)
            user_id_to_handle: dict = {}
            from sqlalchemy import select

            from app.db.models import User
            for seat in seats:
                if seat.user_id not in user_id_to_handle:
                    user = await s.scalar(
                        select(User).where(User.id == seat.user_id),
                    )
                    if user is None:
                        continue
                    user_id_to_handle[seat.user_id] = user.x_user_id
                handle = user_id_to_handle[seat.user_id]
                try:
                    mgr.seat_player(
                        rt.table_id, handle, seat.seat_number, seat.stack,
                    )
                except ValueError:
                    log.warning(
                        "could not reseat %s at seat %d in table %s",
                        handle, seat.seat_number, table_row.id,
                    )

    log.info("recovery complete: %d active tables rehydrated", len(active_tables))
