"""Pure-Python poker engine. No I/O, no async, no database.

The engine is a deterministic function from (game state, action) to (new game state).
All randomness is supplied externally via a seeded Deck. This makes every hand
fully reproducible from its action log + deck seed, which we use for hand history
and dispute resolution.
"""

from app.engine.cards import Card, Deck, Rank, Suit
from app.engine.evaluator import HandRank, evaluate_hand
from app.engine.state import (
    Action,
    ActionType,
    BettingRound,
    GameState,
    HandPhase,
    Player,
    PlayerStatus,
    Pot,
)
from app.engine.table import Table, apply_action, deal_hand, legal_actions

__all__ = [
    "Action",
    "ActionType",
    "BettingRound",
    "Card",
    "Deck",
    "GameState",
    "HandPhase",
    "HandRank",
    "Player",
    "PlayerStatus",
    "Pot",
    "Rank",
    "Suit",
    "Table",
    "apply_action",
    "deal_hand",
    "evaluate_hand",
    "legal_actions",
]
