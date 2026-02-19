import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'
import { GoogleLogin, CredentialResponse } from '@react-oauth/google'
import { LogIn, AlertCircle, Loader } from 'lucide-react'
import { userService } from '../api/services'
import { isNativePlatform, nativeGoogleSignIn } from '../services/googleAuth'

const LoginPage = () => {
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const { login } = useAuth()
  const navigate = useNavigate()

  // Shared logic: send idToken to backend and establish session
  const authenticateWithBackend = async (idToken: string) => {
    const response = await userService.googleLogin(idToken)
    login(response.user_id, response.email, response.display_name, response.auth_token)
    if (response.is_new_user) {
      console.log(`Welcome! Your user ID is: ${response.user_id}`)
    }
    navigate('/')
  }

  // Web path: GoogleLogin component calls this with a credential
  const handleGoogleSuccess = async (credentialResponse: CredentialResponse) => {
    setError(null)
    setLoading(true)
    try {
      if (!credentialResponse.credential) {
        setError('Failed to get Google token. Please try again.')
        return
      }
      await authenticateWithBackend(credentialResponse.credential)
    } catch (err: any) {
      console.error('Google login error:', err)
      if (err.response?.status === 400) {
        setError(err.response?.data?.detail || 'Invalid Google token. Please try again.')
      } else {
        setError('Authentication failed. Please try again.')
      }
    } finally {
      setLoading(false)
    }
  }

  const handleGoogleError = () => {
    setError('Google login failed. Please try again.')
    setLoading(false)
  }

  // Native path: triggers the OS-level Google Sign-In sheet
  const handleNativeGoogleLogin = async () => {
    setError(null)
    setLoading(true)
    try {
      const user = await nativeGoogleSignIn()
      await authenticateWithBackend(user.idToken)
    } catch (err: any) {
      const msg = err?.message ?? err?.error ?? JSON.stringify(err) ?? String(err)
      console.error('Native Google login error:', msg)
      console.error('Request URL:', (err?.config?.baseURL ?? '') + (err?.config?.url ?? ''))
      console.error('Error code:', err?.code ?? 'none')
      if (err?.response) {
        console.error('Backend response:', err.response.status, JSON.stringify(err.response.data))
      }
      setError('Authentication failed. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-home-background-light flex items-center justify-center p-4">
      <div className="max-w-md w-full">
        <div className="card">
          <div className="text-center mb-8">
            <div className="inline-flex items-center justify-center w-16 h-16 bg-home-primary-100 rounded-full mb-4">
              <LogIn className="text-home-primary-600" size={32} />
            </div>
            <h1 className="text-3xl font-bold text-home-text-dark mb-2">
              Welcome to Storage Helper
            </h1>
            <p className="text-home-text-light">
              Sign in with your Google account to get started
            </p>
          </div>

          <div className="space-y-6">
            {error && (
              <div className="flex items-center gap-2 p-3 bg-home-error-50 border border-home-error-200 rounded-home">
                <AlertCircle className="text-home-error-500" size={20} />
                <p className="text-sm text-home-error-700">{error}</p>
              </div>
            )}

            <div className="flex justify-center">
              {loading ? (
                <div className="flex items-center justify-center p-4">
                  <Loader className="text-home-primary-600 animate-spin" size={24} />
                  <span className="ml-2 text-home-text-dark">Signing in...</span>
                </div>
              ) : isNativePlatform() ? (
                // Native Android / iOS: use the system Google Sign-In sheet
                <button
                  onClick={handleNativeGoogleLogin}
                  className="flex items-center gap-3 px-6 py-3 bg-white border border-gray-300 rounded-lg shadow-sm hover:shadow-md transition-shadow text-gray-700 font-medium"
                >
                  <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true">
                    <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
                    <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
                    <path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
                    <path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
                  </svg>
                  Sign in with Google
                </button>
              ) : (
                // Web / PWA: use the standard Google One-Tap component
                <GoogleLogin
                  onSuccess={handleGoogleSuccess}
                  onError={handleGoogleError}
                  theme="outline"
                  size="large"
                />
              )}
            </div>

            <p className="text-xs text-center text-home-text-light mt-6">
              We use Google authentication to securely manage your account.
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}

export default LoginPage

