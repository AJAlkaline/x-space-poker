import type { PotDistribution, PublicState, SeatInfo } from "../lib/types";

interface TableViewProps {
  publicState: PublicState | null;
  seats: (SeatInfo | null)[];
  /** Most recent hand's pot distributions, set on hand_complete and cleared
   *  on the next hand_started. When non-null, we highlight the winning
   *  cards and show hand descriptions next to winners. */
  potDistributions: PotDistribution[] | null;
}

/** Position label color — subtle accent so it doesn't dominate the layout. */
const POSITION_COLOR = "#7fb8a4";

export function TableView({ publicState, seats, potDistributions }: TableViewProps) {
  // No hand yet: show seats from the seats snapshot.
  if (!publicState) {
    const occupied = seats.filter((s) => s != null);
    return (
      <div
        style={{
          padding: "2rem",
          border: "1px dashed #2a4d3f",
          borderRadius: 16,
          background: "#143027",
          textAlign: "center",
        }}
      >
        <div style={{ opacity: 0.7, marginBottom: "1rem" }}>
          {occupied.length < 2
            ? `Waiting for players (${occupied.length}/2 seated)...`
            : "Starting hand..."}
        </div>
        <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", justifyContent: "center" }}>
          {occupied.map((s) => (
            <div
              key={s!.seat}
              style={{
                padding: "0.5rem 0.75rem",
                background: "#1a3a30",
                borderRadius: 6,
              }}
            >
              {s!.user_id} · {s!.stack}
            </div>
          ))}
        </div>
      </div>
    );
  }

  const totalPot = publicState.pot_total;

  // Compute the set of winning cards (across all pots; in side-pot
  // situations multiple players can each have their own winning hand,
  // so the union is what we highlight). Empty if no distributions yet
  // or all winners were fold-wins.
  const winningCards = new Set<string>();
  // Map of player_id → array of hand descriptions (one entry per pot won
  // by that player). Same player rarely wins multiple pots but can in
  // side-pot scenarios; we display all unique descriptions.
  const winnerDescriptions = new Map<string, string[]>();
  if (potDistributions) {
    for (const dist of potDistributions) {
      for (const w of dist.winners) {
        for (const c of w.best_five) {
          winningCards.add(c);
        }
        if (w.hand_description) {
          const existing = winnerDescriptions.get(w.player_id) ?? [];
          if (!existing.includes(w.hand_description)) {
            existing.push(w.hand_description);
            winnerDescriptions.set(w.player_id, existing);
          }
        }
      }
    }
  }

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
          minHeight: 70,
        }}
      >
        {publicState.board.length === 0 ? (
          <div style={{ opacity: 0.5, padding: "1rem" }}>
            {publicState.phase === "pre_flop" ? "Pre-flop" : publicState.phase}
          </div>
        ) : (
          publicState.board.map((c, i) => (
            <CardView key={i} card={c} highlighted={winningCards.has(c)} />
          ))
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
          gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
          gap: "0.75rem",
        }}
      >
        {publicState.players.map((p, i) => {
          if (!p) {
            const seat = seats[i];
            if (seat) {
              return (
                <div
                  key={i}
                  style={{
                    padding: "0.75rem",
                    border: "1px dashed #2a4d3f",
                    borderRadius: 8,
                    opacity: 0.6,
                    fontSize: "0.85rem",
                  }}
                >
                  <div style={{ fontWeight: 600 }}>{seat.user_id}</div>
                  <div style={{ opacity: 0.8 }}>Stack: {seat.stack}</div>
                  <div style={{ opacity: 0.6 }}>Sitting out this hand</div>
                </div>
              );
            }
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
          const seatInfo = seats[p.seat];
          const isDisconnected = seatInfo?.disconnected ?? false;
          const descs = winnerDescriptions.get(p.id) ?? [];
          const isWinner = descs.length > 0;
          return (
            <div
              key={i}
              style={{
                padding: "0.75rem",
                border: isToAct
                  ? "2px solid #f5c542"
                  : isWinner
                    ? "2px solid #4fd682"
                    : "1px solid #2a4d3f",
                borderRadius: 8,
                background: p.status === "folded" ? "#0e1f1a" : "#1a3a30",
                opacity: p.status === "folded" ? 0.5 : 1,
                position: "relative",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ fontWeight: 600 }}>
                  {p.position && (
                    <span
                      style={{
                        color: POSITION_COLOR,
                        fontSize: "0.7rem",
                        fontWeight: 700,
                        marginRight: "0.3rem",
                        letterSpacing: "0.5px",
                      }}
                    >
                      {p.position}
                    </span>
                  )}
                  {p.id}
                  {isDisconnected && (
                    <span
                      style={{
                        marginLeft: "0.4rem",
                        fontSize: "0.7rem",
                        color: "#e57b3a",
                        fontWeight: 500,
                      }}
                    >
                      • disconnected
                    </span>
                  )}
                </span>
                {isButton && (
                  <span
                    style={{
                      fontSize: "0.7rem",
                      background: "#f5c542",
                      color: "#000",
                      padding: "0.1rem 0.4rem",
                      borderRadius: 999,
                      fontWeight: 700,
                    }}
                  >
                    D
                  </span>
                )}
              </div>
              <div style={{ fontSize: "0.85rem", opacity: 0.8 }}>
                Stack: {p.stack}
              </div>
              <div style={{ fontSize: "0.75rem", opacity: 0.6 }}>
                {p.status} {p.last_action ? `· ${p.last_action}` : ""}
              </div>
              {/* Chips in front of player on this street */}
              {p.street_committed > 0 && (
                <div
                  style={{
                    marginTop: "0.5rem",
                    background: "#f5c542",
                    color: "#000",
                    fontWeight: 700,
                    fontSize: "0.85rem",
                    padding: "0.15rem 0.5rem",
                    borderRadius: 999,
                    display: "inline-block",
                  }}
                >
                  → {p.street_committed}
                </div>
              )}
              {/* Showdown reveal */}
              {p.hole && (
                <div style={{ display: "flex", gap: "0.25rem", marginTop: "0.4rem" }}>
                  {p.hole.map((c, j) => (
                    <CardView
                      key={j}
                      card={c}
                      small
                      highlighted={winningCards.has(c)}
                    />
                  ))}
                </div>
              )}
              {/* Winner's hand description, shown after showdown */}
              {descs.length > 0 && (
                <div
                  style={{
                    marginTop: "0.4rem",
                    fontSize: "0.75rem",
                    color: "#4fd682",
                    fontWeight: 600,
                  }}
                >
                  {descs.join(" · ")}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function CardView({
  card,
  small = false,
  highlighted = false,
}: {
  card: string;
  small?: boolean;
  highlighted?: boolean;
}) {
  const rank = card[0];
  const suit = card[1];
  const isRed = suit === "h" || suit === "d";
  const suitChar = ({ s: "♠", h: "♥", d: "♦", c: "♣" } as Record<string, string>)[suit] ?? suit;
  const w = small ? 32 : 48;
  const h = small ? 46 : 68;
  return (
    <div
      style={{
        width: w,
        height: h,
        background: "#fff",
        color: isRed ? "#c33" : "#222",
        // Winning-card outline: brighter border + soft glow.
        border: highlighted ? "2px solid #4fd682" : "1px solid #888",
        boxShadow: highlighted ? "0 0 8px rgba(79, 214, 130, 0.7)" : "none",
        borderRadius: 6,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        fontWeight: 700,
        fontSize: small ? "0.75rem" : "1rem",
        transition: "box-shadow 0.2s, border-color 0.2s",
      }}
    >
      <div>{rank}</div>
      <div style={{ fontSize: small ? "1rem" : "1.4rem", lineHeight: 1 }}>{suitChar}</div>
    </div>
  );
}
