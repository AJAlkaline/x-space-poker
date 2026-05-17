"""Tests for the rich hand-description output of evaluate_hand.

Each test pins down the exact phrasing for a hand class. If treys ever
changes its rank classification — or if we refactor the description
helper — these tests catch regressions in user-facing copy.
"""
from __future__ import annotations

from app.engine.cards import Card
from app.engine.evaluator import HandRank, evaluate_hand


def _eval(hole_strs, board_strs):
    hole = [Card.parse(s) for s in hole_strs]
    board = [Card.parse(s) for s in board_strs]
    return evaluate_hand(hole, board)


def test_high_card_names_the_highest_card():
    r = _eval(["Ah", "Kc"], ["9s", "7d", "4h", "2s", "3c"])
    assert r.rank == HandRank.HIGH_CARD
    assert r.description == "Ace high"


def test_pair_names_pair_rank_and_top_kicker():
    r = _eval(["Ah", "As"], ["Kc", "7d", "2s"])
    assert r.rank == HandRank.ONE_PAIR
    assert r.description == "Pair of aces, king kicker"


def test_pair_lower_rank():
    r = _eval(["Kh", "Ks"], ["Ac", "7d", "2s"])
    assert r.rank == HandRank.ONE_PAIR
    assert r.description == "Pair of kings, ace kicker"


def test_two_pair_high_pair_first_then_low_then_kicker():
    # Kings up with a four-kicker, plus an ace on board → ace is the kicker.
    r = _eval(["Kh", "Ks"], ["4c", "4d", "Ac"])
    assert r.rank == HandRank.TWO_PAIR
    assert r.description == "Two pair, kings and fours, ace kicker"


def test_two_pair_aces_and_kings():
    r = _eval(["Ah", "As"], ["Kc", "Kd", "2s"])
    assert r.rank == HandRank.TWO_PAIR
    assert r.description == "Two pair, aces and kings, two kicker"


def test_three_of_a_kind_names_the_trip_rank():
    r = _eval(["Ah", "As"], ["Ac", "7d", "2s"])
    assert r.rank == HandRank.THREE_OF_A_KIND
    assert r.description == "Three of a kind, aces"


def test_straight_names_high_card():
    r = _eval(["Th", "Jc"], ["Qs", "Kd", "Ah"])
    assert r.rank == HandRank.STRAIGHT
    assert r.description == "Ace-high straight"


def test_wheel_straight_high_is_five_not_ace():
    """A-2-3-4-5 is the lowest straight; the high card is 5, not the ace."""
    r = _eval(["Ah", "2c"], ["3s", "4d", "5h"])
    assert r.rank == HandRank.STRAIGHT
    assert r.description == "Five-high straight"


def test_flush_names_high_card():
    r = _eval(["Ah", "Kh"], ["Qh", "Jh", "2h", "5d", "7c"])
    assert r.rank == HandRank.FLUSH
    assert r.description == "Ace-high flush"


def test_lower_flush():
    r = _eval(["Th", "9h"], ["Qh", "Jh", "2h", "5d", "7c"])
    assert r.rank == HandRank.FLUSH
    assert r.description == "Queen-high flush"


def test_full_house_trips_then_pair():
    r = _eval(["Ah", "As"], ["Ac", "Kd", "Kh"])
    assert r.rank == HandRank.FULL_HOUSE
    assert r.description == "Full house, aces full of kings"


def test_full_house_other_direction():
    r = _eval(["Kh", "Ks"], ["Kc", "Ad", "Ah"])
    assert r.rank == HandRank.FULL_HOUSE
    assert r.description == "Full house, kings full of aces"


def test_four_of_a_kind_names_the_rank():
    r = _eval(["Ah", "As"], ["Ac", "Ad", "7c"])
    assert r.rank == HandRank.FOUR_OF_A_KIND
    assert r.description == "Four of a kind, aces"


def test_straight_flush_names_high_card():
    r = _eval(["9h", "8h"], ["Th", "Jh", "7h"])
    assert r.rank == HandRank.STRAIGHT_FLUSH
    assert r.description == "Jack-high straight flush"


def test_royal_flush_says_royal_flush():
    r = _eval(["Ah", "Kh"], ["Qh", "Jh", "Th"])
    assert r.rank == HandRank.ROYAL_FLUSH
    assert r.description == "Royal flush"


def test_best_five_is_exactly_five_cards():
    """Smoke test: every hand type produces exactly 5 best_five cards."""
    cases = [
        (["Ah", "Kc"], ["9s", "7d", "4h", "2s", "3c"]),  # high card
        (["Ah", "As"], ["Kc", "7d", "2s"]),               # pair
        (["Kh", "Ks"], ["4c", "4d", "Ac"]),               # two pair
        (["Ah", "As"], ["Ac", "7d", "2s"]),               # trips
        (["Th", "Jc"], ["Qs", "Kd", "Ah"]),               # straight
        (["Ah", "Kh"], ["Qh", "Jh", "2h", "5d", "7c"]),   # flush
        (["Ah", "As"], ["Ac", "Kd", "Kh"]),               # full house
        (["Ah", "As"], ["Ac", "Ad", "7c"]),               # quads
        (["9h", "8h"], ["Th", "Jh", "7h"]),               # straight flush
        (["Ah", "Kh"], ["Qh", "Jh", "Th"]),               # royal
    ]
    for hole, board in cases:
        r = _eval(hole, board)
        assert len(r.best_five) == 5, f"{hole}+{board} → {r.best_five}"
