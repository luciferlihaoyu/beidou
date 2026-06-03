import { create } from 'zustand'
import { authApi, setToken, getToken, type UserOut } from '@/lib/api'

interface AuthState {
  user: UserOut | null
  loading: boolean
  error: string | null
  /** Restore user from existing token on page load */
  restoreSession: () => Promise<void>
  /** Login and store token */
  login: (username: string, password: string) => Promise<void>
  /** Register and auto-login */
  register: (username: string, email: string, password: string) => Promise<void>
  /** Logout — clear token and user */
  logout: () => void
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  loading: false,
  error: null,

  restoreSession: async () => {
    const token = getToken()
    if (!token) return
    set({ loading: true })
    try {
      const user = await authApi.me()
      set({ user, loading: false })
    } catch {
      setToken(null)
      set({ user: null, loading: false })
    }
  },

  login: async (username, password) => {
    set({ loading: true, error: null })
    try {
      const { access_token } = await authApi.login({ username, password })
      setToken(access_token)
      const user = await authApi.me()
      set({ user, loading: false })
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Login failed'
      set({ error: msg, loading: false })
      throw e
    }
  },

  register: async (username, email, password) => {
    set({ loading: true, error: null })
    try {
      await authApi.register({ username, email, password })
      // Auto-login after registration
      const { access_token } = await authApi.login({ username, password })
      setToken(access_token)
      const user = await authApi.me()
      set({ user, loading: false })
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Registration failed'
      set({ error: msg, loading: false })
      throw e
    }
  },

  logout: () => {
    setToken(null)
    set({ user: null, error: null })
  },
}))
