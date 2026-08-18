import { create } from "zustand";
import { api, getToken, setToken, type User } from "@/lib/api";

interface AuthState {
  user: User | null;
  ready: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
  bootstrap: () => Promise<void>;
}

export const useAuth = create<AuthState>((set) => ({
  user: null,
  ready: false,
  async login(username, password) {
    const data = await api.post<{ token: string; user: User }>("/api/auth/login", {
      username,
      password,
    });
    setToken(data.token);
    set({ user: data.user });
  },
  logout() {
    setToken(null);
    set({ user: null });
  },
  async bootstrap() {
    if (!getToken()) {
      set({ ready: true });
      return;
    }
    try {
      const user = await api.get<User>("/api/auth/me");
      set({ user, ready: true });
    } catch {
      set({ user: null, ready: true });
    }
  },
}));
