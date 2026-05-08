"""X OAuth 2.0 PKCE flow.

Minimal sketch — the real implementation needs:
- PKCE code_verifier storage (Redis with short TTL)
- State parameter to prevent CSRF
- Token storage (we only need access for the initial /users/me call)
- JWT issuance for our own session
"""
from __future__ import annotations

import secrets
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.config import get_settings

router = APIRouter()


class LoginUrl(BaseModel):
    url: str
    state: str


@router.get("/login", response_model=LoginUrl)
async def login_url() -> LoginUrl:
    settings = get_settings()
    if not settings.x_client_id:
        raise HTTPException(503, "X OAuth not configured")
    state = secrets.token_urlsafe(32)
    # TODO: persist (state, code_verifier) in Redis with 10-minute TTL
    code_challenge = "todo-pkce-challenge"
    params = {
        "response_type": "code",
        "client_id": settings.x_client_id,
        "redirect_uri": settings.x_redirect_uri,
        "scope": "tweet.read users.read offline.access",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return LoginUrl(
        url=f"https://twitter.com/i/oauth2/authorize?{urlencode(params)}",
        state=state,
    )


class Callback(BaseModel):
    code: str
    state: str


@router.post("/callback")
async def callback(payload: Callback):
    """Exchange the authorization code for tokens, fetch user, issue our JWT."""
    # TODO: verify state, exchange code, hit /2/users/me, upsert User row, return JWT
    raise HTTPException(501, "not implemented")
