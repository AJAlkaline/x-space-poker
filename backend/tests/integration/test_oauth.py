"""X OAuth flow tests.

We mock the HTTP calls to X's token + users/me endpoints so the test runs
offline. The state store is the in-memory implementation by default.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api import auth as auth_module
from app.api.main import app
from app.core.config import get_settings
from app.services import oauth as oauth_service


@pytest.fixture(autouse=True)
def configured_oauth(monkeypatch):
    """Set X OAuth env vars + reset the state store so each test starts clean."""
    monkeypatch.setenv("X_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("X_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("X_REDIRECT_URI", "http://localhost:8000/auth/callback")
    monkeypatch.setenv("JWT_SECRET", "test-secret-for-jwt")
    monkeypatch.setenv("AUTH_MODE", "both")  # default — let fake auth still work
    get_settings.cache_clear()
    auth_module.set_state_store(oauth_service.InMemoryStateStore())
    yield
    get_settings.cache_clear()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_login_returns_authorize_url(client: TestClient) -> None:
    res = client.get("/auth/login")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["authorize_url"].startswith("https://twitter.com/i/oauth2/authorize?")
    assert "code_challenge=" in body["authorize_url"]
    assert "code_challenge_method=S256" in body["authorize_url"]
    assert "state=" in body["authorize_url"]
    assert body["state"]


def test_login_fails_when_credentials_missing(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("X_CLIENT_ID", "")
    get_settings.cache_clear()
    res = client.get("/auth/login")
    assert res.status_code == 503


def test_callback_completes_flow_and_sets_cookie(client: TestClient) -> None:
    # First, hit /auth/login to populate the state store.
    res = client.get("/auth/login")
    state = res.json()["state"]

    # Now mock the X token + /users/me responses for the callback.
    fake_token_response = MagicMock()
    fake_token_response.status_code = 200
    fake_token_response.json = MagicMock(return_value={"access_token": "fake-token"})

    fake_me_response = MagicMock()
    fake_me_response.status_code = 200
    fake_me_response.json = MagicMock(
        return_value={"data": {"id": "1234567890", "username": "alice_x"}},
    )

    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value=fake_token_response)
    fake_client.get = AsyncMock(return_value=fake_me_response)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)

    with patch.object(
        oauth_service, "_default_http_client", return_value=fake_client,
    ):
        # Need to follow redirects=False so we can inspect the cookie.
        res = client.get(
            "/auth/callback",
            params={"code": "auth-code-xyz", "state": state},
            follow_redirects=False,
        )

    assert res.status_code == 302, res.text
    assert res.headers["location"] == "/"
    # Cookie should be set, httpOnly.
    set_cookie = res.headers.get("set-cookie", "")
    assert "session=" in set_cookie
    assert "HttpOnly" in set_cookie or "httponly" in set_cookie


def test_callback_with_bad_state_redirects_with_error(client: TestClient) -> None:
    """An unknown state means either an attempt to forge or an expired session."""
    res = client.get(
        "/auth/callback",
        params={"code": "any", "state": "never-issued"},
        follow_redirects=False,
    )
    assert res.status_code == 302
    assert "auth_error" in res.headers["location"]


def test_session_cookie_authenticates_me(client: TestClient) -> None:
    """After a successful login, /auth/me returns the user without ?as=."""
    # Run through the login flow.
    res = client.get("/auth/login")
    state = res.json()["state"]

    fake_token_response = MagicMock()
    fake_token_response.status_code = 200
    fake_token_response.json = MagicMock(return_value={"access_token": "fake-token"})
    fake_me_response = MagicMock()
    fake_me_response.status_code = 200
    fake_me_response.json = MagicMock(
        return_value={"data": {"id": "999", "username": "bob_x"}},
    )
    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value=fake_token_response)
    fake_client.get = AsyncMock(return_value=fake_me_response)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)

    with patch.object(
        oauth_service, "_default_http_client", return_value=fake_client,
    ):
        client.get(
            "/auth/callback",
            params={"code": "abc", "state": state},
            follow_redirects=False,
        )
    # TestClient persists the cookie automatically.
    res = client.get("/auth/me")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["player_id"] == "bob_x"


def test_logout_clears_cookie(client: TestClient) -> None:
    # Set up authenticated state.
    res = client.get("/auth/login")
    state = res.json()["state"]
    fake_token = MagicMock(status_code=200, json=MagicMock(return_value={"access_token": "x"}))
    fake_me = MagicMock(
        status_code=200,
        json=MagicMock(return_value={"data": {"id": "1", "username": "carol"}}),
    )
    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value=fake_token)
    fake_client.get = AsyncMock(return_value=fake_me)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)
    with patch.object(
        oauth_service, "_default_http_client", return_value=fake_client,
    ):
        client.get("/auth/callback", params={"code": "c", "state": state}, follow_redirects=False)

    # Confirm authenticated.
    res = client.get("/auth/me")
    assert res.status_code == 200

    # Logout, cookie should be cleared.
    res = client.post("/auth/logout")
    assert res.status_code == 200
    # After logout, /auth/me should fall through to fake-auth (auth_mode=both).
    # Without a handle, it should 401.
    res = client.get("/auth/me")
    assert res.status_code == 401


def test_strict_mode_rejects_fake_auth(client: TestClient, monkeypatch) -> None:
    """When auth_mode=x_oauth, ?as= is no longer accepted."""
    monkeypatch.setenv("AUTH_MODE", "x_oauth")
    get_settings.cache_clear()
    res = client.get("/auth/me", params={"as": "alice"})
    assert res.status_code == 401


def test_state_is_consumed_once(client: TestClient) -> None:
    """The same state can't be replayed."""
    res = client.get("/auth/login")
    state = res.json()["state"]
    fake_token = MagicMock(status_code=200, json=MagicMock(return_value={"access_token": "x"}))
    fake_me = MagicMock(
        status_code=200,
        json=MagicMock(return_value={"data": {"id": "1", "username": "dave"}}),
    )
    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value=fake_token)
    fake_client.get = AsyncMock(return_value=fake_me)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)
    with patch.object(
        oauth_service, "_default_http_client", return_value=fake_client,
    ):
        # First callback: succeeds.
        res = client.get(
            "/auth/callback", params={"code": "c", "state": state},
            follow_redirects=False,
        )
        assert res.status_code == 302
        assert "auth_error" not in res.headers["location"]
        # Reusing the same state should fail.
        res = client.get(
            "/auth/callback", params={"code": "c", "state": state},
            follow_redirects=False,
        )
        assert res.status_code == 302
        assert "auth_error" in res.headers["location"]


