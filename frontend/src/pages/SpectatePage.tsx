import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { useHandle } from "../lib/useHandle";
import type {
  PublicState,
  SeatInfo,
  ServerMessage,
} from "../lib/types";
import { TableView } from "../components/TableView";

const MAX_SEATS = 9;

/**
 * Spectator-only view of a table. Uses the /ws/spectate/ endpoint, which
 * delivers public events only — hidden information cannot leak here.
 */
export function SpectatePage() {
  const { code } = useParams();
  const { handle } = useHandle();
  const [publicState, setPublicState] = useState<PublicState | null>(null);
  const [seats, setSeats] = useState<(SeatInfo | null)[]>(
    Array.from({ length: MAX_SEATS }, () => null)
  );
  const [viewerCount, setViewerCount] = useState(0);

  const handleMessage = useCallback((msg: ServerMessage) => {
    switch (msg.type) {
      case "hand_started":
      case "state_update":
      case "hand_complete":
        setPublicState(msg.state);
        break;
      case "hand_aborted":
        setPublicState(null);
        break;
      case "seats":
        setSeats(msg.seats);
        break;
      case "viewer_count":
        setViewerCount(msg.count);
        break;
    }
  }, []);

  // Reuse the WebSocket hook by giving it a custom URL builder via window.
  // Simpler: inline the connection logic since spectate has no inbound channel.
  useSpectatorSocket(code, handle, handleMessage);

  if (!code) return <div>Missing table code.</div>;

  return (
    <div style={{ display: "grid", gap: "1rem" }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <h2 style={{ margin: 0 }}>
          Watching: Table {code}
          <span
            style={{
              marginLeft: "0.75rem",
              fontSize: "0.7rem",
              padding: "0.2rem 0.5rem",
              background: "#1f2228",
              border: "1px solid #2a2e36",
              borderRadius: 999,
              opacity: 0.85,
              textTransform: "uppercase",
              letterSpacing: "0.05em",
            }}
          >
            spectator
          </span>
        </h2>
        {viewerCount > 0 && (
          <span style={{ fontSize: "0.85rem", opacity: 0.85 }}>
            👁 {viewerCount}
          </span>
        )}
      </div>

      <TableView publicState={publicState} seats={seats} />
    </div>
  );
}

function useSpectatorSocket(
  code: string | undefined,
  handle: string | null,
  onMessage: (m: ServerMessage) => void
) {
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;

  useEffect(() => {
    if (!code) return;
    const params = new URLSearchParams();
    if (handle) params.set("as", handle);
    const url =
      `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}` +
      `/ws/spectate/${encodeURIComponent(code)}` +
      (params.toString() ? `?${params}` : "");
    const ws = new WebSocket(url);
    ws.onmessage = (e) => {
      try {
        onMessageRef.current(JSON.parse(e.data));
      } catch {
        // ignore
      }
    };
    return () => ws.close();
  }, [code, handle]);
}
