import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, type Me } from "./api";

interface Auth {
  me: Me | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<Auth | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.get<Me>("/v1/auth/me").then(setMe, () => setMe(null)).finally(() => setLoading(false));
    const onLoggedOut = () => setMe(null);
    window.addEventListener("ia:logged-out", onLoggedOut);
    return () => window.removeEventListener("ia:logged-out", onLoggedOut);
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    setMe(await api.post<Me>("/v1/auth/login", { username, password }));
  }, []);
  const logout = useCallback(async () => {
    await api.post("/v1/auth/logout").catch(() => undefined);
    setMe(null);
  }, []);

  return <AuthContext.Provider value={{ me, loading, login, logout }}>{children}</AuthContext.Provider>;
}

export function useAuth(): Auth {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
