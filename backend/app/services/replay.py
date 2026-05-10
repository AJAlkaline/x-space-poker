"""Hand replay reconstruction.

Given a stored hand (start_state, deck seed, ordered actions), this module
drives the engine forward action-by-action, capturing the public state
after each. The output is a list of snapshots the replay viewer renders.

The hand-start state stored in `Hand.start_state` is the public_view at
hand start. From it we can rebuild SeatConfigs and the button position.
The deck is reconstructed deterministically from the stored seed.

If start_state is missing (for hands recorded before the start_state
column existed), this module returns None — callers fall back to the
narration-only replay view.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from app.engine.cards import Deck
from app.engine.table import (
    Action,
    ActionType,
    HandPhase,
    SeatConfig,
    Table,
    apply_action,
    deal_hand,
)
from app.services.table_manager import _public_view

log = logging.getLogger(__name__)


@dataclass
class ReplaySnapshot:
    """One step of the replay. The action that was applied to produce
    this state, plus the public state after that action.

    For step 0 (the initial deal), action is None and public_state is the
    state right after blinds and hole-card deal — i.e. ready for the first
    voluntary action.
    """
    action: dict | None
    public_state: dict


def reconstruct_snapshots(replay_data: dict) -> list[dict] | None:
    """Build per-action public state snapshots from replay data.

    `replay_data` is the dict returned by `persistence.get_hand_for_replay`,
    which already includes start_state, deck_seed_reveal, and the action
    list with handles resolved.

    Returns a list of {"action": dict|None, "public_state": dict} entries,
    or None if start_state is missing.
    """
    start_state = replay_data.get("start_state")
    if start_state is None:
        return None
    deck_seed = replay_data.get("deck_seed_reveal")
    if deck_seed is None:
        return None
    actions = replay_data.get("actions", [])

    # Reconstruct the engine inputs from start_state.
    seats = _seats_from_start_state(start_state)
    table = Table(
        id=str(replay_data.get("table_id", "")),
        small_blind=int(start_state.get("small_blind", 0)),
        big_blind=int(start_state.get("big_blind", 0)),
        # max_seats isn't in the public_view; infer from the player array
        # length (it's the table's max_seats). If players is shorter than
        # the actual configured max we just lose nothing — the engine only
        # cares that max_seats >= max(seat numbers in use).
        max_seats=len(start_state.get("players", [])) or 9,
    )
    button_seat = int(start_state.get("button", 0))
    deck = Deck.from_seed(deck_seed)

    try:
        state = deal_hand(table, seats, button_seat=button_seat, deck=deck)
    except Exception:
        log.exception("replay: deal_hand failed")
        return None

    # Step 0: initial state right after the deal (post-blinds, hole cards
    # dealt). This is what the user sees in the live UI as "hand_started".
    snapshots: list[dict] = [{
        "action": None,
        "public_state": _public_view(state, reveal=False),
    }]

    # Apply each action in sequence.
    for record in actions:
        handle = record.get("handle")
        action_type_str = record.get("action_type")
        amount = int(record.get("amount", 0) or 0)
        if not handle or not action_type_str:
            log.warning("replay: skipping malformed action %r", record)
            continue

        # Skip post_blind actions — they were already applied by deal_hand
        # implicitly. The engine doesn't accept them as inbound actions.
        if action_type_str == ActionType.POST_BLIND.value:
            continue

        try:
            action = Action(
                player_id=handle,
                action_type=ActionType(action_type_str),
                amount=amount,
            )
            state = apply_action(state, action, deck)
        except Exception:
            log.exception(
                "replay: action %r failed at sequence %s",
                record, record.get("sequence"),
            )
            # Stop replaying but return what we have so far so the user
            # still sees something.
            break

        # If the hand has ended, reveal hole cards for the showdown.
        reveal = state.phase == HandPhase.COMPLETE
        snapshots.append({
            "action": {
                "sequence": record.get("sequence"),
                "player_id": handle,
                "action_type": action_type_str,
                "amount": amount,
                "auto": False,  # auto-flag wasn't stored historically
            },
            "public_state": _public_view(state, reveal=reveal),
        })

    return snapshots


def _seats_from_start_state(start_state: dict) -> list[SeatConfig]:
    """Rebuild SeatConfigs from the players array of a stored start_state.

    The player records in start_state hold the *initial* stacks (since
    start_state was captured after blinds were posted, but before any
    voluntary action — wait, actually it's captured at hand-started time,
    AFTER blinds. So the stacks already reflect blind posts.)

    The engine's deal_hand() will re-post the blinds, which would
    double-debit. So we add the blind amounts back to the SB/BB players
    to get pre-blind stacks before passing to the engine.
    """
    players = start_state.get("players", [])
    seats: list[SeatConfig] = []
    for p in players:
        if p is None:
            continue
        # The stored stack already reflects the blind post (street_committed
        # was deducted from stack at deal_hand time). Refund it to get the
        # pre-deal stack the engine expects.
        stack = int(p["stack"]) + int(p.get("street_committed", 0))
        seats.append(SeatConfig(
            user_id=str(p["id"]),
            seat=int(p["seat"]),
            stack=stack,
            sitting_out=False,
        ))
    return seats
