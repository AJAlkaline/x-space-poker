"""Narrator: turn engine events into spoken commentary text.

The narrator is a per-table stateful object. It maintains a small amount of
context — recent actions, current orbit's history, pot trajectory — so that
its commentary reflects what's happening in the *game* rather than restating
the current state. "Alice raises to 50" is a fact; "Alice fires back with
another raise" is commentary.

The output is text only. The TTS layer handles speech synthesis. The audio
layer handles delivery. This module has no I/O and no async — it's a pure
function of (events seen so far) → next utterance.

Design philosophy: under-narrate. The worst poker commentary fills every
moment with words. Good commentary picks its moments. A pre-flop limp
doesn't need a sentence. A 4-bet does. We skip routine actions (small
limps, automatic folds, checks-through) and emphasize meaningful ones
(big raises, all-ins, position changes, river bets, showdowns).

Style choices baked in:
- Short sentences. Live commentary, not written prose.
- Use names sparingly. "Alice raises" once; subsequent actions in the same
  orbit can drop to pronouns or position references.
- Stack sizes matter for context. "Bob calls 50 with 200 behind" tells you
  more than "Bob calls 50".
- Cards: phonetic-friendly. "Ace of spades" not "A of s".
- Numbers: spoken naturally. 150 → "one fifty"; 1000 → "a thousand";
  3500 → "thirty-five hundred". Done in `_format_chips`.

This produces strings. Empty string means "no commentary on this event" —
the audio layer treats that as silence.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Card rendering — phonetic, varied between calls
# ---------------------------------------------------------------------------

_RANK_NAMES = {
    "2": "two", "3": "three", "4": "four", "5": "five", "6": "six",
    "7": "seven", "8": "eight", "9": "nine", "T": "ten", "J": "jack",
    "Q": "queen", "K": "king", "A": "ace",
}
_SUIT_NAMES = {"c": "clubs", "d": "diamonds", "h": "hearts", "s": "spades"}


def _spell_card(card: str) -> str:
    """'Ah' → 'ace of hearts'. Cards in the engine are 2-char strings."""
    if len(card) != 2:
        return card
    rank = _RANK_NAMES.get(card[0], card[0])
    suit = _SUIT_NAMES.get(card[1], card[1])
    return f"{rank} of {suit}"


def _spell_flop(cards: list[str]) -> str:
    if len(cards) != 3:
        return ", ".join(_spell_card(c) for c in cards)
    return f"{_spell_card(cards[0])}, {_spell_card(cards[1])}, {_spell_card(cards[2])}"


# ---------------------------------------------------------------------------
# Chip formatting: read numbers the way a commentator would say them
# ---------------------------------------------------------------------------

def _format_chips(n: int) -> str:
    """Render `n` chips as a commentator would say it aloud.

    Examples:
        50 → "fifty"
        150 → "one fifty"
        1000 → "a thousand"
        1250 → "twelve fifty"
        3500 → "thirty-five hundred"
        12000 → "twelve thousand"
    """
    if n < 100:
        return _below_100(n)
    if n == 100:
        return "a hundred"
    if 100 < n < 1000:
        if n % 100 == 0:
            return f"{_below_100(n // 100)} hundred"
        h = n // 100
        rest = n % 100
        # 150 → "one fifty", 175 → "one seventy five"
        return f"{_below_100(h)} {_below_100(rest)}" if rest >= 10 else f"{_below_100(h)} o {_below_100(rest)}"
    if n == 1000:
        return "a thousand"
    if 1000 < n < 10000:
        # 1500 → "fifteen hundred"; 1250 → "twelve fifty"
        if n % 1000 == 0:
            return f"{_below_100(n // 1000)} thousand"
        if n % 100 == 0:
            return f"{_below_100(n // 100)} hundred"
        # Use "<X-thousand> <Y>" for awkward values (e.g. 1234)
        thousands = n // 1000
        rest = n % 1000
        return f"{_below_100(thousands)} thousand {_format_chips(rest)}"
    # 10000+: just say "twelve thousand" style
    if n % 1000 == 0:
        return f"{_below_100(n // 1000)} thousand"
    return f"{_below_100(n // 1000)} thousand {_format_chips(n % 1000)}"


_BELOW_20 = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen",
]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
         "eighty", "ninety"]


def _below_100(n: int) -> str:
    if n < 20:
        return _BELOW_20[n]
    tens = n // 10
    ones = n % 10
    if ones == 0:
        return _TENS[tens]
    return f"{_TENS[tens]} {_BELOW_20[ones]}"


# ---------------------------------------------------------------------------
# Narrator state — what we remember between events
# ---------------------------------------------------------------------------

@dataclass
class _HandContext:
    """Per-hand state the narrator tracks for context-aware commentary."""
    hand_number: int = 0
    phase: str = "pre_flop"
    players_in_hand: set[str] = field(default_factory=set)
    actions_this_street: list[tuple[str, str, int]] = field(default_factory=list)
    # (player_id, action_type, amount)
    raises_this_street: int = 0
    last_raise_amount: int = 0
    pot_at_street_start: int = 0
    introduced_players: set[str] = field(default_factory=set)
    pot_total: int = 0


@dataclass
class _OrbitContext:
    """Per-orbit context (resets every hand). Tracks who's done what across
    the broader rhythm of multiple hands at the same table — e.g. someone
    who's been raising every hand."""
    raises_by_player: dict[str, int] = field(default_factory=dict)
    hands_played: int = 0


