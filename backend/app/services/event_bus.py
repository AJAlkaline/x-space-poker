"""In-process event bus for a single table.

Two parallel channels per table:

- **Public**: every subscriber gets every event. Used by players (for table
  state), spectators, and persistence.
- **Private**: events are addressed to a specific player_id and only that
  player's queue receives them. Used for hole cards, legal actions, and
  per-player notifications.

Subscribers are bounded queues. Slow consumers drop events rather than
backpressuring the table loop. The loop's correctness must not depend on
any consumer keeping up — the canonical state lives in the loop itself,
and consumers are derived views.

Cross-process fan-out (Redis pub/sub) will be a wrapper around this bus,
not a replacement: the wrapper subscribes to the public channel and
republishes to Redis; remote spectator gateways subscribe to Redis and
fan out to their connected WebSockets.
"""
from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from app.services.events import PrivateEvent, PublicEvent

# Bounded queue size per subscriber. If a consumer is slower than this, they
# drop events. 256 is generous for a table with low actions/sec; spectators
# at high fan-out should be tuned higher.
_QUEUE_MAXSIZE = 256


@dataclass
class EventBus:
    """Per-table event bus. One instance per active table."""

    # Public subscribers: keyed by an opaque subscriber_id (player_id, spectator_id,
    # or a synthetic ID for projectors). Each gets a copy of every public event.
    _public_subscribers: dict[str, asyncio.Queue[PublicEvent]] = field(
        default_factory=dict
    )
    # Private subscribers: keyed by player_id; a player gets only events
    # addressed to them.
    _private_subscribers: dict[str, asyncio.Queue[PrivateEvent]] = field(
        default_factory=dict
    )

    # ------------------------------------------------------------------
    # Publishing (called from the table loop)
    # ------------------------------------------------------------------

    def publish_public(self, event: PublicEvent) -> None:
        """Broadcast a public event to every subscriber. Drops on full queues."""
        for q in list(self._public_subscribers.values()):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(event)

    def publish_private(self, player_id: str, event: PrivateEvent) -> None:
        """Send a private event to one player. No-op if they aren't subscribed."""
        q = self._private_subscribers.get(player_id)
        if q is None:
            return
        with contextlib.suppress(asyncio.QueueFull):
            q.put_nowait(event)

    # ------------------------------------------------------------------
    # Subscription (called from API layer / projectors)
    # ------------------------------------------------------------------

    def subscribe_public(self, subscriber_id: str) -> asyncio.Queue[PublicEvent]:
        """Register a subscriber and return their queue. Replaces any existing
        subscription for the same id (reconnect case)."""
        q: asyncio.Queue[PublicEvent] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._public_subscribers[subscriber_id] = q
        return q

    def unsubscribe_public(self, subscriber_id: str) -> None:
        self._public_subscribers.pop(subscriber_id, None)

    def subscribe_private(self, player_id: str) -> asyncio.Queue[PrivateEvent]:
        q: asyncio.Queue[PrivateEvent] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._private_subscribers[player_id] = q
        return q

    def unsubscribe_private(self, player_id: str) -> None:
        self._private_subscribers.pop(player_id, None)

    # ------------------------------------------------------------------
    # Stats (for viewer count etc.)
    # ------------------------------------------------------------------

    def public_subscriber_count(self) -> int:
        return len(self._public_subscribers)

    def public_subscriber_ids(self) -> list[str]:
        return list(self._public_subscribers.keys())

    # ------------------------------------------------------------------
    # Iterator helpers — for tests and for internal projectors
    # ------------------------------------------------------------------

    async def public_events(
        self, subscriber_id: str
    ) -> AsyncIterator[PublicEvent]:
        """Convenience: subscribe and yield events forever. Caller must
        unsubscribe on exit (use try/finally)."""
        q = self.subscribe_public(subscriber_id)
        try:
            while True:
                yield await q.get()
        finally:
            self.unsubscribe_public(subscriber_id)
