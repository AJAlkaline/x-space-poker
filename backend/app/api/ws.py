"""WebSocket routes.

Two endpoints:

- /ws/tables/{code}  — bidirectional player session. Receives public +
  private events, sends actions back.
- /ws/spectate/{code} — one-way spectator session. Receives only public
  events. No action channel exists for this socket — hidden info cannot
  leak structurally.

Authentication: prefers JWT cookie on the WebSocket handshake. Falls back
to `?as=<handle>` query string when `auth_mode` allows fake auth.
"""
from __future__ import annotations

import asyncio
import contextlib
import re
import secrets

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from app.core.config import get_settings
from app.core.security import verify_session_token
from app.engine import Action, ActionType
from app.services.table_manager import get_manager
from app.services.wire import private_event_to_wire, public_event_to_wire

router = APIRouter()

_HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{2,20}$")


def _resolve_handle(websocket: WebSocket, as_: str | None) -> str | None:
    """Resolve a handle from either the session cookie or the ?as= fallback.

    Returns the handle, or None if no valid auth was provided.
    """
    settings = get_settings()
    cookie = websocket.cookies.get("session")
    if cookie:
        claims = verify_session_token(cookie)
        if claims is not None:
            return claims.handle
        if settings.auth_mode == "x_oauth":
            return None
    if settings.auth_mode in ("fake", "both") and as_ is not None and _HANDLE_RE.match(as_):
        return as_
    return None


@router.websocket("/ws/tables/{code}")
async def player_socket(
    websocket: WebSocket, code: str, as_: str | None = Query(None, alias="as"),
) -> None:
    handle = _resolve_handle(websocket, as_)
    if handle is None:
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION, reason="not authenticated",
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
    public_q, private_q = mgr.subscribe_player(rt.table_id, handle)

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
                    Action(player_id=handle, action_type=action_type, amount=amount),
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
        mgr.unsubscribe_player(rt.table_id, handle)
        with contextlib.suppress(Exception):
            await websocket.close()


@router.websocket("/ws/spectate/{code}")
async def spectator_socket(
    websocket: WebSocket, code: str, as_: str | None = Query(None, alias="as"),
) -> None:
    """Spectator: public stream only. Auth is optional — anonymous spectators
    get a synthetic id; authenticated ones use their handle."""
    handle = _resolve_handle(websocket, as_)
    mgr = get_manager()
    rt = mgr.get_by_code(code)
    if rt is None:
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION, reason="table not found",
        )
        return

    viewer_id = handle if handle else f"anon:{secrets.token_urlsafe(8)}"
    subscriber_id = f"spec:{viewer_id}"

    await websocket.accept()
    public_q = mgr.subscribe_spectator(rt.table_id, subscriber_id)

    async def pump_public() -> None:
        while True:
            event = await public_q.get()
            await websocket.send_json(public_event_to_wire(event))

    task = asyncio.create_task(pump_public())
    try:
        while True:
            try:
                await websocket.receive_text()
            except WebSocketDisconnect:
                break
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        mgr.unsubscribe_spectator(rt.table_id, subscriber_id)
        with contextlib.suppress(Exception):
            await websocket.close()
