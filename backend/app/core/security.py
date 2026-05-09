"""JWT issuance and validation for session cookies.

Tokens are short-lived (24h default). Body contains:
  sub: User.id (uuid string)
  handle: display name (X username)
  x_user_id: X-side immutable user id
  exp: expiration (unix seconds)
  iat: issued-at (unix seconds)

Signed with HS256 + JWT_SECRET. Production must set a strong secret in env.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from jose import JWTError, jwt

from app.core.config import get_settings


@dataclass(frozen=True)
class SessionClaims:
    user_id: str  # User.id uuid string
    handle: str
    x_user_id: str


def issue_session_token(claims: SessionClaims) -> str:
    settings = get_settings()
    now = int(time.time())
    payload = {
        "sub": claims.user_id,
        "handle": claims.handle,
        "x_user_id": claims.x_user_id,
        "iat": now,
        "exp": now + settings.jwt_ttl_seconds,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def verify_session_token(token: str) -> SessionClaims | None:
    """Return claims if valid, None if invalid/expired/malformed."""
    settings = get_settings()
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm],
        )
    except JWTError:
        return None
    sub = payload.get("sub")
    handle = payload.get("handle")
    x_user_id = payload.get("x_user_id")
    if not (sub and handle and x_user_id):
        return None
    return SessionClaims(user_id=sub, handle=handle, x_user_id=x_user_id)
