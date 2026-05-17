"""Narrator tests.

The narrator is a pure stateful function. We test:

1. Card and chip formatters produce reasonable strings
2. Action narration shapes — silent for routine actions, voiced for big ones
3. Hand flow: heads-up hand from deal to showdown produces a coherent
   sequence of commentary lines

Tests are deterministic — narrator accepts a seed so the random choices
between phrasing variants are reproducible.
"""
from __future__ import annotations

from app.services.narrator import (
    Narrator,
    _format_chips,
    _spell_card,
    _spell_flop,
)


class TestFormatters:
    def test_chips_under_100(self):
        assert _format_chips(5) == "five"
        assert _format_chips(50) == "fifty"
        assert _format_chips(99) == "ninety nine"

    def test_chips_round_hundreds(self):
        assert _format_chips(100) == "a hundred"
        assert _format_chips(200) == "two hundred"
        assert _format_chips(500) == "five hundred"

    def test_chips_round_thousands(self):
        assert _format_chips(1000) == "a thousand"
        assert _format_chips(5000) == "five thousand"
        assert _format_chips(12000) == "twelve thousand"

    def test_chips_typical_poker_amounts(self):
        # Common poker values should sound natural.
        assert _format_chips(150) in ("one fifty", "one hundred")
        assert "fifteen" in _format_chips(1500)
        assert "thousand" in _format_chips(10000)

    def test_spell_card(self):
        assert _spell_card("Ah") == "ace of hearts"
        assert _spell_card("Td") == "ten of diamonds"
        assert _spell_card("2c") == "two of clubs"
        assert _spell_card("Ks") == "king of spades"

    def test_spell_flop(self):
        result = _spell_flop(["Ah", "Kd", "Qc"])
        assert "ace of hearts" in result
        assert "king of diamonds" in result
        assert "queen of clubs" in result


