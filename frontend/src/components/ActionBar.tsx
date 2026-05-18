import { useEffect, useState } from "react";
import type {
  ActionType,
  LegalAction,
  PrivateState,
  PublicState,
} from "../lib/types";
import { ActionTimer } from "./ActionTimer";

interface ActionBarProps {
  publicState: PublicState | null;
  privateState: PrivateState | null;
  myHandle: string | null;
  onAction: (action: ActionType, amount?: number) => void;
}

/**
 * Test for client-side consistency between privateState and publicState.
 * Returns true if it's safe to render the action bar with these states.
 *
 * The states get out of sync briefly between when state_update arrives
 * (updating publicState) and when the next private arrives (updating
 * privateState). During that window we want to hide the action bar
 * rather than risk the user clicking a stale button.
 *
 * Two independent invariants the consistent state must satisfy:
 *
 * 1. publicState.to_act[0] === myHandle. Server's view of who's acting
 *    matches our private's `your_turn`.
 *
 * 2. The legals match what the engine would compute for the current
 *    public state:
 *      - to_call == 0 ↔ CHECK is legal, CALL is not.
 *      - to_call >  0 ↔ CALL is legal, CHECK is not.
 *      - current_bet == 0 ↔ BET is legal, RAISE is not.
 *      - current_bet >  0 ↔ RAISE is legal, BET is not.
 *
 *    Note that the BB option pre-flop is a legitimate state where
 *    current_bet > 0 (= big blind) AND CHECK is legal (because to_call
 *    is 0 — the BB has already matched their own blind). That's why we
 *    need to_call separately, not just current_bet.
 *
 * Any drift → hide the action bar. The fresh private will arrive in a
 * few ms and re-enable it with the right buttons.
 *
 * Exported so tests can exercise it directly.
 */
export function isStateConsistent(
  publicState: PublicState,
  legals: LegalAction[],
  yourTurn: boolean,
  myHandle: string | null,
): boolean {
  if (!yourTurn) return true; // we're not acting, no mismatch can hurt us
  const me = (myHandle ?? "");
  if (publicState.to_act[0] !== me) return false;

  // Compute to_call from our own street_committed in the public state.
  const myPlayer = publicState.players.find((p) => p !== null && p.id === me);
  if (!myPlayer) return false; // we're somehow not in the public state
  const toCall = publicState.current_bet - myPlayer.street_committed;

  const hasBet = legals.some((a) => a.action_type === "bet");
  const hasRaise = legals.some((a) => a.action_type === "raise");
  const hasCheck = legals.some((a) => a.action_type === "check");
  const hasCall = legals.some((a) => a.action_type === "call");

  // CHECK vs CALL (driven by to_call)
  if (toCall <= 0 && hasCall) return false;
  if (toCall > 0 && hasCheck) return false;
  // BET vs RAISE (driven by current_bet)
  if (publicState.current_bet === 0 && hasRaise) return false;
  if (publicState.current_bet > 0 && hasBet) return false;

  return true;
}

