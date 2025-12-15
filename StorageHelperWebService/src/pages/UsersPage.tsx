import { useState, useEffect } from 'react'
import { User, Plus, Edit, Trash2, FileText } from 'lucide-react'
import { userService, User as UserType } from '../api/services'

const UsersPage = () => {
  const [users, setUsers] = useState<UserType[]>([])
  const [loading, setLoading] = useState(true)
  const [showAddModal, setShowAddModal] = useState(false)
  const [editingUser, setEditingUser] = useState<UserType | null>(null)
  const [formData, setFormData] = useState({ display_name: '', note: '' })

  useEffect(() => {
    loadUsers()
  }, [])

  const loadUsers = async () => {
    try {
      setLoading(true)
      const response = await userService.getAll()
      setUsers(response.users)
    } catch (error) {
      console.error('Failed to load users:', error)
    } finally {
      setLoading(false)
    }
  }

  const handleAdd = async () => {
    if (!formData.display_name.trim()) {
      alert('Please enter a username')
      return
    }

    try {
      await userService.create(formData)
      setShowAddModal(false)
      setFormData({ display_name: '', note: '' })
      loadUsers()
    } catch (error) {
      console.error('Failed to create user:', error)
      alert('Failed to create user')
    }
  }

  const handleEdit = async () => {
    if (!editingUser || !formData.display_name.trim()) {
      return
    }

    try {
      await userService.update(editingUser.id, formData)
      setEditingUser(null)
      setFormData({ display_name: '', note: '' })
      loadUsers()
    } catch (error) {
      console.error('Failed to update user:', error)
      alert('Failed to update user')
    }
  }

  const handleDelete = async (id: number) => {
    if (!confirm('Are you sure you want to delete this user?')) {
      return
    }

    try {
      await userService.delete(id)
      loadUsers()
    } catch (error) {
      console.error('Failed to delete user:', error)
      alert('Failed to delete user')
    }
  }

  const openEditModal = (user: UserType) => {
    setEditingUser(user)
    setFormData({ display_name: user.display_name, note: user.note || '' })
  }

  const closeModal = () => {
    setShowAddModal(false)
    setEditingUser(null)
    setFormData({ display_name: '', note: '' })
  }

  return (
    <div className="max-w-6xl mx-auto">
      <div className="mb-6 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <h1 className="text-3xl font-bold text-home-text-dark">User Management</h1>
        <button
          onClick={() => setShowAddModal(true)}
          className="btn-primary inline-flex items-center gap-2"
        >
          <Plus size={20} />
          Add User
        </button>
      </div>

      {loading ? (
        <div className="card text-center py-12">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-home-primary-500 mx-auto"></div>
          <p className="mt-4 text-home-text-light">Loading...</p>
        </div>
      ) : users.length === 0 ? (
        <div className="card text-center py-12">
          <User className="mx-auto mb-4 text-home-primary-300" size={48} />
          <p className="text-home-text-light mb-4">No users yet</p>
          <button onClick={() => setShowAddModal(true)} className="btn-primary">
            Add First User
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {users.map((user) => (
            <div key={user.id} className="card">
              <div className="flex items-start justify-between mb-4">
                <div className="w-12 h-12 bg-home-primary-100 rounded-full flex items-center justify-center">
                  <User className="text-home-primary-600" size={24} />
                </div>
                <div className="flex gap-2">
                  <button
                    onClick={() => openEditModal(user)}
                    className="p-2 hover:bg-home-background-dark rounded-home transition-colors"
                  >
                    <Edit className="text-home-secondary-600" size={18} />
                  </button>
                  <button
                    onClick={() => handleDelete(user.id)}
                    className="p-2 hover:bg-home-background-dark rounded-home transition-colors"
                  >
                    <Trash2 className="text-home-error-600" size={18} />
                  </button>
                </div>
              </div>
              <h3 className="text-lg font-semibold text-home-text-dark mb-1">
                {user.display_name}
              </h3>
              {user.note && (
                <p className="text-sm text-home-text-light mb-4 line-clamp-2">
                  {user.note}
                </p>
              )}
              <div className="flex items-center gap-2 text-xs text-home-text-light">
                <FileText size={14} />
                <span>Created on {new Date(user.created_at).toLocaleDateString('en-US')}</span>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Add/Edit user modal */}
      {(showAddModal || editingUser) && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-home shadow-home-lg max-w-md w-full p-6">
            <h2 className="text-2xl font-bold text-home-text-dark mb-6">
              {editingUser ? 'Edit User' : 'Add User'}
            </h2>
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
                  rows={3}
                  placeholder="Enter note"
                />
              </div>
            </div>
            <div className="flex gap-4 mt-6">
              <button
                onClick={editingUser ? handleEdit : handleAdd}
                className="btn-primary flex-1"
              >
                {editingUser ? 'Save' : 'Add'}
              </button>
              <button onClick={closeModal} className="btn-secondary">
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default UsersPage
