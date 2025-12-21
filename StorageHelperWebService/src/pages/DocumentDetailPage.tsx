import { useState, useEffect } from 'react'
import { useParams, Link, useLocation, useNavigate } from 'react-router-dom'
import { ArrowLeft, Calendar, User, FileText, MapPin, Tag, Trash2 } from 'lucide-react'
import { documentService, userService, categoryService, locationService, Document, DocumentPage, DocumentFile, DocumentCategory, StorageLocation } from '../api/services'

const DocumentDetailPage = () => {
  const { id } = useParams<{ id: string }>()
  const location = useLocation()
  const navigate = useNavigate()
  const [document, setDocument] = useState<Document | null>(null)
  const [pages, setPages] = useState<DocumentPage[]>([])
  const [files, setFiles] = useState<DocumentFile[]>([])
  const [loading, setLoading] = useState(true)
  const [users, setUsers] = useState<Array<{ id: number; display_name: string }>>([])
  const [category, setCategory] = useState<DocumentCategory | null>(null)
  const [storageLocation, setStorageLocation] = useState<StorageLocation | null>(null)
  const [deleting, setDeleting] = useState(false)
  
  // Get owner_id from location state if available
  const ownerIdFromState = (location.state as { ownerId?: number })?.ownerId

  useEffect(() => {
    const loadUsers = async () => {
      try {
        const usersResponse = await userService.getAll()
        setUsers(usersResponse.users)
      } catch (error) {
        console.error('Failed to load users:', error)
      }
    }
    loadUsers()
  }, [])

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
      if (!id) return
      try {
        setLoading(true)
        const doc = await documentService.getById(parseInt(id))
        
        // Use owner_id from location state if available, otherwise try to find it
        if (ownerIdFromState) {
          doc.owner_id = ownerIdFromState
        } else if (doc.owner_id === 0 && users.length > 0) {
          // If owner_id is 0, try to find the actual owner by querying all users
          // Only search if users are already loaded
          for (const user of users) {
            try {
              const userDocs = await userService.getDocuments(user.id)
              if (userDocs.document_ids.includes(parseInt(id))) {
                doc.owner_id = user.id
                break
              }
            } catch (error) {
              // Continue searching
            }
          }
        }
        
        // Use document from pages API if available, otherwise use minimal doc
        const pagesData = await documentService.getPages(parseInt(id))
        
        // Backend now returns full document details including category_id and location_id
        if (pagesData.document) {
          setDocument(pagesData.document)
          // Use owner_id from document
          if (pagesData.document.owner_id) {
            doc.owner_id = pagesData.document.owner_id
          }
        } else {
          setDocument(doc)
        }
        
        // Backend now returns full page details including image_url and unique files
        setPages(pagesData.pages || [])
        setFiles(pagesData.files || [])
        
        // Load category and location details if available
        const finalDoc = pagesData.document || doc
        console.log('Document data:', finalDoc)
        console.log('Category ID:', finalDoc.category_id, 'Location ID:', finalDoc.current_location_id, 'Owner ID:', finalDoc.owner_id)
        
        // Load category if available (category_id exists and is not null/undefined)
        if (finalDoc.category_id != null && finalDoc.category_id > 0 && finalDoc.owner_id && finalDoc.owner_id > 0) {
          try {
            console.log('Loading category:', finalDoc.category_id, 'for user:', finalDoc.owner_id)
            const cat = await categoryService.getById(finalDoc.owner_id, finalDoc.category_id)
            console.log('Category loaded:', cat)
            if (cat) {
              setCategory(cat)
            } else {
              console.warn('Category not found for ID:', finalDoc.category_id)
            }
          } catch (error) {
            console.error('Failed to load category:', error)
          }
        } else {
          console.log('Skipping category load - category_id:', finalDoc.category_id, 'owner_id:', finalDoc.owner_id)
        }
        
        // Load location if available (current_location_id exists, is not null/undefined, and is not -1)
        if (finalDoc.current_location_id != null && finalDoc.current_location_id > 0 && finalDoc.owner_id && finalDoc.owner_id > 0) {
          try {
            console.log('Loading location:', finalDoc.current_location_id, 'for user:', finalDoc.owner_id)
            const loc = await locationService.getById(finalDoc.owner_id, finalDoc.current_location_id)
            console.log('Location loaded:', loc)
            if (loc) {
              setStorageLocation(loc)
            } else {
              console.warn('Location not found for ID:', finalDoc.current_location_id)
            }
          } catch (error) {
            console.error('Failed to load location:', error)
          }
        } else {
          console.log('Skipping location load - current_location_id:', finalDoc.current_location_id, 'owner_id:', finalDoc.owner_id)
        }
      } catch (error) {
        console.error('Failed to load document:', error)
      } finally {
        setLoading(false)
      }
    }

    loadDocument()
    // Only depend on id and ownerIdFromState, not users
    // users is loaded separately and doesn't need to trigger document reload
  }, [id, ownerIdFromState])

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
                <span>Owner: {users.find(u => u.id === document.owner_id)?.display_name || `User ${document.owner_id}`}</span>
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
