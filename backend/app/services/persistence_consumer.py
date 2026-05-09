"""Persistence consumer.

Spawned alongside each table loop when `persistence_enabled` is True.
Subscribes to the table's public event stream and writes hand history,
pot wins, and refunds to the database.

This is a "projector" in CQRS terms — it derives DB rows from the event
stream. The event stream itself is the source of truth; the DB is a
queryable projection.

Key behaviors:
- Buffers per-hand actions in memory; flushes them in one DB transaction
  on `HandCompleted` or `HandAborted`. Crash before flush = lose actions
  for the current hand, but the hand was aborted anyway, so it doesn't
  matter.
- Idempotent: pot awards are keyed on (hand_id, pot_index, player_handle),
  refunds on (hand_id, player_handle). Replay-safe.
- Resilient: a DB error doesn't crash the table loop. The consumer logs
  and continues; the in-memory game state remains correct.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid

from app.db.session import get_session
from app.services import persistence
from app.services.event_bus import EventBus
from app.services.events import (
    ActionAppliedEvent,
    HandAbortedEvent,
    HandCompletedEvent,
    HandStartedEvent,
)

log = logging.getLogger(__name__)


async def run_persistence_consumer(
    bus: EventBus, subscriber_id: str, closed: asyncio.Event,
) -> None:
    """Consume public events and write hand history.

    Runs until `closed` is set or the task is cancelled.
    """
    queue = bus.subscribe_public(subscriber_id)

    # Buffer per-hand state.
    current_hand_id: uuid.UUID | None = None
    pending_actions: list[dict] = []
    # Cache of handle → user_id to avoid repeated lookups.
    user_id_cache: dict[str, uuid.UUID] = {}

    try:
        while not closed.is_set():
            try:
                event = await asyncio.wait_for(queue.get(), timeout=1.0)
            except TimeoutError:
                continue

            try:
                if isinstance(event, HandStartedEvent):
                    current_hand_id = uuid.UUID(event.hand_id)
                    pending_actions = []
                    async with get_session() as s:
                        await persistence.record_hand_started(
                            s, hand_id=current_hand_id,
                            table_id=uuid.UUID(event.table_id),
                            hand_number=event.hand_number,
                            deck_commit=event.deck_commit,
                        )

                elif isinstance(event, ActionAppliedEvent):
                    if current_hand_id is None:
                        continue
                    user_id = await _resolve_user_id(
                        event.player_id, user_id_cache,
                    )
                    if user_id is not None:
                        pending_actions.append({
                            "sequence": event.sequence,
                            "user_id": user_id,
                            "action_type": event.action_type,
                            "amount": event.amount,
                        })

                elif isinstance(event, HandCompletedEvent):
                    if current_hand_id is None:
                        continue
                    async with get_session() as s:
                        if pending_actions:
                            await persistence.record_actions(
                                s, current_hand_id, pending_actions,
                            )
                        await persistence.record_hand_completed(
                            s, current_hand_id,
                            deck_reveal=event.deck_reveal,
                            final_state=event.public_state,
                        )
                        # Update each persisted seat's stack to match the
                        # post-hand chip distribution. This is the snapshot
                        # we'd need for recovery — buy-in and cash-out are
                        # the only ledger-affecting operations, while hand
                        # outcomes just shift chips between in-memory seats.
                        await _update_persisted_stacks(
                            s, uuid.UUID(event.table_id), event.public_state,
                        )
                    pending_actions = []
                    current_hand_id = None

                elif isinstance(event, HandAbortedEvent):
                    if current_hand_id is None:
                        continue
                    async with get_session() as s:
                        # Best effort: persist actions we have, mark aborted, refund.
                        if pending_actions:
                            await persistence.record_actions(
                                s, current_hand_id, pending_actions,
                            )
                        await persistence.record_hand_aborted(
                            s, current_hand_id, refunds=event.refunds or {},
                        )
                        for handle, amount in (event.refunds or {}).items():
                            account = await persistence.get_account_for_handle(
                                s, handle,
                            )
                            if account is not None:
                                await persistence.refund_committed(
                                    s, account.id, amount,
                                    hand_id=current_hand_id,
                                    player_handle=handle,
                                )
                    pending_actions = []
                    current_hand_id = None

            except Exception:
                # Persistence failure must not crash the loop. Log and continue.
                log.exception("persistence consumer error on event %s", type(event).__name__)
    except asyncio.CancelledError:
        # Final flush of any pending actions for an in-flight hand.
        if current_hand_id is not None and pending_actions:
            with contextlib.suppress(Exception):
                async with get_session() as s:
                    await persistence.record_actions(
                        s, current_hand_id, pending_actions,
                    )
        raise
    finally:
        bus.unsubscribe_public(subscriber_id)


async def _resolve_user_id(
    handle: str, cache: dict[str, uuid.UUID],
) -> uuid.UUID | None:
    """Resolve a handle to a User.id. Cached after first lookup."""
    if handle in cache:
        return cache[handle]
    try:
        async with get_session() as s:
            account = await persistence.get_account_for_handle(s, handle)
            if account is None:
                return None
            cache[handle] = account.user_id
            return account.user_id
    except Exception:
        log.exception("failed to resolve user_id for handle %s", handle)
        return None


async def _update_persisted_stacks(
    session, table_id: uuid.UUID, final_state: dict,
) -> None:
    """Update each persisted seat's stack to match post-hand chip count.

    The DB seat row is just for recovery: if the server restarts, we want
    to seat each player back with the right stack. Mid-hand stack changes
    aren't worth persisting (they're derivable from the action log), but
    end-of-hand snapshots are cheap and keep recovery simple.

    We look up each seat by (table_id, seat_number) since that's the
    natural key the public_state references.
    """
    from sqlalchemy import select, update

    from app.db.models import TableSeat, User

    for player in final_state.get("players", []):
        if player is None:
            continue
        # Resolve seat: TableSeat with this table_id + seat_number where status='active'.
        result = await session.execute(
            select(TableSeat, User)
            .join(User, TableSeat.user_id == User.id)
            .where(
                TableSeat.table_id == table_id,
                TableSeat.seat_number == player["seat"],
                TableSeat.status == "active",
                User.x_user_id == player["id"],
            ),
        )
        row = result.first()
        if row is None:
            continue
        seat = row[0]
        await session.execute(
            update(TableSeat).where(TableSeat.id == seat.id).values(
                stack=player["stack"],
            ),
        )
