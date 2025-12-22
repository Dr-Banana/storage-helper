import { useState, useEffect } from 'react'
import { User, Edit, LogOut } from 'lucide-react'
import { useAuth } from '../contexts/AuthContext'
import { userService, User as UserType } from '../api/services'
import { useNavigate } from 'react-router-dom'

const ProfilePage = () => {
  const { userId, logout } = useAuth()
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
            {editing ? (
              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-home-text-dark mb-2">
                    Username *
                  </label>
                  <input
                    type="text"
                    value={formData.display_name}
                    onChange={(e) =>
                      setFormData({ ...formData, display_name: e.target.value })
                    }
                    className="input"
                    placeholder="Enter username"
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
                    className="input"
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
                <div className="flex items-center justify-between mb-4">
                  <h2 className="text-2xl font-semibold text-home-text-dark">
                    {user.display_name}
                  </h2>
                  <button
                    onClick={() => setEditing(true)}
                    className="btn-secondary inline-flex items-center gap-2"
                  >
                    <Edit size={18} />
                    Edit Profile
                  </button>
                </div>
                {user.note && (
                  <p className="text-home-text-light mb-4 whitespace-pre-wrap">
                    {user.note}
                  </p>
                )}
                <div className="space-y-2 text-sm text-home-text-light">
                  <div>
                    <span className="font-medium">User ID:</span> {user.id}
                  </div>
                  <div>
                    <span className="font-medium">Created:</span>{' '}
                    {new Date(user.created_at).toLocaleString('en-US')}
                  </div>
                  <div>
                    <span className="font-medium">Last Updated:</span>{' '}
                    {new Date(user.updated_at).toLocaleString('en-US')}
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

export default ProfilePage

