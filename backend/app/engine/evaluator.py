"""5-7 card hand evaluator. Wraps the `treys` library.

`treys` returns lower numbers for better hands (1 = royal flush, 7462 = worst).
We expose this directly along with a class-name string for narration and the
5 cards that compose the best hand (used by the UI to highlight winning
cards at showdown).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from itertools import combinations

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
    best_five: tuple[Card, ...] = field(default_factory=tuple)
    """The 5 cards (from hole + board) that form the best hand. Used by the
    UI to highlight winning cards at showdown. Always exactly 5 cards when
    populated. May be empty if not computed."""


def _to_treys(c: Card) -> int:
    return TreysCard.new(str(c))


def evaluate_hand(hole: list[Card], board: list[Card]) -> HandStrength:
    """Evaluate the best 5-card hand from hole + board.

    Requires len(hole)+len(board) >= 5. Also computes which 5 cards form
    the best hand by exhaustive search (C(7,5)=21 combinations max).
    """
    if len(hole) + len(board) < 5:
        raise ValueError("need at least 5 cards to evaluate")
    treys_hole = [_to_treys(c) for c in hole]
    treys_board = [_to_treys(c) for c in board]
    score = _evaluator.evaluate(treys_board, treys_hole)
    rank_class = _evaluator.get_rank_class(score)
    description = _evaluator.class_to_string(rank_class)

    # Find the specific 5 cards that produce the best score. We test every
    # 5-card subset and pick the one whose 5-card eval matches the score
    # of the full evaluation. For the small N here this is trivially fast.
    all_cards = list(hole) + list(board)
    best_five: tuple[Card, ...] = ()
    if len(all_cards) >= 5:
        best_score = score
        for combo in combinations(range(len(all_cards)), 5):
            cards_subset = [all_cards[i] for i in combo]
            treys_subset = [_to_treys(c) for c in cards_subset]
            # treys 5-card eval: pass the whole hand as `board`, empty hole.
            # `evaluate` works with any 5+ cards distributed between args.
            subset_score = _evaluator.evaluate(treys_subset, [])
            if subset_score == best_score:
                best_five = tuple(cards_subset)
                break
    return HandStrength(
        score=score, rank=HandRank(rank_class), description=description,
        best_five=best_five,
    )
