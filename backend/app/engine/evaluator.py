"""5-7 card hand evaluator. Wraps the `treys` library.

`treys` returns lower numbers for better hands (1 = royal flush, 7462 = worst).
We expose this directly along with a rich description string (specific to
the actual ranks involved — "Pair of aces, king kicker" rather than just
"Pair") and the 5 cards that compose the best hand (used by the UI to
highlight winning cards at showdown).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from enum import IntEnum
from itertools import combinations

from treys import Card as TreysCard
from treys import Evaluator as TreysEvaluator

from app.engine.cards import Card, Rank

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
    description: str  # e.g. "Two pair, aces and kings, jack kicker"
    best_five: tuple[Card, ...] = field(default_factory=tuple)
    """The 5 cards (from hole + board) that form the best hand. Used by the
    UI to highlight winning cards at showdown. Always exactly 5 cards when
    populated. May be empty if not computed."""


def _to_treys(c: Card) -> int:
    return TreysCard.new(str(c))


# Singular and plural rank names — singular for kickers/single cards,
# plural for pairs/trips/quads/full-house components.
_RANK_NAMES_SINGULAR: dict[Rank, str] = {
    Rank.TWO: "two", Rank.THREE: "three", Rank.FOUR: "four",
    Rank.FIVE: "five", Rank.SIX: "six", Rank.SEVEN: "seven",
    Rank.EIGHT: "eight", Rank.NINE: "nine", Rank.TEN: "ten",
    Rank.JACK: "jack", Rank.QUEEN: "queen", Rank.KING: "king",
    Rank.ACE: "ace",
}

_RANK_NAMES_PLURAL: dict[Rank, str] = {
    Rank.TWO: "twos", Rank.THREE: "threes", Rank.FOUR: "fours",
    Rank.FIVE: "fives", Rank.SIX: "sixes", Rank.SEVEN: "sevens",
    Rank.EIGHT: "eights", Rank.NINE: "nines", Rank.TEN: "tens",
    Rank.JACK: "jacks", Rank.QUEEN: "queens", Rank.KING: "kings",
    Rank.ACE: "aces",
}


def _describe_hand(rank: HandRank, best_five: tuple[Card, ...]) -> str:
    """Build a specific description of the hand from its 5 cards.

    Examples:
        HIGH_CARD       → "Ace high"
        ONE_PAIR        → "Pair of aces, king kicker"
        TWO_PAIR        → "Two pair, kings and fours, ace kicker"
        THREE_OF_A_KIND → "Three of a kind, queens"
        STRAIGHT        → "Queen-high straight"
        FLUSH           → "Ace-high flush"
        FULL_HOUSE      → "Full house, aces full of kings"
        FOUR_OF_A_KIND  → "Four of a kind, kings"
        STRAIGHT_FLUSH  → "Ten-high straight flush"
        ROYAL_FLUSH     → "Royal flush"
    """
    if not best_five or len(best_five) != 5:
        # Fallback to a generic name when we don't have the 5-card detail.
        return _GENERIC_NAME.get(rank, "")

    # Count rank frequencies in the 5 cards.
    by_rank: Counter[Rank] = Counter(c.rank for c in best_five)
    # Sorted-by-rank list (high to low) of ranks present, broken by count.
    # E.g. for Two Pair K K 4 4 A: ranks_by_count = [
    #   (count=2, rank=KING), (count=2, rank=FOUR), (count=1, rank=ACE)
    # ]. We sort by (negative count, negative rank) so the more frequent
    # group comes first, then within equal counts the higher rank comes
    # first.
    ranks_by_count = sorted(
        by_rank.items(), key=lambda kv: (-kv[1], -int(kv[0])),
    )
    sorted_high_to_low = sorted(by_rank.keys(), key=lambda r: -int(r))

    sing = _RANK_NAMES_SINGULAR
    plur = _RANK_NAMES_PLURAL

    if rank == HandRank.ROYAL_FLUSH:
        return "Royal flush"

    if rank == HandRank.STRAIGHT_FLUSH:
        high = _straight_high(best_five)
        return f"{sing[high].capitalize()}-high straight flush"

    if rank == HandRank.FOUR_OF_A_KIND:
        quad_rank = ranks_by_count[0][0]
        return f"Four of a kind, {plur[quad_rank]}"

    if rank == HandRank.FULL_HOUSE:
        trips_rank = ranks_by_count[0][0]
        pair_rank = ranks_by_count[1][0]
        return f"Full house, {plur[trips_rank]} full of {plur[pair_rank]}"

    if rank == HandRank.FLUSH:
        high = sorted_high_to_low[0]
        return f"{sing[high].capitalize()}-high flush"

    if rank == HandRank.STRAIGHT:
        high = _straight_high(best_five)
        return f"{sing[high].capitalize()}-high straight"

    if rank == HandRank.THREE_OF_A_KIND:
        trips_rank = ranks_by_count[0][0]
        return f"Three of a kind, {plur[trips_rank]}"

    if rank == HandRank.TWO_PAIR:
        # Two pairs (descending), one kicker.
        high_pair = ranks_by_count[0][0]
        low_pair = ranks_by_count[1][0]
        kicker = ranks_by_count[2][0]
        return (
            f"Two pair, {plur[high_pair]} and {plur[low_pair]}, "
            f"{sing[kicker]} kicker"
        )

    if rank == HandRank.ONE_PAIR:
        pair_rank = ranks_by_count[0][0]
        # The remaining 3 cards are kickers; we name only the highest.
        kickers = [r for r, _ in ranks_by_count[1:]]
        if kickers:
            top_kicker = max(kickers, key=lambda r: int(r))
            return f"Pair of {plur[pair_rank]}, {sing[top_kicker]} kicker"
        return f"Pair of {plur[pair_rank]}"

    # HIGH_CARD
    high = sorted_high_to_low[0]
    return f"{sing[high].capitalize()} high"


# Generic fallback names when best_five isn't available.
_GENERIC_NAME: dict[HandRank, str] = {
    HandRank.ROYAL_FLUSH: "Royal flush",
    HandRank.STRAIGHT_FLUSH: "Straight flush",
    HandRank.FOUR_OF_A_KIND: "Four of a kind",
    HandRank.FULL_HOUSE: "Full house",
    HandRank.FLUSH: "Flush",
    HandRank.STRAIGHT: "Straight",
    HandRank.THREE_OF_A_KIND: "Three of a kind",
    HandRank.TWO_PAIR: "Two pair",
    HandRank.ONE_PAIR: "Pair",
    HandRank.HIGH_CARD: "High card",
}


def _straight_high(best_five: tuple[Card, ...]) -> Rank:
    """Return the high card of a straight. Handles the wheel (A-2-3-4-5)
    where the ace plays as low and 5 is the high card."""
    ranks = sorted({int(c.rank) for c in best_five}, reverse=True)
    # Wheel: A K Q J T no, that's broadway. Wheel is 5-4-3-2-A. So
    # ranks set is {14, 5, 4, 3, 2}. High card is 5.
    if ranks == [14, 5, 4, 3, 2]:
        return Rank.FIVE
    # Otherwise the maximum rank is the high.
    return Rank(ranks[0])


def evaluate_hand(hole: list[Card], board: list[Card]) -> HandStrength:
    """Evaluate the best 5-card hand from hole + board.

    Requires len(hole)+len(board) >= 5. Also computes which 5 cards form
    the best hand by exhaustive search (C(7,5)=21 combinations max), and
    builds a rich human-readable description (e.g. "Two pair, kings and
    fours, ace kicker" rather than the generic "Two pair").
    """
    if len(hole) + len(board) < 5:
        raise ValueError("need at least 5 cards to evaluate")
    treys_hole = [_to_treys(c) for c in hole]
    treys_board = [_to_treys(c) for c in board]
    score = _evaluator.evaluate(treys_board, treys_hole)
    rank_class = _evaluator.get_rank_class(score)
    hand_rank = HandRank(rank_class)

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

    description = _describe_hand(hand_rank, best_five)
    return HandStrength(
        score=score, rank=hand_rank, description=description,
        best_five=best_five,
    )
