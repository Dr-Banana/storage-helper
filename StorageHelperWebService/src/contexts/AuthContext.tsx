import { createContext, useContext, useState, useEffect, ReactNode } from 'react'

interface AuthContextType {
  userId: number | null
  login: (userId: number) => void
  logout: () => void
  isAuthenticated: boolean
}

const AuthContext = createContext<AuthContextType | undefined>(undefined)

export const AuthProvider = ({ children }: { children: ReactNode }) => {
  const [userId, setUserId] = useState<number | null>(() => {
    // 从 localStorage 读取保存的用户 ID
    const savedUserId = localStorage.getItem('userId')
    return savedUserId ? parseInt(savedUserId, 10) : null
  })

  useEffect(() => {
    // 保存用户 ID 到 localStorage
    if (userId) {
      localStorage.setItem('userId', userId.toString())
    } else {
      localStorage.removeItem('userId')
    }
  }, [userId])

  const login = (userId: number) => {
    setUserId(userId)
  }

  const logout = () => {
    setUserId(null)
  }

  return (
    <AuthContext.Provider
      value={{
        userId,
        login,
        logout,
        isAuthenticated: userId !== null,
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export const useAuth = () => {
  const context = useContext(AuthContext)
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return context
}

