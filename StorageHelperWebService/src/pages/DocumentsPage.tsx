import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { FileText, Calendar, User, Search, Trash2 } from 'lucide-react'
import { documentService, userService, Document, DocumentFile } from '../api/services'
import { useAuth } from '../contexts/AuthContext'

interface DocumentWithFiles extends Document {
  previewFiles?: DocumentFile[]
}

const DocumentsPage = () => {
  const { userId } = useAuth()
  const [documents, setDocuments] = useState<DocumentWithFiles[]>([])
  const [loading, setLoading] = useState(true)
  const [searchQuery, setSearchQuery] = useState('')
  const [user, setUser] = useState<{ id: number; display_name: string } | null>(null)
  const [loadingFiles, setLoadingFiles] = useState<Set<number>>(new Set())

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

  useEffect(() => {
    const loadDocuments = async () => {
      if (!userId) return
      
      try {
        setLoading(true)
        
        // Get document IDs for current user
        const docsResponse = await userService.getDocuments(userId)
        
        // Create minimal document objects
        const userDocs: DocumentWithFiles[] = docsResponse.document_ids.map(docId => ({
          id: docId,
          title: `Document #${docId}`,
          owner_id: userId,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          previewFiles: [],
        }))
        
        // Sort by created_at descending (newest first)
        userDocs.sort((a, b) => 
          new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
        )
        
        setDocuments(userDocs)
        
        // Load preview files for each document
        loadPreviewFiles(userDocs)
      } catch (error) {
        console.error('Failed to load documents:', error)
      } finally {
        setLoading(false)
      }
    }

    loadDocuments()
  }, [userId])

  const loadPreviewFiles = async (docs: DocumentWithFiles[]) => {
    const loadingSet = new Set<number>()
    
    for (const doc of docs) {
      if (loadingSet.has(doc.id)) continue
      
      loadingSet.add(doc.id)
      setLoadingFiles(prev => new Set(prev).add(doc.id))
      
      try {
        const pagesData = await documentService.getPages(doc.id)
        
        // Use files field from API response (already deduplicated)
        const fileList = pagesData.files || []
        
        // Update document with preview files
        setDocuments(prevDocs => 
          prevDocs.map(d => 
            d.id === doc.id ? { ...d, previewFiles: fileList } : d
          )
        )
      } catch (error) {
        console.error(`Failed to load preview files for document ${doc.id}:`, error)
      } finally {
        setLoadingFiles(prev => {
          const newSet = new Set(prev)
          newSet.delete(doc.id)
          return newSet
        })
      }
    }
  }

  const filteredDocuments = documents.filter((doc) =>
    doc.title?.toLowerCase().includes(searchQuery.toLowerCase())
  )

  const handleDelete = async (e: React.MouseEvent, docId: number) => {
    e.preventDefault()
    e.stopPropagation()
    
    if (!window.confirm('Are you sure you want to delete this document? This action cannot be undone.')) {
      return
    }

    try {
      await documentService.delete(docId)
      setDocuments(prevDocs => prevDocs.filter(doc => doc.id !== docId))
    } catch (error) {
      console.error('Failed to delete document:', error)
      alert('Failed to delete document. Please try again.')
    }
  }

  return (
    <div className="max-w-7xl mx-auto">
      {/* Page title and actions */}
      <div className="mb-6 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <h1 className="text-3xl font-bold text-home-text-dark">My Documents</h1>
        <Link
          to="/upload"
          className="btn-primary inline-flex items-center justify-center gap-2"
        >
          <FileText size={20} />
          Upload New Document
        </Link>
      </div>

      {/* Search and filter bar */}
      <div className="card mb-6">
        <div className="flex flex-col sm:flex-row gap-4">
          <div className="flex-1 relative">
            <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 text-home-text-light" size={20} />
            <input
              type="text"
              placeholder="Search documents..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="input pl-10"
            />
          </div>
        </div>
      </div>

      {/* Document list */}
      {loading ? (
        <div className="card text-center py-12">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-home-primary-500 mx-auto"></div>
          <p className="mt-4 text-home-text-light">Loading...</p>
        </div>
      ) : filteredDocuments.length === 0 ? (
        <div className="card text-center py-12">
          <FileText className="mx-auto mb-4 text-home-primary-300" size={48} />
          <p className="text-home-text-light mb-4">
            {searchQuery ? 'No matching documents found' : 'No documents yet'}
          </p>
          {!searchQuery && (
            <Link to="/upload" className="btn-primary inline-flex items-center gap-2">
              Upload First Document
            </Link>
          )}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {filteredDocuments.map((doc) => (
            <Link
              key={doc.id}
              to={`/documents/${doc.id}`}
              state={{ ownerId: doc.owner_id }}
              className="card hover:shadow-home-lg transition-all duration-200 group relative"
            >
              {/* Delete button */}
              <button
                onClick={(e) => handleDelete(e, doc.id)}
                className="absolute top-2 right-2 p-2 bg-red-50 text-red-500 rounded-full opacity-0 group-hover:opacity-100 transition-opacity duration-200 hover:bg-red-100 z-10"
                title="Delete document"
              >
                <Trash2 size={18} />
              </button>

              {/* Show preview files if available */}
              {doc.previewFiles && doc.previewFiles.length > 0 ? (
                <div className="mb-4 relative h-56 rounded-home overflow-hidden bg-home-background-dark border border-home-primary-100 group-hover:border-home-primary-300 transition-colors">
                  {/* Main Preview (first file) */}
                  {(() => {
                    const firstFile = doc.previewFiles[0];
                    return firstFile.file_type === 'pdf' ? (
                      <div className="w-full h-full relative">
                        <iframe
                          src={`${firstFile.url}#toolbar=0&navpanes=0&scrollbar=0&page=1&zoom=page-fit`}
                          className="w-full h-full border-0"
                          title={`PDF Preview ${doc.id}`}
                          style={{ 
                            pointerEvents: 'none',
                            width: '100%',
                            height: '100%'
                          }}
                        />
                      </div>
                    ) : (
                      <img
                        src={firstFile.url}
                        alt={doc.title || 'Document'}
                        className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
                      />
                    );
                  })()}
                  
                  {/* Badge for total files if > 1 */}
                  {doc.previewFiles.length > 1 && (
                    <div className="absolute bottom-3 right-3 bg-home-text-dark/70 text-white text-[10px] font-bold px-2 py-1 rounded shadow-sm backdrop-blur-[2px] flex items-center gap-1 z-10">
                      <FileText size={10} />
                      <span>{doc.previewFiles.length} PAGES</span>
                    </div>
                  )}
                  
                  {/* Overlay on hover */}
                  <div className="absolute inset-0 bg-home-primary-900/0 group-hover:bg-home-primary-900/5 transition-colors duration-300" />
                </div>
              ) : doc.image_url ? (
                <div className="mb-4 h-56 rounded-home overflow-hidden bg-home-background-dark border border-home-primary-100">
                  <img
                    src={doc.image_url}
                    alt={doc.title || 'Document'}
                    className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
                  />
                </div>
              ) : loadingFiles.has(doc.id) ? (
                <div className="mb-4 h-56 rounded-home overflow-hidden bg-home-background-dark border border-home-primary-100 flex items-center justify-center">
                  <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-home-primary-500"></div>
                </div>
              ) : (
                <div className="mb-4 h-56 rounded-home overflow-hidden bg-home-background-light border border-dashed border-home-primary-200 flex items-center justify-center">
                  <FileText className="text-home-primary-200" size={48} />
                </div>
              )}
              <h3 className="text-lg font-semibold text-home-text-dark mb-2 line-clamp-2">
                {doc.title || `Document #${doc.id}`}
              </h3>
              <div className="flex items-center gap-4 text-sm text-home-text-light">
                <div className="flex items-center gap-1">
                  <Calendar size={16} />
                  <span>{new Date(doc.created_at).toLocaleDateString('en-US')}</span>
                </div>
                {user && (
                  <div className="flex items-center gap-1">
                    <User size={16} />
                    <span>{user.display_name}</span>
                  </div>
                )}
                {doc.previewFiles && doc.previewFiles.length > 0 && (
                  <div className="flex items-center gap-1">
                    <FileText size={16} />
                    <span>{doc.previewFiles.length} {doc.previewFiles.length === 1 ? 'file' : 'files'}</span>
                  </div>
                )}
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}

export default DocumentsPage
