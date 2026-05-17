"""Per-table audio bus.

When a table starts producing TTS audio, the bus broadcasts each generated
clip to any HTTP listeners currently connected to that table's stream.
Multiple listeners can share one table; each gets a fresh stream starting
from "now" — no replay, no buffering of past audio for new joiners.

The bus also handles **stream-keepalive silence**: HTTP audio streams need
*something* flowing or browsers/proxies will close them. When no commentary
is playing, the bus emits a short silent MP3 chunk every few hundred ms.

Lifecycle: one `AudioBus` per process. Each table gets a `TableAudioStream`
on first use; created lazily. Streams stay alive even when no listeners are
connected (so generated audio isn't lost if a listener disconnects briefly).
Old streams with no listeners and no recent activity get cleaned up by a
background task.

Concurrency: the bus is async, single-event-loop. Everything goes through
`asyncio.Queue` so no locks needed.

Data flow:
  Engine event → narrator_consumer → tts.synthesize() → audio_bus.publish()
  audio_bus.subscribe() → async generator → HTTP streaming response
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


# A 500ms silent MP3 clip we emit periodically to keep HTTP audio streams
# from underrunning during commentary gaps. Generated once by ffmpeg:
#   ffmpeg -f lavfi -i anullsrc=r=22050:cl=mono -t 0.5 -b:a 32k _silent.mp3
# Loaded at module import; bytes are immutable so this is safe to share
# across tables and threads.
_SILENT_MP3_FRAME = (Path(__file__).parent / "_silent.mp3").read_bytes()

KEEPALIVE_INTERVAL_SEC = 0.5
STREAM_IDLE_CLEANUP_SEC = 600.0  # GC streams with no listeners after 10 min


@dataclass
class AudioClip:
    """One audio publication: the bytes and metadata needed to play it.

    `published_at` is monotonic wall time when the clip was created on the
    server. Clients can use this to compute age and drop clips that have
    fallen too far behind the live action.

    `text` is the narration text the audio renders. May be empty when the
    clip is non-speech (or always empty if TTS failed — in that case the
    audio bytes are zero-length but we still emit the clip so the client
    can update its transcript).
    """

    audio: bytes
    text: str
    seq: int
    published_at: float


class TableAudioStream:
    """Audio state for a single table.

    Holds the latest commentary clip and the set of active subscribers.
    Two flavors of subscriber:

    - **Byte-stream subscribers** (`subscribe()`): used by the HTTP
      streaming endpoint. Receive raw MP3 bytes with periodic silence
      keepalive frames so the connection stays open. Suitable for OBS,
      VLC, or other consumers that prefer continuous audio. Browser audio
      elements buffer this aggressively, so it's not great for live
      latency.

    - **Clip subscribers** (`subscribe_clips()`): each subscriber receives
      one `AudioClip` per real audio publication, in order. No keepalive
      silence. Suitable for clients that want to play clips one-shot with
      minimal latency. The audio page uses this via WebSocket.
    """

    def __init__(self, table_id: str) -> None:
        self.table_id = table_id
        # Byte-stream subscribers — HTTP streaming consumers.
        self._byte_subscribers: set[asyncio.Queue[bytes]] = set()
        # Clip subscribers — WebSocket-based per-clip consumers.
        self._clip_subscribers: set[asyncio.Queue[AudioClip]] = set()
        self._last_activity = time.monotonic()
        # Last text spoken — exposed for the transcript endpoint.
        self._transcript: list[tuple[float, str]] = []
        # Monotonically increasing clip sequence number.
        self._next_seq = 0

    @property
    def listener_count(self) -> int:
        """Total active listeners across both subscription modes."""
        return len(self._byte_subscribers) + len(self._clip_subscribers)

    @property
    def last_activity(self) -> float:
        return self._last_activity

    @property
    def transcript(self) -> list[tuple[float, str]]:
        return list(self._transcript)

    def publish(self, audio: bytes, text: str = "") -> None:
        """Broadcast audio to all current subscribers (both flavors)."""
        now = time.monotonic()
        self._last_activity = now
        if text:
            self._transcript.append((now, text))
            # Keep the transcript bounded.
            if len(self._transcript) > 200:
                self._transcript = self._transcript[-200:]

        # Always emit a clip event (even when audio is empty) so clip
        # subscribers see transcript-only entries when TTS is disabled or
        # failed. The client can decide whether to display them.
        seq = self._next_seq
        self._next_seq += 1
        clip = AudioClip(audio=audio, text=text, seq=seq, published_at=now)
        for q in list(self._clip_subscribers):
            try:
                q.put_nowait(clip)
            except asyncio.QueueFull:
                log.warning(
                    "audio_bus: clip subscriber queue full for table %s; "
                    "dropping clip seq=%d", self.table_id, seq,
                )

        # The byte-stream path only carries real audio (no point sending
        # empty bytes; keepalive silence handles connection liveness).
        if not audio:
            return
        for q in list(self._byte_subscribers):
            try:
                q.put_nowait(audio)
            except asyncio.QueueFull:
                # Slow subscriber — drop this clip for them. Better than
                # backing up the whole bus.
                log.warning(
                    "audio_bus: byte subscriber queue full for table %s; "
                    "dropping clip", self.table_id,
                )

    async def subscribe(self) -> AsyncIterator[bytes]:
        """Yield raw MP3 bytes for one HTTP streaming listener.

        Sends a silence keepalive frame every ~500ms when no real audio
        is flowing. Caller is responsible for running this inside an
        async generator consumed by an HTTP streaming response. When the
        caller stops iterating (HTTP client disconnects), we clean up
        the subscriber.
        """
        # Bounded queue to detect slow consumers. 32 clips = ~30 seconds
        # of commentary in flight.
        q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=32)
        self._byte_subscribers.add(q)
        self._last_activity = time.monotonic()
        try:
            while True:
                try:
                    # Wait for a real clip or send silence to keep alive.
                    audio = await asyncio.wait_for(
                        q.get(), timeout=KEEPALIVE_INTERVAL_SEC,
                    )
                    yield audio
                except TimeoutError:
                    yield _SILENT_MP3_FRAME
        finally:
            self._byte_subscribers.discard(q)

    async def subscribe_clips(self) -> AsyncIterator[AudioClip]:
        """Yield AudioClip objects, one per publication, in order.

        No keepalive — silence between clips is just silence. Suitable
        for clients that play each clip as a discrete one-shot rather
        than buffering a continuous stream.
        """
        q: asyncio.Queue[AudioClip] = asyncio.Queue(maxsize=32)
        self._clip_subscribers.add(q)
        self._last_activity = time.monotonic()
        try:
            while True:
                clip = await q.get()
                yield clip
        finally:
            self._clip_subscribers.discard(q)


class AudioBus:
    """Process-global audio bus. One per app."""

    def __init__(self) -> None:
        self._streams: dict[str, TableAudioStream] = {}
        self._gc_task: asyncio.Task | None = None

    def get_or_create(self, table_id: str) -> TableAudioStream:
        if table_id not in self._streams:
            self._streams[table_id] = TableAudioStream(table_id)
            self._ensure_gc_running()
        return self._streams[table_id]

    def get(self, table_id: str) -> TableAudioStream | None:
        return self._streams.get(table_id)

    def remove(self, table_id: str) -> None:
        self._streams.pop(table_id, None)

    def all_streams(self) -> list[TableAudioStream]:
        return list(self._streams.values())

    def _ensure_gc_running(self) -> None:
        if self._gc_task is None or self._gc_task.done():
            try:
                loop = asyncio.get_running_loop()
                self._gc_task = loop.create_task(self._gc_loop())
            except RuntimeError:
                # No loop yet; that's fine — gc starts later.
                pass

    async def _gc_loop(self) -> None:
        """Periodically remove streams with no listeners and no recent activity."""
        while True:
            try:
                await asyncio.sleep(60)
                now = time.monotonic()
                to_remove = [
                    tid for tid, s in self._streams.items()
                    if s.listener_count == 0
                    and now - s.last_activity > STREAM_IDLE_CLEANUP_SEC
                ]
                for tid in to_remove:
                    log.info("audio_bus: GC removing idle stream for table %s", tid)
                    self._streams.pop(tid, None)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("audio_bus: GC iteration failed")

    async def close(self) -> None:
        """Cancel the background GC task. Idempotent."""
        if self._gc_task is not None and not self._gc_task.done():
            self._gc_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._gc_task
        self._gc_task = None


# ---------------------------------------------------------------------------
# Process-global singleton
# ---------------------------------------------------------------------------

_bus: AudioBus | None = None


def get_audio_bus() -> AudioBus:
    global _bus
    if _bus is None:
        _bus = AudioBus()
    return _bus


async def reset_audio_bus() -> None:
    """For tests."""
    global _bus
    if _bus is not None:
        await _bus.close()
    _bus = None
