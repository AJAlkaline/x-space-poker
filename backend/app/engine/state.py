"""Core game state types.

`GameState` is the immutable snapshot of one hand at one moment. Engine functions
take a state + action and return a new state — they never mutate.

Chip amounts are integers throughout. The smallest betting unit is 1 chip.
Stakes like "$0.05/$0.10" should be represented as small_blind=5, big_blind=10
with a display-time multiplier; the engine doesn't care about the unit.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.engine.cards import Card


class HandPhase(str, Enum):
    PRE_DEAL = "pre_deal"     # Hand not yet started
    PRE_FLOP = "pre_flop"
    FLOP = "flop"
    TURN = "turn"
    RIVER = "river"
    SHOWDOWN = "showdown"
    COMPLETE = "complete"     # Pots awarded, ready for next hand


class PlayerStatus(str, Enum):
    ACTIVE = "active"           # In hand, can act
    FOLDED = "folded"
    ALL_IN = "all_in"           # In hand, no chips left to bet
    SITTING_OUT = "sitting_out"  # At table but not in hand
    DISCONNECTED = "disconnected"  # Treated as auto-fold/check, seat preserved


class ActionType(str, Enum):
    FOLD = "fold"
    CHECK = "check"
    CALL = "call"
    BET = "bet"
    RAISE = "raise"
    POST_BLIND = "post_blind"   # Internal action emitted at hand start


@dataclass(frozen=True, slots=True)
class Action:
    """An action taken by a player. `amount` semantics:

    - FOLD/CHECK: amount must be 0
    - CALL: amount = chips moved (engine fills this in; clients can send 0)
    - BET: amount = total bet size for this street (must equal the new street total)
    - RAISE: amount = the *to* size, i.e. the new total street commitment
            (e.g. raise to 200, not raise by 200)
    """
    player_id: str
    action_type: ActionType
    amount: int = 0


@dataclass(frozen=True, slots=True)
class Player:
    """Per-hand snapshot of a player. Stack is the chip count *behind* (not in pot)."""
    id: str               # Stable user/seat identifier
    seat: int             # 0..max_seats-1
    stack: int            # Chips behind
    hole: tuple[Card, ...]  # Empty tuple before deal; (c1, c2) after
    status: PlayerStatus
    # Chips this player has put into the pot on the *current* street.
    # Used to compute call amounts and detect raises. Reset to 0 each street.
    street_committed: int = 0
    # Total chips committed across all streets in this hand. Used for side-pot math.
    total_committed: int = 0
    # Last action this player took on the current street, for UI display.
    last_action: ActionType | None = None


@dataclass(frozen=True, slots=True)
class Pot:
    """A pot or side-pot. `eligible_players` lists IDs that can win this pot."""
    amount: int
    eligible_players: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BettingRound:
    """State scoped to the current betting round (street).

    `current_bet` is the amount any active player must match to stay in (their
    `street_committed` must reach `current_bet`). `min_raise` is the minimum
    legal raise increment — under standard NLHE rules, a raise must be at
    least the size of the previous bet/raise.
    """
    current_bet: int      # Total street commitment to match
    min_raise: int        # Minimum *increment* for the next raise
    last_raiser_id: str | None  # Who last opened/raised this street
    # Players who haven't yet had a chance to act since the last raise.
    # When this is empty and current_bet is matched, the round closes.
    to_act: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GameState:
    """Immutable snapshot of one hand at one moment.

    The engine produces a new GameState for every action. Persistence layer
    can choose to store every snapshot, or just the action log + final state
    and reconstruct intermediate states by replay.
    """
    hand_id: str
    table_id: str
    small_blind: int
    big_blind: int

    # Seating: index = seat number, None for empty seats.
    # Only includes players in this hand (sitting-out players are excluded
    # at hand-start time when the GameState is constructed).
    players: tuple[Player | None, ...]

    button: int                # Seat number of the dealer button
    phase: HandPhase
    board: tuple[Card, ...]    # 0, 3, 4, or 5 cards depending on phase

    pots: tuple[Pot, ...]      # Main pot + side pots, computed at street end
    betting: BettingRound

    # Deck commitment (SHA-256 of seed). Seed itself is not in state — it lives
    # on the Deck object held by the table loop and is revealed at hand end.
    deck_commit: str

    @property
    def active_players(self) -> list[Player]:
        return [p for p in self.players if p is not None and p.status == PlayerStatus.ACTIVE]

    @property
    def in_hand_players(self) -> list[Player]:
        """Players still contesting the pot (active + all-in)."""
        return [
            p for p in self.players
            if p is not None and p.status in (PlayerStatus.ACTIVE, PlayerStatus.ALL_IN)
        ]

    def player_by_id(self, player_id: str) -> Player | None:
        for p in self.players:
            if p is not None and p.id == player_id:
                return p
        return None
