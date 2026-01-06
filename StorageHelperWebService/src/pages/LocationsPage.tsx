import { useState, useEffect, useCallback } from 'react'
import { MapPin, FileText, Plus, Edit2, Trash2, X } from 'lucide-react'
import { locationService, StorageLocation } from '../api/services'
import { useAuth } from '../contexts/AuthContext'

interface LocationFormData {
  name: string
  description: string
  photo_url: string
}

// Modal component extracted outside to prevent re-creation on each render
const Modal = ({ show, onClose, title, children }: { show: boolean; onClose: () => void; title: string; children: React.ReactNode }) => {
  if (!show) return null

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl max-w-md w-full mx-4 max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between p-6 border-b">
          <h2 className="text-xl font-semibold text-home-text-dark">{title}</h2>
          <button
            onClick={onClose}
            className="text-home-text-light hover:text-home-text-dark"
          >
            <X size={24} />
          </button>
        </div>
        <div className="p-6">
          {children}
        </div>
      </div>
    </div>
  )
}

const LocationsPage = () => {
  const { userId } = useAuth()
  const [locations, setLocations] = useState<StorageLocation[]>([])
  const [loading, setLoading] = useState(true)
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [showEditModal, setShowEditModal] = useState(false)
  const [showDeleteModal, setShowDeleteModal] = useState(false)
  const [editingLocation, setEditingLocation] = useState<StorageLocation | null>(null)
  const [deletingLocation, setDeletingLocation] = useState<StorageLocation | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [locationDocuments, setLocationDocuments] = useState<{ [key: number]: number }>({})
  const [formData, setFormData] = useState<LocationFormData>({
    name: '',
    description: '',
    photo_url: ''
  })
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [uploadingImage, setUploadingImage] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    if (userId) {
      loadLocations(userId)
    }
  }, [userId])

  const loadLocations = async (userId: number) => {
    try {
      setLoading(true)
      const response = await locationService.getByUserId(userId)
      setLocations(response.locations)
      
      // Load document counts for each location
      const docCounts: { [key: number]: number } = {}
      for (const location of response.locations) {
        try {
          const docsResponse = await locationService.getLocationDocuments(userId, location.id)
          docCounts[location.id] = docsResponse.total
        } catch (error) {
          console.error(`Failed to load documents for location ${location.id}:`, error)
          docCounts[location.id] = 0
        }
      }
      setLocationDocuments(docCounts)
    } catch (error) {
      console.error('Failed to load locations:', error)
      setLocations([])
    } finally {
      setLoading(false)
    }
  }

  const handleCreate = () => {
    setFormData({ name: '', description: '', photo_url: '' })
    setSelectedFile(null)
    setFormError(null)
    setShowCreateModal(true)
  }

  const handleEdit = (location: StorageLocation) => {
    setEditingLocation(location)
    setFormData({
      name: location.name,
      description: location.description || '',
      photo_url: location.photo_url || ''
    })
    setSelectedFile(null)
    setFormError(null)
    setShowEditModal(true)
  }

  const handleDelete = async (location: StorageLocation) => {
    setDeletingLocation(location)
    setDeleteError(null)
    setShowDeleteModal(true)
  }

  const handleImageSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) {
      if (!file.type.startsWith('image/')) {
        setFormError('Please select an image file')
        return
      }
      setSelectedFile(file)
    }
  }

  const handleCreateSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!userId) return

    if (!formData.name.trim()) {
      setFormError('Location name is required')
      return
    }

    try {
      setSubmitting(true)
      setFormError(null)
      
      let photoUrl: string | undefined = undefined
      
      if (selectedFile) {
        try {
          setUploadingImage(true)
          const uploadResult = await locationService.uploadImage(userId, selectedFile)
          photoUrl = uploadResult.image_url
          setUploadingImage(false)
        } catch (imageError: any) {
          console.error('Failed to upload image:', imageError)
          setFormError(imageError.response?.data?.detail || 'Failed to upload image')
          setUploadingImage(false)
          setSubmitting(false)
          return
        }
      }
      
      await locationService.create(userId, {
        name: formData.name.trim(),
        description: formData.description.trim() || undefined,
        photo_url: photoUrl
      })
      setShowCreateModal(false)
      setSelectedFile(null)
      await loadLocations(userId)
    } catch (error: any) {
      console.error('Failed to create location:', error)
      setFormError(error.response?.data?.detail || 'Failed to create location')
    } finally {
      setSubmitting(false)
    }
  }

  const handleEditSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!userId || !editingLocation) return

    if (!formData.name.trim()) {
      setFormError('Location name is required')
      return
    }

    try {
      setSubmitting(true)
      setFormError(null)
      
      let photoUrl: string | undefined = formData.photo_url
      
      if (selectedFile) {
        try {
          setUploadingImage(true)
          const uploadResult = await locationService.uploadImage(userId, selectedFile)
          photoUrl = uploadResult.image_url
          setUploadingImage(false)
        } catch (imageError: any) {
          console.error('Failed to upload image:', imageError)
          setFormError(imageError.response?.data?.detail || 'Failed to upload image')
          setUploadingImage(false)
          setSubmitting(false)
          return
        }
      }
      
      await locationService.update(userId, editingLocation.id, {
        name: formData.name.trim(),
        description: formData.description.trim() || undefined,
        photo_url: photoUrl
      })
      setShowEditModal(false)
      setEditingLocation(null)
      setSelectedFile(null)
      await loadLocations(userId)
    } catch (error: any) {
      console.error('Failed to update location:', error)
      setFormError(error.response?.data?.detail || 'Failed to update location')
    } finally {
      setSubmitting(false)
    }
  }

  // Use useCallback to stabilize the onChange handlers
  const handleNameChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    setFormData(prev => ({ ...prev, name: e.target.value }))
  }, [])

  const handleDescriptionChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setFormData(prev => ({ ...prev, description: e.target.value }))
  }, [])

  const handleDeleteConfirm = async () => {
    if (!userId || !deletingLocation) return

    try {
      setSubmitting(true)
      setDeleteError(null)
      await locationService.delete(userId, deletingLocation.id)
      setShowDeleteModal(false)
      setDeletingLocation(null)
      await loadLocations(userId)
    } catch (error: any) {
      console.error('Failed to delete location:', error)
      const errorDetail = error.response?.data?.detail
      if (typeof errorDetail === 'object' && errorDetail?.error) {
        // Handle structured error response
        setDeleteError(errorDetail.message || errorDetail.error)
      } else {
        setDeleteError(typeof errorDetail === 'string' ? errorDetail : 'Failed to delete location. Make sure the location is empty before deleting.')
      }
    } finally {
      setSubmitting(false)
    }
  }


  return (
    <div className="max-w-6xl mx-auto">
      <div className="mb-6 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <h1 className="text-3xl font-bold text-home-text-dark">Storage Locations</h1>
        {userId && (
          <button
            onClick={handleCreate}
            className="btn-primary flex items-center gap-2"
          >
            <Plus size={20} />
            Add Location
          </button>
        )}
      </div>

      {loading ? (
        <div className="card text-center py-12">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-home-primary-500 mx-auto"></div>
          <p className="mt-4 text-home-text-light">Loading...</p>
        </div>
      ) : locations.length === 0 ? (
        <div className="card text-center py-12">
          <MapPin className="mx-auto mb-4 text-home-primary-300" size={48} />
          <p className="text-home-text-light mb-4">No locations found for this user</p>
          <button
            onClick={handleCreate}
            className="btn-primary inline-flex items-center gap-2"
          >
            <Plus size={20} />
            Create First Location
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {locations.map((location) => {
            const docCount = locationDocuments[location.id] || 0
            return (
              <div key={location.id} className="card">
                <div className="flex items-start justify-between mb-4">
                  <div className="w-12 h-12 bg-home-primary-100 rounded-full flex items-center justify-center">
                    <MapPin className="text-home-primary-600" size={24} />
                  </div>
                  <div className="flex gap-2">
                    <button
                      onClick={() => handleEdit(location)}
                      className="p-2 text-home-primary-600 hover:bg-home-primary-50 rounded-home transition-colors"
                      title="Edit location"
                    >
                      <Edit2 size={18} />
                    </button>
                    <button
                      onClick={() => handleDelete(location)}
                      className="p-2 text-red-600 hover:bg-red-50 rounded-home transition-colors"
                      title="Delete location"
                    >
                      <Trash2 size={18} />
                    </button>
                  </div>
                </div>
                <h3 className="text-lg font-semibold text-home-text-dark mb-2">
                  {location.name}
                </h3>
                {location.description && (
                  <p className="text-sm text-home-text-light mb-4 line-clamp-3">
                    {location.description}
                  </p>
                )}
                {location.photo_url && (
                  <div className="mb-4 rounded-home overflow-hidden">
                    <img
                      src={location.photo_url}
                      alt={location.name}
                      className="w-full h-32 object-cover"
                    />
                  </div>
                )}
                <div className="flex items-center justify-between text-xs text-home-text-light">
                  <div className="flex items-center gap-2">
                    <FileText size={14} />
                    <span>{docCount} document{docCount !== 1 ? 's' : ''}</span>
                  </div>
                  <span>ID: {location.id}</span>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* Create Modal */}
      <Modal show={showCreateModal} onClose={() => setShowCreateModal(false)} title="Create New Location">
        <form onSubmit={handleCreateSubmit}>
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-home-text-dark mb-1">
                Name <span className="text-red-500">*</span>
              </label>
              <input
                type="text"
                value={formData.name}
                onChange={handleNameChange}
                className="input w-full"
                required
                placeholder="e.g., Bedroom desk, left drawer #2"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-home-text-dark mb-1">
                Description
              </label>
              <textarea
                value={formData.description}
                onChange={handleDescriptionChange}
                className="input w-full"
                rows={3}
                placeholder="Optional description"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-home-text-dark mb-1">
                Location Photo (Optional)
              </label>
              <input
                type="file"
                accept="image/*"
                onChange={handleImageSelect}
                className="w-full px-3 py-2 border border-home-border rounded-lg text-sm"
                disabled={uploadingImage || submitting}
              />
              {selectedFile && (
                <p className="text-sm text-green-600 mt-1">
                  ✓ {selectedFile.name}
                </p>
              )}
            </div>
            {formError && (
              <div className="p-3 bg-red-50 border border-red-200 rounded-home text-red-700 text-sm">
                {formError}
              </div>
            )}
            <div className="flex gap-3 justify-end">
              <button
                type="button"
                onClick={() => setShowCreateModal(false)}
                className="btn-secondary"
                disabled={submitting}
              >
                Cancel
              </button>
              <button
                type="submit"
                className="btn-primary"
                disabled={submitting}
              >
                {submitting ? 'Creating...' : 'Create'}
              </button>
            </div>
          </div>
        </form>
      </Modal>

      {/* Edit Modal */}
      <Modal show={showEditModal} onClose={() => setShowEditModal(false)} title="Edit Location">
        <form onSubmit={handleEditSubmit}>
          <div className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-home-text-dark mb-1">
                Name <span className="text-red-500">*</span>
              </label>
              <input
                type="text"
                value={formData.name}
                onChange={handleNameChange}
                className="input w-full"
                required
                placeholder="e.g., Bedroom desk, left drawer #2"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-home-text-dark mb-1">
                Description
              </label>
              <textarea
                value={formData.description}
                onChange={handleDescriptionChange}
                className="input w-full"
                rows={3}
                placeholder="Optional description"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-home-text-dark mb-1">
                Location Photo (Optional)
              </label>
              <input
                type="file"
                accept="image/*"
                onChange={handleImageSelect}
                className="w-full px-3 py-2 border border-home-border rounded-lg text-sm"
                disabled={uploadingImage || submitting}
              />
              {selectedFile && (
                <p className="text-sm text-green-600 mt-1">
                  ✓ {selectedFile.name}
                </p>
              )}
            </div>
            {formError && (
              <div className="p-3 bg-red-50 border border-red-200 rounded-home text-red-700 text-sm">
                {formError}
              </div>
            )}
            <div className="flex gap-3 justify-end">
              <button
                type="button"
                onClick={() => setShowEditModal(false)}
                className="btn-secondary"
                disabled={submitting}
              >
                Cancel
              </button>
              <button
                type="submit"
                className="btn-primary"
                disabled={submitting}
              >
                {submitting ? 'Saving...' : 'Save'}
              </button>
            </div>
          </div>
        </form>
      </Modal>

      {/* Delete Confirmation Modal */}
      <Modal show={showDeleteModal} onClose={() => setShowDeleteModal(false)} title="Delete Location">
        <div className="space-y-4">
          {deletingLocation && (
            <>
              <p className="text-home-text-dark">
                Are you sure you want to delete <strong>"{deletingLocation.name}"</strong>?
              </p>
              {locationDocuments[deletingLocation.id] > 0 && (
                <div className="p-3 bg-yellow-50 border border-yellow-200 rounded-home text-yellow-800 text-sm">
                  <p className="font-medium mb-1">Warning:</p>
                  <p>This location contains {locationDocuments[deletingLocation.id]} document(s). 
                  You must move all documents out of this location before deleting it.</p>
                </div>
              )}
              {deleteError && (
                <div className="p-3 bg-red-50 border border-red-200 rounded-home text-red-700 text-sm">
                  {deleteError}
                </div>
              )}
            </>
          )}
          <div className="flex gap-3 justify-end">
            <button
              type="button"
              onClick={() => {
                setShowDeleteModal(false)
                setDeleteError(null)
              }}
              className="btn-secondary"
              disabled={submitting}
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleDeleteConfirm}
              className="btn-primary bg-red-600 hover:bg-red-700"
              disabled={submitting || (deletingLocation && locationDocuments[deletingLocation.id] > 0)}
            >
              {submitting ? 'Deleting...' : 'Delete'}
            </button>
          </div>
        </div>
      </Modal>
    </div>
  )
}

export default LocationsPage
