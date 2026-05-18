import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Link, useParams } from "react-router-dom";
import { useSession as useHandle } from "../lib/useSession";
import { useTableSocket } from "../lib/useTableSocket";
import type {
  PotDistribution,
  PrivateState,
  PublicState,
  SeatInfo,
  ServerMessage,
} from "../lib/types";
import {
  applyMessage,
  emptyLogState,
  type EventLogState,
} from "../lib/eventLog";
import { TableView } from "../components/TableView";
import { ActionBar } from "../components/ActionBar";
import { HoleCards } from "../components/HoleCards";
import { EventLog } from "../components/EventLog";

const MAX_SEATS = 9;
const DEFAULT_BUY_IN = 1000;

export function TablePage() {
  const { code } = useParams();
  const { handle } = useHandle();
  const [publicState, setPublicState] = useState<PublicState | null>(null);
  const [privateState, setPrivateState] = useState<PrivateState | null>(null);
  const [seats, setSeats] = useState<(SeatInfo | null)[]>(
    Array.from({ length: MAX_SEATS }, () => null)
  );
  const [viewerCount, setViewerCount] = useState(0);
  const [error, setError] = useState<string | null>(null);
  // Transient success message (top-off applied, bought in, etc.). Renders
  // as a green banner alongside the error slot and auto-dismisses so it
  // doesn't pile up. We use a number key so back-to-back identical
  // notices still trigger the animation/dismiss cycle.
  const [notice, setNotice] = useState<{ id: number; text: string } | null>(null);
  const [joinPending, setJoinPending] = useState(false);
  const [logState, setLogState] = useState<EventLogState>(emptyLogState);
  // Most recent hand's pot distributions. Lives through the inter-hand pause
  // so winners and winning cards stay highlighted until the next deal.
  const [potDistributions, setPotDistributions] =
    useState<PotDistribution[] | null>(null);
  // Absolute deadline (ms since epoch) for the next hand auto-start. Set
  // on hand_complete, cleared on hand_started/hand_aborted. If the deadline
  // expires without a hand_started, the loop is blocked waiting for more
  // eligible players to sit — we transition the banner to a "waiting"
  // message in that case.
  const [nextHandAt, setNextHandAt] = useState<number | null>(null);
  // Track the seat the player most recently occupied. When they bust out,
  // we use this to offer a one-click "Buy back in" at the same seat.
  const lastSeatRef = useRef<number | null>(null);

  const handleMessage = useCallback(
    (msg: ServerMessage) => {
      // Feed every message into the event log builder. The builder itself
      // is pure; we use the functional setState form so we always get the
      // latest log state even if multiple messages arrive in quick succession.
      setLogState((prev) => applyMessage(prev, msg, handle));

      switch (msg.type) {
        case "hand_started":
          setPublicState(msg.state);
          setPrivateState(null);
          setPotDistributions(null);  // clear any previous-hand highlights
          setNextHandAt(null);
          setError(null);
          break;
        case "state_update":
          setPublicState(msg.state);
          setError(null);
          break;
        case "hand_complete":
          setPublicState(msg.state);
          setPotDistributions(msg.pot_distributions ?? []);
          setNextHandAt(msg.next_hand_starts_at_unix_ms ?? null);
          setError(null);
          break;
        case "hand_aborted":
          setPublicState(null);
          setPrivateState(null);
          setPotDistributions(null);
          setNextHandAt(null);
          break;
        case "private":
          setPrivateState(msg.state);
          break;
        case "seats":
          setSeats(msg.seats);
          break;
        case "viewer_count":
          setViewerCount(msg.count);
          break;
        case "illegal_action":
        case "table_error":
          setError(msg.error);
          break;
      }
    },
    [handle],
  );

  const { send, status } = useTableSocket({
    code: code ?? "",
    handle: handle ?? "",
    onMessage: handleMessage,
  });

  // Determine if I'm seated. Check the seats snapshot first; fall back to the
  // current public state's player list in case the seats message hasn't arrived
  // yet but a hand has already started with me in it.
  //
  // The `stack > 0` filter on inPublic matters at hand-complete: a busted
  // player still appears in publicState.players with stack=0 (the engine
  // keeps them for the final snapshot), but the backend has already removed
  // them from rt.seats. Without this filter, `seated` stays true through
  // the inter-hand pause, the TopOffBar renders, and the user's "buy back
  // in" click hits /api/tables/top_off which rejects with "not seated".
  // With the filter, `seated` flips to false at bust, so RebuyCTA shows
  // instead.
  const inSeats = seats.findIndex((s) => s != null && s.user_id === handle) >= 0;
  const inPublic =
    publicState?.players.some(
      (p) => p != null && p.id === handle && p.stack > 0,
    ) ?? false;
  const seated = inSeats || inPublic;

  // Remember the most recent seat I occupied so we can offer a one-click
  // "Buy back in" at the same seat after busting. Updates whenever seated
  // transitions to true.
  useEffect(() => {
    if (seated && handle) {
      const mySeat = seats.findIndex((s) => s != null && s.user_id === handle);
      if (mySeat >= 0) lastSeatRef.current = mySeat;
      else {
        // Fall back to public state if seats snapshot hasn't caught up.
        const inPub = publicState?.players.find((p) => p != null && p.id === handle);
        if (inPub) lastSeatRef.current = inPub.seat;
      }
    }
  }, [seated, seats, publicState, handle]);

  // Has the player previously occupied a seat and is now busted/unseated?
  // Differentiate "first arrival" from "I just lost my chips" so we can
  // show a re-buy CTA instead of forcing them through the full seat picker.
  const previousSeat = lastSeatRef.current;
  const seatStillOpen =
    previousSeat !== null && previousSeat < MAX_SEATS && seats[previousSeat] == null;
  const offerRebuy = !seated && previousSeat !== null && seatStillOpen;

  // Hand-in-progress predicate. We don't have an explicit phase between
  // hands, but `phase` clears to a non-complete value during a hand and
  // hand_complete leaves it set to "complete" until the next deal.
  const handInProgress =
    publicState != null && publicState.phase !== "complete";

  // I'm "waiting to sit" if I reserved a seat (in `seats`) but I'm not
  // dealt into the current hand (not in `publicState.players`). This
  // happens when a hand was already running when I joined. The seat is
  // mine for the next deal, but I'm a spectator until then.
  const waitingForNextHand = inSeats && handInProgress && !inPublic;

  // Number of seats that are eligible to be dealt into the next hand:
  // occupied, not sitting out, and stack > 0. The backend run loop gates
  // on this same predicate (≥ 2 to deal). Used by the inter-hand status
  // banner to decide between "next hand in Xs" and "waiting for players".
  const eligibleSeatsCount = seats.filter(
    (s) => s != null && !s.sitting_out && s.stack > 0,
  ).length;

  // My current public player (used for stack readouts, top-off button).
  const myPublicPlayer =
    publicState?.players.find((p) => p != null && p.id === handle) ?? null;
  // My seat's stack as known by the table — visible even between hands
  // when myPublicPlayer is null.
  const mySeatStack = seats.find((s) => s != null && s.user_id === handle)?.stack ?? null;
  const myStack = myPublicPlayer?.stack ?? mySeatStack ?? 0;

  // Top-off cap is 200 × big_blind. The table's big_blind is visible on
  // any public state we've seen.
  const bigBlind = publicState?.big_blind ?? 10;
  const maxStack = 200 * bigBlind;

  // Clear private state if I'm not seated (e.g. after busting out).
  useEffect(() => {
    if (!seated) setPrivateState(null);
  }, [seated]);

  // Auto-dismiss the success notice after a short delay. The `id` in
  // the dependency restarts the timer when a new notice replaces an
  // existing one.
  useEffect(() => {
    if (!notice) return;
    const t = window.setTimeout(() => setNotice(null), 3000);
    return () => window.clearTimeout(t);
  }, [notice?.id]);

  const showNotice = useCallback((text: string) => {
    setNotice({ id: Date.now(), text });
  }, []);

  // Top-off endpoint — POST then let the seats event do the UI update.
  const topOff = async (amount: number) => {
    if (!handle || !code || amount <= 0) return;
    setError(null);
    try {
      const res = await fetch(`/api/tables/top_off?as=${encodeURIComponent(handle)}`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code, amount }),
      });
      if (!res.ok) {
        const text = await res.text();
        setError(`Top off failed: ${text}`);
        return;
      }
      // The response body is `{table_id, stack}`. Surface the new stack
      // so the player sees the confirmation immediately, without waiting
      // for the seats event to round-trip.
      const data = (await res.json().catch(() => null)) as { stack?: number } | null;
      const newStack = data?.stack;
      showNotice(
        newStack !== undefined
          ? `Topped off +${amount}. Stack: ${newStack}.`
          : `Topped off +${amount}.`,
      );
    } catch (err) {
      setError(`Top off failed: ${err}`);
    }
  };

  const join = async (seatNumber: number) => {
    if (!handle || !code) return;
    setJoinPending(true);
    setError(null);
    try {
      const res = await fetch(`/api/tables/join?as=${encodeURIComponent(handle)}`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code, seat: seatNumber, buy_in: DEFAULT_BUY_IN }),
      });
      if (!res.ok) throw new Error(await res.text());
      showNotice(`Bought in for ${DEFAULT_BUY_IN} at seat ${seatNumber + 1}.`);
    } catch (err) {
      setError(`Join failed: ${err}`);
    } finally {
      setJoinPending(false);
    }
  };

  if (!code) return <div>Missing table code.</div>;

  return (
    <div className="table-page-stack" style={{ display: "grid", gap: "1rem" }}>
      <div
        className="page-header"
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: "0.5rem",
        }}
      >
        <h2 style={{ margin: 0 }}>Table {code}</h2>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "0.75rem",
            flexWrap: "wrap",
          }}
        >
          <NarrationLink code={code ?? ""} />
          {viewerCount > 0 && (
            <span
              style={{
                fontSize: "0.85rem",
                opacity: 0.85,
                padding: "0.2rem 0.6rem",
                background: "#1f2228",
                border: "1px solid #2a2e36",
                borderRadius: 999,
              }}
              title="Total viewers (players + spectators)"
            >
              👁 {viewerCount}
            </span>
          )}
          <span style={{ fontSize: "0.85rem", opacity: 0.7 }}>{status}</span>
        </div>
      </div>

      {error && (
        <div
          style={{
            padding: "0.5rem 1rem",
            border: "1px solid #a33",
            borderRadius: 6,
            color: "#f99",
          }}
        >
          {error}
        </div>
      )}

      <AnimatePresence>
        {notice && (
          <motion.div
            key={notice.id}
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ duration: 0.2 }}
            style={{
              padding: "0.5rem 1rem",
              border: "1px solid #4fd682",
              background: "#0e1f1a",
              borderRadius: 6,
              color: "#cfe6dd",
              fontSize: "0.9rem",
              display: "flex",
              alignItems: "center",
              gap: "0.5rem",
            }}
          >
            <span style={{ color: "#4fd682", fontWeight: 700 }}>✓</span>
            <span>{notice.text}</span>
          </motion.div>
        )}
      </AnimatePresence>

      {!seated && offerRebuy && (
        <RebuyCTA
          seatNumber={previousSeat!}
          buyIn={DEFAULT_BUY_IN}
          onRebuy={() => join(previousSeat!)}
          onPickOtherSeat={() => {
            // Clear the previous seat memory so the full picker shows.
            lastSeatRef.current = null;
            // Force re-render — touch a state that re-evaluates the CTA gate.
            setError(null);
          }}
          disabled={joinPending || status !== "open"}
        />
      )}

      {!seated && !offerRebuy && (
        <SeatPicker
          seats={seats}
          maxSeats={MAX_SEATS}
          onPick={join}
          disabled={joinPending || status !== "open"}
        />
      )}

      <TableView
        publicState={publicState}
        seats={seats}
        potDistributions={potDistributions}
      />

      {waitingForNextHand && (
        <div
          style={{
            padding: "0.6rem 0.9rem",
            border: "1px solid #7fb8a4",
            background: "#143027",
            borderRadius: 6,
            color: "#cfe6dd",
            fontSize: "0.9rem",
            display: "flex",
            alignItems: "center",
            gap: "0.5rem",
          }}
        >
          <span style={{ fontSize: "1.1rem" }}>⏳</span>
          <span>
            You're seated for the next hand. Waiting for the current one
            to finish.
          </span>
        </div>
      )}

      {seated && !handInProgress && (
        <InterHandStatus
          deadlineUnixMs={nextHandAt}
          eligibleSeatsCount={eligibleSeatsCount}
        />
      )}

      {seated && (
        <TopOffBar
          myStack={myStack}
          bigBlind={bigBlind}
          maxStack={maxStack}
          // Top-off is blocked by the server only when I'm an active
          // participant in the current hand. If I'm waiting for the next
          // hand, sitting out, or the hand is between deals, it's allowed.
          canTopOff={!(myPublicPlayer && handInProgress)}
          handInProgress={handInProgress}
          onTopOff={topOff}
        />
      )}

      {seated && <HoleCards privateState={privateState} />}

      {seated && (
        <ActionBar
          publicState={publicState}
          privateState={privateState}
          myHandle={handle}
          onAction={(action, amount) => send({ type: "action", action, amount })}
        />
      )}

      <EventLog entries={logState.entries} />

      <RecentHands code={code ?? ""} />
    </div>
  );
}

