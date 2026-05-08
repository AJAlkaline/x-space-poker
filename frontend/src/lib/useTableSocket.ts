import { useCallback, useEffect, useRef, useState } from "react";
import type { ClientMessage, ServerMessage } from "./types";

interface UseTableSocketOptions {
  code: string;
  handle: string;
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
    if (!code || !handle) {
      setStatus("idle");
      return;
    }
    let cancelled = false;
    // Vite dev proxies /ws → backend; same path in prod.
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
        setStatus("closed");
        wsRef.current = null;
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
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [code, handle]);

  return { send, status };
}
