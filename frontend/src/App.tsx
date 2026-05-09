import { useEffect, useState } from "react";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useSession } from "./lib/useSession";

export function App() {
  const { handle, session, loading, signOut, refresh } = useSession();
  const [authError, setAuthError] = useState<string | null>(null);
  const [signingIn, setSigningIn] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();

  // Pick up auth_error from a failed OAuth redirect.
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const err = params.get("auth_error");
    if (err) {
      setAuthError(err);
      // Strip the query param from the URL so a refresh doesn't reshow the error.
      navigate(location.pathname, { replace: true });
    }
  }, [location, navigate]);

  // After landing back from /auth/callback the cookie is set; refresh once.
  // (callback redirects to /, so this fires on lobby mount.)
  useEffect(() => {
    if (!loading && !handle) {
      // Try once more in case the cookie just landed and useEffect ran early.
      const t = window.setTimeout(refresh, 100);
      return () => window.clearTimeout(t);
    }
  }, [loading, handle, refresh]);

  const startSignIn = async () => {
    setSigningIn(true);
    setAuthError(null);
    try {
      // Round-trip the user's current location through OAuth so they land
      // back where they started after signing in. Skip the trip if they're
      // already at the root.
      const here = location.pathname + location.search;
      const nextParam =
        here && here !== "/" ? `?next=${encodeURIComponent(here)}` : "";
      const res = await fetch(`/auth/login${nextParam}`, {
        credentials: "include",
      });
      if (!res.ok) {
        const txt = await res.text();
        setAuthError(`Sign-in unavailable: ${txt}`);
        setSigningIn(false);
        return;
      }
      const { authorize_url } = (await res.json()) as { authorize_url: string };
      window.location.href = authorize_url;
    } catch (e) {
      setAuthError(`Network error: ${(e as Error).message}`);
      setSigningIn(false);
    }
  };

  if (loading) {
    return (
      <div
        style={{
          minHeight: "100vh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          opacity: 0.6,
        }}
      >
        Loading…
      </div>
    );
  }

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
        <div style={{ display: "grid", gap: "1rem", maxWidth: 360, textAlign: "center" }}>
          <h1 style={{ margin: 0 }}>Spaces Poker</h1>
          <p style={{ margin: 0, opacity: 0.75, fontSize: "0.95rem" }}>
            Sign in with your X account to play. Your X handle is your identity at the table.
          </p>
          {authError && (
            <div
              style={{
                padding: "0.6rem 0.8rem",
                border: "1px solid #a33",
                borderRadius: 6,
                color: "#f99",
                fontSize: "0.85rem",
                textAlign: "left",
              }}
            >
              {authError}
            </div>
          )}
          <button
            onClick={startSignIn}
            disabled={signingIn}
            style={{
              padding: "0.75rem 1rem",
              fontSize: "1rem",
              fontWeight: 600,
              background: "#000",
              color: "#fff",
              border: "1px solid #444",
              borderRadius: 6,
              cursor: signingIn ? "wait" : "pointer",
            }}
          >
            {signingIn ? "Redirecting…" : "Sign in with 𝕏"}
          </button>
          <p style={{ margin: 0, opacity: 0.5, fontSize: "0.75rem" }}>
            We read your username and that's it. Play money only.
          </p>
        </div>
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
          <span style={{ fontSize: "0.9rem", opacity: 0.85 }}>
            @{handle}
            {session && (
              <span style={{ marginLeft: "0.5rem", opacity: 0.6 }}>
                ({session.balance.toLocaleString()} chips)
              </span>
            )}
          </span>
          <button
            onClick={signOut}
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
