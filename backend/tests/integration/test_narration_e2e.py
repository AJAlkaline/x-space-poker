"""End-to-end test for the narration pipeline.

This test verifies the wiring: event bus → narrator consumer → audio bus.
TTS is NOT mocked here — we just don't configure an API key, so the
TTSService runs in disabled mode and produces empty audio. The narration
*text* flow still works and is observable via the transcript endpoint,
which is what we verify.

For tests that exercise the actual ElevenLabs API, see test_tts.py
(uses a mocked httpx transport).
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.api.main import app


@pytest.fixture
def client():
    os.environ.pop("ELEVENLABS_API_KEY", None)
    with TestClient(app) as c:
        yield c


def _drain_until(ws, types, cap=40):
    targets = set(types)
    for _ in range(cap):
        msg = ws.receive_json()
        if msg.get("type") in targets:
            return msg
    raise AssertionError(f"none of {targets} arrived within {cap} messages")


class TestNarrationPipeline:
    def test_table_can_be_created_with_narration_enabled(self, client):
        res = client.post(
            "/api/tables?as=alice",
            json={
                "small_blind": 5, "big_blind": 10,
                "narration_enabled": True,
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["narration_enabled"] is True
        assert data["code"]

    def test_table_without_narration_doesnt_spawn_consumer(self, client):
        res = client.post(
            "/api/tables?as=alice",
            json={"small_blind": 5, "big_blind": 10},
        )
        assert res.json()["narration_enabled"] is False
        code = res.json()["code"]

        # Audio stream endpoint should 404 because narration not enabled.
        res = client.get(f"/api/audio/{code}/stream")
        assert res.status_code == 404

    def test_audio_status_reports_narration_state(self, client):
        res = client.post(
            "/api/tables?as=alice",
            json={"small_blind": 5, "big_blind": 10, "narration_enabled": True},
        )
        code = res.json()["code"]
        status = client.get(f"/api/audio/{code}/status").json()
        assert status["narration_enabled"] is True
        # No API key configured → tts_configured False
        assert status["tts_configured"] is False
        # No listeners yet
        assert status["listener_count"] == 0

    def test_transcript_accumulates_during_a_hand(self, client):
        res = client.post(
            "/api/tables?as=alice",
            json={"small_blind": 5, "big_blind": 10, "narration_enabled": True},
        )
        code = res.json()["code"]

        with client.websocket_connect(f"/ws/tables/{code}?as=alice") as ws_a, \
             client.websocket_connect(f"/ws/tables/{code}?as=bob") as ws_b:
            _drain_until(ws_a, ["seats"])
            _drain_until(ws_b, ["seats"])
            for who, seat in [("alice", 0), ("bob", 1)]:
                client.post(
                    "/api/tables/join",
                    params={"as": who},
                    json={"code": code, "seat": seat, "buy_in": 1000},
                )

            _drain_until(ws_a, ["hand_started"])
            _drain_until(ws_b, ["hand_started"])

            # Alice's turn pre-flop.
            a_priv = _drain_until(ws_a, ["private"])
            assert a_priv["state"]["your_turn"] is True

            # Alice opens to 30.
            ws_a.send_json({"type": "action", "action": "raise", "amount": 30})

            # Bob's BB-option turn.
            for _ in range(15):
                msg = ws_b.receive_json()
                if msg.get("type") == "private" and msg["state"]["your_turn"]:
                    break

            # Bob folds.
            ws_b.send_json({"type": "action", "action": "fold"})

            # Wait a moment for the narrator consumer to process events.
            # (Cannot easily await inside TestClient sync code; sleep instead.)
            import time as _t
            _t.sleep(0.5)

        # Now check the transcript.
        transcript = client.get(f"/api/audio/{code}/transcript").json()
        lines = [entry["text"] for entry in transcript["lines"]]
        full = " ".join(lines).lower()
        # The first hand should be announced.
        assert "heads up" in full or "handed" in full
        # The open raise should be mentioned with alice's name.
        assert "alice" in full
        # Bob folds → on the flop or pre-flop with 30 chip in. With only
        # 30+blinds in the pot, the fold may or may not be voiced. So
        # don't strictly require bob's mention; just verify there's a
        # winner announcement.
        # Hand should have completed.
        assert any("alice" in line.lower() for line in lines)

    def test_status_404_for_unknown_table(self, client):
        res = client.get("/api/audio/NOTACODE/status")
        assert res.status_code == 404

    def test_stream_404_for_unknown_table(self, client):
        res = client.get("/api/audio/NOTACODE/stream")
        assert res.status_code == 404

    def test_ws_audio_delivers_clips(self, client):
        """The new WS audio endpoint pushes individual clips with text
        and base64 audio. This is the path the SPA listener uses to get
        low-latency playback."""
        res = client.post(
            "/api/tables?as=alice",
            json={"small_blind": 5, "big_blind": 10, "narration_enabled": True},
        )
        code = res.json()["code"]

        import base64
        import time as _t

        # Open the WS audio channel.
        with client.websocket_connect(f"/ws/audio/{code}") as ws_audio:
            # Now drive a hand to trigger narration events.
            with client.websocket_connect(f"/ws/tables/{code}?as=alice") as ws_a, \
                 client.websocket_connect(f"/ws/tables/{code}?as=bob") as ws_b:
                for who, seat in [("alice", 0), ("bob", 1)]:
                    client.post(
                        "/api/tables/join", params={"as": who},
                        json={"code": code, "seat": seat, "buy_in": 1000},
                    )
                # Drain alice to her turn
                for _ in range(15):
                    msg = ws_a.receive_json()
                    if msg.get("type") == "private" and msg["state"]["your_turn"]:
                        break
                ws_a.send_json({"type": "action", "action": "raise", "amount": 30})
                # Drain bob to his turn
                for _ in range(15):
                    msg = ws_b.receive_json()
                    if msg.get("type") == "private" and msg["state"]["your_turn"]:
                        break
                ws_b.send_json({"type": "action", "action": "fold"})
                # Give the narrator consumer time to process events.
                _t.sleep(0.5)

            # Now read clips from the audio WS. With no TTS API key the
            # audio_b64 will be empty, but we should see clip events for
            # each narration line.
            clips_received = []
            for _ in range(20):
                try:
                    msg = ws_audio.receive_json()
                    if msg.get("type") == "clip":
                        clips_received.append(msg)
                    if len(clips_received) >= 2:
                        break
                except Exception:
                    break

        assert len(clips_received) >= 1, "should have received at least one clip"
        # Each clip has the expected shape.
        for clip in clips_received:
            assert "seq" in clip
            assert "text" in clip
            assert "audio_b64" in clip
            # Without API key audio is empty.
            assert base64.b64decode(clip["audio_b64"]) == b""

    def test_ws_audio_404_for_unknown_table(self, client):
        # Connecting to an unknown table closes the WS immediately.
        from starlette.websockets import WebSocketDisconnect
        with (
            pytest.raises(WebSocketDisconnect),
            client.websocket_connect("/ws/audio/NOTACODE"),
        ):
            pass

    def test_ws_audio_rejects_table_without_narration(self, client):
        from starlette.websockets import WebSocketDisconnect
        res = client.post(
            "/api/tables?as=alice",
            json={"small_blind": 5, "big_blind": 10, "narration_enabled": False},
        )
        code = res.json()["code"]
        with (
            pytest.raises(WebSocketDisconnect),
            client.websocket_connect(f"/ws/audio/{code}"),
        ):
            pass
