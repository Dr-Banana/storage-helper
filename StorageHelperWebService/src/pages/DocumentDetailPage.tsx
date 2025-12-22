import { useState, useEffect } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { ArrowLeft, Calendar, User, FileText, MapPin, Tag, Trash2 } from 'lucide-react'
import { documentService, userService, categoryService, locationService, Document, DocumentPage, DocumentFile, DocumentCategory, StorageLocation } from '../api/services'
import { useAuth } from '../contexts/AuthContext'

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
      } catch (error) {
        console.error('Failed to load document:', error)
      } finally {
        setLoading(false)
      }
    }

    loadDocument()
  }, [id, userId, navigate])

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
          <p className="text-home-text-light mb-4">Document not found</p>
          <Link to="/documents" className="btn-primary">
            Back to Documents
          </Link>
        </div>
      </div>
    )
  }

  return (
    <div className="max-w-4xl mx-auto">
      {/* Back button */}
      <Link
        to="/documents"
        className="inline-flex items-center gap-2 text-home-text-light hover:text-home-primary-600 mb-6 transition-colors"
      >
        <ArrowLeft size={20} />
        Back to Documents
      </Link>

      {/* Document info card */}
      <div className="card mb-6">
        <div className="flex flex-col md:flex-row gap-6">
          <div className="flex-1">
            <div className="flex justify-between items-start mb-4">
              <h1 className="text-3xl font-bold text-home-text-dark">
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
              <button
                onClick={handleDelete}
                disabled={deleting}
                className="btn-danger flex items-center gap-2"
              >
                <Trash2 size={18} />
                {deleting ? 'Deleting...' : 'Delete'}
              </button>
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
              ) : null}
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
              ) : null}
            </div>
            {document.metadata && Object.keys(document.metadata).length > 0 && (
              <div className="mt-6 pt-6 border-t border-home-primary-100">
                <h3 className="font-semibold text-home-text-dark mb-3">Metadata</h3>
                <div className="grid grid-cols-2 gap-3">
                  {Object.entries(document.metadata).map(([key, value]) => (
                    <div key={key}>
                      <span className="text-sm text-home-text-light">{key}:</span>
                      <span className="ml-2 text-home-text-dark">{String(value)}</span>
                    </div>
                  ))}
                </div>
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
                        src={file.url}
                        className="w-full h-full border-0"
                        title={`PDF Preview ${index + 1}`}
                      />
                    </div>
                  ) : (
                    <div className="rounded-home overflow-hidden bg-home-background-dark">
                      <img
                        src={file.url}
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