class TestHandLifecycle:
    """Walk a full hand through and check the narration is sensible."""

    def _initial_state(self, players=("alice", "bob"), big_blind=10):
        return {
            "hand_number": 1,
            "phase": "pre_flop",
            "board": [],
            "current_bet": big_blind,
            "big_blind": big_blind,
            "small_blind": big_blind // 2,
            "pot_total": big_blind + (big_blind // 2),
            "players": [
                {"id": p, "status": "active", "street_committed": 0}
                for p in players
            ],
        }

    def test_first_hand_gets_announced(self):
        n = Narrator(seed=0)
        text = n.on_hand_started(self._initial_state())
        assert text  # not empty
        assert "heads up" in text.lower() or "handed" in text.lower()

    def test_subsequent_routine_hand_is_silent(self):
        n = Narrator(seed=0)
        n.on_hand_started(self._initial_state())  # hand 1: announce
        text = n.on_hand_started({**self._initial_state(), "hand_number": 2})
        # Hand 2 should be silent (we don't announce every hand).
        assert text == ""

    def test_fold_is_usually_silent(self):
        n = Narrator(seed=0)
        n.on_hand_started(self._initial_state())
        # Carol folds in a small pot — should be silent.
        action = {
            "player_id": "carol", "action_type": "fold", "amount": 0,
            "auto": False,
        }
        text = n.on_action(action, self._initial_state())
        assert text == ""

    def test_open_raise_pre_flop_narrated(self):
        n = Narrator(seed=0)
        n.on_hand_started(self._initial_state())
        action = {
            "player_id": "alice", "action_type": "raise", "amount": 30,
        }
        ps = {**self._initial_state(), "current_bet": 30}
        text = n.on_action(action, ps)
        assert "alice" in text.lower()
        assert "thirty" in text.lower()

    def test_three_bet_is_recognized(self):
        n = Narrator(seed=0)
        n.on_hand_started(self._initial_state())
        # Alice opens
        n.on_action(
            {"player_id": "alice", "action_type": "raise", "amount": 30},
            {**self._initial_state(), "current_bet": 30},
        )
        # Bob 3-bets
        text = n.on_action(
            {"player_id": "bob", "action_type": "raise", "amount": 90},
            {**self._initial_state(), "current_bet": 90},
        )
        assert "three-bet" in text.lower() or "three bet" in text.lower()

    def test_four_bet_is_recognized(self):
        n = Narrator(seed=0)
        n.on_hand_started(self._initial_state())
        n.on_action(
            {"player_id": "alice", "action_type": "raise", "amount": 30},
            {**self._initial_state(), "current_bet": 30},
        )
        n.on_action(
            {"player_id": "bob", "action_type": "raise", "amount": 90},
            {**self._initial_state(), "current_bet": 90},
        )
        text = n.on_action(
            {"player_id": "alice", "action_type": "raise", "amount": 270},
            {**self._initial_state(), "current_bet": 270},
        )
        assert "four-bet" in text.lower() or "four bet" in text.lower()

    def test_flop_announce_includes_all_three_cards(self):
        n = Narrator(seed=0)
        n.on_hand_started(self._initial_state())
        # Get to flop with both players seeing it.
        ps_flop = {
            **self._initial_state(),
            "phase": "flop",
            "board": ["Kc", "Ks", "4h"],
            "current_bet": 0,
        }
        # The action that triggers the phase advance: previous player checks.
        text = n.on_action(
            {"player_id": "bob", "action_type": "check", "amount": 0},
            ps_flop,
        )
        assert "king of clubs" in text.lower()
        assert "king of spades" in text.lower()
        assert "four of hearts" in text.lower()
        assert "flop" in text.lower()

    def test_all_in_is_emphatic(self):
        n = Narrator(seed=0)
        n.on_hand_started(self._initial_state())
        text = n.on_action(
            {"player_id": "alice", "action_type": "all_in", "amount": 500},
            self._initial_state(),
        )
        assert text  # not silent
        assert "all" in text.lower() or "jam" in text.lower()

    def test_winner_announced_at_hand_completion(self):
        n = Narrator(seed=0)
        n.on_hand_started(self._initial_state())
        text = n.on_hand_completed(
            self._initial_state(),
            [{"winners": ["alice"], "amount": 100}],
        )
        assert "alice" in text.lower()
        assert text  # has content

    def test_full_hand_produces_coherent_narration(self):
        """A heads-up hand from deal to win — make sure the sequence of
        narration lines is plausible."""
        n = Narrator(seed=42)
        lines = []

        # Hand starts
        ps = self._initial_state(players=("alice", "bob"))
        line = n.on_hand_started(ps)
        if line:
            lines.append(line)

        # Pre-flop: alice raises, bob 3-bets, alice calls
        line = n.on_action(
            {"player_id": "alice", "action_type": "raise", "amount": 30},
            {**ps, "current_bet": 30, "pot_total": 45},
        )
        if line:
            lines.append(line)
        line = n.on_action(
            {"player_id": "bob", "action_type": "raise", "amount": 90},
            {**ps, "current_bet": 90, "pot_total": 120},
        )
        if line:
            lines.append(line)
        line = n.on_action(
            {"player_id": "alice", "action_type": "call", "amount": 60},
            {**ps, "current_bet": 90, "pot_total": 180},
        )
        if line:
            lines.append(line)

        # Flop: Kc Ks 4h, bob bets 100, alice folds
        ps_flop = {**ps, "phase": "flop", "board": ["Kc", "Ks", "4h"],
                   "current_bet": 0, "pot_total": 180}
        line = n.on_action(
            {"player_id": "bob", "action_type": "bet", "amount": 100},
            {**ps_flop, "current_bet": 100, "pot_total": 280},
        )
        if line:
            lines.append(line)
        line = n.on_action(
            {"player_id": "alice", "action_type": "fold", "amount": 0},
            {**ps_flop, "current_bet": 100, "pot_total": 280},
        )
        if line:
            lines.append(line)

        # Bob wins
        line = n.on_hand_completed(
            {**ps_flop, "pot_total": 280},
            [{"winners": ["bob"], "amount": 280}],
        )
        if line:
            lines.append(line)

        # Joined narration should mention key facts
        full = " ".join(lines).lower()
        assert "alice" in full
        assert "bob" in full
        assert "three-bet" in full or "three bet" in full
        assert "king" in full  # the flop
        assert "bob" in full  # winner

        # Print for human inspection
        for line in lines:
            print(line)

    def test_uuid_player_id_is_anonymized(self):
        """If a player's id is a raw uuid we don't have a handle for, we
        shouldn't try to speak it."""
        n = Narrator(seed=0)
        ps = self._initial_state()
        ps["players"] = [
            {"id": "12345678-abcd-1234-5678-1234567890ab", "status": "active",
             "street_committed": 0},
            {"id": "bob", "status": "active", "street_committed": 0},
        ]
        n.on_hand_started(ps)
        text = n.on_action(
            {"player_id": "12345678-abcd-1234-5678-1234567890ab",
             "action_type": "raise", "amount": 30},
            {**ps, "current_bet": 30},
        )
        assert "12345678" not in text


class TestSilenceForRoutineActions:
    """The narrator should under-narrate, not over-narrate."""

    def _state(self):
        return {
            "hand_number": 5, "phase": "pre_flop", "board": [],
            "current_bet": 10, "big_blind": 10, "small_blind": 5,
            "pot_total": 15, "players": [],
        }

    def test_routine_check_silent(self):
        n = Narrator(seed=0)
        n.on_hand_started(self._state())
        text = n.on_action(
            {"player_id": "alice", "action_type": "check", "amount": 0},
            {**self._state(), "phase": "flop", "current_bet": 0,
             "board": ["2c", "5d", "9h"]},
        )
        # The check itself shouldn't trigger commentary, but the phase
        # advance to flop *should* announce the flop.
        assert "flop" in text.lower()  # phase advance happened

    def test_auto_check_silent(self):
        n = Narrator(seed=0)
        n.on_hand_started(self._state())
        text = n.on_action(
            {"player_id": "alice", "action_type": "check", "amount": 0,
             "auto": True},
            self._state(),
        )
        assert text == ""

    def test_pre_flop_limp_silent(self):
        n = Narrator(seed=0)
        n.on_hand_started(self._state())
        # Alice limps (calls the BB exactly)
        text = n.on_action(
            {"player_id": "alice", "action_type": "call", "amount": 10},
            self._state(),
        )
        assert text == ""

    def test_auto_fold_is_voiced(self):
        n = Narrator(seed=0)
        n.on_hand_started(self._state())
        text = n.on_action(
            {"player_id": "alice", "action_type": "fold", "amount": 0,
             "auto": True},
            self._state(),
        )
        # Auto-folds are interesting — clock ran out.
        assert text
        assert "alice" in text.lower() or "clock" in text.lower() or "time" in text.lower()
