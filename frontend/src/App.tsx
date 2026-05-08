import { Link, Outlet } from "react-router-dom";

export function App() {
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
        <div style={{ fontSize: "0.85rem", opacity: 0.7 }}>play money · no cash value</div>
      </header>
      <main style={{ flex: 1, padding: "1.5rem" }}>
        <Outlet />
      </main>
    </div>
  );
}
