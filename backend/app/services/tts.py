"""Text-to-speech via ElevenLabs Flash v2.5.

Wraps the ElevenLabs HTTP API. Two key responsibilities beyond the basic
'text in, audio bytes out':

1. **Cost protection.** Real money flows through this service. We enforce
   per-table character budgets and a global cap. A bug that produces
   500-word narrations on every action shouldn't be able to drain the
   account in an hour. See `TTSConfig` for the knobs.

2. **Caching.** Common phrases ("fold", "check", "call") get repeated
   constantly. We cache by (voice_id, text) hash so identical generations
   reuse audio. The cache is in-memory and process-local — fine for a
   single-process deploy, doesn't scale to multi-process without Redis.

The service never raises on API errors. Failures are logged and produce
empty audio. The game keeps running; the narration goes silent until the
API recovers.

Audio format: Flash v2.5 default is mp3_22050_32 (22kHz mono MP3 at 32kbps).
That's about 4 KB/sec, perfectly streamable, browser-native.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from collections import OrderedDict
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class TTSConfig:
    """Configuration for the TTS service. Read from environment by default."""
    api_key: str | None = None
    voice_id: str = "21m00Tcm4TlvDq8ikWAM"  # Rachel — ElevenLabs default
    model_id: str = "eleven_flash_v2_5"
    output_format: str = "mp3_22050_32"
    # Cost caps. Conservative defaults.
    max_chars_per_minute_per_table: int = 1500  # ~25 chars/sec — chatty but bounded
    max_chars_per_hour_global: int = 100_000    # ~$0.50/hr at flash rate
    cache_max_entries: int = 2048

    @classmethod
    def from_env(cls) -> TTSConfig:
        return cls(
            api_key=os.environ.get("ELEVENLABS_API_KEY"),
            voice_id=os.environ.get("ELEVENLABS_VOICE_ID",
                                    "21m00Tcm4TlvDq8ikWAM"),
            model_id=os.environ.get("ELEVENLABS_MODEL", "eleven_flash_v2_5"),
            output_format=os.environ.get("ELEVENLABS_FORMAT",
                                         "mp3_22050_32"),
            max_chars_per_minute_per_table=int(os.environ.get(
                "TTS_MAX_CHARS_PER_MIN_PER_TABLE", "1500",
            )),
            max_chars_per_hour_global=int(os.environ.get(
                "TTS_MAX_CHARS_PER_HOUR", "100000",
            )),
        )


# ---------------------------------------------------------------------------
# Cost tracker
# ---------------------------------------------------------------------------

class CharacterBudget:
    """Rolling-window character usage tracking.

    Tracks how many characters were spent in the last N seconds, per-table
    and globally. A `try_spend` call returns whether the spend is allowed
    given the configured caps.
    """

    def __init__(
        self, max_per_min_per_table: int, max_per_hour_global: int,
    ) -> None:
        self._per_table_limit = max_per_min_per_table  # over rolling 60 s
        self._global_limit = max_per_hour_global       # over rolling 3600 s
        # Each entry: (timestamp, n_chars)
        self._per_table: dict[str, list[tuple[float, int]]] = {}
        self._global: list[tuple[float, int]] = []

    def try_spend(self, table_id: str, n_chars: int) -> bool:
        now = time.monotonic()
        # Prune expired entries.
        self._global = [
            (t, c) for t, c in self._global if now - t < 3600
        ]
        per_table = self._per_table.get(table_id, [])
        per_table = [(t, c) for t, c in per_table if now - t < 60]

        global_used = sum(c for _, c in self._global)
        table_used = sum(c for _, c in per_table)

        if table_used + n_chars > self._per_table_limit:
            return False
        if global_used + n_chars > self._global_limit:
            return False

        per_table.append((now, n_chars))
        self._global.append((now, n_chars))
        self._per_table[table_id] = per_table
        return True


# ---------------------------------------------------------------------------
# LRU cache for generated audio
# ---------------------------------------------------------------------------

class _AudioCache:
    """Simple in-memory LRU cache keyed by (voice_id, text) hash."""

    def __init__(self, max_entries: int) -> None:
        self._max = max_entries
        self._store: OrderedDict[str, bytes] = OrderedDict()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> bytes | None:
        if key in self._store:
            self._store.move_to_end(key)
            self._hits += 1
            return self._store[key]
        self._misses += 1
        return None

    def put(self, key: str, data: bytes) -> None:
        self._store[key] = data
        self._store.move_to_end(key)
        while len(self._store) > self._max:
            self._store.popitem(last=False)

    def stats(self) -> dict:
        return {
            "size": len(self._store),
            "hits": self._hits,
            "misses": self._misses,
        }


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------

class TTSService:
    """Async TTS client. Singleton per process — share one instance for
    cache reuse across tables. Construct once at app startup, pass into
    each narrator consumer.

    If `api_key` is None or the service is otherwise disabled, every
    `synthesize` call returns empty bytes. This lets the rest of the
    pipeline run without ElevenLabs configured (e.g. local dev).
    """

    def __init__(
        self, config: TTSConfig, http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._cache = _AudioCache(config.cache_max_entries)
        self._budget = CharacterBudget(
            config.max_chars_per_minute_per_table,
            config.max_chars_per_hour_global,
        )
        # An async client we own (so callers don't have to manage one).
        self._http = http_client or httpx.AsyncClient(
            base_url="https://api.elevenlabs.io",
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0),
        )
        self._enabled = bool(config.api_key)
        if not self._enabled:
            log.warning("TTSService: no API key configured; running in disabled mode")

    @property
    def enabled(self) -> bool:
        return self._enabled

    def stats(self) -> dict:
        return {"cache": self._cache.stats(), "enabled": self._enabled}

    async def synthesize(self, text: str, *, table_id: str) -> bytes:
        """Generate audio for the given text.

        Returns MP3 bytes on success, empty bytes on any failure (no API key,
        budget exceeded, API error). Never raises.
        """
        if not text or not self._enabled:
            return b""

        # Cache lookup first — free, instant.
        cache_key = self._cache_key(text)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        # Charge the budget before making the call.
        if not self._budget.try_spend(table_id, len(text)):
            log.warning(
                "TTSService: budget exceeded for table %s, dropping %d chars",
                table_id, len(text),
            )
            return b""

        try:
            audio = await self._call_api(text)
            if audio:
                self._cache.put(cache_key, audio)
            return audio
        except Exception:
            log.exception("TTSService: API call failed; returning silent audio")
            return b""

    async def aclose(self) -> None:
        await self._http.aclose()

    # -----------------------------------------------------------------------

    def _cache_key(self, text: str) -> str:
        # Include voice_id and model_id so cache stays valid across config
        # changes (e.g. you swap voices and want fresh audio).
        material = f"{self._config.voice_id}::{self._config.model_id}::{text}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    async def _call_api(self, text: str) -> bytes:
        url = f"/v1/text-to-speech/{self._config.voice_id}"
        params = {
            "output_format": self._config.output_format,
        }
        body = {
            "text": text,
            "model_id": self._config.model_id,
            "voice_settings": {
                # Defaults are fine for poker commentary — punchy,
                # consistent, not too dramatic.
                "stability": 0.5,
                "similarity_boost": 0.75,
                "style": 0.0,
                "use_speaker_boost": True,
            },
        }
        headers = {
            "xi-api-key": self._config.api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }
        response = await self._http.post(
            url, params=params, json=body, headers=headers,
        )
        if response.status_code != 200:
            # Log the body for diagnostics, but don't crash.
            log.error(
                "TTS API returned %s: %s", response.status_code,
                response.text[:500],
            )
            return b""
        return response.content


# ---------------------------------------------------------------------------
# Process-global singleton
# ---------------------------------------------------------------------------

_service: TTSService | None = None
_service_lock = asyncio.Lock()


async def get_tts_service() -> TTSService:
    """Lazy-construct the shared TTS service. Safe to call from anywhere."""
    global _service
    if _service is None:
        async with _service_lock:
            if _service is None:
                _service = TTSService(TTSConfig.from_env())
    return _service


async def reset_tts_service() -> None:
    """For tests."""
    global _service
    if _service is not None:
        await _service.aclose()
    _service = None
