"""Cards, suits, ranks, and a deterministic deck.

The deck takes a seed at construction time. The seed is hashed (SHA-256) at hand
start as a commitment, and revealed at hand end. Players can verify after the
fact that the deck wasn't reshuffled mid-hand or rigged based on hole cards.
"""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from enum import Enum
from random import Random


class Suit(str, Enum):
    SPADES = "s"
    HEARTS = "h"
    DIAMONDS = "d"
    CLUBS = "c"


class Rank(int, Enum):
    TWO = 2
    THREE = 3
    FOUR = 4
    FIVE = 5
    SIX = 6
    SEVEN = 7
    EIGHT = 8
    NINE = 9
    TEN = 10
    JACK = 11
    QUEEN = 12
    KING = 13
    ACE = 14


_RANK_CHARS = {
    Rank.TWO: "2", Rank.THREE: "3", Rank.FOUR: "4", Rank.FIVE: "5",
    Rank.SIX: "6", Rank.SEVEN: "7", Rank.EIGHT: "8", Rank.NINE: "9",
    Rank.TEN: "T", Rank.JACK: "J", Rank.QUEEN: "Q", Rank.KING: "K",
    Rank.ACE: "A",
}
_CHAR_RANK = {v: k for k, v in _RANK_CHARS.items()}


@dataclass(frozen=True, slots=True)
class Card:
    rank: Rank
    suit: Suit

    def __str__(self) -> str:
        return f"{_RANK_CHARS[self.rank]}{self.suit.value}"

    @classmethod
    def parse(cls, s: str) -> Card:
        if len(s) != 2:
            raise ValueError(f"invalid card string: {s!r}")
        return cls(rank=_CHAR_RANK[s[0].upper()], suit=Suit(s[1].lower()))


def _full_deck() -> list[Card]:
    return [Card(rank=r, suit=s) for s in Suit for r in Rank]


@dataclass
class Deck:
    """Stateful deck. Construct with a seed for determinism, or use `random()` for a fresh one.

    Use `commit()` to produce a SHA-256 of the seed (publishable at hand start) and
    `reveal()` to expose the seed at hand end for verification.
    """
    seed: str
    _cards: list[Card]
    _index: int

    @classmethod
    def from_seed(cls, seed: str) -> Deck:
        cards = _full_deck()
        Random(seed).shuffle(cards)
        return cls(seed=seed, _cards=cards, _index=0)

    @classmethod
    def random(cls) -> Deck:
        # 256 bits of entropy; hex-encoded so the seed is human-loggable
        return cls.from_seed(secrets.token_hex(32))

    def commit(self) -> str:
        return hashlib.sha256(self.seed.encode("utf-8")).hexdigest()

    def reveal(self) -> str:
        return self.seed

    def draw(self, n: int = 1) -> list[Card]:
        if self._index + n > len(self._cards):
            raise RuntimeError("deck exhausted")
        out = self._cards[self._index : self._index + n]
        self._index += n
        return out

    def remaining(self) -> int:
        return len(self._cards) - self._index
