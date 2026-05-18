"""Table event types. The event stream is the canonical record of everything
that happens at a table. Persistence, public broadcasts, private sends, and
(eventually) cross-process pub/sub all derive from this stream.

Two parallel streams in practice:

- **Public events**: visible to all subscribers (players, spectators, persistence).
  These never contain hidden information like hole cards.
- **Private events**: addressed to a specific player. Carry hole cards, legal
  actions, and action deadlines. Spectators never receive these by design —
  the spectator subscription code path cannot route a private event because
  it's subscribed to the wrong channel.

Events are immutable dataclasses. They're produced by the table loop and
consumed by zero or more downstream subscribers. Consumers must not block
the loop; if a consumer is slow, it drops messages (via a bounded queue).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# ---------------------------------------------------------------------------
# Public events — anyone subscribed to a table sees these.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HandStartedEvent:
    type: Literal["hand_started"] = "hand_started"
    table_id: str = ""
    hand_id: str = ""
    hand_number: int = 0
    deck_commit: str = ""
    public_state: dict = None  # type: ignore[assignment]


@dataclass(frozen=True)
class ActionAppliedEvent:
    """An action was successfully applied. The new public state follows."""
    type: Literal["action_applied"] = "action_applied"
    table_id: str = ""
    hand_id: str = ""
    sequence: int = 0
    player_id: str = ""
    action_type: str = ""  # ActionType value
    amount: int = 0
    auto: bool = False  # True if this was an auto-fold/check from timeout
    public_state: dict = None  # type: ignore[assignment]


@dataclass(frozen=True)
class HandCompletedEvent:
    type: Literal["hand_completed"] = "hand_completed"
    table_id: str = ""
    hand_id: str = ""
    deck_reveal: str = ""
    public_state: dict = None  # type: ignore[assignment]  # with hole reveals
    pot_distributions: list[dict] = None  # type: ignore[assignment]
    # Absolute deadline (ms since epoch) for when the next hand will
    # auto-start, assuming enough seated players remain. Clients render
    # a countdown by computing remaining time from `Date.now()`. If the
    # countdown expires and no `hand_started` arrives, the loop is
    # blocked waiting for more eligible players to sit — clients should
    # transition to a "waiting for players" indicator.
    next_hand_starts_at_unix_ms: int = 0


@dataclass(frozen=True)
class HandAbortedEvent:
    """Hand was interrupted (server restart, table close mid-hand). Committed
    chips are refunded to player accounts via the ledger."""
    type: Literal["hand_aborted"] = "hand_aborted"
    table_id: str = ""
    hand_id: str = ""
    refunds: dict = None  # type: ignore[assignment]  # {player_id: chips}


@dataclass(frozen=True)
class SeatsChangedEvent:
    type: Literal["seats_changed"] = "seats_changed"
    table_id: str = ""
    seats: list = None  # type: ignore[assignment]


@dataclass(frozen=True)
class ViewerCountChangedEvent:
    type: Literal["viewer_count_changed"] = "viewer_count_changed"
    table_id: str = ""
    count: int = 0


@dataclass(frozen=True)
class TableErrorEvent:
    type: Literal["table_error"] = "table_error"
    table_id: str = ""
    error: str = ""


# ---------------------------------------------------------------------------
# Private events — addressed to a single player.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PrivateStateEvent:
    """Per-player view: hole cards + (when to-act) legal actions + deadlines."""
    type: Literal["private_state"] = "private_state"
    table_id: str = ""
    player_id: str = ""
    state: dict = None  # type: ignore[assignment]


@dataclass(frozen=True)
class IllegalActionEvent:
    type: Literal["illegal_action"] = "illegal_action"
    table_id: str = ""
    player_id: str = ""
    error: str = ""


# ---------------------------------------------------------------------------
# Type aliases for consumers
# ---------------------------------------------------------------------------

PublicEvent = (
    HandStartedEvent
    | ActionAppliedEvent
    | HandCompletedEvent
    | HandAbortedEvent
    | SeatsChangedEvent
    | ViewerCountChangedEvent
    | TableErrorEvent
)

PrivateEvent = PrivateStateEvent | IllegalActionEvent

TableEvent = PublicEvent | PrivateEvent
