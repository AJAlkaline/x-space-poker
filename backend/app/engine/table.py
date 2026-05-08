"""Table-level operations: deal a hand, compute legal actions, apply an action.

This module is the engine's public API. All functions are pure: they take state
in and return state out. No I/O, no async, no globals.

The two key entry points are:
- `deal_hand`: given a Table snapshot, deck, and button position, produce the
  initial GameState (blinds posted, hole cards dealt, action on UTG).
- `apply_action`: given a GameState and an Action, validate and produce the
  next GameState. May trigger phase transitions (deal flop, deal turn, etc.)
  and pot resolution at hand end.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, replace

from app.engine.cards import Deck
from app.engine.evaluator import evaluate_hand
from app.engine.pots import compute_pots
from app.engine.state import (
    Action,
    ActionType,
    BettingRound,
    GameState,
    HandPhase,
    Player,
    PlayerStatus,
)


@dataclass(frozen=True, slots=True)
class Table:
    """Persistent table configuration. Lives between hands."""
    id: str
    small_blind: int
    big_blind: int
    max_seats: int


@dataclass(frozen=True, slots=True)
class SeatConfig:
    """Used by deal_hand to describe who's playing this hand and with what stack."""
    user_id: str
    seat: int
    stack: int
    sitting_out: bool = False


# ---------------------------------------------------------------------------
# Hand setup
# ---------------------------------------------------------------------------

def deal_hand(
    table: Table,
    seats: list[SeatConfig],
    button_seat: int,
    deck: Deck,
) -> GameState:
    """Initialize a new hand. Posts blinds, deals hole cards, sets action.

    `seats` lists every occupied seat at the table. Players with `sitting_out=True`
    or with `stack == 0` are skipped — they sit out this hand.

    `button_seat` is the seat number of the dealer button. Caller is responsible
    for advancing it correctly between hands (skip empty seats and bust players).

    Heads-up rules: with exactly 2 players, the button posts the small blind and
    acts first pre-flop / second post-flop. With 3+, button is button, SB is left
    of button, BB is left of SB.
    """
    in_hand_seats = [
        s for s in sorted(seats, key=lambda x: x.seat)
        if not s.sitting_out and s.stack > 0
    ]
    if len(in_hand_seats) < 2:
        raise ValueError("need at least 2 active seats to deal a hand")

    # Build seat array of length max_seats, dealing hole cards rotationally
    # (one card to each player in order, then a second pass).
    seat_array: list[Player | None] = [None] * table.max_seats
    seat_to_player_id: dict[int, str] = {}
    first_cards = deck.draw(len(in_hand_seats))
    second_cards = deck.draw(len(in_hand_seats))
    for i, s in enumerate(in_hand_seats):
        hole = (first_cards[i], second_cards[i])
        player = Player(
            id=s.user_id,
            seat=s.seat,
            stack=s.stack,
            hole=hole,
            status=PlayerStatus.ACTIVE,
        )
        seat_array[s.seat] = player
        seat_to_player_id[s.seat] = s.user_id

    in_hand_seat_nums = sorted(seat_to_player_id.keys())

    # Determine SB / BB seats.
    n = len(in_hand_seat_nums)
    button_idx = in_hand_seat_nums.index(button_seat) if button_seat in in_hand_seat_nums \
        else _nearest_seat_index(in_hand_seat_nums, button_seat)
    if n == 2:
        sb_idx = button_idx
        bb_idx = (button_idx + 1) % n
    else:
        sb_idx = (button_idx + 1) % n
        bb_idx = (button_idx + 2) % n

    sb_seat = in_hand_seat_nums[sb_idx]
    bb_seat = in_hand_seat_nums[bb_idx]

    # Post blinds.
    state = GameState(
        hand_id=str(uuid.uuid4()),
        table_id=table.id,
        small_blind=table.small_blind,
        big_blind=table.big_blind,
        players=tuple(seat_array),
        button=button_seat,
        phase=HandPhase.PRE_FLOP,
        board=(),
        pots=(),
        betting=BettingRound(
            current_bet=0,
            min_raise=table.big_blind,
            last_raiser_id=None,
            to_act=(),
        ),
        deck_commit=deck.commit(),
    )
    state = _post_blind(state, sb_seat, table.small_blind)
    state = _post_blind(state, bb_seat, table.big_blind)

    # current_bet after blinds is BB; min raise increment is BB.
    # First-to-act pre-flop: in heads-up, button (= SB); otherwise, seat after BB.
    first_to_act = (
        in_hand_seat_nums[button_idx] if n == 2
        else in_hand_seat_nums[(bb_idx + 1) % n]
    )

    # Build to_act order: starting from first_to_act, in seat order, all in-hand players
    # who haven't already used up their action (BB still gets option to raise pre-flop).
    to_act_order = _action_order(in_hand_seat_nums, first_to_act, state.players)
    state = replace(
        state,
        betting=BettingRound(
            current_bet=table.big_blind,
            min_raise=table.big_blind,
            last_raiser_id=seat_to_player_id[bb_seat],  # BB is treated as the opener
            to_act=tuple(to_act_order),
        ),
    )
    return state


