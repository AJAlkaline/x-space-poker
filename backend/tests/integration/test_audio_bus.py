"""Audio bus tests."""
from __future__ import annotations

import asyncio

import pytest

from app.services.audio_bus import _SILENT_MP3_FRAME, AudioBus, TableAudioStream


class TestTableAudioStream:
    @pytest.mark.asyncio
    async def test_publish_broadcasts_to_subscribers(self):
        stream = TableAudioStream("t1")
        received = []

        async def listener():
            async for chunk in stream.subscribe():
                received.append(chunk)
                if len(received) >= 1:
                    break

        task = asyncio.create_task(listener())
        # Give the listener a tick to register.
        await asyncio.sleep(0.01)
        stream.publish(b"audio_data", text="test")
        await asyncio.wait_for(task, timeout=1.0)
        assert received == [b"audio_data"]

    @pytest.mark.asyncio
    async def test_multiple_subscribers_all_receive(self):
        stream = TableAudioStream("t1")
        received_a, received_b = [], []

        async def listener(buf):
            async for chunk in stream.subscribe():
                buf.append(chunk)
                if len(buf) >= 1:
                    break

        task_a = asyncio.create_task(listener(received_a))
        task_b = asyncio.create_task(listener(received_b))
        await asyncio.sleep(0.01)
        stream.publish(b"shared")
        await asyncio.wait_for(asyncio.gather(task_a, task_b), timeout=1.0)
        assert received_a == [b"shared"]
        assert received_b == [b"shared"]

    @pytest.mark.asyncio
    async def test_late_subscriber_doesnt_get_old_audio(self):
        stream = TableAudioStream("t1")
        # Publish before anyone is listening.
        stream.publish(b"old_audio")

        received = []

        async def listener():
            async for chunk in stream.subscribe():
                # Skip keepalive silence; only capture real audio.
                if chunk != _SILENT_MP3_FRAME:
                    received.append(chunk)
                    break

        task = asyncio.create_task(listener())
        # Give them a tick to register.
        await asyncio.sleep(0.01)
        stream.publish(b"new_audio")
        await asyncio.wait_for(task, timeout=2.0)
        assert received == [b"new_audio"]

    @pytest.mark.asyncio
    async def test_listener_count_tracks_subscribers(self):
        stream = TableAudioStream("t1")
        assert stream.listener_count == 0

        async def short_listener():
            async for _chunk in stream.subscribe():
                break  # immediately disconnect

        # Multiple listeners briefly.
        tasks = [asyncio.create_task(short_listener()) for _ in range(3)]
        await asyncio.sleep(0.01)
        # Need to actually trigger them. The subscribe loop calls
        # asyncio.wait_for; they're parked there.
        assert stream.listener_count == 3
        # Cancel them.
        for t in tasks:
            t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.gather(*tasks, return_exceptions=False)
        # After cancellation, finally blocks should have run, count back to 0.
        # Give the event loop a tick.
        await asyncio.sleep(0.01)
        assert stream.listener_count == 0

    @pytest.mark.asyncio
    async def test_keepalive_silence_when_idle(self):
        stream = TableAudioStream("t1")
        chunks = []

        async def listener():
            async for chunk in stream.subscribe():
                chunks.append(chunk)
                if len(chunks) >= 2:
                    break

        task = asyncio.create_task(listener())
        # Wait long enough for two keepalive frames (~1 second at 500ms each).
        await asyncio.wait_for(task, timeout=2.0)
        # Both should be silence frames.
        assert all(c == _SILENT_MP3_FRAME for c in chunks)

    @pytest.mark.asyncio
    async def test_slow_subscriber_drops_clips(self):
        stream = TableAudioStream("t1")
        # Subscribe manually so we can not-read.
        # Hack the queue size to be tiny so we can fill it.
        q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=2)
        stream._byte_subscribers.add(q)
        try:
            # Publish more than the queue can hold.
            for i in range(10):
                stream.publish(f"clip{i}".encode())
            # Only 2 should be queued; the rest dropped.
            assert q.qsize() == 2
        finally:
            stream._byte_subscribers.discard(q)

    def test_transcript_recorded(self):
        stream = TableAudioStream("t1")
        stream.publish(b"audio1", text="first line")
        stream.publish(b"audio2", text="second line")
        transcript = stream.transcript
        assert len(transcript) == 2
        assert transcript[0][1] == "first line"
        assert transcript[1][1] == "second line"

    def test_transcript_bounded(self):
        stream = TableAudioStream("t1")
        for i in range(250):
            stream.publish(b"a", text=f"line {i}")
        # Transcript is bounded to ~200 entries.
        assert len(stream.transcript) <= 200

    @pytest.mark.asyncio
    async def test_clip_subscribers_receive_publications(self):
        stream = TableAudioStream("t1")
        received = []

        async def listener():
            async for clip in stream.subscribe_clips():
                received.append(clip)
                if len(received) >= 2:
                    break

        task = asyncio.create_task(listener())
        await asyncio.sleep(0.01)
        stream.publish(b"audio_a", text="first")
        stream.publish(b"audio_b", text="second")
        await asyncio.wait_for(task, timeout=1.0)
        assert len(received) == 2
        assert received[0].audio == b"audio_a"
        assert received[0].text == "first"
        assert received[1].audio == b"audio_b"
        assert received[1].text == "second"
        # Sequence numbers monotonically increasing.
        assert received[1].seq > received[0].seq

    @pytest.mark.asyncio
    async def test_clip_subscribers_get_empty_audio_too(self):
        """When TTS fails, we publish empty audio. Clip subscribers still
        receive the event so they can show transcript-only lines."""
        stream = TableAudioStream("t1")
        received = []

        async def listener():
            async for clip in stream.subscribe_clips():
                received.append(clip)
                if len(received) >= 1:
                    break

        task = asyncio.create_task(listener())
        await asyncio.sleep(0.01)
        stream.publish(b"", text="transcript without audio")
        await asyncio.wait_for(task, timeout=1.0)
        assert received[0].audio == b""
        assert received[0].text == "transcript without audio"

    @pytest.mark.asyncio
    async def test_clip_subscribers_no_keepalive_silence(self):
        """Unlike the byte-stream subscribers, clip subscribers only get
        real publications, no silence keepalive frames."""
        stream = TableAudioStream("t1")
        received = []

        async def listener():
            try:
                async with asyncio.timeout(1.5):
                    async for clip in stream.subscribe_clips():
                        received.append(clip)
            except TimeoutError:
                pass

        task = asyncio.create_task(listener())
        # Don't publish anything for 1.5s.
        await asyncio.wait_for(task, timeout=2.5)
        # No publications happened — listener should have received nothing.
        assert received == []

    @pytest.mark.asyncio
    async def test_listener_count_includes_both_subscriber_kinds(self):
        stream = TableAudioStream("t1")
        # Spin up one of each.
        byte_q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=2)
        from app.services.audio_bus import AudioClip  # noqa: F401
        clip_q: asyncio.Queue = asyncio.Queue(maxsize=2)
        stream._byte_subscribers.add(byte_q)
        stream._clip_subscribers.add(clip_q)
        try:
            assert stream.listener_count == 2
        finally:
            stream._byte_subscribers.discard(byte_q)
            stream._clip_subscribers.discard(clip_q)


class TestAudioBus:
    @pytest.mark.asyncio
    async def test_get_or_create_idempotent(self):
        bus = AudioBus()
        try:
            s1 = bus.get_or_create("table_a")
            s2 = bus.get_or_create("table_a")
            assert s1 is s2
        finally:
            await bus.close()

    @pytest.mark.asyncio
    async def test_separate_tables_separate_streams(self):
        bus = AudioBus()
        try:
            s1 = bus.get_or_create("table_a")
            s2 = bus.get_or_create("table_b")
            assert s1 is not s2
            s1.publish(b"only_a")
            assert s2.listener_count == 0
        finally:
            await bus.close()

    @pytest.mark.asyncio
    async def test_remove_drops_stream(self):
        bus = AudioBus()
        try:
            bus.get_or_create("table_a")
            assert bus.get("table_a") is not None
            bus.remove("table_a")
            assert bus.get("table_a") is None
        finally:
            await bus.close()
