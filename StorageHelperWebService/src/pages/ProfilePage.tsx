import { useState, useEffect } from 'react'
import { User, Edit, LogOut, Mail } from 'lucide-react'
import { useAuth } from '../contexts/AuthContext'
import { userService, User as UserType } from '../api/services'
import { useNavigate } from 'react-router-dom'

const ProfilePage = () => {
  const { userId, userEmail, userDisplayName, logout } = useAuth()
  const navigate = useNavigate()
  const [user, setUser] = useState<UserType | null>(null)
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState(false)
  const [formData, setFormData] = useState({ display_name: '', note: '' })

  useEffect(() => {
    if (userId) {
      loadUser()
    }
  }, [userId])

  const loadUser = async () => {
    if (!userId) return

    try {
      setLoading(true)
      const userData = await userService.getById(userId)
      setUser(userData)
      setFormData({
        display_name: userData.display_name,
        note: userData.note || '',
      })
    } catch (error) {
      console.error('Failed to load user:', error)
    } finally {
      setLoading(false)
    }
  }

  const handleSave = async () => {
    if (!userId || !formData.display_name.trim()) {
      return
    }

    try {
      await userService.update(userId, formData)
      setEditing(false)
      loadUser()
    } catch (error) {
      console.error('Failed to update user:', error)
      alert('Failed to update profile')
    }
  }

  const handleLogout = () => {
    if (confirm('Are you sure you want to logout?')) {
      logout()
      navigate('/login')
    }
  }

  if (loading) {
    return (
      <div className="max-w-4xl mx-auto">
        <div className="card text-center py-12">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-home-primary-500 mx-auto"></div>
          <p className="mt-4 text-home-text-light">Loading profile...</p>
        </div>
      </div>
    )
  }

  if (!user) {
    return (
      <div className="max-w-4xl mx-auto">
        <div className="card text-center py-12">
          <p className="text-home-text-light">Failed to load profile</p>
        </div>
      </div>
    )
  }

  return (
    <div className="max-w-4xl mx-auto">
      <div className="mb-6 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <h1 className="text-3xl font-bold text-home-text-dark">My Profile</h1>
        <button
          onClick={handleLogout}
          className="btn-secondary inline-flex items-center gap-2"
        >
          <LogOut size={20} />
          Logout
        </button>
      </div>

      <div className="card">
        <div className="flex items-start gap-6 mb-6">
          <div className="w-20 h-20 bg-home-primary-100 rounded-full flex items-center justify-center flex-shrink-0">
            <User className="text-home-primary-600" size={40} />
          </div>
          <div className="flex-1">
            <h2 className="text-2xl font-bold text-home-text-dark mb-2">{userDisplayName || user.display_name}</h2>
            <div className="flex items-center gap-2 text-home-text-light">
              <Mail size={16} />
              <span>{userEmail || user.email}</span>
            </div>
            <p className="text-sm text-home-text-light mt-2">User ID: {userId}</p>
          </div>
        </div>

        {editing ? (
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-home-text-dark mb-2">
                Display Name *
              </label>
              <input
                type="text"
                value={formData.display_name}
                onChange={(e) =>
                  setFormData({ ...formData, display_name: e.target.value })
                }
                className="input w-full"
                placeholder="Enter display name"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-home-text-dark mb-2">
                Note
              </label>
              <textarea
                value={formData.note}
                onChange={(e) =>
                  setFormData({ ...formData, note: e.target.value })
                }
                className="input w-full"
                rows={4}
                placeholder="Enter note"
              />
            </div>
            <div className="flex gap-4">
              <button onClick={handleSave} className="btn-primary">
                Save Changes
              </button>
              <button
                onClick={() => {
                  setEditing(false)
                  setFormData({
                    display_name: user.display_name,
                    note: user.note || '',
                  })
                }}
                className="btn-secondary"
              >
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <div>
            <div className="flex items-center justify-between mb-6">
              <h3 className="text-lg font-semibold text-home-text-dark">
                Account Details
              </h3>
              <button
                onClick={() => setEditing(true)}
                className="btn-secondary inline-flex items-center gap-2"
              >
                <Edit size={18} />
                Edit Profile
              </button>
            </div>

            {user.note && (
              <div className="mb-6 pb-6 border-b border-home-border-light">
                <h4 className="font-medium text-home-text-dark mb-2">Note</h4>
                <p className="text-home-text-light whitespace-pre-wrap">{user.note}</p>
              </div>
            )}

            <div className="space-y-3 text-sm">
              <div className="flex justify-between">
                <span className="font-medium text-home-text-dark">Created:</span>
                <span className="text-home-text-light">
                  {new Date(user.created_at).toLocaleString('en-US')}
                </span>
              </div>
              <div className="flex justify-between">
                <span className="font-medium text-home-text-dark">Last Updated:</span>
                <span className="text-home-text-light">
                  {new Date(user.updated_at).toLocaleString('en-US')}
                </span>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

export default ProfilePage