def _nearest_seat_index(seats: list[int], target: int) -> int:
    """If button_seat is empty (e.g. player just left), pick the next-highest occupied seat."""
    for i, s in enumerate(seats):
        if s >= target:
            return i
    return 0


def _post_blind(state: GameState, seat: int, amount: int) -> GameState:
    p = state.players[seat]
    assert p is not None
    pay = min(p.stack, amount)
    new_player = replace(
        p,
        stack=p.stack - pay,
        street_committed=p.street_committed + pay,
        total_committed=p.total_committed + pay,
        status=PlayerStatus.ALL_IN if p.stack - pay == 0 else PlayerStatus.ACTIVE,
        last_action=ActionType.POST_BLIND,
    )
    new_players = list(state.players)
    new_players[seat] = new_player
    return replace(state, players=tuple(new_players))


def _action_order(
    in_hand_seats: list[int],
    start_seat: int,
    players: tuple[Player | None, ...],
) -> list[str]:
    """Return player IDs in action order starting from `start_seat`, only including
    players who can still act (status == ACTIVE)."""
    if start_seat not in in_hand_seats:
        # Caller error; fall back to first seat.
        start_seat = in_hand_seats[0]
    start_idx = in_hand_seats.index(start_seat)
    rotated = in_hand_seats[start_idx:] + in_hand_seats[:start_idx]
    out = []
    for s in rotated:
        p = players[s]
        if p is not None and p.status == PlayerStatus.ACTIVE:
            out.append(p.id)
    return out


# ---------------------------------------------------------------------------
# Legal-action computation
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class LegalAction:
    action_type: ActionType
    min_amount: int = 0   # For BET/RAISE: minimum legal `to` amount
    max_amount: int = 0   # For BET/RAISE/CALL: maximum (typically all-in)


def legal_actions(state: GameState, player_id: str) -> list[LegalAction]:
    """Return the set of legal actions for this player given current state.

    Returns empty list if it's not their turn or hand is over.
    """
    if state.phase in (HandPhase.PRE_DEAL, HandPhase.SHOWDOWN, HandPhase.COMPLETE):
        return []
    if not state.betting.to_act or state.betting.to_act[0] != player_id:
        return []
    p = state.player_by_id(player_id)
    if p is None or p.status != PlayerStatus.ACTIVE:
        return []

    out: list[LegalAction] = [LegalAction(ActionType.FOLD)]

    to_call = state.betting.current_bet - p.street_committed
    if to_call <= 0:
        out.append(LegalAction(ActionType.CHECK))
    else:
        # Call (capped at remaining stack).
        out.append(LegalAction(
            ActionType.CALL,
            min_amount=min(to_call, p.stack),
            max_amount=min(to_call, p.stack),
        ))

    # BET vs RAISE depends on whether anyone has bet this street.
    if state.betting.current_bet == 0:
        # BET is legal if player has chips. Min bet is one BB (or all-in if less).
        min_bet = min(state.big_blind, p.stack)
        if p.stack > 0:
            out.append(LegalAction(
                ActionType.BET,
                min_amount=min_bet,
                max_amount=p.stack,  # All-in
            ))
    else:
        # RAISE: must raise to at least current_bet + min_raise (or shove).
        if p.stack > to_call:  # Has more than just calling
            min_raise_to = min(
                state.betting.current_bet + state.betting.min_raise,
                p.street_committed + p.stack,  # All-in shove
            )
            max_raise_to = p.street_committed + p.stack
            out.append(LegalAction(
                ActionType.RAISE,
                min_amount=min_raise_to,
                max_amount=max_raise_to,
            ))

    return out