def test_websocket_authenticates_via_cookie(client: TestClient) -> None:
    """A WebSocket connection without ?as= should still work if the session
    cookie is present."""
    # Log in to get a cookie set on the TestClient.
    res = client.get("/auth/login")
    state = res.json()["state"]
    fake_token = MagicMock(status_code=200, json=MagicMock(return_value={"access_token": "x"}))
    fake_me = MagicMock(
        status_code=200,
        json=MagicMock(return_value={"data": {"id": "1", "username": "eve"}}),
    )
    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value=fake_token)
    fake_client.get = AsyncMock(return_value=fake_me)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)
    with patch.object(
        oauth_service, "_default_http_client", return_value=fake_client,
    ):
        client.get("/auth/callback", params={"code": "c", "state": state}, follow_redirects=False)

    # Create a table (auth via cookie).
    res = client.post("/tables", json={"small_blind": 5, "big_blind": 10})
    assert res.status_code == 200
    code = res.json()["code"]

    # Connect WebSocket without ?as= — should authenticate via cookie.
    with client.websocket_connect(f"/ws/tables/{code}") as ws:
        # If we got past accept, auth worked. Drain seats to confirm.
        for _ in range(10):
            msg = ws.receive_json()
            if msg["type"] == "seats":
                break
        else:
            raise AssertionError("never received seats")


def _mock_x_client(handle: str = "alice_x", x_id: str = "1") -> MagicMock:
    """Build a MagicMock httpx-style client returning canned token + users/me responses."""
    fake_token = MagicMock(
        status_code=200,
        json=MagicMock(return_value={"access_token": "fake-token"}),
    )
    fake_me = MagicMock(
        status_code=200,
        json=MagicMock(return_value={"data": {"id": x_id, "username": handle}}),
    )
    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value=fake_token)
    fake_client.get = AsyncMock(return_value=fake_me)
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)
    return fake_client


def test_login_with_next_redirects_there_after_callback(client: TestClient) -> None:
    """A `next` query param at /auth/login should be honored on the callback redirect."""
    res = client.get("/auth/login", params={"next": "/table/ABC234"})
    assert res.status_code == 200
    state = res.json()["state"]

    fake_client = _mock_x_client()
    with patch.object(oauth_service, "_default_http_client", return_value=fake_client):
        res = client.get(
            "/auth/callback",
            params={"code": "c", "state": state},
            follow_redirects=False,
        )
    assert res.status_code == 302
    assert res.headers["location"] == "/table/ABC234"


def test_login_with_unsafe_next_falls_back_to_root(client: TestClient) -> None:
    """Unsafe `next` values must not produce open redirects.

    Cases tested: protocol-relative (`//evil.com/...`), absolute external,
    and backslash variant. The callback should redirect to `/` regardless.
    """
    for bad_next in [
        "//evil.com/phish",
        "https://evil.com/phish",
        "/\\evil.com",
        "javascript:alert(1)",  # doesn't start with /
    ]:
        res = client.get("/auth/login", params={"next": bad_next})
        assert res.status_code == 200
        state = res.json()["state"]

        fake_client = _mock_x_client()
        with patch.object(oauth_service, "_default_http_client", return_value=fake_client):
            res = client.get(
                "/auth/callback",
                params={"code": "c", "state": state},
                follow_redirects=False,
            )
        assert res.status_code == 302, f"bad_next={bad_next!r} got {res.status_code}"
        assert res.headers["location"] == "/", (
            f"bad_next={bad_next!r} redirected to {res.headers['location']!r}"
        )


def test_login_without_next_redirects_to_root(client: TestClient) -> None:
    """No `next` param means redirect to `/` (existing behavior preserved)."""
    res = client.get("/auth/login")
    state = res.json()["state"]
    fake_client = _mock_x_client()
    with patch.object(oauth_service, "_default_http_client", return_value=fake_client):
        res = client.get(
            "/auth/callback",
            params={"code": "c", "state": state},
            follow_redirects=False,
        )
    assert res.status_code == 302
    assert res.headers["location"] == "/"