interface AudioStatus {
  narration_enabled: boolean;
  listener_count: number;
  tts_configured: boolean;
}

function NarrationLink({ code }: { code: string }) {
  const [status, setStatus] = useState<AudioStatus | null>(null);

  useEffect(() => {
    if (!code) return;
    let cancelled = false;
    const fetchStatus = async () => {
      try {
        const res = await fetch(`/api/audio/${encodeURIComponent(code)}/status`);
        if (cancelled) return;
        if (res.ok) {
          setStatus(await res.json());
        } else {
          // 404 = table doesn't exist or narration not enabled. Either way,
          // don't render.
          setStatus(null);
        }
      } catch {
        // Network error — fail silent, the link isn't critical.
        setStatus(null);
      }
    };
    fetchStatus();
    // Refresh occasionally so the listener count stays roughly fresh.
    const id = window.setInterval(fetchStatus, 10000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [code]);

  if (!status?.narration_enabled) return null;

  const title = status.tts_configured
    ? `Live AI commentary. ${status.listener_count} listening.`
    : "AI commentary enabled (text only — TTS not configured on server).";

  return (
    <Link
      to={`/audio/${code}`}
      title={title}
      style={{
        fontSize: "0.8rem",
        padding: "0.2rem 0.6rem",
        background: status.tts_configured ? "#1a3a30" : "#332f1a",
        border: `1px solid ${status.tts_configured ? "#2a4d3f" : "#665a2a"}`,
        borderRadius: 999,
        textDecoration: "none",
        color: "inherit",
      }}
    >
      🔊 narration
      {status.listener_count > 0 && (
        <span style={{ marginLeft: "0.35rem", opacity: 0.7 }}>
          · {status.listener_count}
        </span>
      )}
    </Link>
  );
}

interface HandSummary {
  hand_id: string;
  hand_number: number;
  started_at: string | null;
}

type RecentHandsState =
  | { status: "idle" }                            // never fetched
  | { status: "loading" }
  | { status: "ok"; hands: HandSummary[] }
  | { status: "disabled" }                        // backend has persistence off
  | { status: "error"; message: string };

function RecentHands({ code }: { code: string }) {
  const [state, setState] = useState<RecentHandsState>({ status: "idle" });
  const [open, setOpen] = useState(false);

  const refresh = useCallback(async () => {
    if (!code) return;
    setState({ status: "loading" });
    try {
      const res = await fetch(
        `/api/tables/${encodeURIComponent(code)}/hands`,
        { credentials: "include" },
      );
      if (res.status === 503) {
        setState({ status: "disabled" });
        return;
      }
      if (!res.ok) {
        const text = await res.text();
        setState({
          status: "error",
          message: `${res.status}: ${text || res.statusText}`,
        });
        return;
      }
      const data = (await res.json()) as { hands: HandSummary[] };
      setState({ status: "ok", hands: data.hands });
    } catch (e) {
      setState({ status: "error", message: (e as Error).message });
    }
  }, [code]);

  useEffect(() => {
    if (open && state.status === "idle") refresh();
  }, [open, state.status, refresh]);

  if (!code) return null;

  const handCount =
    state.status === "ok" ? state.hands.length : null;

  return (
    <details
      open={open}
      onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}
      style={{
        padding: "0.5rem 0.75rem",
        border: "1px solid #2a2e36",
        borderRadius: 8,
        fontSize: "0.85rem",
      }}
    >
      <summary style={{ cursor: "pointer", userSelect: "none" }}>
        Recent hands at this table
        {handCount !== null && handCount > 0 && (
          <span style={{ marginLeft: "0.5rem", opacity: 0.5 }}>
            ({handCount})
          </span>
        )}
      </summary>
      <div style={{ marginTop: "0.5rem" }}>
        {state.status === "loading" && (
          <div style={{ opacity: 0.6 }}>Loading…</div>
        )}
        {state.status === "error" && (
          <div style={{ color: "#e05050", fontSize: "0.8rem" }}>
            {state.message}
          </div>
        )}
        {state.status === "disabled" && (
          <div style={{ opacity: 0.7, fontSize: "0.8rem" }}>
            Hand history is unavailable: persistence is disabled on the server.
            Set <code>PERSISTENCE_ENABLED=true</code> to enable it.
          </div>
        )}
        {state.status === "ok" && state.hands.length === 0 && (
          <div style={{ opacity: 0.6, fontStyle: "italic" }}>
            No completed hands yet.
          </div>
        )}
        {state.status === "ok" && state.hands.length > 0 && (
          <ul
            style={{
              margin: 0,
              paddingLeft: "1.25rem",
              display: "grid",
              gap: "0.2rem",
            }}
          >
            {state.hands.map((h) => (
              <li key={h.hand_id}>
                <Link to={`/replay/${h.hand_id}`}>Hand #{h.hand_number}</Link>
                {h.started_at && (
                  <span style={{ opacity: 0.5, marginLeft: "0.5rem" }}>
                    {formatTimestamp(h.started_at)}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
        <button
          onClick={refresh}
          disabled={state.status === "loading"}
          style={{
            marginTop: "0.5rem",
            fontSize: "0.75rem",
            padding: "0.2rem 0.5rem",
          }}
        >
          {state.status === "loading" ? "Loading…" : "Refresh"}
        </button>
      </div>
    </details>
  );
}

function formatTimestamp(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString();
  } catch {
    return iso;
  }
}

/**
 * Status banner between hands, shown to seated players only.
 *
 * Three states:
 *   - Countdown:  deadline is in the future → "Next hand in Ns…"
 *   - Waiting:    deadline missing OR expired AND fewer than 2 eligible
 *                 players are seated → "Waiting for more players…"
 *   - Dealing:    deadline expired but enough eligible players → loop is
 *                 about to publish hand_started; brief transient.
 *
 * `eligibleSeatsCount` mirrors the backend gate (`len(eligible) >= 2`)
 * — see [backend/app/services/table_manager.py:480-491].
 */
function InterHandStatus({
  deadlineUnixMs,
  eligibleSeatsCount,
}: {
  deadlineUnixMs: number | null;
  eligibleSeatsCount: number;
}) {
  // Tick every 500ms so the countdown second-resolution feels responsive
  // without burning CPU. Unmount stops the interval.
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(id);
  }, []);

  const hasDeadline = deadlineUnixMs != null && deadlineUnixMs > 0;
  const remainingMs = hasDeadline ? deadlineUnixMs! - now : 0;
  const countingDown = hasDeadline && remainingMs > 0;
  const enoughPlayers = eligibleSeatsCount >= 2;

  let icon: string;
  let text: string;
  let border: string;
  if (countingDown) {
    const secs = Math.max(1, Math.ceil(remainingMs / 1000));
    icon = "⏱";
    text = `Next hand in ${secs}s…`;
    border = "#7fb8a4";
  } else if (!enoughPlayers) {
    const need = Math.max(0, 2 - eligibleSeatsCount);
    icon = "👥";
    text = need === 1
      ? "Waiting for 1 more player to sit down…"
      : "Waiting for players to sit down…";
    border = "#c89c3a";
  } else {
    // Deadline passed and we have enough players — hand_started is
    // imminent. Brief transient banner so the UI doesn't go blank.
    icon = "🃏";
    text = "Dealing next hand…";
    border = "#7fb8a4";
  }

  return (
    <div
      style={{
        padding: "0.6rem 0.9rem",
        border: `1px solid ${border}`,
        background: "#143027",
        borderRadius: 6,
        color: "#cfe6dd",
        fontSize: "0.9rem",
        display: "flex",
        alignItems: "center",
        gap: "0.5rem",
      }}
    >
      <span style={{ fontSize: "1.1rem" }}>{icon}</span>
      <span>{text}</span>
    </div>
  );
}

function RebuyCTA({
  seatNumber,
  buyIn,
  onRebuy,
  onPickOtherSeat,
  disabled,
}: {
  seatNumber: number;
  buyIn: number;
  onRebuy: () => void;
  onPickOtherSeat: () => void;
  disabled: boolean;
}) {
  return (
    <div
      style={{
        padding: "1rem",
        border: "1px solid #c89c3a",
        background: "#2a230f",
        borderRadius: 8,
        display: "grid",
        gap: "0.6rem",
      }}
    >
      <div style={{ fontWeight: 600 }}>You're out of chips at this table.</div>
      <div style={{ opacity: 0.75, fontSize: "0.85rem" }}>
        Buy back in for {buyIn} chips at seat {seatNumber + 1}, or pick a different seat.
      </div>
      <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
        <button
          onClick={onRebuy}
          disabled={disabled}
          style={{
            padding: "0.5rem 1rem",
            background: "#c89c3a",
            color: "#000",
            border: 0,
            borderRadius: 6,
            fontWeight: 600,
            cursor: disabled ? "not-allowed" : "pointer",
          }}
        >
          Buy back in (seat {seatNumber + 1})
        </button>
        <button
          onClick={onPickOtherSeat}
          disabled={disabled}
          style={{
            padding: "0.5rem 1rem",
            background: "transparent",
            color: "inherit",
            border: "1px solid #2a2e36",
            borderRadius: 6,
            cursor: disabled ? "not-allowed" : "pointer",
          }}
        >
          Pick a different seat
        </button>
      </div>
    </div>
  );
}

function SeatPicker({
  seats,
  maxSeats,
  onPick,
  disabled,
}: {
  seats: (SeatInfo | null)[];
  maxSeats: number;
  onPick: (n: number) => void;
  disabled: boolean;
}) {
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
      <div>Pick a seat to sit down (buy-in {DEFAULT_BUY_IN}):</div>
      <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
        {Array.from({ length: maxSeats }).map((_, i) => {
          const occupied = seats[i] != null;
          return (
            <button
              key={i}
              onClick={() => onPick(i)}
              disabled={disabled || occupied}
              style={{ minWidth: 80 }}
            >
              {occupied ? `${seats[i]!.user_id}` : `Seat ${i + 1}`}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function TopOffBar({
  myStack,
  bigBlind,
  maxStack,
  canTopOff,
  handInProgress,
  onTopOff,
}: {
  myStack: number;
  bigBlind: number;
  maxStack: number;
  canTopOff: boolean;
  handInProgress: boolean;
  onTopOff: (amount: number) => void;
}) {
  // The amount that would bring you back up to the cap.
  const headroom = Math.max(0, maxStack - myStack);
  // Hide entirely when there's no headroom (already at max).
  if (headroom === 0) return null;
  // Suggested quick amount: round up the headroom needed to fill to max,
  // capped at the actual headroom. For UX, also offer "Fill to max".
  const oneBuyIn = Math.min(headroom, 100 * bigBlind);
  return (
    <div
      className="top-off-bar"
      style={{
        padding: "0.5rem 0.9rem",
        border: "1px solid #2a4d3f",
        borderRadius: 6,
        background: "#0e1f1a",
        display: "flex",
        flexWrap: "wrap",
        alignItems: "center",
        gap: "0.75rem",
        fontSize: "0.85rem",
      }}
    >
      <span style={{ opacity: 0.75 }}>
        Your stack: <strong style={{ opacity: 1 }}>{myStack}</strong>{" "}
        <span style={{ opacity: 0.5 }}>/ {maxStack}</span>
      </span>
      <div style={{ flex: 1 }} />
      {canTopOff ? (
        <>
          {oneBuyIn > 0 && oneBuyIn < headroom && (
            <button
              onClick={() => onTopOff(oneBuyIn)}
              style={{
                padding: "0.35rem 0.75rem",
                background: "transparent",
                border: "1px solid #2a4d3f",
                color: "inherit",
                borderRadius: 6,
                fontSize: "0.85rem",
                cursor: "pointer",
              }}
            >
              + {oneBuyIn}
            </button>
          )}
          <button
            onClick={() => onTopOff(headroom)}
            style={{
              padding: "0.35rem 0.85rem",
              background: "#c89c3a",
              color: "#000",
              border: 0,
              borderRadius: 6,
              fontSize: "0.85rem",
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            Fill to {maxStack}
          </button>
        </>
      ) : (
        <span style={{ fontSize: "0.8rem", opacity: 0.6, fontStyle: "italic" }}>
          {handInProgress
            ? "Top off available between hands"
            : "Top off unavailable"}
        </span>
      )}
    </div>
  );
}
