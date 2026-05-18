import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import type { PrivateState } from "../lib/types";
import { CardView } from "./TableView";

interface HoleCardsProps {
  privateState: PrivateState | null;
}

export function HoleCards({ privateState }: HoleCardsProps) {
  // Use the small card variant on mobile so the strip is more compact.
  // Listen for viewport changes so rotation updates the layout.
  const [small, setSmall] = useState(() =>
    typeof window !== "undefined" &&
    window.matchMedia?.("(max-width: 640px)").matches,
  );
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mq = window.matchMedia("(max-width: 640px)");
    const handler = (e: MediaQueryListEvent) => setSmall(e.matches);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);
  if (!privateState?.hole) return null;
  const cards = privateState.hole;
  // Re-key by card identity so when a new hand deals new cards, the
  // components remount and re-animate from the initial state.
  const dealKey = cards.join("|");
  const currentHand = privateState.current_hand;
  // The set of card strings that compose the best 5 — we'll highlight
  // those in the hole-card display so the player can see at a glance
  // which of their cards are "live" in their current best hand.
  const winningCards = new Set(currentHand?.best_five ?? []);
  return (
    <div
      className="hole-cards"
      style={{
        display: "flex",
        alignItems: "center",
        gap: "0.75rem",
        padding: "0.75rem 1rem",
        background: "#1a1d23",
        border: "1px solid #2a2e36",
        borderRadius: 8,
        flexWrap: "wrap",
      }}
    >
      <span style={{ fontSize: "0.85rem", opacity: 0.7 }}>Your hand</span>
      <div style={{ display: "flex", gap: "0.35rem" }}>
        {cards.map((c, i) => (
          <motion.div
            key={`${dealKey}-${i}`}
            initial={{ opacity: 0, y: -30, rotateY: 90 }}
            animate={{ opacity: 1, y: 0, rotateY: 0 }}
            transition={{
              duration: 0.4,
              delay: i * 0.15,
              ease: "easeOut",
            }}
            style={{ transformStyle: "preserve-3d" }}
          >
            <CardView card={c} highlighted={winningCards.has(c)} small={small} />
          </motion.div>
        ))}
      </div>
      <AnimatePresence mode="popLayout">
        {currentHand && (
          <motion.div
            key={currentHand.description}
            initial={{ opacity: 0, x: -8 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.3 }}
            style={{
              fontSize: "0.85rem",
              color: "#7fb8a4",
              fontWeight: 600,
              marginLeft: "0.25rem",
            }}
          >
            {currentHand.description}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
