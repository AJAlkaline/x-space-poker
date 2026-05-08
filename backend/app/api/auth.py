"""Path A authentication: trust a `?as=<name>` query parameter.

This is for local two-tab playtesting only. The real X OAuth flow will replace
this later — when it does, the `current_player_id` dependency is the only
thing other modules import from here, so the swap is contained.

Also exposes a per-player in-memory chip balance ("wallet") so buy-ins debit
something. Resets on process restart.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

router = APIRouter()

_HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{2,20}$")
_DEFAULT_BALANCE = 10_000  # 10k chips ≈ 10 buy-ins at 5/10 100bb

_balances: dict[str, int] = defaultdict(lambda: _DEFAULT_BALANCE)


def current_player_id(as_: Annotated[str, Query(alias="as")]) -> str:
    """FastAPI dependency: pulls `?as=<handle>` from query and validates."""
    if not _HANDLE_RE.match(as_):
        raise HTTPException(400, "invalid handle (2-20 chars, alphanumeric + _)")
    _ = _balances[as_]  # ensure wallet entry exists
    return as_


PlayerId = Annotated[str, Depends(current_player_id)]


def get_balance(player_id: str) -> int:
    return _balances[player_id]


def adjust_balance(player_id: str, delta: int) -> int:
    """Adjust a player's chip balance. Negative delta debits."""
    new_balance = _balances[player_id] + delta
    if new_balance < 0:
        raise HTTPException(400, "insufficient chips")
    _balances[player_id] = new_balance
    return new_balance


@router.get("/me")
async def me(player_id: PlayerId) -> dict:
    return {"player_id": player_id, "balance": get_balance(player_id)}