# ---------------------------------------------------------------------------
# Action application
# ---------------------------------------------------------------------------

def apply_action(state: GameState, action: Action, deck: Deck) -> GameState:
    """Apply an action and return the next state. Validates and may transition phase."""
    legals = legal_actions(state, action.player_id)
    legal_types = {la.action_type for la in legals}
    if action.action_type not in legal_types:
        raise ValueError(
            f"illegal action {action.action_type} for player {action.player_id} "
            f"(legal: {legal_types})"
        )

    p = state.player_by_id(action.player_id)
    assert p is not None

    new_state = state
    if action.action_type == ActionType.FOLD:
        new_state = _apply_fold(state, p)
    elif action.action_type == ActionType.CHECK:
        new_state = _apply_check(state, p)
    elif action.action_type == ActionType.CALL:
        new_state = _apply_call(state, p)
    elif action.action_type == ActionType.BET:
        la = next(la for la in legals if la.action_type == ActionType.BET)
        if action.amount < la.min_amount or action.amount > la.max_amount:
            raise ValueError(f"bet amount {action.amount} outside [{la.min_amount}, {la.max_amount}]")
        new_state = _apply_bet(state, p, action.amount)
    elif action.action_type == ActionType.RAISE:
        la = next(la for la in legals if la.action_type == ActionType.RAISE)
        if action.amount < la.min_amount or action.amount > la.max_amount:
            raise ValueError(f"raise to {action.amount} outside [{la.min_amount}, {la.max_amount}]")
        new_state = _apply_raise(state, p, action.amount)

    # Check if betting round is complete or hand is over.
    new_state = _maybe_advance_phase(new_state, deck)
    return new_state


def _update_player(state: GameState, player: Player) -> GameState:
    new_players = list(state.players)
    new_players[player.seat] = player
    return replace(state, players=tuple(new_players))


def _drop_from_to_act(state: GameState, player_id: str) -> GameState:
    return replace(
        state,
        betting=replace(
            state.betting,
            to_act=tuple(pid for pid in state.betting.to_act if pid != player_id),
        ),
    )


def _apply_fold(state: GameState, player: Player) -> GameState:
    new_player = replace(player, status=PlayerStatus.FOLDED, last_action=ActionType.FOLD)
    state = _update_player(state, new_player)
    return _drop_from_to_act(state, player.id)


def _apply_check(state: GameState, player: Player) -> GameState:
    new_player = replace(player, last_action=ActionType.CHECK)
    state = _update_player(state, new_player)
    return _drop_from_to_act(state, player.id)


def _apply_call(state: GameState, player: Player) -> GameState:
    to_call = state.betting.current_bet - player.street_committed
    pay = min(to_call, player.stack)
    new_status = PlayerStatus.ALL_IN if pay == player.stack else PlayerStatus.ACTIVE
    new_player = replace(
        player,
        stack=player.stack - pay,
        street_committed=player.street_committed + pay,
        total_committed=player.total_committed + pay,
        status=new_status,
        last_action=ActionType.CALL,
    )
    state = _update_player(state, new_player)
    return _drop_from_to_act(state, player.id)


def _apply_bet(state: GameState, player: Player, amount: int) -> GameState:
    # `amount` is the total street commitment (== bet size since street_committed was 0).
    pay = amount - player.street_committed
    new_status = PlayerStatus.ALL_IN if pay == player.stack else PlayerStatus.ACTIVE
    new_player = replace(
        player,
        stack=player.stack - pay,
        street_committed=amount,
        total_committed=player.total_committed + pay,
        status=new_status,
        last_action=ActionType.BET,
    )
    state = _update_player(state, new_player)

    # New current_bet = amount; min_raise = amount (the bet itself sets the min raise).
    # All other in-hand active players need to act again.
    other_actives = [
        p for p in state.players
        if p is not None and p.id != player.id and p.status == PlayerStatus.ACTIVE
    ]
    new_to_act = tuple(p.id for p in sorted(other_actives, key=lambda x: x.seat))
    # Reorder so it starts from seat after bettor.
    new_to_act = _rotate_to_after(new_to_act, state, player.seat)
    return replace(
        state,
        betting=BettingRound(
            current_bet=amount,
            min_raise=amount,
            last_raiser_id=player.id,
            to_act=new_to_act,
        ),
    )


