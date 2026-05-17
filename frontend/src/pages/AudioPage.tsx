import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

interface AudioStatus {
  narration_enabled: boolean;
  listener_count: number;
  tts_configured: boolean;
  transcript_lines: number;
}

interface TranscriptLine {
  time: number;
  text: string;
}

interface TranscriptResponse {
  lines: TranscriptLine[];
}

export function AudioPage() {
  const { code } = useParams<{ code: string }>();
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [status, setStatus] = useState<AudioStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [transcript, setTranscript] = useState<TranscriptLine[]>([]);
  const [playing, setPlaying] = useState(false);
  const transcriptRef = useRef<HTMLDivElement | null>(null);

  // Fetch status periodically — once on mount, then every 5s.
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

  // Poll transcript every 2s while we're on the page.
  useEffect(() => {
    if (!code) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const res = await fetch(
          `/api/audio/${encodeURIComponent(code)}/transcript`,
        );
        if (cancelled) return;
        if (res.ok) {
          const data = (await res.json()) as TranscriptResponse;
          setTranscript(data.lines);
        }
      } catch {
        // ignore transient transcript errors
      }
    };
    tick();
    const id = window.setInterval(tick, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [code]);

  // Auto-scroll transcript to bottom when new lines arrive.
  useEffect(() => {
    if (transcriptRef.current) {
      transcriptRef.current.scrollTop = transcriptRef.current.scrollHeight;
    }
  }, [transcript]);

  const handlePlay = () => {
    if (!audioRef.current) return;
    audioRef.current.play().then(() => setPlaying(true)).catch((e) => {
      console.error("audio.play() failed:", e);
    });
  };

  const handlePause = () => {
    if (!audioRef.current) return;
    audioRef.current.pause();
    setPlaying(false);
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
          <audio
            ref={audioRef}
            src={streamUrl}
            preload="none"
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
            onError={(e) => console.error("audio error", e)}
          />
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "1rem",
              flexWrap: "wrap",
            }}
          >
            {!playing ? (
              <button onClick={handlePlay} style={{ padding: "0.5rem 1rem" }}>
                ▶ Play live audio
              </button>
            ) : (
              <button onClick={handlePause} style={{ padding: "0.5rem 1rem" }}>
                ⏸ Pause
              </button>
            )}
            <span style={{ fontSize: "0.8rem", opacity: 0.6 }}>
              {status.listener_count} listener
              {status.listener_count === 1 ? "" : "s"}
            </span>
          </div>
          <div style={{ marginTop: "0.5rem", fontSize: "0.75rem", opacity: 0.5 }}>
            Direct stream URL: <code>{streamUrl}</code> — point OBS or VLC at
            this URL to broadcast the audio elsewhere.
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
            transcript.map((line, i) => (
              <div key={i} style={{ marginBottom: "0.35rem" }}>
                {line.text}
              </div>
            ))
          )}
        </div>
      </section>
    </div>
  );
}
