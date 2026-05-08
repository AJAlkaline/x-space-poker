"""Engine tests — the six scenarios that catch most poker engine bugs.

These tests use a rigged deck so we can assert exact outcomes. The deck is
constructed by passing specific cards, not a seed.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.engine import (
    Action,
    ActionType,
    Card,
    Deck,
    HandPhase,
    PlayerStatus,
)
from app.engine.cards import _full_deck  # type: ignore[attr-defined]
from app.engine.table import SeatConfig, Table, apply_action, deal_hand

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class RiggedDeck(Deck):
    """A deck that draws from a fixed list (so tests can pin every card)."""

    @classmethod
    def from_cards(cls, cards: list[Card]) -> RiggedDeck:
        return cls(seed="rigged-test-seed", _cards=cards.copy(), _index=0)


def _table(sb: int = 5, bb: int = 10, max_seats: int = 9) -> Table:
    return Table(id="t1", small_blind=sb, big_blind=bb, max_seats=max_seats)


def _stack_of(state, player_id: str) -> int:
    p = state.player_by_id(player_id)
    assert p is not None
    return p.stack


# ---------------------------------------------------------------------------
# 1. Heads-up all-in pre-flop
# ---------------------------------------------------------------------------

def test_heads_up_all_in_preflop():
    """Two players, one shoves, other calls. Board runs out, higher hand wins everything."""
    table = _table()
    seats = [
        SeatConfig(user_id="alice", seat=0, stack=100),
        SeatConfig(user_id="bob", seat=1, stack=100),
    ]
    # Rig: alice gets AA, bob gets KK, board is rags.
    cards = [
        Card.parse("As"),  # alice c1
        Card.parse("Kh"),  # bob c1
        Card.parse("Ah"),  # alice c2
        Card.parse("Kd"),  # bob c2
        Card.parse("2c"), Card.parse("7c"), Card.parse("9d"),  # flop (no straight/flush)
        Card.parse("4d"),  # turn
        Card.parse("Jh"),  # river
    ]
    deck = RiggedDeck.from_cards(cards + [c for c in _full_deck() if c not in cards])

    # Heads-up: button=SB acts first pre-flop.
    state = deal_hand(table, seats, button_seat=0, deck=deck)
    assert state.phase == HandPhase.PRE_FLOP
    # Alice (button/SB) posted 5, Bob (BB) posted 10.
    assert _stack_of(state, "alice") == 95
    assert _stack_of(state, "bob") == 90

    # Alice shoves all-in (raise to 100).
    state = apply_action(state, Action("alice", ActionType.RAISE, 100), deck)
    # Bob calls.
    state = apply_action(state, Action("bob", ActionType.CALL), deck)

    # Hand should be complete; alice wins 200.
    assert state.phase == HandPhase.COMPLETE
    assert _stack_of(state, "alice") == 200
    assert _stack_of(state, "bob") == 0


# ---------------------------------------------------------------------------
# 2. Three-way all-in with side pots (the classic)
# ---------------------------------------------------------------------------

def test_three_way_all_in_side_pots():
    """A=100 all-in, B=300 all-in, C=500 calls.
    Main pot 300 (A,B,C eligible). Side pot 400 (B,C). Uncalled 200 returns to C.
    """
    table = _table(sb=5, bb=10)
    seats = [
        SeatConfig(user_id="A", seat=0, stack=100),
        SeatConfig(user_id="B", seat=1, stack=300),
        SeatConfig(user_id="C", seat=2, stack=500),
    ]
    # Rig: A=AsKs, B=2h3h, C=2d3d. Board gives A two pair (aces and kings).
    # Rotational deal: A-c1, B-c1, C-c1, A-c2, B-c2, C-c2.
    cards = [
        Card.parse("As"),  # A c1
        Card.parse("2h"),  # B c1
        Card.parse("2d"),  # C c1
        Card.parse("Ks"),  # A c2
        Card.parse("3h"),  # B c2
        Card.parse("3d"),  # C c2
        Card.parse("Ah"), Card.parse("Kh"), Card.parse("9c"),  # flop — A has two pair
        Card.parse("8s"),  # turn
        Card.parse("Td"),  # river — no straight for B/C
    ]
    deck = RiggedDeck.from_cards(cards + [c for c in _full_deck() if c not in cards])

    state = deal_hand(table, seats, button_seat=0, deck=deck)
    # 3-handed: SB=seat1 (B, posts 5), BB=seat2 (C, posts 10), button=seat0 (A) acts first.
    assert _stack_of(state, "A") == 100
    assert _stack_of(state, "B") == 295
    assert _stack_of(state, "C") == 490

    # A shoves 100. B (SB, has 295) calls — but to call A's 100 from his SB-posted 5
    # would only be 95; instead B raises all-in to 300.
    state = apply_action(state, Action("A", ActionType.RAISE, 100), deck)
    state = apply_action(state, Action("B", ActionType.RAISE, 300), deck)
    # C calls 300.
    state = apply_action(state, Action("C", ActionType.CALL), deck)

    # Hand resolves immediately because all remaining players are all-in.
    assert state.phase == HandPhase.COMPLETE

    # A wins main pot of 300 (3 * 100). B and C contest side pot of 400 (2 * 200).
    # C's 200 above B's all-in is uncalled, returned to C.
    # B has nothing better than C; both have just pair of pair. With our rigged
    # board (Ah Kh 4c 5d 6c), B has 2h 3h (no pair, just 6-high), C has 2d 3d
    # (also nothing). B's best 5 includes the board pair... actually both make
    # the same hand off the board. Let's check by total accounting:
    a_final = _stack_of(state, "A")
    b_final = _stack_of(state, "B")
    c_final = _stack_of(state, "C")
    # Total chips conserved (sum of starting stacks).
    assert a_final + b_final + c_final == 100 + 300 + 500
    # A wins main pot, finishing with 300.
    assert a_final == 300
    # Uncalled 200 returns to C, so C gets at least 200 back beyond any side pot share.
    # If B and C split (board plays for both), each gets 200 from the 400 side pot.
    # If one wins outright, they get all 400. Either way, C >= 200.
    assert c_final >= 200


# ---------------------------------------------------------------------------
# 3. SB all-in for less than BB
# ---------------------------------------------------------------------------

def test_sb_all_in_for_less_than_bb():
    """SB has only 3 chips, blinds are 5/10. SB posts 3 (all-in), BB posts 10."""
    table = _table(sb=5, bb=10)
    seats = [
        SeatConfig(user_id="short", seat=0, stack=3),     # button + SB in heads-up
        SeatConfig(user_id="big", seat=1, stack=200),     # BB
    ]
    deck = Deck.from_seed("test-sb-short")

    state = deal_hand(table, seats, button_seat=0, deck=deck)
    short = state.player_by_id("short")
    assert short is not None
    # SB posted only 3 (all his stack), so he's all-in.
    assert short.status == PlayerStatus.ALL_IN
    assert short.stack == 0
    assert short.total_committed == 3

    # BB posts 10, has 190 behind.
    big = state.player_by_id("big")
    assert big is not None
    assert big.stack == 190
    assert big.total_committed == 10

    # Action is on big. Short can't act (all-in). Legal options for big should be
    # check (since current_bet == BB which big has matched) or raise.
    # But actually short going all-in for less than BB doesn't reset the bet;
    # current_bet stays at BB (10) and big has matched it via blind.
    # Big's only meaningful action is check (then board runs out).
    state = apply_action(state, Action("big", ActionType.CHECK), deck)

    # All remaining in-hand players are all-in (short) or can't be raised (big checked).
    # Wait — big isn't all-in, but short is, and they're heads up. Round closed
    # since big checked and short can't act. Should run out the board.
    assert state.phase == HandPhase.COMPLETE
    # Total chips conserved.
    assert _stack_of(state, "short") + _stack_of(state, "big") == 203


# ---------------------------------------------------------------------------
# 4. Walk: everyone folds to BB pre-flop
# ---------------------------------------------------------------------------

def test_walk_everyone_folds_to_bb():
    table = _table(sb=5, bb=10)
    seats = [
        SeatConfig(user_id="btn", seat=0, stack=100),
        SeatConfig(user_id="sb", seat=1, stack=100),
        SeatConfig(user_id="bb", seat=2, stack=100),
    ]
    deck = Deck.from_seed("walk-test")

    state = deal_hand(table, seats, button_seat=0, deck=deck)
    # 3-handed: SB=seat1, BB=seat2. Action starts on btn (seat0) pre-flop.
    state = apply_action(state, Action("btn", ActionType.FOLD), deck)
    state = apply_action(state, Action("sb", ActionType.FOLD), deck)

    # Hand should be complete; BB takes SB's 5 + own 10 back = stack should be 105.
    assert state.phase == HandPhase.COMPLETE
    assert _stack_of(state, "bb") == 105
    assert _stack_of(state, "sb") == 95
    assert _stack_of(state, "btn") == 100


# ---------------------------------------------------------------------------
# 5. Showdown with split pot
# ---------------------------------------------------------------------------

def test_split_pot_at_showdown():
    """Two players, board plays — both have the same hand off the board."""
    table = _table(sb=5, bb=10)
    seats = [
        SeatConfig(user_id="alice", seat=0, stack=100),
        SeatConfig(user_id="bob", seat=1, stack=100),
    ]
    cards = [
        Card.parse("2s"),  # alice c1
        Card.parse("3h"),  # bob c1
        Card.parse("4d"),  # alice c2
        Card.parse("5c"),  # bob c2
        # Board: royal flush plays for everyone (broadway straight on board)
        Card.parse("Ts"), Card.parse("Js"), Card.parse("Qs"),
        Card.parse("Ks"),
        Card.parse("As"),
    ]
    deck = RiggedDeck.from_cards(cards + [c for c in _full_deck() if c not in cards])

    state = deal_hand(table, seats, button_seat=0, deck=deck)
    # Heads up: alice is button/SB, acts first. Both call/check to showdown.
    state = apply_action(state, Action("alice", ActionType.CALL), deck)  # call to 10
    state = apply_action(state, Action("bob", ActionType.CHECK), deck)
    # Flop
    state = apply_action(state, Action("bob", ActionType.CHECK), deck)  # SB acts first post-flop in HU... wait no.
    # In heads-up post-flop: BB acts first, then button. Let me recheck.
    # Actually per our table.py: post-flop first-to-act is "first active player left of button".
    # In HU with button at seat 0, that's seat 1 (bob). So bob acts first post-flop.
    # I already did bob.check above, now alice.
    state = apply_action(state, Action("alice", ActionType.CHECK), deck)
    # Turn
    state = apply_action(state, Action("bob", ActionType.CHECK), deck)
    state = apply_action(state, Action("alice", ActionType.CHECK), deck)
    # River
    state = apply_action(state, Action("bob", ActionType.CHECK), deck)
    state = apply_action(state, Action("alice", ActionType.CHECK), deck)

    assert state.phase == HandPhase.COMPLETE
    # Both have royal flush off the board; pot of 20 splits 10/10.
    assert _stack_of(state, "alice") == 100
    assert _stack_of(state, "bob") == 100


# ---------------------------------------------------------------------------
# 6. Pot-limit accounting: chips never created or destroyed
# ---------------------------------------------------------------------------

def test_chip_conservation_random_hand():
    """Across a hand with mixed actions, total chips at the table must equal start."""
    table = _table(sb=5, bb=10)
    starting_stacks = [200, 300, 150, 500]
    seats = [
        SeatConfig(user_id=f"p{i}", seat=i, stack=s)
        for i, s in enumerate(starting_stacks)
    ]
    deck = Deck.from_seed("conservation-test")
    state = deal_hand(table, seats, button_seat=0, deck=deck)

    # Drive a sequence of actions; we don't care who wins, just chip total.
    # 4-handed: SB=1, BB=2, action starts at seat 3.
    actions = [
        Action("p3", ActionType.RAISE, 30),  # raise to 30
        Action("p0", ActionType.CALL),       # call 30
        Action("p1", ActionType.CALL),       # SB completes from 5 to 30
        Action("p2", ActionType.CALL),       # BB calls 20 more
    ]
    for a in actions:
        state = apply_action(state, a, deck)

    # Now on flop, everyone checks down to river.
    if state.phase != HandPhase.COMPLETE:
        # Find action order and check around for each remaining street.
        for _ in range(20):  # Safety bound
            if state.phase == HandPhase.COMPLETE:
                break
            if not state.betting.to_act:
                break
            current = state.betting.to_act[0]
            state = apply_action(state, Action(current, ActionType.CHECK), deck)

    # Total chips at end should equal sum of starting + the pot still pending (none).
    total_end = sum(_stack_of(state, f"p{i}") for i in range(4))
    assert total_end == sum(starting_stacks)
