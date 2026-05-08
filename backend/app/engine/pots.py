"""Side-pot calculator.

The classic case the algorithm must handle:

    Player A (stack 100) goes all-in.
    Player B (stack 300) goes all-in.
    Player C (stack 500) calls 300.

    Main pot:  3 * 100 = 300, contested by A, B, C.
    Side pot 1: 2 * 200 = 400, contested by B, C only (A can't win this).
    No further side pot because B is all-in for less than C's commit; the extra
    chips C committed beyond B's all-in are returned to C *unless* there are
    other contestants. Here there aren't, so C gets back 200 uncalled.

The algorithm:

1. Group players by their `total_committed` amount (ascending).
2. For each distinct commitment level, peel off `level * num_contributors` chips
   into a pot, eligible to all players who committed at least `level` AND are
   still in the hand (not folded). Folded players' chips go into the lowest
   pots they qualify for but they are not eligible to win.
3. After the loop, any chips committed by exactly one in-hand player above all
   others are returned to that player (uncalled bet refund).

This is called once at showdown OR when only one in-hand player remains (the
latter case wins everything by default).
"""
from __future__ import annotations

from app.engine.state import Player, PlayerStatus, Pot


def compute_pots(
    players: list[Player],
) -> tuple[list[Pot], dict[str, int]]:
    """Compute pots and uncalled-bet refunds from final commitments.

    Returns (pots, refunds). `refunds[player_id]` is chips returned to the player
    because no one matched their final raise.

    Folded players' chips are included in the pot they contributed to but they
    are not in `eligible_players`.
    """
    if not players:
        return [], {}

    # Snapshot commitments. Filter zero-commit players (they didn't pay blinds and folded immediately).
    contributions: list[tuple[str, int, bool]] = [
        (p.id, p.total_committed, p.status != PlayerStatus.FOLDED)
        for p in players
        if p.total_committed > 0
    ]

    # Uncalled-bet refund: if exactly one in-hand player committed strictly more
    # than the next-highest commitment, the excess is returned. If multiple
    # in-hand players are tied at the top, no refund.
    refunds: dict[str, int] = {}
    in_hand = [(pid, c) for pid, c, ih in contributions if ih]
    if len(in_hand) >= 1:
        max_commit = max(c for _, c in in_hand)
        top = [pid for pid, c in in_hand if c == max_commit]
        if len(top) == 1:
            # Find second-highest commitment among ALL contributors (folded or not).
            others = sorted(
                (c for pid, c, _ in contributions if pid != top[0]),
                reverse=True,
            )
            if others and max_commit > others[0]:
                refund = max_commit - others[0]
                refunds[top[0]] = refund
                # Reduce that player's recorded contribution for the pot calc.
                contributions = [
                    (pid, c - refund if pid == top[0] else c, ih)
                    for pid, c, ih in contributions
                ]
        # else: tied at top, no refund

    # Build pots by peeling off layers.
    pots: list[Pot] = []
    levels = sorted({c for _, c, _ in contributions if c > 0})
    prev = 0
    for level in levels:
        slice_size = level - prev
        contributors = [pid for pid, c, _ in contributions if c >= level]
        eligible = tuple(
            pid for pid, c, ih in contributions if c >= level and ih
        )
        amount = slice_size * len(contributors)
        if amount > 0 and eligible:
            # Merge with previous pot if eligibility set is identical (avoids
            # spurious "side pot of 0 extra" when no one busted at this level).
            if pots and pots[-1].eligible_players == eligible:
                pots[-1] = Pot(
                    amount=pots[-1].amount + amount,
                    eligible_players=eligible,
                )
            else:
                pots.append(Pot(amount=amount, eligible_players=eligible))
        elif amount > 0 and not eligible and pots:
            # Dead money (e.g. all eligible players folded). Add to last pot.
            pots[-1] = Pot(
                amount=pots[-1].amount + amount,
                eligible_players=pots[-1].eligible_players,
            )
            # else: pathological case (everyone folded with no in-hand player);
            # caller should have short-circuited before reaching here.
        prev = level

    return pots, refunds
