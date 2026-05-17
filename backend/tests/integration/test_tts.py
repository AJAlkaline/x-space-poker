"""TTS service tests. No real API calls — httpx client is mocked."""
from __future__ import annotations

import httpx
import pytest

from app.services.tts import CharacterBudget, TTSConfig, TTSService


def _fake_response(status_code: int = 200, content: bytes = b"FAKE_MP3_BYTES"):
    """Build a fake httpx.Response."""
    return httpx.Response(status_code=status_code, content=content)


class _MockTransport(httpx.AsyncBaseTransport):
    """An async transport that returns canned responses and records calls."""

    def __init__(self, response: httpx.Response, *, fail_after: int = -1):
        self.response = response
        self.calls: list[httpx.Request] = []
        self.fail_after = fail_after

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.fail_after >= 0 and len(self.calls) > self.fail_after:
            raise httpx.ConnectError("mock failure")
        return self.response


@pytest.fixture
def fake_client():
    """An httpx client backed by a mock transport that returns success."""
    transport = _MockTransport(_fake_response(200, b"FAKE_MP3"))
    client = httpx.AsyncClient(
        transport=transport, base_url="https://api.elevenlabs.io",
    )
    return client, transport


class TestCharacterBudget:
    def test_under_limit_passes(self):
        b = CharacterBudget(max_per_min_per_table=1000, max_per_hour_global=10000)
        assert b.try_spend("t1", 500) is True
        assert b.try_spend("t1", 400) is True

    def test_per_table_limit_blocks(self):
        b = CharacterBudget(max_per_min_per_table=1000, max_per_hour_global=10000)
        assert b.try_spend("t1", 1000) is True
        assert b.try_spend("t1", 1) is False  # over

    def test_per_table_is_independent(self):
        b = CharacterBudget(max_per_min_per_table=1000, max_per_hour_global=10000)
        b.try_spend("t1", 1000)
        # t2 should have its own bucket
        assert b.try_spend("t2", 1000) is True

    def test_global_limit_blocks(self):
        b = CharacterBudget(max_per_min_per_table=1000, max_per_hour_global=2000)
        b.try_spend("t1", 1000)
        b.try_spend("t2", 1000)
        # 2000/2000 globally used
        assert b.try_spend("t3", 1) is False


class TestTTSService:
    @pytest.mark.asyncio
    async def test_disabled_when_no_api_key(self):
        config = TTSConfig(api_key=None)
        svc = TTSService(config)
        assert svc.enabled is False
        out = await svc.synthesize("hello", table_id="t1")
        assert out == b""
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_basic_synthesize_returns_audio(self, fake_client):
        client, transport = fake_client
        config = TTSConfig(api_key="test-key")
        svc = TTSService(config, http_client=client)
        out = await svc.synthesize("hello world", table_id="t1")
        assert out == b"FAKE_MP3"
        assert len(transport.calls) == 1
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_cache_hits_on_repeat(self, fake_client):
        client, transport = fake_client
        config = TTSConfig(api_key="test-key")
        svc = TTSService(config, http_client=client)
        await svc.synthesize("same text", table_id="t1")
        await svc.synthesize("same text", table_id="t1")
        await svc.synthesize("same text", table_id="t1")
        # Only one actual API call.
        assert len(transport.calls) == 1
        # Cache stats reflect hits.
        stats = svc.stats()
        assert stats["cache"]["hits"] == 2
        assert stats["cache"]["misses"] == 1
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_different_text_different_call(self, fake_client):
        client, transport = fake_client
        config = TTSConfig(api_key="test-key")
        svc = TTSService(config, http_client=client)
        await svc.synthesize("first", table_id="t1")
        await svc.synthesize("second", table_id="t1")
        assert len(transport.calls) == 2
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_api_error_returns_empty(self):
        transport = _MockTransport(_fake_response(500, b"Server Error"))
        client = httpx.AsyncClient(
            transport=transport, base_url="https://api.elevenlabs.io",
        )
        config = TTSConfig(api_key="test-key")
        svc = TTSService(config, http_client=client)
        out = await svc.synthesize("hello", table_id="t1")
        assert out == b""  # silent on error
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_network_error_returns_empty(self):
        # Transport that fails on first call.
        transport = _MockTransport(_fake_response(200), fail_after=0)
        client = httpx.AsyncClient(
            transport=transport, base_url="https://api.elevenlabs.io",
        )
        config = TTSConfig(api_key="test-key")
        svc = TTSService(config, http_client=client)
        out = await svc.synthesize("hello", table_id="t1")
        assert out == b""  # silent on network failure
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_budget_exceeded_returns_empty(self, fake_client):
        client, transport = fake_client
        # Tiny budget — first call exhausts it.
        config = TTSConfig(
            api_key="test-key", max_chars_per_minute_per_table=10,
        )
        svc = TTSService(config, http_client=client)
        # First call: 5 chars, fits.
        out1 = await svc.synthesize("hello", table_id="t1")
        assert out1 == b"FAKE_MP3"
        # Second call: 8 more chars, total 13, exceeds 10.
        out2 = await svc.synthesize("goodbyes", table_id="t1")
        assert out2 == b""
        # API was hit only once.
        assert len(transport.calls) == 1
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_empty_text_returns_empty_no_api_call(self, fake_client):
        client, transport = fake_client
        config = TTSConfig(api_key="test-key")
        svc = TTSService(config, http_client=client)
        out = await svc.synthesize("", table_id="t1")
        assert out == b""
        assert len(transport.calls) == 0
        await svc.aclose()

    @pytest.mark.asyncio
    async def test_cached_audio_doesnt_count_against_budget(self, fake_client):
        """Once we've paid for a phrase, replaying it is free — including
        budget-wise. This is critical for common phrases like 'fold'."""
        client, transport = fake_client
        config = TTSConfig(
            api_key="test-key", max_chars_per_minute_per_table=10,
        )
        svc = TTSService(config, http_client=client)
        await svc.synthesize("fold", table_id="t1")  # 4 chars
        # Cache hit; should not count against budget.
        for _ in range(20):
            out = await svc.synthesize("fold", table_id="t1")
            assert out == b"FAKE_MP3"
        # API hit only once total.
        assert len(transport.calls) == 1
        await svc.aclose()
