import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import type {
  ActionType,
  PublicState,
  ServerMessage,
  SeatInfo,
} from "../lib/types";
import {
  applyMessage,
  emptyLogState,
  type EventLogState,
} from "../lib/eventLog";
import { TableView } from "../components/TableView";
import { EventLog } from "../components/EventLog";

interface ReplayAction {
  sequence: number;
  user_id: string;
  handle: string | null;
  action_type: string;
  amount: number;
  at: string | null;
}

interface ReplaySnapshot {
  action: {
    sequence: number;
    player_id: string;
    action_type: ActionType;
    amount: number;
    auto: boolean;
  } | null;
  public_state: PublicState;
}

interface ReplayResponse {
  hand_id: string;
  table_id: string;
  hand_number: number;
  deck_seed_commit: string;
  deck_seed_reveal: string;
  start_state: PublicState | null;
  final_state: PublicState | null;
  started_at: string | null;
  actions: ReplayAction[];
  snapshots: ReplaySnapshot[] | null;
}

const PLAYBACK_INTERVAL_MS = 1500;
const EMPTY_SEATS: (SeatInfo | null)[] = [];

export function ReplayPage() {
  const { hand_id } = useParams();
  const [replay, setReplay] = useState<ReplayResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [step, setStep] = useState(0);
  const [playing, setPlaying] = useState(false);

  // Fetch the replay on mount.
  useEffect(() => {
    if (!hand_id) return;
    let cancelled = false;
    setReplay(null);
    setError(null);
    fetch(`/api/tables/hands/${encodeURIComponent(hand_id)}/replay`, {
      credentials: "include",
    })
      .then(async (res) => {
        if (!res.ok) {
          const txt = await res.text();
          throw new Error(`${res.status}: ${txt}`);
        }
        return res.json();
      })
      .then((data: ReplayResponse) => {
        if (cancelled) return;
        setReplay(data);
        setStep(0);
      })
      .catch((e) => {
        if (cancelled) return;
        setError((e as Error).message);
      });
    return () => {
      cancelled = true;
    };
  }, [hand_id]);

  const snapshots = replay?.snapshots ?? null;
  const totalSteps = snapshots?.length ?? 0;
  const lastStep = Math.max(0, totalSteps - 1);

  // Auto-play timer. Advance one step every PLAYBACK_INTERVAL_MS while
  // playing; stop at the last snapshot.
  useEffect(() => {
    if (!playing || totalSteps === 0) return;
    if (step >= lastStep) {
      setPlaying(false);
      return;
    }
    const t = window.setTimeout(() => {
      setStep((s) => Math.min(s + 1, lastStep));
    }, PLAYBACK_INTERVAL_MS);
    return () => window.clearTimeout(t);
  }, [playing, step, lastStep, totalSteps]);

  // Build an EventLogState from snapshots[0..step]. Recomputed whenever
  // the current step changes — pure derivation, cheap for typical hand
  // lengths (<20 actions).
  const logState = useMemo<EventLogState>(() => {
    if (!snapshots || snapshots.length === 0) return emptyLogState();
    let s = emptyLogState();
    for (let i = 0; i <= step && i < snapshots.length; i++) {
      const snap = snapshots[i];
      const msg = snapshotToServerMessage(snap, i);
      if (msg) s = applyMessage(s, msg, null);
    }
    return s;
  }, [snapshots, step]);

  // Keyboard shortcuts: space toggles play, arrow keys step.
  const stepRef = useRef(step);
  stepRef.current = step;
  const onKey = useCallback(
    (e: KeyboardEvent) => {
      if (totalSteps === 0) return;
      if (e.target instanceof HTMLInputElement) return;
      if (e.key === " ") {
        e.preventDefault();
        setPlaying((p) => !p);
      } else if (e.key === "ArrowLeft") {
        setPlaying(false);
        setStep((s) => Math.max(0, s - 1));
      } else if (e.key === "ArrowRight") {
        setPlaying(false);
        setStep((s) => Math.min(lastStep, s + 1));
      } else if (e.key === "Home") {
        setPlaying(false);
        setStep(0);
      } else if (e.key === "End") {
        setPlaying(false);
        setStep(lastStep);
      }
    },
    [totalSteps, lastStep],
  );
  useEffect(() => {
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onKey]);

  if (error) {
    return (
      <div style={{ padding: "1rem", color: "#e05050" }}>
        <h2>Replay unavailable</h2>
        <p>{error}</p>
        <Link to="/">← Back to lobby</Link>
      </div>
    );
  }

  if (!replay) {
    return <div style={{ padding: "1rem", opacity: 0.6 }}>Loading replay…</div>;
  }

  // Old hands without start_state: snapshots will be null. Fall back to
  // narration-only view built from the action list alone.
  if (!snapshots) {
    return <NarrationOnlyReplay replay={replay} />;
  }

  const currentSnap = snapshots[Math.min(step, snapshots.length - 1)];
  const atEnd = step >= lastStep;

  return (
    <div style={{ display: "grid", gap: "1rem" }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: "1rem",
          flexWrap: "wrap",
        }}
      >
        <h2 style={{ margin: 0 }}>
          Replay: hand #{replay.hand_number}
          <span
            style={{
              marginLeft: "0.75rem",
              fontSize: "0.7rem",
              padding: "0.2rem 0.5rem",
              background: "#1f2228",
              border: "1px solid #2a2e36",
              borderRadius: 999,
              opacity: 0.85,
              textTransform: "uppercase",
              letterSpacing: "0.05em",
            }}
          >
            replay
          </span>
        </h2>
        <Link to="/" style={{ fontSize: "0.85rem" }}>
          ← Lobby
        </Link>
      </div>

      <TableView publicState={currentSnap.public_state} seats={EMPTY_SEATS} />

      <ReplayControls
        step={step}
        totalSteps={totalSteps}
        playing={playing}
        atEnd={atEnd}
        onStepChange={(s) => {
          setPlaying(false);
          setStep(s);
        }}
        onPlayToggle={() => {
          if (atEnd) {
            setStep(0);
            setPlaying(true);
          } else {
            setPlaying((p) => !p);
          }
        }}
        onStepBackward={() => {
          setPlaying(false);
          setStep((s) => Math.max(0, s - 1));
        }}
        onStepForward={() => {
          setPlaying(false);
          setStep((s) => Math.min(lastStep, s + 1));
        }}
      />

      <EventLog entries={logState.entries} />

      <details style={{ fontSize: "0.85rem", opacity: 0.75 }}>
        <summary style={{ cursor: "pointer" }}>Verification</summary>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "auto 1fr",
            gap: "0.4rem 0.75rem",
            marginTop: "0.5rem",
            fontFamily:
              "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
            fontSize: "0.75rem",
          }}
        >
          <span>Hand ID</span>
          <span style={{ wordBreak: "break-all" }}>{replay.hand_id}</span>
          <span>Deck commit</span>
          <span style={{ wordBreak: "break-all" }}>
            {replay.deck_seed_commit}
          </span>
          <span>Deck seed</span>
          <span style={{ wordBreak: "break-all" }}>
            {replay.deck_seed_reveal}
          </span>
          <span>Started</span>
          <span>{replay.started_at ?? "—"}</span>
        </div>
      </details>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Translate a stored snapshot into the same ServerMessage shape the live
