"""Table manager: owns the asyncio task that runs each active table.

This module is the writer for table state. It produces a stream of typed
events (see events.py) that downstream consumers (WebSocket clients,
persistence, future Redis publisher) translate into whatever they need.

The loop's contract:
- Single writer for current_state. Action queue is the only inbound channel.
- Publishes events on every state-changing transition.
- Never blocks on consumers — slow consumers drop events.

Path A scope: in-memory only. No DB persistence yet (Tier 2 will wire that
up via an event subscriber). No Redis fan-out yet (also Tier 2 follow-up).
"""
from __future__ import annotations

import asyncio
import contextlib
import secrets
import time
from dataclasses import dataclass, field

from app.engine import (
    Action,
    ActionType,
    Deck,
    GameState,
    HandPhase,
    PlayerStatus,
)
from app.engine.table import (
    SeatConfig,
    apply_action,
    deal_hand,
    legal_actions,
)
from app.engine.table import Table as EngineTable
from app.services.event_bus import EventBus
from app.services.events import (
    ActionAppliedEvent,
    HandAbortedEvent,
    HandCompletedEvent,
    HandStartedEvent,
    IllegalActionEvent,
    PrivateStateEvent,
    SeatsChangedEvent,
    TableErrorEvent,
    ViewerCountChangedEvent,
)

# ---------------------------------------------------------------------------
# Configuration constants (monkey-patchable in tests)
# ---------------------------------------------------------------------------

# Action timer config — table-level for now, settings-level later.
ACTION_TIMER_SECONDS = 25.0
TIMEBANK_MAX = 60.0
TIMEBANK_REFILL_PER_HAND = 10.0
DISCONNECT_GRACE_SECONDS = 30.0

# Avoid easily-confused chars (no 0/O, 1/I/L)
_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


def generate_table_code(length: int = 6) -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(length))


# ---------------------------------------------------------------------------
# Per-player runtime state
# ---------------------------------------------------------------------------

@dataclass
class SeatRuntimeState:
    """Per-player runtime state that lives outside the engine.

    The engine deals with chips and cards; this dataclass holds everything
    else that's per-seat — time bank, disconnect timestamp, sit-out flag.
    """
    time_bank_seconds: float = 30.0
    disconnected_at: float | None = None
    sitting_out: bool = False


# ---------------------------------------------------------------------------
# Per-table runtime
# ---------------------------------------------------------------------------

@dataclass
class TableRuntime:
    """Per-table runtime state, held in memory while the table is active."""

    table_id: str
    code: str
    config: EngineTable
    seats: dict[int, SeatConfig] = field(default_factory=dict)
    seat_state: dict[str, SeatRuntimeState] = field(default_factory=dict)
    button_seat: int = -1
    current_state: GameState | None = None
    current_deck: Deck | None = None
    hand_number: int = 0
    hand_action_sequence: int = 0
    action_queue: asyncio.Queue[Action] = field(default_factory=asyncio.Queue)
    bus: EventBus = field(default_factory=EventBus)
    closed: asyncio.Event = field(default_factory=asyncio.Event)
    seats_changed: asyncio.Event = field(default_factory=asyncio.Event)


# ---------------------------------------------------------------------------
# TableManager
# ---------------------------------------------------------------------------