# ---------------------------------------------------------------------------
# The narrator
# ---------------------------------------------------------------------------

class Narrator:
    """Stateful narrator for a single table.

    Call `on_hand_started`, `on_action`, `on_hand_completed` as events
    arrive. Each returns a string (commentary to speak) or empty string
    (skip this event).
    """

    def __init__(self, *, seed: int | None = None) -> None:
        self._rng = random.Random(seed)
        self._hand: _HandContext = _HandContext()
        self._orbit: _OrbitContext = _OrbitContext()

    # -----------------------------------------------------------------------
    # Hand lifecycle
    # -----------------------------------------------------------------------

    def on_hand_started(self, public_state: dict) -> str:
        """New hand. Reset hand context. Maybe announce."""
        self._hand = _HandContext(
            hand_number=int(public_state.get("hand_number") or 0),
            phase="pre_flop",
            players_in_hand={
                p["id"] for p in public_state.get("players", [])
                if p and p.get("status") in ("active", "all_in")
            },
            pot_total=int(public_state.get("pot_total") or 0),
        )
        self._orbit.hands_played += 1

        # Don't speak every hand. First hand of the session, milestone hands,
        # or hands with notable setup (short stacks, big blinds straddling).
        if self._orbit.hands_played == 1:
            n_players = len(self._hand.players_in_hand)
            if n_players == 2:
                return "Heads up. Let's go."
            return f"{_count_phrase(n_players)} handed. Cards in the air."

        # Periodic re-announcement of hand number, every 10 hands.
        if self._hand.hand_number % 10 == 0:
            return f"Hand number {self._hand.hand_number}."

        # Otherwise stay silent on the deal.
        return ""

    def on_action(self, action_event: dict, public_state: dict) -> str:
        """An action was applied. Return commentary or empty string."""
        player_id = action_event.get("player_id", "")
        action_type = action_event.get("action_type", "")
        amount = int(action_event.get("amount") or 0)
        auto = bool(action_event.get("auto"))

        # Detect street advance — check the public_state's phase against ours.
        new_phase = public_state.get("phase", self._hand.phase)
        if new_phase != self._hand.phase:
            self._hand.phase = new_phase
            self._hand.actions_this_street = []
            self._hand.raises_this_street = 0
            self._hand.last_raise_amount = 0
            self._hand.pot_at_street_start = int(public_state.get("pot_total") or 0)
            # Phase advance produces its own commentary — the new board.
            board = public_state.get("board") or []
            phase_announce = self._announce_phase(new_phase, board)
            # Then append the action commentary on the new street (if any).
            action_text = self._narrate_action(
                player_id, action_type, amount, auto, public_state,
            )
            self._record_action(player_id, action_type, amount, public_state)
            return _join_sentences(phase_announce, action_text)

        text = self._narrate_action(
            player_id, action_type, amount, auto, public_state,
        )
        self._record_action(player_id, action_type, amount, public_state)
        return text

    def on_hand_completed(
        self, public_state: dict, pot_distributions: list[dict],
    ) -> str:
        """Hand resolved. Announce showdown reveals (if any) and winners.

        New `pot_distributions` schema (from `_compute_pot_distributions`):
            [{"amount": int, "winners": [
                {"player_id": str, "hand_description": str,
                 "best_five": [card_strs]}, ...
            ]}, ...]
        Older shape used plain strings in `winners`; we accept both.

        For showdown hands (2+ players reach the river without folding), we
        prepend a per-player reveal sentence so the narration reads like:
            "Alice shows ace of hearts, king of hearts. Bob shows queen of
            clubs, queen of diamonds. Alice takes it with two pair, aces
            and kings."
        """
        if not pot_distributions:
            return ""

        def _winner_id(w) -> str:
            """Accept both new dict-shaped winners and legacy string ids."""
            if isinstance(w, dict):
                return str(w.get("player_id", ""))
            return str(w)

        def _winner_desc(w) -> str:
            if isinstance(w, dict):
                return str(w.get("hand_description", ""))
            return ""

        # ---- Showdown reveal lines ----
        # Build a list of (player_id, hole_cards) for everyone whose cards
        # are revealed in the public_state — at showdown, the engine reveals
        # holes of all non-folded players. Single-survivor wins don't reveal
        # anyone's cards (some engines do, ours doesn't), so this naturally
        # skips fold-wins.
        reveal_lines: list[str] = []
        revealed: list[tuple[int, str, list[str]]] = []  # (seat, id, holes)
        for p in public_state.get("players") or []:
            if not p:
                continue
            if p.get("status") == "folded":
                continue
            hole = p.get("hole")
            if not hole or len(hole) < 2:
                continue
            revealed.append((int(p.get("seat") or 0), p.get("id", ""), list(hole)))

        # Only count it as a showdown if 2+ players revealed.
        if len(revealed) >= 2:
            # Order clockwise from button so first to act post-flop speaks
            # first — that's roughly the showdown order in casino rules,
            # and at minimum makes the narration deterministic.
            button = int(public_state.get("button") or 0)
            n_seats = len(public_state.get("players") or []) or 1
            revealed.sort(
                key=lambda t: (t[0] - button - 1) % n_seats,
            )
            for _seat, pid, holes in revealed:
                handle = self._handle(pid)
                cards_str = ", ".join(_spell_card(c) for c in holes[:2])
                reveal_lines.append(
                    self._rng.choice([
                        f"{handle} shows {cards_str}.",
                        f"{handle} turns over {cards_str}.",
                        f"{handle} tables {cards_str}.",
                    ])
                )

        # ---- Winner announcement ----
        winner_line = ""
        if len(pot_distributions) == 1:
            d = pot_distributions[0]
            winners = d.get("winners") or []
            amount = int(d.get("amount") or 0)
            if winners:
                if len(winners) == 1:
                    w = winners[0]
                    wid = _winner_id(w)
                    desc = _winner_desc(w)
                    handle = self._handle(wid)
                    if desc:
                        winner_line = (
                            f"{handle} takes it with {desc.lower()}, "
                            f"{_format_chips(amount)}."
                        )
                    else:
                        # No description = fold-win
                        winner_line = f"{handle} scoops {_format_chips(amount)}."
                else:
                    names = " and ".join(self._handle(_winner_id(w)) for w in winners)
                    desc = _winner_desc(winners[0])
                    if desc:
                        winner_line = f"{names} chop the pot with {desc.lower()}."
                    else:
                        winner_line = f"{names} chop the pot."
        else:
            winner_line = "Multiple pots split among the winners."

        return _join_sentences(*reveal_lines, winner_line)

    # -----------------------------------------------------------------------
    # Action narration — the meat
    # -----------------------------------------------------------------------

    def _narrate_action(
        self, player_id: str, action_type: str, amount: int, auto: bool,
        public_state: dict,
    ) -> str:
        handle = self._handle(player_id)
        was_introduced = player_id in self._hand.introduced_players
        self._hand.introduced_players.add(player_id)

        # Auto-actions (timeout-driven) — say it only if it's a fold and
        # there was real money on the line. Auto-checks are silent.
        if auto:
            if action_type == "fold":
                return self._rng.choice([
                    f"{handle} times out. Folds.",
                    f"Clock catches {handle}. Folded.",
                ])
            return ""

        if action_type == "fold":
            # Folds are mostly boring. Mention only on big pots, on the
            # river, or for a player who's been active.
            current_bet = int(public_state.get("current_bet") or 0)
            pot = int(public_state.get("pot_total") or 0)
            if self._hand.phase == "river" and current_bet > 0:
                return f"{handle} lays it down on the river."
            if pot >= 20 * int(public_state.get("big_blind") or 1):
                return f"{handle} releases."
            return ""  # Silent fold

        if action_type == "check":
            # Checks are silent except for the BB option pre-flop, or check
            # after a flop bet (rare — only the first player to act).
            return ""

        if action_type == "call":
            return self._narrate_call(handle, amount, public_state, was_introduced)

        if action_type == "bet":
            return self._narrate_bet(handle, amount, public_state)

        if action_type == "raise":
            return self._narrate_raise(player_id, handle, amount, public_state)

        if action_type == "all_in":
            return self._narrate_all_in(handle, amount, public_state)

        return ""

    def _narrate_call(
        self, handle: str, amount: int, public_state: dict, was_introduced: bool,
    ) -> str:
        bb = int(public_state.get("big_blind") or 0) or 1
        # Limp pre-flop = call the BB. Silent unless it's a big stack limping.
        if self._hand.phase == "pre_flop" and amount <= bb:
            return ""
        # Calling a big bet: comment.
        if amount >= 5 * bb:
            return self._rng.choice([
                f"{handle} calls {_format_chips(amount)}.",
                f"{handle} comes along for {_format_chips(amount)}.",
                f"Call from {handle}, {_format_chips(amount)}.",
            ])
        # Routine call: brief.
        return f"{handle} calls."

    def _narrate_bet(self, handle: str, amount: int, public_state: dict) -> str:
        bb = int(public_state.get("big_blind") or 0) or 1
        pot = int(public_state.get("pot_total") or 0)
        # Pot-relative sizing description.
        pre_action_pot = pot - amount
        if pre_action_pot > 0 and amount >= pre_action_pot:
            return self._rng.choice([
                f"{handle} pots it. {_format_chips(amount)}.",
                f"Pot-sized from {handle}. {_format_chips(amount)}.",
            ])
        if amount >= 5 * bb:
            return self._rng.choice([
                f"{handle} fires {_format_chips(amount)}.",
                f"{handle} bets {_format_chips(amount)}.",
                f"Lead from {handle}, {_format_chips(amount)}.",
            ])
        return f"{handle} bets {_format_chips(amount)}."

    def _narrate_raise(
        self, player_id: str, handle: str, amount: int, public_state: dict,
    ) -> str:
        self._hand.raises_this_street += 1
        n = self._hand.raises_this_street
        # n is which raise this is on this street (1 = open, 2 = 3-bet, 3 = 4-bet, etc.)
        self._orbit.raises_by_player[player_id] = (
            self._orbit.raises_by_player.get(player_id, 0) + 1
        )

        if n == 1 and self._hand.phase == "pre_flop":
            return self._rng.choice([
                f"{handle} opens to {_format_chips(amount)}.",
                f"Raise from {handle}, {_format_chips(amount)}.",
                f"{handle} comes in for {_format_chips(amount)}.",
            ])
        if n == 2 and self._hand.phase == "pre_flop":
            return self._rng.choice([
                f"{handle} three-bets to {_format_chips(amount)}.",
                f"Three-bet from {handle}, {_format_chips(amount)}.",
                f"{handle} fires back, {_format_chips(amount)} to play.",
            ])
        if n == 3 and self._hand.phase == "pre_flop":
            return self._rng.choice([
                f"{handle} four-bets to {_format_chips(amount)}.",
                f"Four-bet, {handle} for {_format_chips(amount)}.",
            ])
        if n >= 4 and self._hand.phase == "pre_flop":
            return f"{handle} keeps shoving it back, {_format_chips(amount)}."
        # Post-flop raises.
        if n >= 2:
            return self._rng.choice([
                f"{handle} raises to {_format_chips(amount)}.",
                f"And a raise from {handle}, {_format_chips(amount)}.",
            ])
        return f"{handle} raises to {_format_chips(amount)}."

    def _narrate_all_in(
        self, handle: str, amount: int, public_state: dict,
    ) -> str:
        return self._rng.choice([
            f"{handle} is all in for {_format_chips(amount)}.",
            f"All in from {handle}, {_format_chips(amount)}.",
            f"{handle} jams for {_format_chips(amount)}.",
        ])

    # -----------------------------------------------------------------------
    # Phase announcements
    # -----------------------------------------------------------------------

    def _announce_phase(self, phase: str, board: list[str]) -> str:
        if phase == "flop" and len(board) >= 3:
            return f"Flop. {_spell_flop(board[:3])}."
        if phase == "turn" and len(board) >= 4:
            return f"Turn. {_spell_card(board[3])}."
        if phase == "river" and len(board) >= 5:
            return f"River. {_spell_card(board[4])}."
        if phase == "showdown":
            return "Showdown."
        return ""

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _handle(self, player_id: str) -> str:
        """Render a player id. For X handles like '@alice', strip the @ for
        speech and capitalize. For raw uuids, fallback to 'a player'."""
        if not player_id:
            return "the player"
        # If it looks like a uuid, anonymize.
        if len(player_id) > 16 and "-" in player_id:
            return "the player"
        h = player_id.lstrip("@")
        return h.capitalize()

    def _record_action(
        self, player_id: str, action_type: str, amount: int, public_state: dict,
    ) -> None:
        self._hand.actions_this_street.append((player_id, action_type, amount))
        if action_type == "fold":
            self._hand.players_in_hand.discard(player_id)
        if action_type == "raise":
            self._hand.last_raise_amount = amount
        self._hand.pot_total = int(public_state.get("pot_total") or 0)

    def _went_to_showdown(self, public_state: dict) -> bool:
        # If more than one player is still in at hand_completed time and
        # the phase reached river, it went to showdown.
        active = [
            p for p in public_state.get("players") or []
            if p and p.get("status") in ("active", "all_in")
        ]
        return len(active) >= 2 and public_state.get("phase") in ("river", "showdown", "complete")

    def _winning_hand_description(
        self, player_id: str, public_state: dict,
    ) -> str:
        """If the winning player's hole cards and board are visible in the
        public_state (showdown), describe the made hand. Otherwise empty."""
        # We won't actually compute the made hand here — that's the engine's
        # job. The public_state at hand_complete may include a hand_rank
        # description for the winners; use it if so.
        for p in public_state.get("players") or []:
            if p and p.get("id") == player_id:
                hand_desc = p.get("hand_description") or p.get("hand_rank_label")
                if hand_desc:
                    return str(hand_desc)
        return ""


def _join_sentences(*parts: str) -> str:
    """Join non-empty parts with a space."""
    return " ".join(p for p in parts if p)


def _count_phrase(n: int) -> str:
    return {2: "Two", 3: "Three", 4: "Four", 5: "Five", 6: "Six",
            7: "Seven", 8: "Eight", 9: "Nine"}.get(n, str(n))