// event log builder consumes. This lets us reuse all narration logic.
// ---------------------------------------------------------------------------

function snapshotToServerMessage(
  snap: ReplaySnapshot,
  index: number,
): ServerMessage | null {
  if (index === 0) {
    // Initial deal.
    return { type: "hand_started", state: snap.public_state };
  }
  if (snap.public_state.phase === "complete") {
    return { type: "hand_complete", state: snap.public_state };
  }
  return {
    type: "state_update",
    state: snap.public_state,
    action: snap.action ?? undefined,
  };
}

// ---------------------------------------------------------------------------
// Playback controls (scrubber + buttons)
// ---------------------------------------------------------------------------

interface ReplayControlsProps {
  step: number;
  totalSteps: number;
  playing: boolean;
  atEnd: boolean;
  onStepChange: (n: number) => void;
  onPlayToggle: () => void;
  onStepBackward: () => void;
  onStepForward: () => void;
}

function ReplayControls({
  step,
  totalSteps,
  playing,
  atEnd,
  onStepChange,
  onPlayToggle,
  onStepBackward,
  onStepForward,
}: ReplayControlsProps) {
  const lastStep = Math.max(0, totalSteps - 1);
  return (
    <div
      style={{
        display: "grid",
        gap: "0.6rem",
        padding: "0.75rem",
        border: "1px solid #2a2e36",
        borderRadius: 8,
        background: "#0e1116",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "0.5rem",
          justifyContent: "center",
        }}
      >
        <ControlButton
          onClick={onStepBackward}
          disabled={step <= 0}
          title="Step back (←)"
        >
          ⏮
        </ControlButton>
        <ControlButton
          onClick={onPlayToggle}
          title={
            atEnd ? "Restart from beginning" : playing ? "Pause (space)" : "Play (space)"
          }
          primary
        >
          {atEnd ? "↻ replay" : playing ? "⏸ pause" : "▶ play"}
        </ControlButton>
        <ControlButton
          onClick={onStepForward}
          disabled={step >= lastStep}
          title="Step forward (→)"
        >
          ⏭
        </ControlButton>
        <span
          style={{
            marginLeft: "0.5rem",
            fontSize: "0.8rem",
            opacity: 0.65,
            fontFamily: "ui-monospace, monospace",
          }}
        >
          {step + 1}/{totalSteps}
        </span>
      </div>
      <input
        type="range"
        min={0}
        max={lastStep}
        value={step}
        onChange={(e) => onStepChange(Number(e.target.value))}
        style={{ width: "100%" }}
      />
    </div>
  );
}