class TableManager:
    def __init__(self) -> None:
        self._tables: dict[str, TableRuntime] = {}
        self._codes: dict[str, str] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    # ---- Lookup ----

    def get_by_code(self, code: str) -> TableRuntime | None:
        table_id = self._codes.get(code.upper())
        return self._tables.get(table_id) if table_id else None

    def get(self, table_id: str) -> TableRuntime | None:
        return self._tables.get(table_id)

    def all_tables(self) -> list[TableRuntime]:
        return list(self._tables.values())

    # ---- Lifecycle ----

    async def create_table(
        self, table_id: str, small_blind: int, big_blind: int,
        max_seats: int = 9, code: str | None = None,
    ) -> TableRuntime:
        if code is None:
            for _ in range(10):
                code = generate_table_code()
                if code not in self._codes:
                    break
            else:
                raise RuntimeError("could not generate unique table code")
        elif code in self._codes:
            raise RuntimeError(f"code {code} already in use")

        rt = TableRuntime(
            table_id=table_id, code=code,
            config=EngineTable(
                id=table_id, small_blind=small_blind,
                big_blind=big_blind, max_seats=max_seats,
            ),
        )
        self._tables[table_id] = rt
        self._codes[code] = table_id
        self._tasks[table_id] = asyncio.create_task(
            self._run_table(rt), name=f"table-{code}",
        )
        # If persistence is enabled, also spawn a consumer that writes hand
        # history + ledger entries from the event stream.
        from app.core.config import get_settings
        if get_settings().persistence_enabled:
            from app.services.persistence_consumer import run_persistence_consumer
            self._tasks[f"{table_id}:persistence"] = asyncio.create_task(
                run_persistence_consumer(
                    rt.bus, f"persistence:{table_id}", rt.closed,
                ),
                name=f"persistence-{code}",
            )
        return rt

    async def close_table(self, table_id: str) -> None:
        rt = self._tables.get(table_id)
        if rt is None:
            return
        rt.closed.set()
        rt.seats_changed.set()
        for key in (table_id, f"{table_id}:persistence"):
            task = self._tasks.pop(key, None)
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._codes.pop(rt.code, None)
        self._tables.pop(table_id, None)

    # ---- Seats ----

    def seat_player(
        self, table_id: str, player_id: str, seat_number: int, buy_in: int,
    ) -> None:
        rt = self._tables[table_id]
        if seat_number in rt.seats:
            raise ValueError(f"seat {seat_number} taken")
        if seat_number < 0 or seat_number >= rt.config.max_seats:
            raise ValueError(f"seat {seat_number} out of range")
        if any(s.user_id == player_id for s in rt.seats.values()):
            raise ValueError("player already seated")
        rt.seats[seat_number] = SeatConfig(
            user_id=player_id, seat=seat_number, stack=buy_in,
        )
        rt.seat_state[player_id] = SeatRuntimeState(time_bank_seconds=TIMEBANK_MAX)
        rt.seats_changed.set()

    def unseat_player(self, table_id: str, player_id: str) -> int:
        rt = self._tables[table_id]
        seat_num = next(
            (n for n, s in rt.seats.items() if s.user_id == player_id), None,
        )
        if seat_num is None:
            return 0
        if rt.current_state and rt.current_state.phase != HandPhase.COMPLETE:
            p = rt.current_state.player_by_id(player_id)
            if p is not None and p.status in (PlayerStatus.ACTIVE, PlayerStatus.ALL_IN):
                raise ValueError("cannot leave mid-hand (Path A limitation)")
        seat = rt.seats.pop(seat_num)
        rt.seat_state.pop(player_id, None)
        rt.seats_changed.set()
        return seat.stack

    # ---- Inbound actions ----

    async def submit_action(self, table_id: str, action: Action) -> None:
        rt = self._tables[table_id]
        await rt.action_queue.put(action)

    # ---- Subscriptions (delegate to the bus) ----

    def subscribe_player(
        self, table_id: str, player_id: str,
    ) -> tuple[asyncio.Queue, asyncio.Queue]:
        """A player gets the public stream + their own private stream.

        On reconnect, clears any disconnect timestamp and re-emits a viewer count
        change so other clients see the player came back.

        Returns (public_queue, private_queue). Caller must call
        unsubscribe_player(table_id, player_id) on disconnect.
        """
        rt = self._tables[table_id]
        # Reconnect handling
        state = rt.seat_state.get(player_id)
        was_disconnected = state is not None and state.disconnected_at is not None
        if state is not None:
            state.disconnected_at = None

        public_q = rt.bus.subscribe_public(player_id)
        private_q = rt.bus.subscribe_private(player_id)

        # Send the current snapshot to the new subscriber so they have something
        # to render before the next event arrives.
        self._send_initial_snapshot(rt, player_id, public_q, private_q)

        if was_disconnected:
            # Notify everyone that the seat is no longer disconnected.
            self._publish_seats(rt)
        # Always update viewer count on subscribe.
        self._publish_viewer_count(rt)
        return public_q, private_q

    def unsubscribe_player(self, table_id: str, player_id: str) -> None:
        rt = self._tables.get(table_id)
        if rt is None:
            return
        rt.bus.unsubscribe_public(player_id)
        rt.bus.unsubscribe_private(player_id)
        # If they're seated, mark disconnect timestamp.
        state = rt.seat_state.get(player_id)
        if state is not None and state.disconnected_at is None:
            state.disconnected_at = time.monotonic()
            self._publish_seats(rt)
        self._publish_viewer_count(rt)

    def subscribe_spectator(
        self, table_id: str, viewer_id: str,
    ) -> asyncio.Queue:
        """A spectator gets the public stream only. Spectators have no private
        channel by construction — hidden information cannot leak to them
        because the path that would carry it doesn't exist."""
        rt = self._tables[table_id]
        public_q = rt.bus.subscribe_public(viewer_id)
        # Spectators get the same initial public snapshot players do, minus the
        # private stream (and minus any hole-card reveals on a complete hand
        # they joined late — same as players who join late, actually).
        self._send_initial_public_snapshot(rt, public_q)
        self._publish_viewer_count(rt)
        return public_q

    def unsubscribe_spectator(self, table_id: str, viewer_id: str) -> None:
        rt = self._tables.get(table_id)
        if rt is None:
            return
        rt.bus.unsubscribe_public(viewer_id)
        self._publish_viewer_count(rt)

    # ---- Initial snapshots ----

    def _send_initial_snapshot(
        self, rt: TableRuntime, player_id: str,
        public_q: asyncio.Queue, private_q: asyncio.Queue,
    ) -> None:
        """Push current state into a newly-subscribed player's queues."""
        if rt.current_state is not None:
            if rt.current_state.phase == HandPhase.COMPLETE:
                event = HandCompletedEvent(
                    table_id=rt.table_id,
                    hand_id=rt.current_state.hand_id,
                    deck_reveal="",  # filled in at hand-end normally
                    public_state=_public_view(rt.current_state, reveal=True),
                    pot_distributions=[],
                )
                with contextlib.suppress(asyncio.QueueFull):
                    public_q.put_nowait(event)
            else:
                started = HandStartedEvent(
                    table_id=rt.table_id,
                    hand_id=rt.current_state.hand_id,
                    hand_number=rt.hand_number,
                    deck_commit=rt.current_state.deck_commit,
                    public_state=_public_view(rt.current_state),
                )
                with contextlib.suppress(asyncio.QueueFull):
                    public_q.put_nowait(started)
                priv_state = _private_view(rt.current_state, player_id)
                if priv_state is not None:
                    private_event = PrivateStateEvent(
                        table_id=rt.table_id, player_id=player_id, state=priv_state,
                    )
                    with contextlib.suppress(asyncio.QueueFull):
                        private_q.put_nowait(private_event)

        seats_event = SeatsChangedEvent(table_id=rt.table_id, seats=_seats_view(rt))
        with contextlib.suppress(asyncio.QueueFull):
            public_q.put_nowait(seats_event)

    def _send_initial_public_snapshot(
        self, rt: TableRuntime, public_q: asyncio.Queue,
    ) -> None:
        """Spectator version: public state only, no private events."""
        if rt.current_state is not None:
            if rt.current_state.phase == HandPhase.COMPLETE:
                event = HandCompletedEvent(
                    table_id=rt.table_id,
                    hand_id=rt.current_state.hand_id,
                    deck_reveal="",
                    public_state=_public_view(rt.current_state, reveal=True),
                    pot_distributions=[],
                )
            else:
                event = HandStartedEvent(
                    table_id=rt.table_id,
                    hand_id=rt.current_state.hand_id,
                    hand_number=rt.hand_number,
                    deck_commit=rt.current_state.deck_commit,
                    public_state=_public_view(rt.current_state),
                )
            with contextlib.suppress(asyncio.QueueFull):
                public_q.put_nowait(event)
        seats_event = SeatsChangedEvent(table_id=rt.table_id, seats=_seats_view(rt))
        with contextlib.suppress(asyncio.QueueFull):
            public_q.put_nowait(seats_event)

    # ---- Event publishing helpers ----

    def _publish_seats(self, rt: TableRuntime) -> None:
        rt.bus.publish_public(SeatsChangedEvent(
            table_id=rt.table_id, seats=_seats_view(rt),
        ))

    def _publish_viewer_count(self, rt: TableRuntime) -> None:
        rt.bus.publish_public(ViewerCountChangedEvent(
            table_id=rt.table_id, count=rt.bus.public_subscriber_count(),
        ))

    def _publish_private_state(self, rt: TableRuntime, player_id: str) -> None:
        if rt.current_state is None:
            return
        priv = _private_view(rt.current_state, player_id)
        if priv is not None:
            rt.bus.publish_private(player_id, PrivateStateEvent(
                table_id=rt.table_id, player_id=player_id, state=priv,
            ))

    def _publish_private_state_with_deadlines(
        self, rt: TableRuntime, player_id: str,
    ) -> None:
        if rt.current_state is None:
            return
        state = rt.seat_state.get(player_id)
        bank_remaining = state.time_bank_seconds if state else 0.0
        now_ms = int(time.time() * 1000)
        base_deadline = now_ms + int(ACTION_TIMER_SECONDS * 1000)
        bank_deadline = base_deadline + int(bank_remaining * 1000)
        priv = _private_view(
            rt.current_state, player_id,
            base_deadline_unix_ms=base_deadline,
            bank_deadline_unix_ms=bank_deadline,
            timebank_remaining_ms=int(bank_remaining * 1000),
            action_timer_seconds=ACTION_TIMER_SECONDS,
        )
        if priv is not None:
            rt.bus.publish_private(player_id, PrivateStateEvent(
                table_id=rt.table_id, player_id=player_id, state=priv,
            ))

    # ------------------------------------------------------------------
    # The table loop
    # ------------------------------------------------------------------

    async def _run_table(self, rt: TableRuntime) -> None:
        try:
            while not rt.closed.is_set():
                eligible = [
                    s for s in rt.seats.values()
                    if s.stack > 0 and not s.sitting_out
                ]
                if len(eligible) < 2:
                    rt.seats_changed.clear()
                    await rt.seats_changed.wait()
                    self._publish_seats(rt)
                    continue

                if rt.button_seat == -1:
                    rt.button_seat = min(rt.seats.keys())
                else:
                    rt.button_seat = _next_button(rt)

                self._publish_seats(rt)

                rt.current_deck = Deck.random()
                rt.current_state = deal_hand(
                    rt.config, list(rt.seats.values()),
                    button_seat=rt.button_seat, deck=rt.current_deck,
                )
                rt.hand_number += 1
                rt.hand_action_sequence = 0

                rt.bus.publish_public(HandStartedEvent(
                    table_id=rt.table_id,
                    hand_id=rt.current_state.hand_id,
                    hand_number=rt.hand_number,
                    deck_commit=rt.current_state.deck_commit,
                    public_state=_public_view(rt.current_state),
                ))

                # Send private views to all in-hand players except the to-act
                # player — they get a deadline-bearing view from the action loop.
                first_to_act = (
                    rt.current_state.betting.to_act[0]
                    if rt.current_state.betting.to_act else None
                )
                for p in rt.current_state.players:
                    if p is None or p.id == first_to_act:
                        continue
                    self._publish_private_state(rt, p.id)

                # ---- Action loop ----
                to_act_deadline_started_at: float | None = None
                to_act_player_for_deadline: str | None = None

                while rt.current_state.phase != HandPhase.COMPLETE:
                    current_to_act = (
                        rt.current_state.betting.to_act[0]
                        if rt.current_state.betting.to_act else None
                    )
                    if current_to_act != to_act_player_for_deadline:
                        to_act_player_for_deadline = current_to_act
                        to_act_deadline_started_at = time.monotonic()
                        if current_to_act is not None:
                            self._publish_private_state_with_deadlines(
                                rt, current_to_act,
                            )

                    if current_to_act is None:
                        timeout = None
                    else:
                        ss = rt.seat_state.get(current_to_act)
                        bank_remaining = ss.time_bank_seconds if ss else 0.0
                        elapsed = time.monotonic() - (to_act_deadline_started_at or 0)
                        total_budget = ACTION_TIMER_SECONDS + bank_remaining
                        timeout = max(0.05, total_budget - elapsed)

                    is_auto = False
                    try:
                        if timeout is None:
                            action = await rt.action_queue.get()
                        else:
                            action = await asyncio.wait_for(
                                rt.action_queue.get(), timeout=timeout,
                            )
                    except TimeoutError:
                        if current_to_act is None:
                            continue
                        legals = legal_actions(rt.current_state, current_to_act)
                        legal_types = {la.action_type for la in legals}
                        if ActionType.CHECK in legal_types:
                            auto = Action(
                                player_id=current_to_act, action_type=ActionType.CHECK,
                            )
                        else:
                            auto = Action(
                                player_id=current_to_act, action_type=ActionType.FOLD,
                            )
                        elapsed = time.monotonic() - (to_act_deadline_started_at or 0)
                        bank_used = max(0.0, elapsed - ACTION_TIMER_SECONDS)
                        ss = rt.seat_state.get(current_to_act)
                        if ss is not None:
                            ss.time_bank_seconds = max(
                                0.0, ss.time_bank_seconds - bank_used,
                            )
                        action = auto
                        is_auto = True
                    except asyncio.CancelledError:
                        # Table is closing mid-hand: refund all committed chips
                        # via a HandAborted event before exiting.
                        await self._abort_current_hand(rt)
                        return

                    p = rt.current_state.player_by_id(action.player_id)
                    if p is None:
                        continue
                    if (
                        not rt.current_state.betting.to_act
                        or rt.current_state.betting.to_act[0] != action.player_id
                    ):
                        rt.bus.publish_private(action.player_id, IllegalActionEvent(
                            table_id=rt.table_id, player_id=action.player_id,
                            error="not your turn",
                        ))
                        continue

                    # Real action arrived from to-act player. Deduct timebank usage.
                    if (
                        not is_auto
                        and to_act_player_for_deadline == action.player_id
                        and to_act_deadline_started_at is not None
                    ):
                        elapsed = time.monotonic() - to_act_deadline_started_at
                        bank_used = max(0.0, elapsed - ACTION_TIMER_SECONDS)
                        ss = rt.seat_state.get(action.player_id)
                        if ss is not None:
                            ss.time_bank_seconds = max(
                                0.0, ss.time_bank_seconds - bank_used,
                            )

                    try:
                        rt.current_state = apply_action(
                            rt.current_state, action, rt.current_deck,
                        )
                    except ValueError as e:
                        rt.bus.publish_private(action.player_id, IllegalActionEvent(
                            table_id=rt.table_id, player_id=action.player_id,
                            error=str(e),
                        ))
                        continue

                    rt.hand_action_sequence += 1
                    rt.bus.publish_public(ActionAppliedEvent(
                        table_id=rt.table_id,
                        hand_id=rt.current_state.hand_id,
                        sequence=rt.hand_action_sequence,
                        player_id=action.player_id,
                        action_type=action.action_type.value,
                        amount=action.amount,
                        auto=is_auto,
                        public_state=_public_view(rt.current_state),
                    ))

                    new_to_act = (
                        rt.current_state.betting.to_act[0]
                        if rt.current_state.betting.to_act else None
                    )
                    for pp in rt.current_state.players:
                        if pp is None or pp.id == new_to_act:
                            continue
                        self._publish_private_state(rt, pp.id)

                # ---- Hand resolution ----
                rt.bus.publish_public(HandCompletedEvent(
                    table_id=rt.table_id,
                    hand_id=rt.current_state.hand_id,
                    deck_reveal=rt.current_deck.reveal() if rt.current_deck else "",
                    public_state=_public_view(rt.current_state, reveal=True),
                    pot_distributions=[],  # TODO populate from final state
                ))

                # Update seats from final stacks + bust handling.
                for p in rt.current_state.players:
                    if p is None:
                        continue
                    if p.stack > 0:
                        rt.seats[p.seat] = SeatConfig(
                            user_id=p.id, seat=p.seat, stack=p.stack,
                        )
                    else:
                        rt.seats.pop(p.seat, None)
                        rt.seat_state.pop(p.id, None)

                # Refill timebank, sit out long-disconnected players.
                now = time.monotonic()
                for pid, ss in list(rt.seat_state.items()):
                    ss.time_bank_seconds = min(
                        TIMEBANK_MAX, ss.time_bank_seconds + TIMEBANK_REFILL_PER_HAND,
                    )
                    if (
                        ss.disconnected_at is not None
                        and now - ss.disconnected_at > DISCONNECT_GRACE_SECONDS
                    ):
                        ss.sitting_out = True
                        for seat_num, seat_cfg in list(rt.seats.items()):
                            if seat_cfg.user_id == pid:
                                rt.seats[seat_num] = SeatConfig(
                                    user_id=pid, seat=seat_cfg.seat,
                                    stack=seat_cfg.stack, sitting_out=True,
                                )

                self._publish_seats(rt)
                await asyncio.sleep(3)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            import traceback
            traceback.print_exc()
            rt.bus.publish_public(TableErrorEvent(
                table_id=rt.table_id, error=str(e),
            ))

    async def _abort_current_hand(self, rt: TableRuntime) -> None:
        """Refund chips committed in the current hand and emit a HandAborted
        event. Used on cancellation/shutdown."""
        if rt.current_state is None or rt.current_state.phase == HandPhase.COMPLETE:
            return
        refunds: dict[str, int] = {}
        for p in rt.current_state.players:
            if p is None:
                continue
            committed = p.total_committed
            if committed > 0:
                refunds[p.id] = committed
                if p.seat in rt.seats:
                    seat = rt.seats[p.seat]
                    rt.seats[p.seat] = SeatConfig(
                        user_id=seat.user_id, seat=seat.seat,
                        stack=seat.stack + committed,
                        sitting_out=seat.sitting_out,
                    )
        rt.bus.publish_public(HandAbortedEvent(
            table_id=rt.table_id,
            hand_id=rt.current_state.hand_id,
            refunds=refunds,
        ))


