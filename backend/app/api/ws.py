"""WebSocket: clients subscribe to a table to receive live state updates.

Path A protocol:

Client -> Server
  { "type": "action", "action": "fold|check|call|bet|raise", "amount": int }

Server -> Client
  { "type": "hand_started",  "state": <public_view> }
  { "type": "state_update",  "state": <public_view> }
  { "type": "hand_complete", "state": <public_view with hole reveal> }
  { "type": "private",       "state": <private_view> }
  { "type": "seats",         "seats": <seats_view> }
  { "type": "illegal_action","error":  <str> }
  { "type": "table_error",   "error":  <str> }

Authentication is via `?as=<handle>` query string (Path A only). The same
handle must already be seated at the table — spectators aren't supported
in Path A.
"""
from __future__ import annotations

import asyncio
import contextlib
import re

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from app.engine import Action, ActionType
from app.services.table_manager import get_manager

router = APIRouter()

_HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{2,20}$")


@router.websocket("/ws/tables/{code}")
async def table_socket(
    websocket: WebSocket,
    code: str,
    as_: str = Query(..., alias="as"),
) -> None:
    if not _HANDLE_RE.match(as_):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="bad handle")
        return

    mgr = get_manager()
    rt = mgr.get_by_code(code)
    if rt is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="table not found")
        return

    # Spectators allowed: a connection without a seat just observes. They can
    # POST /tables/join after connecting and the next state_update will reflect
    # their seat. Hole cards still only flow to seated players.

    await websocket.accept()
    queue = mgr.subscribe(rt.table_id, as_)

    async def pump_outbound() -> None:
        """Drain server-side queue into the WebSocket."""
        while True:
            msg = await queue.get()
            await websocket.send_json(msg)

    async def pump_inbound() -> None:
        """Read client messages and feed them into the table's action queue."""
        while True:
            msg = await websocket.receive_json()
            mtype = msg.get("type")
            if mtype == "action":
                action_str = msg.get("action")
                amount = int(msg.get("amount", 0))
                try:
                    action_type = ActionType(action_str)
                except ValueError:
                    await websocket.send_json(
                        {"type": "illegal_action", "error": f"unknown action {action_str!r}"}
                    )
                    continue
                await mgr.submit_action(
                    rt.table_id,
                    Action(player_id=as_, action_type=action_type, amount=amount),
                )
            # Other inbound message types (sit_out, leave, ...) handled later.

    out_task = asyncio.create_task(pump_outbound())
    in_task = asyncio.create_task(pump_inbound())

    try:
        # Wait for either side to finish (disconnect or error).
        done, pending = await asyncio.wait(
            {out_task, in_task}, return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
        for t in done:
            with contextlib.suppress(WebSocketDisconnect, asyncio.CancelledError):
                t.result()
    except WebSocketDisconnect:
        pass
    finally:
        mgr.unsubscribe(rt.table_id, as_)
        with contextlib.suppress(Exception):
            await websocket.close()
