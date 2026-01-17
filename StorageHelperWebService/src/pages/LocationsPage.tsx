import { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { MapPin, FileText, Plus, Edit2, Trash2, X, ChevronRight, Search, Clock } from 'lucide-react'
import { locationService, documentService, categoryService, Document, StorageLocation } from '../api/services'
import { useAuth } from '../contexts/AuthContext'
import CategoryIcon from '../components/CategoryIcon'

interface LocationFormData {
  name: string
  description: string
  photo_url: string
}

// Modern Slide-over / Drawer Component
const Drawer = ({ show, onClose, title, children }: { show: boolean; onClose: () => void; title: string; children: React.ReactNode }) => {
  return (
    <>
      {/* Backdrop */}
      <div 
        className={`fixed inset-0 bg-black/40 backdrop-blur-sm transition-opacity duration-300 z-[60] ${show ? 'opacity-100' : 'opacity-0 pointer-events-none'}`}
        onClick={onClose}
      />
      {/* Drawer Content */}
      <div className={`fixed top-0 right-0 h-full w-full max-w-lg bg-home-background-light shadow-2xl transition-transform duration-500 ease-in-out z-[70] transform ${show ? 'translate-x-0' : 'translate-x-full'}`}>
        <div className="flex flex-col h-full">
          <div className="flex items-center justify-between p-6 border-b border-home-primary-100 bg-white">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 bg-home-primary-50 rounded-xl flex items-center justify-center">
                <MapPin className="text-home-primary-600" size={20} />
              </div>
              <h2 className="text-xl font-bold text-home-text-dark">{title}</h2>
            </div>
            <button
              onClick={onClose}
              className="p-2 hover:bg-home-background-dark rounded-full transition-colors"
            >
              <X size={24} className="text-home-text-light" />
            </button>
          </div>
          <div className="flex-1 overflow-y-auto p-6">
            {children}
          </div>
        </div>
      </div>
    </>
  )
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

  // Document viewing states
  const [selectedLocationForDocs, setSelectedLocationForDocs] = useState<StorageLocation | null>(null)
  const [docsInLocation, setDocsInLocation] = useState<(Document & { category_code?: string })[]>([])
  const [loadingDocs, setLoadingDocs] = useState(false)
  const [docSearchQuery, setDocSearchQuery] = useState('')
  const [allCategories, setAllCategories] = useState<{ [key: number]: string }>({})

  useEffect(() => {
    if (userId) {
      loadLocations(userId)
      // Load categories for icon mapping
      categoryService.getByUserId(userId).then(res => {
        const catMap: { [key: number]: string } = {}
        res.categories.forEach(c => {
          catMap[c.id] = c.code
        })
        setAllCategories(catMap)
      })
    }
  }, [userId])

  const loadDocsForLocation = async (location: StorageLocation) => {
    if (!userId) return
    try {
      setLoadingDocs(true)
      setSelectedLocationForDocs(location)
      const response = await locationService.getLocationDocuments(userId, location.id)
      
      // Fetch full document details for each ID
      const fullDocs = await Promise.all(
        response.document_ids.map(async (id: number) => {
          try {
            const doc = await documentService.getById(id)
            return {
              ...doc,
              category_code: doc.category_id ? allCategories[doc.category_id] : undefined
            }
          } catch (e) {
            console.error(`Failed to fetch doc ${id}`, e)
            return null
          }
        })
      )
      
      setDocsInLocation(fullDocs.filter(d => d !== null) as (Document & { category_code?: string })[])
    } catch (error) {
      console.error('Failed to load docs for location:', error)
      setDocsInLocation([])
    } finally {
      setLoadingDocs(false)
    }
  }

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
              <div 
                key={location.id} 
                className="group bg-white rounded-2xl border border-home-primary-100 shadow-sm hover:shadow-home-lg transition-all duration-300 overflow-hidden flex flex-col cursor-pointer"
                onClick={() => loadDocsForLocation(location)}
              >
                <div className="p-6">
                  <div className="flex items-start justify-between mb-4">
                    <div className="w-12 h-12 bg-home-primary-50 text-home-primary-600 rounded-xl flex items-center justify-center group-hover:scale-110 transition-transform duration-300">
                      <MapPin size={24} />
                    </div>
                    <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity" onClick={e => e.stopPropagation()}>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleEdit(location);
                        }}
                        className="p-2 text-home-text-light hover:text-home-primary-600 hover:bg-home-primary-50 rounded-lg transition-colors"
                        title="Edit location"
                      >
                        <Edit2 size={18} />
                      </button>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleDelete(location);
                        }}
                        className="p-2 text-home-text-light hover:text-red-600 hover:bg-red-50 rounded-lg transition-colors"
                        title="Delete location"
                      >
                        <Trash2 size={18} />
                      </button>
                    </div>
                  </div>
                  <h3 className="text-xl font-bold text-home-text-dark mb-2 group-hover:text-home-primary-600 transition-colors">
                    {location.name}
                  </h3>
                  {location.description && (
                    <p className="text-sm text-home-text-light mb-4 line-clamp-2">
                      {location.description}
                    </p>
                  )}
                  {location.photo_url && (
                    <div className="mb-4 rounded-xl overflow-hidden aspect-video bg-home-background-dark">
                      <img
                        src={location.photo_url}
                        alt={location.name}
                        className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-500"
                      />
                    </div>
                  )}
                  <div className="flex items-center justify-between pt-4 border-t border-home-primary-50">
                    <div className="flex items-center gap-2 text-sm font-semibold text-home-primary-600">
                      <FileText size={16} />
                      <span>{docCount} item{docCount !== 1 ? 's' : ''}</span>
                    </div>
                    <ChevronRight size={18} className="text-home-primary-300 group-hover:translate-x-1 transition-transform" />
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* Location Documents Drawer */}
      <Drawer 
        show={!!selectedLocationForDocs} 
        onClose={() => setSelectedLocationForDocs(null)} 
        title={selectedLocationForDocs?.name || 'Items'}
      >
        <div className="space-y-6">
          {/* Search in Drawer */}
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-home-text-light" size={18} />
            <input 
              type="text" 
              placeholder="Search items in this location..."
              className="input pl-10 w-full bg-white border-home-primary-100 focus:border-home-primary-500"
              value={docSearchQuery}
              onChange={(e) => setDocSearchQuery(e.target.value)}
            />
          </div>

          {loadingDocs ? (
            <div className="py-20 text-center">
              <div className="animate-spin rounded-full h-10 w-10 border-b-2 border-home-primary-500 mx-auto"></div>
              <p className="mt-4 text-home-text-light font-medium text-sm">Finding items...</p>
            </div>
          ) : docsInLocation.length === 0 ? (
            <div className="py-20 text-center bg-white rounded-2xl border-2 border-dashed border-home-primary-100">
              <FileText className="mx-auto mb-4 text-home-primary-200" size={48} />
              <p className="text-home-text-light font-medium">This location is empty</p>
            </div>
          ) : (
            <div className="space-y-3">
              {(() => {
                const filtered = docsInLocation.filter(doc => 
                  (doc.title || doc.metadata?.product_name || '').toLowerCase().includes(docSearchQuery.toLowerCase())
                );
                
                if (filtered.length === 0 && docSearchQuery) {
                  return (
                    <div className="py-10 text-center">
                      <p className="text-home-text-light text-sm">No items match "{docSearchQuery}"</p>
                    </div>
                  );
                }
                
                return filtered.map((doc) => {
                  const isFood = doc.metadata?.is_food;
                  return (
                    <Link
                      key={doc.id}
                      to={`/documents/${doc.id}`}
                      className="flex items-center gap-4 p-4 bg-white rounded-2xl border border-home-primary-100 hover:border-home-primary-300 hover:shadow-home-sm transition-all group"
                    >
                      <div className="w-12 h-12 rounded-xl bg-home-background-dark overflow-hidden flex-shrink-0 flex items-center justify-center">
                        {doc.image_url ? (
                          <img src={doc.image_url} className="w-full h-full object-cover" />
                        ) : (
                          <CategoryIcon categoryCode={doc.category_code || (isFood ? 'VEGETABLE' : 'UNKNOWN')} size={20} />
                        )}
                      </div>
                      <div className="flex-1 min-w-0">
                        <h4 className="font-bold text-home-text-dark truncate text-sm">
                          {doc.metadata?.product_name || doc.metadata?.merchant || doc.title || `Item #${doc.id}`}
                        </h4>
                        <div className="flex items-center gap-2 mt-1">
                          <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-bold bg-home-primary-50 text-home-primary-700 uppercase tracking-wider">
                            {isFood ? 'Food' : 'Document'}
                          </span>
                          <span className="text-[10px] text-home-text-light flex items-center gap-1">
                            <Clock size={10} />
                            {new Date(doc.created_at).toLocaleDateString()}
                          </span>
                        </div>
                      </div>
                      <ChevronRight size={18} className="text-home-primary-200 group-hover:text-home-primary-500 transform group-hover:translate-x-1 transition-all" />
                    </Link>
                  );
                });
              })()}
            </div>
          )}
        </div>
      </Drawer>

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
              disabled={submitting || (!!deletingLocation && locationDocuments[deletingLocation.id] > 0)}
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
