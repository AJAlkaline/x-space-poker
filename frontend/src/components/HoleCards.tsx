import type { PrivateState } from "../lib/types";
import { CardView } from "./TableView";

interface HoleCardsProps {
  privateState: PrivateState | null;
}

export function HoleCards({ privateState }: HoleCardsProps) {
  if (!privateState?.hole) return null;
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: "0.75rem",
        padding: "0.75rem 1rem",
        background: "#1a1d23",
        border: "1px solid #2a2e36",
        borderRadius: 8,
      }}
    >
      <span style={{ fontSize: "0.85rem", opacity: 0.7 }}>Your hand</span>
      <div style={{ display: "flex", gap: "0.35rem" }}>
        {privateState.hole.map((c, i) => (
          <CardView key={i} card={c} />
        ))}
      </div>
    </div>
  );
}
