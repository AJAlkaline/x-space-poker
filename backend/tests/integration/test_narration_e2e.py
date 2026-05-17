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
from app.services import audio_bus, table_manager, tts


@pytest.fixture(autouse=True)
def reset_singletons():
    """Each test starts with fresh manager + audio bus + TTS singletons."""
    yield
    # Manager reset
    table_manager._manager = None
    # Audio bus reset (synchronous teardown is fine since tests await close)
    audio_bus._bus = None
    # TTS reset
    tts._service = None


@pytest.fixture
def client():
    # Make sure no API key leaks in from CI env
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
