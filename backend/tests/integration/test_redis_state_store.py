"""Tests for RedisStateStore.

Uses `fakeredis` to exercise the async redis API in-process. This validates:

- `put` writes with TTL.
- `pop` returns the value and deletes the key (single-use).
- `pop` of an absent key returns None.
- Bytes values from Redis are decoded to str (real-world deployments may
  return bytes).
"""
from __future__ import annotations

import pytest


@pytest.fixture
async def redis_client():
    fakeredis = pytest.importorskip("fakeredis")
    client = fakeredis.aioredis.FakeRedis()
    yield client
    await client.aclose()


@pytest.mark.asyncio
async def test_redis_state_store_round_trip(redis_client) -> None:
    from app.services.oauth import RedisStateStore
    store = RedisStateStore(redis_client)
    await store.put("state-abc", "verifier-xyz", ttl=60)
    got = await store.pop("state-abc")
    assert got == "verifier-xyz"
    # Single-use: a second pop returns None.
    again = await store.pop("state-abc")
    assert again is None


@pytest.mark.asyncio
async def test_redis_state_store_pop_missing(redis_client) -> None:
    from app.services.oauth import RedisStateStore
    store = RedisStateStore(redis_client)
    result = await store.pop("does-not-exist")
    assert result is None


@pytest.mark.asyncio
async def test_redis_state_store_ttl_is_applied(redis_client) -> None:
    """TTL should be set on the underlying key."""
    from app.services.oauth import RedisStateStore
    store = RedisStateStore(redis_client)
    await store.put("state-ttl", "v", ttl=120)
    # The full key includes the namespace prefix.
    ttl = await redis_client.ttl(RedisStateStore._key("state-ttl"))
    # fakeredis reports remaining TTL in seconds; should be close to 120.
    assert 0 < ttl <= 120


@pytest.mark.asyncio
async def test_redis_state_store_handles_bytes_response(redis_client) -> None:
    """Real Redis clients can return bytes; the store should decode them."""
    from app.services.oauth import RedisStateStore
    store = RedisStateStore(redis_client)
    # Put raw bytes through the redis client directly to simulate a value
    # written by another process.
    await redis_client.set(RedisStateStore._key("from-bytes"), b"payload")
    got = await store.pop("from-bytes")
    assert got == "payload"


@pytest.mark.asyncio
async def test_lifespan_installs_and_closes_redis_state_store(
    monkeypatch,
) -> None:
    """With persistence_enabled=true, the lifespan must:
    1. Swap the in-memory state store for a Redis-backed one on startup.
    2. Close the Redis client on shutdown."""
    from unittest.mock import patch

    import fakeredis
    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.api import auth as auth_module
    from app.api.main import app
    from app.core.config import get_settings
    from app.db import session as db_session
    from app.db.models import Base
    from app.services.oauth import InMemoryStateStore, RedisStateStore

    # Set up the same DB plumbing the persistence_e2e tests use, otherwise
    # recovery on startup will try to talk to a real DB and fail.
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    db_session.set_engine(engine)

    monkeypatch.setenv("PERSISTENCE_ENABLED", "true")
    monkeypatch.setenv("JWT_SECRET", "x")
    get_settings.cache_clear()

    auth_module.set_state_store(InMemoryStateStore())

    # Wrap the fakeredis client so we can detect that aclose was called
    # by the lifespan on shutdown.
    fake_client = fakeredis.aioredis.FakeRedis()
    close_count = 0
    real_aclose = fake_client.aclose

    async def spy_aclose():
        nonlocal close_count
        close_count += 1
        await real_aclose()

    fake_client.aclose = spy_aclose  # type: ignore[method-assign]

    with patch("redis.asyncio.from_url", return_value=fake_client), TestClient(app):
        assert isinstance(auth_module.get_state_store(), RedisStateStore)
        # close not called yet — we're still inside the lifespan context.
        assert close_count == 0

    # TestClient context exited → lifespan shutdown ran → aclose called once.
    assert close_count == 1, (
        f"expected lifespan to aclose() the Redis client exactly once, "
        f"got {close_count}"
    )

    # Cleanup.
    db_session.set_engine(None)  # type: ignore[arg-type]
    await engine.dispose()
    get_settings.cache_clear()
