import { useCallback, useState } from "react";
import { useParams } from "react-router-dom";
import { useTableSocket } from "../lib/useTableSocket";
import type { PrivateState, PublicState, ServerMessage } from "../lib/types";
import { TableView } from "../components/TableView";
import { ActionBar } from "../components/ActionBar";

export function TablePage() {
  const { code } = useParams();
  const [publicState, setPublicState] = useState<PublicState | null>(null);
  const [privateState, setPrivateState] = useState<PrivateState | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleMessage = useCallback((msg: ServerMessage) => {
    switch (msg.type) {
      case "hand_started":
      case "state_update":
        setPublicState(msg.state);
        setError(null);
        break;
      case "private":
        setPrivateState(msg.state);
        break;
      case "illegal_action":
        setError(msg.error);
        break;
    }
  }, []);

  const wsUrl = code ? `/ws/tables/${code}` : null;
  const { send, status } = useTableSocket({ url: wsUrl, onMessage: handleMessage });

  if (!code) return <div>Missing table code.</div>;

  return (
    <div style={{ display: "grid", gap: "1rem" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
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

      <TableView publicState={publicState} privateState={privateState} />

      <ActionBar
        publicState={publicState}
        privateState={privateState}
        onAction={(action, amount) => send({ type: "action", action, amount })}
      />
    </div>
  );
}
