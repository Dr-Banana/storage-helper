import { createContext, useContext, useState, useEffect, useCallback, ReactNode } from 'react'
import { setAuthTokenGetter, getApiBaseUrl } from '../api/client'
import { nativeGoogleSignOut } from '../services/googleAuth'
import { CookingLevel, UserLanguage } from '../api/services'

export interface DailyUsage {
  usage_date: string
  meal_plan_sessions: number
  token_count: number
  is_premium_active: boolean
  sessions_limit: number | null
  tokens_limit: number | null
}

interface AuthContextType {
  userId: number | null
  userEmail: string | null
  userDisplayName: string | null
  authToken: string | null
  cookingLevel: CookingLevel
  language: UserLanguage
  isPremium: boolean
  dailyUsage: DailyUsage | null
  refreshUsage: () => Promise<void>
  login: (userId: number, email: string, displayName: string, token: string) => void
  logout: () => void
  isAuthenticated: boolean
  updateCookingLevel: (level: CookingLevel) => void
  updateLanguage: (lang: UserLanguage) => void
}

const AuthContext = createContext<AuthContextType | undefined>(undefined)

export const AuthProvider = ({ children }: { children: ReactNode }) => {
  const [authToken, setAuthToken] = useState<string | null>(() => {
    return localStorage.getItem('authToken')
  })

  const [userId, setUserId] = useState<number | null>(() => {
    return null
  })

  const [userEmail, setUserEmail] = useState<string | null>(() => {
    return localStorage.getItem('userEmail')
  })

  const [userDisplayName, setUserDisplayName] = useState<string | null>(() => {
    return localStorage.getItem('userDisplayName')
  })

  const [cookingLevel, setCookingLevel] = useState<CookingLevel>(() => {
    return (localStorage.getItem('cookingLevel') as CookingLevel) || 'beginner'
  })

  const [language, setLanguage] = useState<UserLanguage>(() => {
    return (localStorage.getItem('userLanguage') as UserLanguage) || 'zh'
  })

  const [isPremium, setIsPremium] = useState(false)
  const [dailyUsage, setDailyUsage] = useState<DailyUsage | null>(null)

  useEffect(() => {
    setAuthTokenGetter(() => authToken)
  }, [authToken])

  const fetchSubscriptionAndUsage = useCallback(async (uid: number, token: string) => {
    try {
      const [subResp, usageResp] = await Promise.all([
        fetch(`${getApiBaseUrl()}/users/${uid}/subscription`, {
          headers: { Authorization: `Bearer ${token}` },
        }),
        fetch(`${getApiBaseUrl()}/users/${uid}/daily-usage`, {
          headers: { Authorization: `Bearer ${token}` },
        }),
      ])
      if (subResp.ok) {
        const sub = await subResp.json()
        setIsPremium(sub.is_active === true)
      }
      if (usageResp.ok) {
        setDailyUsage(await usageResp.json())
      }
    } catch {
      // Non-fatal: keep defaults
    }
  }, [])

  const refreshUsage = useCallback(async () => {
    if (userId && authToken) {
      await fetchSubscriptionAndUsage(userId, authToken)
    }
  }, [userId, authToken, fetchSubscriptionAndUsage])

  useEffect(() => {
    const verifyToken = async () => {
      if (!authToken) {
        setUserId(null)
        return
      }

      try {
        const response = await fetch(`${getApiBaseUrl()}/auth/verify`, {
          headers: { Authorization: `Bearer ${authToken}` },
        })

        if (response.ok) {
          const data = await response.json()
          setUserId(data.user_id)
          try {
            const userResp = await fetch(`${getApiBaseUrl()}/users/${data.user_id}`, {
              headers: { Authorization: `Bearer ${authToken}` },
            })
            if (userResp.ok) {
              const userData = await userResp.json()
              const level: CookingLevel = userData.cooking_level || 'beginner'
              setCookingLevel(level)
              localStorage.setItem('cookingLevel', level)
              const lang: UserLanguage = userData.language || 'en'
              setLanguage(lang)
              localStorage.setItem('userLanguage', lang)
            }
          } catch {
            // Non-blocking
          }
          // Fetch subscription & usage after login
          await fetchSubscriptionAndUsage(data.user_id, authToken)
        } else {
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
  }, [authToken, fetchSubscriptionAndUsage])

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

  useEffect(() => {
    localStorage.setItem('cookingLevel', cookingLevel)
  }, [cookingLevel])

  useEffect(() => {
    localStorage.setItem('userLanguage', language)
  }, [language])

  const login = (uid: number, email: string, displayName: string, token: string) => {
    setUserId(uid)
    setUserEmail(email)
    setUserDisplayName(displayName)
    setAuthToken(token)
  }

  const updateCookingLevel = (level: CookingLevel) => {
    setCookingLevel(level)
    localStorage.setItem('cookingLevel', level)
  }

  const updateLanguage = (lang: UserLanguage) => {
    setLanguage(lang)
    localStorage.setItem('userLanguage', lang)
  }

  const logout = () => {
    setUserId(null)
    setUserEmail(null)
    setUserDisplayName(null)
    setAuthToken(null)
    setCookingLevel('beginner')
    setLanguage('zh')
    setIsPremium(false)
    setDailyUsage(null)
    localStorage.removeItem('authToken')
    localStorage.removeItem('userEmail')
    localStorage.removeItem('userDisplayName')
    localStorage.removeItem('cookingLevel')
    localStorage.removeItem('userLanguage')
    nativeGoogleSignOut()
  }

  return (
    <AuthContext.Provider
      value={{
        userId,
        userEmail,
        userDisplayName,
        authToken,
        cookingLevel,
        language,
        isPremium,
        dailyUsage,
        refreshUsage,
        login,
        logout,
        isAuthenticated: userId !== null && authToken !== null,
        updateCookingLevel,
        updateLanguage,
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
