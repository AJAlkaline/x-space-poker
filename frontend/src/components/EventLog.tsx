import { useEffect, useRef, useState } from "react";
import type { LogEntry, LogLevel } from "../lib/eventLog";
import { relativeTime } from "../lib/eventLog";

interface EventLogProps {
  entries: LogEntry[];
}

const LEVEL_COLOR: Record<LogLevel, string> = {
  info: "inherit",
  warning: "#e0a020",
  error: "#e05050",
};

export function EventLog({ entries }: EventLogProps) {
  const [stickToBottom, setStickToBottom] = useState(true);
  const [debug, setDebug] = useState(false);
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());
  const [now, setNow] = useState(Date.now());
  const scrollRef = useRef<HTMLDivElement>(null);

  // Update relative timestamps every second.
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);

  // Stick-to-bottom: when new entries arrive and the toggle is on, scroll
  // to the bottom on the next frame after layout.
  useEffect(() => {
    if (stickToBottom && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [entries.length, stickToBottom]);

  // Detect if the user scrolls away from the bottom; if so, disable sticky.
  // Detect if they scroll back to the bottom; re-enable.
  const handleScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (distanceFromBottom < 8) {
      if (!stickToBottom) setStickToBottom(true);
    } else {
      if (stickToBottom) setStickToBottom(false);
    }
  };

  const toggleExpanded = (id: number) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <div
      className="event-log"
      style={{
        display: "grid",
        gridTemplateRows: "auto 1fr",
        border: "1px solid #2a2e36",
        borderRadius: 8,
        background: "#0e1116",
        height: 280,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "0.5rem 0.75rem",
          borderBottom: "1px solid #2a2e36",
          fontSize: "0.85rem",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "0.6rem" }}>
          <span style={{ fontWeight: 600 }}>Event log</span>
          <span style={{ opacity: 0.5, fontSize: "0.75rem" }}>
            {entries.length} {entries.length === 1 ? "entry" : "entries"}
          </span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "0.6rem" }}>
          <label
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "0.3rem",
              cursor: "pointer",
              fontSize: "0.75rem",
              opacity: stickToBottom ? 1 : 0.5,
            }}
            title="Auto-scroll to the newest entry as it arrives"
          >
            <input
              type="checkbox"
              checked={stickToBottom}
              onChange={(e) => setStickToBottom(e.target.checked)}
            />
            stick to bottom
          </label>
          <label
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "0.3rem",
              cursor: "pointer",
              fontSize: "0.75rem",
              opacity: debug ? 1 : 0.5,
            }}
            title="Show raw event payloads for debugging"
          >
            <input
              type="checkbox"
              checked={debug}
              onChange={(e) => setDebug(e.target.checked)}
            />
            debug
          </label>
        </div>
      </div>
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        style={{
          overflowY: "auto",
          padding: "0.4rem 0.5rem",
          fontFamily:
            "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
          fontSize: "0.78rem",
          lineHeight: 1.5,
        }}
      >
        {entries.length === 0 ? (
          <div style={{ opacity: 0.5, padding: "0.5rem", fontStyle: "italic" }}>
            (no events yet)
          </div>
        ) : (
          entries.map((entry) => {
            const expanded = expandedIds.has(entry.id);
            return (
              <div
                key={entry.id}
                onClick={() => toggleExpanded(entry.id)}
                style={{
                  cursor: "pointer",
                  padding: "0.15rem 0.3rem",
                  borderRadius: 3,
                  display: "grid",
                  gridTemplateColumns: "auto 1fr",
                  gap: "0.5rem",
                  color: LEVEL_COLOR[entry.level],
                }}
              >
                <span style={{ opacity: 0.4, minWidth: 56 }}>
                  {relativeTime(entry.timestamp, now)}
                </span>
                <div>
                  <div style={{ wordBreak: "break-word" }}>{entry.text}</div>
                  {(debug || expanded) && (
                    <pre
                      style={{
                        margin: "0.25rem 0 0 0",
                        padding: "0.4rem 0.5rem",
                        background: "#1a1e26",
                        borderRadius: 4,
                        fontSize: "0.7rem",
                        whiteSpace: "pre-wrap",
                        wordBreak: "break-all",
                        opacity: 0.85,
                      }}
                    >
                      {JSON.stringify(entry.source, null, 2)}
                    </pre>
                  )}
                </div>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
