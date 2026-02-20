import { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Calendar, User, FileText, MapPin, Tag, Trash2, Edit2, Save, X } from 'lucide-react'
import { documentService, userService, categoryService, locationService, ingestionService, Document, DocumentPage, DocumentFile, DocumentCategory, StorageLocation, CategoryTypeInfo } from '../api/services'
import { useAuth } from '../contexts/AuthContext'
import apiClient, { getApiBaseUrl } from '../api/client'

const _API_ORIGIN = getApiBaseUrl().replace(/\/api\/?$/, '')
const toAbsoluteUrl = (url: string | null | undefined): string =>
  !url ? '' : (url.startsWith('http://') || url.startsWith('https://')) ? url : _API_ORIGIN + (url.startsWith('/') ? url : '/' + url)
import MetadataViewer from '../components/metadata/MetadataViewer'

const DocumentDetailPage = () => {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { userId } = useAuth()
  const [document, setDocument] = useState<Document | null>(null)
  const [pages, setPages] = useState<DocumentPage[]>([])
  const [files, setFiles] = useState<DocumentFile[]>([])
  const [loading, setLoading] = useState(true)
  const [user, setUser] = useState<{ id: number; display_name: string } | null>(null)
  const [category, setCategory] = useState<DocumentCategory | null>(null)
  const [storageLocation, setStorageLocation] = useState<StorageLocation | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [selectedCategoryId, setSelectedCategoryId] = useState<number | null>(null)
  const [selectedLocationId, setSelectedLocationId] = useState<number | null>(null)
  const [categories, setCategories] = useState<DocumentCategory[]>([])
  const [locations, setLocations] = useState<StorageLocation[]>([])
  const [categoryTypes, setCategoryTypes] = useState<CategoryTypeInfo[]>([])

  useEffect(() => {
    const loadUser = async () => {
      if (!userId) return
      try {
        const userData = await userService.getById(userId)
        setUser({ id: userData.id, display_name: userData.display_name })
      } catch (error) {
        console.error('Failed to load user:', error)
      }
    }
    loadUser()
  }, [userId])

  // Load categories, locations, and category types
  useEffect(() => {
    const loadData = async () => {
      if (!userId) return
      try {
        const [categoriesRes, locationsRes, categoryConfig] = await Promise.all([
          categoryService.getByUserId(userId),
          locationService.getByUserId(userId),
          ingestionService.getCategoryConfig()
        ])
        setCategories(categoriesRes.categories || [])
        setLocations(locationsRes.locations || [])
        setCategoryTypes(categoryConfig.category_types || [])
      } catch (error) {
        console.error('Failed to load categories/locations:', error)
      }
    }
    loadData()
  }, [userId])

  const handleDelete = async () => {
    if (!id || !window.confirm('Are you sure you want to delete this document? This action cannot be undone.')) {
      return
    }

    try {
      setDeleting(true)
      await documentService.delete(parseInt(id))
      navigate('/documents')
    } catch (error) {
      console.error('Failed to delete document:', error)
      alert('Failed to delete document. Please try again.')
      setDeleting(false)
    }
  }

  useEffect(() => {
    // Listen for global document update events
    const handleDocumentUpdated = () => {
      // Reload document data
      if (id && userId) {
        const loadDocument = async () => {
          try {
            const pagesData = await documentService.getPages(parseInt(id))
            const finalDoc = pagesData.document || {
              id: parseInt(id),
              title: `Document #${id}`,
              owner_id: userId,
              created_at: new Date().toISOString(),
              updated_at: new Date().toISOString(),
            }
            setDocument(finalDoc)
            // Reload metadata-dependent state
            if (finalDoc.category_id) setSelectedCategoryId(finalDoc.category_id)
            if (finalDoc.current_location_id) setSelectedLocationId(finalDoc.current_location_id)
          } catch (error) {
            console.error('Failed to reload document:', error)
          }
        }
        loadDocument()
      }
    }

    window.addEventListener('document-updated', handleDocumentUpdated)
    return () => window.removeEventListener('document-updated', handleDocumentUpdated)
  }, [id, userId])

  useEffect(() => {
    const loadDocument = async () => {
      if (!id || !userId) return
      try {
        setLoading(true)
        
        // Use document from pages API
        const pagesData = await documentService.getPages(parseInt(id))
        
        // Backend now returns full document details including category_id and location_id
        const finalDoc = pagesData.document || {
          id: parseInt(id),
          title: `Document #${id}`,
          owner_id: userId,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        }
        
        // Verify document belongs to current user
        if (finalDoc.owner_id !== userId) {
          navigate('/documents')
          return
        }
        
        setDocument(finalDoc)
        
        // Backend now returns full page details including image_url and unique files
        setPages(pagesData.pages || [])
        setFiles(pagesData.files || [])
        
        // Load category if available
        if (finalDoc.category_id != null && finalDoc.category_id > 0) {
          try {
            const cat = await categoryService.getById(userId, finalDoc.category_id)
            if (cat) {
              setCategory(cat)
            }
          } catch (error) {
            console.error('Failed to load category:', error)
          }
        }
        
        // Load location if available
        if (finalDoc.current_location_id != null && finalDoc.current_location_id > 0) {
          try {
            const loc = await locationService.getById(userId, finalDoc.current_location_id)
            if (loc) {
              setStorageLocation(loc)
            }
          } catch (error) {
            console.error('Failed to load location:', error)
          }
        }

        // Initialize edit form values
        setSelectedCategoryId(finalDoc.category_id || null)
        setSelectedLocationId(finalDoc.current_location_id || null)
      } catch (error) {
        console.error('Failed to load document:', error)
      } finally {
        setLoading(false)
      }
    }

    loadDocument()
  }, [id, userId, navigate])

  const handleStartEdit = () => {
    if (document) {
      setSelectedCategoryId(document.category_id || null)
      setSelectedLocationId(document.current_location_id || null)
      setEditing(true)
    }
  }

  const handleCancelEdit = () => {
    if (document) {
      setSelectedCategoryId(document.category_id || null)
      setSelectedLocationId(document.current_location_id || null)
      setEditing(false)
    }
  }

  const handleSave = async () => {
    if (!document || !userId || !id) return

    setSaving(true)
    try {
      // Direct update using the new PATCH endpoint
      const updateData = {
        category_id: selectedCategoryId,
        current_location_id: selectedLocationId !== null ? selectedLocationId : -1,
        metadata: document.metadata
      }

      const result = await documentService.update(document.id, updateData)

      if (result.status === 'updated') {
        // Reload document to get updated data
        const pagesData = await documentService.getPages(parseInt(id))
        const updatedDoc = pagesData.document || document
        
        setDocument(updatedDoc)
        
        // Reload category and location
        if (selectedCategoryId) {
          try {
            const cat = await categoryService.getById(userId, selectedCategoryId)
            setCategory(cat)
          } catch (error) {
            console.error('Failed to load category:', error)
          }
        } else {
          setCategory(null)
        }

        if (selectedLocationId && selectedLocationId > 0) {
          try {
            const loc = await locationService.getById(userId, selectedLocationId)
            setStorageLocation(loc)
          } catch (error) {
            console.error('Failed to load location:', error)
          }
        } else {
          setStorageLocation(null)
        }

        setEditing(false)
      } else {
        alert('Failed to update document. Please try again.')
      }
    } catch (error: any) {
      console.error('Failed to update document:', error)
      alert(`Failed to update document: ${error.response?.data?.detail || error.message}`)
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <div className="max-w-4xl mx-auto">
        <div className="card text-center py-12">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-home-primary-500 mx-auto"></div>
          <p className="mt-4 text-home-text-light">Loading...</p>
        </div>
      </div>
    )
  }

  if (!document) {
    return (
      <div className="max-w-4xl mx-auto">
        <div className="card text-center py-12">
          <p className="text-home-text-light">Document not found</p>
        </div>
      </div>
    )
  }

  return (
    <div className="max-w-4xl mx-auto">
      {/* Document info card */}
      <div className="card mb-6">
        <div className="flex flex-col md:flex-row gap-6">
          <div className="flex-1">
            <div className="flex flex-col sm:flex-row sm:justify-between sm:items-start gap-3 mb-4">
              <h1 className="text-2xl sm:text-3xl font-bold text-home-text-dark leading-tight">
                {(() => {
                  // If title is "Document page X" format, use Document #ID instead
                  if (document.title && /^Document page \d+$/i.test(document.title)) {
                    return `Document #${document.id}`
                  }
                  // If title is empty or just "Document #X", try to use a better default
                  if (!document.title || document.title === `Document #${document.id}`) {
                    return `Document #${document.id}`
                  }
                  return document.title
                })()}
              </h1>
              <div className="flex gap-2 flex-shrink-0">
                {!editing ? (
                  <>
                    <button
                      onClick={handleStartEdit}
                      className="btn-secondary flex items-center gap-2 flex-1 sm:flex-none justify-center"
                    >
                      <Edit2 size={18} />
                      Edit
                    </button>
                    <button
                      onClick={handleDelete}
                      disabled={deleting}
                      className="btn-danger flex items-center gap-2 flex-1 sm:flex-none justify-center"
                    >
                      <Trash2 size={18} />
                      {deleting ? 'Deleting...' : 'Delete'}
                    </button>
                  </>
                ) : (
                  <>
                    <button
                      onClick={handleSave}
                      disabled={saving}
                      className="btn-primary flex items-center gap-2 flex-1 sm:flex-none justify-center"
                    >
                      <Save size={18} />
                      {saving ? 'Saving...' : 'Save'}
                    </button>
                    <button
                      onClick={handleCancelEdit}
                      disabled={saving}
                      className="btn-secondary flex items-center gap-2 flex-1 sm:flex-none justify-center"
                    >
                      <X size={18} />
                      Cancel
                    </button>
                  </>
                )}
              </div>
            </div>
            <div className="space-y-3">
              <div className="flex items-center gap-2 text-home-text-light">
                <Calendar size={18} />
                <span>Created: {new Date(document.created_at).toLocaleString('en-US')}</span>
              </div>
              <div className="flex items-center gap-2 text-home-text-light">
                <User size={18} />
                {user && <span>Owner: {user.display_name}</span>}
              </div>
              {pages.length > 0 && (
                <div className="flex items-center gap-2 text-home-text-light">
                  <FileText size={18} />
                  <span>Page IDs: {pages.map(p => p.id).join(', ')}</span>
                </div>
              )}
              {editing ? (
                <>
                  {/* Category Selection */}
                  <div className="mb-4">
                    <label className="block text-sm font-medium text-home-text-dark mb-2">
                      Document Category *
                    </label>
                    <select
                      value={selectedCategoryId || ''}
                      onChange={async (e) => {
                        const value = e.target.value
                        if (!value) {
                          setSelectedCategoryId(null)
                          return
                        }
                        
                        const categoryId = parseInt(value)
                        if (!isNaN(categoryId)) {
                          setSelectedCategoryId(categoryId)
                        } else {
                          // It's a category_code - need to create or find the category
                          const categoryType = categoryTypes.find(ct => ct.code === value)
                          if (categoryType && userId) {
                            let existingCategory = categories.find(cat => cat.code === categoryType.code)
                            
                            if (!existingCategory) {
                              try {
                                await apiClient.post(`/users/${userId}/categories`, {
                                  code: categoryType.code,
                                  name: categoryType.name,
                                  description: categoryType.description
                                })
                                const categoriesRes = await categoryService.getByUserId(userId)
                                setCategories(categoriesRes.categories || [])
                                existingCategory = categoriesRes.categories.find(cat => cat.code === categoryType.code)
                              } catch (error: any) {
                                if (error.response?.status === 400 && userId) {
                                  const categoriesRes = await categoryService.getByUserId(userId)
                                  setCategories(categoriesRes.categories || [])
                                  existingCategory = categoriesRes.categories.find(cat => cat.code === categoryType.code)
                                } else {
                                  alert(`Failed to create category: ${error.response?.data?.detail || error.message}`)
                                  return
                                }
                              }
                            }
                            
                            if (existingCategory) {
                              setSelectedCategoryId(existingCategory.id)
                            }
                          }
                        }
                      }}
                      className="input"
                    >
                      <option value="">-- Select Category --</option>
                      {categories.map((cat) => (
                        <option key={cat.id} value={cat.id}>
                          {cat.name} ({cat.code})
                        </option>
                      ))}
                      {categoryTypes
                        .filter(catType => !categories.some(cat => cat.code === catType.code))
                        .map((catType) => (
                          <option key={catType.code} value={catType.code}>
                            {catType.name} ({catType.code})
                          </option>
                        ))}
                    </select>
                  </div>

                  {/* Location Selection */}
                  <div className="mb-4">
                    <label className="block text-sm font-medium text-home-text-dark mb-2">
                      Storage Location
                    </label>
                    <select
                      value={selectedLocationId !== null && selectedLocationId !== -1 ? selectedLocationId : ''}
                      onChange={(e) => {
                        const value = e.target.value
                        setSelectedLocationId(value ? parseInt(value) : -1)
                      }}
                      className="input"
                    >
                      <option value="">-- No Location --</option>
                      {locations.map((loc) => (
                        <option key={loc.id} value={loc.id}>
                          {loc.name}
                        </option>
                      ))}
                    </select>
                  </div>
                </>
              ) : (
                <>
                  {(document.current_location_id != null && document.current_location_id > 0) ? (
                    <div className="flex items-center gap-2 text-home-text-light">
                      <MapPin size={18} />
                      <span>
                        Storage Location: {storageLocation ? (
                          <>
                            {storageLocation.name}
                            {storageLocation.description && ` (${storageLocation.description})`}
                          </>
                        ) : (
                          `#${document.current_location_id}`
                        )}
                      </span>
                    </div>
                  ) : (
                    <div className="flex items-center gap-2 text-home-text-light">
                      <MapPin size={18} />
                      <span>
                        Storage Location: {document.metadata?.suggested_storage ? (
                          <span className="text-amber-600 font-medium">
                            AI Suggested: {document.metadata.suggested_storage}
                          </span>
                        ) : 'No Location'}
                      </span>
                    </div>
                  )}
                  {(document.category_id != null && document.category_id > 0) ? (
                    <div className="flex items-center gap-2 text-home-text-light">
                      <Tag size={18} />
                      <span>
                        Category: {category ? (
                          <>
                            {category.name} ({category.code})
                            {category.description && ` - ${category.description}`}
                          </>
                        ) : (
                          `#${document.category_id}`
                        )}
                      </span>
                    </div>
                  ) : (
                    <div className="flex items-center gap-2 text-home-text-light">
                      <Tag size={18} />
                      <span>Category: No Category</span>
                    </div>
                  )}
                </>
              )}
            </div>
            {document.metadata && Object.keys(document.metadata).length > 0 && (
              <div className="mt-6 pt-6 border-t border-home-primary-100">
                <h3 className="font-semibold text-home-text-dark mb-4">Metadata</h3>
                <MetadataViewer 
                  metadata={document.metadata} 
                  categoryCode={category?.code}
                  isEditing={editing}
                  onMetadataChange={(newMetadata) => {
                    setDocument({
                      ...document,
                      metadata: newMetadata
                    });
                  }}
                />
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Document files preview */}
      {files.length > 0 ? (
        <div className="card">
          <h2 className="text-xl font-semibold text-home-text-dark mb-4">
            File Preview ({files.length} {files.length === 1 ? 'file' : 'files'})
          </h2>
          <div className="grid grid-cols-1 gap-6">
            {files.map((file, index) => {
              return (
                <div key={`${file.url}-${index}`} className="border border-home-primary-100 rounded-home p-4">
                  <div className="mb-2">
                    <span className="text-sm text-home-text-light">
                      {file.file_type === 'pdf' ? 'PDF File' : 'Image File'}
                    </span>
                  </div>
                  {file.file_type === 'pdf' ? (
                    <div className="w-full h-[600px] rounded-home overflow-hidden bg-home-background-dark">
                      <iframe
                        src={toAbsoluteUrl(file.url)}
                        className="w-full h-full border-0"
                        title={`PDF Preview ${index + 1}`}
                      />
                    </div>
                  ) : (
                    <div className="rounded-home overflow-hidden bg-home-background-dark">
                      <img
                        src={toAbsoluteUrl(file.url)}
                        alt={`File ${index + 1}`}
                        className="w-full h-auto max-h-[600px] object-contain"
                      />
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      ) : (
        <div className="card text-center py-12">
          <FileText className="mx-auto mb-4 text-home-primary-300" size={48} />
          <p className="text-home-text-light">No file preview available</p>
        </div>
      )}
    </div>
  )
}

export default DocumentDetailPage
