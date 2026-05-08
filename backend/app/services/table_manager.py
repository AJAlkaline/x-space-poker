"""Table manager: owns the asyncio task that runs each active table.

One async task per active table. The task owns the GameState and is the single
writer for that table. Player actions arrive via an asyncio.Queue and are
processed serially.

Outbound traffic goes through two channels:

- **Public broadcast** to every subscriber on the table. Strips hole cards.
- **Private send** to a specific player. Includes that player's hole cards
  and (when it's their turn) the legal-actions list and a turn prompt.

Subscribers are keyed by player_id. Each player gets at most one active
WebSocket per process for a given table; reconnection replaces the queue.

Path A scope: in-memory only, no persistence, no action timer, no rake,
no disconnect grace.
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
    PlayerStatus,
)
from app.engine.table import (
    SeatConfig,
    apply_action,
    deal_hand,
    legal_actions,
)
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
    button_seat: int = -1
    current_state: GameState | None = None
    current_deck: Deck | None = None
    action_queue: asyncio.Queue[Action] = field(default_factory=asyncio.Queue)
    subscribers: dict[str, asyncio.Queue[dict]] = field(default_factory=dict)
    closed: asyncio.Event = field(default_factory=asyncio.Event)
    seats_changed: asyncio.Event = field(default_factory=asyncio.Event)


class TableManager:
    def __init__(self) -> None:
        self._tables: dict[str, TableRuntime] = {}
        self._codes: dict[str, str] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def get_by_code(self, code: str) -> TableRuntime | None:
        table_id = self._codes.get(code.upper())
        return self._tables.get(table_id) if table_id else None

    def get(self, table_id: str) -> TableRuntime | None:
        return self._tables.get(table_id)

    async def create_table(
        self,
        table_id: str,
        small_blind: int,
        big_blind: int,
        max_seats: int = 9,
    ) -> TableRuntime:
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
                id=table_id,
                small_blind=small_blind,
                big_blind=big_blind,
                max_seats=max_seats,
            ),
        )
        self._tables[table_id] = rt
        self._codes[code] = table_id
        self._tasks[table_id] = asyncio.create_task(
            self._run_table(rt), name=f"table-{code}"
        )
        return rt

    async def close_table(self, table_id: str) -> None:
        rt = self._tables.get(table_id)
        if rt is None:
            return
        rt.closed.set()
        rt.seats_changed.set()
        task = self._tasks.pop(table_id, None)
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._codes.pop(rt.code, None)
        self._tables.pop(table_id, None)

    def seat_player(
        self, table_id: str, player_id: str, seat_number: int, buy_in: int
    ) -> None:
        rt = self._tables[table_id]
        if seat_number in rt.seats:
            raise ValueError(f"seat {seat_number} taken")
        if seat_number < 0 or seat_number >= rt.config.max_seats:
            raise ValueError(f"seat {seat_number} out of range")
        if any(s.user_id == player_id for s in rt.seats.values()):
            raise ValueError("player already seated")
        rt.seats[seat_number] = SeatConfig(
            user_id=player_id, seat=seat_number, stack=buy_in
        )
        rt.seats_changed.set()

    def unseat_player(self, table_id: str, player_id: str) -> int:
        rt = self._tables[table_id]
        seat_num = next(
            (n for n, s in rt.seats.items() if s.user_id == player_id), None
        )
        if seat_num is None:
            return 0
        if rt.current_state and rt.current_state.phase != HandPhase.COMPLETE:
            p = rt.current_state.player_by_id(player_id)
            if p is not None and p.status in (PlayerStatus.ACTIVE, PlayerStatus.ALL_IN):
                raise ValueError("cannot leave mid-hand (Path A limitation)")
        seat = rt.seats.pop(seat_num)
        rt.seats_changed.set()
        return seat.stack

    def subscribe(self, table_id: str, player_id: str) -> asyncio.Queue[dict]:
        rt = self._tables[table_id]
        q: asyncio.Queue[dict] = asyncio.Queue(maxsize=64)
        rt.subscribers[player_id] = q
        if rt.current_state is not None:
            # If a hand is in progress, send hand_started so the client treats it
            # as a fresh hand from their perspective. (For a complete hand, send
            # hand_complete with reveals.)
            if rt.current_state.phase == HandPhase.COMPLETE:
                q.put_nowait({
                    "type": "hand_complete",
                    "state": _public_view(rt.current_state, reveal=True),
                })
            else:
                q.put_nowait({
                    "type": "hand_started",
                    "state": _public_view(rt.current_state),
                })
                priv = _private_view(rt.current_state, player_id)
                if priv is not None:
                    q.put_nowait({"type": "private", "state": priv})
        q.put_nowait({"type": "seats", "seats": _seats_view(rt)})
        return q

    def unsubscribe(self, table_id: str, player_id: str) -> None:
        rt = self._tables.get(table_id)
        if rt is not None:
            rt.subscribers.pop(player_id, None)

    async def submit_action(self, table_id: str, action: Action) -> None:
        rt = self._tables[table_id]
        await rt.action_queue.put(action)

    async def _broadcast(self, rt: TableRuntime, message: dict) -> None:
        for q in list(rt.subscribers.values()):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(message)

    async def _send_private(
        self, rt: TableRuntime, player_id: str, message: dict
    ) -> None:
        q = rt.subscribers.get(player_id)
        if q is None:
            return
        with contextlib.suppress(asyncio.QueueFull):
            q.put_nowait(message)

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
                    continue

                if rt.button_seat == -1:
                    rt.button_seat = min(rt.seats.keys())
                else:
                    rt.button_seat = _next_button(rt)

                rt.current_deck = Deck.random()
                rt.current_state = deal_hand(
                    rt.config,
                    list(rt.seats.values()),
                    button_seat=rt.button_seat,
                    deck=rt.current_deck,
                )

                await self._broadcast(rt, {
                    "type": "hand_started",
                    "state": _public_view(rt.current_state),
                })
                for p in rt.current_state.players:
                    if p is not None:
                        priv = _private_view(rt.current_state, p.id)
                        if priv is not None:
                            await self._send_private(
                                rt, p.id, {"type": "private", "state": priv}
                            )

                while rt.current_state.phase != HandPhase.COMPLETE:
                    try:
                        action = await rt.action_queue.get()
                    except asyncio.CancelledError:
                        return

                    p = rt.current_state.player_by_id(action.player_id)
                    if p is None:
                        continue
                    if (
                        not rt.current_state.betting.to_act
                        or rt.current_state.betting.to_act[0] != action.player_id
                    ):
                        await self._send_private(
                            rt, action.player_id,
                            {"type": "illegal_action", "error": "not your turn"},
                        )
                        continue

                    try:
                        rt.current_state = apply_action(
                            rt.current_state, action, rt.current_deck,
                        )
                    except ValueError as e:
                        await self._send_private(
                            rt, action.player_id,
                            {"type": "illegal_action", "error": str(e)},
                        )
                        continue

                    await self._broadcast(rt, {
                        "type": "state_update",
                        "state": _public_view(rt.current_state),
                    })
                    for pp in rt.current_state.players:
                        if pp is not None:
                            priv = _private_view(rt.current_state, pp.id)
                            if priv is not None:
                                await self._send_private(
                                    rt, pp.id, {"type": "private", "state": priv}
                                )

                await self._broadcast(rt, {
                    "type": "hand_complete",
                    "state": _public_view(rt.current_state, reveal=True),
                })

                for p in rt.current_state.players:
                    if p is not None:
                        if p.stack > 0:
                            rt.seats[p.seat] = SeatConfig(
                                user_id=p.id, seat=p.seat, stack=p.stack,
                            )
                        else:
                            rt.seats.pop(p.seat, None)

                await self._broadcast(rt, {"type": "seats", "seats": _seats_view(rt)})
                await asyncio.sleep(3)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            import traceback
            traceback.print_exc()
            await self._broadcast(rt, {"type": "table_error", "error": str(e)})


def _next_button(rt: TableRuntime) -> int:
    occupied = sorted(s for s, cfg in rt.seats.items() if cfg.stack > 0)
    if not occupied:
        return rt.button_seat
    after = [s for s in occupied if s > rt.button_seat]
    return after[0] if after else occupied[0]


def _public_view(state: GameState, reveal: bool = False) -> dict:
    return {
        "hand_id": state.hand_id,
        "phase": state.phase.value,
        "board": [str(c) for c in state.board],
        "pots": [
            {"amount": p.amount, "eligible": list(p.eligible_players)}
            for p in state.pots
        ],
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


def _private_view(state: GameState, player_id: str) -> dict | None:
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
    }


def _seats_view(rt: TableRuntime) -> list[dict | None]:
    return [
        (
            {
                "seat": n,
                "user_id": rt.seats[n].user_id,
                "stack": rt.seats[n].stack,
            }
            if n in rt.seats
            else None
        )
        for n in range(rt.config.max_seats)
    ]


_manager: TableManager | None = None


def get_manager() -> TableManager:
    global _manager
    if _manager is None:
        _manager = TableManager()
    return _manager
