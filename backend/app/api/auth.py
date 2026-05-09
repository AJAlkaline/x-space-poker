"""Authentication.

Two paths share one `current_player_id` dependency:

- **Production / X OAuth**: read JWT from the `session` httpOnly cookie.
  Validate. The handle in the JWT is the player_id we use everywhere.
- **Dev / fake auth**: read `?as=<handle>` from query string. Gated by
  `auth_mode` setting — disabled in production.

Routes:

- `GET /auth/login` — start OAuth, return the X authorize URL.
- `GET /auth/callback?code=...&state=...` — finish OAuth, set cookie,
  redirect to frontend.
- `POST /auth/logout` — clear cookie.
- `GET /auth/me` — return current session's user.

The in-memory wallet from Path A still exists in the fake path for
backwards compatibility with tests; production uses the DB.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.security import (
    SessionClaims,
    issue_session_token,
    verify_session_token,
)
from app.services import oauth as oauth_service

router = APIRouter()

_HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{2,20}$")
_DEFAULT_BALANCE = 10_000
_balances: dict[str, int] = defaultdict(lambda: _DEFAULT_BALANCE)

# State store singleton (replaced with Redis when persistence_enabled and a
# Redis client is available; in-memory otherwise).
_state_store: oauth_service.StateStore | None = None


def get_state_store() -> oauth_service.StateStore:
    global _state_store
    if _state_store is None:
        _state_store = oauth_service.InMemoryStateStore()
    return _state_store


def set_state_store(store: oauth_service.StateStore) -> None:
    """Override the state store (used by app startup to install Redis)."""
    global _state_store
    _state_store = store


# ---------------------------------------------------------------------------
# The dependency every other endpoint uses
# ---------------------------------------------------------------------------

def current_player_id(
    session: Annotated[str | None, Cookie()] = None,
    as_: Annotated[str | None, Query(alias="as")] = None,
) -> str:
    """Return the authenticated player's id (= their handle).

    Order of precedence:
    1. `session` cookie (JWT) — production path.
    2. `?as=<handle>` query — dev/test path, gated by auth_mode setting.

    This dependency only does identity. Balance lookups are routed through
    `/auth/me` (or directly through the persistence layer for ledger
    operations) — keeping this sync and free of side effects.
    """
    settings = get_settings()
    if session:
        claims = verify_session_token(session)
        if claims is not None:
            return claims.handle
        if settings.auth_mode == "x_oauth":
            raise HTTPException(401, "invalid session")
    if settings.auth_mode in ("fake", "both"):
        if as_ is None:
            raise HTTPException(401, "not authenticated")
        if not _HANDLE_RE.match(as_):
            raise HTTPException(400, "invalid handle (2-20 chars, alphanumeric + _)")
        return as_
    raise HTTPException(401, "not authenticated")


PlayerId = Annotated[str, Depends(current_player_id)]


def get_balance(player_id: str) -> int:
    return _balances[player_id]


def adjust_balance(player_id: str, delta: int) -> int:
    new_balance = _balances[player_id] + delta
    if new_balance < 0:
        raise HTTPException(400, "insufficient chips")
    _balances[player_id] = new_balance
    return new_balance


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/me")
async def me(player_id: PlayerId) -> dict:
    """Return the current session's user + balance.

    In persistence mode, balance comes from the `accounts` table — the
    real source of truth. In fake mode it comes from the in-memory dict.
    """
    if get_settings().persistence_enabled:
        from app.db.session import get_session
        from app.services import persistence
        async with get_session() as s:
            account = await persistence.get_account_for_handle(s, player_id)
            if account is None:
                # Session is valid but the account hasn't been provisioned yet
                # (e.g. user authed via fake auth in persistence mode and never
                # hit a flow that creates the account). Auto-create.
                account = await persistence.ensure_account_for_handle(s, player_id)
            return {"player_id": player_id, "balance": account.balance_minor}
    return {"player_id": player_id, "balance": get_balance(player_id)}


def _safe_next_url(candidate: str | None) -> str | None:
    """Validate a `next` parameter to prevent open-redirect attacks.

    Accept only paths on our own origin: must start with a single `/`
    (not `//`, which is a protocol-relative URL the browser would treat
    as same-scheme + arbitrary host). Reject backslashes and control
    characters that some browsers normalize away.
    """
    if not candidate:
        return None
    if not candidate.startswith("/"):
        return None
    # Reject "//host", "/\evil", and anything with a scheme like "/javascript:"
    if candidate.startswith("//") or candidate.startswith("/\\"):
        return None
    # No control characters or whitespace
    if any(c < " " or c == "\x7f" for c in candidate):
        return None
    return candidate


@router.get("/login")
async def login(next: str | None = Query(None)) -> dict:
    """Start the OAuth flow. Returns the X authorize URL for the client to
    redirect to. The state is stored server-side.

    If `next` is provided and is a safe same-origin path, the user will be
    redirected there after completing the flow. Otherwise they land on `/`.
    """
    safe_next = _safe_next_url(next)
    try:
        flow = await oauth_service.start_oauth(get_state_store(), next_url=safe_next)
    except oauth_service.OAuthError as e:
        raise HTTPException(503, str(e)) from e
    return {"authorize_url": flow.authorize_url, "state": flow.state}


@router.get("/callback")
async def callback(
    request: Request,
    code: str = Query(...),
    state: str = Query(...),
) -> RedirectResponse:
    """Finish OAuth: exchange code → token → user id, set session cookie,
    redirect to frontend (to the `next` URL stashed at login time, or `/`)."""
    try:
        x_user = await oauth_service.complete_oauth(get_state_store(), code, state)
    except oauth_service.OAuthError as e:
        # Redirect to frontend with an error param so the UI can show it.
        return RedirectResponse(url=f"/?auth_error={e}", status_code=302)

    # Upsert User + Account if persistence is enabled. Otherwise just issue
    # a token for the in-memory flow.
    user_id = x_user.x_user_id  # use X id as user_id when no DB
    if get_settings().persistence_enabled:
        from app.db.session import get_session
        from app.services import persistence
        async with get_session() as s:
            account = await persistence.ensure_account_for_handle(
                s, x_user.handle,
            )
            user_id = str(account.user_id)
            # If the user's X id changed since last time (handle reuse), update.
            from sqlalchemy import update

            from app.db.models import User
            await s.execute(
                update(User).where(User.id == account.user_id).values(
                    x_user_id=x_user.x_user_id, handle=x_user.handle,
                ),
            )

    token = issue_session_token(SessionClaims(
        user_id=user_id, handle=x_user.handle, x_user_id=x_user.x_user_id,
    ))

    # Re-validate next_url even though we validated it at login time —
    # defense in depth in case the state store ever leaks structured data
    # from elsewhere.
    redirect_target = _safe_next_url(x_user.next_url) or "/"

    response = RedirectResponse(url=redirect_target, status_code=302)
    _set_session_cookie(response, token)
    return response


def _set_session_cookie(response, token: str) -> None:
    """Set the session cookie on a response. Centralized so the cookie
    flags stay consistent across login paths (real OAuth, fake login, and
    any future ones)."""
    settings = get_settings()
    response.set_cookie(
        key="session",
        value=token,
        max_age=settings.jwt_ttl_seconds,
        httponly=True,
        secure=(settings.env != "dev"),
        samesite="lax",
        path="/",
    )


@router.post("/logout")
async def logout() -> JSONResponse:
    response = JSONResponse({"ok": True})
    response.delete_cookie("session", path="/")
    return response


@router.get("/config")
async def auth_config() -> dict:
    """Tell the client which auth options are available.

    The frontend calls this on mount to decide whether to render the
    "Sign in with X" button, the "Continue as <handle>" form, or both.
    """
    settings = get_settings()
    return {
        "auth_mode": settings.auth_mode,
        "oauth_available": bool(
            settings.x_client_id and settings.x_client_secret,
        ),
        "fake_auth_enabled": settings.auth_mode in ("fake", "both"),
    }


class FakeLoginRequest(BaseModel):
    handle: str = Field(min_length=2, max_length=20)


@router.post("/fake-login")
async def fake_login(req: FakeLoginRequest) -> JSONResponse:
    """Dev-only: take a handle and issue a session cookie for it.

    Gated by `auth_mode in ('fake', 'both')` — refuses in production
    (`auth_mode='x_oauth'`) so a misconfigured deploy can't accidentally
    accept arbitrary handles.
    """
    settings = get_settings()
    if settings.auth_mode not in ("fake", "both"):
        raise HTTPException(404, "fake login disabled")
    if not _HANDLE_RE.match(req.handle):
        raise HTTPException(400, "invalid handle (2-20 chars, alphanumeric + _)")

    # The handle becomes the player_id. In persistence mode, also provision
    # a User+Account row so /auth/me and ledger operations work consistently.
    user_id = req.handle
    if settings.persistence_enabled:
        from app.db.session import get_session
        from app.services import persistence
        async with get_session() as s:
            account = await persistence.ensure_account_for_handle(s, req.handle)
            user_id = str(account.user_id)

    token = issue_session_token(SessionClaims(
        user_id=user_id, handle=req.handle, x_user_id=f"fake:{req.handle}",
    ))
    response = JSONResponse({"player_id": req.handle})
    _set_session_cookie(response, token)
    return response