def _apply_raise(state: GameState, player: Player, amount: int) -> GameState:
    # `amount` is the new total street commitment (the "raise to" value).
    pay = amount - player.street_committed
    raise_increment = amount - state.betting.current_bet
    new_status = PlayerStatus.ALL_IN if pay == player.stack else PlayerStatus.ACTIVE

    # Under standard NLHE rules, an all-in raise smaller than min_raise does NOT
    # reopen the action for players who already acted. We track this by only
    # updating min_raise if the increment was at least the previous min_raise.
    new_min_raise = max(state.betting.min_raise, raise_increment) \
        if raise_increment >= state.betting.min_raise \
        else state.betting.min_raise

    new_player = replace(
        player,
        stack=player.stack - pay,
        street_committed=amount,
        total_committed=player.total_committed + pay,
        status=new_status,
        last_action=ActionType.RAISE,
    )
    state = _update_player(state, new_player)

    # Reopen action for all other active players IF this was a full raise.
    # If undersized all-in, only players who haven't yet acted at the prior
    # current_bet level get to act (i.e., to_act minus current player).
    if raise_increment >= state.betting.min_raise:
        other_actives = [
            p for p in state.players
            if p is not None and p.id != player.id and p.status == PlayerStatus.ACTIVE
        ]
        new_to_act = tuple(p.id for p in sorted(other_actives, key=lambda x: x.seat))
        new_to_act = _rotate_to_after(new_to_act, state, player.seat)
    else:
        new_to_act = tuple(pid for pid in state.betting.to_act if pid != player.id)

    return replace(
        state,
        betting=BettingRound(
            current_bet=amount,
            min_raise=new_min_raise,
            last_raiser_id=player.id,
            to_act=new_to_act,
        ),
    )


def _rotate_to_after(player_ids: tuple[str, ...], state: GameState, after_seat: int) -> tuple[str, ...]:
    """Reorder a tuple of player IDs so it starts with the seat after `after_seat`."""
    if not player_ids:
        return player_ids
    seat_of = {p.id: p.seat for p in state.players if p is not None}
    sorted_by_seat = sorted(player_ids, key=lambda pid: seat_of[pid])
    after = [pid for pid in sorted_by_seat if seat_of[pid] > after_seat]
    before = [pid for pid in sorted_by_seat if seat_of[pid] <= after_seat]
    return tuple(after + before)


# ---------------------------------------------------------------------------
# Phase advancement
# ---------------------------------------------------------------------------

def _maybe_advance_phase(state: GameState, deck: Deck) -> GameState:
    """If betting round is complete OR only one player remains, advance phase."""
    in_hand = state.in_hand_players
    active = state.active_players

    # If only one player is left in the hand, they win immediately.
    if len(in_hand) <= 1:
        return _resolve_hand(state, deck, single_winner=True)

    # Round complete if to_act is empty.
    if state.betting.to_act:
        return state

    # Round closed. If all remaining in-hand players are all-in (or only one is
    # active), deal remaining streets and go to showdown.
    if len(active) <= 1:
        # Run out the board and go to showdown.
        state = _run_out_board(state, deck)
        return _resolve_hand(state, deck, single_winner=False)

    # Otherwise, advance to next street.
    return _advance_street(state, deck)


