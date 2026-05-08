"""Narration service: queue + cache wrapper around ElevenLabs streaming TTS.

Audio output is the host's responsibility — this service produces audio bytes
and exposes them via a websocket-friendly stream. The host machine has OBS or
VB-Cable routing the resulting audio into the X Spaces app.

Latency strategy:
- Common phrases ("check", "fold", every card, every player handle) are
  pre-synthesized at startup and cached in object storage / local disk.
- Dynamic phrases (e.g. raise amounts) are synthesized on demand.
- The narration queue serializes calls so phrases never overlap.
- Playback latency is treated as flavor; the *web UI* drives action timing.

This file is a stub — full implementation requires the ElevenLabs Python SDK
or direct httpx calls to the streaming endpoint.
"""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass

import httpx

from app.core.config import get_settings


@dataclass(frozen=True)
class NarrationRequest:
    text: str
    priority: int = 5  # Lower = more urgent (1 = action prompt, 9 = pleasantry)


class Narrator:
    def __init__(self) -> None:
        self.queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._task: asyncio.Task | None = None
        self._cache: dict[str, bytes] = {}  # phrase_hash -> mp3 bytes; offload to S3 in prod
        self._http = httpx.AsyncClient(timeout=10.0)

    async def start(self) -> None:
        self._task = asyncio.create_task(self._worker())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        await self._http.aclose()

    async def say(self, text: str, priority: int = 5) -> None:
        await self.queue.put((priority, NarrationRequest(text=text, priority=priority)))

    async def _worker(self) -> None:
        while True:
            _, req = await self.queue.get()
            try:
                audio = await self._synth(req.text)
                # TODO: pipe `audio` to the host's audio output channel
                # (named pipe, loopback websocket, etc.)
                _ = audio
            except Exception:
                # Don't crash the worker on a single failure.
                pass

    async def _synth(self, text: str) -> bytes:
        h = hashlib.sha256(text.encode()).hexdigest()
        if h in self._cache:
            return self._cache[h]
        settings = get_settings()
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{settings.elevenlabs_voice_id}"
        headers = {"xi-api-key": settings.elevenlabs_api_key}
        body = {"text": text, "model_id": settings.elevenlabs_model}
        r = await self._http.post(url, json=body, headers=headers)
        r.raise_for_status()
        audio = r.content
        self._cache[h] = audio
        return audio
