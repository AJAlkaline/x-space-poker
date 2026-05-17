import { useCallback, useEffect, useRef, useState } from "react";
import type { ClientMessage, ServerMessage } from "./types";

interface UseTableSocketOptions {
  code: string;
  handle: string | null;
  onMessage: (msg: ServerMessage) => void;
}

export function useTableSocket({ code, handle, onMessage }: UseTableSocketOptions) {
  const wsRef = useRef<WebSocket | null>(null);
  const queueRef = useRef<ClientMessage[]>([]);
  const reconnectTimerRef = useRef<number | null>(null);
  const attemptRef = useRef(0);
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;
  const [status, setStatus] = useState<"idle" | "connecting" | "open" | "closed">(
    "idle"
  );

  const send = useCallback((msg: ClientMessage) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(msg));
    } else {
      queueRef.current.push(msg);
    }
  }, []);

  useEffect(() => {
    // Don't try to connect without both a code AND a resolved handle. The
    // handle starts as "" while /auth/me is in flight; if we opened a WS
    // with handle="" and another one a few ms later with handle="AJ" (when
    // auth resolves), we'd hit a race where the first ws's `onclose` fires
    // *after* the second ws is open and clobbers wsRef. Waiting for both
    // values keeps the effect to one run per session.
    if (!code || !handle) {
      setStatus("idle");
      return;
    }
    let cancelled = false;
    // Vite dev proxies /ws → backend; same path in prod.
    // The session cookie is sent automatically on the handshake. ?as= is
    // also appended in fake-auth mode as a belt-and-braces fallback.
    const url =
      `${location.protocol === "https:" ? "wss:" : "ws:"}` +
      `//${location.host}/ws/tables/${encodeURIComponent(code)}` +
      `?as=${encodeURIComponent(handle)}`;

    const connect = () => {
      if (cancelled) return;
      setStatus("connecting");
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        attemptRef.current = 0;
        setStatus("open");
        while (queueRef.current.length > 0) {
          const m = queueRef.current.shift()!;
          ws.send(JSON.stringify(m));
        }
      };
      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data) as ServerMessage;
          onMessageRef.current(msg);
        } catch {
          // Ignore malformed
        }
      };
      ws.onclose = () => {
        // Only clear wsRef if it still points at *this* socket. Without
        // this check, a stale socket's late `onclose` would null out
        // the reference to a newer, healthy socket and silently break
        // outbound sends.
        if (wsRef.current === ws) {
          wsRef.current = null;
        }
        setStatus("closed");
        if (cancelled) return;
        const delay = Math.min(8000, 500 * 2 ** attemptRef.current++);
        reconnectTimerRef.current = window.setTimeout(connect, delay);
      };
      ws.onerror = () => ws.close();
    };

    connect();
    return () => {
      cancelled = true;
      if (reconnectTimerRef.current) window.clearTimeout(reconnectTimerRef.current);
      const ws = wsRef.current;
      wsRef.current = null;
      ws?.close();
    };
  }, [code, handle]);

  return { send, status };
}
