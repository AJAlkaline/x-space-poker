"""X OAuth 2.0 PKCE flow.

PKCE (RFC 7636) lets us do OAuth without a client secret in the browser.
Server-side flow:

1. `start_oauth()` generates state + code_verifier, stashes in Redis
   keyed by state (10-min TTL), returns the X authorize URL.
2. User authorizes on X, gets redirected back to /auth/callback with
   `?code=...&state=...`.
3. `complete_oauth()` looks up the verifier by state, exchanges the code
   for an access token, fetches /users/me, returns (x_user_id, handle).

The caller (the auth route) is responsible for upserting the User row and
issuing our session JWT.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from app.core.config import get_settings

# State TTL: how long the user has to complete the flow on X's side.
STATE_TTL_SECONDS = 600

X_AUTHORIZE_URL = "https://twitter.com/i/oauth2/authorize"
X_TOKEN_URL = "https://api.twitter.com/2/oauth2/token"
X_ME_URL = "https://api.twitter.com/2/users/me"


class OAuthError(Exception):
    pass


@dataclass(frozen=True)
class StartedFlow:
    authorize_url: str
    state: str


def _gen_pkce() -> tuple[str, str]:
    """Return (code_verifier, code_challenge). RFC 7636 §4.2."""
    verifier = secrets.token_urlsafe(64)  # 64 url-safe bytes ≈ 86 chars
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


async def start_oauth(
    state_store: StateStore, next_url: str | None = None,
) -> StartedFlow:
    settings = get_settings()
    if not settings.x_client_id:
        raise OAuthError("X_CLIENT_ID not configured")
    state = secrets.token_urlsafe(32)
    verifier, challenge = _gen_pkce()
    payload = json.dumps({"verifier": verifier, "next": next_url})
    await state_store.put(state, payload, ttl=STATE_TTL_SECONDS)
    params = {
        "response_type": "code",
        "client_id": settings.x_client_id,
        "redirect_uri": settings.x_redirect_uri,
        "scope": "tweet.read users.read offline.access",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return StartedFlow(
        authorize_url=f"{X_AUTHORIZE_URL}?{urlencode(params)}",
        state=state,
    )


@dataclass(frozen=True)
class XUser:
    x_user_id: str   # immutable X user id
    handle: str      # username (mutable, used for display only)
    next_url: str | None = None  # where to redirect after sign-in


async def complete_oauth(
    state_store: StateStore,
    code: str,
    state: str,
    *,
    http_client_factory=None,
) -> XUser:
    """Exchange auth code for a user identity. Raises OAuthError on failure."""
    settings = get_settings()
    if not (settings.x_client_id and settings.x_client_secret):
        raise OAuthError("X OAuth credentials not configured")

    raw = await state_store.pop(state)
    if raw is None:
        raise OAuthError("invalid or expired state")
    # The store value is JSON {"verifier": ..., "next": ...}. Older
    # entries (or hand-crafted tests) might be a bare verifier string;
    # accept that shape too.
    try:
        payload = json.loads(raw)
        if isinstance(payload, dict):
            verifier = payload.get("verifier")
            next_url = payload.get("next")
        else:
            verifier, next_url = raw, None
    except (json.JSONDecodeError, ValueError):
        verifier, next_url = raw, None
    if not verifier:
        raise OAuthError("state payload missing verifier")

    # Confidential clients (which X treats web apps as) need Basic auth on
    # the token endpoint AND the client_id in the body. PKCE replaces the
    # need for the secret in some flows but X still requires it.
    auth = (settings.x_client_id, settings.x_client_secret)
    body = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.x_redirect_uri,
        "client_id": settings.x_client_id,
        "code_verifier": verifier,
    }

    factory = http_client_factory or _default_http_client
    async with factory() as client:
        token_resp = await client.post(
            X_TOKEN_URL, data=body, auth=auth,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if token_resp.status_code != 200:
            raise OAuthError(
                f"token exchange failed: {token_resp.status_code} {token_resp.text}",
            )
        access_token = token_resp.json().get("access_token")
        if not access_token:
            raise OAuthError("token response missing access_token")

        me_resp = await client.get(
            X_ME_URL, headers={"Authorization": f"Bearer {access_token}"},
        )
        if me_resp.status_code != 200:
            raise OAuthError(
                f"users/me failed: {me_resp.status_code} {me_resp.text}",
            )
        data = me_resp.json().get("data", {})
        x_user_id = data.get("id")
        username = data.get("username")
        if not (x_user_id and username):
            raise OAuthError(f"users/me response malformed: {data}")

    return XUser(
        x_user_id=str(x_user_id), handle=str(username), next_url=next_url,
    )


def _default_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=10.0)


# ---------------------------------------------------------------------------
# State store interface — Redis in production, in-memory for tests
# ---------------------------------------------------------------------------

class StateStore:
    """Async key-value store for the PKCE state → verifier mapping.

    Keys are random tokens (no PII). Values are code_verifiers that should
    expire if the user abandons the flow. Implementations: RedisStateStore
    (production), InMemoryStateStore (tests).
    """

    async def put(self, key: str, value: str, ttl: int) -> None:
        raise NotImplementedError

    async def pop(self, key: str) -> str | None:
        """Get-and-delete in one operation. Returns None if missing."""
        raise NotImplementedError


class InMemoryStateStore(StateStore):
    """Test-only state store. No TTL enforcement (tests don't wait long enough
    for it to matter)."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    async def put(self, key: str, value: str, ttl: int) -> None:
        self._data[key] = value

    async def pop(self, key: str) -> str | None:
        return self._data.pop(key, None)


class RedisStateStore(StateStore):
    """Redis-backed store with TTL via SET ... EX. Used in production."""

    def __init__(self, redis_client) -> None:
        self._redis = redis_client

    @staticmethod
    def _key(state: str) -> str:
        return f"oauth:state:{state}"

    async def put(self, key: str, value: str, ttl: int) -> None:
        await self._redis.set(self._key(key), value, ex=ttl)

    async def pop(self, key: str) -> str | None:
        full_key = self._key(key)
        # GETDEL is atomic; available since Redis 6.2.
        result = await self._redis.getdel(full_key)
        if result is None:
            return None
        if isinstance(result, bytes):
            return result.decode("utf-8")
        return result
