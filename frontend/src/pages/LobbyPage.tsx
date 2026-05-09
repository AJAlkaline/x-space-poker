import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useSession as useHandle } from "../lib/useSession";

export function LobbyPage() {
  const navigate = useNavigate();
  const { handle } = useHandle();
  const [joinCode, setJoinCode] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleJoin = (e: React.FormEvent) => {
    e.preventDefault();
    if (joinCode.trim().length >= 4) {
      navigate(`/table/${joinCode.trim().toUpperCase()}`);
    }
  };

  const handleCreate = async () => {
    if (!handle) return;
    setCreating(true);
    setError(null);
    try {
      const res = await fetch(`/api/tables?as=${encodeURIComponent(handle)}`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ small_blind: 5, big_blind: 10, max_seats: 9 }),
      });
      if (!res.ok) throw new Error(await res.text());
      const data = (await res.json()) as { table_id: string; code: string };
      navigate(`/table/${data.code}`);
    } catch (err) {
      setError(`Failed to create table: ${err}`);
    } finally {
      setCreating(false);
    }
  };

  return (
    <div style={{ maxWidth: 480, margin: "0 auto", display: "grid", gap: "2rem" }}>
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

      <section>
        <h2>Join a table</h2>
        <form onSubmit={handleJoin} style={{ display: "flex", gap: "0.5rem" }}>
          <input
            placeholder="Table code (e.g. ABC234)"
            value={joinCode}
            onChange={(e) => setJoinCode(e.target.value.toUpperCase())}
            maxLength={8}
            style={{ flex: 1 }}
          />
          <button type="submit" disabled={joinCode.length < 4}>
            Join
          </button>
          <button
            type="button"
            disabled={joinCode.length < 4}
            onClick={() => navigate(`/spectate/${joinCode.trim().toUpperCase()}`)}
            title="Watch without joining a seat"
          >
            Watch
          </button>
        </form>
        <div style={{ fontSize: "0.85rem", opacity: 0.6, marginTop: "0.4rem" }}>
          Join to play; Watch to spectate without taking a seat.
        </div>
      </section>

      <section>
        <h2>Host a new table</h2>
        <p style={{ opacity: 0.7, fontSize: "0.9rem" }}>
          Creates a 5/10 NL Hold'em cash table with a shareable code.
        </p>
        <button onClick={handleCreate} disabled={creating}>
          {creating ? "Creating..." : "Create table"}
        </button>
      </section>
    </div>
  );
}
