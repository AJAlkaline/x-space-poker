"""Translate typed events to wire-format JSON dicts for WebSocket clients.

The event types are internal; clients see a stable JSON shape. This is the
boundary between the in-process event abstraction and the public protocol.

The wire format is intentionally close to what the table_manager used to
broadcast directly, so frontend clients didn't need to change.
"""
from __future__ import annotations

from app.services.events import (
    ActionAppliedEvent,
    HandAbortedEvent,
    HandCompletedEvent,
    HandStartedEvent,
    IllegalActionEvent,
    PrivateEvent,
    PrivateStateEvent,
    PublicEvent,
    SeatsChangedEvent,
    TableErrorEvent,
    ViewerCountChangedEvent,
)


def public_event_to_wire(event: PublicEvent) -> dict:
    """Convert a public event to the dict the WebSocket sends to clients."""
    if isinstance(event, HandStartedEvent):
        return {"type": "hand_started", "state": event.public_state}
    if isinstance(event, ActionAppliedEvent):
        return {
            "type": "state_update",
            "state": event.public_state,
            "action": {
                "sequence": event.sequence,
                "player_id": event.player_id,
                "action_type": event.action_type,
                "amount": event.amount,
                "auto": event.auto,
            },
        }
    if isinstance(event, HandCompletedEvent):
        return {"type": "hand_complete", "state": event.public_state}
    if isinstance(event, HandAbortedEvent):
        return {
            "type": "hand_aborted",
            "hand_id": event.hand_id,
            "refunds": event.refunds or {},
        }
    if isinstance(event, SeatsChangedEvent):
        return {"type": "seats", "seats": event.seats}
    if isinstance(event, ViewerCountChangedEvent):
        return {"type": "viewer_count", "count": event.count}
    if isinstance(event, TableErrorEvent):
        return {"type": "table_error", "error": event.error}
    raise ValueError(f"unknown public event type: {type(event).__name__}")


def private_event_to_wire(event: PrivateEvent) -> dict:
    if isinstance(event, PrivateStateEvent):
        return {"type": "private", "state": event.state}
    if isinstance(event, IllegalActionEvent):
        return {"type": "illegal_action", "error": event.error}
    raise ValueError(f"unknown private event type: {type(event).__name__}")


def event_to_wire(event) -> dict:
    """Dispatch on event kind. Used by the player WebSocket pump where a
    single queue carries both public and private events."""
    if isinstance(event, PrivateEvent):
        return private_event_to_wire(event)
    return public_event_to_wire(event)