function ControlButton({
  onClick,
  children,
  disabled,
  title,
  primary,
}: {
  onClick: () => void;
  children: React.ReactNode;
  disabled?: boolean;
  title?: string;
  primary?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      style={{
        padding: primary ? "0.4rem 0.9rem" : "0.4rem 0.6rem",
        fontSize: "0.85rem",
        fontWeight: primary ? 600 : 400,
        background: primary ? "#1a3a30" : "transparent",
        color: "inherit",
        border: "1px solid #2a4d3f",
        borderRadius: 6,
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.4 : 1,
      }}
    >
      {children}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Narration-only fallback for old hands without start_state
// ---------------------------------------------------------------------------

function NarrationOnlyReplay({ replay }: { replay: ReplayResponse }) {
  return (
    <div style={{ display: "grid", gap: "1rem" }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <h2 style={{ margin: 0 }}>Replay: hand #{replay.hand_number}</h2>
        <Link to="/" style={{ fontSize: "0.85rem" }}>
          ← Lobby
        </Link>
      </div>
      <div
        style={{
          padding: "0.75rem",
          border: "1px solid #2a4d3f",
          borderRadius: 8,
          background: "#143027",
          fontSize: "0.85rem",
          opacity: 0.85,
        }}
      >
        Interactive playback isn't available for this hand (it was recorded
        before the snapshot capture was added). Showing the action list only.
      </div>
      <ol
        style={{
          fontFamily: "ui-monospace, monospace",
          fontSize: "0.85rem",
          paddingLeft: "1.5rem",
          display: "grid",
          gap: "0.25rem",
        }}
      >
        {replay.actions.map((a) => (
          <li key={a.sequence}>
            <span style={{ opacity: 0.6 }}>#{a.sequence}</span>{" "}
            <strong>@{a.handle ?? a.user_id.slice(0, 8)}</strong>{" "}
            {a.action_type}
            {a.amount > 0 ? ` ${a.amount}` : ""}
          </li>
        ))}
      </ol>
    </div>
  );
}