export function ActionBar({
  publicState, privateState, myHandle, onAction,
}: ActionBarProps) {
  const legals = privateState?.legal_actions ?? [];
  const yourTurn = privateState?.your_turn ?? false;
  // Numeric source of truth for the bet/raise amount. Always a valid number.
  const [betAmount, setBetAmount] = useState<number>(0);
  // Raw text in the number input. Tracked separately so the user can type
  // multi-digit numbers without the controlled-input value snapping back
  // to `min` on each keystroke. Synced from `betAmount` when the field
  // isn't focused, so external changes (presets, slider) reflect here.
  const [betInputText, setBetInputText] = useState<string>("");
  const [numberInputFocused, setNumberInputFocused] = useState(false);

  // Compute the legal range and clamped amount up front, before any
  // conditional returns, so the hooks below see the same values on every
  // render. Legals come from privateState (may be empty if not your turn).
  const bet = legals.find((a) => a.action_type === "bet");
  const raise = legals.find((a) => a.action_type === "raise");
  const sizer = bet ?? raise;
  const minSize = sizer?.min_amount ?? 0;
  const maxSize = sizer?.max_amount ?? 0;
  // The amount we'd submit if the user clicks Bet/Raise *right now*.
  // Clamped to the legal range. Used for the Bet/Raise button labels and
  // for the slider's `value` (sliders are inherently clamped controls).
  const clampedAmount = Math.min(maxSize, Math.max(minSize, betAmount || minSize));

  // Reset bet amount when it's no longer your turn (so the slider starts fresh next time).
  useEffect(() => {
    if (!yourTurn) {
      setBetAmount(0);
      setBetInputText("");
    }
  }, [yourTurn, publicState?.hand_id]);

  // When a preset/slider changes betAmount, mirror the new value into the
  // text-input buffer so the next time the user focuses the number field
  // they see the up-to-date value.
  //
  // IMPORTANT: this useEffect must stay above the conditional early-return
  // below. If it moved down, the hook would be skipped when !yourTurn and
  // React would throw error #310 (rendered fewer hooks than expected).
  useEffect(() => {
    if (!numberInputFocused) {
      setBetInputText(String(clampedAmount));
    }
  }, [clampedAmount, numberInputFocused]);

  const stateConsistent =
    !publicState || isStateConsistent(publicState, legals, yourTurn, myHandle);

  if (!publicState || !yourTurn || legals.length === 0 || !stateConsistent) {
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

  // The number input's visible value. While the field is focused, we show
  // exactly what the user typed (even invalid intermediate states like
  // "" or "3"). When the field is blurred, we show the clamped amount.
  const numberInputValue = numberInputFocused
    ? betInputText
    : String(clampedAmount);

  const pot = publicState.pot_total;
  const presets = [
    { label: "½ pot", amount: Math.round(pot * 0.5) },
    { label: "¾ pot", amount: Math.round(pot * 0.75) },
    { label: "Pot", amount: pot },
    { label: "All in", amount: maxSize },
  ];

  return (
    <div
      className="action-bar"
      style={{
        padding: "1rem",
        border: "2px solid #f5c542",
        borderRadius: 8,
        display: "grid",
        gap: "0.75rem",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ fontSize: "0.85rem", opacity: 0.85 }}>Your action</span>
        <ActionTimer
          baseDeadlineUnixMs={privateState?.base_deadline_unix_ms ?? null}
          bankDeadlineUnixMs={privateState?.bank_deadline_unix_ms ?? null}
          actionTimerSeconds={privateState?.action_timer_seconds ?? null}
        />
      </div>
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
                onClick={() =>
                  setBetAmount(Math.min(maxSize, Math.max(minSize, p.amount)))
                }
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
            step={1}
            value={numberInputValue}
            onFocus={(e) => {
              setNumberInputFocused(true);
              // Select the existing value so the user can start typing
              // immediately without manually clearing the field.
              e.target.select();
            }}
            onChange={(e) => {
              // While focused, take the raw string. Don't snap to min —
              // that's the bug we're fixing. Parsing happens on blur.
              setBetInputText(e.target.value);
              // Also update betAmount as we go, but only if the text
              // parses to a non-NaN integer. This keeps the slider and
              // button labels in sync with what you're typing without
              // clobbering the input field. Clamping for submission
              // happens via `clampedAmount` at click time.
              const parsed = Number(e.target.value);
              if (!Number.isNaN(parsed)) {
                setBetAmount(Math.floor(parsed));
              }
            }}
            onBlur={() => {
              setNumberInputFocused(false);
              // On blur, commit a clean clamped integer value. If the
              // user typed something invalid (empty, NaN, decimal,
              // out-of-range), they'll see the legal value pop in.
              setBetAmount(clampedAmount);
              setBetInputText(String(clampedAmount));
            }}
            onKeyDown={(e) => {
              // Enter submits the current bet/raise. Better UX than
              // Tab-then-click for keyboard-driven players.
              if (e.key === "Enter") {
                e.currentTarget.blur();
                if (bet) onAction("bet", clampedAmount);
                else if (raise) onAction("raise", clampedAmount);
              }
            }}
          />
          <div style={{ fontSize: "0.85rem", opacity: 0.7 }}>
            Min {minSize} · Max {maxSize}
          </div>
        </div>
      )}
    </div>
  );
}
