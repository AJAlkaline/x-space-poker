import type { PrivateState, PublicState } from "../lib/types";

interface TableViewProps {
  publicState: PublicState | null;
  privateState: PrivateState | null;
}

export function TableView({ publicState, privateState }: TableViewProps) {
  if (!publicState) {
    return (
      <div
        style={{
          padding: "2rem",
          border: "1px dashed #444",
          borderRadius: 8,
          textAlign: "center",
          opacity: 0.7,
        }}
      >
        Waiting for hand to start...
      </div>
    );
  }

  const totalPot = publicState.pots.reduce((s, p) => s + p.amount, 0);

  return (
    <div
      style={{
        position: "relative",
        background: "#143027",
        border: "1px solid #2a4d3f",
        borderRadius: 16,
        padding: "2rem 1rem",
        minHeight: 400,
      }}
    >
      {/* Board */}
      <div
        style={{
          display: "flex",
          justifyContent: "center",
          gap: "0.5rem",
          marginBottom: "1rem",
        }}
      >
        {publicState.board.length === 0 ? (
          <div style={{ opacity: 0.5, padding: "1rem" }}>
            {publicState.phase === "pre_flop" ? "Pre-flop" : publicState.phase}
          </div>
        ) : (
          publicState.board.map((c, i) => <CardView key={i} card={c} />)
        )}
      </div>

      {/* Pot */}
      <div style={{ textAlign: "center", marginBottom: "1.5rem" }}>
        <div style={{ opacity: 0.7, fontSize: "0.85rem" }}>Pot</div>
        <div style={{ fontSize: "1.4rem", fontWeight: 600 }}>{totalPot}</div>
      </div>

      {/* Players */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
          gap: "0.75rem",
        }}
      >
        {publicState.players.map((p, i) => {
          if (!p) {
            return (
              <div
                key={i}
                style={{
                  padding: "0.75rem",
                  border: "1px dashed #2a4d3f",
                  borderRadius: 8,
                  opacity: 0.4,
                  textAlign: "center",
                  fontSize: "0.85rem",
                }}
              >
                Seat {i + 1}
              </div>
            );
          }
          const isToAct = publicState.to_act[0] === p.id;
          const isButton = publicState.button === p.seat;
          return (
            <div
              key={i}
              style={{
                padding: "0.75rem",
                border: isToAct ? "2px solid #f5c542" : "1px solid #2a4d3f",
                borderRadius: 8,
                background: p.status === "folded" ? "#0e1f1a" : "#1a3a30",
                opacity: p.status === "folded" ? 0.5 : 1,
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ fontWeight: 600 }}>{p.id.slice(0, 10)}</span>
                {isButton && <span style={{ fontSize: "0.75rem" }}>D</span>}
              </div>
              <div style={{ fontSize: "0.85rem", opacity: 0.8 }}>
                Stack: {p.stack}
                {p.street_committed > 0 && ` · bet ${p.street_committed}`}
              </div>
              <div style={{ fontSize: "0.75rem", opacity: 0.6 }}>
                {p.status} {p.last_action ? `· ${p.last_action}` : ""}
              </div>
            </div>
          );
        })}
      </div>

      {/* Hole cards (private) */}
      {privateState?.hole && (
        <div
          style={{
            position: "absolute",
            bottom: 16,
            left: 16,
            display: "flex",
            gap: "0.25rem",
          }}
        >
          {privateState.hole.map((c, i) => (
            <CardView key={i} card={c} />
          ))}
        </div>
      )}
    </div>
  );
}

function CardView({ card }: { card: string }) {
  const rank = card[0];
  const suit = card[1];
  const isRed = suit === "h" || suit === "d";
  const suitChar = { s: "♠", h: "♥", d: "♦", c: "♣" }[suit] ?? suit;
  return (
    <div
      style={{
        width: 48,
        height: 68,
        background: "#fff",
        color: isRed ? "#c33" : "#222",
        border: "1px solid #888",
        borderRadius: 6,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        fontWeight: 700,
      }}
    >
      <div>{rank}</div>
      <div style={{ fontSize: "1.4rem", lineHeight: 1 }}>{suitChar}</div>
    </div>
  );
}
