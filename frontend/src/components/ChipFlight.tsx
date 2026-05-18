import { AnimatePresence, motion } from "framer-motion";

export interface ChipFlight {
  /** Stable ID so React can reconcile and AnimatePresence can fire exit. */
  id: string;
  /** Page-coordinate origin and destination (from getBoundingClientRect). */
  fromX: number;
  fromY: number;
  toX: number;
  toY: number;
  /** Amount label shown on the flying chip stack. */
  amount: number;
  /** Style hint: "to_pot" (street-end collection) uses neutral chip color;
   *  "from_pot" (winner payout) uses winner green; "bet" (mid-action bet
   *  appearing in front of player) uses the actor's gold. */
  kind: "to_pot" | "from_pot" | "bet";
}

/**
 * Renders the active chip flights as fixed-positioned overlays that
 * animate from `from` to `to` and then unmount.
 *
 * The renderer takes a list of flights; the caller (ChipFlightProvider in
 * TableView) decides when to add/remove them. Each flight is keyed by its
 * stable id, so the same id staying in the list across renders keeps the
 * element mounted (its animation continues smoothly).
 */
export function ChipFlightOverlay({
  flights,
  onComplete,
}: {
  flights: ChipFlight[];
  onComplete: (id: string) => void;
}) {
  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        pointerEvents: "none",
        zIndex: 9999,
      }}
    >
      <AnimatePresence>
        {flights.map((f) => (
          <motion.div
            key={f.id}
            initial={{
              x: f.fromX,
              y: f.fromY,
              opacity: 0,
              scale: 0.6,
            }}
            animate={{
              x: f.toX,
              y: f.toY,
              opacity: 1,
              scale: 1,
            }}
            exit={{ opacity: 0, scale: 0.6 }}
            transition={{
              duration: f.kind === "bet" ? 0.25 : 0.55,
              ease: "easeOut",
              // Pot→winner flights wait for street-end flights to arrive
              // first, so the visual story reads as "chips collect in pot,
              // pot flies to winner."
              delay: f.kind === "from_pot" ? 0.5 : 0,
            }}
            onAnimationComplete={() => {
              // Schedule removal slightly after the animation completes so
              // the exit transition can play.
              window.setTimeout(() => onComplete(f.id), 250);
            }}
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              // Center the chip element on its coordinate by offsetting by
              // half its expected size. Setting `transform: translate(-50%,
              // -50%)` here would conflict with Framer Motion's `x`/`y`
              // props which animate the transform themselves.
              marginLeft: -20,
              marginTop: -12,
              padding: "0.15rem 0.55rem",
              borderRadius: 999,
              fontSize: "0.85rem",
              fontWeight: 700,
              fontVariantNumeric: "tabular-nums",
              color: "#000",
              background:
                f.kind === "from_pot"
                  ? "#4fd682"
                  : f.kind === "bet"
                    ? "#f5c542"
                    : "#d1b56b",
              boxShadow: "0 2px 8px rgba(0,0,0,0.4)",
              whiteSpace: "nowrap",
            }}
          >
            {f.amount > 0 ? f.amount : ""}
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}
