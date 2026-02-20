import { useState, useEffect } from 'react'
import { User, Edit, LogOut, Mail, AlertTriangle, Trash2, ChevronDown, ChevronUp } from 'lucide-react'
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
  const [showDangerZone, setShowDangerZone] = useState(false)
  const [showEraseConfirm, setShowEraseConfirm] = useState(false)
  const [eraseConfirmText, setEraseConfirmText] = useState('')
  const [eraseLoading, setEraseLoading] = useState(false)

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

  const handleEraseAllData = async () => {
    if (!userId) return

    // First confirmation: Show dialog
    if (!showEraseConfirm) {
      setShowEraseConfirm(true)
      return
    }

    // Second confirmation: Check if user typed the confirmation text
    const confirmationText = 'DELETE ALL MY DATA'
    if (eraseConfirmText !== confirmationText) {
      alert('Please type "DELETE ALL MY DATA" to confirm deletion')
      return
    }

    // Final confirmation dialog
    if (!confirm('This is the final confirmation. Are you sure you want to permanently delete all data? This action cannot be undone!')) {
      return
    }

    try {
      setEraseLoading(true)
      await userService.eraseAllData(userId)
      alert('All data has been successfully deleted. You will be logged out.')
      logout()
      navigate('/login')
    } catch (error) {
      console.error('Failed to erase user data:', error)
      alert('Failed to delete data. Please try again later')
      setEraseLoading(false)
      setShowEraseConfirm(false)
      setEraseConfirmText('')
    }
  }

  if (loading) {
    return (
      <div className="max-w-4xl mx-auto px-4 sm:px-6 py-6">
        <div className="card text-center py-12">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-home-primary-500 mx-auto"></div>
          <p className="mt-4 text-home-text-light">Loading profile...</p>
        </div>
      </div>
    )
  }

  if (!user) {
    return (
      <div className="max-w-4xl mx-auto px-4 sm:px-6 py-6">
        <div className="card text-center py-12">
          <p className="text-home-text-light">Failed to load profile</p>
        </div>
      </div>
    )
  }

  return (
    <div className="max-w-4xl mx-auto px-4 sm:px-6 py-6">
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

      {/* Danger Zone - Collapsible */}
      <div className="card border-2 border-red-300 bg-red-50 dark:bg-red-950/20">
        <button
          onClick={() => {
            setShowDangerZone(!showDangerZone)
            // Reset erase confirm state when collapsing
            if (showDangerZone) {
              setShowEraseConfirm(false)
              setEraseConfirmText('')
            }
          }}
          className="w-full flex items-center justify-between gap-3 text-left"
        >
          <div className="flex items-center gap-3">
            <AlertTriangle className="text-red-600 dark:text-red-400 flex-shrink-0" size={24} />
            <div>
              <h3 className="text-lg font-semibold text-red-800 dark:text-red-300">
                Danger Zone
              </h3>
              <p className="text-sm text-red-700 dark:text-red-400">
                Permanently delete all your data
              </p>
            </div>
          </div>
          {showDangerZone ? (
            <ChevronUp className="text-red-600 dark:text-red-400 flex-shrink-0" size={20} />
          ) : (
            <ChevronDown className="text-red-600 dark:text-red-400 flex-shrink-0" size={20} />
          )}
        </button>

        {showDangerZone && (
          <div className="mt-4 pt-4 border-t border-red-300 dark:border-red-700">
            <p className="text-sm text-red-700 dark:text-red-400 mb-4">
              This action cannot be undone and will delete all your documents, files, locations, categories, and account.
            </p>

            {!showEraseConfirm ? (
              <button
                onClick={handleEraseAllData}
                className="btn-danger inline-flex items-center gap-2"
                disabled={eraseLoading}
              >
                <Trash2 size={18} />
                Delete All Data
              </button>
            ) : (
              <div className="space-y-4">
                <div className="bg-red-100 dark:bg-red-900/30 border border-red-300 dark:border-red-700 rounded-lg p-4">
                  <p className="text-sm font-medium text-red-800 dark:text-red-300 mb-2">
                    Warning: This action will permanently delete:
                  </p>
                  <ul className="text-sm text-red-700 dark:text-red-400 list-disc list-inside space-y-1">
                    <li>All documents and files</li>
                    <li>All storage locations and images</li>
                    <li>All document categories</li>
                    <li>All schedules</li>
                    <li>Your account</li>
                  </ul>
                </div>
                <div>
                  <label className="block text-sm font-medium text-red-800 dark:text-red-300 mb-2">
                    Please type <span className="font-mono font-bold">DELETE ALL MY DATA</span> to confirm:
                  </label>
                  <input
                    type="text"
                    value={eraseConfirmText}
                    onChange={(e) => setEraseConfirmText(e.target.value)}
                    className="input w-full border-red-300 focus:border-red-500 focus:ring-red-500"
                    placeholder="DELETE ALL MY DATA"
                    disabled={eraseLoading}
                  />
                </div>
                <div className="flex gap-3">
                  <button
                    onClick={handleEraseAllData}
                    className="btn-danger inline-flex items-center gap-2"
                    disabled={eraseLoading || eraseConfirmText !== 'DELETE ALL MY DATA'}
                  >
                    {eraseLoading ? (
                      <>
                        <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-white"></div>
                        Deleting...
                      </>
                    ) : (
                      <>
                        <Trash2 size={18} />
                        Confirm Delete All Data
                      </>
                    )}
                  </button>
                  <button
                    onClick={() => {
                      setShowEraseConfirm(false)
                      setEraseConfirmText('')
                    }}
                    className="btn-secondary"
                    disabled={eraseLoading}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

export default ProfilePage

