import { useState, FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'
import { userService } from '../api/services'
import { LogIn, AlertCircle } from 'lucide-react'

const LoginPage = () => {
  const [userId, setUserId] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const { login } = useAuth()
  const navigate = useNavigate()

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    setLoading(true)

    try {
      const userIdNum = parseInt(userId.trim())
      if (isNaN(userIdNum) || userIdNum <= 0) {
        setError('Please enter a valid user ID (positive number)')
        setLoading(false)
        return
      }

      // 验证用户是否存在
      try {
        await userService.getById(userIdNum)
        // 用户存在，登录成功
        login(userIdNum)
        navigate('/')
      } catch (error: any) {
        if (error.response?.status === 404) {
          setError(`User with ID ${userIdNum} does not exist. Please check your user ID.`)
        } else {
          setError('Failed to verify user. Please try again.')
        }
      }
    } catch (error: any) {
      setError('An error occurred. Please try again.')
      console.error('Login error:', error)
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
              Please enter your User ID to continue
            </p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-6">
            <div>
              <label
                htmlFor="userId"
                className="block text-sm font-medium text-home-text-dark mb-2"
              >
                User ID
              </label>
              <input
                id="userId"
                type="number"
                value={userId}
                onChange={(e) => setUserId(e.target.value)}
                placeholder="Enter your user ID"
                className="input w-full"
                required
                min="1"
                disabled={loading}
                autoFocus
              />
              <p className="text-xs text-home-text-light mt-2">
                Enter your unique user identification number
              </p>
            </div>

            {error && (
              <div className="flex items-center gap-2 p-3 bg-home-error-50 border border-home-error-200 rounded-home">
                <AlertCircle className="text-home-error-500" size={20} />
                <p className="text-sm text-home-error-700">{error}</p>
              </div>
            )}

            <button
              type="submit"
              disabled={loading || !userId.trim()}
              className="btn-primary w-full disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {loading ? 'Logging in...' : 'Login'}
            </button>
          </form>
        </div>
      </div>
    </div>
  )
}

export default LoginPage

