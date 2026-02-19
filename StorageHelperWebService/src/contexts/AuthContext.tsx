import { createContext, useContext, useState, useEffect, ReactNode } from 'react'
import { setAuthTokenGetter, getApiBaseUrl } from '../api/client'
import { nativeGoogleSignOut } from '../services/googleAuth'

interface AuthContextType {
  userId: number | null
  userEmail: string | null
  userDisplayName: string | null
  authToken: string | null
  login: (userId: number, email: string, displayName: string, token: string) => void
  logout: () => void
  isAuthenticated: boolean
}

const AuthContext = createContext<AuthContextType | undefined>(undefined)

export const AuthProvider = ({ children }: { children: ReactNode }) => {
  // Store token in state (more secure than localStorage for credentials)
  const [authToken, setAuthToken] = useState<string | null>(() => {
    return localStorage.getItem('authToken')
  })

  const [userId, setUserId] = useState<number | null>(() => {
    // Only read userId from backend verification, not localStorage
    // This will be set after token verification
    return null
  })

  const [userEmail, setUserEmail] = useState<string | null>(() => {
    return localStorage.getItem('userEmail')
  })

  const [userDisplayName, setUserDisplayName] = useState<string | null>(() => {
    return localStorage.getItem('userDisplayName')
  })

  // Set up auth token getter for API client on mount and whenever token changes
  useEffect(() => {
    setAuthTokenGetter(() => authToken)
  }, [authToken])

  // Verify token on app load
  useEffect(() => {
    const verifyToken = async () => {
      if (!authToken) {
        setUserId(null)
        return
      }

      try {
        const response = await fetch(`${getApiBaseUrl()}/auth/verify`, {
          headers: {
            'Authorization': `Bearer ${authToken}`
          }
        })

        if (response.ok) {
          const data = await response.json()
          setUserId(data.user_id)
        } else {
          // Token invalid, clear it
          localStorage.removeItem('authToken')
          setAuthToken(null)
          setUserId(null)
        }
      } catch (error) {
        console.error('Token verification failed:', error)
        setUserId(null)
      }
    }

    verifyToken()
  }, [authToken])

  // Save credentials when they change
  useEffect(() => {
    if (authToken) {
      localStorage.setItem('authToken', authToken)
    } else {
      localStorage.removeItem('authToken')
    }

    if (userEmail) {
      localStorage.setItem('userEmail', userEmail)
    } else {
      localStorage.removeItem('userEmail')
    }

    if (userDisplayName) {
      localStorage.setItem('userDisplayName', userDisplayName)
    } else {
      localStorage.removeItem('userDisplayName')
    }
  }, [authToken, userEmail, userDisplayName])

  const login = (userId: number, email: string, displayName: string, token: string) => {
    setUserId(userId)
    setUserEmail(email)
    setUserDisplayName(displayName)
    setAuthToken(token)
  }

  const logout = () => {
    setUserId(null)
    setUserEmail(null)
    setUserDisplayName(null)
    setAuthToken(null)
    localStorage.removeItem('authToken')
    localStorage.removeItem('userEmail')
    localStorage.removeItem('userDisplayName')
    nativeGoogleSignOut()
  }

  return (
    <AuthContext.Provider
      value={{
        userId,
        userEmail,
        userDisplayName,
        authToken,
        login,
        logout,
        isAuthenticated: userId !== null && authToken !== null,
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