def _advance_street(state: GameState, deck: Deck) -> GameState:
    """Move to the next betting round: deal cards, reset street state."""
    if state.phase == HandPhase.PRE_FLOP:
        next_phase = HandPhase.FLOP
        new_cards = tuple(deck.draw(3))
    elif state.phase == HandPhase.FLOP:
        next_phase = HandPhase.TURN
        new_cards = tuple(deck.draw(1))
    elif state.phase == HandPhase.TURN:
        next_phase = HandPhase.RIVER
        new_cards = tuple(deck.draw(1))
    elif state.phase == HandPhase.RIVER:
        return _resolve_hand(state, deck, single_winner=False)
    else:
        raise RuntimeError(f"cannot advance from phase {state.phase}")

    # Reset street_committed for everyone, clear last_action.
    new_players = tuple(
        replace(p, street_committed=0, last_action=None) if p is not None else None
        for p in state.players
    )

    # First-to-act post-flop: first active player left of button.
    in_hand_seat_nums = sorted(
        p.seat for p in new_players
        if p is not None and p.status == PlayerStatus.ACTIVE
    )
    if not in_hand_seat_nums:
        # No one to act (everyone all-in). Caller _maybe_advance_phase should have caught this.
        raise RuntimeError("advance_street with no active players")

    first_seat = next(
        (s for s in in_hand_seat_nums if s > state.button),
        in_hand_seat_nums[0],
    )
    # In heads-up post-flop, SB acts first. With 3+, first active left of button.
    to_act = _action_order(in_hand_seat_nums, first_seat, new_players)

    return replace(
        state,
        phase=next_phase,
        board=state.board + new_cards,
        players=new_players,
        betting=BettingRound(
            current_bet=0,
            min_raise=state.big_blind,
            last_raiser_id=None,
            to_act=tuple(to_act),
        ),
    )


def _run_out_board(state: GameState, deck: Deck) -> GameState:
    """Deal remaining community cards (used when all remaining players are all-in)."""
    if state.phase == HandPhase.PRE_FLOP:
        state = replace(state, board=state.board + tuple(deck.draw(3)), phase=HandPhase.FLOP)
    if state.phase == HandPhase.FLOP:
        state = replace(state, board=state.board + tuple(deck.draw(1)), phase=HandPhase.TURN)
    if state.phase == HandPhase.TURN:
        state = replace(state, board=state.board + tuple(deck.draw(1)), phase=HandPhase.RIVER)
    return state


def _resolve_hand(state: GameState, deck: Deck, single_winner: bool) -> GameState:
    """Compute pots, determine winners, distribute chips. Set phase=COMPLETE."""
    in_hand = state.in_hand_players

    # Compute pots and refunds from total commitments.
    all_players = [p for p in state.players if p is not None]
    pots, refunds = compute_pots(all_players)

    # Apply refunds to stacks.
    new_players_list = list(state.players)
    for pid, refund in refunds.items():
        for i, p in enumerate(new_players_list):
            if p is not None and p.id == pid:
                new_players_list[i] = replace(p, stack=p.stack + refund)
                break

    if single_winner and len(in_hand) == 1:
        # Single winner takes all pots regardless of cards.
        winner_id = in_hand[0].id
        for pot in pots:
            for i, p in enumerate(new_players_list):
                if p is not None and p.id == winner_id:
                    new_players_list[i] = replace(p, stack=p.stack + pot.amount)
                    break
        return replace(
            state,
            phase=HandPhase.COMPLETE,
            players=tuple(new_players_list),
            pots=tuple(pots),
        )

    # Showdown: evaluate hands for in-hand players.
    strengths = {
        p.id: evaluate_hand(list(p.hole), list(state.board))
        for p in in_hand
    }

    # Award each pot to the best eligible hand(s).
    for pot in pots:
        eligible = [pid for pid in pot.eligible_players if pid in strengths]
        if not eligible:
            continue
        best = min(strengths[pid].score for pid in eligible)
        winners = [pid for pid in eligible if strengths[pid].score == best]
        # Split with remainder going to first seat after button (left of dealer).
        share, remainder = divmod(pot.amount, len(winners))
        # Order winners by seat distance from button for remainder distribution.
        winners_by_seat = sorted(
            winners,
            key=lambda pid: _seat_distance_from_button(state, pid),
        )
        for i, pid in enumerate(winners_by_seat):
            extra = 1 if i < remainder else 0
            for j, p in enumerate(new_players_list):
                if p is not None and p.id == pid:
                    new_players_list[j] = replace(p, stack=p.stack + share + extra)
                    break

    return replace(
        state,
        phase=HandPhase.COMPLETE,
        players=tuple(new_players_list),
        pots=tuple(pots),
    )


def _seat_distance_from_button(state: GameState, player_id: str) -> int:
    seat_of = next(p.seat for p in state.players if p is not None and p.id == player_id)
    n = len(state.players)
    return (seat_of - state.button - 1) % n
