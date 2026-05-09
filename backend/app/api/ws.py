"""WebSocket routes.

Two endpoints:

- /ws/tables/{code}?as=<handle>  — bidirectional player session. Receives
  public + private events, sends actions back.
- /ws/spectate/{code}?as=<handle> — one-way spectator session. Receives only
  public events. No action channel exists for this socket — hidden info
  cannot leak structurally.

Both authenticate via the Path A `?as=<handle>` query string. Real X OAuth
will replace this in Tier 3.
"""
from __future__ import annotations

import asyncio
import contextlib
import re
import secrets

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from app.engine import Action, ActionType
from app.services.table_manager import get_manager
from app.services.wire import private_event_to_wire, public_event_to_wire

router = APIRouter()

_HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{2,20}$")


@router.websocket("/ws/tables/{code}")
async def player_socket(
    websocket: WebSocket, code: str, as_: str = Query(..., alias="as"),
) -> None:
    if not _HANDLE_RE.match(as_):
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION, reason="bad handle",
        )
        return
    mgr = get_manager()
    rt = mgr.get_by_code(code)
    if rt is None:
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION, reason="table not found",
        )
        return

    await websocket.accept()
    public_q, private_q = mgr.subscribe_player(rt.table_id, as_)

    async def pump_public() -> None:
        while True:
            event = await public_q.get()
            await websocket.send_json(public_event_to_wire(event))

    async def pump_private() -> None:
        while True:
            event = await private_q.get()
            await websocket.send_json(private_event_to_wire(event))

    async def pump_inbound() -> None:
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
                        {"type": "illegal_action",
                         "error": f"unknown action {action_str!r}"},
                    )
                    continue
                await mgr.submit_action(
                    rt.table_id,
                    Action(player_id=as_, action_type=action_type, amount=amount),
                )

    tasks = [
        asyncio.create_task(pump_public()),
        asyncio.create_task(pump_private()),
        asyncio.create_task(pump_inbound()),
    ]
    try:
        done, pending = await asyncio.wait(
            tasks, return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
        for t in done:
            with contextlib.suppress(WebSocketDisconnect, asyncio.CancelledError):
                t.result()
    except WebSocketDisconnect:
        pass
    finally:
        for t in tasks:
            t.cancel()
        mgr.unsubscribe_player(rt.table_id, as_)
        with contextlib.suppress(Exception):
            await websocket.close()


@router.websocket("/ws/spectate/{code}")
async def spectator_socket(
    websocket: WebSocket, code: str, as_: str | None = Query(None, alias="as"),
) -> None:
    """Spectator: public stream only. Anonymous spectators get a synthetic id.

    Authenticated spectators (handle in `?as=`) show up with their handle in
    viewer presence; anonymous ones get an opaque id. Either way, no private
    events can route here.
    """
    if as_ is not None and not _HANDLE_RE.match(as_):
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION, reason="bad handle",
        )
        return
    mgr = get_manager()
    rt = mgr.get_by_code(code)
    if rt is None:
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION, reason="table not found",
        )
        return

    viewer_id = as_ if as_ else f"anon:{secrets.token_urlsafe(8)}"
    # If a player with the same handle is already subscribed, prefix to avoid
    # collision in the subscriber map. Spectators are conceptually distinct
    # from players even when they share a handle.
    subscriber_id = f"spec:{viewer_id}"

    await websocket.accept()
    public_q = mgr.subscribe_spectator(rt.table_id, subscriber_id)

    async def pump_public() -> None:
        while True:
            event = await public_q.get()
            await websocket.send_json(public_event_to_wire(event))

    task = asyncio.create_task(pump_public())
    try:
        # Spectator socket has no inbound channel for actions. We still
        # await receive() to detect disconnect promptly.
        while True:
            try:
                await websocket.receive_text()
                # Ignore — spectators can't send anything meaningful.
            except WebSocketDisconnect:
                break
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        mgr.unsubscribe_spectator(rt.table_id, subscriber_id)
        with contextlib.suppress(Exception):
            await websocket.close()
