import { create } from "zustand"
import { persist, createJSONStorage } from "zustand/middleware"

interface AuthState {
  userId: number | null
  isAuthenticated: boolean
  login: (userId: number) => void
  logout: () => void
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      userId: null,
      isAuthenticated: false,
      login: (userId: number) => {
        set({ userId, isAuthenticated: true })
      },
      logout: () => {
        set({ userId: null, isAuthenticated: false })
      },
    }),
    {
      name: "auth-storage",
      storage: typeof window !== "undefined" ? createJSONStorage(() => localStorage) : undefined,
    }
  )
)

