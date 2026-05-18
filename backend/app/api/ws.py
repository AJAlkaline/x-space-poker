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
import logging
import re
import secrets

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from app.core.config import get_settings
from app.core.security import verify_session_token
from app.engine import Action, ActionType
from app.services.ban_list import is_banned
from app.services.table_manager import get_manager
from app.services.wire import event_to_wire, public_event_to_wire

router = APIRouter()
log = logging.getLogger(__name__)

_HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{2,20}$")


def _client_ip(websocket: WebSocket) -> str | None:
    """Resolve the client IP for a WebSocket.

    Starlette stores it on `websocket.client.host`. Behind the ALB,
    this only reflects the real client when uvicorn is started with
    `--proxy-headers --forwarded-allow-ips "*"` (see Dockerfile).
    """
    return websocket.client.host if websocket.client else None


async def _reject_if_banned(websocket: WebSocket) -> bool:
    """Close the connection with 1008 if the client IP is banned.

    Returns True if the connection was rejected (caller should return
    immediately); False if it's allowed to proceed.
    """
    ip = _client_ip(websocket)
    if is_banned(ip):
        log.info("ws: rejecting banned ip %s", ip)
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION, reason="forbidden",
        )
        return True
    return False


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
    if await _reject_if_banned(websocket):
        return
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

    # Log the handle+IP correlation at connect time. This is the only
    # place we surface "which IP did handle X connect from", which makes
    # it possible to find a griefer's IP for the ban list by grepping
    # the CloudWatch logs after the fact.
    log.info("ws/tables: handle=%s code=%s ip=%s", handle, code, _client_ip(websocket))

    await websocket.accept()
    queue = mgr.subscribe_player(rt.table_id, handle)

    async def pump_outbound() -> None:
        """Drain the player's queue. Order is preserved between public and
        private events because they share a single queue on the bus."""
        while True:
            event = await queue.get()
            await websocket.send_json(event_to_wire(event))

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
        asyncio.create_task(pump_outbound()),
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
    if await _reject_if_banned(websocket):
        return
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


@router.websocket("/ws/audio/{code}")
async def audio_socket(websocket: WebSocket, code: str) -> None:
    """One-shot audio clip delivery for low-latency playback.

    Pushes one JSON message per audio clip:
        {"type": "clip", "seq": int, "published_at": float,
         "text": str, "audio_b64": str}

    Where `audio_b64` is the base64-encoded MP3 bytes (may be empty if
    TTS failed for this line — clients should still update transcript).

    No auth required. Audio contains no hidden info; same auth model as
    the HTTP audio stream.

    Designed for browser clients that want minimal lag: each clip is a
    discrete event the client can play immediately via Web Audio API or
    a transient `Audio()` element. No browser-side buffering of a long
    continuous stream.
    """
    import base64

    from app.services.audio_bus import get_audio_bus

    if await _reject_if_banned(websocket):
        return
    mgr = get_manager()
    rt = mgr.get_by_code(code)
    if rt is None:
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION, reason="table not found",
        )
        return
    if not rt.narration_enabled:
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="narration not enabled for this table",
        )
        return

    await websocket.accept()
    audio_bus = get_audio_bus()
    stream = audio_bus.get_or_create(rt.table_id)

    async def pump_clips() -> None:
        async for clip in stream.subscribe_clips():
            payload = {
                "type": "clip",
                "seq": clip.seq,
                "published_at": clip.published_at,
                "text": clip.text,
                "audio_b64": base64.b64encode(clip.audio).decode("ascii"),
            }
            await websocket.send_json(payload)

    pump_task = asyncio.create_task(pump_clips())
    try:
        # Hold the WS open. We don't expect inbound messages but we read
        # them to detect disconnects.
        while True:
            try:
                await websocket.receive_text()
            except WebSocketDisconnect:
                break
    finally:
        pump_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pump_task
        with contextlib.suppress(Exception):
            await websocket.close()
