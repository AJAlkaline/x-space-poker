import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import type { PotDistribution, PublicState, SeatInfo } from "../lib/types";
import { ChipFlightOverlay, type ChipFlight } from "./ChipFlight";

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

/**
 * Hook returning the value from the previous render. Used to detect
 * transitions (e.g. street_committed cleared, pot grew).
 */
function usePrevious<T>(value: T): T | undefined {
  const ref = useRef<T | undefined>(undefined);
  useEffect(() => {
    ref.current = value;
  });
  return ref.current;
}

export function TableView({ publicState, seats, potDistributions }: TableViewProps) {
  // Refs for animation source/destination position lookup. Keyed by seat
  // index for players and the literal string "pot" for the pot tile.
  const tileRefs = useRef<Map<string, HTMLDivElement | null>>(new Map());
  // Active chip flights — transient elements rendered by ChipFlightOverlay.
  const [flights, setFlights] = useState<ChipFlight[]>([]);
  const removeFlight = (id: string) =>
    setFlights((curr) => curr.filter((f) => f.id !== id));

  // ---- Transition detection ----
  const prev = usePrevious(publicState);

  // Helper: get the page-coordinate center of a ref'd element.
  const centerOf = (key: string): { x: number; y: number } | null => {
    const el = tileRefs.current.get(key);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  };

  // Detect street-end "chips → pot" collection. Triggers when a player's
  // street_committed went from positive to zero and the pot grew. We
  // approximate by firing one flight per player who had committed chips
  // last frame but doesn't this frame, regardless of whether pot_total
  // grew exactly that amount (side pots, rake-free math). Cheap and reads
  // right at a glance.
  useEffect(() => {
    if (!prev || !publicState) return;
    if (prev.hand_id !== publicState.hand_id) return; // new hand, skip
    const newFlights: ChipFlight[] = [];
    for (let i = 0; i < publicState.players.length; i++) {
      const before = prev.players[i];
      const now = publicState.players[i];
      if (!before || !now) continue;
      // street_committed cleared (street ended).
      if (before.street_committed > 0 && now.street_committed === 0) {
        const from = centerOf(`player-${i}-chips`);
        const to = centerOf("pot");
        if (from && to) {
          newFlights.push({
            id: `street-${publicState.hand_id}-${i}-${before.street_committed}-${Date.now()}`,
            fromX: from.x,
            fromY: from.y,
            toX: to.x,
            toY: to.y,
            amount: before.street_committed,
            kind: "to_pot",
          });
        }
      }
    }
    if (newFlights.length > 0) {
      setFlights((curr) => [...curr, ...newFlights]);
    }
  // The dependency on prev means this runs once per publicState change.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [publicState]);

  // Detect winner payout: when potDistributions becomes non-null (hand
  // just completed), fly chips from the pot to each winner.
  //
  // ALSO, at the same moment, fly any remaining street_committed chips
  // from each player to the pot. The engine doesn't clear street_committed
  // when resolving a hand, so river-bet chips visually stay in front of
  // players. Without this, those chips just disappear when the next hand
  // starts. Firing chip→pot flights here completes the visual story:
  // chips go from players → pot → winner.
  const prevDistsLength = useRef(0);
  useEffect(() => {
    const currLen = potDistributions?.length ?? 0;
    if (currLen > 0 && prevDistsLength.current === 0 && publicState) {
      const potCenter = centerOf("pot");
      if (!potCenter) {
        prevDistsLength.current = currLen;
        return;
      }
      const newFlights: ChipFlight[] = [];

      // Phase 1: any unresolved river chips → pot.
      for (let i = 0; i < publicState.players.length; i++) {
        const p = publicState.players[i];
        if (!p || p.street_committed <= 0) continue;
        const from = centerOf(`player-${i}-chips`);
        if (!from) continue;
        newFlights.push({
          id: `final-collect-${publicState.hand_id}-${i}-${Date.now()}`,
          fromX: from.x,
          fromY: from.y,
          toX: potCenter.x,
          toY: potCenter.y,
          amount: p.street_committed,
          kind: "to_pot",
        });
      }

      // Phase 2: pot → winners.
      for (const dist of potDistributions!) {
        const eachAmount = Math.floor(dist.amount / Math.max(1, dist.winners.length));
        for (const w of dist.winners) {
          const seatIdx = publicState.players.findIndex(
            (p) => p != null && p.id === w.player_id,
          );
          if (seatIdx < 0) continue;
          const to = centerOf(`player-${seatIdx}`);
          if (!to) continue;
          newFlights.push({
            id: `payout-${publicState.hand_id}-${w.player_id}-${Date.now()}`,
            fromX: potCenter.x,
            fromY: potCenter.y,
            toX: to.x,
            toY: to.y,
            amount: eachAmount,
            kind: "from_pot",
          });
        }
      }

      if (newFlights.length > 0) {
        setFlights((curr) => [...curr, ...newFlights]);
      }
    }
    prevDistsLength.current = currLen;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [potDistributions]);

  // ---- Render ----

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

  // Phase-flash key: changes when the board grows, triggering a brief
  // overlay via AnimatePresence below.
  const boardKey = `phase-${publicState.hand_id}-${publicState.board.length}`;

  return (
    <>
      <ChipFlightOverlay flights={flights} onComplete={removeFlight} />
      <div
        className="table-frame"
        style={{
          position: "relative",
          background: "#143027",
          border: "1px solid #2a4d3f",
          borderRadius: 16,
          padding: "2rem 1rem",
          minHeight: 400,
          overflow: "hidden",
        }}
      >
        {/* Phase flash: brief overlay each time a new street comes out. Keyed
            on board length so it remounts (and re-animates) on each phase. */}
        <AnimatePresence>
          <motion.div
            key={boardKey}
            initial={{ opacity: 0 }}
            animate={{ opacity: [0, 0.18, 0] }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.5 }}
            style={{
              position: "absolute",
              inset: 0,
              background: "radial-gradient(circle at center, #f5c542 0%, transparent 70%)",
              pointerEvents: "none",
            }}
          />
        </AnimatePresence>

        {/* Board */}
        <div
          style={{
            display: "flex",
            justifyContent: "center",
            gap: "0.5rem",
            marginBottom: "1rem",
            minHeight: 70,
            position: "relative",
          }}
        >
          {publicState.board.length === 0 ? (
            <div style={{ opacity: 0.5, padding: "1rem" }}>
              {publicState.phase === "pre_flop" ? "Pre-flop" : publicState.phase}
            </div>
          ) : (
            publicState.board.map((c, i) => (
              <AnimatedCardView
                key={`${publicState.hand_id}-board-${i}`}
                card={c}
                highlighted={winningCards.has(c)}
                indexInGroup={i}
              />
            ))
          )}
        </div>

        {/* Pot */}
        <div
          ref={(el) => { tileRefs.current.set("pot", el); }}
          style={{ textAlign: "center", marginBottom: "1.5rem" }}
        >
          <div style={{ opacity: 0.7, fontSize: "0.85rem" }}>Pot</div>
          <motion.div
            key={totalPot}
            initial={{ scale: 1 }}
            animate={{ scale: [1, 1.08, 1] }}
            transition={{ duration: 0.35 }}
            style={{ fontSize: "1.4rem", fontWeight: 600 }}
          >
            {totalPot}
          </motion.div>
        </div>

        {/* Players */}
        <div
          className="table-players"
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
              <motion.div
                key={i}
                ref={(el) => { tileRefs.current.set(`player-${i}`, el); }}
                animate={
                  isToAct
                    ? {
                        // Subtle breathing pulse on the actor.
                        boxShadow: [
                          "0 0 0px rgba(245, 197, 66, 0)",
                          "0 0 14px rgba(245, 197, 66, 0.55)",
                          "0 0 0px rgba(245, 197, 66, 0)",
                        ],
                      }
                    : { boxShadow: "0 0 0px rgba(0,0,0,0)" }
                }
                transition={
                  isToAct
                    ? { duration: 1.6, repeat: Infinity, ease: "easeInOut" }
                    : { duration: 0.2 }
                }
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
                    {isToAct && publicState.to_act_deadline_unix_ms && (
                      <CountdownBadge
                        deadlineUnixMs={publicState.to_act_deadline_unix_ms}
                      />
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
                  Stack: <AnimatedNumber value={p.stack} />
                </div>
                <div style={{ fontSize: "0.75rem", opacity: 0.6 }}>
                  {p.status} {p.last_action ? `· ${p.last_action}` : ""}
                </div>
                {/* Chips in front of player on this street. AnimatePresence
                    handles the entry/exit so the chip badge swoops in when
                    a bet is placed and fades when the street collects. */}
                <div
                  ref={(el) => { tileRefs.current.set(`player-${i}-chips`, el); }}
                  style={{ minHeight: 28, marginTop: "0.4rem" }}
                >
                  <AnimatePresence>
                    {p.street_committed > 0 && (
                      <motion.div
                        key="chips"
                        initial={{ opacity: 0, y: -10, scale: 0.6 }}
                        animate={{ opacity: 1, y: 0, scale: 1 }}
                        exit={{ opacity: 0, scale: 0.6 }}
                        transition={{ duration: 0.25, ease: "easeOut" }}
                        style={{
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
                      </motion.div>
                    )}
                  </AnimatePresence>
                </div>
                {/* Showdown reveal */}
                {p.hole && (
                  <div style={{ display: "flex", gap: "0.25rem", marginTop: "0.4rem" }}>
                    {p.hole.map((c, j) => (
                      <AnimatedCardView
                        key={`${publicState.hand_id}-${p.id}-hole-${j}`}
                        card={c}
                        small
                        highlighted={winningCards.has(c)}
                        indexInGroup={j}
                      />
                    ))}
                  </div>
                )}
                {/* Winner's hand description, shown after showdown */}
                {descs.length > 0 && (
                  <motion.div
                    initial={{ opacity: 0, y: 4 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.4, delay: 0.2 }}
                    style={{
                      marginTop: "0.4rem",
                      fontSize: "0.75rem",
                      color: "#4fd682",
                      fontWeight: 600,
                    }}
                  >
                    {descs.join(" · ")}
                  </motion.div>
                )}
              </motion.div>
            );
          })}
        </div>
      </div>
    </>
  );
}

/**
 * Number display that briefly highlights when its value changes. Pure
 * visual feedback for stack changes during a hand.
 */
function AnimatedNumber({ value }: { value: number }) {
  return (
    <motion.span
      key={value}
      initial={{ color: "#f5c542" }}
      animate={{ color: "#ffffffcc" }}
      transition={{ duration: 0.8 }}
      style={{ fontVariantNumeric: "tabular-nums" }}
    >
      {value}
    </motion.span>
  );
}

/**
 * Card with a slide-in animation on mount. `indexInGroup` staggers
 * adjacent cards (flop deals 3 cards in quick succession).
 */
function AnimatedCardView({
  card,
  small = false,
  highlighted = false,
  indexInGroup = 0,
}: {
  card: string;
  small?: boolean;
  highlighted?: boolean;
  indexInGroup?: number;
}) {
  // Memoize the parsed parts so re-renders don't recompute.
  const parts = useMemo(() => {
    const rank = card[0];
    const suit = card[1];
    const isRed = suit === "h" || suit === "d";
    const suitChar =
      ({ s: "♠", h: "♥", d: "♦", c: "♣" } as Record<string, string>)[suit] ?? suit;
    return { rank, suit, isRed, suitChar };
  }, [card]);
  const w = small ? 32 : 48;
  const h = small ? 46 : 68;
  return (
    <motion.div
      initial={{ opacity: 0, y: -30, rotateY: 90 }}
      animate={{ opacity: 1, y: 0, rotateY: 0 }}
      transition={{
        duration: 0.35,
        delay: indexInGroup * 0.12,
        ease: "easeOut",
      }}
      style={{
        width: w,
        height: h,
        background: "#fff",
        color: parts.isRed ? "#c33" : "#222",
        border: highlighted ? "2px solid #4fd682" : "1px solid #888",
        boxShadow: highlighted ? "0 0 8px rgba(79, 214, 130, 0.7)" : "none",
        borderRadius: 6,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        fontWeight: 700,
        fontSize: small ? "0.75rem" : "1rem",
        transformStyle: "preserve-3d",
      }}
    >
      <div>{parts.rank}</div>
      <div style={{ fontSize: small ? "1rem" : "1.4rem", lineHeight: 1 }}>
        {parts.suitChar}
      </div>
    </motion.div>
  );
}

/** Static export used by HoleCards (player's own non-animated cards). */
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
        border: highlighted ? "2px solid #4fd682" : "1px solid #888",
        boxShadow: highlighted ? "0 0 8px rgba(79, 214, 130, 0.7)" : "none",
        borderRadius: 6,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        fontWeight: 700,
        fontSize: small ? "0.75rem" : "1rem",
      }}
    >
      <div>{rank}</div>
      <div style={{ fontSize: small ? "1rem" : "1.4rem", lineHeight: 1 }}>{suitChar}</div>
    </div>
  );
}

function CountdownBadge({ deadlineUnixMs }: { deadlineUnixMs: number }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(id);
  }, []);
  const remaining = Math.max(0, Math.ceil((deadlineUnixMs - now) / 1000));
  const urgent = remaining <= 5;
  return (
    <span
      style={{
        marginLeft: "0.4rem",
        fontSize: "0.7rem",
        fontWeight: 700,
        padding: "0.1rem 0.45rem",
        borderRadius: 999,
        background: urgent ? "#a33" : "#0e1f1a",
        color: urgent ? "#fff" : "#7fb8a4",
        border: `1px solid ${urgent ? "#c66" : "#2a4d3f"}`,
        fontVariantNumeric: "tabular-nums",
      }}
      title={urgent ? "Time bank in use" : "Action timer"}
    >
      {remaining}s
    </span>
  );
}