# ---------------------------------------------------------------------------
# View helpers — convert engine state to wire formats
# ---------------------------------------------------------------------------

def _next_button(rt: TableRuntime) -> int:
    occupied = sorted(s for s, cfg in rt.seats.items() if cfg.stack > 0)
    if not occupied:
        return rt.button_seat
    after = [s for s in occupied if s > rt.button_seat]
    return after[0] if after else occupied[0]


def _public_view(state: GameState, reveal: bool = False) -> dict:
    pot_total = sum(p.total_committed for p in state.players if p is not None)
    return {
        "hand_id": state.hand_id,
        "phase": state.phase.value,
        "board": [str(c) for c in state.board],
        "pots": [
            {"amount": p.amount, "eligible": list(p.eligible_players)}
            for p in state.pots
        ],
        "pot_total": pot_total,
        "current_bet": state.betting.current_bet,
        "min_raise": state.betting.min_raise,
        "to_act": list(state.betting.to_act),
        "button": state.button,
        "small_blind": state.small_blind,
        "big_blind": state.big_blind,
        "players": [
            None if p is None else {
                "id": p.id,
                "seat": p.seat,
                "stack": p.stack,
                "status": p.status.value,
                "street_committed": p.street_committed,
                "total_committed": p.total_committed,
                "last_action": p.last_action.value if p.last_action else None,
                "hole": (
                    [str(c) for c in p.hole]
                    if reveal and p.status != PlayerStatus.FOLDED and p.hole
                    else None
                ),
            }
            for p in state.players
        ],
    }


