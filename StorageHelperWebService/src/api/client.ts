import axios from 'axios'

/**
 * Resolve the API base URL for the current runtime environment.
 *
 * For native apps (Android/iOS), we rely on `adb reverse` (Android) or
 * equivalent to tunnel device localhost → host localhost, so no IP rewriting
 * is needed. Just return the configured URL as-is.
 */
export const getApiBaseUrl = (): string => {
  return import.meta.env.VITE_API_BASE_URL || '/api'
}

const API_BASE_URL = getApiBaseUrl()

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
})

// Store auth context reference
let getAuthToken: (() => string | null) | null = null

export const setAuthTokenGetter = (getter: () => string | null) => {
  getAuthToken = getter
}

// Request interceptor - Add authorization token to all requests
apiClient.interceptors.request.use(
  (config) => {
    // Get token from stored getter function
    if (getAuthToken) {
      const token = getAuthToken()
      if (token) {
        config.headers.Authorization = `Bearer ${token}`
      }
    } else {
      // Fallback: Try to get from localStorage if getter not set
      const token = localStorage.getItem('authToken')
      if (token) {
        config.headers.Authorization = `Bearer ${token}`
      }
    }
    return config
  },
  (error) => {
    return Promise.reject(error)
  }
)

// Response interceptor
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response) {
      // Handle 401 Unauthorized - token expired or invalid
      if (error.response.status === 401) {
        console.warn('Unauthorized - token expired or invalid')
        localStorage.removeItem('authToken')
        localStorage.removeItem('userEmail')
        localStorage.removeItem('userDisplayName')
        // Clear the auth token getter
        getAuthToken = null
        // Redirect to login
        if (window.location.pathname !== '/login') {
          window.location.href = '/login'
        }
      } else {
        console.error('API Error:', error.response.data)
      }
    }
    return Promise.reject(error)
  }
)

export default apiClient
