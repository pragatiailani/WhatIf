import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { api } from "../api/client";
import type { User } from "../api/types";
import { PageLoader } from "../components/Loaders";

const TOKEN_KEY = "whatif.token";

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

  useEffect(() => {
    const token = localStorage.getItem(TOKEN_KEY);
    if (!token) return setReady(true);
    api
      .me(token)
      .then(setUser)
      .catch(() => localStorage.removeItem(TOKEN_KEY))
      .finally(() => setReady(true));
  }, []);

  const value: AuthState = {
    user,
    ready,
    async signIn(email, password) {
      const s = await api.signIn(email, password);
      localStorage.setItem(TOKEN_KEY, s.token);
      setUser(s.user);
    },
    async signUp(name, email, password) {
      const s = await api.signUp(name, email, password);
      localStorage.setItem(TOKEN_KEY, s.token);
      setUser(s.user);
    },
    signOut() {
      localStorage.removeItem(TOKEN_KEY);
      setUser(null);
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