def _private_view(
    state: GameState, player_id: str, *,
    base_deadline_unix_ms: int | None = None,
    bank_deadline_unix_ms: int | None = None,
    timebank_remaining_ms: int | None = None,
    action_timer_seconds: float | None = None,
) -> dict | None:
    p = state.player_by_id(player_id)
    if p is None:
        return None
    legals = legal_actions(state, player_id)
    return {
        "hole": [str(c) for c in p.hole] if p.hole else None,
        "your_turn": (
            bool(state.betting.to_act) and state.betting.to_act[0] == player_id
        ),
        "legal_actions": [
            {
                "action_type": la.action_type.value,
                "min_amount": la.min_amount,
                "max_amount": la.max_amount,
            }
            for la in legals
        ],
        "base_deadline_unix_ms": base_deadline_unix_ms,
        "bank_deadline_unix_ms": bank_deadline_unix_ms,
        "timebank_remaining_ms": timebank_remaining_ms,
        "action_timer_seconds": action_timer_seconds,
    }


def _seats_view(rt: TableRuntime) -> list[dict | None]:
    out: list[dict | None] = []
    for n in range(rt.config.max_seats):
        seat = rt.seats.get(n)
        if seat is None:
            out.append(None)
            continue
        runtime = rt.seat_state.get(seat.user_id)
        out.append({
            "seat": n,
            "user_id": seat.user_id,
            "stack": seat.stack,
            "sitting_out": seat.sitting_out,
            "disconnected": (
                runtime is not None and runtime.disconnected_at is not None
            ),
        })
    return out


# ---------------------------------------------------------------------------
# Module singleton
# ---------------------------------------------------------------------------

_manager: TableManager | None = None


def get_manager() -> TableManager:
    global _manager
    if _manager is None:
        _manager = TableManager()
    return _manager
