import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { useHandle } from "../lib/useHandle";
import { useTableSocket } from "../lib/useTableSocket";
import type {
  PrivateState,
  PublicState,
  SeatInfo,
  ServerMessage,
} from "../lib/types";
import { TableView } from "../components/TableView";
import { ActionBar } from "../components/ActionBar";
import { HoleCards } from "../components/HoleCards";

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
  const [error, setError] = useState<string | null>(null);
  const [joinPending, setJoinPending] = useState(false);

  const handleMessage = useCallback((msg: ServerMessage) => {
    switch (msg.type) {
      case "hand_started":
        setPublicState(msg.state);
        setPrivateState(null);
        setError(null);
        break;
      case "state_update":
      case "hand_complete":
        setPublicState(msg.state);
        setError(null);
        break;
      case "private":
        setPrivateState(msg.state);
        break;
      case "seats":
        setSeats(msg.seats);
        break;
      case "illegal_action":
      case "table_error":
        setError(msg.error);
        break;
    }
  }, []);

  const { send, status } = useTableSocket({
    code: code ?? "",
    handle: handle ?? "",
    onMessage: handleMessage,
  });

  // Determine if I'm seated. Check the seats snapshot first; fall back to the
  // current public state's player list in case the seats message hasn't arrived
  // yet but a hand has already started with me in it.
  const inSeats = seats.findIndex((s) => s != null && s.user_id === handle) >= 0;
  const inPublic =
    publicState?.players.some((p) => p != null && p.id === handle) ?? false;
  const seated = inSeats || inPublic;

  // Clear private state if I'm not seated (e.g. after busting out).
  useEffect(() => {
    if (!seated) setPrivateState(null);
  }, [seated]);

  const join = async (seatNumber: number) => {
    if (!handle || !code) return;
    setJoinPending(true);
    setError(null);
    try {
      const res = await fetch(`/api/tables/join?as=${encodeURIComponent(handle)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code, seat: seatNumber, buy_in: DEFAULT_BUY_IN }),
      });
      if (!res.ok) throw new Error(await res.text());
    } catch (err) {
      setError(`Join failed: ${err}`);
    } finally {
      setJoinPending(false);
    }
  };

  if (!code) return <div>Missing table code.</div>;

  return (
    <div style={{ display: "grid", gap: "1rem" }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <h2 style={{ margin: 0 }}>Table {code}</h2>
        <span style={{ fontSize: "0.85rem", opacity: 0.7 }}>{status}</span>
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

      {!seated && (
        <SeatPicker
          seats={seats}
          maxSeats={MAX_SEATS}
          onPick={join}
          disabled={joinPending || status !== "open"}
        />
      )}

      <TableView publicState={publicState} seats={seats} />

      {seated && <HoleCards privateState={privateState} />}

      {seated && (
        <ActionBar
          publicState={publicState}
          privateState={privateState}
          onAction={(action, amount) => send({ type: "action", action, amount })}
        />
      )}
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
