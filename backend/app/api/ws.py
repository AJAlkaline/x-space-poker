"""WebSocket: clients subscribe to a table to receive live state updates.

Two channels per client:
- Public state (everything except others' hole cards), broadcast to all subscribers
- Private state (your hole cards, plus prompts when it's your turn), per-client

This file is a sketch — it shows the message shapes and the public/private split.
"""
from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/ws/tables/{table_id}")
async def table_socket(websocket: WebSocket, table_id: str):
    # TODO: authenticate via subprotocol or query token before accept()
    await websocket.accept()
    try:
        while True:
            msg = await websocket.receive_json()
            # Expected client messages:
            # { "type": "action", "action": "fold|check|call|bet|raise", "amount": int }
            # { "type": "ack_prompt" }      // confirms client received "your turn" so server can start timer
            # { "type": "sit_out" } / { "type": "sit_in" }
            # { "type": "buy_in", "amount": int } / { "type": "leave" }
            _ = msg
            await websocket.send_json({"type": "ack"})
    except WebSocketDisconnect:
        # TODO: register disconnect grace period; auto-fold if it expires
        return
