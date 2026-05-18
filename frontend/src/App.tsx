import { useEffect, useState } from "react";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useSession } from "./lib/useSession";

interface AuthConfig {
  auth_mode: "fake" | "x_oauth" | "both";
  oauth_available: boolean;
  fake_auth_enabled: boolean;
}

export function App() {
  const { handle, session, loading, signOut, refresh } = useSession();
  const [authError, setAuthError] = useState<string | null>(null);
  const [signingIn, setSigningIn] = useState(false);
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [fakeHandle, setFakeHandle] = useState("");
  const [fakeSubmitting, setFakeSubmitting] = useState(false);
  const location = useLocation();
  const navigate = useNavigate();

  // Fetch auth config on mount so we know which paths to render.
  useEffect(() => {
    fetch("/auth/config", { credentials: "include" })
      .then((r) => r.json())
      .then((data: AuthConfig) => setConfig(data))
      .catch(() => {
        // If we can't reach the server at all, leave config null and let the
        // sign-in screen render a generic error if the user clicks anything.
      });
  }, []);

  // Pick up auth_error from a failed OAuth redirect.
  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const err = params.get("auth_error");
    if (err) {
      setAuthError(err);
      navigate(location.pathname, { replace: true });
    }
  }, [location, navigate]);

  // After landing back from /auth/callback the cookie is set; refresh once.
  useEffect(() => {
    if (!loading && !handle) {
      const t = window.setTimeout(refresh, 100);
      return () => window.clearTimeout(t);
    }
  }, [loading, handle, refresh]);

  const startSignIn = async () => {
    setSigningIn(true);
    setAuthError(null);
    try {
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

  const submitFakeLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = fakeHandle.trim();
    if (!/^[A-Za-z0-9_]{2,20}$/.test(trimmed)) {
      setAuthError("Handle must be 2-20 chars: letters, numbers, underscore.");
      return;
    }
    setFakeSubmitting(true);
    setAuthError(null);
    try {
      const res = await fetch("/auth/fake-login", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ handle: trimmed }),
      });
      if (!res.ok) {
        const txt = await res.text();
        setAuthError(`Login failed: ${txt}`);
        setFakeSubmitting(false);
        return;
      }
      // Cookie is now set; refresh session.
      await refresh();
    } catch (e) {
      setAuthError(`Network error: ${(e as Error).message}`);
    } finally {
      setFakeSubmitting(false);
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
    // Decide which sign-in paths to show based on server config.
    const showOauth = config?.oauth_available ?? false;
    const showFake = config?.fake_auth_enabled ?? false;
    const showBothLabel = showOauth && showFake;

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
        <div style={{ display: "grid", gap: "1rem", maxWidth: 380, textAlign: "center" }}>
          <h1 style={{ margin: 0 }}>Spaces Poker</h1>

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

          {showOauth && (
            <>
              <p style={{ margin: 0, opacity: 0.75, fontSize: "0.95rem" }}>
                Sign in with your X account to play. Your X handle is your
                identity at the table.
              </p>
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
            </>
          )}

          {showBothLabel && (
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: "0.5rem",
                margin: "0.25rem 0",
                opacity: 0.5,
              }}
            >
              <div style={{ flex: 1, height: 1, background: "#2a2e36" }} />
              <span style={{ fontSize: "0.75rem" }}>or, in dev</span>
              <div style={{ flex: 1, height: 1, background: "#2a2e36" }} />
            </div>
          )}

          {showFake && (
            <form
              onSubmit={submitFakeLogin}
              style={{ display: "grid", gap: "0.5rem", textAlign: "left" }}
            >
              {!showOauth && (
                <p
                  style={{
                    margin: 0,
                    opacity: 0.7,
                    fontSize: "0.85rem",
                    textAlign: "center",
                  }}
                >
                  Pick a handle to use for this session. Dev/test mode —
                  no real authentication.
                </p>
              )}
              <input
                placeholder="handle (2-20 chars, A-Z 0-9 _)"
                value={fakeHandle}
                onChange={(e) => setFakeHandle(e.target.value)}
                autoFocus={!showOauth}
                style={{ padding: "0.5rem 0.7rem", fontSize: "0.95rem" }}
              />
              <button
                type="submit"
                disabled={fakeSubmitting || fakeHandle.trim().length < 2}
                style={{
                  padding: "0.6rem 1rem",
                  fontSize: "0.95rem",
                  background: showOauth ? "transparent" : "#1a3a30",
                  color: "inherit",
                  border: "1px solid #2a4d3f",
                  borderRadius: 6,
                  cursor:
                    fakeSubmitting || fakeHandle.trim().length < 2
                      ? "not-allowed"
                      : "pointer",
                }}
              >
                {fakeSubmitting
                  ? "Signing in…"
                  : showOauth
                  ? `Continue as ${fakeHandle.trim() || "…"}`
                  : "Continue"}
              </button>
            </form>
          )}

          {!showOauth && !showFake && (
            <p style={{ opacity: 0.6, fontSize: "0.85rem" }}>
              No sign-in method is enabled. Check the server's AUTH_MODE setting.
            </p>
          )}
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
          flexWrap: "wrap",
          gap: "0.5rem",
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
      <main className="app-shell" style={{ flex: 1, padding: "1.5rem" }}>
        <Outlet />
      </main>
    </div>
  );
}
