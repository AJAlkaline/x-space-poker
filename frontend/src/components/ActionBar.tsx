import { useState } from "react";
import type { ActionType, PrivateState, PublicState } from "../lib/types";

interface ActionBarProps {
  publicState: PublicState | null;
  privateState: PrivateState | null;
  onAction: (action: ActionType, amount?: number) => void;
}

export function ActionBar({ publicState, privateState, onAction }: ActionBarProps) {
  const [betAmount, setBetAmount] = useState<number>(0);
  const legals = privateState?.legal_actions ?? [];

  if (!publicState || legals.length === 0) {
    return (
      <div
        style={{
          padding: "1rem",
          border: "1px solid #2a2e36",
          borderRadius: 8,
          opacity: 0.6,
          textAlign: "center",
        }}
      >
        Waiting for your turn...
      </div>
    );
  }

  const fold = legals.find((a) => a.action_type === "fold");
  const check = legals.find((a) => a.action_type === "check");
  const call = legals.find((a) => a.action_type === "call");
  const bet = legals.find((a) => a.action_type === "bet");
  const raise = legals.find((a) => a.action_type === "raise");
  const sizer = bet ?? raise;

  const minSize = sizer?.min_amount ?? 0;
  const maxSize = sizer?.max_amount ?? 0;
  const clampedAmount = Math.min(maxSize, Math.max(minSize, betAmount || minSize));

  const pot = publicState.pots.reduce((s, p) => s + p.amount, 0);
  const presets = [
    { label: "½ pot", amount: Math.round(pot * 0.5) },
    { label: "¾ pot", amount: Math.round(pot * 0.75) },
    { label: "Pot", amount: pot },
    { label: "All in", amount: maxSize },
  ];

  return (
    <div
      style={{
        padding: "1rem",
        border: "1px solid #2a2e36",
        borderRadius: 8,
        display: "grid",
        gap: "0.75rem",
      }}
    >
      <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
        {fold && <button onClick={() => onAction("fold")}>Fold</button>}
        {check && <button onClick={() => onAction("check")}>Check</button>}
        {call && (
          <button onClick={() => onAction("call")}>
            Call {call.max_amount}
          </button>
        )}
        {bet && (
          <button onClick={() => onAction("bet", clampedAmount)}>
            Bet {clampedAmount}
          </button>
        )}
        {raise && (
          <button onClick={() => onAction("raise", clampedAmount)}>
            Raise to {clampedAmount}
          </button>
        )}
      </div>

      {sizer && (
        <div style={{ display: "grid", gap: "0.5rem" }}>
          <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
            {presets.map((p) => (
              <button
                key={p.label}
                onClick={() => setBetAmount(Math.min(maxSize, Math.max(minSize, p.amount)))}
                style={{ fontSize: "0.85rem", padding: "0.3rem 0.6rem" }}
              >
                {p.label}
              </button>
            ))}
          </div>
          <input
            type="range"
            min={minSize}
            max={maxSize}
            value={clampedAmount}
            onChange={(e) => setBetAmount(Number(e.target.value))}
          />
          <input
            type="number"
            min={minSize}
            max={maxSize}
            value={clampedAmount}
            onChange={(e) => setBetAmount(Number(e.target.value))}
          />
          <div style={{ fontSize: "0.85rem", opacity: 0.7 }}>
            Min {minSize} · Max {maxSize}
          </div>
        </div>
      )}
    </div>
  );
}
