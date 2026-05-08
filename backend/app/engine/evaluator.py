"""5-7 card hand evaluator. Wraps the `treys` library.

`treys` returns lower numbers for better hands (1 = royal flush, 7462 = worst).
We expose this directly along with a class-name string for narration.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from treys import Card as TreysCard
from treys import Evaluator as TreysEvaluator

from app.engine.cards import Card

_evaluator = TreysEvaluator()


class HandRank(IntEnum):
    """`treys` rank classes. Lower = stronger."""
    ROYAL_FLUSH = 0
    STRAIGHT_FLUSH = 1
    FOUR_OF_A_KIND = 2
    FULL_HOUSE = 3
    FLUSH = 4
    STRAIGHT = 5
    THREE_OF_A_KIND = 6
    TWO_PAIR = 7
    ONE_PAIR = 8
    HIGH_CARD = 9


@dataclass(frozen=True, slots=True)
class HandStrength:
    """Result of evaluating a hand. `score` is the raw treys score (lower = better)."""
    score: int
    rank: HandRank
    description: str  # e.g. "Two Pair, Aces and Kings"


def _to_treys(c: Card) -> int:
    return TreysCard.new(str(c))


def evaluate_hand(hole: list[Card], board: list[Card]) -> HandStrength:
    """Evaluate the best 5-card hand from hole + board. Requires len(hole)+len(board) >= 5."""
    if len(hole) + len(board) < 5:
        raise ValueError("need at least 5 cards to evaluate")
    treys_hole = [_to_treys(c) for c in hole]
    treys_board = [_to_treys(c) for c in board]
    score = _evaluator.evaluate(treys_board, treys_hole)
    rank_class = _evaluator.get_rank_class(score)
    description = _evaluator.class_to_string(rank_class)
    return HandStrength(score=score, rank=HandRank(rank_class), description=description)
