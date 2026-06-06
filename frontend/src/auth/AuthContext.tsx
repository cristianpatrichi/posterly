import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import type { ReactNode } from "react";
import * as api from "../api";

interface AuthState {
  user: string | null; // email, or null when signed out
  loading: boolean;
  setUser: (email: string) => void;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUserState] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Resolve the current session on mount.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { email } = await api.me();
        if (!cancelled) setUserState(email);
      } catch {
        if (!cancelled) setUserState(null);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Any 401 from the API (e.g. an expired session) drops us back to login.
  useEffect(() => {
    const onUnauthorized = () => setUserState(null);
    window.addEventListener("auth:unauthorized", onUnauthorized);
    return () => window.removeEventListener("auth:unauthorized", onUnauthorized);
  }, []);

  const setUser = useCallback((email: string) => setUserState(email), []);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      // Keep collage_project_id so the SAME user resumes their last collage on
      // re-login instead of spawning a fresh empty one each time. A different
      // user on this browser just gets a 404 on it (owner check) and falls back
      // to their own latest project.
      setUserState(null);
    }
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, setUser, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
