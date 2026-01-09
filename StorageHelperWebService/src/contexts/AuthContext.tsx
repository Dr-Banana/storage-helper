import { createContext, useContext, useState, useEffect, ReactNode } from 'react'

interface AuthContextType {
  userId: number | null
  userEmail: string | null
  userDisplayName: string | null
  login: (userId: number, email: string, displayName: string) => void
  logout: () => void
  isAuthenticated: boolean
}

const AuthContext = createContext<AuthContextType | undefined>(undefined)

export const AuthProvider = ({ children }: { children: ReactNode }) => {
  const [userId, setUserId] = useState<number | null>(() => {
    // 从 localStorage 读取保存的用户 ID
    const savedUserId = localStorage.getItem('userId')
    if (!savedUserId) return null
    const parsed = parseInt(savedUserId, 10)
    return isNaN(parsed) ? null : parsed
  })

  const [userEmail, setUserEmail] = useState<string | null>(() => {
    return localStorage.getItem('userEmail')
  })

  const [userDisplayName, setUserDisplayName] = useState<string | null>(() => {
    return localStorage.getItem('userDisplayName')
  })

  useEffect(() => {
    // 保存用户信息到 localStorage
    if (userId !== null && userId !== undefined) {
      localStorage.setItem('userId', userId.toString())
    } else {
      localStorage.removeItem('userId')
    }

    if (userEmail !== null && userEmail !== undefined) {
      localStorage.setItem('userEmail', userEmail)
    } else {
      localStorage.removeItem('userEmail')
    }

    if (userDisplayName !== null && userDisplayName !== undefined) {
      localStorage.setItem('userDisplayName', userDisplayName)
    } else {
      localStorage.removeItem('userDisplayName')
    }
  }, [userId, userEmail, userDisplayName])

  const login = (userId: number, email: string, displayName: string) => {
    setUserId(userId)
    setUserEmail(email)
    setUserDisplayName(displayName)
  }

  const logout = () => {
    setUserId(null)
    setUserEmail(null)
    setUserDisplayName(null)
  }

  return (
    <AuthContext.Provider
      value={{
        userId,
        userEmail,
        userDisplayName,
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

