import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { api } from "../api/client";
import type { User } from "../api/types";
import { PageLoader } from "../components/Loaders";
import { onSessionLost, setAccessToken } from "./token";

interface AuthState {
  user: User | null;
  ready: boolean;
  signIn(email: string, password: string): Promise<void>;
  signUp(name: string, email: string, password: string): Promise<void>;
  signOut(): void;
}

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);

  // The access token is only in memory, so on load the refresh cookie restores the session.
  useEffect(() => {
    api
      .refresh()
      .then((s) => {
        setAccessToken(s.token);
        setUser(s.user);
      })
      .catch(() => {})
      .finally(() => setReady(true));
    // If a later request can't renew the session, drop to signed out; RequireAuth then redirects.
    return onSessionLost(() => setUser(null));
  }, []);

  const value: AuthState = {
    user,
    ready,
    async signIn(email, password) {
      const s = await api.signIn(email, password);
      setAccessToken(s.token);
      setUser(s.user);
    },
    async signUp(name, email, password) {
      const s = await api.signUp(name, email, password);
      setAccessToken(s.token);
      setUser(s.user);
    },
    signOut() {
      setAccessToken(null);
      setUser(null);
      void api.signOut();
    },
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}

/** Redirects to sign-in, then back here afterwards. */
export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, ready } = useAuth();
  const loc = useLocation();
  if (!ready) return <PageLoader label="Lighting the candles…" />;
  if (!user) return <Navigate to={`/signin?next=${encodeURIComponent(loc.pathname + loc.search)}`} replace />;
  return <>{children}</>;
}
