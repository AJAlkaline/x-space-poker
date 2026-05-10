"""Unit tests for replay snapshot reconstruction.

These build replay_data dicts directly (matching the shape that
get_hand_for_replay returns) and feed them into reconstruct_snapshots
without going through the API or DB. Lets us cover edge cases
deterministically.
"""
from __future__ import annotations

from app.engine.cards import Deck
from app.engine.table import (
    Action,
    ActionType,
    SeatConfig,
    Table,
    apply_action,
    deal_hand,
)
from app.services.replay import reconstruct_snapshots
from app.services.table_manager import _public_view


def _live_play_to_replay_data(
    seed: str,
    seats: list[SeatConfig],
    button_seat: int,
    sb: int,
    bb: int,
    actions: list[tuple[str, ActionType, int]],
) -> dict:
    """Drive the engine through `actions` and capture what the
    persistence layer would have stored. Mirrors what record_hand_started
    + record_actions + record_hand_completed do in the live loop."""
    table = Table(id="t-1", small_blind=sb, big_blind=bb, max_seats=9)
    deck = Deck.from_seed(seed)
    state = deal_hand(table, seats, button_seat=button_seat, deck=deck)
    start_state = _public_view(state)

    action_records = []
    for sequence, (handle, atype, amount) in enumerate(actions, start=1):
        action_records.append({
            "sequence": sequence,
            "user_id": handle,  # in real DB this is UUID, here we don't care
            "handle": handle,
            "action_type": atype.value,
            "amount": amount,
        })
        state = apply_action(state, Action(handle, atype, amount), deck)

    return {
        "hand_id": "h-1",
        "table_id": "t-1",
        "hand_number": 1,
        "deck_seed_commit": deck.commit(),
        "deck_seed_reveal": seed,
        "start_state": start_state,
        "actions": action_records,
        "started_at": None,
        "final_state": _public_view(state, reveal=True),
    }


def test_reconstruct_snapshots_heads_up_fold() -> None:
    """Simplest case: heads-up, alice (button/SB) folds preflop."""
    seats = [
        SeatConfig(user_id="alice", seat=0, stack=1000),
        SeatConfig(user_id="bob", seat=1, stack=1000),
    ]
    replay_data = _live_play_to_replay_data(
        seed="seed-fold-test", seats=seats, button_seat=0, sb=5, bb=10,
        actions=[("alice", ActionType.FOLD, 0)],
    )

    snapshots = reconstruct_snapshots(replay_data)
    assert snapshots is not None
    assert len(snapshots) == 2

    # Snapshot 0: initial state right after deal_hand.
    assert snapshots[0]["action"] is None
    assert snapshots[0]["public_state"]["phase"] == "pre_flop"
    assert snapshots[0]["public_state"]["pot_total"] == 15  # SB + BB

    # Snapshot 1: after alice's fold; bob wins.
    assert snapshots[1]["action"]["action_type"] == "fold"
    assert snapshots[1]["action"]["player_id"] == "alice"
    assert snapshots[1]["public_state"]["phase"] == "complete"


def test_reconstruct_snapshots_heads_up_call_check_fold() -> None:
    """Heads-up: alice (SB/button) calls preflop, bob (BB) checks the option,
    flop comes, bob (BB) acts first post-flop and bets, alice folds.
    Five snapshots expected: initial, post-call, post-check, post-bet, post-fold."""
    seats = [
        SeatConfig(user_id="alice", seat=0, stack=1000),
        SeatConfig(user_id="bob", seat=1, stack=1000),
    ]
    replay_data = _live_play_to_replay_data(
        seed="seed-call-check-bet-fold", seats=seats, button_seat=0, sb=5, bb=10,
        actions=[
            ("alice", ActionType.CALL, 0),
            ("bob", ActionType.CHECK, 0),
            ("bob", ActionType.BET, 20),
            ("alice", ActionType.FOLD, 0),
        ],
    )

    snapshots = reconstruct_snapshots(replay_data)
    assert snapshots is not None
    assert len(snapshots) == 5

    # Initial deal.
    assert snapshots[0]["public_state"]["phase"] == "pre_flop"
    assert len(snapshots[0]["public_state"]["board"]) == 0

    # After alice's call.
    assert snapshots[1]["action"]["action_type"] == "call"
    assert snapshots[1]["public_state"]["phase"] == "pre_flop"

    # After bob's check — phase should have advanced to flop.
    assert snapshots[2]["action"]["action_type"] == "check"
    assert snapshots[2]["public_state"]["phase"] == "flop"
    assert len(snapshots[2]["public_state"]["board"]) == 3

    # After bob's bet.
    assert snapshots[3]["action"]["action_type"] == "bet"
    assert snapshots[3]["action"]["amount"] == 20

    # After alice's fold — hand complete.
    assert snapshots[4]["action"]["action_type"] == "fold"
    assert snapshots[4]["public_state"]["phase"] == "complete"


def test_reconstruct_returns_none_without_start_state() -> None:
    """Old hands recorded before the start_state column existed should
    return None — caller falls back to narration-only view."""
    replay_data = {
        "hand_id": "old-hand",
        "table_id": "t",
        "hand_number": 1,
        "deck_seed_commit": "abc",
        "deck_seed_reveal": "seed",
        "start_state": None,
        "actions": [],
        "started_at": None,
        "final_state": None,
    }
    assert reconstruct_snapshots(replay_data) is None


def test_reconstruct_returns_none_without_deck_reveal() -> None:
    """Hands that haven't completed don't have a deck reveal — replay isn't
    meaningful."""
    replay_data = {
        "hand_id": "h",
        "table_id": "t",
        "hand_number": 1,
        "deck_seed_commit": "abc",
        "deck_seed_reveal": None,
        "start_state": {"foo": "bar"},
        "actions": [],
        "started_at": None,
        "final_state": None,
    }
    assert reconstruct_snapshots(replay_data) is None


def test_reconstruct_three_handed_with_raise() -> None:
    """Three players, alice raises, bob calls, carol folds."""
    seats = [
        SeatConfig(user_id="alice", seat=0, stack=1000),
        SeatConfig(user_id="bob", seat=1, stack=1000),
        SeatConfig(user_id="carol", seat=2, stack=1000),
    ]
    # Button at seat 0 (alice). 3-handed: SB=bob (seat 1), BB=carol (seat 2).
    # Pre-flop action starts at alice (button = UTG in 3-handed).
    replay_data = _live_play_to_replay_data(
        seed="seed-3handed", seats=seats, button_seat=0, sb=5, bb=10,
        actions=[
            ("alice", ActionType.RAISE, 30),
            ("bob", ActionType.CALL, 0),
            ("carol", ActionType.FOLD, 0),
        ],
    )

    snapshots = reconstruct_snapshots(replay_data)
    assert snapshots is not None
    assert len(snapshots) == 4

    # After alice's raise.
    assert snapshots[1]["action"]["action_type"] == "raise"
    assert snapshots[1]["action"]["amount"] == 30
    assert snapshots[1]["public_state"]["current_bet"] == 30

    # After bob's call — carol still to act.
    assert snapshots[2]["action"]["action_type"] == "call"
    assert "carol" in snapshots[2]["public_state"]["to_act"]

    # After carol's fold — heads-up to flop.
    assert snapshots[3]["action"]["action_type"] == "fold"
    assert snapshots[3]["public_state"]["phase"] == "flop"
