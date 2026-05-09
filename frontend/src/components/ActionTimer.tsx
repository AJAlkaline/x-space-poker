import { useEffect, useState } from "react";

interface ActionTimerProps {
  baseDeadlineUnixMs: number | null;
  bankDeadlineUnixMs: number | null;
  actionTimerSeconds: number | null;
}

/**
 * Two-stage action countdown:
 * - Green progress bar drains as the base timer ticks down
 * - Switches to orange "time bank" when the base timer hits zero
 * - Shows "auto-folded" once the bank is also exhausted
 */
export function ActionTimer({
  baseDeadlineUnixMs,
  bankDeadlineUnixMs,
  actionTimerSeconds,
}: ActionTimerProps) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!baseDeadlineUnixMs) return;
    const id = window.setInterval(() => setNow(Date.now()), 100);
    return () => window.clearInterval(id);
  }, [baseDeadlineUnixMs]);

  if (!baseDeadlineUnixMs || !bankDeadlineUnixMs || !actionTimerSeconds) {
    return null;
  }

  const baseBudgetMs = actionTimerSeconds * 1000;
  const bankBudgetMs = Math.max(0, bankDeadlineUnixMs - baseDeadlineUnixMs);

  const baseRemainingMs = Math.max(0, baseDeadlineUnixMs - now);
  const bankRemainingMs = Math.max(0, bankDeadlineUnixMs - now);

  let stage: "base" | "bank" | "expired";
  let secondsLeft: number;
  let fillFraction: number;
  let color: string;
  let label: string;

  if (baseRemainingMs > 0) {
    stage = "base";
    secondsLeft = baseRemainingMs / 1000;
    fillFraction = baseRemainingMs / baseBudgetMs;
    color = secondsLeft < 5 ? "#e5a83a" : "#9bd17b";
    label = "";
  } else if (bankRemainingMs > 0) {
    stage = "bank";
    secondsLeft = bankRemainingMs / 1000;
    fillFraction = bankBudgetMs > 0 ? bankRemainingMs / bankBudgetMs : 0;
    color = "#e57b3a";
    label = "time bank";
  } else {
    stage = "expired";
    secondsLeft = 0;
    fillFraction = 0;
    color = "#7a3a3a";
    label = "auto-folded";
  }

  return (
    <div
      style={{
        display: "inline-flex",
        flexDirection: "column",
        alignItems: "stretch",
        gap: "0.3rem",
        minWidth: 160,
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "baseline",
          fontSize: "0.85rem",
          fontVariantNumeric: "tabular-nums",
        }}
      >
        <span style={{ color, fontWeight: 600 }}>
          {stage === "expired" ? label : `${secondsLeft.toFixed(1)}s`}
        </span>
        {label && stage !== "expired" && (
          <span style={{ opacity: 0.7, fontSize: "0.75rem" }}>{label}</span>
        )}
      </div>
      <div
        style={{
          height: 5,
          background: "#1a1d23",
          borderRadius: 999,
          overflow: "hidden",
        }}
      >
        <div
          style={{
            width: `${Math.max(0, Math.min(1, fillFraction)) * 100}%`,
            height: "100%",
            background: color,
            transition: "width 100ms linear",
          }}
        />
      </div>
    </div>
  );
}
