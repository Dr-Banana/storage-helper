import { useState, useEffect } from 'react'
import { Tag, User, FileText } from 'lucide-react'
import { userService, categoryService, DocumentCategory } from '../api/services'

const CategoriesPage = () => {
  const [users, setUsers] = useState<Array<{ id: number; display_name: string }>>([])
  const [selectedUserId, setSelectedUserId] = useState<number | null>(null)
  const [categories, setCategories] = useState<DocumentCategory[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const loadUsers = async () => {
      try {
        const response = await userService.getAll()
        setUsers(response.users)
        // Auto-select first user if available
        if (response.users.length > 0) {
          setSelectedUserId(response.users[0].id)
        }
      } catch (error) {
        console.error('Failed to load users:', error)
      }
    }
    loadUsers()
  }, [])

  useEffect(() => {
    if (selectedUserId) {
      loadCategories(selectedUserId)
    }
  }, [selectedUserId])

  const loadCategories = async (userId: number) => {
    try {
      setLoading(true)
      const response = await categoryService.getByUserId(userId)
      setCategories(response.categories)
    } catch (error) {
      console.error('Failed to load categories:', error)
      setCategories([])
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="max-w-6xl mx-auto">
      <div className="mb-6 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <h1 className="text-3xl font-bold text-home-text-dark">Document Categories</h1>
        <div className="flex items-center gap-4">
          <label className="text-sm font-medium text-home-text-dark">User:</label>
          <select
            value={selectedUserId || ''}
            onChange={(e) => setSelectedUserId(Number(e.target.value))}
            className="input"
          >
            <option value="">Select a user</option>
            {users.map((user) => (
              <option key={user.id} value={user.id}>
                {user.display_name}
              </option>
            ))}
          </select>
        </div>
      </div>

      {loading ? (
        <div className="card text-center py-12">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-home-primary-500 mx-auto"></div>
          <p className="mt-4 text-home-text-light">Loading...</p>
        </div>
      ) : !selectedUserId ? (
        <div className="card text-center py-12">
          <Tag className="mx-auto mb-4 text-home-primary-300" size={48} />
          <p className="text-home-text-light">Please select a user to view categories</p>
        </div>
      ) : categories.length === 0 ? (
        <div className="card text-center py-12">
          <Tag className="mx-auto mb-4 text-home-primary-300" size={48} />
          <p className="text-home-text-light mb-4">No categories found for this user</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {categories.map((category) => (
            <div key={category.id} className="card">
              <div className="flex items-start mb-4">
                <div className="w-12 h-12 bg-home-primary-100 rounded-full flex items-center justify-center">
                  <Tag className="text-home-primary-600" size={24} />
                </div>
              </div>
              <h3 className="text-lg font-semibold text-home-text-dark mb-1">
                {category.name}
              </h3>
              <div className="mb-2">
                <span className="inline-block px-2 py-1 bg-home-secondary-100 text-home-secondary-700 text-xs font-medium rounded-home">
                  {category.code}
                </span>
              </div>
              {category.description && (
                <p className="text-sm text-home-text-light mb-4 line-clamp-3">
                  {category.description}
                </p>
              )}
              <div className="flex items-center gap-2 text-xs text-home-text-light">
                <FileText size={14} />
                <span>ID: {category.id}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default CategoriesPage

