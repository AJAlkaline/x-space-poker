"""Table manager: owns the asyncio task that runs each active table.

One async task per active table. The task owns the GameState and is the single
writer for that table. All player actions arrive via an asyncio.Queue and are
processed serially. State changes broadcast to subscribed WebSocket clients.

This is a sketch — the full implementation needs:
- Action timer with auto-fold/check
- Disconnect grace period
- Persistence after every street (flush hand action log)
- Table code generation with collision retry
- Lifecycle: create, join seat, leave seat, sit out, close

The shape below shows where each concern lives.
"""
from __future__ import annotations

import asyncio
import contextlib
import secrets
from dataclasses import dataclass, field

from app.engine import (
    Action,
    Deck,
    GameState,
    HandPhase,
)
from app.engine.table import SeatConfig, apply_action, deal_hand
from app.engine.table import Table as EngineTable

# Avoid easily-confused chars (no 0/O, 1/I/L)
_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


def generate_table_code(length: int = 6) -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(length))


@dataclass
class TableRuntime:
    """Per-table runtime state, held in memory while the table is active."""
    table_id: str
    code: str
    config: EngineTable
    seats: dict[int, SeatConfig] = field(default_factory=dict)
    button_seat: int = 0
    current_state: GameState | None = None
    current_deck: Deck | None = None
    action_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    closed: asyncio.Event = field(default_factory=asyncio.Event)


class TableManager:
    def __init__(self) -> None:
        self._tables: dict[str, TableRuntime] = {}
        self._codes: dict[str, str] = {}  # code -> table_id
        self._tasks: dict[str, asyncio.Task] = {}

    def get_by_code(self, code: str) -> TableRuntime | None:
        table_id = self._codes.get(code.upper())
        return self._tables.get(table_id) if table_id else None

    async def create_table(
        self,
        table_id: str,
        small_blind: int,
        big_blind: int,
        max_seats: int = 9,
    ) -> TableRuntime:
        # Generate a unique code (retry on collision).
        for _ in range(10):
            code = generate_table_code()
            if code not in self._codes:
                break
        else:
            raise RuntimeError("could not generate unique table code")

        rt = TableRuntime(
            table_id=table_id,
            code=code,
            config=EngineTable(
                id=table_id, small_blind=small_blind,
                big_blind=big_blind, max_seats=max_seats,
            ),
        )
        self._tables[table_id] = rt
        self._codes[code] = table_id
        self._tasks[table_id] = asyncio.create_task(self._run_table(rt))
        return rt

    async def submit_action(self, table_id: str, action: Action) -> None:
        rt = self._tables.get(table_id)
        if rt is None:
            raise KeyError(f"unknown table {table_id}")
        await rt.action_queue.put(action)

    async def subscribe(self, table_id: str) -> asyncio.Queue:
        rt = self._tables[table_id]
        q: asyncio.Queue = asyncio.Queue(maxsize=64)
        rt.subscribers.add(q)
        return q

    def unsubscribe(self, table_id: str, q: asyncio.Queue) -> None:
        rt = self._tables.get(table_id)
        if rt is not None:
            rt.subscribers.discard(q)

    async def _broadcast(self, rt: TableRuntime, message: dict) -> None:
        """Push a message to every subscriber. Drops on full queues (slow client)."""
        for q in list(rt.subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(message)

    async def _run_table(self, rt: TableRuntime) -> None:
        """Main loop for a table. Runs hand after hand until closed."""
        try:
            while not rt.closed.is_set():
                # Wait until at least 2 players are seated with chips.
                eligible = [s for s in rt.seats.values() if s.stack > 0 and not s.sitting_out]
                if len(eligible) < 2:
                    await asyncio.sleep(1)
                    continue

                # Deal a new hand.
                rt.current_deck = Deck.random()
                rt.current_state = deal_hand(
                    rt.config, list(rt.seats.values()),
                    button_seat=rt.button_seat, deck=rt.current_deck,
                )
                await self._broadcast(rt, {"type": "hand_started", "state": _public_view(rt.current_state)})

                # Run the hand: pop actions until COMPLETE.
                while rt.current_state.phase != HandPhase.COMPLETE:
                    try:
                        # TODO: action timer / auto-fold goes here using asyncio.wait_for.
                        action = await rt.action_queue.get()
                    except asyncio.CancelledError:
                        return

                    try:
                        rt.current_state = apply_action(
                            rt.current_state, action, rt.current_deck,
                        )
                    except ValueError as e:
                        # Illegal action — notify just the offender (TODO).
                        await self._broadcast(rt, {"type": "illegal_action", "error": str(e)})
                        continue

                    await self._broadcast(rt, {
                        "type": "state_update",
                        "state": _public_view(rt.current_state),
                    })

                # Hand complete: update seats from final stacks, advance button.
                for p in rt.current_state.players:
                    if p is not None:
                        rt.seats[p.seat] = SeatConfig(
                            user_id=p.id, seat=p.seat, stack=p.stack,
                        )
                rt.button_seat = _next_button(rt)

                # Brief pause between hands.
                await asyncio.sleep(2)
        except Exception:
            # TODO: structured logging
            raise

    async def close_table(self, table_id: str) -> None:
        rt = self._tables.get(table_id)
        if rt is None:
            return
        rt.closed.set()
        task = self._tasks.pop(table_id, None)
        if task:
            task.cancel()
        del self._tables[table_id]
        del self._codes[rt.code]


def _next_button(rt: TableRuntime) -> int:
    """Advance button to the next seat with chips."""
    occupied = sorted(s for s, cfg in rt.seats.items() if cfg.stack > 0)
    if not occupied:
        return rt.button_seat
    after = [s for s in occupied if s > rt.button_seat]
    return after[0] if after else occupied[0]


def _public_view(state: GameState) -> dict:
    """Strip hole cards from the state for broadcast.

    NOTE: per-player private view (with own hole cards) needs to be sent on
    each player's private channel separately; this helper only produces the
    public view. See api/ws.py for the routing.
    """
    return {
        "hand_id": state.hand_id,
        "phase": state.phase.value,
        "board": [str(c) for c in state.board],
        "pots": [{"amount": p.amount, "eligible": list(p.eligible_players)} for p in state.pots],
        "current_bet": state.betting.current_bet,
        "min_raise": state.betting.min_raise,
        "to_act": list(state.betting.to_act),
        "button": state.button,
        "players": [
            None if p is None else {
                "id": p.id, "seat": p.seat, "stack": p.stack,
                "status": p.status.value,
                "street_committed": p.street_committed,
                "last_action": p.last_action.value if p.last_action else None,
                # hole cards intentionally omitted
            }
            for p in state.players
        ],
    }
