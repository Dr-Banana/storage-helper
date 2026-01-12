import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'
import { GoogleLogin, CredentialResponse } from '@react-oauth/google'
import { LogIn, AlertCircle, Loader } from 'lucide-react'
import { userService } from '../api/services'

const LoginPage = () => {
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const { login } = useAuth()
  const navigate = useNavigate()

  const handleGoogleSuccess = async (credentialResponse: CredentialResponse) => {
    setError(null)
    setLoading(true)

    try {
      if (!credentialResponse.credential) {
        setError('Failed to get Google token. Please try again.')
        setLoading(false)
        return
      }

      // Send token to backend for authentication
      const response = await userService.googleLogin(credentialResponse.credential)
      
      // Login successful
      login(response.user_id, response.email, response.display_name)
      
      // Show message for new users
      if (response.is_new_user) {
        console.log(`Welcome! Your user ID is: ${response.user_id}`)
      }
      
      navigate('/')
    } catch (error: any) {
      console.error('Google login error:', error)
      
      if (error.response?.status === 400) {
        setError(error.response?.data?.detail || 'Invalid Google token. Please try again.')
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
              ) : (
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

