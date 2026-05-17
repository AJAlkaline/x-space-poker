"""Narrator consumer.

Spawned alongside each opted-in table loop. Subscribes to the table's public
event stream, runs events through a Narrator to produce commentary text,
calls the TTS service to synthesize speech, and publishes the audio onto
the AudioBus where listeners can subscribe.

This consumer is **non-essential**. If TTS fails, narration fails, or the
audio bus is busy, the game keeps running normally. Errors are logged and
the consumer continues to the next event.

Concurrency: the narrator is sync (text generation is cheap), but TTS is
async (network call). To prevent slow TTS from backing up the event queue,
we run TTS calls in a separate task with a bounded pending queue. If three
texts are already in flight, we drop the oldest pending one (newest commentary
is more relevant than stale).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging

from app.services.audio_bus import AudioBus
from app.services.event_bus import EventBus
from app.services.events import (
    ActionAppliedEvent,
    HandCompletedEvent,
    HandStartedEvent,
)
from app.services.narrator import Narrator
from app.services.tts import TTSService

log = logging.getLogger(__name__)

# How many TTS calls can be queued before we start dropping the oldest.
MAX_PENDING_TTS = 3


async def run_narrator_consumer(
    bus: EventBus,
    table_id: str,
    audio_bus: AudioBus,
    tts: TTSService,
    closed: asyncio.Event,
    *,
    subscriber_id: str | None = None,
) -> None:
    """Consume public events and produce narration audio.

    Runs until `closed` is set or the task is cancelled.
    """
    subscriber_id = subscriber_id or f"narrator:{table_id}"
    queue = bus.subscribe_public(subscriber_id)
    narrator = Narrator()
    stream = audio_bus.get_or_create(table_id)

    # Pending TTS work. Each entry is the *text* to be synthesized; a worker
    # task pulls from this queue, calls TTS, publishes audio.
    pending: asyncio.Queue[str] = asyncio.Queue(maxsize=MAX_PENDING_TTS)

    async def tts_worker() -> None:
        while True:
            text = await pending.get()
            if not text:
                pending.task_done()
                continue
            try:
                audio = await tts.synthesize(text, table_id=table_id)
                if audio:
                    stream.publish(audio, text=text)
                else:
                    # Still record the text in the transcript even if audio
                    # synthesis failed — listeners may want to see what was
                    # supposed to be spoken.
                    stream.publish(b"", text=text)
            except Exception:
                log.exception("narrator: TTS worker failed on text: %r", text[:80])
            finally:
                pending.task_done()

    worker_task = asyncio.create_task(
        tts_worker(), name=f"tts-worker-{table_id}",
    )

    try:
        while not closed.is_set():
            # Read next event with a timeout so we can periodically check
            # `closed`.
            try:
                event = await asyncio.wait_for(queue.get(), timeout=1.0)
            except TimeoutError:
                continue

            text = _text_for_event(narrator, event)
            if not text:
                continue

            # Put on the pending queue. If full, drop the oldest pending.
            if pending.full():
                try:
                    dropped = pending.get_nowait()
                    pending.task_done()
                    log.debug(
                        "narrator: dropping oldest pending text: %r",
                        dropped[:60],
                    )
                except asyncio.QueueEmpty:
                    pass
            await pending.put(text)
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("narrator: consumer loop crashed for table %s", table_id)
    finally:
        worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker_task
        bus.unsubscribe_public(subscriber_id)


def _text_for_event(narrator: Narrator, event: object) -> str:
    """Dispatch a single event to the narrator and return the resulting text.

    Returns empty string if the event isn't a narratable type or the
    narrator chose silence.
    """
    if isinstance(event, HandStartedEvent):
        ps = event.public_state or {}
        return narrator.on_hand_started(ps)
    if isinstance(event, ActionAppliedEvent):
        ps = event.public_state or {}
        action = {
            "player_id": event.player_id,
            "action_type": event.action_type,
            "amount": event.amount,
            "auto": event.auto,
        }
        return narrator.on_action(action, ps)
    if isinstance(event, HandCompletedEvent):
        ps = event.public_state or {}
        return narrator.on_hand_completed(ps, event.pot_distributions or [])
    return ""
