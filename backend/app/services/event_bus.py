"""In-process event bus for a single table.

Two subscriber kinds:

- **Players** subscribe via `subscribe_player(player_id)` and get a single
  ordered queue carrying *both* public events (fanned to every subscriber)
  and private events addressed to them. Order is preserved: a private
  state event published after a public state-update event arrives after
  it on the wire, every time. This eliminates the cross-channel race
  that older two-queue designs suffer from.

- **Spectators / projectors** subscribe via `subscribe_public(subscriber_id)`
  and get a queue that receives public events only. There is no API path
  by which a private event could route to a public-only queue — the
  security boundary is structural, not filtered.

Subscribers are bounded queues. Slow consumers drop events rather than
backpressuring the table loop. The loop's correctness must not depend on
any consumer keeping up — the canonical state lives in the loop itself,
and consumers are derived views.
"""
from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field

from app.services.events import PrivateEvent, PublicEvent

# Bounded queue size per subscriber. If a consumer is slower than this, they
# drop events. 256 is generous for a table with low actions/sec; spectators
# at high fan-out should be tuned higher.
_QUEUE_MAXSIZE = 256

# A player's queue can carry either kind of event.
PlayerEvent = PublicEvent | PrivateEvent


@dataclass
class EventBus:
    """Per-table event bus. One instance per active table."""

    # Player queues: keyed by player_id. Each receives public events (via
    # fan-out) AND private events (addressed to that player_id). Single queue
    # per player so order between public and private is preserved.
    _player_queues: dict[str, asyncio.Queue[PlayerEvent]] = field(
        default_factory=dict
    )

    # Public-only subscribers: spectators, persistence consumers, future
    # cross-process publishers. They receive public events only — there is
    # no API path that routes a private event here.
    _public_only_subscribers: dict[str, asyncio.Queue[PublicEvent]] = field(
        default_factory=dict
    )

    # ------------------------------------------------------------------
    # Publishing (called from the table loop)
    # ------------------------------------------------------------------

    def publish_public(self, event: PublicEvent) -> None:
        """Broadcast a public event to every subscriber (players and
        public-only). Drops on full queues."""
        for q in list(self._player_queues.values()):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(event)
        for q in list(self._public_only_subscribers.values()):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(event)

    def publish_private(self, player_id: str, event: PrivateEvent) -> None:
        """Send a private event to one player's queue. No-op if they
        aren't subscribed. Spectator/projector queues are NEVER touched
        by this method — the structural boundary that prevents private
        information from leaking to spectators."""
        q = self._player_queues.get(player_id)
        if q is None:
            return
        with contextlib.suppress(asyncio.QueueFull):
            q.put_nowait(event)

    # ------------------------------------------------------------------
    # Subscription
    # ------------------------------------------------------------------

    def subscribe_player(self, player_id: str) -> asyncio.Queue[PlayerEvent]:
        """Register a player and return their single combined queue.

        Replaces any existing subscription for the same player_id (handles
        the reconnect case — the new queue replaces the old one in both
        the fan-out list and the private-routing map).
        """
        q: asyncio.Queue[PlayerEvent] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._player_queues[player_id] = q
        return q

    def unsubscribe_player(self, player_id: str) -> None:
        self._player_queues.pop(player_id, None)

    def subscribe_public(self, subscriber_id: str) -> asyncio.Queue[PublicEvent]:
        """Register a spectator or projector. Receives public events only."""
        q: asyncio.Queue[PublicEvent] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._public_only_subscribers[subscriber_id] = q
        return q

    def unsubscribe_public(self, subscriber_id: str) -> None:
        self._public_only_subscribers.pop(subscriber_id, None)

    # ------------------------------------------------------------------
    # Stats (for viewer count etc.)
    # ------------------------------------------------------------------

    def total_subscriber_count(self) -> int:
        """Total of players + spectators + projectors. Used for viewer count
        broadcasts (which intentionally count everyone watching)."""
        return len(self._player_queues) + len(self._public_only_subscribers)
