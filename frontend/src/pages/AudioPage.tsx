import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

interface AudioStatus {
  narration_enabled: boolean;
  listener_count: number;
  tts_configured: boolean;
  transcript_lines: number;
}

interface ClipMessage {
  type: "clip";
  seq: number;
  published_at: number; // server monotonic seconds
  text: string;
  audio_b64: string;
}

interface TranscriptEntry {
  seq: number;
  text: string;
}

// Maximum age of a clip (in seconds, relative to the newest clip seen)
// before we skip its audio. Behind-the-action commentary is worse than
// no commentary; better to stay close to live than play stale lines.
const MAX_CLIP_AGE_SEC = 10;


export function AudioPage() {
  const { code } = useParams<{ code: string }>();
  const [status, setStatus] = useState<AudioStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [transcript, setTranscript] = useState<TranscriptEntry[]>([]);
  const [listening, setListening] = useState(false);
  const [wsState, setWsState] = useState<"idle" | "connecting" | "open" | "closed">(
    "idle",
  );
  const transcriptRef = useRef<HTMLDivElement | null>(null);

  // Refs we mutate from inside event handlers so React doesn't need to
  // re-render to update them.
  const wsRef = useRef<WebSocket | null>(null);
  const playbackQueueRef = useRef<ClipMessage[]>([]);
  const playingNowRef = useRef<HTMLAudioElement | null>(null);
  const newestPublishedAtRef = useRef<number>(0);
  // Track whether the user has clicked Listen so audio plays. Without this,
  // browsers block autoplay before any user gesture.
  const listeningRef = useRef(false);

  // Periodic status refresh — listener count, tts_configured indicator.
  const refreshStatus = useCallback(async () => {
    if (!code) return;
    try {
      const res = await fetch(`/api/audio/${encodeURIComponent(code)}/status`);
      if (!res.ok) {
        setStatusError(`${res.status}: ${await res.text()}`);
        return;
      }
      setStatus(await res.json());
      setStatusError(null);
    } catch (e) {
      setStatusError(String(e));
    }
  }, [code]);

  useEffect(() => {
    refreshStatus();
    const id = window.setInterval(refreshStatus, 5000);
    return () => window.clearInterval(id);
  }, [refreshStatus]);

  // Auto-scroll transcript to bottom when new lines arrive.
  useEffect(() => {
    if (transcriptRef.current) {
      transcriptRef.current.scrollTop = transcriptRef.current.scrollHeight;
    }
  }, [transcript]);

  // Play the next clip in the queue, if any and if we should.
  const playNextClip = useCallback(() => {
    if (playingNowRef.current) return; // already playing
    if (!listeningRef.current) return;  // user hasn't clicked Listen

    // Skip stale clips at the head of the queue. A clip is stale if it
    // was published more than MAX_CLIP_AGE_SEC seconds before the newest
    // clip we've seen. This catches up to live when we fall behind.
    while (playbackQueueRef.current.length > 0) {
      const clip = playbackQueueRef.current[0];
      const age = newestPublishedAtRef.current - clip.published_at;
      if (age <= MAX_CLIP_AGE_SEC) break;
      // Drop it.
      playbackQueueRef.current.shift();
    }

    const clip = playbackQueueRef.current.shift();
    if (!clip) return;
    if (!clip.audio_b64) {
      // Empty audio (TTS disabled or failed). Nothing to play; transcript
      // was already updated when the clip arrived.
      // Schedule the next one on a microtask so we don't recurse.
      Promise.resolve().then(playNextClip);
      return;
    }

    // Decode base64 → Blob → Object URL → Audio.
    try {
      const binary = atob(clip.audio_b64);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
      const blob = new Blob([bytes], { type: "audio/mpeg" });
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      playingNowRef.current = audio;
      audio.onended = () => {
        URL.revokeObjectURL(url);
        if (playingNowRef.current === audio) playingNowRef.current = null;
        playNextClip();
      };
      audio.onerror = () => {
        URL.revokeObjectURL(url);
        if (playingNowRef.current === audio) playingNowRef.current = null;
        playNextClip();
      };
      audio.play().catch((e) => {
        // Most likely the user revoked permission or there's a codec issue.
        // Skip this clip and try the next.
        console.error("clip play failed:", e);
        URL.revokeObjectURL(url);
        if (playingNowRef.current === audio) playingNowRef.current = null;
        playNextClip();
      });
    } catch (e) {
      console.error("clip decode failed:", e);
      playNextClip();
    }
  }, []);

  // Open the audio WebSocket as soon as we have a code. It runs even
  // before the user clicks Listen so transcript captures everything.
  useEffect(() => {
    if (!code) return;
    let cancelled = false;
    const url =
      `${location.protocol === "https:" ? "wss:" : "ws:"}` +
      `//${location.host}/ws/audio/${encodeURIComponent(code)}`;
    setWsState("connecting");
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      if (cancelled) return;
      setWsState("open");
    };
    ws.onmessage = (event) => {
      if (cancelled) return;
      let msg: ClipMessage;
      try {
        msg = JSON.parse(event.data) as ClipMessage;
      } catch {
        return;
      }
      if (msg.type !== "clip") return;

      // Update the watermark for stale-detection.
      if (msg.published_at > newestPublishedAtRef.current) {
        newestPublishedAtRef.current = msg.published_at;
      }

      // Update transcript regardless of whether we're listening.
      if (msg.text) {
        setTranscript((prev) => {
          const next = [...prev, { seq: msg.seq, text: msg.text }];
          return next.length > 200 ? next.slice(-200) : next;
        });
      }

      // Queue for playback. The playNextClip call decides whether to
      // actually play (depends on listeningRef).
      playbackQueueRef.current.push(msg);
      playNextClip();
    };
    ws.onclose = () => {
      if (wsRef.current === ws) {
        wsRef.current = null;
      }
      if (!cancelled) setWsState("closed");
    };
    ws.onerror = () => ws.close();

    return () => {
      cancelled = true;
      const w = wsRef.current;
      wsRef.current = null;
      w?.close();
      // Stop any in-flight playback.
      const playing = playingNowRef.current;
      playingNowRef.current = null;
      if (playing) {
        playing.pause();
      }
      playbackQueueRef.current = [];
    };
  }, [code, playNextClip]);

  const startListening = () => {
    listeningRef.current = true;
    setListening(true);
    // If clips are already queued, start playing the most recent one.
    playNextClip();
  };

  const stopListening = () => {
    listeningRef.current = false;
    setListening(false);
    const playing = playingNowRef.current;
    if (playing) {
      playing.pause();
      playingNowRef.current = null;
    }
    // Drop the queue — if user unmutes, start fresh from new clips.
    playbackQueueRef.current = [];
  };

  if (!code) return <div>Missing table code.</div>;

  const streamUrl = `/api/audio/${encodeURIComponent(code)}/stream`;

  return (
    <div style={{ maxWidth: 640, margin: "0 auto", display: "grid", gap: "1.5rem" }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "baseline",
        }}
      >
        <h2 style={{ margin: 0 }}>Live narration: table {code}</h2>
        <Link to="/" style={{ fontSize: "0.85rem" }}>
          ← Lobby
        </Link>
      </div>

      {statusError && (
        <div style={{ color: "#e05050", fontSize: "0.85rem" }}>
          {statusError}
        </div>
      )}

      {status && !status.narration_enabled && (
        <div
          style={{
            padding: "0.75rem 1rem",
            border: "1px solid #553",
            borderRadius: 6,
            background: "#221",
            fontSize: "0.9rem",
          }}
        >
          Narration is not enabled on this table. The table host needs to
          opt in to narration when creating the table.
        </div>
      )}

      {status?.narration_enabled && !status.tts_configured && (
        <div
          style={{
            padding: "0.75rem 1rem",
            border: "1px solid #553",
            borderRadius: 6,
            background: "#221",
            fontSize: "0.85rem",
          }}
        >
          Narration is enabled but the TTS service has no API key configured.
          Commentary text is being captured in the transcript below, but no
          audio is being generated. Set <code>ELEVENLABS_API_KEY</code> on
          the server to enable speech.
        </div>
      )}

      {status?.narration_enabled && (
        <div
          style={{
            padding: "1rem",
            border: "1px solid #2a4d3f",
            borderRadius: 8,
            background: "#0e1116",
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "1rem",
              flexWrap: "wrap",
            }}
          >
            {!listening ? (
              <button onClick={startListening} style={{ padding: "0.5rem 1rem" }}>
                🔊 Listen live
              </button>
            ) : (
              <button onClick={stopListening} style={{ padding: "0.5rem 1rem" }}>
                🔇 Stop
              </button>
            )}
            <span style={{ fontSize: "0.8rem", opacity: 0.6 }}>
              {wsState === "open" ? "Connected" : wsState}
              {status.listener_count > 0 &&
                ` · ${status.listener_count} listener${status.listener_count === 1 ? "" : "s"}`}
            </span>
          </div>
          <div style={{ marginTop: "0.5rem", fontSize: "0.75rem", opacity: 0.5 }}>
            For OBS / VLC: use the continuous-buffered HTTP stream at{" "}
            <code>{streamUrl}</code>. Browsers should use this page (lower latency).
          </div>
        </div>
      )}

      <section>
        <h3 style={{ margin: "0 0 0.5rem 0", fontSize: "0.95rem" }}>
          Transcript
        </h3>
        <div
          ref={transcriptRef}
          style={{
            height: 320,
            overflowY: "auto",
            padding: "0.75rem 1rem",
            border: "1px solid #2a2e36",
            borderRadius: 8,
            background: "#0a0d12",
            fontSize: "0.9rem",
            lineHeight: 1.5,
          }}
        >
          {transcript.length === 0 ? (
            <div style={{ opacity: 0.4, fontStyle: "italic" }}>
              No commentary yet. Once a hand starts and an action happens,
              lines will appear here.
            </div>
          ) : (
            transcript.map((line) => (
              <div key={line.seq} style={{ marginBottom: "0.35rem" }}>
                {line.text}
              </div>
            ))
          )}
        </div>
      </section>
    </div>
  );
}
