"""Audio streaming endpoints.

  GET /api/audio/{code}/stream
    Streaming MP3 response. Open in a browser audio element or feed to
    OBS / VLC / any HTTP-audio consumer. No auth — anyone with the code
    can listen. Audio includes silence keepalive frames during quiet
    periods so the connection stays open and the buffer doesn't underrun.

  GET /api/audio/{code}/transcript
    JSON of the recent narration lines spoken on this table. Useful for
    spectator UIs that want to display a captions log, or for debugging
    what the narrator decided to say.

  GET /api/audio/{code}/status
    Minimal JSON status (narration_enabled, listener_count). Used by the
    SPA to decide whether to show the audio player.

These endpoints require the table to exist and to have been created with
`narration_enabled=true`. Without that, audio_bus will not have a stream
for the table and we return 404.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from starlette.responses import StreamingResponse

from app.services.audio_bus import get_audio_bus
from app.services.table_manager import get_manager
from app.services.tts import get_tts_service

log = logging.getLogger(__name__)

router = APIRouter()


@router.get("/{code}/status")
async def audio_status(code: str):
    rt = get_manager().get_by_code(code)
    if rt is None:
        raise HTTPException(404, "table not found")
    bus = get_audio_bus()
    stream = bus.get(rt.table_id)
    tts = await get_tts_service()
    return {
        "narration_enabled": rt.narration_enabled,
        "listener_count": stream.listener_count if stream else 0,
        "tts_configured": tts.enabled,
        "transcript_lines": len(stream.transcript) if stream else 0,
    }


@router.get("/{code}/transcript")
async def audio_transcript(code: str):
    rt = get_manager().get_by_code(code)
    if rt is None:
        raise HTTPException(404, "table not found")
    bus = get_audio_bus()
    stream = bus.get(rt.table_id)
    if stream is None:
        return {"lines": []}
    # Return the most recent ~50 lines.
    return {
        "lines": [
            {"time": t, "text": text}
            for t, text in stream.transcript[-50:]
        ],
    }


@router.get("/{code}/stream")
async def audio_stream(code: str):
    """Stream MP3 audio for a table. Headers tell the browser this is a
    live audio stream that should be played continuously.
    """
    rt = get_manager().get_by_code(code)
    if rt is None:
        raise HTTPException(404, "table not found")
    if not rt.narration_enabled:
        raise HTTPException(404, "narration not enabled for this table")

    bus = get_audio_bus()
    stream = bus.get_or_create(rt.table_id)

    async def body():
        try:
            async for chunk in stream.subscribe():
                yield chunk
        except Exception:
            log.exception("audio_stream: subscriber generator failed")

    return StreamingResponse(
        body(),
        media_type="audio/mpeg",
        headers={
            # Prevent browser/proxy caching of the live stream.
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            # Indicate this is a chunked live stream.
            "X-Accel-Buffering": "no",
            # Permissive CORS for OBS / external embedding. The audio
            # content itself isn't sensitive (no auth-gated info).
            "Access-Control-Allow-Origin": "*",
        },
    )
