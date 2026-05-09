import { useCallback, useEffect, useState } from "react";

interface Session {
  player_id: string;
  balance: number;
}

interface UseSessionResult {
  /** The current user's handle (= player_id), or null if not signed in. */
  handle: string | null;
  /** Full session info including balance, or null. */
  session: Session | null;
  /** True while the initial /auth/me check is in flight. */
  loading: boolean;
  /** Sign out: clears the cookie and refetches session. */
  signOut: () => Promise<void>;
  /** Refetch the session — call after a login redirect lands. */
  refresh: () => Promise<void>;
}

/**
 * The browser's session cookie is the source of truth. This hook reflects
 * what /auth/me returns and provides a sign-out action. There is no `save`:
 * sign-in happens via the OAuth redirect flow (see App.tsx).
 */
export function useSession(): UseSessionResult {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch("/auth/me", { credentials: "include" });
      if (res.ok) {
        const data = (await res.json()) as Session;
        setSession(data);
      } else {
        setSession(null);
      }
    } catch {
      setSession(null);
    } finally {
      setLoading(false);
    }
  }, []);

  const signOut = useCallback(async () => {
    try {
      await fetch("/auth/logout", {
        method: "POST",
        credentials: "include",
      });
    } catch {
      // Continue even if the request fails — clearing local state is enough.
    }
    setSession(null);
    await refresh();
  }, [refresh]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return {
    handle: session?.player_id ?? null,
    session,
    loading,
    signOut,
    refresh,
  };
}
