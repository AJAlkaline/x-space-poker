import { useState } from "react";
import { Link, Outlet } from "react-router-dom";
import { useHandle } from "./lib/useHandle";

export function App() {
  const { handle, save, clear } = useHandle();
  const [draft, setDraft] = useState("");

  if (!handle) {
    return (
      <div
        style={{
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: "1rem",
        }}
      >
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const trimmed = draft.trim();
            if (/^[A-Za-z0-9_]{2,20}$/.test(trimmed)) save(trimmed);
          }}
          style={{ display: "grid", gap: "0.75rem", maxWidth: 360 }}
        >
          <h1 style={{ margin: 0 }}>Spaces Poker</h1>
          <p style={{ margin: 0, opacity: 0.7, fontSize: "0.9rem" }}>
            Choose a handle for this session. Path A uses fake auth — pick anything,
            it'll be your identity until you clear browser storage.
          </p>
          <input
            placeholder="handle (2-20 chars, A-Z 0-9 _)"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            autoFocus
          />
          <button type="submit">Continue</button>
        </form>
      </div>
    );
  }

  return (
    <div style={{ minHeight: "100vh", display: "flex", flexDirection: "column" }}>
      <header
        style={{
          padding: "1rem 1.5rem",
          borderBottom: "1px solid #2a2e36",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
        }}
      >
        <Link to="/" style={{ textDecoration: "none", color: "inherit" }}>
          <h1 style={{ margin: 0, fontSize: "1.2rem" }}>Spaces Poker</h1>
        </Link>
        <div style={{ display: "flex", alignItems: "center", gap: "1rem" }}>
          <span style={{ fontSize: "0.9rem", opacity: 0.8 }}>@{handle}</span>
          <button
            onClick={clear}
            style={{ fontSize: "0.8rem", padding: "0.25rem 0.6rem" }}
          >
            Sign out
          </button>
        </div>
      </header>
      <main style={{ flex: 1, padding: "1.5rem" }}>
        <Outlet />
      </main>
    </div>
  );
}
